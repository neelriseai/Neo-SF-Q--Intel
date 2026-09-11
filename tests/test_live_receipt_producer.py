from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from neo_sf_q_intel.execution_assertions import (
    AssertionOutcome,
    AssertionSubjectKind,
    ExecutionAssertion,
    ExecutionAssertionArtifact,
    ExecutionToolVersion,
    SQLiteExecutionAssertionStore,
    TrustedExecutionRunnerKey,
    build_execution_artifact_reference,
    execution_artifact_index_sha256,
    serialize_execution_assertion,
    sign_execution_assertion,
)
from neo_sf_q_intel.live_receipt_ledger import (
    SQLiteLiveReceiptLedger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipt_producer import (
    GateExecutionEvidence,
    LiveReceiptProducerCode,
    LiveReceiptProducerError,
    TrustedGateAppendAuthority,
    TrustedLiveReceiptProducer,
)
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    PinnedLiveAcceptanceProfile,
    ReceiptScope,
    SignedLiveReceipt,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
)
from tests.test_live_receipts import HOST_KEY, PRODUCT_KEY, _issuer_registry, _profile, _scope

RUNNER_KEY = b"live-receipt-producer-test-runner-key"


def _trusted_keys(*, host: bool) -> dict[str, TrustedExecutionRunnerKey]:
    return {
        "test-runner-key": TrustedExecutionRunnerKey(
            key_id="test-runner-key",
            producer_id=("host-authority-issuer" if host else "product-receipt-issuer"),
            runner_id="trusted-test-runner",
            allowed_gate_ids=frozenset({"SF-L01"} if host else {"SF-L02"}),
            allowed_receipt_roles=frozenset(
                {"HOST_ENROLLMENT_RECEIPT"} if host else {"CLI_AUTHENTICATION_RECEIPT"}
            ),
            hmac_key=RUNNER_KEY,
        )
    }


def _issuer(*, host: bool) -> TrustedIssuer:
    return TrustedIssuer(
        issuer_id="host-authority-issuer" if host else "product-receipt-issuer",
        issuer_class=(
            TrustedIssuerClass.HOST_AUTHORITY if host else TrustedIssuerClass.PRODUCT_EXECUTION
        ),
        hmac_key=HOST_KEY if host else PRODUCT_KEY,
        allowed_receipt_roles=frozenset(
            {"HOST_ENROLLMENT_RECEIPT"} if host else {"CLI_AUTHENTICATION_RECEIPT"}
        ),
    )


