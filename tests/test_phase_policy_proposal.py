from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.local_validation_phase import (
    ALL_EVIDENCE_PHASES,
    HostLocalValidationPhasePolicy,
)
from neo_sf_q_intel.phase_policy_proposal import propose_local_validation_phase_policy
from tests.test_local_validation_phase import _phase_compilation


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    return _phase_compilation(tmp_path_factory.mktemp("phase-proposal"))


def test_proposal_is_offline_non_authorizing_and_never_installs_configuration(
    candidate, monkeypatch
):
    def no_action(*args, **kwargs):
        pytest.fail("Review proposal attempted external action or configuration writes")

    monkeypatch.setattr("subprocess.Popen", no_action)
    monkeypatch.setattr("socket.create_connection", no_action)
    monkeypatch.setattr(Path, "write_bytes", no_action)
    monkeypatch.setattr(Path, "write_text", no_action)
    captures = []

    def capture():
        captures.append(True)
        return candidate

    proposal = propose_local_validation_phase_policy(capture, observed_at=datetime.now(UTC))
    assert captures == [True]
    assert proposal.authority_class == "REVIEW_PROPOSAL_ONLY"
    assert proposal.authorizes_execution is False and proposal.operator_review_required is True
    assert proposal.unresolved_obligation_ids == tuple(
        value.obligation.obligation_id for value in candidate.required_local_validations
    )
    assert proposal.candidate_bundle_sha256 == candidate.candidate_bundle_sha256
    assert proposal.draft_policy.valid_until <= datetime.fromisoformat(
        candidate.verified_scope.valid_until
    )
    with pytest.raises(ValidationError):
        HostLocalValidationPhasePolicy.model_validate(proposal.model_dump(mode="python"))


@pytest.mark.parametrize("seconds", [0, -1, 3601, True, 1.5])
def test_proposal_requires_bounded_explicit_validity_before_capture(candidate, seconds):
    def no_capture():
        pytest.fail("Invalid proposal bounds reached source capture")

    with pytest.raises(ValueError, match="LOCAL_PHASE_PROPOSAL_VALIDITY_INVALID"):
        propose_local_validation_phase_policy(
            no_capture, observed_at=datetime.now(UTC), maximum_validity_seconds=seconds
        )


def test_expired_capture_cannot_generate_current_authority(candidate):
    with pytest.raises(ValueError):
        propose_local_validation_phase_policy(
            lambda: candidate, observed_at=datetime.now(UTC) + timedelta(days=1)
        )


def test_proposal_cannot_silently_narrow_an_all_phase_source_obligation(candidate):
    body = candidate.model_dump(mode="json", exclude={"compilation_sha256"})
    body["required_local_validations"][0]["obligation"]["required_evidence_phases"] = [
        value.value for value in ALL_EVIDENCE_PHASES
    ]
    body["compilation_sha256"] = stable_sha256(body)
    changed = CandidateTargetCompilation.model_validate(body)
    with pytest.raises(ValueError, match="LOCAL_PHASE_PROPOSAL_EXPLICIT_SOURCE_REQUEST_REQUIRED"):
        propose_local_validation_phase_policy(lambda: changed, observed_at=datetime.now(UTC))


def test_nonlocal_unknown_blockers_cannot_be_packaged_as_baseline_permission(candidate):
    body = candidate.model_dump(mode="json", exclude={"compilation_sha256"})
    body["blocking_reason_codes"].append("UNKNOWN_CHANGED_FILE")
    body["compilation_sha256"] = stable_sha256(body)
    changed = CandidateTargetCompilation.model_validate(body)
    with pytest.raises(ValueError, match="LOCAL_PHASE_PROPOSAL_NONLOCAL_BLOCKER"):
        propose_local_validation_phase_policy(lambda: changed, observed_at=datetime.now(UTC))
