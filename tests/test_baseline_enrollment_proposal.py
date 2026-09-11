"""Fake CLI only: enrollment proposals never count as live Salesforce acceptance."""

from __future__ import annotations

import copy
import hashlib
import json
import pickle
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.baseline_enrollment_proposal import (
    OPERATOR_CONFIRMATION,
    BaselineEnrollmentProposal,
    EnrollmentProposalError,
    HostBaselineEnrollmentProposalService,
    serialize_baseline_enrollment_proposal,
)
from neo_sf_q_intel.classification_bootstrap import ClassificationError
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_baseline import HostBaselineConfiguration
from neo_sf_q_intel.live_read_evidence import CliCompleted
from tests.test_classification_bootstrap import ORG, USER, USERNAME, _fixture
from tests.test_local_validation_phase import _phase_compilation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    return _phase_compilation(tmp_path_factory.mktemp("enrollment-proposal"))


class _FakeInvoker:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.version = b"@salesforce/cli/2.148.3 win32-x64 node-v22.16.0\n"
        self.failure = None

    def run(self, invocation):
        self.calls.append(invocation)
        if self.failure:
            return self.failure
        if len(self.calls) == 1:
            assert invocation.arguments == ("--version",)
            return CliCompleted(0, self.version)
        return CliCompleted(0, json.dumps(self.responses[len(self.calls) - 2]).encode())


def _case(candidate, *, sandbox=False, edition="Developer Edition", alias="host-fixed"):
    _, _, now, responses, _, _, _ = _fixture(sandbox=sandbox, edition=edition)
    runner = _FakeInvoker(responses)
    settings = Settings(_env_file=None, sf_operator_alias=alias, allow_llm=False)
    captures = []

    def capture():
        captures.append(True)
        return candidate

    service = HostBaselineEnrollmentProposalService(
        settings, ROOT, capture_compilation=capture, runner=runner, clock=lambda: now
    )
    token = service.confirm_classification_only(
        operator_confirmation=OPERATOR_CONFIRMATION, task_authority_sha256="a" * 64
    )
    return service, token, runner, settings, captures, now


