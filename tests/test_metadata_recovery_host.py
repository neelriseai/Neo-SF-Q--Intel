from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.live_read_evidence as transport_module
import neo_sf_q_intel.metadata_recovery_host as host_module
import tests.test_metadata_recovery as recovery_tests
from neo_sf_q_intel.classification_bootstrap import (
    CLASSIFICATION_OPERATION_PLAN_SHA256,
    ClassificationAuthority,
    ClassificationPins,
)
from neo_sf_q_intel.live_read_evidence import (
    CliCompleted,
    CliInvocation,
    LiveReadError,
    PinnedCliLaunch,
    SubprocessCliRunner,
)
from neo_sf_q_intel.live_receipt_ledger import SQLiteLiveReceiptLedger, serialize_live_receipt
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    RecoveryPermit,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
)
from neo_sf_q_intel.metadata_recovery import _root, _sha
from neo_sf_q_intel.metadata_recovery_host import (
    MetadataCliPins,
    MetadataFenceStore,
    MetadataHostEnrollment,
    MetadataHostError,
    MetadataHostSettings,
    create_metadata_recovery_host,
    metadata_authorization_claim_sha256,
    metadata_preflight_artifact_sha256,
    serialize_reviewed_metadata_enrollment,
    verify_metadata_enrollment,
)
from tests.test_metadata_recovery import KEY, NOW, PREIMAGE, SalesforceFake, h, intent, lease


@pytest.fixture
def repo(tmp_path):
    return recovery_tests.repo.__wrapped__(tmp_path)


