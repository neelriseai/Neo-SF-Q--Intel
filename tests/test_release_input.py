import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.path_replay as path_replay_module
import neo_sf_q_intel.release_input as release_input_module
from neo_sf_q_intel.domain import AssuranceRun, DecisionCode
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.governance import assess_run, decide
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.path_replay import (
    compile_complete_graph_path_replay,
    load_complete_path_replay_policy,
)
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.propagation import (
    evaluate_analysis_risk,
    load_analysis_risk_policy,
    load_propagation_policy,
    traverse_propagation,
)
from neo_sf_q_intel.release_input import (
    DEFAULT_RELEASE_INPUT_POLICY_SHA256,
    ConflictSetBinding,
    ExecutionReceiptBinding,
    HumanConfirmationSetBinding,
    ImmutableReleaseInput,
    ObligationReceiptBinding,
    PolicyBinding,
    ReleaseInputContractError,
    ReleaseInputGapCode,
    RiskReceiptBinding,
    compile_immutable_release_input,
    load_release_input_policy,
    seal_receipt,
    verify_immutable_release_input,
)
from tests.support.path_replay_fixture import OBSERVED, ROOT, build_system, replay_input

POLICY_PATH = ROOT / "config" / "release-input-policy.json"
PATH_POLICY_PATH = ROOT / "config" / "complete-path-replay-policy.json"
CHANGE_BYTES = b'{"changed":["generic/component"]}'
BUILD_BYTES = b'{"build":"candidate-fixture"}'


@pytest.fixture(autouse=True)
def _fixed_current_time(monkeypatch: pytest.MonkeyPatch) -> None:
    current = OBSERVED + timedelta(minutes=1)
    monkeypatch.setattr(path_replay_module, "_utc_now", lambda: current)
    monkeypatch.setattr(release_input_module, "_utc_now", lambda: current)


def _contracts(system):
    propagation = load_propagation_policy(
        ROOT / "config" / "propagation-policy.json", system["ontology"]
    )
    path_policy = load_complete_path_replay_policy(PATH_POLICY_PATH, propagation)
    release_policy = load_release_input_policy(POLICY_PATH)
    path_result = compile_complete_graph_path_replay(
        system["graph"], propagation, path_policy, replay_input(system)
    )
    assert path_result.artifact is not None
    return propagation, path_policy, release_policy, path_result.artifact


def _policy_bindings(path, release_policy):
    roots = {
        "analysis-risk": (
            release_policy.analysis_risk_policy.policy_id,
            release_policy.analysis_risk_policy.policy_version,
            release_policy.analysis_risk_policy.policy_sha256,
        ),
        "canonical-ontology": (
            path.ontology_id,
            path.ontology_version,
            path.ontology_sha256,
        ),
        "extractor-registry": (
            path.trusted_edge_registry_id,
            path.trusted_edge_registry_version,
            path.trusted_edge_registry_sha256,
        ),
        "path-replay": (
            path.path_replay_policy_id,
            path.path_replay_policy_version,
            path.path_replay_policy_sha256,
        ),
        "propagation": (
            path.propagation_policy_id,
            path.propagation_policy_version,
            path.propagation_policy_sha256,
        ),
        "source-profile": (
            path.profile_id,
            path.profile_version,
            path.profile_sha256,
        ),
        "trusted-edge": (
            path.trusted_edge_policy_id,
            path.trusted_edge_policy_version,
            path.trusted_edge_policy_sha256,
        ),
        **{
            item.role: (item.policy_id, item.policy_version, item.policy_sha256)
            for item in release_policy.pinned_producer_policies
        },
    }
    return tuple(
        PolicyBinding(
            role=role,
            policy_id=value[0],
            policy_version=value[1],
            policy_sha256=value[2],
        )
        for role, value in sorted(roots.items())
    )