def _evidence(
    store: SQLiteExecutionAssertionStore,
    *,
    scope: ReceiptScope,
    gate_id: str,
    producer_id: str,
    outcome: AssertionOutcome = AssertionOutcome.PASSED,
    enrollment: bool = False,
    started_at: datetime | None = None,
    terminal_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> GateExecutionEvidence:
    now = datetime.now(UTC)
    started = started_at or now
    terminal = terminal_at or max(started, now)
    result = build_execution_artifact_reference(
        artifact_role="SANITIZED_TEST_RESULT",
        media_type="application/json",
        content=b'{"status":"PASSED"}',
    )
    artifact = ExecutionAssertionArtifact(
        expected_contract_bytes_sha256="0" * 64,
        producer_id=producer_id,
        runner_id="trusted-test-runner",
        runner_key_id="test-runner-key",
        execution_id=f"execution-{gate_id}",
        runner_version="1.0.0",
        adapter_version="test-adapter-1.0.0",
        tool_versions=(ExecutionToolVersion(tool_id="pytest", version="8.4.0"),),
        gate_id=gate_id,
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        scope=scope,
        expected_assertion_ids=("terminal-assertion",),
        assertions=(
            ExecutionAssertion(
                assertion_id="terminal-assertion",
                subject_kind=AssertionSubjectKind.TEST_METHOD,
                subject_id="test-method",
                target_sha256="e" * 64,
                predicate="TERMINAL_TEST_STATUS",
                outcome=outcome,
                expected_sha256="a" * 64,
                observed_sha256=result.content_sha256,
                result_artifact_sha256=result.content_sha256,
                result_artifact_role=result.artifact_role,
                predicate_sha256="f" * 64,
                observed_cardinality=1,
                observed_projection=("status",),
                duration_ms=1,
                detail_code=(
                    None if outcome is AssertionOutcome.PASSED else "TERMINAL_ASSERTION_FAILED"
                ),
            ),
        ),
        result_artifacts=(result,),
        runner_result_sha256=execution_artifact_index_sha256((result,)),
        started_at=started,
        terminal_at=terminal,
        expires_at=expires_at or max(now + timedelta(minutes=10), terminal),
        gap_codes=(() if outcome is AssertionOutcome.PASSED else ("TERMINAL_ASSERTION_FAILED",)),
        runner_signature_sha256="0" * 64,
    )
    artifact = sign_execution_assertion(artifact, runner_key=RUNNER_KEY)
    stored = store.append(serialize_execution_assertion(artifact))
    return GateExecutionEvidence(
        assertion_artifact_id=stored.assertion_artifact_id,
        enrollment=(
            HostEnrollmentBinding(
                alias_reference_sha256="1" * 64,
                organization_id_sha256="7" * 64,
                instance_host_sha256="2" * 64,
                edition_sha256="3" * 64,
                environment_class="DEVELOPER_EDITION",
                persona_fingerprint_sha256="8" * 64,
            )
            if enrollment
            else None
        ),
    )


def _host_receipt(
    tmp_path: Path,
) -> tuple[
    PinnedLiveAcceptanceProfile,
    ReceiptScope,
    TrustedIssuerRegistry,
    SignedLiveReceipt,
]:
    profile = _profile()
    scope = _scope(profile.profile_sha256)
    registry = _issuer_registry(profile)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.db")
    ledger.setup()
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=True),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    authority = producer.bind_gate(
        gate_id="SF-L01",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
    )
    stored = producer.append_gate_receipt(
        authority,
        _evidence(
            assertion_store,
            scope=scope,
            gate_id="SF-L01",
            producer_id="host-authority-issuer",
            enrollment=True,
        ),
    )
    return profile, scope, registry, stored.parsed_receipt()


def _resign_host_receipt(
    receipt: SignedLiveReceipt,
    **payload_changes: object,
) -> SignedLiveReceipt:
    payload = receipt.payload.model_dump(mode="python")
    payload.update(payload_changes)
    return sign_live_receipt(
        payload,
        issuer_id="host-authority-issuer",
        hmac_key=HOST_KEY,
    )


def test_host_and_product_producers_append_exact_registry_verified_bytes(
    tmp_path: Path,
) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.db")
    ledger.setup()
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "product-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )
    authority = producer.bind_gate(
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        dependency_receipts=(enrollment,),
        input_receipts=(enrollment,),
    )

    evidence = _evidence(
        assertion_store,
        scope=scope,
        gate_id="SF-L02",
        producer_id="product-receipt-issuer",
    )
    first = producer.append_gate_receipt(authority, evidence)
    exact_retry = producer.append_gate_receipt(authority, evidence)

    assert first == exact_retry
    parsed = first.parsed_receipt()
    assert isinstance(parsed.payload, GateReceiptPayload)
    assert parsed.payload.gate_id == "SF-L02"
    assert parsed.payload.effect_class == "CLASSIFICATION_ONLY_READ"
    assert registry.verify(parsed) is None
    assert ledger.replay(campaign_id=scope.campaign_id, gate_id="SF-L02") == (first,)


def test_receipt_outcome_and_digests_come_only_from_replayed_assertion_artifact(
    tmp_path: Path,
) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.db")
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "failed-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )
    authority = producer.bind_gate(
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        dependency_receipts=(enrollment,),
        input_receipts=(enrollment,),
    )
    evidence = _evidence(
        assertion_store,
        scope=scope,
        gate_id="SF-L02",
        producer_id="product-receipt-issuer",
        outcome=AssertionOutcome.FAILED,
    )
    artifact = assertion_store.get(evidence.assertion_artifact_id).parsed_artifact()

    receipt = producer.append_gate_receipt(authority, evidence).parsed_receipt()

    assert isinstance(receipt.payload, GateReceiptPayload)
    assert receipt.payload.outcome.value == "FAILED"
    assert receipt.payload.gaps == ("TERMINAL_ASSERTION_FAILED",)
    assert receipt.payload.assertions_sha256 == artifact.assertions_sha256
    assert receipt.payload.artifact_index_sha256 == artifact.artifact_index_sha256


