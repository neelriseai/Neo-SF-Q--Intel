from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    LiveReceiptContractError,
    ReceiptGapCode,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptReference,
    ReceiptScope,
    RecoveryPermit,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    parse_pinned_profile,
    sign_live_receipt,
    validate_live_campaign,
)

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_KEY = b"product-receipt-issuer-key-material-0001"
HOST_KEY = b"host-authority-issuer-key-material-0001"
NOW = datetime.now(UTC)


def _profile():
    document = json.loads(
        (ROOT / "config" / "live-salesforce-acceptance-profile.json").read_text(encoding="utf-8")
    )
    digest = stable_sha256(document)
    return parse_pinned_profile(document, expected_profile_sha256=digest)


def _scope(profile_sha256: str, **changes: object) -> ReceiptScope:
    values: dict[str, object] = {
        "campaign_id": "campaign-001",
        "project_id": "project-generic",
        "source_contract_sha256": "1" * 64,
        "candidate_sha256": "2" * 64,
        "build_sha256": "3" * 64,
        "operation_plan_sha256": "4" * 64,
        "restore_scope_sha256": "5" * 64,
        "policy_sha256": "6" * 64,
        "profile_sha256": profile_sha256,
        "org_fingerprint_sha256": "7" * 64,
        "actor_fingerprint_sha256": "8" * 64,
        "recovery_deadline": NOW + timedelta(minutes=90),
    }
    values.update(changes)
    return ReceiptScope(**values)


def _issuer_registry(profile) -> TrustedIssuerRegistry:
    gate_roles = {item.receiptType for item in profile.profile.gates_by_id.values()}
    input_roles = {
        role
        for item in profile.profile.gates_by_id.values()
        for role in (
            *item.requiredInputReceiptRoles,
            *item.successPathAdditionalInputReceiptRoles,
            *item.failurePathAdditionalInputReceiptRoles,
        )
    }
    return TrustedIssuerRegistry(
        [
            TrustedIssuer(
                issuer_id="product-receipt-issuer",
                issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
                hmac_key=PRODUCT_KEY,
                allowed_receipt_roles=frozenset(gate_roles - {"HOST_ENROLLMENT_RECEIPT"}),
            ),
            TrustedIssuer(
                issuer_id="host-authority-issuer",
                issuer_class=TrustedIssuerClass.HOST_AUTHORITY,
                hmac_key=HOST_KEY,
                allowed_receipt_roles=frozenset(input_roles | {"HOST_ENROLLMENT_RECEIPT"}),
            ),
        ]
    )


def _authority_role(role: str) -> bool:
    return "AUTHORITY" in role or "APPROVAL" in role or "PERMIT" in role