def _candidate_inputs(system):
    propagation, path_policy, policy, path = _contracts(system)
    bindings = _policy_bindings(path, policy)
    observed = OBSERVED.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid = (OBSERVED + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    build_sha = __import__("hashlib").sha256(BUILD_BYTES).hexdigest()
    risk_policy = load_analysis_risk_policy(
        ROOT / "config" / "analysis-risk-policy.json",
        system["ontology"],
        propagation,
    )
    trusted = traverse_propagation(
        system["graph"],
        path.required_scope.seed_ids,
        propagation,
        trusted_edge_replay=replace(
            replay_input(system), evaluated_at=OBSERVED + timedelta(minutes=1)
        ),
    )
    factors = {
        target: {
            "businessCriticality": "LOW",
            "changeIntent": "INFORMATIONAL",
            "securityReach": "NONE",
        }
        for target in path.required_scope.material_output_ids
    }
    risk_result = evaluate_analysis_risk(
        trusted, factors, risk_policy, expected_graph=system["graph"]
    ).model_dump(mode="json")
    risks = tuple(
        seal_receipt(
            RiskReceiptBinding,
            {
                "receipt_id": f"risk-{target}",
                "policy_id": policy.analysis_risk_policy.policy_id,
                "policy_version": policy.analysis_risk_policy.policy_version,
                "policy_sha256": policy.analysis_risk_policy.policy_sha256,
                "observed_at": observed,
                "valid_until": valid,
                "target_id": target,
                "build_artifact_sha256": build_sha,
                "candidate_scope_sha256": "0" * 64,
                "path_sha256s": tuple(
                    sorted(
                        item["path_sha256"]
                        for assessment in risk_result["assessments"]
                        if assessment["target_id"] == target
                        for item in assessment["path_contributions"]
                    )
                ),
                "risk_result": risk_result,
            },
        )
        for target in path.required_scope.material_output_ids
    )
    obligations = tuple(
        seal_receipt(
            ObligationReceiptBinding,
            {
                "receipt_id": f"obligation-{target}",
                "policy_id": "candidate-obligation-unattested",
                "policy_version": "0.0.0",
                "policy_sha256": "1" * 64,
                "observed_at": observed,
                "valid_until": valid,
                "target_id": target,
                "build_artifact_sha256": build_sha,
                "candidate_scope_sha256": "0" * 64,
                "test_ids": (f"test-{target}",),
                "candidate_payload_sha256": stable_sha256(
                    {"target": target, "kind": "candidate-obligation"}
                ),
                "candidate_payload_size_bytes": 1,
            },
        )
        for target in path.required_scope.material_output_ids
    )
    executions = tuple(
        seal_receipt(
            ExecutionReceiptBinding,
            {
                "receipt_id": f"execution-{item.target_id}",
                "policy_id": "candidate-execution-unattested",
                "policy_version": "0.0.0",
                "policy_sha256": "2" * 64,
                "observed_at": observed,
                "valid_until": valid,
                "obligation_receipt_id": item.receipt_id,
                "test_id": item.test_ids[0],
                "build_artifact_sha256": build_sha,
                "environment_id": "candidate-environment",
                "candidate_scope_sha256": "0" * 64,
                "outcome": "PASSED",
                "result_artifact_sha256": stable_sha256({"test": item.test_ids[0]}),
                "candidate_payload_sha256": stable_sha256(
                    {"test": item.test_ids[0], "runner": "fixture"}
                ),
                "candidate_payload_size_bytes": 1,
            },
        )
        for item in obligations
    )
    conflict = seal_receipt(
        ConflictSetBinding,
        {
            "receipt_id": "conflict-set",
            "policy_id": "candidate-conflict-unattested",
            "policy_version": "0.0.0",
            "policy_sha256": "3" * 64,
            "observed_at": observed,
            "valid_until": valid,
            "project_id": path.project_id,
            "source_snapshot": path.source_snapshot,
            "build_artifact_sha256": build_sha,
            "required_scope_sha256": release_input_module._static_path_replay_sha256(path),
            "candidate_scope_sha256": "0" * 64,
            "propositions": (
                {
                    "statement_id": "candidate-proposition-1",
                    "payload_sha256": stable_sha256({"claim": "candidate"}),
                    "payload_size_bytes": 1,
                },
            ),
            "proposition_ids": ("candidate-proposition-1",),
            "unresolved_conflict_ids": (),
        },
    )
    human = seal_receipt(
        HumanConfirmationSetBinding,
        {
            "receipt_id": "human-confirmation-set",
            "policy_id": "candidate-human-unattested",
            "policy_version": "0.0.0",
            "policy_sha256": "4" * 64,
            "observed_at": observed,
            "valid_until": valid,
            "project_id": path.project_id,
            "source_snapshot": path.source_snapshot,
            "build_artifact_sha256": build_sha,
            "required_scope_sha256": release_input_module._static_path_replay_sha256(path),
            "candidate_scope_sha256": "0" * 64,
            "confirmations": (
                {
                    "statement_id": "candidate-confirmation-1",
                    "payload_sha256": stable_sha256({"confirmation": "candidate"}),
                    "payload_size_bytes": 1,
                },
            ),
            "confirmation_ids": ("candidate-confirmation-1",),
        },
    )
    reasoning = ReasoningPolicy.load()
    candidate_run = AssuranceRun.model_validate(
        {
            "schema_version": "2.0.0",
            "run_id": "11111111-1111-1111-1111-111111111111",
            "trace_id": "22222222-2222-2222-2222-222222222222",
            "reasoning_policy_version": reasoning.schema_version,
            "reasoning_policy_sha256": reasoning.policy_sha256,
            "reasoning_eval_set_id": reasoning.retrieval_eval_set_id,
            "reasoning_eval_set_sha256": reasoning.retrieval_eval_set_sha256,
            "source_snapshot": path.source_snapshot,
            "source_graph_sha256": path.source_graph_sha256,
            "ontology_id": path.ontology_id,
            "ontology_version": path.ontology_version,
            "ontology_sha256": path.ontology_sha256,
            "source_profile_id": path.profile_id,
            "source_profile_version": path.profile_version,
            "source_profile_sha256": path.profile_sha256,
            "normalized_graph_sha256": path.normalized_graph_sha256,
            "request": {
                "requirement": "Assess the generic candidate change",
                "changed_paths": ["src/generic/component.py"],
                "change_intent": "PLANNED_CHANGE",
                "project_id": path.project_id,
                "source_ref": path.source_snapshot,
            },
            "evidence": [
                {
                    "evidence_id": "evidence-1",
                    "kind": "candidate",
                    "label": "candidate evidence",
                    "source": "fixture",
                    "state": "UNVERIFIED",
                }
            ],
            "impacts": [
                {
                    "entity_id": target,
                    "label": target,
                    "kind": "generic",
                    "relation": "impacts",
                    "severity": "LOW",
                    "evidence_strength": 0.5,
                    "strength_basis": "fixture candidate",
                    "evidence_ids": ["evidence-1"],
                }
                for target in path.required_scope.material_output_ids
            ],
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "Candidate claim",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "selected_tests": [
                {
                    "test_id": "selected-1",
                    "label": "candidate test",
                    "classification": "MANDATORY",
                    "reason": "candidate selection",
                    "evidence_ids": ["evidence-1"],
                }
            ],
        }
    )
    analysis = release_input_module._candidate_analysis_binding(candidate_run)
    change_body = {
        "project_id": path.project_id,
        "source_snapshot": path.source_snapshot,
        "environment_id": "candidate-environment",
        "change_artifact_id": "change-fixture",
        "change_artifact_sha256": __import__("hashlib").sha256(CHANGE_BYTES).hexdigest(),
        "change_artifact_size_bytes": len(CHANGE_BYTES),
        "build_artifact_id": "build-fixture",
        "build_artifact_sha256": build_sha,
        "build_artifact_size_bytes": len(BUILD_BYTES),
    }
    change_build = release_input_module.ChangeBuildBinding.model_validate(
        {**change_body, "binding_sha256": stable_sha256(change_body)}
    )
    candidate_scope = release_input_module.candidate_scope_sha256(analysis, change_build, path)

    def rescope(receipt):
        body = receipt.model_dump(mode="json")
        body.pop("receipt_sha256")
        body["candidate_scope_sha256"] = candidate_scope
        return seal_receipt(type(receipt), body)

    risks = tuple(rescope(item) for item in risks)
    obligations = tuple(rescope(item) for item in obligations)
    executions = tuple(rescope(item) for item in executions)
    conflict = rescope(conflict)
    human = rescope(human)
    return {
        "candidate_path_artifact": path,
        "graph": system["graph"],
        "propagation_policy": propagation,
        "path_policy": path_policy,
        "trusted_edge_replay": replay_input(system),
        "policy": policy,
        "candidate_run": candidate_run,
        "project_id": path.project_id,
        "source_snapshot": path.source_snapshot,
        "environment_id": "candidate-environment",
        "change_artifact_id": "change-fixture",
        "change_artifact_content": CHANGE_BYTES,
        "build_artifact_id": "build-fixture",
        "build_artifact_content": BUILD_BYTES,
        "risk_receipts": risks,
        "obligation_receipts": obligations,
        "execution_receipts": executions,
        "conflict_set": conflict,
        "human_confirmation_set": human,
        "policy_bindings": bindings,
    }


def _codes(result):
    return {item.code for item in result.gaps}


def _reseal(receipt, **updates):
    body = receipt.model_dump(mode="json")
    body.pop("receipt_sha256")
    body.update(updates)
    return seal_receipt(type(receipt), body)


def test_policy_is_self_hashed_and_externally_pinned(tmp_path: Path) -> None:
    policy = load_release_input_policy(POLICY_PATH)
    assert policy.sha256 == DEFAULT_RELEASE_INPUT_POLICY_SHA256

    changed = deepcopy(policy.model_dump(mode="json", by_alias=True))
    changed["maximumArtifactBytes"] += 1
    target = tmp_path / "policy.json"
    target.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ReleaseInputContractError):
        load_release_input_policy(target)


