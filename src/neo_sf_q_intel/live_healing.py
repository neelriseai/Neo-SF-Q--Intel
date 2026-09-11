"""Fail-closed healing sequence; no Salesforce, browser, or authority implementation.

Host adapters must authenticate durable observations and the complete source obligation set.
This domain report is deliberately not an acceptance receipt or a release authorization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptScope
from neo_sf_q_intel.live_target_plan import BrowserTarget
from neo_sf_q_intel.ontology import contract_sha256
from neo_sf_q_intel.temporal import UtcModel, aware_utc

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def digest(value: BaseModel) -> str:
    return contract_sha256(value.model_dump(mode="json", warnings=False))


class HealingStage(StrEnum):
    CAPTURE_BASELINE = "CAPTURE_BASELINE"
    APPLY_TEMPORARY_METADATA = "APPLY_TEMPORARY_METADATA"
    PROVE_STALE = "PROVE_STALE"
    DISCOVER_CANDIDATES = "DISCOVER_CANDIDATES"
    REBIND_LOCATORS = "REBIND_LOCATORS"
    RERUN_ASSERTIONS = "RERUN_ASSERTIONS"
    RESTORE_METADATA = "RESTORE_METADATA"
    RESET_LOCATORS = "RESET_LOCATORS"
    VERIFY_RESTORATION = "VERIFY_RESTORATION"
    CLOSE_SESSION = "CLOSE_SESSION"


RECOVERY_STAGES = frozenset(
    {
        HealingStage.RESTORE_METADATA,
        HealingStage.RESET_LOCATORS,
        HealingStage.VERIFY_RESTORATION,
        HealingStage.CLOSE_SESSION,
    }
)


class HealingAssertion(_Model):
    obligation_sha256: Digest
    original_locator_sha256: Digest
    semantic_identity_sha256: Digest
    expected_value_sha256: Digest


class SourceHealingTarget(_Model):
    """Host compiler output, never inferred from the prose of a browser intent."""

    browser_intent: BrowserTarget
    planned_target_sha256: Digest
    source_assertion_contract_sha256: Digest
    assertions: tuple[HealingAssertion, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_assertions(self) -> SourceHealingTarget:
        ids = tuple(item.obligation_sha256 for item in self.assertions)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Healing obligations must be complete, sorted and unique")
        return self


class HealingRequest(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    scope: ReceiptScope
    evidence_phase: EvidencePhase
    target_plan_sha256: Digest
    expected_execution_contract_sha256: Digest
    classification_receipt_sha256: Digest
    session_enrollment_receipt_sha256: Digest
    expected_aut_prestate_sha256: Digest
    expected_residue_sha256: Digest
    temporary_metadata_plan_sha256: Digest | None = None
    temporary_metadata_expected_state_sha256: Digest | None = None
    targets: tuple[SourceHealingTarget, ...] = Field(min_length=1, max_length=100)
    headless: Literal[True] = True

    @model_validator(mode="after")
    def validate_scope(self) -> HealingRequest:
        roots = tuple(item.planned_target_sha256 for item in self.targets)
        if roots != tuple(sorted(set(roots))):
            raise ValueError("Healing targets must be complete, sorted and unique")
        if (self.temporary_metadata_plan_sha256 is None) != (
            self.temporary_metadata_expected_state_sha256 is None
        ):
            raise ValueError("A temporary metadata plan requires its exact expected state root")
        if self.evidence_phase not in {
            EvidencePhase.LIVE_BASELINE,
            EvidencePhase.DEPLOYED_CANDIDATE,
        }:
            raise ValueError("Unsupported healing evidence phase")
        if (
            self.temporary_metadata_plan_sha256 is not None
            and self.evidence_phase != EvidencePhase.DEPLOYED_CANDIDATE
        ):
            raise ValueError("Baseline evidence cannot authorize temporary metadata mutation")
        return self


class HealingLease(_Model):
    """Opaque proof roots resolved by a host authority, not caller authorization flags."""

    request_sha256: Digest
    authority_receipt_sha256: Digest
    restore_authority_receipt_sha256: Digest
    one_use_claim_sha256: Digest
    issued_at: datetime
    expires_at: datetime
    recovery_deadline: datetime

    @model_validator(mode="after")
    def bounded_lease(self) -> HealingLease:
        if not self.issued_at < self.expires_at < self.recovery_deadline:
            raise ValueError("Invalid healing lease interval")
        if self.recovery_deadline - self.issued_at > timedelta(minutes=30):
            raise ValueError("Healing lease exceeds bounded recovery interval")
        return self


class HealingCommand(_Model):
    request_sha256: Digest
    target_sha256: Digest
    stage: HealingStage
    previous_observation_sha256: Digest | None
    baseline_observation_sha256: Digest | None
    candidate_observation_sha256: Digest | None
    deadline: datetime
    maximum_output_bytes: StrictInt = Field(ge=1024, le=1_048_576)


class AssertionObservation(_Model):
    obligation_sha256: Digest
    locator_sha256: Digest
    outcome: Literal["PASSED", "LOCATOR_NOT_FOUND", "ASSERTION_MISMATCH", "NOT_RUN"]
    observed_value_sha256: Digest | None


class BaselineFacts(_Model):
    kind: Literal["BASELINE"] = "BASELINE"
    headless: Literal[True]
    aut_state_sha256: Digest
    residue_sha256: Digest
    locator_state_sha256: Digest
    assertions: tuple[AssertionObservation, ...] = Field(min_length=1, max_length=200)


class AssertionFacts(_Model):
    kind: Literal["ASSERTIONS"] = "ASSERTIONS"
    assertions: tuple[AssertionObservation, ...] = Field(min_length=1, max_length=200)


class CandidateObservation(_Model):
    obligation_sha256: Digest
    locator_sha256: Digest
    semantic_identity_sha256: Digest
    supporting_evidence_sha256: Digest
    visible: StrictBool
    enabled: StrictBool


class CandidateFacts(_Model):
    kind: Literal["CANDIDATES"] = "CANDIDATES"
    candidates: tuple[CandidateObservation, ...] = Field(max_length=400)


class StateFacts(_Model):
    kind: Literal["STATE"] = "STATE"
    before_sha256: Digest
    after_sha256: Digest
    pending_rebinds: StrictInt = Field(ge=0, le=200)


class RestorationFacts(_Model):
    kind: Literal["RESTORATION"] = "RESTORATION"
    aut_state_sha256: Digest
    residue_sha256: Digest
    locator_state_sha256: Digest
    pending_rebinds: StrictInt = Field(ge=0, le=200)
    assertions: tuple[AssertionObservation, ...] = Field(min_length=1, max_length=200)


class CloseFacts(_Model):
    kind: Literal["CLOSE"] = "CLOSE"
    context_closed: StrictBool
    browser_closed: StrictBool
    temporary_rebind_store_closed: StrictBool


Facts = Annotated[
    BaselineFacts | AssertionFacts | CandidateFacts | StateFacts | RestorationFacts | CloseFacts,
    Field(discriminator="kind"),
]


class HealingObservation(_Model):
    command_sha256: Digest
    durable_receipt_sha256: Digest
    observed_at: datetime
    facts: Facts


class HealingAuthorityPort(Protocol):
    """Host-only implementation: signed authority, durable one-use ledger and source replay.

    claim verifies the complete source target/assertion set, live classification, phase,
    deployment/check-only/restore prerequisites and independently enrolled actor/org.
    revalidate verifies the exact command and recovery authority immediately before dispatch.
    verify_observation authenticates durable receipt bytes, scope, producer and observation root.
    Expired/revoked effect authority does not invalidate fresh, signed local teardown evidence.
    Neither a source declaration nor an LLM proposal can implement this authority.
    """

    def claim(self, request: HealingRequest, now: datetime) -> HealingLease: ...

    def revalidate(
        self, request: HealingRequest, lease: HealingLease, command: HealingCommand, now: datetime
    ) -> None: ...

    def verify_observation(
        self, lease: HealingLease, command: HealingCommand, observation: HealingObservation
    ) -> None: ...


class HealingDispatchPort(Protocol):
    """Injected host adapter, not supplied by API/MCP requests.

    Must independently enforce the permit, headless session, hard deadline and output bound;
    only opaque roots cross this boundary. A partial/timeout operation must still be recoverable
    by request root even when no result returned. Raw locators/DOM/session material stay private.
    """

    def execute(self, command: HealingCommand, lease: HealingLease) -> HealingObservation: ...

    def close_local(self, command: HealingCommand) -> HealingObservation:
        """Unconditional local-only teardown, including after lease revocation/expiry.

        This path cannot navigate, call Salesforce or restore metadata. It closes the local
        context/browser and discards the ephemeral rebind store by the request/target roots.
        Observation signatures remain verifiable after authority expires; no new effect is
        authorized by observing cleanup. Implementations must have bounded emergency teardown.
        """
        ...


class HealingGap(StrEnum):
    EXECUTION_DISABLED = "EXECUTION_DISABLED"
    METADATA_MUTATION_DISABLED = "METADATA_MUTATION_DISABLED"
    AUTHORITY_REJECTED = "AUTHORITY_REJECTED"
    OBSERVATION_INVALID = "OBSERVATION_INVALID"
    BASELINE_NOT_PROVEN = "BASELINE_NOT_PROVEN"
    STALE_LOCATOR_NOT_PROVEN = "STALE_LOCATOR_NOT_PROVEN"
    CANDIDATE_NOT_UNIQUE = "CANDIDATE_NOT_UNIQUE"
    REBIND_NOT_PROVEN = "REBIND_NOT_PROVEN"
    ASSERTION_RERUN_FAILED = "ASSERTION_RERUN_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_TIMED_OUT = "OPERATION_TIMED_OUT"
    RESTORATION_UNRESOLVED = "RESTORATION_UNRESOLVED"
    SESSION_CLOSURE_UNRESOLVED = "SESSION_CLOSURE_UNRESOLVED"


class HealingFailure(RuntimeError):
    def __init__(self, code: HealingGap):
        self.code = code
        super().__init__(code.value)


class VerifiedHealingStage(_Model):
    stage: HealingStage
    command_sha256: Digest
    durable_receipt_sha256: Digest
    observation_sha256: Digest
    started_at: datetime
    terminal_at: datetime
    duration_ms: StrictInt = Field(ge=0)


class HealingTargetReport(_Model):
    target_sha256: Digest
    source_browser_intent_sha256: Digest
    obligation_sha256s: tuple[Digest, ...]
    stale_obligation_sha256s: tuple[Digest, ...]
    status: Literal["NOT_RUN", "FAILED", "OUTCOME_VERIFIED", "RECONCILIATION_REQUIRED"]
    observation_sha256s: tuple[Digest, ...]
    attempted_stages: tuple[HealingStage, ...]
    verified_stages: tuple[VerifiedHealingStage, ...]
    metadata_restored_exactly: StrictBool
    locator_state_reset_exactly: StrictBool
    session_closed: StrictBool
    gap_codes: tuple[HealingGap, ...]

    @model_validator(mode="after")
    def truthful_outcome(self) -> HealingTargetReport:
        if self.status == "OUTCOME_VERIFIED" and (
            self.gap_codes
            or not self.observation_sha256s
            or not self.stale_obligation_sha256s
            or not self.metadata_restored_exactly
            or not self.locator_state_reset_exactly
            or not self.session_closed
        ):
            raise ValueError("A verified outcome requires complete independent cleanup evidence")
        if not set(self.stale_obligation_sha256s).issubset(self.obligation_sha256s):
            raise ValueError("Stale obligations are outside the source obligation set")
        if (
            tuple(item.observation_sha256 for item in self.verified_stages)
            != self.observation_sha256s
        ):
            raise ValueError("Stage report and observation roots differ")
        return self


class LiveHealingReport(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    capability_id: Literal["automation.applied-healing"] = "automation.applied-healing"
    evidence_class: Literal["SEQUENCE_CONTRACT_REPORT_NOT_ACCEPTANCE_RECEIPT"] = (
        "SEQUENCE_CONTRACT_REPORT_NOT_ACCEPTANCE_RECEIPT"
    )
    acceptance_credit: Literal[False] = False
    release_eligible: Literal[False] = False
    request_sha256: Digest
    scope_sha256: Digest
    evidence_phase: EvidencePhase
    targets: tuple[HealingTargetReport, ...]
    gap_codes: tuple[HealingGap, ...]


class LiveHealingOrchestrator:
    def __init__(
        self,
        authority: HealingAuthorityPort,
        dispatch: HealingDispatchPort,
        *,
        execution_enabled: bool = False,
        metadata_mutation_enabled: bool = False,
        operation_timeout_seconds: int = 30,
        now=None,
    ):
        if type(execution_enabled) is not bool or type(metadata_mutation_enabled) is not bool:
            raise ValueError("Healing host enable flags must be explicit booleans")
        if type(operation_timeout_seconds) is not int or not 1 <= operation_timeout_seconds <= 120:
            raise ValueError("Healing operation timeout must be bounded")
        self.authority = authority
        self.dispatch = dispatch
        self.execution_enabled = execution_enabled
        self.metadata_mutation_enabled = metadata_mutation_enabled
        self.timeout = operation_timeout_seconds
        clock = now or (lambda: datetime.now(UTC))
        self.now = lambda: aware_utc(clock())

    def run(self, request: HealingRequest) -> LiveHealingReport:
        # Revalidation also rejects forged model_construct/model_copy payloads before effects.
        request = HealingRequest.model_validate_json(request.model_dump_json(warnings=False))
        gaps: list[HealingGap] = []
        if not self.execution_enabled:
            gaps.append(HealingGap.EXECUTION_DISABLED)
        elif request.temporary_metadata_plan_sha256 and not self.metadata_mutation_enabled:
            gaps.append(HealingGap.METADATA_MUTATION_DISABLED)
        lease = None
        if not gaps:
            try:
                lease = HealingLease.model_validate_json(
                    self.authority.claim(request, self.now()).model_dump_json(warnings=False)
                )
                if (
                    lease.request_sha256 != digest(request)
                    or not lease.issued_at <= self.now() < lease.expires_at
                    or lease.recovery_deadline > request.scope.recovery_deadline
                    or lease.recovery_deadline - lease.expires_at
                    < timedelta(seconds=3 * self.timeout)
                ):
                    raise HealingFailure(HealingGap.AUTHORITY_REJECTED)
            except Exception:
                gaps.append(HealingGap.AUTHORITY_REJECTED)
        reports = []
        for target in request.targets:
            if lease is None or gaps:
                reports.append(self._not_run(target, gaps))
                continue
            result = self._run_target(request, target, lease)
            reports.append(result)
            # All-or-block: never continue a campaign after uncertain state or a failed target.
            if result.status != "OUTCOME_VERIFIED":
                gaps.extend(result.gap_codes)
        return LiveHealingReport(
            request_sha256=digest(request),
            scope_sha256=digest(request.scope),
            evidence_phase=request.evidence_phase,
            targets=tuple(reports),
            gap_codes=tuple(sorted(set(gaps))),
        )

    @staticmethod
    def _not_run(target: SourceHealingTarget, gaps: list[HealingGap]) -> HealingTargetReport:
        return HealingTargetReport(
            target_sha256=target.planned_target_sha256,
            source_browser_intent_sha256=digest(target.browser_intent),
            obligation_sha256s=tuple(item.obligation_sha256 for item in target.assertions),
            stale_obligation_sha256s=(),
            status="NOT_RUN",
            observation_sha256s=(),
            attempted_stages=(),
            verified_stages=(),
            metadata_restored_exactly=False,
            locator_state_reset_exactly=False,
            session_closed=False,
            gap_codes=tuple(sorted(set(gaps))),
        )

    def _run_target(
        self, request: HealingRequest, target: SourceHealingTarget, lease: HealingLease
    ) -> HealingTargetReport:
        observations: list[HealingObservation] = []
        attempted_stages: list[HealingStage] = []
        verified_stages: list[VerifiedHealingStage] = []
        gaps: list[HealingGap] = []
        baseline = candidate = None
        baseline_facts = None
        stale: tuple[str, ...] = ()
        rebind_attempted = metadata_attempted = session_attempted = False
        outcome = aut_restored = locators_reset = closed = False

        def execute(stage: HealingStage) -> Facts:
            nonlocal session_attempted, metadata_attempted, rebind_attempted
            attempted_stages.append(stage)
            now = self.now()
            local_close = stage == HealingStage.CLOSE_SESSION
            limit = (
                now + timedelta(seconds=self.timeout)
                if local_close
                else lease.recovery_deadline
                if stage in RECOVERY_STAGES
                else lease.expires_at
            )
            if not local_close and not lease.issued_at <= now < limit:
                raise HealingFailure(HealingGap.AUTHORITY_REJECTED)
            command = HealingCommand(
                request_sha256=digest(request),
                target_sha256=target.planned_target_sha256,
                stage=stage,
                previous_observation_sha256=digest(observations[-1]) if observations else None,
                baseline_observation_sha256=digest(baseline) if baseline else None,
                candidate_observation_sha256=digest(candidate) if candidate else None,
                deadline=min(limit, now + timedelta(seconds=self.timeout)),
                maximum_output_bytes=1_048_576,
            )
            if not local_close:
                try:
                    self.authority.revalidate(request, lease, command, now)
                except Exception as error:
                    raise HealingFailure(HealingGap.AUTHORITY_REJECTED) from error
                if self.now() >= command.deadline:
                    raise HealingFailure(HealingGap.AUTHORITY_REJECTED)
                # Set only after authorization, before dispatch: partial effects require recovery.
                session_attempted = True
                metadata_attempted |= stage == HealingStage.APPLY_TEMPORARY_METADATA
                rebind_attempted |= stage == HealingStage.REBIND_LOCATORS
                raw = self.dispatch.execute(command, lease)
            else:
                raw = self.dispatch.close_local(command)
            encoded = raw.model_dump_json(warnings=False)
            if len(encoded.encode()) > command.maximum_output_bytes:
                raise HealingFailure(HealingGap.OBSERVATION_INVALID)
            observation = HealingObservation.model_validate_json(encoded)
            if (
                observation.command_sha256 != digest(command)
                or observation.observed_at.tzinfo is None
                or not now <= observation.observed_at <= self.now() < command.deadline
            ):
                raise HealingFailure(HealingGap.OBSERVATION_INVALID)
            try:
                self.authority.verify_observation(lease, command, observation)
            except Exception as error:
                raise HealingFailure(HealingGap.OBSERVATION_INVALID) from error
            observations.append(observation)
            verified_stages.append(
                VerifiedHealingStage(
                    stage=stage,
                    command_sha256=digest(command),
                    durable_receipt_sha256=observation.durable_receipt_sha256,
                    observation_sha256=digest(observation),
                    started_at=now,
                    terminal_at=observation.observed_at,
                    duration_ms=int((observation.observed_at - now).total_seconds() * 1000),
                )
            )
            return observation.facts

        def check_assertions(
            values: tuple[AssertionObservation, ...], selected: dict[str, str] | None = None
        ) -> bool:
            expected = {item.obligation_sha256: item for item in target.assertions}
            if tuple(item.obligation_sha256 for item in values) != tuple(expected):
                return False
            return all(
                item.outcome == "PASSED"
                and item.observed_value_sha256
                == expected[item.obligation_sha256].expected_value_sha256
                and item.locator_sha256
                == (selected or {}).get(
                    item.obligation_sha256, expected[item.obligation_sha256].original_locator_sha256
                )
                for item in values
            )

        try:
            facts = execute(HealingStage.CAPTURE_BASELINE)
            if (
                not isinstance(facts, BaselineFacts)
                or not check_assertions(facts.assertions)
                or facts.aut_state_sha256 != request.expected_aut_prestate_sha256
                or facts.residue_sha256 != request.expected_residue_sha256
            ):
                raise HealingFailure(HealingGap.BASELINE_NOT_PROVEN)
            baseline_facts, baseline = facts, observations[-1]
            if request.temporary_metadata_plan_sha256:
                facts = execute(HealingStage.APPLY_TEMPORARY_METADATA)
                if (
                    not isinstance(facts, StateFacts)
                    or facts.before_sha256 != baseline_facts.aut_state_sha256
                    or facts.before_sha256 == facts.after_sha256
                    or facts.after_sha256 != request.temporary_metadata_expected_state_sha256
                    or facts.pending_rebinds != 0
                ):
                    raise HealingFailure(HealingGap.OBSERVATION_INVALID)
            facts = execute(HealingStage.PROVE_STALE)
            if not isinstance(facts, AssertionFacts):
                raise HealingFailure(HealingGap.STALE_LOCATOR_NOT_PROVEN)
            expected = {item.obligation_sha256: item for item in target.assertions}
            if tuple(item.obligation_sha256 for item in facts.assertions) != tuple(expected):
                raise HealingFailure(HealingGap.STALE_LOCATOR_NOT_PROVEN)
            for item in facts.assertions:
                spec = expected[item.obligation_sha256]
                if (
                    item.locator_sha256 != spec.original_locator_sha256
                    or (
                        item.outcome == "PASSED"
                        and item.observed_value_sha256 != spec.expected_value_sha256
                    )
                    or (
                        item.outcome == "LOCATOR_NOT_FOUND"
                        and item.observed_value_sha256 is not None
                    )
                    or item.outcome not in {"PASSED", "LOCATOR_NOT_FOUND"}
                ):
                    raise HealingFailure(HealingGap.STALE_LOCATOR_NOT_PROVEN)
            stale = tuple(
                item.obligation_sha256
                for item in facts.assertions
                if item.outcome == "LOCATOR_NOT_FOUND"
            )
            if not stale:
                raise HealingFailure(HealingGap.STALE_LOCATOR_NOT_PROVEN)
            facts = execute(HealingStage.DISCOVER_CANDIDATES)
            if (
                not isinstance(facts, CandidateFacts)
                or tuple(item.obligation_sha256 for item in facts.candidates) != stale
            ):
                raise HealingFailure(HealingGap.CANDIDATE_NOT_UNIQUE)
            if any(
                not item.visible
                or not item.enabled
                or item.semantic_identity_sha256
                != expected[item.obligation_sha256].semantic_identity_sha256
                or item.locator_sha256 == expected[item.obligation_sha256].original_locator_sha256
                for item in facts.candidates
            ):
                raise HealingFailure(HealingGap.CANDIDATE_NOT_UNIQUE)
            selected = {item.obligation_sha256: item.locator_sha256 for item in facts.candidates}
            candidate = observations[-1]
            facts = execute(HealingStage.REBIND_LOCATORS)
            if (
                not isinstance(facts, StateFacts)
                or facts.before_sha256 != baseline_facts.locator_state_sha256
                or facts.after_sha256 == facts.before_sha256
                or facts.pending_rebinds != len(stale)
            ):
                raise HealingFailure(HealingGap.REBIND_NOT_PROVEN)
            facts = execute(HealingStage.RERUN_ASSERTIONS)
            if not isinstance(facts, AssertionFacts) or not check_assertions(
                facts.assertions, selected
            ):
                raise HealingFailure(HealingGap.ASSERTION_RERUN_FAILED)
            outcome = True
        except HealingFailure as error:
            gaps.append(error.code)
        except TimeoutError:
            gaps.append(HealingGap.OPERATION_TIMED_OUT)
        except Exception:
            gaps.append(HealingGap.OPERATION_FAILED)
        finally:
            # Recovery branches are independent: one failure cannot skip another cleanup.
            if metadata_attempted:
                try:
                    facts = execute(HealingStage.RESTORE_METADATA)
                    if not isinstance(facts, StateFacts) or (
                        facts.after_sha256 != request.expected_aut_prestate_sha256
                    ):
                        raise HealingFailure(HealingGap.RESTORATION_UNRESOLVED)
                except Exception:
                    gaps.append(HealingGap.RESTORATION_UNRESOLVED)
            if rebind_attempted:
                try:
                    facts = execute(HealingStage.RESET_LOCATORS)
                    if not isinstance(facts, StateFacts) or (
                        facts.after_sha256 != baseline_facts.locator_state_sha256
                        or facts.pending_rebinds != 0
                    ):
                        raise HealingFailure(HealingGap.RESTORATION_UNRESOLVED)
                except Exception:
                    gaps.append(HealingGap.RESTORATION_UNRESOLVED)
            if baseline_facts is not None:
                try:
                    facts = execute(HealingStage.VERIFY_RESTORATION)
                    if isinstance(facts, RestorationFacts):
                        aut_restored = (
                            facts.aut_state_sha256 == baseline_facts.aut_state_sha256
                            and facts.residue_sha256 == baseline_facts.residue_sha256
                        )
                        locators_reset = (
                            facts.locator_state_sha256 == baseline_facts.locator_state_sha256
                            and facts.pending_rebinds == 0
                            and check_assertions(facts.assertions)
                        )
                    if not aut_restored or not locators_reset:
                        raise HealingFailure(HealingGap.RESTORATION_UNRESOLVED)
                except Exception:
                    gaps.append(HealingGap.RESTORATION_UNRESOLVED)
            if session_attempted:
                try:
                    facts = execute(HealingStage.CLOSE_SESSION)
                    closed = isinstance(facts, CloseFacts) and (
                        facts.context_closed
                        and facts.browser_closed
                        and facts.temporary_rebind_store_closed
                    )
                    if not closed:
                        raise HealingFailure(HealingGap.SESSION_CLOSURE_UNRESOLVED)
                except Exception:
                    gaps.append(HealingGap.SESSION_CLOSURE_UNRESOLVED)
        unresolved = any(
            item in gaps
            for item in {HealingGap.RESTORATION_UNRESOLVED, HealingGap.SESSION_CLOSURE_UNRESOLVED}
        )
        return HealingTargetReport(
            target_sha256=target.planned_target_sha256,
            source_browser_intent_sha256=digest(target.browser_intent),
            obligation_sha256s=tuple(item.obligation_sha256 for item in target.assertions),
            stale_obligation_sha256s=stale,
            status=(
                "RECONCILIATION_REQUIRED"
                if unresolved
                else "OUTCOME_VERIFIED"
                if outcome and not gaps
                else "FAILED"
            ),
            observation_sha256s=tuple(digest(item) for item in observations),
            attempted_stages=tuple(attempted_stages),
            verified_stages=tuple(verified_stages),
            metadata_restored_exactly=aut_restored,
            locator_state_reset_exactly=locators_reset,
            session_closed=closed,
            gap_codes=tuple(sorted(set(gaps))),
        )