def _build_bundle(
    *,
    invalid_recovery_permit: bool = False,
) -> tuple[object, ReceiptScope, list[SignedLiveReceipt], TrustedIssuerRegistry]:
    profile = _profile()
    scope = _scope(profile.profile_sha256)
    gates = profile.profile.gates_by_id
    required_by_role: dict[str, set[str]] = {}
    for gate in gates.values():
        for role in (
            *gate.requiredInputReceiptRoles,
            *gate.successPathAdditionalInputReceiptRoles,
            *gate.failurePathAdditionalInputReceiptRoles,
        ):
            required_by_role.setdefault(role, set()).add(gate.gateId)
    gate_roles = {item.receiptType for item in gates.values()}
    support_roles = set(required_by_role) - gate_roles

    supports: dict[str, SignedLiveReceipt] = {}
    for role in sorted(support_roles):
        gate_ids = tuple(sorted(required_by_role[role])) if _authority_role(role) else ()
        effects = tuple(sorted({gates[item].effectClass for item in gate_ids}))
        recovery_permit = None
        if role == "CAMPAIGN_RECOVERY_PERMIT_RECEIPT":
            recovery_permit = RecoveryPermit(
                deployment_gate_id="SF-C03",
                recovery_gate_id="SF-C06",
                activation_receipt_role="DEPLOYMENT_DISPATCH_RECEIPT",
                restore_scope_sha256=scope.restore_scope_sha256,
                operation_plan_sha256=scope.operation_plan_sha256,
                valid_through=(
                    NOW + timedelta(minutes=30)
                    if invalid_recovery_permit
                    else NOW + timedelta(hours=2)
                ),
            )
        terminal_at = NOW - timedelta(minutes=24)
        issued_at = NOW - timedelta(minutes=25)
        if role != "DEPLOYMENT_DISPATCH_RECEIPT":
            terminal_at = NOW - timedelta(minutes=51)
            issued_at = NOW - timedelta(minutes=52)
        payload = SupportingReceiptPayload(
            receipt_role=role,
            scope=scope,
            authorized_gate_ids=gate_ids,
            authorized_effect_classes=effects,
            recovery_permit=recovery_permit,
            provenance=ReceiptProvenance.HOST_AUTHORITY,
            outcome=ReceiptOutcome.RECORDED,
            issued_at=issued_at,
            terminal_at=terminal_at,
            expires_at=NOW + timedelta(hours=2),
            artifact_sha256="a" * 64,
        )
        supports[role] = sign_live_receipt(
            payload, issuer_id="host-authority-issuer", hmac_key=HOST_KEY
        )

    candidate_supports: dict[str, SignedLiveReceipt] = {}
    candidate_assertion = next(
        item
        for item in profile.profile.candidateGates
        if item.kind == "DEPLOYED_CANDIDATE_ASSERTIONS"
    )
    dependency_roles = {gates[item].receiptType for item in candidate_assertion.dependsOn}
    for role in candidate_assertion.requiredInputReceiptRoles:
        if (
            role not in gate_roles
            or role in dependency_roles
            or role
            in {
                "DEPLOYMENT_RECEIPT",
                "CANDIDATE_RECONCILIATION_RECEIPT",
            }
        ):
            continue
        payload = SupportingReceiptPayload(
            receipt_role=role,
            scope=scope,
            evidence_phase=EvidencePhase.DEPLOYED_CANDIDATE,
            provenance=ReceiptProvenance.PRODUCT_OWNED,
            outcome=ReceiptOutcome.PASSED,
            issued_at=NOW - timedelta(minutes=21),
            terminal_at=NOW - timedelta(minutes=20),
            expires_at=NOW + timedelta(hours=2),
            artifact_sha256="d" * 64,
        )
        candidate_supports[role] = sign_live_receipt(
            payload,
            issuer_id="product-receipt-issuer",
            hmac_key=PRODUCT_KEY,
        )

    gate_receipts: dict[str, SignedLiveReceipt] = {}
    ordered_definitions = (*profile.profile.gates, *profile.profile.candidateGates)
    for index, gate in enumerate(ordered_definitions):
        role_producers = {
            item.payload.receipt_type: item
            for item in gate_receipts.values()
            if isinstance(item.payload, GateReceiptPayload)
        }
        required_roles = gate.requiredInputReceiptRoles
        if gate.kind == "RESTORE_AND_RESIDUE_RECONCILIATION":
            required_roles = (*required_roles, *gate.successPathAdditionalInputReceiptRoles)
        inputs = tuple(
            ReceiptReference(
                role=role,
                receipt_id=(
                    candidate_supports.get(role)
                    if gate.kind == "DEPLOYED_CANDIDATE_ASSERTIONS"
                    else None
                ).receipt_id
                if gate.kind == "DEPLOYED_CANDIDATE_ASSERTIONS" and role in candidate_supports
                else (role_producers.get(role) or supports[role]).receipt_id,
            )
            for role in required_roles
        )
        dependencies = tuple(gate_receipts[item].receipt_id for item in gate.dependsOn)
        issued_at = NOW - timedelta(minutes=45 - (index * 2))
        payload = GateReceiptPayload(
            gate_id=gate.gateId,
            receipt_type=gate.receiptType,
            effect_class=gate.effectClass,
            evidence_phase=gate.acceptedEvidencePhases[0],
            requirement_ids=gate.requirementIds,
            capability_ids=gate.capabilityIds,
            scope=scope,
            dependency_receipt_ids=dependencies,
            input_receipts=inputs,
            provenance=(
                ReceiptProvenance.HOST_AUTHORITY
                if gate.receiptType == "HOST_ENROLLMENT_RECEIPT"
                else ReceiptProvenance.PRODUCT_OWNED
            ),
            outcome=ReceiptOutcome.PASSED,
            issued_at=issued_at,
            terminal_at=issued_at + timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=2),
            artifact_index_sha256="b" * 64,
            assertions_sha256="c" * 64,
            enrollment=(
                HostEnrollmentBinding(
                    alias_reference_sha256="d" * 64,
                    organization_id_sha256=scope.org_fingerprint_sha256,
                    instance_host_sha256="e" * 64,
                    edition_sha256="f" * 64,
                    environment_class="DEVELOPER_EDITION",
                    persona_fingerprint_sha256=scope.actor_fingerprint_sha256,
                )
                if gate.receiptType == "HOST_ENROLLMENT_RECEIPT"
                else None
            ),
        )
        if gate.receiptType == "HOST_ENROLLMENT_RECEIPT":
            gate_receipts[gate.gateId] = sign_live_receipt(
                payload, issuer_id="host-authority-issuer", hmac_key=HOST_KEY
            )
        else:
            gate_receipts[gate.gateId] = sign_live_receipt(
                payload, issuer_id="product-receipt-issuer", hmac_key=PRODUCT_KEY
            )
    receipts = [*supports.values(), *candidate_supports.values(), *gate_receipts.values()]
    return profile, scope, receipts, _issuer_registry(profile)