@pytest.fixture
def enrolled(repo):
    scope = ReceiptScope(
        campaign_id="metadata:test",
        project_id="generic-project",
        source_contract_sha256=h("source"),
        candidate_sha256=h("candidate-bundle"),
        build_sha256=h("build"),
        operation_plan_sha256=h("plan"),
        restore_scope_sha256=h("restore-scope"),
        policy_sha256=h("policy"),
        profile_sha256=h("profile"),
        org_fingerprint_sha256=h("org"),
        actor_fingerprint_sha256=h("actor"),
        recovery_deadline=NOW + timedelta(minutes=30),
    )
    source = intent(scope_sha256=_root(scope))
    permit = lease(source)
    pins = ClassificationPins(
        alias="test-alias",
        api_version="v67.0",
        org_fingerprint_sha256=h("org"),
        actor_fingerprint_sha256=h("actor"),
        actor_user_id_sha256=h("user"),
        instance_host_sha256=h("instance"),
        edition_sha256=h("edition"),
        environment_class="DEVELOPER_EDITION",
    )
    cli = MetadataCliPins(
        node_sha256=h("node"),
        entrypoint_sha256=h("entry"),
        package_sha256=h("package"),
        version="2.148.3",
    )
    task, window, store_id = h("current-task"), h("exclusive-change-window"), h("independent-store")
    claim = metadata_authorization_claim_sha256(
        intent=source,
        execution_binding_sha256=permit.execution_binding_sha256,
        scope=scope,
        task_authority_sha256=task,
        exclusive_change_window_sha256=window,
        state_store_id=store_id,
        host_implementation_sha256=_sha(Path(host_module.__file__).read_bytes()),
        cli=cli,
        classification_pins=pins,
        issued_at=permit.issued_at,
        expires_at=permit.expires_at,
        recovery_deadline=permit.recovery_deadline,
    )
    host_roles = frozenset(
        {"HOST_ENROLLMENT_RECEIPT", host_module._AUTHORITY, host_module._RECOVERY}
    )
    product_roles = frozenset(
        {"CLI_AUTHENTICATION_RECEIPT", host_module._CHECK, host_module._RESTORE}
    )
    host = TrustedIssuer("host:metadata", TrustedIssuerClass.HOST_AUTHORITY, KEY, host_roles)
    product = TrustedIssuer(
        "product:metadata", TrustedIssuerClass.PRODUCT_EXECUTION, KEY[::-1], product_roles
    )
    registry = TrustedIssuerRegistry((host, product))
    ledger = SQLiteLiveReceiptLedger(repo / ".runtime/receipts.sqlite3")
    receipts = []
    for role in (
        "HOST_ENROLLMENT_RECEIPT",
        "CLI_AUTHENTICATION_RECEIPT",
        host_module._AUTHORITY,
        host_module._RECOVERY,
        host_module._CHECK,
        host_module._RESTORE,
    ):
        issuer = host if role in host_roles else product
        common = dict(
            scope=scope,
            issued_at=NOW,
            terminal_at=NOW,
            expires_at=permit.recovery_deadline,
            provenance=ReceiptProvenance.HOST_AUTHORITY
            if issuer == host
            else ReceiptProvenance.PRODUCT_OWNED,
        )
        if role in {"HOST_ENROLLMENT_RECEIPT", "CLI_AUTHENTICATION_RECEIPT"}:
            payload = GateReceiptPayload(
                **common,
                gate_id="SF-L01" if issuer == host else "SF-L02",
                receipt_type=role,
                effect_class="HOST_CONTROL_PLANE" if issuer == host else "CLASSIFICATION_ONLY_READ",
                evidence_phase=EvidencePhase.LIVE_BASELINE,
                requirement_ids=("REQ-SF-001",),
                capability_ids=("runtime.salesforce-live-evidence",),
                outcome=ReceiptOutcome.PASSED,
                artifact_index_sha256=h(role + "artifact"),
                assertions_sha256=h(role + "assertions"),
                enrollment=HostEnrollmentBinding(
                    alias_reference_sha256=_sha(pins.alias.encode()),
                    organization_id_sha256=pins.org_fingerprint_sha256,
                    instance_host_sha256=pins.instance_host_sha256,
                    edition_sha256=pins.edition_sha256,
                    environment_class=pins.environment_class,
                    persona_fingerprint_sha256=pins.actor_fingerprint_sha256,
                )
                if issuer == host
                else None,
            )
        else:
            payload = SupportingReceiptPayload(
                **common,
                receipt_role=role,
                outcome=ReceiptOutcome.RECORDED if issuer == host else ReceiptOutcome.PASSED,
                artifact_sha256=claim
                if issuer == host
                else metadata_preflight_artifact_sha256(source, role),
                authorized_gate_ids=("SF-C03",)
                if role == host_module._AUTHORITY
                else ("SF-C06",)
                if role == host_module._RECOVERY
                else (),
                authorized_effect_classes=("METADATA_MUTATION",)
                if role == host_module._AUTHORITY
                else ("RESTORE_MUTATION",)
                if role == host_module._RECOVERY
                else (),
                recovery_permit=RecoveryPermit(
                    deployment_gate_id="SF-C03",
                    recovery_gate_id="SF-C06",
                    activation_receipt_role="DEPLOYMENT_DISPATCH_RECEIPT",
                    restore_scope_sha256=scope.restore_scope_sha256,
                    operation_plan_sha256=scope.operation_plan_sha256,
                    valid_through=permit.recovery_deadline,
                )
                if role == host_module._RECOVERY
                else None,
            )
        receipt = sign_live_receipt(payload, issuer_id=issuer.issuer_id, hmac_key=issuer.hmac_key)
        receipts.append(receipt)
        ledger.append(serialize_live_receipt(receipt))
    roots = {item.receipt_role: _sha(serialize_live_receipt(item)) for item in receipts}
    permit = permit.model_copy(
        update=dict(
            classification_receipt_sha256=roots["HOST_ENROLLMENT_RECEIPT"],
            mutation_authority_receipt_sha256=roots[host_module._AUTHORITY],
            recovery_authority_receipt_sha256=roots[host_module._RECOVERY],
            restore_rehearsal_receipt_sha256=roots[host_module._RESTORE],
            one_use_claim_sha256=_sha(("metadata-one-use:" + claim).encode()),
        )
    )
    enrollment = MetadataHostEnrollment(
        authority_class="ONE_SOURCE_PROPERTY_METADATA_TRANSACTION",
        task_authority_sha256=task,
        exclusive_change_window_sha256=window,
        state_store_id=store_id,
        host_implementation_sha256=_sha(Path(host_module.__file__).read_bytes()),
        intent=source,
        lease=permit,
        scope=scope,
        classification_pins=pins,
        cli=cli,
        receipts=tuple(receipts),
        classification_authority=ClassificationAuthority(
            schema_version="1.0.0",
            authority_class="FIXED_SYSTEM_CLASSIFICATION_ONLY",
            task_authority_sha256=task,
            pins_sha256=pins.pins_sha256,
            operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
            issued_at=NOW,
            expires_at=permit.recovery_deadline,
            maximum_response_bytes=65536,
        ),
    )
    document = serialize_reviewed_metadata_enrollment(
        enrollment, registry=registry, ledger=ledger, now=NOW
    )
    settings = MetadataHostSettings(
        enabled=True, enrollment_sha256=_sha(document), expected_task_authority_sha256=task
    )
    (repo / settings.enrollment_path).write_bytes(document)
    (repo / settings.state_path).parent.mkdir(parents=True, exist_ok=True)
    store = MetadataFenceStore(repo / settings.state_path, store_id=store_id, key=KEY)
    store.initialize()
    store.register(permit)
    return enrollment, settings, registry, ledger, store


