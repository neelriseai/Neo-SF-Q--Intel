from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.domain import (
    AssuranceRun,
    ChangeIntent,
    ChangeRequest,
    DecisionCode,
    EvidenceRef,
    EvidenceState,
    GovernanceAssessment,
    ImpactFinding,
    ReleaseDecision,
    RiskSeverity,
)
from neo_sf_q_intel.domain import (
    TestClassification as ExecutionClassification,
)
from neo_sf_q_intel.domain import (
    TestExecution as ExecutionResult,
)
from neo_sf_q_intel.domain import (
    TestOutcome as ExecutionOutcome,
)
from neo_sf_q_intel.domain import (
    TestSelection as ExecutionSelection,
)
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.outcomes import (
    DEFAULT_OUTCOME_EVALUATION_SHA256,
    DEFAULT_OUTCOME_POLICY_SHA256,
    CorrectionTargetKind,
    HumanCorrectionClaimInput,
    HumanCorrectionClaimPayload,
    IncidentEventInput,
    IncidentEventPayload,
    IncidentLifecycleEvent,
    IncidentTargetInput,
    OutcomeCandidateState,
    OutcomeContractError,
    OutcomeInputError,
    OutcomeIntegrityError,
    OutcomeMemoryService,
    OutcomeRecord,
    load_outcome_evaluation_contract,
    load_outcome_policy,
    validate_outcome_record,
)
from neo_sf_q_intel.outcomes import (
    TestExecutionOutcomePayload as ExecutionOutcomePayload,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _stable_hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


def _artifact_sha256(artifact: dict[str, Any]) -> str:
    return _stable_hash(artifact)


def _run(
    *,
    project_id: str = "project-alpha",
    snapshot: str = "snapshot-alpha",
    entity_id: str = "component:alpha",
    test_id: str = "test:alpha",
    runner_id: str = "verified-test-runner",
    executed_at: datetime | None = None,
    governance_policy: GovernancePolicy | None = None,
) -> AssuranceRun:
    governance_policy = governance_policy or GovernancePolicy.load()
    execution_time = executed_at or NOW - timedelta(minutes=1)
    valid_until = execution_time + timedelta(hours=1)
    artifact = {
        "test_id": test_id,
        "outcome": ExecutionOutcome.PASSED,
        "runner_id": runner_id,
        "source_snapshot": snapshot,
        "executed_at": execution_time.isoformat(),
        "valid_until": valid_until.isoformat(),
    }
    result_sha = _artifact_sha256(artifact)
    execution_id = f"execution:{result_sha[:24]}"
    source_evidence_id = "evidence:source"
    governance = GovernanceAssessment(
        policy_version=governance_policy.schema_version,
        policy_sha256=governance_policy.sha256,
        analysis_input_sha256=SHA_F,
        metrics=[],
        guardrails=[],
        passed=True,
    )
    return AssuranceRun(
        created_at=NOW - timedelta(hours=1),
        reasoning_policy_version="1.0.0",
        reasoning_policy_sha256=SHA_A,
        reasoning_eval_set_id="reasoning-eval-v1",
        reasoning_eval_set_sha256=SHA_B,
        source_snapshot=snapshot,
        source_graph_sha256=SHA_C,
        ontology_id="canonical-ontology",
        ontology_version="1.0.0",
        ontology_sha256=SHA_D,
        source_profile_id="source-profile",
        source_profile_version="1.0.0",
        source_profile_sha256=SHA_E,
        normalized_graph_sha256=SHA_F,
        request=ChangeRequest(
            requirement="Assess a generic configured change",
            change_intent=ChangeIntent.OBSERVED_CHANGE,
            project_id=project_id,
        ),
        evidence=[
            EvidenceRef(
                evidence_id=source_evidence_id,
                kind="source-record",
                label="Configured source record",
                source="source-adapter",
                state=EvidenceState.CONFIRMED,
                attributes={
                    "entity_id": entity_id,
                    "snapshot_id": snapshot,
                    "source_hash": SHA_A,
                },
            ),
            EvidenceRef(
                evidence_id=execution_id,
                kind="test-execution",
                label="Trusted execution receipt",
                source=runner_id,
                state=EvidenceState.CONFIRMED,
                attributes={
                    "test_id": test_id,
                    "outcome": ExecutionOutcome.PASSED,
                    "runner_id": runner_id,
                    "source_snapshot": snapshot,
                    "result_sha256": result_sha,
                    "source_hash": result_sha,
                    "executed_at": execution_time.isoformat(),
                    "valid_until": valid_until.isoformat(),
                    "result_artifact": artifact,
                },
            ),
        ],
        impacts=[
            ImpactFinding(
                entity_id=entity_id,
                label="Configured component",
                kind="configured-component",
                relation="direct:declared",
                severity=RiskSeverity.MEDIUM,
                evidence_strength=1.0,
                strength_basis="Confirmed source evidence",
                evidence_ids=[source_evidence_id],
            )
        ],
        selected_tests=[
            ExecutionSelection(
                test_id=test_id,
                label="Configured validation",
                classification=ExecutionClassification.MANDATORY,
                reason="Graph-connected validation obligation",
                evidence_ids=[source_evidence_id],
            )
        ],
        test_results=[
            ExecutionResult(
                test_id=test_id,
                outcome=ExecutionOutcome.PASSED,
                runner_id=runner_id,
                result_sha256=result_sha,
                source_snapshot=snapshot,
                executed_at=execution_time,
                valid_until=valid_until,
                evidence_ids=[execution_id],
            )
        ],
        governance=governance,
        decision=ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["RELEASE_EVIDENCE_MODEL_INCOMPLETE"],
        ),
    )