def _codes(result) -> set[ReceiptGapCode]:
    return {item.code for item in result.gaps}


def _replace_receipt(
    receipts: list[SignedLiveReceipt],
    old: SignedLiveReceipt,
    replacement: SignedLiveReceipt,
) -> None:
    receipts[receipts.index(old)] = replacement


def _gate(receipts: list[SignedLiveReceipt], gate_id: str) -> SignedLiveReceipt:
    return next(
        item
        for item in receipts
        if isinstance(item.payload, GateReceiptPayload) and item.payload.gate_id == gate_id
    )


def test_complete_signed_bundle_remains_incomplete_without_durable_ledger() -> None:
    profile, scope, receipts, registry = _build_bundle()

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert result.locally_valid_gate_ids == profile.profile.requiredGateIds
    assert result.accepted_completion_numerator == 0
    assert result.requirements_satisfied is False
    assert result.release_eligible is False
    assert result.quarantined is False
    assert _codes(result) == {ReceiptGapCode.DURABLE_LEDGER_REQUIRED}


def test_profile_requires_an_external_digest_pin() -> None:
    document = json.loads((ROOT / "config" / "live-salesforce-acceptance-profile.json").read_text())

    with pytest.raises(LiveReceiptContractError, match="PROFILE_PIN_MISMATCH"):
        parse_pinned_profile(document, expected_profile_sha256="f" * 64)


def test_mutated_profile_after_parsing_cannot_widen_classification() -> None:
    profile, scope, receipts, registry = _build_bundle()
    profile.profile.orgClassificationPolicy["allowedNonProductionClasses"].append("PRODUCTION")

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.PROFILE_PIN_MISMATCH in _codes(result)
    assert result.locally_valid_gate_ids == ()


def test_forged_and_wrong_issuer_receipts_fail_closed() -> None:
    profile, scope, receipts, registry = _build_bundle()
    target = _gate(receipts, "SF-L03")
    forged = target.model_copy(update={"signature_sha256": "0" * 64})
    _replace_receipt(receipts, target, forged)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.RECEIPT_SIGNATURE_INVALID in _codes(result)
    assert "SF-L03" not in result.locally_valid_gate_ids


def test_unknown_or_production_enrollment_classification_is_blocked() -> None:
    profile, scope, receipts, registry = _build_bundle()
    enrollment = _gate(receipts, "SF-L01")
    payload = enrollment.payload
    assert isinstance(payload, GateReceiptPayload)
    assert payload.enrollment is not None
    replacement = sign_live_receipt(
        payload.model_copy(
            update={
                "enrollment": payload.enrollment.model_copy(
                    update={"environment_class": "PRODUCTION"}
                )
            }
        ),
        issuer_id="host-authority-issuer",
        hmac_key=HOST_KEY,
    )
    _replace_receipt(receipts, enrollment, replacement)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.ORG_CLASSIFICATION_BLOCKED in _codes(result)
    assert "SF-L01" not in result.locally_valid_gate_ids


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign_id", "other-campaign"),
        ("project_id", "other-project"),
        ("org_fingerprint_sha256", "d" * 64),
        ("actor_fingerprint_sha256", "e" * 64),
        ("build_sha256", "f" * 64),
        ("candidate_sha256", "0" * 64),
        ("policy_sha256", "9" * 64),
    ],
)
def test_every_material_scope_root_is_exact(field: str, value: str) -> None:
    profile, scope, receipts, registry = _build_bundle()
    target = _gate(receipts, "SF-L03")
    payload = target.payload
    assert isinstance(payload, GateReceiptPayload)
    wrong_scope = payload.scope.model_copy(update={field: value})
    replacement = sign_live_receipt(
        payload.model_copy(update={"scope": wrong_scope}),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, target, replacement)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.RECEIPT_SCOPE_MISMATCH in _codes(result)


