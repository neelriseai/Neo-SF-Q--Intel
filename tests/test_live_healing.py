from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.live_healing import (
    AssertionFacts,
    AssertionObservation,
    BaselineFacts,
    CandidateFacts,
    CandidateObservation,
    CloseFacts,
    HealingAssertion,
    HealingGap,
    HealingLease,
    HealingObservation,
    HealingRequest,
    HealingStage,
    LiveHealingOrchestrator,
    RestorationFacts,
    SourceHealingTarget,
    StateFacts,
    digest,
)
from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptScope
from neo_sf_q_intel.live_target_plan import BrowserTarget

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def h(text):
    return sha256(text.encode()).hexdigest()


def request(*, metadata=False, prefix="alpha", target_count=1):
    assertions = tuple(
        sorted(
            (
                HealingAssertion(
                    obligation_sha256=h(prefix + str(index)),
                    original_locator_sha256=h("old" + str(index)),
                    semantic_identity_sha256=h("identity" + str(index)),
                    expected_value_sha256=h("expected" + str(index)),
                )
                for index in range(2)
            ),
            key=lambda item: item.obligation_sha256,
        )
    )
    targets = tuple(
        sorted(
            (
                SourceHealingTarget(
                    browser_intent=BrowserTarget(
                        sourceEntityIds=(f"component:{prefix}-{index}",),
                        applicationIntent=prefix,
                        actionClass="VIEW",
                        surfaceIntent=f"surface-{prefix}",
                    ),
                    planned_target_sha256=h("target" + prefix + str(index)),
                    source_assertion_contract_sha256=h("source-assertions"),
                    assertions=assertions,
                )
                for index in range(target_count)
            ),
            key=lambda item: item.planned_target_sha256,
        )
    )
    return HealingRequest(
        scope=ReceiptScope(
            campaign_id="campaign-test",
            project_id="project-test",
            source_contract_sha256=h("source"),
            candidate_sha256=h("candidate"),
            build_sha256=h("build"),
            operation_plan_sha256=h("plan"),
            restore_scope_sha256=h("restore"),
            policy_sha256=h("policy"),
            profile_sha256=h("profile"),
            org_fingerprint_sha256=h("org"),
            actor_fingerprint_sha256=h("actor"),
            recovery_deadline=NOW + timedelta(minutes=10),
        ),
        evidence_phase=EvidencePhase.DEPLOYED_CANDIDATE,
        target_plan_sha256=h("target-plan"),
        expected_execution_contract_sha256=h("expected-contract"),
        classification_receipt_sha256=h("classification"),
        session_enrollment_receipt_sha256=h("enrollment"),
        expected_aut_prestate_sha256=h("aut-prestate"),
        expected_residue_sha256=h("residue"),
        temporary_metadata_plan_sha256=h("metadata-plan") if metadata else None,
        temporary_metadata_expected_state_sha256=h("aut-changed") if metadata else None,
        targets=targets,
    )


class FakeAuthority:
    """Offline seam only; not cryptographic or live acceptance evidence."""

    def __init__(self):
        self.claims = 0
        self.commands = []
        self.reject_stage = None
        self.reject_observation = False

    def claim(self, req, now):
        self.claims += 1
        if self.claims > 1:
            raise RuntimeError("one-use replay")
        return HealingLease(
            request_sha256=digest(req),
            authority_receipt_sha256=h("authority"),
            restore_authority_receipt_sha256=h("recovery-authority"),
            one_use_claim_sha256=h("claim"),
            issued_at=now,
            expires_at=now + timedelta(minutes=2),
            recovery_deadline=now + timedelta(minutes=5),
        )

    def revalidate(self, req, lease, command, now):
        self.commands.append(command)
        assert command.request_sha256 == lease.request_sha256 == digest(req)
        if command.stage == self.reject_stage:
            raise RuntimeError("authority rejected")

    def verify_observation(self, lease, command, observation):
        if self.reject_observation:
            raise RuntimeError("untrusted result")