def test_complete_local_binding_is_deterministic_canonical_and_never_release_eligible() -> None:
    system = build_system()
    inputs = _candidate_inputs(system)
    first = compile_immutable_release_input(**inputs)
    permuted = {
        **inputs,
        "risk_receipts": tuple(reversed(inputs["risk_receipts"])),
        "obligation_receipts": tuple(reversed(inputs["obligation_receipts"])),
        "execution_receipts": tuple(reversed(inputs["execution_receipts"])),
        "policy_bindings": tuple(reversed(inputs["policy_bindings"])),
    }
    second = compile_immutable_release_input(**permuted)

    assert first == second
    assert first.canonical_candidate_binding_complete is True
    assert first.artifact is not None
    assert first.artifact.canonical_candidate_binding_complete is True
    assert first.artifact.release_prerequisites_complete is False
    assert first.artifact.release_eligible is False
    assert first.artifact.release_interlock == "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    assert GovernancePolicy.load().release_authority.enabled is False


@pytest.mark.parametrize(
    "field",
    ["risk_receipts", "obligation_receipts", "execution_receipts", "policy_bindings"],
)
def test_empty_required_partition_fails_closed(field: str) -> None:
    inputs = _candidate_inputs(build_system())
    inputs[field] = ()
    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY in _codes(result)