def test_wrong_phase_stale_future_fixture_and_duplicate_reuse_are_rejected() -> None:
    profile, scope, receipts, registry = _build_bundle()
    wrong_phase = _gate(receipts, "SF-L03")
    payload = wrong_phase.payload
    assert isinstance(payload, GateReceiptPayload)
    replacement = sign_live_receipt(
        payload.model_copy(update={"evidence_phase": EvidencePhase.RESTORED_BASELINE}),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, wrong_phase, replacement)

    stale = _gate(receipts, "SF-L04")
    stale_payload = stale.payload
    assert isinstance(stale_payload, GateReceiptPayload)
    stale_replacement = sign_live_receipt(
        stale_payload.model_copy(
            update={
                "issued_at": NOW - timedelta(hours=3),
                "terminal_at": NOW - timedelta(hours=2, minutes=59),
                "expires_at": NOW - timedelta(hours=1),
            }
        ),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, stale, stale_replacement)

    future = _gate(receipts, "SF-L05")
    future_payload = future.payload
    assert isinstance(future_payload, GateReceiptPayload)
    future_replacement = sign_live_receipt(
        future_payload.model_copy(
            update={
                "issued_at": NOW + timedelta(hours=1),
                "terminal_at": NOW + timedelta(hours=1, minutes=1),
                "expires_at": NOW + timedelta(hours=2),
            }
        ),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, future, future_replacement)

    fixture = _gate(receipts, "SF-L08")
    fixture_payload = fixture.payload
    assert isinstance(fixture_payload, GateReceiptPayload)
    fixture_replacement = sign_live_receipt(
        fixture_payload.model_copy(update={"provenance": ReceiptProvenance.FIXTURE}),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, fixture, fixture_replacement)
    receipts.append(_gate(receipts, "SF-L09"))

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert _codes(result) >= {
        ReceiptGapCode.GATE_PHASE_MISMATCH,
        ReceiptGapCode.RECEIPT_STALE,
        ReceiptGapCode.RECEIPT_FUTURE,
        ReceiptGapCode.RECEIPT_PROVENANCE_FORBIDDEN,
        ReceiptGapCode.RECEIPT_DUPLICATE,
        ReceiptGapCode.GATE_RECEIPT_DUPLICATE,
    }


def test_product_execution_receipt_cannot_self_issue_authority() -> None:
    profile, scope, receipts, registry = _build_bundle()
    authority = next(
        item for item in receipts if item.receipt_role == "CURRENT_TEST_AUTHORITY_RECEIPT"
    )
    payload = authority.payload
    assert isinstance(payload, SupportingReceiptPayload)
    forged = sign_live_receipt(
        payload,
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, authority, forged)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.RECEIPT_ROLE_NOT_AUTHORIZED in _codes(result)
    assert ReceiptGapCode.INPUT_REFERENCE_MISSING in _codes(result)


def test_scope_widened_authority_is_rejected() -> None:
    profile, scope, receipts, registry = _build_bundle()
    authority = next(
        item for item in receipts if item.receipt_role == "CURRENT_BROWSER_APPROVAL_RECEIPT"
    )
    payload = authority.payload
    assert isinstance(payload, SupportingReceiptPayload)
    widened_payload = payload.model_copy(
        update={"authorized_gate_ids": (*payload.authorized_gate_ids, "SF-L03")}
    )
    widened = sign_live_receipt(
        widened_payload,
        issuer_id="host-authority-issuer",
        hmac_key=HOST_KEY,
    )
    _replace_receipt(receipts, authority, widened)
    target = _gate(receipts, "SF-L09")
    target_payload = target.payload
    assert isinstance(target_payload, GateReceiptPayload)
    refs = tuple(
        item.model_copy(update={"receipt_id": widened.receipt_id})
        if item.role == authority.receipt_role
        else item
        for item in target_payload.input_receipts
    )
    replacement = sign_live_receipt(
        target_payload.model_copy(update={"input_receipts": refs}),
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    _replace_receipt(receipts, target, replacement)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert ReceiptGapCode.AUTHORIZATION_SCOPE_MISMATCH in _codes(result)


def test_post_dispatch_missing_restore_quarantines_campaign() -> None:
    profile, scope, receipts, registry = _build_bundle()
    receipts.remove(_gate(receipts, "SF-C06"))

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert result.quarantined is True
    assert _codes(result) >= {
        ReceiptGapCode.GATE_RECEIPT_MISSING,
        ReceiptGapCode.RESTORE_REQUIRED,
    }


def test_recovery_permit_must_cover_exact_scope_through_deadline() -> None:
    profile, scope, receipts, registry = _build_bundle(invalid_recovery_permit=True)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert result.quarantined is True
    assert ReceiptGapCode.RECOVERY_PERMIT_INVALID in _codes(result)


def test_deployment_without_trusted_dispatch_is_inconsistent_and_quarantined() -> None:
    profile, scope, receipts, registry = _build_bundle()
    dispatch = next(item for item in receipts if item.receipt_role == "DEPLOYMENT_DISPATCH_RECEIPT")
    receipts.remove(dispatch)

    result = validate_live_campaign(
        pinned_profile=profile,
        expected_scope=scope,
        receipts=receipts,
        issuer_registry=registry,
    )

    assert result.quarantined is True
    assert ReceiptGapCode.DEPLOYMENT_DISPATCH_INCONSISTENT in _codes(result)