class FakeDispatch:
    def __init__(self, req):
        self.req = req
        self.commands = []
        self.transform = lambda command, facts: facts
        self.transform_observation = lambda value: value
        self.clock = NOW

    @staticmethod
    def assertions(target, *, stale=False, healed=False):
        return tuple(
            AssertionObservation(
                obligation_sha256=item.obligation_sha256,
                locator_sha256=h("replacement")
                if healed and index == 0
                else item.original_locator_sha256,
                outcome="LOCATOR_NOT_FOUND" if stale and index == 0 else "PASSED",
                observed_value_sha256=None if stale and index == 0 else item.expected_value_sha256,
            )
            for index, item in enumerate(target.assertions)
        )

    def execute(self, command, lease):
        self.commands.append(command)
        target = next(
            item for item in self.req.targets if item.planned_target_sha256 == command.target_sha256
        )
        stage = command.stage
        if stage == HealingStage.CAPTURE_BASELINE:
            facts = BaselineFacts(
                headless=True,
                aut_state_sha256=h("aut-prestate"),
                residue_sha256=h("residue"),
                locator_state_sha256=h("locator-prestate"),
                assertions=self.assertions(target),
            )
        elif stage == HealingStage.APPLY_TEMPORARY_METADATA:
            facts = StateFacts(
                before_sha256=h("aut-prestate"), after_sha256=h("aut-changed"), pending_rebinds=0
            )
        elif stage in {HealingStage.PROVE_STALE, HealingStage.RERUN_ASSERTIONS}:
            facts = AssertionFacts(
                assertions=self.assertions(
                    target,
                    stale=stage == HealingStage.PROVE_STALE,
                    healed=stage == HealingStage.RERUN_ASSERTIONS,
                )
            )
        elif stage == HealingStage.DISCOVER_CANDIDATES:
            facts = CandidateFacts(
                candidates=(
                    CandidateObservation(
                        obligation_sha256=target.assertions[0].obligation_sha256,
                        locator_sha256=h("replacement"),
                        semantic_identity_sha256=target.assertions[0].semantic_identity_sha256,
                        supporting_evidence_sha256=h("independent-metadata-evidence"),
                        visible=True,
                        enabled=True,
                    ),
                )
            )
        elif stage == HealingStage.REBIND_LOCATORS:
            facts = StateFacts(
                before_sha256=h("locator-prestate"),
                after_sha256=h("locator-changed"),
                pending_rebinds=1,
            )
        elif stage == HealingStage.RESET_LOCATORS:
            facts = StateFacts(
                before_sha256=h("locator-changed"),
                after_sha256=h("locator-prestate"),
                pending_rebinds=0,
            )
        elif stage == HealingStage.RESTORE_METADATA:
            facts = StateFacts(
                before_sha256=h("aut-changed"), after_sha256=h("aut-prestate"), pending_rebinds=0
            )
        elif stage == HealingStage.VERIFY_RESTORATION:
            facts = RestorationFacts(
                aut_state_sha256=h("aut-prestate"),
                residue_sha256=h("residue"),
                locator_state_sha256=h("locator-prestate"),
                pending_rebinds=0,
                assertions=self.assertions(target),
            )
        else:
            facts = CloseFacts(
                context_closed=True, browser_closed=True, temporary_rebind_store_closed=True
            )
        facts = self.transform(command, facts)
        value = HealingObservation(
            command_sha256=digest(command),
            durable_receipt_sha256=h("receipt" + digest(command)),
            observed_at=self.clock,
            facts=facts,
        )
        return self.transform_observation(value)

    def close_local(self, command):
        assert command.stage == HealingStage.CLOSE_SESSION
        return self.execute(command, None)


def run(req=None, *, transform=None, authority=None, **options):
    req = req or request()
    authority = authority or FakeAuthority()
    dispatch = FakeDispatch(req)
    if transform:
        dispatch.transform = transform
    orchestrator = LiveHealingOrchestrator(
        authority, dispatch, execution_enabled=True, now=lambda: dispatch.clock, **options
    )
    return orchestrator.run(req), dispatch, authority


def at(stage, change):
    return lambda command, facts: change(facts) if command.stage == stage else facts


