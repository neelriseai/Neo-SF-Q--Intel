import json

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.governance_policy import DEFAULT_POLICY_PATH, GovernancePolicy


def policy_document() -> dict:
    return json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))


def test_governance_policy_defines_executable_measurements_and_controls() -> None:
    policy = GovernancePolicy.load()

    assert all(item.numerator_definition != item.denominator_definition for item in policy.metrics)
    assert all(item.minimum_sample_size >= 1 for item in policy.metrics)
    assert {item.metric for item in policy.metrics} == {
        "material_claim_evidence_coverage",
        "impact_evidence_coverage",
        "selected_test_evidence_coverage",
    }


def test_blocking_metric_requires_an_explicit_guardrail_hook() -> None:
    raw = policy_document()
    raw["controls"] = [
        item for item in raw["controls"] if item.get("metric") != "impact_evidence_coverage"
    ]

    with pytest.raises(ValidationError, match="blocking metrics require"):
        GovernancePolicy.model_validate(raw)


def test_metric_cannot_use_vague_or_missing_population_definitions() -> None:
    raw = policy_document()
    raw["metrics"][0]["denominator_definition"] = "claims"

    with pytest.raises(ValidationError, match="at least 20 characters"):
        GovernancePolicy.model_validate(raw)


def test_direct_guardrail_cannot_carry_an_unrelated_metric_threshold() -> None:
    raw = policy_document()
    raw["controls"][0]["metric"] = "impact_evidence_coverage"

    with pytest.raises(ValidationError, match="direct controls cannot reference"):
        GovernancePolicy.model_validate(raw)


def test_blocking_metric_cannot_be_guarded_only_by_nonblocking_control() -> None:
    raw = policy_document()
    control = next(
        item for item in raw["controls"] if item.get("metric") == "material_claim_evidence_coverage"
    )
    control["blocking"] = False

    with pytest.raises(ValidationError, match="blocking metrics require"):
        GovernancePolicy.model_validate(raw)


def test_policy_hash_changes_when_a_threshold_changes() -> None:
    original = GovernancePolicy.load()
    raw = policy_document()
    raw["metrics"][0]["target"] = 0.9

    changed = GovernancePolicy.model_validate(raw)

    assert original.sha256 != changed.sha256


def test_policy_requires_every_blocking_outcome_mapping() -> None:
    raw = policy_document()
    del raw["decision_mapping"]["DENY"]

    with pytest.raises(ValidationError, match="decision_mapping is incomplete"):
        GovernancePolicy.model_validate(raw)


def test_policy_rejects_fail_open_decision_mapping() -> None:
    raw = policy_document()
    raw["decision_mapping"]["DENY"] = "GO"

    with pytest.raises(ValidationError, match="cannot weaken"):
        GovernancePolicy.model_validate(raw)
