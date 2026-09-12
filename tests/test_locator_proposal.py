"""D4: the model proposes a locator, deterministic code decides.

The proposal is an ordinal into the candidate list the worker already found, never a selector
string. A model that cannot express a selector cannot inject one, so the blast radius of a bad
or hostile completion is bounded to picking the wrong element out of a set we assembled.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from neo_sf_q_intel.locator_proposal import (
    DomCandidateView,
    LocatorHealingContext,
    LocatorProposalError,
    build_locator_prompt,
    parse_locator_proposal,
    propose_locator,
)
from neo_sf_q_intel.specialist import (
    ProviderCallOutcome,
    ProviderCallStatus,
    ProviderErrorCode,
    ProviderFinishReason,
    ProviderProfile,
)

RECORD_ID = "005fj00000N5t8IAAR"
PERSON_NAME = "Synthetic Regional VP"


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _profile() -> ProviderProfile:
    body = {
        "provider_kind": "openai",
        "model_id": "gpt-x",
        "deployment_id": "deployment",
        "model_version": "2026-01-01",
        "api_version": "v1",
        "response_format": "STRICT_JSON_SCHEMA",
        "temperature_milli": 0,
        "top_p_milli": 1000,
        "reasoning_profile": "deterministic",
        "tools_enabled": False,
    }
    return ProviderProfile(**body, profile_sha256=_digest(body))


def _candidate(ordinal: int, *, tag: str = "input") -> DomCandidateView:
    return DomCandidateView(
        ordinal=ordinal,
        tag=tag,
        role="combobox",
        structure="lightning-input-field>input[role=combobox]",
        attrNames=["data-value", "role", "title"],
        attrHashes={"data-value": _short(RECORD_ID), "title": _short(PERSON_NAME)},
        nameDigest=_short(PERSON_NAME),
        nearby=["label", "lightning-icon"],
        visible=True,
        enabled=True,
    )


def _context(**overrides) -> LocatorHealingContext:
    payload = {
        "obligationId": "ob-vp-approver",
        "objectApiName": "Opportunity",
        "fieldApiName": "Regional_VP_Approver__c",
        "fieldLabel": "Regional VP Approver",
        "fieldType": "Lookup",
        "graphEdges": ["field:Opportunity.Regional_VP_Approver__c -> references -> object:User"],
        "intentSection": "The approver lookup must be editable before submission.",
        "signatureStructure": "lightning-input-field>input[role=combobox]",
        "signatureAttrNames": ["data-value", "title"],
        "signatureAttrHashes": {"title": _short(PERSON_NAME)},
        "candidates": [_candidate(0), _candidate(1, tag="button")],
    }
    payload.update(overrides)
    return LocatorHealingContext(**payload)


def _response(**overrides) -> str:
    body = {
        "candidateOrdinal": 0,
        "confidenceMilli": 900,
        "rationale": "Structure and attribute names match the stored signature.",
        "citedRefs": ["cand:0", "meta:Opportunity.Regional_VP_Approver__c"],
    }
    body.update(overrides)
    return json.dumps(body)


def _outcome(raw: str | None, status: ProviderCallStatus = ProviderCallStatus.SUCCESS):
    common = {
        "status": status,
        "provider_profile_sha256": _profile().profile_sha256,
        "invoked_at": "2026-09-12T10:00:00.000000Z",
        "completed_at": "2026-09-12T10:00:01.000000Z",
        "duration_milliseconds": 900,
    }
    if status is ProviderCallStatus.SUCCESS:
        return ProviderCallOutcome(
            **common,
            raw_response=raw,
            finish_reason=ProviderFinishReason.STOP,
            input_tokens=400,
            output_tokens=60,
        )
    return ProviderCallOutcome(**common, error_code=ProviderErrorCode.PROVIDER_OUTAGE)


class _Provider:
    def __init__(self, outcome: ProviderCallOutcome) -> None:
        self._outcome = outcome
        self.prompts: list[str] = []

    @property
    def profile(self) -> ProviderProfile:
        return _profile()

    def __call__(
        self, prompt: str, *, timeout_milliseconds: int, maximum_output_tokens: int
    ) -> ProviderCallOutcome:
        self.prompts.append(prompt)
        return self._outcome


# --- prompt construction -------------------------------------------------------------------


def test_prompt_envelope_carries_no_record_value_or_person_name() -> None:
    envelope = build_locator_prompt(_context())

    serialized = json.dumps(envelope.model_dump(mode="json"))
    assert RECORD_ID not in serialized
    assert PERSON_NAME not in serialized
    assert _short(RECORD_ID) in serialized


def test_prompt_envelope_digest_is_deterministic_for_an_identical_context() -> None:
    first = build_locator_prompt(_context())
    second = build_locator_prompt(_context())

    assert first.prompt_sha256 == second.prompt_sha256


def test_prompt_envelope_changes_when_the_candidate_set_changes() -> None:
    first = build_locator_prompt(_context())
    second = build_locator_prompt(_context(candidates=[_candidate(0)]))

    assert first.prompt_sha256 != second.prompt_sha256


def test_context_without_candidates_is_refused_before_any_provider_call() -> None:
    with pytest.raises(LocatorProposalError) as error:
        build_locator_prompt(_context(candidates=[]))

    assert error.value.code == "NO_CANDIDATES"


def test_context_carrying_a_machine_path_is_refused() -> None:
    with pytest.raises(LocatorProposalError) as error:
        build_locator_prompt(_context(intentSection="See C:\\Users\\someone\\secrets.txt"))

    assert error.value.code == "CONTEXT_TEXT_UNSAFE"


def test_candidate_count_beyond_the_bound_is_refused() -> None:
    with pytest.raises(LocatorProposalError) as error:
        build_locator_prompt(_context(candidates=[_candidate(index) for index in range(41)]))

    assert error.value.code == "CANDIDATE_BOUND_EXCEEDED"


# --- proposal parsing ----------------------------------------------------------------------


def test_well_formed_proposal_is_accepted() -> None:
    proposal = parse_locator_proposal(_response(), _context())

    assert proposal.candidate_ordinal == 0
    assert proposal.confidence_milli == 900
    assert proposal.cited_refs == ["cand:0", "meta:Opportunity.Regional_VP_Approver__c"]


def test_non_json_response_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal("I think it is the second box.", _context())

    assert error.value.code == "PROPOSAL_NOT_JSON"


def test_ordinal_outside_the_pushed_candidate_set_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(_response(candidateOrdinal=7), _context())

    assert error.value.code == "PROPOSAL_ORDINAL_UNKNOWN"


def test_citation_that_was_never_supplied_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(
            _response(citedRefs=["cand:0", "meta:Account.Invented__c"]), _context()
        )

    assert error.value.code == "PROPOSAL_CITATION_UNKNOWN"


def test_proposal_without_any_citation_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(_response(citedRefs=[]), _context())

    assert error.value.code == "PROPOSAL_SCHEMA_INVALID"


def test_unexpected_response_key_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(_response(selector="input.deal-amount"), _context())

    assert error.value.code == "PROPOSAL_SCHEMA_INVALID"


def test_rationale_containing_a_machine_path_is_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(
            _response(rationale="Matched against C:\\Users\\someone\\dom.html"), _context()
        )

    assert error.value.code == "PROPOSAL_TEXT_UNSAFE"


def test_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(LocatorProposalError) as error:
        parse_locator_proposal(
            '{"candidateOrdinal":0,"candidateOrdinal":1,"confidenceMilli":900,'
            '"rationale":"x","citedRefs":["cand:0"]}',
            _context(),
        )

    assert error.value.code == "PROPOSAL_SCHEMA_INVALID"


# --- end to end ----------------------------------------------------------------------------


def test_accepted_proposal_is_recorded_with_a_success_receipt() -> None:
    provider = _Provider(_outcome(_response()))

    record = propose_locator(provider, _context())

    assert record.accepted is True
    assert record.rejection_code is None
    assert record.proposal is not None
    assert record.proposal.candidate_ordinal == 0
    assert record.receipt.status is ProviderCallStatus.SUCCESS
    assert record.receipt.response_sha256 is not None


def test_rejected_proposal_is_recorded_rather_than_raised() -> None:
    provider = _Provider(_outcome(_response(candidateOrdinal=99)))

    record = propose_locator(provider, _context())

    assert record.accepted is False
    assert record.rejection_code == "PROPOSAL_ORDINAL_UNKNOWN"
    assert record.proposal is None
    assert record.receipt.status is ProviderCallStatus.INVALID_RESPONSE
    assert record.receipt.response_sha256 is not None


def test_confidence_below_the_floor_abstains() -> None:
    provider = _Provider(_outcome(_response(confidenceMilli=100)))

    record = propose_locator(provider, _context(), minimum_confidence_milli=600)

    assert record.accepted is False
    assert record.rejection_code == "PROPOSAL_CONFIDENCE_BELOW_FLOOR"


def test_provider_outage_abstains_without_a_response_digest() -> None:
    provider = _Provider(_outcome(None, ProviderCallStatus.OUTAGE))

    record = propose_locator(provider, _context())

    assert record.accepted is False
    assert record.rejection_code == "PROVIDER_CALL_FAILED"
    assert record.receipt.status is ProviderCallStatus.OUTAGE
    assert record.receipt.response_sha256 is None


def test_the_prompt_handed_to_the_provider_contains_the_candidate_ordinals() -> None:
    provider = _Provider(_outcome(_response()))

    propose_locator(provider, _context())

    assert provider.prompts
    assert '"ordinal":0' in provider.prompts[0].replace(" ", "")


def test_record_serializes_without_any_raw_org_value() -> None:
    provider = _Provider(_outcome(_response()))

    record = propose_locator(provider, _context())

    serialized = json.dumps(record.model_dump(mode="json"))
    assert RECORD_ID not in serialized
    assert PERSON_NAME not in serialized
