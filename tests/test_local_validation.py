"""Offline adversarial replay tests; these fixtures are not live validation evidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_target_compiler import (
    CandidateSourceFileBinding,
    CandidateTargetCompilerError,
    RequiredLocalValidation,
    SourceLocalTestObligation,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipt_ledger import (
    SQLiteLiveReceiptLedger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
)
from neo_sf_q_intel.local_validation import (
    ROLE,
    LocalValidationArtifact,
    LocalValidationEvidence,
    LocalValidationFileRoot,
    LocalValidationGeneratedOutput,
    LocalValidationInvocation,
    LocalValidationRunnerPins,
    LocalValidationVerificationError,
    StoredLocalValidationArtifact,
    serialize_local_validation_artifact,
    verify_local_validation_evidence,
)


class FixtureArtifactStore:
    """Unit-test byte port only; production has a host-owned durable implementation."""

    def __init__(self):
        self.records = {}

    def append(self, artifact_document):
        artifact = LocalValidationArtifact.model_validate_json(artifact_document)
        record = StoredLocalValidationArtifact(
            artifact_sha256=artifact.artifact_sha256,
            document_sha256=hashlib.sha256(artifact_document).hexdigest(),
            document_bytes=artifact_document,
            appended_at=datetime.now(UTC),
        )
        self.records[record.artifact_sha256] = record
        return record

    def read(self, *, artifact_sha256):
        return self.records[artifact_sha256]


def _seal_artifact(values):
    body = LocalValidationArtifact.model_construct(**values).model_dump(
        mode="json", exclude={"artifact_sha256"}
    )
    body["artifact_sha256"] = stable_sha256(body)
    return LocalValidationArtifact.model_validate(body)


def _reseal(artifact, **updates):
    values = {
        name: getattr(artifact, name)
        for name in type(artifact).model_fields
        if name != "artifact_sha256"
    }
    values.update(updates)
    values["result_sha256"] = stable_sha256(
        [value.model_dump(mode="json") for value in values["invocations"]]
    )
    return _seal_artifact(values)


def _pair(case, artifact=None, *, payload_updates=None, issuer=None, persist=True):
    artifact = artifact or case.artifact
    payload = SupportingReceiptPayload(
        receipt_role=ROLE,
        scope=case.scope,
        provenance=ReceiptProvenance.PRODUCT_OWNED,
        outcome=ReceiptOutcome.PASSED,
        issued_at=artifact.started_at,
        terminal_at=artifact.terminal_at,
        expires_at=artifact.expires_at,
        artifact_sha256=artifact.artifact_sha256,
    )
    if payload_updates:
        payload = SupportingReceiptPayload.model_validate(
            {**payload.model_dump(), **payload_updates}
        )
    signer = issuer or case.issuer
    receipt = sign_live_receipt(payload, issuer_id=signer.issuer_id, hmac_key=signer.hmac_key)
    if persist:
        case.artifact_store.append(serialize_local_validation_artifact(artifact))
        case.ledger.append(serialize_live_receipt(receipt))
    return LocalValidationEvidence(artifact=artifact, receipt=receipt)


def _case(tmp_path, *, label="catalog", generated=False):
    now = datetime.now(UTC)
    directory = f"source/{label}"
    test_locator = f"{directory}/validate.mjs"
    output_locator = f"{directory}/generated.json"
    contents = {
        f"{directory}/unchanged-input.json": b'{"upstream":true}',
        test_locator: b"// source-owned exact script",
    }
    if generated:
        contents[output_locator] = b'{"current":true}\n'
    files = tuple(
        CandidateSourceFileBinding(
            locator=path, size_bytes=len(raw), content_sha256=hashlib.sha256(raw).hexdigest()
        )
        for path, raw in sorted(contents.items())
    )
    obligation = SourceLocalTestObligation(
        obligationId=f"local:{label}",
        runnerKind="NODE_GENERATED_CHECK" if generated else "NODE_TEST",
        workingDirectory=directory,
        testLocators=(test_locator,),
        maximumSeconds=60,
        regeneratedLocators=(output_locator,) if generated else (),
        requiredResult="COMPLETE_PASS_NO_SKIP",
    )
    required = RequiredLocalValidation(
        obligation=obligation,
        bound_files=files,
        candidate_tree_sha256="1" * 64,
        command_contract_sha256=stable_sha256(
            {
                "obligation": obligation.model_dump(mode="json"),
                "files": [v.model_dump() for v in files],
            }
        ),
    )
    compilation = SimpleNamespace(
        candidate_bundle_sha256="2" * 64,
        source_contract_sha256="3" * 64,
        verified_scope=SimpleNamespace(
            project_id=label, valid_until=(now + timedelta(minutes=10)).isoformat()
        ),
        required_local_validations=(required,),
    )
    scope = ReceiptScope(
        campaign_id=f"campaign:{label}",
        project_id=label,
        source_contract_sha256=compilation.source_contract_sha256,
        candidate_sha256=compilation.candidate_bundle_sha256,
        build_sha256="4" * 64,
        operation_plan_sha256="5" * 64,
        restore_scope_sha256="6" * 64,
        policy_sha256="7" * 64,
        profile_sha256="8" * 64,
        org_fingerprint_sha256="9" * 64,
        actor_fingerprint_sha256="a" * 64,
        recovery_deadline=now + timedelta(minutes=15),
    )
    pins = LocalValidationRunnerPins(
        runner_id="host-local-validation",
        runner_version="1.0.0",
        implementation_sha256="b" * 64,
        node_executable_locator="node.exe",
        node_expected_major=26,
        node_minimum_version="26.5.0",
        allowed_runner_kinds=("NODE_GENERATED_CHECK", "NODE_TEST"),
        maximum_global_timeout_seconds=120,
        maximum_output_bytes=1024 * 1024,
    )
    invocation = LocalValidationInvocation(
        test_locator=test_locator,
        complete=True,
        exit_code=0,
        total_count=2,
        passed_count=2,
        failed_count=0,
        skipped_count=0,
        cancelled_count=0,
        todo_count=0,
    )
    artifact = _seal_artifact(
        dict(
            candidate_bundle_sha256=compilation.candidate_bundle_sha256,
            candidate_tree_sha256=required.candidate_tree_sha256,
            obligation_id=obligation.obligation_id,
            command_contract_sha256=required.command_contract_sha256,
            bound_files=tuple(LocalValidationFileRoot(**value.model_dump()) for value in files),
            runner_kind=obligation.runner_kind,
            runner_id=pins.runner_id,
            runner_version=pins.runner_version,
            runner_implementation_sha256=pins.implementation_sha256,
            node_executable_locator=pins.node_executable_locator,
            node_version="26.5.0",
            working_directory=obligation.working_directory,
            test_locators=obligation.test_locators,
            regenerated_outputs=tuple(
                LocalValidationGeneratedOutput(
                    locator=path,
                    size_bytes=len(contents[path]),
                    content_sha256=hashlib.sha256(contents[path]).hexdigest(),
                    content_bytes=contents[path],
                )
                for path in obligation.regenerated_locators
            ),
            invocations=(invocation,),
            started_at=now - timedelta(seconds=3),
            terminal_at=now - timedelta(seconds=2),
            expires_at=now + timedelta(minutes=3),
            result_sha256=stable_sha256([invocation.model_dump(mode="json")]),
        )
    )
    issuer = TrustedIssuer(
        issuer_id="fixture-local-producer",
        issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
        hmac_key=b"unit-test-only-local-validation-key",
        allowed_receipt_roles=frozenset({ROLE}),
    )
    return SimpleNamespace(
        artifact=artifact,
        compilation=compilation,
        scope=scope,
        pins=pins,
        issuer=issuer,
        registry=TrustedIssuerRegistry((issuer,)),
        ledger=SQLiteLiveReceiptLedger(tmp_path / f"{label}.db"),
        artifact_store=FixtureArtifactStore(),
    )


def _verify(case, pairs, **updates):
    kwargs = dict(
        scope=case.scope,
        registry=case.registry,
        ledger=case.ledger,
        artifact_store=case.artifact_store,
        runner_pins=case.pins,
        observed_at=datetime.now(UTC),
    )
    kwargs.update(updates)
    return verify_local_validation_evidence(case.compilation, pairs, **kwargs)


@pytest.mark.parametrize("label", ["catalog", "renamed-unrelated-project"])
@pytest.mark.parametrize("generated", [False, True])
def test_exact_durable_validation_is_generic_and_idempotent(tmp_path, label, generated):
    case = _case(tmp_path, label=label, generated=generated)
    pair = _pair(case)
    result = _verify(case, (pair,))
    assert len(result) == 1
    assert result == _verify(case, (pair,))
    assert result[0].obligation_id == f"local:{label}"
    assert result[0].artifact_sha256 == case.artifact.artifact_sha256
    assert (
        result[0].artifact_document_sha256
        == hashlib.sha256(serialize_local_validation_artifact(case.artifact)).hexdigest()
    )
    assert case.compilation.required_local_validations[0].satisfied is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_bundle_sha256", "e" * 64),
        ("candidate_tree_sha256", "e" * 64),
        ("command_contract_sha256", "e" * 64),
        ("obligation_id", "local:other"),
        ("runner_id", "other-runner"),
        ("runner_version", "1.0.1"),
        ("runner_implementation_sha256", "e" * 64),
        ("node_executable_locator", "other-node.exe"),
        ("node_version", "25.5.0"),
        ("node_version", "26.4.9"),
        ("working_directory", "other/source"),
        ("runner_kind", "NODE_GENERATED_CHECK"),
    ],
)
def test_signed_result_cannot_substitute_current_source_or_runner(tmp_path, field, value):
    case = _case(tmp_path)
    pair = _pair(case, _reseal(case.artifact, **{field: value}))
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (pair,))


@pytest.mark.parametrize("mutation", ["omit", "extra", "unchanged_input_tamper", "test_rename"])
def test_complete_inputs_and_exact_invocations_cannot_be_narrowed(tmp_path, mutation):
    case = _case(tmp_path)
    files = case.artifact.bound_files
    updates = {}
    if mutation == "omit":
        updates["bound_files"] = files[1:]
    elif mutation == "extra":
        updates["bound_files"] = (
            LocalValidationFileRoot(
                locator="extra.json", size_bytes=0, content_sha256=hashlib.sha256(b"").hexdigest()
            ),
            *files,
        )
    elif mutation == "unchanged_input_tamper":
        updates["bound_files"] = (
            files[0].model_copy(update={"content_sha256": "e" * 64}),
            *files[1:],
        )
    else:
        updates["test_locators"] = ("source/catalog/replacement.mjs",)
        updates["invocations"] = (
            case.artifact.invocations[0].model_copy(
                update={"test_locator": updates["test_locators"][0]}
            ),
        )
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, _reseal(case.artifact, **updates)),))


@pytest.mark.parametrize(
    "mutation", ["failed", "skipped", "cancelled", "todo", "empty", "incomplete", "exit"]
)
def test_complete_no_skip_result_is_measured_not_claimed(tmp_path, mutation):
    case = _case(tmp_path)
    invocation = case.artifact.invocations[0]
    updates = (
        {"passed_count": 1, f"{mutation}_count": 1}
        if mutation in {"failed", "skipped", "cancelled", "todo"}
        else {}
    )
    if mutation == "empty":
        updates = {"total_count": 0, "passed_count": 0}
    if mutation == "incomplete":
        updates = {"complete": False}
    if mutation == "exit":
        updates = {"exit_code": 1}
    artifact = _reseal(case.artifact, invocations=(invocation.model_copy(update=updates),))
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, artifact),))


@pytest.mark.parametrize("mutation", ["expired", "future", "timeout", "past_capture"])
def test_local_evidence_time_is_bounded(tmp_path, mutation):
    case = _case(tmp_path)
    now = datetime.now(UTC)
    updates = {}
    if mutation == "expired":
        updates = {"expires_at": now - timedelta(seconds=1)}
    elif mutation == "future":
        updates = {
            "started_at": now + timedelta(seconds=1),
            "terminal_at": now + timedelta(seconds=2),
        }
    elif mutation == "timeout":
        updates = {"started_at": now - timedelta(minutes=3)}
    else:
        updates = {"expires_at": now + timedelta(minutes=11)}
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, _reseal(case.artifact, **updates)),))


@pytest.mark.parametrize("mutation", ["missing", "stale", "extra"])
def test_generated_output_must_match_all_exact_current_bytes(tmp_path, mutation):
    case = _case(tmp_path, generated=True)
    outputs = case.artifact.regenerated_outputs
    if mutation == "missing":
        outputs = ()
    elif mutation == "stale":
        raw = b'{"old":true}'
        outputs = (
            LocalValidationGeneratedOutput(
                locator=outputs[0].locator,
                size_bytes=len(raw),
                content_sha256=hashlib.sha256(raw).hexdigest(),
                content_bytes=raw,
            ),
        )
    else:
        outputs = (
            *outputs,
            LocalValidationGeneratedOutput(
                locator="source/catalog/other.json",
                size_bytes=2,
                content_sha256=hashlib.sha256(b"{}").hexdigest(),
                content_bytes=b"{}",
            ),
        )
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, _reseal(case.artifact, regenerated_outputs=outputs)),))


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_role", "OTHER_ROLE"),
        ("provenance", ReceiptProvenance.MANUAL_OBSERVATION),
        ("outcome", ReceiptOutcome.RECORDED),
        ("evidence_phase", EvidencePhase.LIVE_BASELINE),
        ("artifact_sha256", "e" * 64),
        ("authorized_gate_ids", ("SF-L03",)),
    ],
)
def test_support_receipt_cannot_claim_wrong_kind_or_authority(tmp_path, field, value):
    case = _case(tmp_path)
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, payload_updates={field: value}),))


@pytest.mark.parametrize(
    "field",
    [
        "candidate_sha256",
        "project_id",
        "source_contract_sha256",
        "campaign_id",
        "actor_fingerprint_sha256",
        "build_sha256",
    ],
)
def test_signed_scope_cannot_replay_across_candidate_actor_or_project(tmp_path, field):
    case = _case(tmp_path)
    wrong = "e" * 64 if field.endswith("sha256") else "other"
    scope = case.scope.model_copy(update={field: wrong})
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, payload_updates={"scope": scope}),))


@pytest.mark.parametrize("mutation", ["host_authority", "untrusted", "wrong_key", "missing_role"])
def test_local_issuer_must_be_authorized_product_execution(tmp_path, mutation):
    case = _case(tmp_path)
    signer = case.issuer
    if mutation == "host_authority":
        signer = TrustedIssuer(
            signer.issuer_id,
            TrustedIssuerClass.HOST_AUTHORITY,
            signer.hmac_key,
            signer.allowed_receipt_roles,
        )
        case.registry = TrustedIssuerRegistry((signer,))
    elif mutation == "untrusted":
        case.registry = TrustedIssuerRegistry(())
    elif mutation == "wrong_key":
        signer = TrustedIssuer(
            signer.issuer_id,
            signer.issuer_class,
            b"other-unit-test-only-fixture-key-32",
            signer.allowed_receipt_roles,
        )
    else:
        case.registry = TrustedIssuerRegistry(
            (
                TrustedIssuer(
                    signer.issuer_id,
                    signer.issuer_class,
                    signer.hmac_key,
                    frozenset({"OTHER_ROLE"}),
                ),
            )
        )
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (_pair(case, issuer=signer),))


@pytest.mark.parametrize(
    "mutation",
    [
        "no_receipt",
        "no_artifact",
        "artifact_bytes",
        "artifact_time",
        "receipt_bytes",
        "receipt_duplicate",
    ],
)
def test_both_exact_durable_documents_are_required(tmp_path, mutation):
    case = _case(tmp_path)
    pair = _pair(case, persist=mutation != "no_receipt")
    if mutation == "no_artifact":
        case.artifact_store.records.clear()
    elif mutation in {"artifact_bytes", "artifact_time"}:
        stored = case.artifact_store.read(artifact_sha256=pair.artifact.artifact_sha256)
        updates = (
            {"document_bytes": stored.document_bytes + b" "}
            if mutation == "artifact_bytes"
            else {"appended_at": datetime.now(UTC) + timedelta(minutes=1)}
        )
        case.artifact_store.records[stored.artifact_sha256] = stored.model_copy(update=updates)
    elif mutation in {"receipt_bytes", "receipt_duplicate"}:
        records = case.ledger.replay(campaign_id=case.scope.campaign_id)
        if mutation == "receipt_bytes":
            records = (
                records[0].model_copy(
                    update={"receipt_document": records[0].receipt_document + b" "}
                ),
            )
        else:
            records = (*records, *records)
        case.ledger = SimpleNamespace(replay=lambda **kwargs: records)
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (pair,))


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "bool", "tampered", "capture_expired"]
)
def test_evidence_set_is_complete_typed_and_immutable(tmp_path, mutation):
    case = _case(tmp_path)
    pair = _pair(case)
    pairs = (pair,)
    options = {}
    if mutation == "missing":
        pairs = ()
    elif mutation == "duplicate":
        pairs = (pair, pair)
    elif mutation == "bool":
        pairs = True
    elif mutation == "tampered":
        pairs = (
            pair.model_copy(
                update={"artifact": pair.artifact.model_copy(update={"node_version": "26.9.0"})}
            ),
        )
    else:
        options["observed_at"] = datetime.now(UTC) + timedelta(minutes=11)
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, pairs, **options)


def test_non_model_success_flag_cannot_enter_composition():
    with pytest.raises(CandidateTargetCompilerError):
        compose_candidate_live_plan(None, None, None, None, local_validation_evidence=True)


def test_strict_result_counts_and_closed_artifact_schema(tmp_path):
    case = _case(tmp_path)
    values = case.artifact.invocations[0].model_dump()
    with pytest.raises(ValidationError):
        LocalValidationInvocation.model_validate({**values, "passed_count": True})
    with pytest.raises(ValidationError):
        LocalValidationInvocation.model_validate({**values, "total_count": 99})
    with pytest.raises(ValidationError):
        LocalValidationArtifact.model_validate({**case.artifact.model_dump(), "satisfied": True})


def test_every_independent_local_obligation_is_required_even_when_inputs_overlap(tmp_path):
    case = _case(tmp_path)
    first = case.compilation.required_local_validations[0]
    second = first.model_copy(
        update={
            "obligation": first.obligation.model_copy(update={"obligation_id": "local:second"}),
            "command_contract_sha256": "f" * 64,
        }
    )
    case.compilation.required_local_validations = (first, second)
    one = _pair(case)
    two = _pair(
        case,
        _reseal(
            case.artifact,
            obligation_id="local:second",
            command_contract_sha256=second.command_contract_sha256,
        ),
    )
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (one,))
    result = _verify(case, (two, one))
    assert tuple(value.obligation_id for value in result) == ("local:catalog", "local:second")
    assert len({value.receipt_id for value in result}) == 2


def test_global_timeout_applies_across_individually_valid_local_obligations(tmp_path):
    case = _case(tmp_path)
    first = case.compilation.required_local_validations[0]
    second = first.model_copy(
        update={
            "obligation": first.obligation.model_copy(update={"obligation_id": "local:second"}),
            "command_contract_sha256": "f" * 64,
        }
    )
    case.compilation.required_local_validations = (first, second)
    now = datetime.now(UTC)
    one = _pair(
        case,
        _reseal(
            case.artifact,
            started_at=now - timedelta(seconds=100),
            terminal_at=now - timedelta(seconds=99),
        ),
    )
    two = _pair(
        case,
        _reseal(
            case.artifact,
            obligation_id="local:second",
            command_contract_sha256=second.command_contract_sha256,
        ),
    )
    with pytest.raises(LocalValidationVerificationError):
        _verify(
            case,
            (one, two),
            runner_pins=case.pins.model_copy(update={"maximum_global_timeout_seconds": 60}),
        )


def test_local_evidence_cannot_outlive_host_campaign_authority(tmp_path):
    case = _case(tmp_path)
    case.scope = case.scope.model_copy(
        update={"recovery_deadline": datetime.now(UTC) + timedelta(seconds=30)}
    )
    pair = _pair(case)
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, (pair,))


def test_batch_rejects_individually_current_but_differently_expiring_obligations(tmp_path):
    case = _case(tmp_path)
    first = case.compilation.required_local_validations[0]
    second = first.model_copy(
        update={
            "obligation": first.obligation.model_copy(update={"obligation_id": "local:second"}),
            "command_contract_sha256": "f" * 64,
        }
    )
    case.compilation.required_local_validations = (first, second)
    pairs = (
        _pair(case),
        _pair(
            case,
            _reseal(
                case.artifact,
                obligation_id="local:second",
                command_contract_sha256=second.command_contract_sha256,
                expires_at=case.artifact.expires_at - timedelta(seconds=1),
            ),
        ),
    )
    with pytest.raises(LocalValidationVerificationError):
        _verify(case, pairs)


def test_cumulative_sanitized_output_is_bounded(tmp_path):
    case = _case(tmp_path)
    first = case.compilation.required_local_validations[0]
    requirements, pairs = [], []
    for index in range(8):
        obligation_id = f"local:independent-{index}"
        command_root = stable_sha256({"obligation": obligation_id})
        requirements.append(
            first.model_copy(
                update={
                    "obligation": first.obligation.model_copy(
                        update={"obligation_id": obligation_id}
                    ),
                    "command_contract_sha256": command_root,
                }
            )
        )
        pairs.append(
            _pair(
                case,
                _reseal(
                    case.artifact, obligation_id=obligation_id, command_contract_sha256=command_root
                ),
            )
        )
    case.compilation.required_local_validations = tuple(requirements)
    with pytest.raises(LocalValidationVerificationError):
        _verify(
            case,
            tuple(pairs),
            runner_pins=case.pins.model_copy(update={"maximum_output_bytes": 1024}),
        )