def test_disabled_default_dispatches_nothing():
    req = request()
    authority, dispatch = FakeAuthority(), FakeDispatch(req)
    report = LiveHealingOrchestrator(authority, dispatch).run(req)
    assert report.gap_codes == (HealingGap.EXECUTION_DISABLED,)
    assert not authority.claims and not dispatch.commands


def test_metadata_mutation_requires_separate_host_enable_and_candidate_phase():
    report, dispatch, authority = run(request(metadata=True))
    assert report.gap_codes == (HealingGap.METADATA_MUTATION_DISABLED,)
    assert not authority.claims and not dispatch.commands
    raw = request(metadata=True).model_dump(mode="json")
    raw["evidence_phase"] = "LIVE_BASELINE"
    with pytest.raises(ValidationError):
        HealingRequest.model_validate(raw)


def test_complete_sequence_preserves_all_obligations_and_separate_restoration():
    report, dispatch, authority = run(request(metadata=True), metadata_mutation_enabled=True)
    target = report.targets[0]
    assert target.status == "OUTCOME_VERIFIED"
    assert target.metadata_restored_exactly and target.locator_state_reset_exactly
    assert target.session_closed and len(target.obligation_sha256s) == 2
    assert len(target.stale_obligation_sha256s) == 1
    assert not report.acceptance_credit and not report.release_eligible
    stages = [item.stage for item in dispatch.commands]
    assert stages == list(HealingStage)
    assert dispatch.commands[:-1] == authority.commands
    assert all(item.deadline <= NOW + timedelta(seconds=30) for item in dispatch.commands)
    assert all(item.previous_observation_sha256 for item in dispatch.commands[1:])


@pytest.mark.parametrize(
    "stage",
    [HealingStage.CAPTURE_BASELINE, HealingStage.PROVE_STALE, HealingStage.RERUN_ASSERTIONS],
)
def test_omitted_independent_assertion_blocks_even_if_one_passes(stage):
    report, _, _ = run(
        transform=at(
            stage, lambda facts: facts.model_copy(update={"assertions": facts.assertions[:1]})
        )
    )
    assert report.targets[0].status != "OUTCOME_VERIFIED"


@pytest.mark.parametrize("outcome", ["PASSED", "ASSERTION_MISMATCH", "NOT_RUN"])
def test_arbitrary_failure_or_no_failure_is_not_stale_locator(outcome):
    def alter(facts):
        first = facts.assertions[0].model_copy(update={"outcome": outcome})
        return facts.model_copy(update={"assertions": (first, *facts.assertions[1:])})

    report, dispatch, _ = run(transform=at(HealingStage.PROVE_STALE, alter))
    assert HealingGap.STALE_LOCATOR_NOT_PROVEN in report.gap_codes
    assert not any(item.stage == HealingStage.REBIND_LOCATORS for item in dispatch.commands)


@pytest.mark.parametrize("variant", ["none", "duplicate", "identity", "old", "hidden", "disabled"])
def test_candidate_is_fresh_unique_and_semantically_bound(variant):
    def alter(facts):
        candidate = facts.candidates[0]
        if variant == "none":
            return CandidateFacts(candidates=())
        if variant == "duplicate":
            return CandidateFacts(candidates=(candidate, candidate))
        update = {
            "identity": {"semantic_identity_sha256": h("wrong")},
            "old": {"locator_sha256": request().targets[0].assertions[0].original_locator_sha256},
            "hidden": {"visible": False},
            "disabled": {"enabled": False},
        }[variant]
        return CandidateFacts(candidates=(candidate.model_copy(update=update),))

    report, dispatch, _ = run(transform=at(HealingStage.DISCOVER_CANDIDATES, alter))
    assert HealingGap.CANDIDATE_NOT_UNIQUE in report.gap_codes
    assert not any(item.stage == HealingStage.REBIND_LOCATORS for item in dispatch.commands)


@pytest.mark.parametrize(
    "field,value",
    [
        ("aut_state_sha256", h("wrong")),
        ("residue_sha256", h("residue-added")),
        ("locator_state_sha256", h("wrong")),
        ("pending_rebinds", 1),
    ],
)
def test_restoration_checks_independent_aut_residue_and_locator_reset(field, value):
    report, dispatch, _ = run(
        transform=at(
            HealingStage.VERIFY_RESTORATION, lambda facts: facts.model_copy(update={field: value})
        )
    )
    assert report.targets[0].status == "RECONCILIATION_REQUIRED"
    assert dispatch.commands[-1].stage == HealingStage.CLOSE_SESSION


