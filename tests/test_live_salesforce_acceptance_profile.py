import json
from pathlib import Path

import pytest

from neo_sf_q_intel.classification_bootstrap import CLASSIFICATION_OPERATION_PLAN_SHA256
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipts import LiveReceiptContractError, parse_pinned_profile
from scripts.quality.check_genericity import (
    _live_salesforce_profile_findings,
    _strict_json_loads,
)

ROOT = Path(__file__).resolve().parents[1]


def _document(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _requirement_capabilities() -> dict[str, set[str]]:
    requirements = _document("config/requirement-registry.json")
    return {
        item["requirementId"]: set(item["capabilityIds"]) for item in requirements["requirements"]
    }


def test_live_salesforce_profile_has_all_non_substitutable_gates() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    findings = _live_salesforce_profile_findings(profile, _requirement_capabilities())

    assert findings == []


def test_classification_profile_pins_server_subject_operation_and_runnable_defaults() -> None:
    from neo_sf_q_intel.config import Settings
    from neo_sf_q_intel.live_target_plan import contract_sha256

    profile = _document("config/live-salesforce-acceptance-profile.json")
    policy = profile["orgClassificationPolicy"]
    assert policy["schemaVersion"] == "1.1.0"
    assert policy["classificationBootstrapOperationSha256"] == CLASSIFICATION_OPERATION_PLAN_SHA256
    assert policy["serverSubjectObservationRequired"] is True
    assert policy["repeatSubjectAndDisplayRequired"] is True
    assert policy["displayAliasIsInformational"] is True
    digest = stable_sha256(profile)
    assert Settings.model_fields["live_acceptance_profile_sha256"].default == digest
    target = _document("config/live-target-policy.json")
    assert target["acceptanceProfileSha256"] == digest
    assert target["sha256"] == contract_sha256(target)
    assert f"LIVE_ACCEPTANCE_PROFILE_SHA256={digest}" in (ROOT / ".env.example").read_text()
    parse_pinned_profile(profile, expected_profile_sha256=digest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", "1.0.0"),
        ("classificationBootstrapOperation", "ORG_DISPLAY_ONLY"),
        ("classificationBootstrapOperationSha256", "f" * 64),
        ("serverSubjectObservationRequired", False),
        ("repeatSubjectAndDisplayRequired", False),
        ("displayAliasIsInformational", False),
        ("bootstrapOutputMayLeaveHostBroker", True),
        ("unknownAuthority", True),
    ],
)
def test_rehashed_weakened_or_stale_classification_policy_cannot_become_runnable(field, value):
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["orgClassificationPolicy"][field] = value
    with pytest.raises(LiveReceiptContractError, match="profile is invalid"):
        parse_pinned_profile(profile, expected_profile_sha256=stable_sha256(profile))


def test_old_profile_pin_cannot_authorize_new_classification_operations():
    profile = _document("config/live-salesforce-acceptance-profile.json")
    with pytest.raises(LiveReceiptContractError, match="PROFILE_PIN_MISMATCH"):
        parse_pinned_profile(
            profile,
            expected_profile_sha256=(
                "c704b8a01bdc453c9602967e634b12db1c7b86545ad90d0c9381c84da00bb5da"
            ),
        )


def test_live_profile_rejects_fixture_substitution_and_optional_candidate_campaign() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["gates"][0]["fixtureMaySatisfy"] = True
    profile["candidateCampaign"]["requiredForLiveSalesforceCampaignCompletion"] = False

    findings = _live_salesforce_profile_findings(profile, _requirement_capabilities())

    assert {item.code for item in findings} >= {
        "SUBSTITUTABLE_LIVE_GATE",
        "WEAK_CANDIDATE_RECOVERY_POLICY",
    }


def test_profile_definition_cannot_be_edited_into_fabricated_runtime_success() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    for gate in [*profile["gates"], *profile["candidateGates"]]:
        gate["status"] = "PASSED"
        gate["evidenceRequirements"] = ["fixture", "screenshot", "manual claim"]

    findings = _live_salesforce_profile_findings(profile, _requirement_capabilities())

    assert sum(item.code == "MUTABLE_LIVE_GATE_STATUS" for item in findings) == 15


def test_profile_rejects_missing_authority_and_skippable_recovery() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    candidate = {item["gateId"]: item for item in profile["candidateGates"]}
    candidate["SF-C03"]["requiredInputReceiptRoles"].remove(
        "INDEPENDENT_MUTATION_AUTHORIZATION_RECEIPT"
    )
    candidate["SF-C06"]["activationRule"] = "ON_DEPENDENCIES_PASSED"

    codes = {
        item.code
        for item in _live_salesforce_profile_findings(profile, _requirement_capabilities())
    }

    assert {"MISSING_LIVE_AUTHORITY_INPUT", "SKIPPABLE_LIVE_RECOVERY"} <= codes


def test_profile_rejects_phase_target_and_profile_rotation() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["profileId"] = "rotated-without-validator-migration"
    profile["gates"][2]["acceptedEvidencePhases"] = ["DEPLOYED_CANDIDATE"]
    profile["liveTargetPlan"]["requiredRoots"][0] = "arbitrary caller value"

    codes = {
        item.code
        for item in _live_salesforce_profile_findings(profile, _requirement_capabilities())
    }

    assert {
        "INVALID_LIVE_PROFILE_INVARIANT",
        "WEAKENED_LIVE_GATE_INVARIANT",
        "WEAK_LIVE_TARGET_PLAN",
    } <= codes


def test_profile_rejects_production_and_metadata_scope_weakening() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["orgClassificationPolicy"]["allowedNonProductionClasses"].append("PRODUCTION")
    profile["metadataRetrievalPolicy"]["wildcardsAllowed"] = True

    codes = {
        item.code
        for item in _live_salesforce_profile_findings(profile, _requirement_capabilities())
    }

    assert "WEAK_LIVE_SAFETY_POLICY" in codes


def test_profile_rejects_unknown_fields_and_duplicate_json_keys() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["runtimeStatus"] = "PASSED"

    codes = {
        item.code
        for item in _live_salesforce_profile_findings(profile, _requirement_capabilities())
    }

    assert "UNKNOWN_LIVE_PROFILE_FIELD" in codes
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _strict_json_loads('{"profileId":"first","profileId":"second"}')


def test_profile_rejects_security_text_replacement_and_gate_reordering() -> None:
    profile = _document("config/live-salesforce-acceptance-profile.json")
    profile["gates"][5]["evidenceRequirements"] = [
        "persist session URL",
        "log token",
        "return credential",
    ]
    profile["gates"][0], profile["gates"][1] = (
        profile["gates"][1],
        profile["gates"][0],
    )

    codes = {
        item.code
        for item in _live_salesforce_profile_findings(profile, _requirement_capabilities())
    }

    assert "UNREVIEWED_LIVE_PROFILE_ROTATION" in codes