def test_missing_conflict_or_human_set_never_claims_verified_absence() -> None:
    for field in ("conflict_set", "human_confirmation_set"):
        inputs = _candidate_inputs(build_system())
        inputs[field] = None
        result = compile_immutable_release_input(**inputs)
        assert result.artifact is None
        assert ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY in _codes(result)


def test_mutated_build_bytes_break_exact_candidate_build_binding() -> None:
    inputs = _candidate_inputs(build_system())
    inputs["build_artifact_content"] = b"different-build"
    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_COVERAGE_MISMATCH in _codes(result)


def test_duplicate_and_unknown_execution_coverage_fails_closed() -> None:
    inputs = _candidate_inputs(build_system())
    first = inputs["execution_receipts"][0]
    inputs["execution_receipts"] = (*inputs["execution_receipts"], first)
    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.DUPLICATE_RECEIPT_ID in _codes(result)
    assert ReleaseInputGapCode.TEST_EXECUTION_COVERAGE_MISMATCH in _codes(result)


def test_expired_receipt_fails_at_current_internal_time() -> None:
    inputs = _candidate_inputs(build_system())
    receipt = inputs["risk_receipts"][0]
    body = receipt.model_dump(mode="json")
    body.pop("receipt_sha256")
    body["valid_until"] = OBSERVED.strftime("%Y-%m-%dT%H:%M:%SZ")
    expired = seal_receipt(RiskReceiptBinding, body)
    inputs["risk_receipts"] = (expired, *inputs["risk_receipts"][1:])
    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.RECEIPT_EXPIRED_OR_FUTURE in _codes(result)


