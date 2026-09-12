"""The join between the worker's pushed evidence and the model's healing context.

This is the seam where digests from TypeScript meet digests from PostgreSQL. Everything the
model is allowed to see passes through here, so it is also the last place a raw org value could
be introduced by accident.
"""

from __future__ import annotations

import json

import pytest

from neo_sf_q_intel.context_feeds import FieldMetadata, PicklistValue
from neo_sf_q_intel.element_signature import build_signature
from neo_sf_q_intel.locator_proposal import LocatorProposalError, build_healing_context

RECORD_ID = "005fj00000N5t8IAAR"
PERSON_NAME = "Synthetic Regional VP"
RECORD_ID_DIGEST = "b167df2522f2e65b"
PERSON_NAME_DIGEST = "c5ecd76d4d625d98"


def _dom_evidence(count: int = 2) -> dict:
    return {
        "capturedForOutcome": "LOCATOR_NOT_FOUND",
        "candidateCount": count,
        "truncated": False,
        "candidates": [
            {
                "ordinal": index,
                "tag": "input",
                "role": "combobox",
                "structure": "lightning-input-field>input[role=combobox]",
                "attrNames": ["data-value", "title"],
                "attrHashes": {"data-value": RECORD_ID_DIGEST, "title": PERSON_NAME_DIGEST},
                "nameDigest": PERSON_NAME_DIGEST,
                "nearby": ["label"],
                "visible": True,
                "enabled": True,
            }
            for index in range(count)
        ],
    }


def _signature():
    return build_signature(
        project_id="bridge",
        page_key="strategic-deal-workbench",
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
        obligation_id="ob-vp",
        structure="lightning-input-field>input[role=combobox]",
        attributes={"data-value": RECORD_ID, "title": PERSON_NAME},
        nearby=["label"],
        snapshot_root="b" * 64,
        captured_at_utc="2026-09-12T18:20:00Z",
    )


def _metadata() -> FieldMetadata:
    return FieldMetadata(
        objectApiName="Opportunity",
        fieldApiName="Regional_VP_Approver__c",
        type="Lookup",
        label="Regional VP Approver",
        required=False,
        referenceTo=["User"],
        sourcePath="force-app/main/default/objects/Opportunity/fields/x.field-meta.xml",
    )


def _build(**overrides):
    payload = {
        "obligation_id": "ob-vp",
        "object_api_name": "Opportunity",
        "field_api_name": "Regional_VP_Approver__c",
        "dom_evidence": _dom_evidence(),
        "field_metadata": _metadata(),
        "signature": _signature(),
        "graph_edges": ["field:Opportunity.Regional_VP_Approver__c -> references -> object:User"],
        "intent_section": "The approver lookup must be editable before submission.",
    }
    payload.update(overrides)
    return build_healing_context(**payload)


def test_context_joins_worker_candidates_with_the_stored_signature() -> None:
    context = _build()

    assert len(context.candidates) == 2
    assert context.field_label == "Regional VP Approver"
    assert context.field_type == "Lookup"
    assert context.signature_structure == "lightning-input-field>input[role=combobox]"
    assert context.signature_attr_hashes["title"] == PERSON_NAME_DIGEST


def test_the_two_languages_agree_so_signature_and_candidate_digests_match() -> None:
    context = _build()

    candidate = context.candidates[0]
    assert candidate.attr_hashes["title"] == context.signature_attr_hashes["title"]
    assert candidate.attr_hashes["data-value"] == context.signature_attr_hashes["data-value"]


def test_no_raw_org_value_survives_the_join() -> None:
    serialized = json.dumps(_build().model_dump(by_alias=True, mode="json"))

    assert RECORD_ID not in serialized
    assert PERSON_NAME not in serialized
    assert RECORD_ID_DIGEST in serialized


def test_a_candidate_carrying_a_non_digest_attribute_value_is_refused() -> None:
    evidence = _dom_evidence()
    evidence["candidates"][0]["attrHashes"]["title"] = PERSON_NAME

    with pytest.raises(LocatorProposalError) as error:
        _build(dom_evidence=evidence)

    assert error.value.code == "DOM_EVIDENCE_INVALID"


def test_evidence_without_candidates_is_refused() -> None:
    with pytest.raises(LocatorProposalError) as error:
        _build(dom_evidence=_dom_evidence(0))

    assert error.value.code == "NO_CANDIDATES"


def test_malformed_evidence_is_refused_rather_than_partially_read() -> None:
    with pytest.raises(LocatorProposalError) as error:
        _build(dom_evidence={"candidates": "all of them"})

    assert error.value.code == "DOM_EVIDENCE_INVALID"


def test_absent_metadata_and_signature_still_produce_a_usable_context() -> None:
    context = _build(field_metadata=None, signature=None)

    assert context.field_label is None
    assert context.signature_structure is None
    assert len(context.candidates) == 2


def test_picklist_values_reach_the_model_as_the_field_type_detail() -> None:
    metadata = _metadata().model_copy(
        update={
            "field_type": "Picklist",
            "picklist_values": [PicklistValue(value="Flow", label="Flow", default=True)],
        }
    )

    context = _build(field_metadata=metadata)

    assert context.field_type == "Picklist"


def test_a_signature_for_a_different_field_is_refused() -> None:
    signature = _signature().model_copy(update={"field_api_name": "Amount"})

    with pytest.raises(LocatorProposalError) as error:
        _build(signature=signature)

    assert error.value.code == "SIGNATURE_IDENTITY_MISMATCH"


def test_metadata_for_a_different_field_is_refused() -> None:
    metadata = _metadata().model_copy(update={"field_api_name": "Amount"})

    with pytest.raises(LocatorProposalError) as error:
        _build(field_metadata=metadata)

    assert error.value.code == "METADATA_IDENTITY_MISMATCH"