@pytest.mark.parametrize(
    "stage",
    [
        HealingStage.APPLY_TEMPORARY_METADATA,
        HealingStage.REBIND_LOCATORS,
        HealingStage.RERUN_ASSERTIONS,
    ],
)
def test_timeout_partial_effects_always_attempt_both_independent_recoveries(stage):
    def fail(_):
        raise TimeoutError("private DOM/session material must not be copied")

    report, dispatch, _ = run(
        request(metadata=True), metadata_mutation_enabled=True, transform=at(stage, fail)
    )
    assert HealingGap.OPERATION_TIMED_OUT in report.gap_codes
    stages = [item.stage for item in dispatch.commands]
    assert HealingStage.RESTORE_METADATA in stages
    if stage != HealingStage.APPLY_TEMPORARY_METADATA:
        assert HealingStage.RESET_LOCATORS in stages
    assert HealingStage.VERIFY_RESTORATION in stages and stages[-1] == HealingStage.CLOSE_SESSION
    assert "private DOM" not in report.model_dump_json()


def test_metadata_restore_failure_cannot_skip_locator_reset_or_session_close():
    def fail(_):
        raise RuntimeError("restore failed")

    report, dispatch, _ = run(
        request(metadata=True),
        metadata_mutation_enabled=True,
        transform=at(HealingStage.RESTORE_METADATA, fail),
    )
    assert report.targets[0].status == "RECONCILIATION_REQUIRED"
    assert dispatch.commands[-3].stage == HealingStage.RESET_LOCATORS
    assert dispatch.commands[-1].stage == HealingStage.CLOSE_SESSION


@pytest.mark.parametrize(
    "field", ["context_closed", "browser_closed", "temporary_rebind_store_closed"]
)
def test_every_session_and_temporary_state_container_must_close(field):
    report, _, _ = run(
        transform=at(
            HealingStage.CLOSE_SESSION, lambda facts: facts.model_copy(update={field: False})
        )
    )
    assert report.targets[0].status == "RECONCILIATION_REQUIRED"
    assert HealingGap.SESSION_CLOSURE_UNRESOLVED in report.gap_codes


def test_authority_revalidated_before_rebind_and_no_action_on_rejection():
    authority = FakeAuthority()
    authority.reject_stage = HealingStage.REBIND_LOCATORS
    report, dispatch, _ = run(authority=authority)
    assert HealingGap.AUTHORITY_REJECTED in report.gap_codes
    assert not any(item.stage == HealingStage.REBIND_LOCATORS for item in dispatch.commands)
    assert dispatch.commands[-1].stage == HealingStage.CLOSE_SESSION


def test_untrusted_observation_is_not_evidence():
    authority = FakeAuthority()
    authority.reject_observation = True
    report, dispatch, _ = run(authority=authority)
    assert HealingGap.OBSERVATION_INVALID in report.gap_codes
    assert report.targets[0].observation_sha256s == ()
    assert dispatch.commands[-1].stage == HealingStage.CLOSE_SESSION


@pytest.mark.parametrize("tamper", ["command", "future", "old", "unknown"])
def test_observation_scope_time_and_closed_shape_rejected(tamper):
    req = request()
    authority, dispatch = FakeAuthority(), FakeDispatch(req)
    changes = {
        "command": {"command_sha256": h("wrong")},
        "future": {"observed_at": NOW + timedelta(seconds=1)},
        "old": {"observed_at": NOW - timedelta(seconds=1)},
        "unknown": {"raw_dom": "secret must not escape"},
    }
    if tamper == "unknown":
        # model_construct extras are dropped; malicious adapter serialization still must be closed.
        dispatch.transform_observation = lambda value: value.model_copy(
            update={"facts": {**value.facts.model_dump(), **changes[tamper]}}
        )
    else:
        dispatch.transform_observation = lambda value: value.model_copy(update=changes[tamper])
    report = LiveHealingOrchestrator(
        authority, dispatch, execution_enabled=True, now=lambda: NOW
    ).run(req)
    assert report.targets[0].status != "OUTCOME_VERIFIED"
    assert "secret" not in report.model_dump_json()