def _service() -> OutcomeMemoryService:
    return OutcomeMemoryService.from_repository(clock=lambda: NOW)


def _correction(entity_id: str = "component:alpha") -> HumanCorrectionClaimInput:
    return HumanCorrectionClaimInput(
        correction_id="correction-alpha",
        target_kind=CorrectionTargetKind.IMPACT,
        target_id=entity_id,
        prior_assertion="The configured component has the prior classification.",
        claimed_correction="The configured component has the corrected classification.",
        rationale="A reviewer identified a source-supported classification mismatch.",
        evidence_ids=("evidence:source",),
        authority_receipt_ref="receipt:opaque-123",
    )


def _incident_targets(
    target_id: str,
    target_kind: CorrectionTargetKind = CorrectionTargetKind.IMPACT,
) -> tuple[IncidentTargetInput, ...]:
    return (IncidentTargetInput(target_kind=target_kind, target_id=target_id),)


def _opened(entity_id: str = "component:alpha") -> IncidentEventInput:
    return IncidentEventInput(
        incident_id="incident-alpha",
        event=IncidentLifecycleEvent.OPENED,
        summary="A validation incident was observed for the configured component.",
        observed_at=NOW - timedelta(minutes=2),
        targets=_incident_targets(entity_id),
        evidence_ids=("evidence:source",),
    )


def test_trusted_test_outcome_is_derived_from_run() -> None:
    run = _run()
    record = _service().derive_test_execution(run, "test:alpha", GovernancePolicy.load())

    assert isinstance(record.payload, ExecutionOutcomePayload)
    assert record.payload.outcome is ExecutionOutcome.PASSED
    assert record.payload.result_sha256 == run.test_results[0].result_sha256
    assert record.lineage.run_sha256 == _stable_hash(run.model_dump(mode="json"))
    assert record.candidate_state is OutcomeCandidateState.CANDIDATE
    assert record.may_satisfy_release_evidence is False
    validate_outcome_record(
        record,
        run,
        load_outcome_policy(),
        load_outcome_evaluation_contract(),
        governance_policy=GovernancePolicy.load(),
        evaluated_at=NOW,
    )


def test_tampered_test_receipt_is_rejected() -> None:
    run = _run()
    run.evidence[-1].attributes["source_hash"] = SHA_A

    with pytest.raises(OutcomeInputError, match="receipt was rejected"):
        _service().derive_test_execution(run, "test:alpha", GovernancePolicy.load())


def test_untrusted_runner_is_rejected() -> None:
    run = _run(runner_id="runner-not-in-policy")

    with pytest.raises(OutcomeInputError, match="receipt was rejected"):
        _service().derive_test_execution(run, "test:alpha", GovernancePolicy.load())


def test_future_test_execution_is_rejected() -> None:
    run = _run(executed_at=NOW + timedelta(minutes=1))

    with pytest.raises(OutcomeInputError, match="receipt was rejected"):
        _service().derive_test_execution(run, "test:alpha", GovernancePolicy.load())