def test_gate_phase_scope_dependencies_and_input_roles_are_profile_bound(
    tmp_path: Path,
) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "second-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=SQLiteLiveReceiptLedger(tmp_path / "second.db"),
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )

    invalid_bindings = (
        {
            "evidence_phase": EvidencePhase.DEPLOYED_CANDIDATE,
            "dependency_receipts": (enrollment,),
            "input_receipts": (enrollment,),
        },
        {
            "evidence_phase": EvidencePhase.LIVE_BASELINE,
            "dependency_receipts": (),
            "input_receipts": (enrollment,),
        },
        {
            "evidence_phase": EvidencePhase.LIVE_BASELINE,
            "dependency_receipts": (enrollment,),
            "input_receipts": (),
        },
    )
    for binding in invalid_bindings:
        with pytest.raises(
            LiveReceiptProducerError,
            match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
        ):
            producer.bind_gate(
                gate_id="SF-L02",
                **binding,  # type: ignore[arg-type]
            )

    wrong_scope = scope.model_copy(update={"candidate_sha256": "f" * 64})
    wrong_scope_ledger = SQLiteLiveReceiptLedger(tmp_path / "wrong-scope.db")
    wrong_scope_ledger.setup()
    wrong_scope_store = SQLiteExecutionAssertionStore(tmp_path / "wrong-scope-assertions.db")
    wrong_scope_producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=wrong_scope,
        issuer=_issuer(host=True),
        issuer_registry=registry,
        ledger=wrong_scope_ledger,
        execution_assertion_store=wrong_scope_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    wrong_scope_authority = wrong_scope_producer.bind_gate(
        gate_id="SF-L01",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
    )
    wrong_scope_receipt = wrong_scope_producer.append_gate_receipt(
        wrong_scope_authority,
        _evidence(
            wrong_scope_store,
            scope=wrong_scope,
            gate_id="SF-L01",
            producer_id="host-authority-issuer",
            enrollment=True,
        ),
    ).parsed_receipt()
    with pytest.raises(LiveReceiptProducerError):
        producer.bind_gate(
            gate_id="SF-L02",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=(wrong_scope_receipt,),
            input_receipts=(wrong_scope_receipt,),
        )


def test_mapping_or_cross_producer_authority_cannot_reach_signing(tmp_path: Path) -> None:
    profile, scope, registry, _ = _host_receipt(tmp_path)
    first_store = SQLiteExecutionAssertionStore(tmp_path / "first-assertions.db")
    first = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=True),
        issuer_registry=registry,
        ledger=SQLiteLiveReceiptLedger(tmp_path / "first.db"),
        execution_assertion_store=first_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    second_ledger = SQLiteLiveReceiptLedger(tmp_path / "second.db")
    second_ledger.setup()
    second_store = SQLiteExecutionAssertionStore(tmp_path / "second-assertions.db")
    second = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=True),
        issuer_registry=registry,
        ledger=second_ledger,
        execution_assertion_store=second_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    authority = first.bind_gate(
        gate_id="SF-L01",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.AUTHORITY_INVALID.value}$",
    ):
        second.append_gate_receipt(
            authority,
            _evidence(
                first_store,
                scope=scope,
                gate_id="SF-L01",
                producer_id="host-authority-issuer",
                enrollment=True,
            ),
        )
    with pytest.raises(LiveReceiptProducerError):
        second.append_gate_receipt(
            cast(TrustedGateAppendAuthority, {"gate_id": "SF-L01"}),
            _evidence(
                second_store,
                scope=scope,
                gate_id="SF-L01",
                producer_id="host-authority-issuer",
                enrollment=True,
            ),
        )
    assert second_ledger.replay(campaign_id=scope.campaign_id) == ()