def test_rehashed_release_input_mutation_is_rejected() -> None:
    inputs = _candidate_inputs(build_system())
    compiled = compile_immutable_release_input(**inputs)
    assert compiled.artifact is not None
    body = compiled.artifact.model_dump(mode="json")
    body.pop("release_input_sha256")
    body["policy_bindings"][0]["policy_sha256"] = "f" * 64
    tampered = ImmutableReleaseInput.model_validate(
        {**body, "release_input_sha256": stable_sha256(body)}
    )

    result = verify_immutable_release_input(tampered, **inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.RELEASE_INPUT_TAMPERED in _codes(result)


def test_renamed_topology_keeps_the_same_complete_binding_class() -> None:
    first = compile_immutable_release_input(**_candidate_inputs(build_system()))
    renamed_system = build_system((("link-x", "unit-x", "unit-y"), ("link-y", "unit-y", "unit-z")))
    renamed = compile_immutable_release_input(**_candidate_inputs(renamed_system))

    assert first.artifact is not None and renamed.artifact is not None
    first_scope = first.artifact.complete_path_replay.required_scope
    renamed_scope = renamed.artifact.complete_path_replay.required_scope
    assert first_scope.path_count == renamed_scope.path_count
    assert (
        first.canonical_candidate_binding_complete
        == renamed.canonical_candidate_binding_complete
        is True
    )


def test_later_but_unexpired_candidate_is_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _candidate_inputs(build_system())
    first = compile_immutable_release_input(**inputs)
    assert first.artifact is not None
    later = OBSERVED + timedelta(minutes=2)
    monkeypatch.setattr(path_replay_module, "_utc_now", lambda: later)
    monkeypatch.setattr(release_input_module, "_utc_now", lambda: later)

    refreshed = verify_immutable_release_input(first.artifact, **inputs)

    assert refreshed.artifact is not None
    assert refreshed.artifact.evaluated_at != first.artifact.evaluated_at
    assert refreshed.canonical_candidate_binding_complete is True


def test_receipt_with_nonpositive_lifetime_fails_closed() -> None:
    inputs = _candidate_inputs(build_system())
    receipt = inputs["risk_receipts"][0]
    invalid = _reseal(receipt, valid_until=receipt.observed_at)
    inputs["risk_receipts"] = (invalid, *inputs["risk_receipts"][1:])

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.RECEIPT_EXPIRED_OR_FUTURE in _codes(result)


@pytest.mark.parametrize("field", ["conflict_set", "human_confirmation_set"])
def test_candidate_aggregate_cannot_be_reused_across_build_scope(field: str) -> None:
    inputs = _candidate_inputs(build_system())
    inputs[field] = _reseal(inputs[field], build_artifact_sha256="f" * 64)

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH in _codes(result)


def test_required_partition_gap_identity_names_each_missing_partition() -> None:
    identities = set()
    for field in ("risk_receipts", "obligation_receipts", "execution_receipts"):
        inputs = _candidate_inputs(build_system())
        inputs[field] = ()
        result = compile_immutable_release_input(**inputs)
        identities.add(
            next(
                gap.identity_sha256
                for gap in result.gaps
                if gap.code is ReleaseInputGapCode.REQUIRED_PARTITION_EMPTY
            )
        )
    assert len(identities) == 3


@pytest.mark.parametrize(
    ("partition", "updates"),
    [
        ("obligation_receipts", {"candidate_payload_sha256": "a" * 64}),
        ("execution_receipts", {"outcome": "FAILED"}),
    ],
)
def test_candidate_receipt_mutation_cannot_replay_old_binding(
    partition: str, updates: dict[str, str]
) -> None:
    inputs = _candidate_inputs(build_system())
    baseline = compile_immutable_release_input(**inputs)
    assert baseline.artifact is not None
    first = inputs[partition][0]
    inputs[partition] = (_reseal(first, **updates), *inputs[partition][1:])

    result = verify_immutable_release_input(baseline.artifact, **inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.RELEASE_INPUT_TAMPERED in _codes(
        result
    ) or ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH in _codes(result)


def test_inconsistent_risk_aggregate_roots_fail_closed() -> None:
    inputs = _candidate_inputs(build_system())
    if len(inputs["risk_receipts"]) < 2:
        pytest.skip("fixture has one material target")
    second = inputs["risk_receipts"][1]
    risk = second.risk_result.model_dump(mode="json")
    risk.pop("result_sha256")
    risk["assessments"][0]["score"] += 1
    risk["result_sha256"] = stable_sha256(risk)
    inputs["risk_receipts"] = (
        inputs["risk_receipts"][0],
        _reseal(second, risk_result=risk),
        *inputs["risk_receipts"][2:],
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_COVERAGE_MISMATCH in _codes(result)


def test_shared_rehashed_but_wrong_risk_result_is_replayed_and_rejected() -> None:
    inputs = _candidate_inputs(build_system())
    original = inputs["risk_receipts"][0].risk_result.model_dump(mode="json")
    original.pop("result_sha256")
    original["assessments"][0]["score"] = 999
    original["assessments"][0]["level"] = "HIGH"
    changed = {**original, "result_sha256": stable_sha256(original)}
    inputs["risk_receipts"] = tuple(
        _reseal(item, risk_result=changed) for item in inputs["risk_receipts"]
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_COVERAGE_MISMATCH in _codes(result)


def test_shared_rehashed_fake_risk_path_substitution_is_rejected() -> None:
    inputs = _candidate_inputs(build_system())
    body = inputs["risk_receipts"][0].risk_result.model_dump(mode="json")
    body.pop("result_sha256")
    fake_by_target = {}
    for assessment in body["assessments"]:
        fake_paths = []
        for index, contribution in enumerate(assessment["path_contributions"]):
            fake = stable_sha256({"fake": assessment["target_id"], "index": index})
            contribution["path_sha256"] = fake
            fake_paths.append(fake)
        fake_by_target[assessment["target_id"]] = tuple(sorted(fake_paths))
    changed = {**body, "result_sha256": stable_sha256(body)}
    inputs["risk_receipts"] = tuple(
        _reseal(
            item,
            risk_result=changed,
            path_sha256s=fake_by_target[item.target_id],
        )
        for item in inputs["risk_receipts"]
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_COVERAGE_MISMATCH in _codes(result)


def test_valid_risk_path_ids_swapped_between_contributions_are_rejected() -> None:
    inputs = _candidate_inputs(build_system())
    body = inputs["risk_receipts"][0].risk_result.model_dump(mode="json")
    body.pop("result_sha256")
    contributions = body["assessments"][0]["path_contributions"]
    assert len(contributions) >= 2 and contributions[0]["score"] != contributions[1]["score"]
    contributions[0]["path_sha256"], contributions[1]["path_sha256"] = (
        contributions[1]["path_sha256"],
        contributions[0]["path_sha256"],
    )
    changed = {**body, "result_sha256": stable_sha256(body)}
    inputs["risk_receipts"] = tuple(
        _reseal(item, risk_result=changed) for item in inputs["risk_receipts"]
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_COVERAGE_MISMATCH in _codes(result)


def test_request_mutation_and_cross_graph_root_cannot_reuse_receipts() -> None:
    inputs = _candidate_inputs(build_system())
    request = inputs["candidate_run"].request.model_copy(
        update={"requirement": "A materially different generic change"}
    )
    inputs["candidate_run"] = inputs["candidate_run"].model_copy(
        update={"request": request, "source_snapshot": "other-snapshot"}
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH in _codes(result)


def test_changed_path_permutation_is_canonical() -> None:
    inputs = _candidate_inputs(build_system())
    request = inputs["candidate_run"].request.model_copy(
        update={"changed_paths": ["src/b.py", "src/a.py"]}
    )
    run = inputs["candidate_run"].model_copy(update={"request": request})
    first = release_input_module._candidate_analysis_binding(run)
    reversed_request = request.model_copy(update={"changed_paths": ["src/a.py", "src/b.py"]})
    second = release_input_module._candidate_analysis_binding(
        run.model_copy(update={"request": reversed_request})
    )
    assert first == second


def test_absolute_changed_path_is_typed_rejection_not_exception() -> None:
    inputs = _candidate_inputs(build_system())
    request = inputs["candidate_run"].request.model_copy(
        update={"changed_paths": ["C:/sensitive/source.cls"]}
    )
    inputs["candidate_run"] = inputs["candidate_run"].model_copy(update={"request": request})

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH in _codes(result)


def test_raw_candidate_secrets_are_not_serialized_in_binding() -> None:
    inputs = _candidate_inputs(build_system())
    evidence = (
        inputs["candidate_run"]
        .evidence[0]
        .model_copy(
            update={
                "source": "https://example.invalid/secrets/frontdoor?sid=secret-token",
                "attributes": {"password": "never-persist-this"},
            }
        )
    )
    inputs["candidate_run"] = inputs["candidate_run"].model_copy(update={"evidence": [evidence]})
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None  # scope digest correctly invalidates old receipts
    serialized = json.dumps(
        release_input_module._candidate_analysis_binding(inputs["candidate_run"]).model_dump(
            mode="json"
        )
    )
    assert "secret-token" not in serialized
    assert "never-persist-this" not in serialized


def test_global_governance_interlock_remains_incomplete() -> None:
    inputs = _candidate_inputs(build_system())
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is not None

    run = inputs["candidate_run"]
    run.governance = assess_run(run)
    decision = decide(run)

    assert decision.code is DecisionCode.INCOMPLETE
    assert decision.reasons == ["RELEASE_EVIDENCE_MODEL_INCOMPLETE"]
    assert result.artifact.release_eligible is False
    assert "RELEASE_EVIDENCE_MODEL_INCOMPLETE" in result.artifact.blocking_gap_codes
    assert "TEST_EXECUTION_SCOPE_NOT_ATTESTED" in result.artifact.blocking_gap_codes


def test_current_policy_rotation_and_candidate_reasoning_mismatch_block() -> None:
    inputs = _candidate_inputs(build_system())
    binding = next(item for item in inputs["policy_bindings"] if item.role == "reasoning")
    inputs["policy_bindings"] = tuple(
        item.model_copy(update={"policy_sha256": "f" * 64}) if item is binding else item
        for item in inputs["policy_bindings"]
    )
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.POLICY_ROOT_MISMATCH in _codes(result)


def test_path_replay_failure_is_propagated_as_typed_gap() -> None:
    inputs = _candidate_inputs(build_system())
    inputs["candidate_path_artifact"] = inputs["candidate_path_artifact"].model_copy(
        update={"artifact_sha256": "f" * 64}
    )
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.PATH_REPLAY_INCOMPLETE in _codes(result)


def test_stored_candidate_expiry_and_future_time_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _candidate_inputs(build_system())
    compiled = compile_immutable_release_input(**inputs)
    assert compiled.artifact is not None
    expired_at = OBSERVED + timedelta(minutes=31)
    monkeypatch.setattr(path_replay_module, "_utc_now", lambda: expired_at)
    monkeypatch.setattr(release_input_module, "_utc_now", lambda: expired_at)
    expired = verify_immutable_release_input(compiled.artifact, **inputs)
    assert ReleaseInputGapCode.RELEASE_INPUT_EXPIRED in _codes(expired)

    body = compiled.artifact.model_dump(mode="json")
    body.pop("release_input_sha256")
    body["evaluated_at"] = (OBSERVED + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body["valid_until"] = (OBSERVED + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body["freshness_seconds"] = 3600
    future = ImmutableReleaseInput.model_validate(
        {**body, "release_input_sha256": stable_sha256(body)}
    )
    future_result = verify_immutable_release_input(future, **inputs)
    assert ReleaseInputGapCode.RELEASE_INPUT_FROM_FUTURE in _codes(future_result)


def test_source_ref_only_mutation_invalidates_candidate_scope() -> None:
    inputs = _candidate_inputs(build_system())
    request = inputs["candidate_run"].request.model_copy(
        update={"source_ref": "different-working-tree"}
    )
    inputs["candidate_run"] = inputs["candidate_run"].model_copy(
        update={"request": request}
    )
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.CHANGE_BUILD_MISMATCH in _codes(result)


@pytest.mark.parametrize("field", ["change_artifact_id", "build_artifact_id"])
def test_absolute_artifact_locator_is_typed_rejection(field: str) -> None:
    inputs = _candidate_inputs(build_system())
    inputs[field] = "C:/sensitive/candidate.bin"
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.CHANGE_BUILD_MISMATCH in _codes(result)


@pytest.mark.parametrize(
    "locator", ["/absolute/item", "../escape", ".", "segment./item", "CON/item"]
)
def test_unsafe_artifact_locator_variants_are_typed_rejections(locator: str) -> None:
    inputs = _candidate_inputs(build_system())
    inputs["change_artifact_id"] = locator
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.CHANGE_BUILD_MISMATCH in _codes(result)


def test_model_cannot_omit_permanent_release_gap() -> None:
    result = compile_immutable_release_input(**_candidate_inputs(build_system()))
    assert result.artifact is not None
    body = result.artifact.model_dump(mode="json")
    body.pop("release_input_sha256")
    body["blocking_gap_codes"].remove("VERIFIED_CHANGE_SET_MISSING")
    with pytest.raises(ValueError):
        ImmutableReleaseInput.model_validate(
            {**body, "release_input_sha256": stable_sha256(body)}
        )


def test_extra_execution_and_substituted_obligation_fail_exact_coverage() -> None:
    inputs = _candidate_inputs(build_system())
    execution = inputs["execution_receipts"][0]
    inputs["execution_receipts"] = (
        *inputs["execution_receipts"],
        _reseal(execution, receipt_id="unexpected-execution", test_id="unexpected-test"),
    )
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.TEST_EXECUTION_COVERAGE_MISMATCH in _codes(result)

    inputs = _candidate_inputs(build_system())
    obligation = inputs["obligation_receipts"][0]
    inputs["obligation_receipts"] = (
        _reseal(obligation, target_id="unknown-target"),
        *inputs["obligation_receipts"][1:],
    )
    result = compile_immutable_release_input(**inputs)
    assert result.artifact is None
    assert ReleaseInputGapCode.MATERIAL_TARGET_COVERAGE_MISMATCH in _codes(result)


@pytest.mark.parametrize("partition", ["evidence", "impacts", "claims", "selected_tests"])
def test_candidate_partition_mutation_invalidates_cross_receipt_scope(partition: str) -> None:
    inputs = _candidate_inputs(build_system())
    run = inputs["candidate_run"]
    records = list(getattr(run, partition))
    record = records[0]
    if partition == "evidence":
        records[0] = record.model_copy(update={"label": "mutated evidence"})
    elif partition == "impacts":
        records[0] = record.model_copy(update={"label": "mutated impact"})
    elif partition == "claims":
        records[0] = record.model_copy(update={"text": "mutated claim"})
    else:
        records[0] = record.model_copy(update={"reason": "mutated selection"})
    inputs["candidate_run"] = run.model_copy(update={partition: records})

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.CHANGE_BUILD_MISMATCH in _codes(result)


def test_cross_scope_evidence_root_is_typed_rejection() -> None:
    inputs = _candidate_inputs(build_system())
    evidence = inputs["candidate_run"].evidence[0].model_copy(
        update={"attributes": {"source_snapshot": "different-snapshot"}}
    )
    inputs["candidate_run"] = inputs["candidate_run"].model_copy(
        update={"evidence": [evidence]}
    )

    result = compile_immutable_release_input(**inputs)

    assert result.artifact is None
    assert ReleaseInputGapCode.CANDIDATE_INPUT_MISMATCH in _codes(result)