def test_human_correction_is_non_authorizing_candidate() -> None:
    run = _run()
    record = _service().derive_human_correction_claim(run, _correction())

    assert isinstance(record.payload, HumanCorrectionClaimPayload)
    assert record.payload.evidence_state is EvidenceState.INFERRED
    assert record.payload.authority_receipt_ref == "receipt:opaque-123"
    assert record.payload.authority_receipt_state == "UNVERIFIED_CANDIDATE"
    assert record.payload.target_artifact_sha256 == _stable_hash(
        run.impacts[0].model_dump(mode="json")
    )
    assert record.payload.prior_assertion_sha256 == _stable_hash(record.payload.prior_assertion)
    assert record.lineage.reasoning_evaluation_identity_status == (
        "INCOMPLETE_VERSION_UNAVAILABLE_UPSTREAM"
    )
    assert record.lineage.reasoning_workflow_linkage_status == ("DEFERRED_UNTIL_FULL_A6_REPLAY")
    assert record.lineage.identity_gaps == (
        "A6_REPLAY_LINKAGE_UNAVAILABLE",
        "REASONING_EVALUATION_VERSION_UNAVAILABLE",
    )
    assert not any(key.startswith("advisory_") for key in type(record.lineage).model_fields)
    assert record.may_authorize is False
    assert record.may_mutate_governance is False
    assert record.may_mutate_decision is False


def test_outcome_contract_rejects_authority_forging() -> None:
    run = _run()
    record = _service().derive_human_correction_claim(run, _correction())
    for field, value in (
        ("posture", "AUTHORITATIVE"),
        ("candidate_state", "HUMAN_CONFIRMED"),
        ("authority_state", "AUTHORIZING"),
        ("may_authorize", True),
        ("may_satisfy_release_evidence", True),
        ("may_mutate_governance", True),
        ("may_mutate_decision", True),
        ("may_mutate_policy", True),
        ("may_mutate_evaluation", True),
    ):
        forged = record.model_dump(mode="json")
        forged[field] = value
        with pytest.raises(ValidationError):
            OutcomeRecord.model_validate(forged)
    forged_payload = record.model_dump(mode="json")
    forged_payload["payload"]["evidence_state"] = "HUMAN_CONFIRMED"
    with pytest.raises(ValidationError):
        OutcomeRecord.model_validate(forged_payload)
    for forbidden_field, value in (
        ("recorded_at", NOW.isoformat()),
        ("lineage", {}),
        ("outcome_sha256", SHA_A),
        ("authority_state", "AUTHORIZING"),
    ):
        with pytest.raises(ValidationError):
            HumanCorrectionClaimInput.model_validate(
                {**_correction().model_dump(mode="json"), forbidden_field: value}
            )


def test_legal_incident_chain_binds_predecessor() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    acknowledged = service.derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident-alpha",
            event=IncidentLifecycleEvent.ACKNOWLEDGED,
            summary="The incident was acknowledged for deterministic investigation.",
            observed_at=NOW - timedelta(minutes=1),
            targets=_incident_targets("component:alpha"),
            evidence_ids=("evidence:source",),
        ),
        predecessor=opened,
    )

    assert isinstance(acknowledged.payload, IncidentEventPayload)
    assert acknowledged.payload.predecessor_outcome_id == opened.outcome_id
    assert acknowledged.payload.predecessor_sha256 == opened.outcome_sha256
    validate_outcome_record(
        acknowledged,
        run,
        load_outcome_policy(),
        load_outcome_evaluation_contract(),
        predecessor=opened,
        evaluated_at=NOW,
    )