def test_tampered_authority_or_input_signature_is_rejected_before_append(
    tmp_path: Path,
) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.db")
    ledger.setup()
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "product-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )
    authority = producer.bind_gate(
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        dependency_receipts=(enrollment,),
        input_receipts=(enrollment,),
    )
    tampered_authority = replace(authority, authority_class="CALLER_SELECTED")
    with pytest.raises(LiveReceiptProducerError):
        producer.append_gate_receipt(
            tampered_authority,
            _evidence(
                assertion_store,
                scope=scope,
                gate_id="SF-L02",
                producer_id="product-receipt-issuer",
            ),
        )

    forged = enrollment.model_copy(update={"signature_sha256": "0" * 64})
    with pytest.raises(LiveReceiptProducerError):
        producer.bind_gate(
            gate_id="SF-L02",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=(forged,),
            input_receipts=(forged,),
        )
    assert tuple(item.receipt_role for item in ledger.replay(campaign_id=scope.campaign_id)) == (
        "HOST_ENROLLMENT_RECEIPT",
    )


def test_signed_but_undurable_dependency_cannot_authorize_a_gate(tmp_path: Path) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    empty_ledger = SQLiteLiveReceiptLedger(tmp_path / "empty-product.db")
    empty_ledger.setup()
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=empty_ledger,
        execution_assertion_store=SQLiteExecutionAssertionStore(
            tmp_path / "empty-product-assertions.db"
        ),
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
    ):
        producer.bind_gate(
            gate_id="SF-L02",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=(enrollment,),
            input_receipts=(enrollment,),
        )


def test_output_signature_must_verify_through_registry_before_append(tmp_path: Path) -> None:
    profile = _profile()
    scope = _scope(profile.profile_sha256)
    mismatched_registry = TrustedIssuerRegistry(
        [
            TrustedIssuer(
                issuer_id="host-authority-issuer",
                issuer_class=TrustedIssuerClass.HOST_AUTHORITY,
                hmac_key=b"different-host-authority-key-material-0001",
                allowed_receipt_roles=frozenset({"HOST_ENROLLMENT_RECEIPT"}),
            )
        ]
    )
    ledger = SQLiteLiveReceiptLedger(tmp_path / "mismatched-registry.db")
    ledger.setup()
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "mismatch-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=True),
        issuer_registry=mismatched_registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    authority = producer.bind_gate(
        gate_id="SF-L01",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.SIGNATURE_INVALID.value}$",
    ):
        producer.append_gate_receipt(
            authority,
            _evidence(
                assertion_store,
                scope=scope,
                gate_id="SF-L01",
                producer_id="host-authority-issuer",
                enrollment=True,
            ),
        )

    assert ledger.replay(campaign_id=scope.campaign_id) == ()


def test_gate_issuer_class_is_derived_from_profile_not_selected_by_caller(
    tmp_path: Path,
) -> None:
    profile = _profile()
    scope = _scope(profile.profile_sha256)
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "wrong-class-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=_issuer_registry(profile),
        ledger=SQLiteLiveReceiptLedger(tmp_path / "wrong-class.db"),
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
    ):
        producer.bind_gate(
            gate_id="SF-L01",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
        )


def test_stale_future_and_wrong_phase_inputs_fail_bind_and_append_replay(
    tmp_path: Path,
) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    now = datetime.now(UTC)
    invalid_receipts = (
        _resign_host_receipt(
            enrollment,
            issued_at=now - timedelta(minutes=3),
            terminal_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        ),
        _resign_host_receipt(
            enrollment,
            issued_at=now + timedelta(minutes=1),
            terminal_at=now + timedelta(minutes=2),
            expires_at=now + timedelta(minutes=3),
        ),
        _resign_host_receipt(
            enrollment,
            evidence_phase=EvidencePhase.DEPLOYED_CANDIDATE,
        ),
    )
    for index, invalid in enumerate(invalid_receipts):
        ledger = SQLiteLiveReceiptLedger(tmp_path / f"invalid-input-{index}.db")
        ledger.setup()
        ledger.append(serialize_live_receipt(enrollment))
        ledger.append(serialize_live_receipt(invalid))
        assertion_store = SQLiteExecutionAssertionStore(
            tmp_path / f"invalid-input-assertions-{index}.db"
        )
        producer = TrustedLiveReceiptProducer(
            pinned_profile=profile,
            expected_scope=scope,
            issuer=_issuer(host=False),
            issuer_registry=registry,
            ledger=ledger,
            execution_assertion_store=assertion_store,
            trusted_execution_runner_keys=_trusted_keys(host=False),
            clock=lambda: now,
        )
        with pytest.raises(
            LiveReceiptProducerError,
            match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
        ):
            producer.bind_gate(
                gate_id="SF-L02",
                evidence_phase=EvidencePhase.LIVE_BASELINE,
                dependency_receipts=(invalid,),
                input_receipts=(invalid,),
            )

        valid_authority = producer.bind_gate(
            gate_id="SF-L02",
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=(enrollment,),
            input_receipts=(enrollment,),
        )
        forged_authority = replace(
            valid_authority,
            dependency_receipts=(invalid,),
            input_receipts=(invalid,),
        )
        with pytest.raises(LiveReceiptProducerError):
            producer.append_gate_receipt(
                forged_authority,
                _evidence(
                    assertion_store,
                    scope=scope,
                    gate_id="SF-L02",
                    producer_id="product-receipt-issuer",
                ),
            )