def build(repo, enrolled, monkeypatch, *, clock=lambda: NOW):
    enrollment, settings, registry, ledger, _store = enrolled
    monkeypatch.setattr(
        host_module,
        "resolve_metadata_cli_pins",
        lambda expected: PinnedCliLaunch(("offline-fake.exe",), ()),
    )
    return create_metadata_recovery_host(
        repository_root=repo,
        settings=settings,
        registry=registry,
        ledger=ledger,
        source_intent=lambda: enrollment.intent,
        state_key=KEY,
        journal_key=KEY,
        clock=clock,
    )


def test_disabled_factory_does_not_read_files_or_construct_runner(repo, monkeypatch):
    monkeypatch.setattr(
        host_module, "_read_nofollow", lambda *args, **kwargs: pytest.fail("disabled read")
    )
    with pytest.raises(MetadataHostError, match="METADATA_HOST_DISABLED"):
        create_metadata_recovery_host(
            repository_root=repo,
            settings=MetadataHostSettings(),
            registry=None,
            ledger=None,
            source_intent=None,
            state_key=b"",
            journal_key=b"",
        )


def test_factory_is_offline_and_installs_only_real_pinned_runner(repo, enrolled, monkeypatch):
    monkeypatch.setattr(SubprocessCliRunner, "run", lambda *args: pytest.fail("factory dispatched"))
    service = build(repo, enrolled, monkeypatch)
    assert type(service._adapter.runner) is host_module._ClassifiedMetadataRunner
    assert type(service._adapter.authority.runner) is SubprocessCliRunner
    assert service._adapter.authority.runner.launch_pin is not None
    with pytest.raises(TypeError):
        service.run(alias="caller-override")


def test_full_host_transaction_uses_bound_source_authority_and_independent_state(
    repo, enrolled, monkeypatch
):
    service = build(repo, enrolled, monkeypatch)
    fake = SalesforceFake()
    observed = []
    monkeypatch.setattr(SubprocessCliRunner, "run", lambda self, invocation: fake.run(invocation))
    monkeypatch.setattr(
        host_module,
        "capture_classified_identity",
        lambda **kwargs: observed.append(kwargs["pins"].pins_sha256),
    )
    result = service.run()
    assert result.status == "RESTORED", result
    assert fake.state == PREIMAGE and len(fake.deployments) == 2 and observed
    count = len(fake.calls)
    assert service.run().status != "RESTORED"
    assert len(fake.calls) == count


@pytest.mark.parametrize(
    "setting,value",
    [("enrollment_sha256", h("wrong")), ("expected_task_authority_sha256", h("wrong-task"))],
)
def test_wrong_external_pin_or_task_blocks_before_transport(
    repo, enrolled, monkeypatch, setting, value
):
    enrollment, settings, registry, ledger, store = enrolled
    with pytest.raises(MetadataHostError):
        build(
            repo,
            (enrollment, settings.model_copy(update={setting: value}), registry, ledger, store),
            monkeypatch,
        )


def test_changed_source_is_rejected_before_classification_or_mutation(repo, enrolled, monkeypatch):
    service = build(repo, enrolled, monkeypatch)
    service._adapter.authority.source_intent = lambda: enrolled[0].intent.model_copy(
        update={"source_contract_sha256": h("drift")}
    )
    monkeypatch.setattr(
        SubprocessCliRunner, "run", lambda *args: pytest.fail("source drift dispatched")
    )
    assert service.run().status == "FAILED_BEFORE_DEPLOY"


def test_journal_independent_claim_survives_new_store_instance_and_cannot_repeat(enrolled):
    enrollment, _settings, _registry, _ledger, store = enrolled
    root = store.claim(enrollment.lease, "DEPLOY", enrollment.intent.expected_candidate_sha256)
    reopened = MetadataFenceStore(store.path, store_id=store.store_id, key=KEY)
    assert reopened.fence(enrollment.lease, "DEPLOY") == root
    with pytest.raises(MetadataHostError, match="ALREADY_CLAIMED"):
        reopened.claim(enrollment.lease, "DEPLOY", enrollment.intent.expected_candidate_sha256)