def test_incident_targets_are_typed_hash_bound_and_unambiguous() -> None:
    shared_id = "artifact:shared"
    run = _run(entity_id=shared_id, test_id=shared_id)
    service = _service()
    impact_record = service.derive_incident_event(run, _opened(shared_id))
    selection_input = IncidentEventInput(
        incident_id="incident-selection",
        event=IncidentLifecycleEvent.OPENED,
        summary="A typed test-selection incident is recorded independently.",
        observed_at=NOW,
        targets=_incident_targets(shared_id, CorrectionTargetKind.TEST_SELECTION),
        evidence_ids=("evidence:source",),
    )
    selection_record = service.derive_incident_event(run, selection_input)

    impact_target = impact_record.payload.targets[0]
    selection_target = selection_record.payload.targets[0]
    assert impact_target.target_kind is CorrectionTargetKind.IMPACT
    assert selection_target.target_kind is CorrectionTargetKind.TEST_SELECTION
    assert impact_target.target_id == selection_target.target_id == shared_id
    assert impact_target.target_artifact_sha256 == _stable_hash(
        run.impacts[0].model_dump(mode="json")
    )
    assert selection_target.target_artifact_sha256 == _stable_hash(
        run.selected_tests[0].model_dump(mode="json")
    )
    assert impact_target.target_artifact_sha256 != selection_target.target_artifact_sha256

    wrong_kind = selection_input.model_copy(
        update={"targets": _incident_targets(shared_id, CorrectionTargetKind.CLAIM)}
    )
    with pytest.raises(OutcomeInputError, match="typed artifact outside"):
        service.derive_incident_event(run, wrong_kind)

    duplicated = selection_input.model_copy(
        update={"targets": (selection_input.targets[0], selection_input.targets[0])}
    )
    with pytest.raises(OutcomeInputError, match="duplicate or unsorted typed key"):
        service.derive_incident_event(run, duplicated)

    forged = selection_record.model_dump(mode="json")
    forged["payload"]["targets"][0]["target_artifact_sha256"] = SHA_A
    body = dict(forged)
    body.pop("outcome_id")
    body.pop("outcome_sha256")
    digest = _stable_hash(body)
    forged["outcome_id"] = f"outcome:{digest}"
    forged["outcome_sha256"] = digest
    rehashed = OutcomeRecord.model_validate(forged)
    with pytest.raises(OutcomeIntegrityError, match="targets differ"):
        validate_outcome_record(
            rehashed,
            run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )


def test_illegal_incident_transition_is_rejected() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    reopened = IncidentEventInput(
        incident_id="incident-alpha",
        event=IncidentLifecycleEvent.REOPENED,
        summary="An invalid transition attempt must be rejected.",
        observed_at=NOW,
        targets=_incident_targets("component:alpha"),
        evidence_ids=("evidence:source",),
    )

    with pytest.raises(OutcomeInputError, match="transition"):
        service.derive_incident_event(run, reopened, predecessor=opened)


def test_incident_transition_requires_exact_predecessor_object() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    acknowledged = service.derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident-alpha",
            event=IncidentLifecycleEvent.ACKNOWLEDGED,
            summary="The incident was acknowledged with its predecessor.",
            observed_at=NOW - timedelta(minutes=1),
            targets=_incident_targets("component:alpha"),
            evidence_ids=("evidence:source",),
        ),
        predecessor=opened,
    )

    with pytest.raises(OutcomeIntegrityError, match="requires its predecessor object"):
        validate_outcome_record(
            acknowledged,
            run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )
    alternate_opened = service.derive_incident_event(
        run,
        _opened().model_copy(
            update={"summary": "A distinct initial incident receipt with the same incident ID."}
        ),
    )
    with pytest.raises(OutcomeIntegrityError, match="predecessor chain"):
        validate_outcome_record(
            acknowledged,
            run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            predecessor=alternate_opened,
            evaluated_at=NOW,
        )


def test_opened_incident_rejects_supplied_predecessor() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())

    with pytest.raises(OutcomeInputError, match="OPENED"):
        service.derive_incident_event(run, _opened(), predecessor=opened)


def test_incident_observation_time_cannot_move_backward() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    backward = IncidentEventInput(
        incident_id="incident-alpha",
        event=IncidentLifecycleEvent.ACKNOWLEDGED,
        summary="This transition has an invalid earlier observation time.",
        observed_at=NOW - timedelta(minutes=3),
        targets=_incident_targets("component:alpha"),
        evidence_ids=("evidence:source",),
    )

    with pytest.raises(OutcomeInputError, match="backward"):
        service.derive_incident_event(run, backward, predecessor=opened)