def test_dependency_expiry_is_rechecked_between_bind_and_append(tmp_path: Path) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "expiry-replay.db")
    ledger.setup()
    ledger.append(serialize_live_receipt(enrollment))
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "expiry-replay-assertions.db")
    observed_at = [enrollment.payload.terminal_at + timedelta(seconds=1)]
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
        clock=lambda: observed_at[0],
    )
    authority = producer.bind_gate(
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        dependency_receipts=(enrollment,),
        input_receipts=(enrollment,),
    )
    observed_at[0] = enrollment.payload.expires_at

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
    ):
        producer.append_gate_receipt(
            authority,
            _evidence(
                assertion_store,
                scope=scope,
                gate_id="SF-L02",
                producer_id="product-receipt-issuer",
            ),
        )


def test_input_receipt_must_precede_execution_start(tmp_path: Path) -> None:
    profile, scope, registry, enrollment = _host_receipt(tmp_path)
    ledger = SQLiteLiveReceiptLedger(tmp_path / "causal-order.db")
    ledger.setup()
    ledger.append(serialize_live_receipt(enrollment))
    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "causal-order-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=False),
        issuer_registry=registry,
        ledger=ledger,
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=False),
    )
    authority = producer.bind_gate(
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        dependency_receipts=(enrollment,),
        input_receipts=(enrollment,),
    )
    terminal = enrollment.payload.terminal_at
    evidence = _evidence(
        assertion_store,
        scope=scope,
        gate_id="SF-L02",
        producer_id="product-receipt-issuer",
        started_at=terminal - timedelta(seconds=2),
        terminal_at=terminal - timedelta(seconds=1),
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.GATE_BINDING_INVALID.value}$",
    ):
        producer.append_gate_receipt(authority, evidence)
    assert tuple(item.receipt_role for item in ledger.replay(campaign_id=scope.campaign_id)) == (
        "HOST_ENROLLMENT_RECEIPT",
    )


def test_ledger_exception_is_sanitized_and_receipt_payload_is_not_exposed(
    tmp_path: Path,
) -> None:
    profile, scope, registry, _ = _host_receipt(tmp_path)

    class _FailingLedger:
        def replay(self, *, campaign_id: str, gate_id: str | None = None):  # noqa: ANN201
            return ()

        def append(self, document: bytes):  # noqa: ANN201
            raise RuntimeError("sk-sensitive-key and raw receipt payload")

    assertion_store = SQLiteExecutionAssertionStore(tmp_path / "failing-assertions.db")
    producer = TrustedLiveReceiptProducer(
        pinned_profile=profile,
        expected_scope=scope,
        issuer=_issuer(host=True),
        issuer_registry=registry,
        ledger=_FailingLedger(),
        execution_assertion_store=assertion_store,
        trusted_execution_runner_keys=_trusted_keys(host=True),
    )
    authority = producer.bind_gate(
        gate_id="SF-L01",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
    )

    with pytest.raises(
        LiveReceiptProducerError,
        match=f"^{LiveReceiptProducerCode.APPEND_FAILED.value}$",
    ) as captured:
        producer.append_gate_receipt(
            authority,
            _evidence(
                assertion_store,
                scope=scope,
                gate_id="SF-L01",
                producer_id="host-authority-issuer",
                enrollment=True,
            ),
        )

    assert "sensitive" not in str(captured.value)
    assert captured.value.__cause__ is None