@pytest.mark.parametrize("terminal", [None, False])
def test_independent_unknown_or_nonquiescent_process_cannot_be_cleared_on_restart(
    enrolled, terminal
):
    enrollment, _settings, _registry, _ledger, store = enrolled
    nonce = store.begin_process(enrollment.lease, "DEPLOY")
    if terminal is not None:
        store.finish_process(enrollment.lease, "DEPLOY", nonce, terminal)
    reopened = MetadataFenceStore(store.path, store_id=store.store_id, key=KEY)
    with pytest.raises(MetadataHostError):
        reopened.require_quiescent(enrollment.lease)
    with pytest.raises(MetadataHostError):
        reopened.finish_process(enrollment.lease, "DEPLOY", "invented", True)


def test_missing_or_tampered_state_is_not_reinitialized_by_factory(repo, enrolled, monkeypatch):
    enrollment, settings, registry, ledger, store = enrolled
    with closing(sqlite3.connect(store.path)) as database, database:
        database.execute("UPDATE metadata_host_leases SET body='{}'")
    with pytest.raises(MetadataHostError):
        build(repo, enrolled, monkeypatch)
    store.path.unlink()
    with pytest.raises(MetadataHostError):
        build(repo, enrolled, monkeypatch)
    assert not store.path.exists()


def test_missing_live_rehearsal_is_not_replaced_by_offline_results(enrolled):
    enrollment, _settings, registry, ledger, _store = enrolled
    missing = enrollment.model_copy(
        update={
            "receipts": tuple(
                item for item in enrollment.receipts if item.receipt_role != host_module._RESTORE
            )
        }
    )
    with pytest.raises(ValidationError):
        serialize_reviewed_metadata_enrollment(missing, registry=registry, ledger=ledger, now=NOW)


def test_valid_signature_without_exact_durable_receipt_is_rejected(repo, enrolled):
    enrollment, _settings, registry, _ledger, _store = enrolled
    empty = SQLiteLiveReceiptLedger(repo / ".runtime/empty-receipts.sqlite3")
    with pytest.raises(MetadataHostError, match="RECEIPT_INVALID"):
        verify_metadata_enrollment(enrollment, registry=registry, ledger=empty, now=NOW)


def test_forged_signature_and_reissued_one_use_identity_do_not_authorize(enrolled):
    enrollment, _settings, registry, ledger, _store = enrolled
    receipt = enrollment.receipts[-1].model_copy(update={"signature_sha256": h("forged")})
    forged = enrollment.model_copy(update={"receipts": (*enrollment.receipts[:-1], receipt)})
    with pytest.raises(MetadataHostError):
        verify_metadata_enrollment(forged, registry=registry, ledger=ledger, now=NOW)
    with pytest.raises(ValidationError):
        MetadataHostEnrollment.model_validate(
            {
                **enrollment.model_dump(),
                "lease": enrollment.lease.model_copy(
                    update={"one_use_claim_sha256": h("new-attempt")}
                ),
            }
        )


@pytest.mark.parametrize(
    "path",
    ["../outside.json", ".runtime/../outside.json", "C:/private/file.json", ".runtime\\file.json"],
)
def test_host_enrollment_path_cannot_be_absolute_or_traverse(path):
    with pytest.raises(ValidationError):
        MetadataHostSettings(enrollment_path=path)


def test_transport_pin_checks_exact_resolved_path_and_actual_file_bytes(tmp_path):
    executable = tmp_path / "node.exe"
    executable.write_bytes(b"reviewed executable")
    pin = PinnedCliLaunch((str(executable),), ((executable, _sha(executable.read_bytes())),))
    pin.require((str(executable), "--version"))
    with pytest.raises(LiveReadError):
        pin.require((str(tmp_path / "other.exe"), "--version"))
    executable.write_bytes(b"replacement")
    with pytest.raises(LiveReadError):
        pin.require((str(executable), "--version"))


def test_declared_cli_version_and_package_hash_are_both_required(tmp_path, monkeypatch):
    directory = tmp_path / "client/bin"
    directory.mkdir(parents=True)
    node, entrypoint, package = (
        directory / "node.exe",
        directory / "run.js",
        directory.parent / "package.json",
    )
    node.write_bytes(b"node")
    entrypoint.write_bytes(b"entry")
    package.write_text(json.dumps({"name": "@salesforce/cli", "version": "2.148.3"}))
    monkeypatch.setattr(
        host_module,
        "_resolve_launch",
        lambda *_: [str(node), "--no-deprecation", str(entrypoint), "--version"],
    )
    pins = MetadataCliPins(
        node_sha256=_sha(node.read_bytes()),
        entrypoint_sha256=_sha(entrypoint.read_bytes()),
        package_sha256=_sha(package.read_bytes()),
        version="2.148.3",
    )
    assert host_module.resolve_metadata_cli_pins(pins).prefix == (
        str(node),
        "--no-deprecation",
        str(entrypoint),
    )
    with pytest.raises(MetadataHostError, match="VERSION_MISMATCH"):
        host_module.resolve_metadata_cli_pins(pins.model_copy(update={"version": "2.148.4"}))