def test_incident_record_time_cannot_precede_predecessor() -> None:
    run = _run()
    future_service = OutcomeMemoryService.from_repository(
        clock=lambda: NOW + timedelta(seconds=1)
    )
    opened = future_service.derive_incident_event(run, _opened())
    acknowledged = IncidentEventInput(
        incident_id="incident-alpha",
        event=IncidentLifecycleEvent.ACKNOWLEDGED,
        summary="A transition cannot be recorded before its predecessor.",
        observed_at=NOW,
        targets=_incident_targets("component:alpha"),
        evidence_ids=("evidence:source",),
    )

    with pytest.raises(OutcomeInputError, match="record time"):
        _service().derive_incident_event(run, acknowledged, predecessor=opened)


def test_incident_chain_replays_every_supplied_predecessor() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    acknowledged = service.derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident-alpha",
            event=IncidentLifecycleEvent.ACKNOWLEDGED,
            summary="The incident was acknowledged.",
            observed_at=NOW - timedelta(minutes=1),
            targets=_incident_targets("component:alpha"),
            evidence_ids=("evidence:source",),
        ),
        predecessor=opened,
    )
    resolved = service.derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident-alpha",
            event=IncidentLifecycleEvent.RESOLVED,
            summary="The incident was resolved after acknowledgement.",
            observed_at=NOW,
            targets=_incident_targets("component:alpha"),
            evidence_ids=("evidence:source",),
        ),
        predecessor=acknowledged,
        predecessor_chain=(opened,),
    )

    validate_outcome_record(
        resolved,
        run,
        load_outcome_policy(),
        load_outcome_evaluation_contract(),
        predecessor=acknowledged,
        predecessor_chain=(opened,),
        evaluated_at=NOW,
    )


def test_incident_chain_depth_and_duplicate_ids_are_rejected_before_replay() -> None:
    run = _run()
    service = _service()
    opened = service.derive_incident_event(run, _opened())
    duplicate_input = IncidentEventInput(
        incident_id="incident-alpha",
        event=IncidentLifecycleEvent.ACKNOWLEDGED,
        summary="Duplicate predecessor identities are not a valid incident history.",
        observed_at=NOW,
        targets=_incident_targets("component:alpha"),
        evidence_ids=("evidence:source",),
    )
    with pytest.raises(OutcomeInputError, match="duplicate outcome ID"):
        service.derive_incident_event(
            run,
            duplicate_input,
            predecessor=opened,
            predecessor_chain=(opened,),
        )

    predecessor = opened
    predecessor_chain: list[OutcomeRecord] = []
    while len(predecessor_chain) + 1 < load_outcome_policy().limits.maximum_incident_chain_depth:
        prior_event = predecessor.payload.event
        next_event = {
            IncidentLifecycleEvent.OPENED: IncidentLifecycleEvent.ACKNOWLEDGED,
            IncidentLifecycleEvent.ACKNOWLEDGED: IncidentLifecycleEvent.RESOLVED,
            IncidentLifecycleEvent.RESOLVED: IncidentLifecycleEvent.REOPENED,
            IncidentLifecycleEvent.REOPENED: IncidentLifecycleEvent.RESOLVED,
        }[prior_event]
        next_record = service.derive_incident_event(
            run,
            IncidentEventInput(
                incident_id="incident-alpha",
                event=next_event,
                summary="A bounded generic incident lifecycle event.",
                observed_at=NOW,
                targets=_incident_targets("component:alpha"),
                evidence_ids=("evidence:source",),
            ),
            predecessor=predecessor,
            predecessor_chain=tuple(predecessor_chain),
        )
        predecessor_chain.append(predecessor)
        predecessor = next_record

    overflow_event = (
        IncidentLifecycleEvent.REOPENED
        if predecessor.payload.event is IncidentLifecycleEvent.RESOLVED
        else IncidentLifecycleEvent.RESOLVED
    )
    with pytest.raises(OutcomeInputError, match="depth bound"):
        service.derive_incident_event(
            run,
            IncidentEventInput(
                incident_id="incident-alpha",
                event=overflow_event,
                summary="This event exceeds the configured incident history depth.",
                observed_at=NOW,
                targets=_incident_targets("component:alpha"),
                evidence_ids=("evidence:source",),
            ),
            predecessor=predecessor,
            predecessor_chain=tuple(predecessor_chain),
        )

    acknowledged = service.derive_incident_event(run, duplicate_input, predecessor=opened)
    with pytest.raises(OutcomeIntegrityError, match="duplicate outcome ID"):
        validate_outcome_record(
            acknowledged,
            run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            predecessor=opened,
            predecessor_chain=(opened,),
            evaluated_at=NOW,
        )