def test_rejected_adapter_shape_emits_no_raw_payload_warning(recwarn, capsys, caplog):
    req = request()
    authority, dispatch = FakeAuthority(), FakeDispatch(req)
    dispatch.transform_observation = lambda value: value.model_copy(
        update={"facts": {**value.facts.model_dump(), "raw_dom": "PRIVATE-DOM-CANARY"}}
    )
    report = LiveHealingOrchestrator(
        authority, dispatch, execution_enabled=True, now=lambda: NOW
    ).run(req)
    captured = capsys.readouterr()
    assert (
        "PRIVATE-DOM-CANARY"
        not in report.model_dump_json() + captured.out + captured.err + caplog.text
    )
    assert not recwarn.list


@pytest.mark.parametrize(
    "stage,recovery",
    [
        (HealingStage.APPLY_TEMPORARY_METADATA, HealingStage.RESTORE_METADATA),
        (HealingStage.REBIND_LOCATORS, HealingStage.RESET_LOCATORS),
    ],
)
def test_authority_denied_before_dispatch_does_not_invent_recovery_write(stage, recovery):
    authority = FakeAuthority()
    authority.reject_stage = stage
    report, dispatch, _ = run(
        request(metadata=True), metadata_mutation_enabled=True, authority=authority
    )
    stages = [item.stage for item in dispatch.commands]
    assert stage not in stages and recovery not in stages
    assert stages[-1] == HealingStage.CLOSE_SESSION
    assert report.targets[0].status == "FAILED"


def test_unrelated_metadata_change_cannot_be_claimed_as_planned_change():
    report, _, _ = run(
        request(metadata=True),
        metadata_mutation_enabled=True,
        transform=at(
            HealingStage.APPLY_TEMPORARY_METADATA,
            lambda facts: facts.model_copy(update={"after_sha256": h("other")}),
        ),
    )
    assert HealingGap.OBSERVATION_INVALID in report.gap_codes
    assert report.targets[0].status == "FAILED"
    assert report.targets[0].metadata_restored_exactly


@pytest.mark.parametrize("after_seconds", [121, 301])
def test_forward_or_recovery_expiry_never_prevents_local_session_close(after_seconds):
    req = request(metadata=True)
    authority, dispatch = FakeAuthority(), FakeDispatch(req)

    def advance(command, facts):
        if command.stage == HealingStage.APPLY_TEMPORARY_METADATA:
            dispatch.clock = NOW + timedelta(seconds=after_seconds)
        return facts

    dispatch.transform = advance
    report = LiveHealingOrchestrator(
        authority,
        dispatch,
        execution_enabled=True,
        metadata_mutation_enabled=True,
        now=lambda: dispatch.clock,
    ).run(req)
    assert dispatch.commands[-1].stage == HealingStage.CLOSE_SESSION
    assert report.targets[0].session_closed
    assert report.targets[0].status != "OUTCOME_VERIFIED"
    if after_seconds > 300:
        assert report.targets[0].status == "RECONCILIATION_REQUIRED"
        assert not any(item.stage == HealingStage.RESTORE_METADATA for item in dispatch.commands)


def test_no_recovery_time_reservation_blocks_before_first_dispatch():
    class ShortLeaseAuthority(FakeAuthority):
        def claim(self, req, now):
            lease = super().claim(req, now)
            return lease.model_copy(
                update={"recovery_deadline": lease.expires_at + timedelta(seconds=1)}
            )

    report, dispatch, _ = run(authority=ShortLeaseAuthority())
    assert report.gap_codes == (HealingGap.AUTHORITY_REJECTED,)
    assert not dispatch.commands