def test_enrollment_offsets_normalize_and_expiry_blocks(enrolled):
    enrollment, _settings, registry, ledger, _store = enrolled
    payload = enrollment.model_dump(mode="json")
    payload["lease"]["issued_at"] = "2026-09-11T05:30:00+05:30"
    parsed = MetadataHostEnrollment.model_validate(payload)
    assert _root(parsed) == _root(enrollment)
    with pytest.raises(MetadataHostError, match="EXPIRED"):
        verify_metadata_enrollment(
            enrollment,
            registry=registry,
            ledger=ledger,
            now=enrollment.lease.recovery_deadline,
            recovery=True,
        )


def test_classification_process_failure_blocks_metadata_and_restart(repo, enrolled, monkeypatch):
    service = build(repo, enrolled, monkeypatch)
    calls = []

    def raw(_runner, invocation):
        calls.append(invocation.arguments)
        return CliCompleted(-1, b"", quiescent=False)

    def classify(**kwargs):
        kwargs["invoke"](
            ("org", "display", "--target-org", "test-alias", "--json"),
            NOW + timedelta(minutes=30),
            65536,
        )

    monkeypatch.setattr(SubprocessCliRunner, "run", raw)
    monkeypatch.setattr(host_module, "capture_classified_identity", classify)
    report = service.run()
    assert report.status == "QUARANTINED"
    assert len(calls) == 1 and calls[0][:2] == ("org", "display")
    with pytest.raises(MetadataHostError):
        build(repo, enrolled, monkeypatch)
    assert len(calls) == 1


def test_classification_cannot_extend_whole_metadata_invocation_timeout(
    repo, enrolled, monkeypatch
):
    current = [NOW]
    service = build(repo, enrolled, monkeypatch, clock=lambda: current[0])
    calls = []

    def classify(**_kwargs):
        current[0] += timedelta(seconds=31)

    monkeypatch.setattr(
        SubprocessCliRunner, "run", lambda self, invocation: calls.append(invocation.arguments)
    )
    monkeypatch.setattr(host_module, "capture_classified_identity", classify)
    assert service.run().status == "FAILED_BEFORE_DEPLOY"
    assert calls == []


def test_native_executable_pin_stays_write_locked_through_process_collection(tmp_path, monkeypatch):
    if transport_module.os.name != "nt":
        pytest.skip("Native Windows immutable launch contract")
    executable = tmp_path / "node.exe"
    executable.write_bytes(b"pinned native executable")
    pin = PinnedCliLaunch((str(executable),), ((executable, _sha(executable.read_bytes())),))

    class Containment:
        def prepare(self):
            pass

        def attach_and_resume(self, _process):
            pass

        def terminate_and_wait(self, _process, *, seconds):
            return True

        def close(self):
            pass

    def collect(*_args):
        with pytest.raises(PermissionError):
            executable.write_bytes(b"must-not-change")
        return CliCompleted(0, b"{}")

    monkeypatch.setattr(
        transport_module, "_resolve_launch", lambda _, args: [str(executable), *args]
    )
    monkeypatch.setattr(transport_module, "_ProcessContainment", Containment)
    monkeypatch.setattr(transport_module.subprocess, "Popen", lambda *args, **kwargs: object())
    monkeypatch.setattr(SubprocessCliRunner, "_collect", staticmethod(collect))
    result = SubprocessCliRunner(launch_pin=pin).run(CliInvocation(("--version",), 30, 4096))
    assert result.returncode == 0
    executable.write_bytes(b"lock released after process cleanup")


def test_expired_forward_operation_cannot_borrow_recovery_authority(repo, enrolled, monkeypatch):
    current = [NOW]
    service = build(repo, enrolled, monkeypatch, clock=lambda: current[0])
    authority = service._adapter.authority
    enrollment = enrolled[0]
    authority.note_process_state(enrollment.intent, enrollment.lease, "DEPLOY", None)
    current[0] = enrollment.lease.expires_at
    calls = []
    monkeypatch.setattr(
        SubprocessCliRunner, "run", lambda self, invocation: calls.append(invocation.arguments)
    )
    monkeypatch.setattr(host_module, "capture_classified_identity", lambda **kwargs: None)
    result = service._adapter.runner.run(CliInvocation(("project", "deploy", "start"), 30, 65536))
    assert result.returncode != 0 and calls == []