def test_unknown_target_is_rejected() -> None:
    with pytest.raises(OutcomeInputError, match="outside the originating run"):
        _service().derive_human_correction_claim(_run(), _correction("component:unknown"))


def test_evidence_attribute_string_does_not_create_target_membership() -> None:
    run = _run().model_copy(update={"impacts": []})

    with pytest.raises(OutcomeInputError, match="outside the originating run"):
        _service().derive_human_correction_claim(run, _correction())


def test_duplicate_typed_correction_target_is_rejected() -> None:
    run = _run()
    run.impacts.append(run.impacts[0].model_copy(deep=True))

    with pytest.raises(OutcomeInputError, match="Duplicate typed correction target"):
        _service().derive_human_correction_claim(run, _correction())


def test_unknown_evidence_is_rejected() -> None:
    correction = _correction().model_copy(update={"evidence_ids": ("evidence:unknown",)})

    with pytest.raises(OutcomeInputError, match="outside the originating run"):
        _service().derive_human_correction_claim(_run(), correction)


def test_cross_project_record_is_rejected() -> None:
    run = _run()
    record = _service().derive_human_correction_claim(run, _correction())

    with pytest.raises(OutcomeIntegrityError, match="lineage"):
        validate_outcome_record(
            record,
            _run(project_id="project-beta"),
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )


def test_cross_snapshot_record_is_rejected() -> None:
    run = _run()
    record = _service().derive_human_correction_claim(run, _correction())

    with pytest.raises(OutcomeIntegrityError, match="lineage"):
        validate_outcome_record(
            record,
            _run(snapshot="snapshot-beta"),
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )


def test_derivation_does_not_mutate_run() -> None:
    run = _run()
    before = run.model_dump_json()

    _service().derive_human_correction_claim(run, _correction())
    _service().derive_incident_event(run, _opened())
    _service().derive_test_execution(run, "test:alpha", GovernancePolicy.load())

    assert run.model_dump_json() == before


def test_record_hash_is_deterministic() -> None:
    run = _run()
    first = _service().derive_human_correction_claim(run, _correction())
    second = _service().derive_human_correction_claim(run, _correction())

    assert first == second
    assert first.outcome_id == f"outcome:{first.outcome_sha256}"


def test_renamed_identities_preserve_control_flow() -> None:
    policy = GovernancePolicy.load()
    renamed_run = _run(
        project_id="project-renamed",
        snapshot="snapshot-renamed",
        entity_id="module:renamed",
        test_id="test:renamed",
    )
    service = _service()
    correction = service.derive_human_correction_claim(renamed_run, _correction("module:renamed"))
    execution = service.derive_test_execution(renamed_run, "test:renamed", policy)
    incident = service.derive_incident_event(
        renamed_run,
        IncidentEventInput(
            incident_id="incident-renamed",
            event=IncidentLifecycleEvent.OPENED,
            summary="A renamed incident follows the same configured control path.",
            observed_at=NOW,
            targets=_incident_targets("module:renamed"),
            evidence_ids=("evidence:source",),
        ),
    )

    assert isinstance(correction.payload, HumanCorrectionClaimPayload)
    assert isinstance(execution.payload, ExecutionOutcomePayload)
    assert isinstance(incident.payload, IncidentEventPayload)
    assert len({item.posture for item in (correction, execution, incident)}) == 1
    assert all(not item.may_authorize for item in (correction, execution, incident))


def test_assessed_outcomes_require_the_module_pinned_governance_policy() -> None:
    canonical = GovernancePolicy.load()
    alternate_runner = canonical.trusted_runners[0].model_copy(
        update={"runner_id": "alternate-runner", "evidence_source": "alternate-runner"}
    )
    alternate = GovernancePolicy.model_validate(
        canonical.model_dump(mode="python")
        | {"trusted_runners": [alternate_runner.model_dump(mode="python")]}
    )
    alternate_assessed_run = _run(
        runner_id="alternate-runner", governance_policy=alternate
    )

    with pytest.raises(OutcomeInputError, match="module-pinned governance policy"):
        _service().derive_human_correction_claim(alternate_assessed_run, _correction())

    canonical_run = _run()
    with pytest.raises(OutcomeInputError, match="module-pinned policy"):
        _service().derive_test_execution(canonical_run, "test:alpha", alternate)


