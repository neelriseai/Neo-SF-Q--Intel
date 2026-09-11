"""Read-only, sanitized replay of durable live Salesforce campaign receipts.

This application service composes the append-only ledger with the current pinned
acceptance validator.  It never accepts receipt bytes, issuer material, scope roots,
or release decisions from an API caller and it exposes no receipt payloads.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.live_receipt_ledger import (
    LiveReceiptLedgerSelection,
    open_live_receipt_ledger,
)
from neo_sf_q_intel.live_receipts import (
    CampaignValidationResult,
    GateReceiptPayload,
    LiveReceiptContractError,
    PinnedLiveAcceptanceProfile,
    ReceiptScope,
    SignedLiveReceipt,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    parse_pinned_profile,
    validate_live_campaign,
)


class LiveCampaignStatusError(RuntimeError):
    """Stable application-boundary refusal without persistence or receipt detail."""

    code = "LIVE_CAMPAIGN_STATUS_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__(self.code)


class CampaignReplayState(StrEnum):
    EMPTY = "EMPTY"
    VALIDATED_INCOMPLETE = "VALIDATED_INCOMPLETE"
    QUARANTINED_INCOMPLETE = "QUARANTINED_INCOMPLETE"


class GateReplayState(StrEnum):
    LOCALLY_VALID = "LOCALLY_VALID"
    NOT_CURRENT = "NOT_CURRENT"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LiveGateReplayStatus(_Model):
    gate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    kind: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    receipt_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    accepted_evidence_phases: tuple[str, ...] = Field(min_length=1, max_length=4)
    required_for_completion: bool
    state: GateReplayState


class LiveCampaignStatus(_Model):
    """Sanitized status projection; deliberately incapable of a release claim."""

    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    replay_state: CampaignReplayState
    validator_replayed: bool
    evidence_state: Literal["INCOMPLETE"] = "INCOMPLETE"
    requirements_satisfied: Literal[False] = False
    release_eligible: Literal[False] = False
    accepted_completion_numerator: Literal[0] = 0
    completion_denominator: Literal[15] = 15
    locally_valid_gate_count: int = Field(ge=0, le=15)
    not_current_gate_count: int = Field(ge=0, le=15)
    receipt_count: int = Field(ge=0, le=256)
    acceptance_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ledger_mode: Literal["POSTGRESQL", "SQLITE"]
    ledger_degradation_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    required_gate_ids: tuple[str, ...] = Field(min_length=15, max_length=15)
    locally_valid_gate_ids: tuple[str, ...] = Field(max_length=15)
    missing_required_gate_ids: tuple[str, ...] = Field(max_length=15)
    live_baseline_gate_ids: tuple[str, ...] = Field(max_length=15)
    candidate_gate_ids: tuple[str, ...] = Field(max_length=6)
    gates: tuple[LiveGateReplayStatus, ...] = Field(min_length=15, max_length=15)
    quarantined: bool
    stored_quarantine_reasons: tuple[str, ...] = Field(max_length=256)
    gap_codes: tuple[str, ...] = Field(max_length=512)
    evaluation_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class LiveCampaignStatusReader:
    """Current validator replay over exact durable receipt bytes."""

    def __init__(
        self,
        *,
        selection: LiveReceiptLedgerSelection,
        pinned_profile: PinnedLiveAcceptanceProfile,
        issuer_registry: TrustedIssuerRegistry,
    ) -> None:
        self._selection = selection
        self._pinned_profile = pinned_profile
        self._issuer_registry = issuer_registry

    @property
    def ledger_mode(self) -> str:
        return self._selection.mode

    @property
    def ledger_degradation_code(self) -> str | None:
        return self._selection.degradation_code

    def get(self, campaign_id: str) -> LiveCampaignStatus:
        try:
            records = self._selection.ledger.replay(campaign_id=campaign_id)
            quarantine = self._selection.ledger.quarantine_state(campaign_id=campaign_id)
            if not records:
                return self._project_empty(
                    campaign_id,
                    quarantine.quarantined,
                    (item.reason.value for item in quarantine.records),
                )

            receipts = tuple(record.parsed_receipt() for record in records)
            expected_scope = self._expected_scope_from_host_enrollment(receipts)
            if expected_scope.campaign_id != campaign_id:
                raise LiveCampaignStatusError
            validation = validate_live_campaign(
                pinned_profile=self._pinned_profile,
                expected_scope=expected_scope,
                receipts=receipts,
                issuer_registry=self._issuer_registry,
            )
            return self._project_validation(
                campaign_id=campaign_id,
                receipt_count=len(records),
                validation=validation,
                stored_quarantine=quarantine.quarantined,
                quarantine_reasons=(item.reason.value for item in quarantine.records),
            )
        except LiveCampaignStatusError:
            raise
        except Exception:
            raise LiveCampaignStatusError from None

    def _expected_scope_from_host_enrollment(
        self, receipts: tuple[SignedLiveReceipt, ...]
    ) -> ReceiptScope:
        """Use the one trusted host enrollment as the replay root, never record order."""

        definition = next(
            (
                item
                for item in self._pinned_profile.profile.gates
                if item.kind == "HOST_NONPRODUCTION_ENROLLMENT"
            ),
            None,
        )
        if definition is None:
            raise LiveCampaignStatusError
        candidates = tuple(
            receipt
            for receipt in receipts
            if isinstance(receipt.payload, GateReceiptPayload)
            and receipt.payload.gate_id == definition.gateId
            and receipt.payload.receipt_type == definition.receiptType
        )
        if len(candidates) != 1 or self._issuer_registry.verify(candidates[0]) is not None:
            raise LiveCampaignStatusError
        return candidates[0].payload.scope

    def _project_empty(
        self,
        campaign_id: str,
        stored_quarantine: bool,
        quarantine_reasons: Iterable[str],
    ) -> LiveCampaignStatus:
        gap_codes = ["CAMPAIGN_RECEIPTS_NOT_FOUND"]
        if stored_quarantine:
            gap_codes.append("CAMPAIGN_QUARANTINED")
        return LiveCampaignStatus(
            campaign_id=campaign_id,
            replay_state=(
                CampaignReplayState.QUARANTINED_INCOMPLETE
                if stored_quarantine
                else CampaignReplayState.EMPTY
            ),
            validator_replayed=False,
            receipt_count=0,
            acceptance_profile_sha256=self._pinned_profile.profile_sha256,
            ledger_mode=self._selection.mode,
            ledger_degradation_code=self._selection.degradation_code,
            locally_valid_gate_count=0,
            not_current_gate_count=len(self._pinned_profile.profile.requiredGateIds),
            required_gate_ids=tuple(self._pinned_profile.profile.requiredGateIds),
            locally_valid_gate_ids=(),
            missing_required_gate_ids=tuple(self._pinned_profile.profile.requiredGateIds),
            live_baseline_gate_ids=self._phase_gate_ids("LIVE_BASELINE"),
            candidate_gate_ids=self._candidate_gate_ids(),
            gates=self._gate_statuses(()),
            quarantined=stored_quarantine,
            stored_quarantine_reasons=tuple(dict.fromkeys(quarantine_reasons)),
            gap_codes=tuple(gap_codes),
        )

    def _project_validation(
        self,
        *,
        campaign_id: str,
        receipt_count: int,
        validation: CampaignValidationResult,
        stored_quarantine: bool,
        quarantine_reasons: Iterable[str],
    ) -> LiveCampaignStatus:
        reasons = tuple(dict.fromkeys(quarantine_reasons))
        quarantined = stored_quarantine or validation.quarantined
        gap_codes = [item.code.value for item in validation.gaps]
        if stored_quarantine:
            gap_codes.append("CAMPAIGN_QUARANTINED")
        locally_valid = tuple(validation.locally_valid_gate_ids)
        required = tuple(self._pinned_profile.profile.requiredGateIds)
        missing = tuple(gate_id for gate_id in required if gate_id not in set(locally_valid))
        return LiveCampaignStatus(
            campaign_id=campaign_id,
            replay_state=(
                CampaignReplayState.QUARANTINED_INCOMPLETE
                if quarantined
                else CampaignReplayState.VALIDATED_INCOMPLETE
            ),
            validator_replayed=True,
            receipt_count=receipt_count,
            acceptance_profile_sha256=self._pinned_profile.profile_sha256,
            ledger_mode=self._selection.mode,
            ledger_degradation_code=self._selection.degradation_code,
            locally_valid_gate_count=len(locally_valid),
            not_current_gate_count=len(missing),
            required_gate_ids=required,
            locally_valid_gate_ids=locally_valid,
            missing_required_gate_ids=missing,
            live_baseline_gate_ids=self._phase_gate_ids("LIVE_BASELINE"),
            candidate_gate_ids=self._candidate_gate_ids(),
            gates=self._gate_statuses(locally_valid),
            quarantined=quarantined,
            stored_quarantine_reasons=reasons,
            gap_codes=tuple(dict.fromkeys(gap_codes)),
            evaluation_sha256=validation.evaluation_sha256,
        )

    def _gate_statuses(self, locally_valid: Iterable[str]) -> tuple[LiveGateReplayStatus, ...]:
        valid = frozenset(locally_valid)
        return tuple(
            LiveGateReplayStatus(
                gate_id=gate_id,
                kind=self._pinned_profile.profile.gates_by_id[gate_id].kind,
                receipt_type=self._pinned_profile.profile.gates_by_id[gate_id].receiptType,
                accepted_evidence_phases=tuple(
                    self._pinned_profile.profile.gates_by_id[gate_id].acceptedEvidencePhases
                ),
                required_for_completion=self._pinned_profile.profile.gates_by_id[
                    gate_id
                ].requiredForCompletion,
                state=(
                    GateReplayState.LOCALLY_VALID
                    if gate_id in valid
                    else GateReplayState.NOT_CURRENT
                ),
            )
            for gate_id in self._pinned_profile.profile.requiredGateIds
        )

    def _phase_gate_ids(self, phase: str) -> tuple[str, ...]:
        return tuple(
            gate_id
            for gate_id in self._pinned_profile.profile.requiredGateIds
            if phase in self._pinned_profile.profile.gates_by_id[gate_id].acceptedEvidencePhases
        )

    def _candidate_gate_ids(self) -> tuple[str, ...]:
        candidate_ids = {item.gateId for item in self._pinned_profile.profile.candidateGates}
        return tuple(
            gate_id
            for gate_id in self._pinned_profile.profile.requiredGateIds
            if gate_id in candidate_ids
        )


def create_live_campaign_status_reader(
    settings: Settings, repository_root: Path
) -> LiveCampaignStatusReader:
    """Compose the configured durable ledger, pinned profile, and trusted issuers."""

    pinned_profile = _load_pinned_profile(settings, repository_root)
    selection = open_live_receipt_ledger(
        database_url=(settings.database_url.get_secret_value() if settings.database_url else None),
        sqlite_path=settings.resolved_live_receipt_sqlite_path(repository_root),
        postgres_schema=settings.postgres_schema,
    )
    return LiveCampaignStatusReader(
        selection=selection,
        pinned_profile=pinned_profile,
        issuer_registry=_issuer_registry(settings),
    )


def _load_pinned_profile(settings: Settings, repository_root: Path) -> PinnedLiveAcceptanceProfile:
    try:
        document = json.loads(
            settings.resolved_live_acceptance_profile_path(repository_root).read_text(
                encoding="utf-8"
            ),
            object_pairs_hook=_reject_duplicate_profile_keys,
        )
        if not isinstance(document, dict):
            raise TypeError
        return parse_pinned_profile(
            document,
            expected_profile_sha256=settings.require_live_acceptance_profile_sha256(),
        )
    except (OSError, json.JSONDecodeError, TypeError, LiveReceiptContractError, ValueError):
        raise LiveCampaignStatusError from None


def _issuer_registry(settings: Settings) -> TrustedIssuerRegistry:
    issuers: list[TrustedIssuer] = []
    configurations = (
        (
            settings.live_product_receipt_issuer_id,
            settings.live_product_receipt_hmac_key,
            settings.live_product_receipt_roles,
            TrustedIssuerClass.PRODUCT_EXECUTION,
        ),
        (
            settings.live_host_receipt_issuer_id,
            settings.live_host_receipt_hmac_key,
            settings.live_host_receipt_roles,
            TrustedIssuerClass.HOST_AUTHORITY,
        ),
    )
    for issuer_id, secret, role_text, issuer_class in configurations:
        if issuer_id is None or secret is None or not role_text:
            continue
        roles = tuple(item.strip() for item in role_text.split(",") if item.strip())
        try:
            issuers.append(
                TrustedIssuer(
                    issuer_id=issuer_id,
                    issuer_class=issuer_class,
                    hmac_key=secret.get_secret_value().encode("utf-8"),
                    allowed_receipt_roles=frozenset(roles),
                )
            )
        except ValueError:
            raise LiveCampaignStatusError from None
    return TrustedIssuerRegistry(issuers)


def _reject_duplicate_profile_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous policy documents before digest or model validation."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("live acceptance profile contains a duplicate JSON key")
        result[key] = value
    return result


__all__ = [
    "CampaignReplayState",
    "GateReplayState",
    "LiveCampaignStatus",
    "LiveCampaignStatusError",
    "LiveCampaignStatusReader",
    "LiveGateReplayStatus",
    "create_live_campaign_status_reader",
]