@pytest.mark.parametrize(
    "sandbox,edition", [(False, "Developer Edition"), (False, "Developer"), (True, "Enterprise")]
)
def test_derives_review_only_configuration_from_realistic_six_step_envelope(
    candidate, sandbox, edition, monkeypatch
):
    service, token, runner, settings, captures, now = _case(
        candidate, sandbox=sandbox, edition=edition
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Enrollment proposal attempted writes, receipts, live baseline or real process")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.live_baseline.sign_live_receipt", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.live_baseline.HostOwnedLiveBaselineService.run", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    before_settings = settings.model_dump()
    proposal = service.propose(token)
    assert captures == [True]
    assert settings.model_dump() == before_settings
    assert len(runner.calls) == 7
    sequence = [call.arguments for call in runner.calls[1:]]
    assert sequence[0] == sequence[5] == ("org", "display", "--target-org", "host-fixed", "--json")
    assert sequence[1][3] == "SELECT Id,IsSandbox,OrganizationType FROM Organization LIMIT 2"
    assert sequence[2] == sequence[4]
    assert sequence[2][3] == "/services/oauth2/userinfo"
    assert sequence[3][3] == f"SELECT Id,Username,IsActive FROM User WHERE Id = '{USER}' LIMIT 2"
    assert all(call.maximum_stdout_bytes == 262144 for call in runner.calls[1:])
    assert all(0 < call.timeout_seconds <= 30 for call in runner.calls)
    assert proposal.authority_class == "REVIEW_PROPOSAL_ONLY"
    assert not proposal.authorizes_execution
    assert proposal.installed_salesforce_cli_version == "2.148.3"
    assert proposal.source.compilation_sha256 == candidate.compilation_sha256
    assert proposal.source.candidate_tree_sha256s == tuple(
        sorted({value.candidate_tree_sha256 for value in candidate.required_local_validations})
    )
    assert proposal.classification_pins.environment_class == (
        "SANDBOX" if sandbox else "DEVELOPER_EDITION"
    )
    assert (
        proposal.classification_pins.org_fingerprint_sha256
        == hashlib.sha256(ORG.encode()).hexdigest()
    )
    assert (
        proposal.classification_pins.actor_user_id_sha256
        == hashlib.sha256(USER.encode()).hexdigest()
    )
    assert proposal.draft_configuration.live_read.api_version == "67.0"
    assert proposal.draft_configuration.local_validation_phase_policy is None
    assert proposal.expires_at <= token.expires_at
    serialized = serialize_baseline_enrollment_proposal(proposal, observed_at=now)
    assert json.loads(serialized)["authorizesExecution"] is False
    for secret in (ORG, USER, USERNAME, "sensitive-canary", "example.my.salesforce.com", str(ROOT)):
        assert secret.encode() not in serialized
        assert secret not in repr(proposal)
    with pytest.raises(ValidationError):
        HostBaselineConfiguration.model_validate_json(serialized)


@pytest.mark.parametrize("edition", ["Enterprise Edition", "Unlimited Edition", "Unknown", ""])
def test_production_unknown_stop_before_userinfo_or_application_access(candidate, edition):
    service, token, runner, _, _, _ = _case(candidate, edition=edition)
    with pytest.raises(ClassificationError):
        service.propose(token)
    assert len(runner.calls) == 3  # Local version + display + Organization only.
    assert all("api" not in item.arguments for item in runner.calls)


@pytest.mark.parametrize("position", [2, 4])
@pytest.mark.parametrize("field", ["user_id", "organization_id", "preferred_username"])
def test_subject_reconciliation_and_drift(candidate, position, field):
    service, token, runner, _, _, _ = _case(candidate)
    runner.responses[position]["result"]["body"][field] = {
        "user_id": "005000000000002AAA",
        "organization_id": "00D000000000002AAA",
        "preferred_username": "other@example.invalid",
    }[field]
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        service.propose(token)
    assert len(runner.calls) <= 6


@pytest.mark.parametrize(
    "fault", ["non2xx", "redirect", "malformed_body", "header", "bool_status", "oversize", "extra"]
)
def test_invalid_transport_and_oversize_are_sanitized(candidate, fault):
    service, token, runner, _, _, _ = _case(candidate)
    result = runner.responses[2]["result"]
    if fault in ("non2xx", "redirect", "bool_status"):
        result["statusCode"] = {"non2xx": 500, "redirect": 302, "bool_status": True}[fault]
    elif fault == "malformed_body":
        result["body"] = "sensitive-canary"
    elif fault == "header":
        result["headers"]["Location"] = "https://example.invalid/sensitive-canary"
    elif fault == "oversize":
        result["body"]["profile"] = "sensitive-canary" * 30000
    else:
        result["untrusted"] = True
    with pytest.raises((ClassificationError, EnrollmentProposalError)) as error:
        service.propose(token)
    assert len(runner.calls) == 4
    assert "sensitive-canary" not in str(error.value)


@pytest.mark.parametrize(
    "position,field,value",
    [
        (3, "IsActive", False),
        (5, "id", "00D000000000002AAA"),
        (5, "username", "other@example.invalid"),
        (5, "apiVersion", "68.0"),
        (0, "apiVersion", "67.0 --query injected"),
    ],
)
def test_active_user_display_drift_and_injected_version_fail(candidate, position, field, value):
    service, token, runner, _, _, _ = _case(candidate)
    result = runner.responses[position]["result"]
    if position == 3:
        result = result["records"][0]
    result[field] = value
    with pytest.raises(ClassificationError):
        service.propose(token)
    assert len(runner.calls) == position + 2


@pytest.mark.parametrize("completed", range(1, 8))
def test_expiry_after_each_operation_cannot_return_proposal(candidate, completed):
    service, token, runner, _, _, now = _case(candidate)
    service._clock = lambda: token.expires_at if len(runner.calls) >= completed else now
    with pytest.raises(EnrollmentProposalError, match="EXPIRED"):
        service.propose(token)
    assert len(runner.calls) == completed


def test_confirmation_is_explicit_local_instance_bound_and_one_use(candidate):
    service, token, runner, _, captures, _ = _case(candidate)
    with pytest.raises(EnrollmentProposalError, match="CONFIRMATION_REQUIRED"):
        service.confirm_classification_only(
            operator_confirmation="yes", task_authority_sha256="a" * 64
        )
    for supplied in (
        None,
        {},
        "confirm",
        replace(token),
        copy.copy(service).confirm_classification_only(
            operator_confirmation=OPERATOR_CONFIRMATION, task_authority_sha256="b" * 64
        ),
    ):
        with pytest.raises(EnrollmentProposalError, match="CONFIRMATION_REQUIRED"):
            service.propose(supplied)
    assert runner.calls == [] and captures == []
    with pytest.raises(TypeError, match="PROCESS_LOCAL"):
        pickle.dumps(token)
    service.propose(token)
    with pytest.raises(EnrollmentProposalError, match="CONFIRMATION_REQUIRED"):
        service.propose(token)
    assert len(runner.calls) == 7


def test_fixed_alias_ignores_display_alias_and_rejects_scope_overrides(candidate):
    service, token, runner, settings, captures, _ = _case(candidate, alias="renamed-host-alias")
    for payload in (runner.responses[0], runner.responses[5]):
        payload["result"]["alias"] = "different-display-preference"
    proposal = service.propose(token)
    assert proposal.classification_pins.alias == settings.sf_operator_alias
    assert all(
        call.arguments[call.arguments.index("--target-org") + 1] == "renamed-host-alias"
        for call in runner.calls[1:]
    )
    before = len(captures)
    for override in ({"alias": "other"}, {"query": "SELECT Id FROM Account"}, {"path": "other"}):
        with pytest.raises(TypeError):
            service.propose(token, **override)
    assert len(captures) == before


@pytest.mark.parametrize("change", ["alias", "policy_pin", "expiry", "source_failure"])
def test_refusals_before_dispatch_and_no_secret_exception(candidate, change):
    service, token, runner, settings, _, _ = _case(candidate)
    if change == "alias":
        settings.sf_operator_alias = "changed"
    elif change == "policy_pin":
        settings.live_target_policy_sha256 = "f" * 64
    elif change == "expiry":
        service._clock = lambda: token.expires_at
    else:

        def fail():
            raise RuntimeError("sensitive-canary")

        service._capture_compilation = fail
    with pytest.raises(EnrollmentProposalError) as error:
        service.propose(token)
    assert runner.calls == []
    assert "sensitive-canary" not in str(error.value)
    with pytest.raises(EnrollmentProposalError, match="CONFIRMATION_REQUIRED"):
        service.propose(token)


@pytest.mark.parametrize(
    "result,code",
    [
        (CliCompleted(1, b"sensitive-canary"), "COMMAND_FAILED"),
        (CliCompleted(-1, b"", timed_out=True), "TIMEOUT"),
        (CliCompleted(0, b"", quiescent=False), "NOT_QUIESCENT"),
        (CliCompleted(0, b"", output_exceeded=True), "OUTPUT_LIMIT"),
    ],
)
def test_invoker_failure_is_non_authorizing(candidate, result, code):
    service, token, runner, _, _, _ = _case(candidate)
    runner.failure = result
    with pytest.raises(EnrollmentProposalError, match=code):
        service.propose(token)
    assert len(runner.calls) == 1


def test_proposal_expiry_and_tamper_are_enforced_by_pure_serializer(candidate):
    service, token, _, _, _, now = _case(candidate)
    proposal = service.propose(token)
    for observed_at in (proposal.expires_at, now - timedelta(seconds=1), datetime.now()):
        with pytest.raises(EnrollmentProposalError, match="PROPOSAL_EXPIRED"):
            serialize_baseline_enrollment_proposal(proposal, observed_at=observed_at)
    with pytest.raises(ValidationError):
        serialize_baseline_enrollment_proposal(
            proposal.model_copy(update={"authorizes_execution": True}), observed_at=now
        )
    body = proposal.model_dump(mode="json", by_alias=True)
    body["draft_configuration"]["live_read"]["alias"] = "other"
    body["proposal_sha256"] = stable_sha256(
        {key: value for key, value in body.items() if key != "proposal_sha256"}
    )
    with pytest.raises(ValidationError):
        BaselineEnrollmentProposal.model_validate(body)


@pytest.mark.parametrize("pinned", [True, False])
def test_exact_target_policy_file_pin_is_reported_or_missing_is_explicit(candidate, pinned):
    service, _, runner, settings, _, _ = _case(candidate)
    expected = hashlib.sha256((ROOT / "config/live-target-policy.json").read_bytes()).hexdigest()
    if pinned:
        settings.live_target_policy_sha256 = expected
    token = service.confirm_classification_only(
        operator_confirmation=OPERATOR_CONFIRMATION, task_authority_sha256="b" * 64
    )
    proposal = service.propose(token)
    assert proposal.target_policy_bytes_sha256 == expected
    assert proposal.configuration_gaps == (
        () if pinned else ("TARGET_POLICY_EXTERNAL_PIN_REQUIRED",)
    )
    assert len(runner.calls) == 7


def test_wrong_configured_policy_pin_blocks_before_local_cli_version(candidate):
    service, _, runner, settings, _, _ = _case(candidate)
    settings.live_target_policy_sha256 = "f" * 64
    token = service.confirm_classification_only(
        operator_confirmation=OPERATOR_CONFIRMATION, task_authority_sha256="b" * 64
    )
    with pytest.raises(EnrollmentProposalError, match="POLICY_PIN_MISMATCH"):
        service.propose(token)
    assert runner.calls == []


@pytest.mark.parametrize("version", [b"not sf sensitive-canary", b"@salesforce/cli/2.1.0 win32\n"])
def test_cli_version_malformed_or_unsupported_stops_before_org_display(candidate, version):
    service, token, runner, _, _, _ = _case(candidate)
    runner.version = version
    with pytest.raises(EnrollmentProposalError, match="CLI_VERSION") as error:
        service.propose(token)
    assert len(runner.calls) == 1
    assert "sensitive-canary" not in str(error.value)


def test_implementation_change_during_command_blocks_before_dependent_work(candidate, monkeypatch):
    service, token, runner, _, _, _ = _case(candidate)
    monkeypatch.setattr(
        "neo_sf_q_intel.baseline_enrollment_proposal.enrollment_proposal_implementation_sha256",
        lambda: "b" * 64 if runner.calls else "a" * 64,
    )
    with pytest.raises(EnrollmentProposalError, match="IMPLEMENTATION_CHANGED"):
        service.propose(token)
    assert len(runner.calls) == 1