def test_policy_and_evaluation_tamper_are_rejected(tmp_path: Path) -> None:
    policy_document = json.loads(
        Path("config/outcome-memory-policy.json").read_text(encoding="utf-8")
    )
    policy_document["limits"]["maximumTargetIds"] += 1
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy_document), encoding="utf-8")
    with pytest.raises(OutcomeContractError, match="digest"):
        load_outcome_policy(policy_path, expected_sha256=DEFAULT_OUTCOME_POLICY_SHA256)

    evaluation_document = json.loads(
        Path("quality/evals/outcome-memory-contract-v1.json").read_text(encoding="utf-8")
    )
    evaluation_document["minimumAdjudicatedCaseCount"] += 1
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(json.dumps(evaluation_document), encoding="utf-8")
    with pytest.raises(OutcomeContractError, match="digest"):
        load_outcome_evaluation_contract(
            evaluation_path, expected_sha256=DEFAULT_OUTCOME_EVALUATION_SHA256
        )


def test_evaluation_manifest_matches_every_executable_test() -> None:
    module_path = Path("tests/test_outcomes.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    expected = {
        f"tests/test_outcomes.py::{node.name}"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    evaluation = load_outcome_evaluation_contract()

    assert {item.test_id for item in evaluation.contract_cases} == expected


def test_replay_rejects_a_record_from_another_run() -> None:
    first_run = _run()
    record = _service().derive_human_correction_claim(first_run, _correction())
    another_run = _run()

    with pytest.raises(OutcomeIntegrityError, match="lineage"):
        validate_outcome_record(
            record,
            another_run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )


def test_incident_future_time_is_rejected() -> None:
    event = _opened().model_copy(update={"observed_at": NOW + timedelta(minutes=1)})

    with pytest.raises(OutcomeInputError, match="future"):
        _service().derive_incident_event(_run(), event)


def test_secret_bearing_free_text_is_rejected() -> None:
    secret_text = "api_key=" + ("x" * 16)
    document = _correction().model_dump(mode="python")
    document["claimed_correction"] = secret_text

    with pytest.raises(ValidationError, match="secret"):
        HumanCorrectionClaimInput.model_validate(document)


@pytest.mark.parametrize(
    ("kind", "unsafe_value"),
    (
        ("project", "C:\\private\\project"),
        ("project", "path=C:\\private\\project"),
        ("project", "\\\\server\\share\\project"),
        ("project", "/tmp/project"),
        ("project", "/HOME/user/project"),
        ("incident", "C:\\private\\incident"),
        ("correction", "sk-" + ("x" * 16)),
    ),
)
def test_recursive_record_safety_rejects_unsafe_identifiers(kind: str, unsafe_value: str) -> None:
    service = _service()
    if kind == "project":
        with pytest.raises(OutcomeInputError, match="machine-path"):
            service.derive_human_correction_claim(_run(project_id=unsafe_value), _correction())
    elif kind == "incident":
        event = _opened().model_copy(update={"incident_id": unsafe_value})
        with pytest.raises(OutcomeInputError, match="machine-path"):
            service.derive_incident_event(_run(), event)
    else:
        correction = _correction().model_copy(update={"correction_id": unsafe_value})
        with pytest.raises(OutcomeInputError, match="secret"):
            service.derive_human_correction_claim(_run(), correction)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "path=C:\\private\\project",
        "\\\\server\\share\\project",
        "/tmp/project",
        "/HOME/user/project",
        "sk-" + ("x" * 16),
    ),
)
def test_recursive_record_safety_is_reapplied_during_replay(unsafe_value: str) -> None:
    run = _run()
    record = _service().derive_human_correction_claim(run, _correction())
    forged = record.model_dump(mode="json")
    forged["lineage"]["project_id"] = unsafe_value
    body = dict(forged)
    body.pop("outcome_id")
    body.pop("outcome_sha256")
    digest = _stable_hash(body)
    forged["outcome_id"] = f"outcome:{digest}"
    forged["outcome_sha256"] = digest
    rehashed = OutcomeRecord.model_validate(forged)

    with pytest.raises(OutcomeIntegrityError, match="secret"):
        validate_outcome_record(
            rehashed,
            run,
            load_outcome_policy(),
            load_outcome_evaluation_contract(),
            evaluated_at=NOW,
        )