@pytest.mark.parametrize("offset", ["+05:30", "-04:00", "-05:00"])
def test_equivalent_explicit_offsets_normalize_to_same_digest(offset):
    from datetime import timezone

    hours, minutes = (int(value) for value in offset[1:].split(":"))
    zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if offset[0] == "+" else -1)))
    lease = FakeAuthority().claim(request(), NOW)
    payload = lease.model_dump(mode="json")
    for field in ("issued_at", "expires_at", "recovery_deadline"):
        payload[field] = getattr(lease, field).astimezone(zone).isoformat()
    normalized = HealingLease.model_validate(payload)
    assert normalized == lease and digest(normalized) == digest(lease)


@pytest.mark.parametrize("timestamp", ["2026-09-11T00:00:00", "2026-09-11T00:00:00-00:00"])
def test_naive_or_unknown_offsets_are_not_authority(timestamp):
    payload = FakeAuthority().claim(request(), NOW).model_dump(mode="json")
    payload["issued_at"] = timestamp
    with pytest.raises(ValidationError):
        HealingLease.model_validate(payload)


def test_dst_fall_back_compares_instants_not_local_clock_order():
    payload = FakeAuthority().claim(request(), NOW).model_dump(mode="json")
    payload.update(
        {
            "issued_at": "2026-11-01T01:59:00-04:00",
            "expires_at": "2026-11-01T01:01:00-05:00",
            "recovery_deadline": "2026-11-01T01:04:00-05:00",
        }
    )
    normalized = HealingLease.model_validate(payload)
    assert normalized.expires_at - normalized.issued_at == timedelta(minutes=2)
    assert normalized.issued_at.tzinfo == UTC


def test_report_cannot_label_failed_cleanup_as_verified():
    from neo_sf_q_intel.live_healing import HealingTargetReport

    report, _, _ = run()
    payload = report.targets[0].model_dump(mode="json")
    payload["locator_state_reset_exactly"] = False
    with pytest.raises(ValidationError):
        HealingTargetReport.model_validate(payload)


@pytest.mark.parametrize(
    "options",
    [
        {"execution_enabled": "true"},
        {"metadata_mutation_enabled": 1},
        {"operation_timeout_seconds": True},
        {"operation_timeout_seconds": 0},
        {"operation_timeout_seconds": 121},
    ],
)
def test_host_flags_and_limits_are_not_coerced(options):
    with pytest.raises(ValueError):
        LiveHealingOrchestrator(FakeAuthority(), FakeDispatch(request()), **options)


def test_one_use_authority_replay_cannot_dispatch_again():
    req = request()
    authority, dispatch = FakeAuthority(), FakeDispatch(req)
    orchestrator = LiveHealingOrchestrator(
        authority, dispatch, execution_enabled=True, now=lambda: NOW
    )
    assert orchestrator.run(req).targets[0].status == "OUTCOME_VERIFIED"
    count = len(dispatch.commands)
    assert orchestrator.run(req).gap_codes == (HealingGap.AUTHORITY_REJECTED,)
    assert len(dispatch.commands) == count


def test_failed_target_keeps_remaining_target_visible_and_not_run():
    report, _, _ = run(
        request(target_count=2),
        transform=at(
            HealingStage.PROVE_STALE,
            lambda facts: facts.model_copy(update={"assertions": facts.assertions[:1]}),
        ),
    )
    assert [item.status for item in report.targets] == ["FAILED", "NOT_RUN"]
    assert len(report.targets[1].obligation_sha256s) == 2


def test_entity_rename_and_additional_targets_do_not_narrow_algorithm_or_leak_names():
    original, first, _ = run(request(prefix="alpha", target_count=2))
    renamed, second, _ = run(request(prefix="unrelated-new-domain", target_count=2))
    assert [item.stage for item in first.commands] == [item.stage for item in second.commands]
    assert all(item.status == "OUTCOME_VERIFIED" for item in renamed.targets)
    assert original.request_sha256 != renamed.request_sha256
    assert "unrelated-new-domain" not in renamed.model_dump_json()


def test_forged_request_headless_false_or_unknown_phase_rejected_before_authority():
    req = request().model_copy(update={"headless": False})
    authority, dispatch = FakeAuthority(), FakeDispatch(req)
    with pytest.raises(ValidationError):
        LiveHealingOrchestrator(authority, dispatch, execution_enabled=True).run(req)
    assert not authority.claims and not dispatch.commands
