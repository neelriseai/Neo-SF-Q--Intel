from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_target_compiler import SourceOperationDeclarations
from neo_sf_q_intel.live_target_plan import BrowserTarget, TargetPartition, derive_target_sha256
from tests.test_candidate_target_compiler import _declarations, _rebind_declaration


def test_closed_locator_rebind_contract_roundtrips_complete_read_only_obligations() -> None:
    source = SourceOperationDeclarations.model_validate(_declarations())
    declaration = source.browser_intents[0]
    target = BrowserTarget(
        sourceEntityIds=("component:fixture",),
        **declaration.model_dump(by_alias=True, exclude={"source_node_id"}),
    )
    assert target.locator_rebind == declaration.locator_rebind
    assert target.locator_rebind.data_mutation == "FORBIDDEN"
    assert target.locator_rebind.metadata_drift.restoration == "PREIMAGE_EXACT"
    assert [value.obligation_id for value in target.locator_rebind.obligations] == [
        "field:Amount",
        "probe:inspect",
    ]


@pytest.mark.parametrize(
    "change",
    [
        "missing-contract",
        "wrong-action",
        "unknown-navigation",
        "unknown-dataset",
        "wrong-member",
        "wrong-object",
        "wrong-identity",
        "wrong-marker",
        "unknown-marker",
        "record-id",
        "url",
        "query",
        "alternate-list",
        "restore-default",
        "metadata-wildcard",
        "raw-selector",
        "selector-injection",
        "missing-field",
        "mixed-identity",
        "write-assertion",
        "missing-editable",
        "action-editable",
        "duplicate-id",
        "duplicate-original",
        "duplicate-semantic",
        "unsorted",
        "empty",
        "too-many",
        "allow-data-write",
    ],
)
def test_locator_rebind_contract_fails_closed(change: str) -> None:
    document = _declarations()
    browser = document["browserIntents"][0]
    rebind = browser["locatorRebind"]
    navigation = rebind["navigation"]
    drift = rebind["metadataDrift"]
    obligation = rebind["obligations"][0]
    if change == "missing-contract":
        del browser["locatorRebind"]
    elif change == "wrong-action":
        browser["actionClass"] = "NAVIGATE"
    elif change == "unknown-navigation":
        navigation["kind"] = "CALLER_URL"
    elif change == "unknown-dataset":
        navigation["datasetId"] = "other"
    elif change == "wrong-member":
        navigation["resolution"] = "FIRST_MATCH"
    elif change == "wrong-object":
        navigation["objectApiName"] = "Other__c"
    elif change == "wrong-identity":
        navigation["identityField"] = "Name"
    elif change == "wrong-marker":
        navigation["markerField"] = "OwnerId"
    elif change == "unknown-marker":
        navigation["markerValue"] = "outside-authorized-set"
    elif change in {"record-id", "url", "query"}:
        navigation[change] = "caller-controlled"
    elif change == "alternate-list":
        drift["alternateValue"] = ["alternate", "another"]
    elif change == "restore-default":
        drift["restoration"] = "DEFAULT_VALUE"
    elif change == "metadata-wildcard":
        drift["member"] = "*"
    elif change == "raw-selector":
        obligation["originalLocator"] = {"kind": "CSS", "value": "input"}
    elif change == "selector-injection":
        obligation["originalLocator"]["value"] = 'value"],input,[data-testid="'
    elif change == "missing-field":
        del obligation["semanticIdentity"]["fieldApiName"]
    elif change == "mixed-identity":
        obligation["semanticIdentity"]["action"] = "save"
    elif change == "write-assertion":
        obligation["assertions"] = ["FILL", "SAVE"]
    elif change == "missing-editable":
        obligation["assertions"] = ["ENABLED", "VISIBLE"]
    elif change == "action-editable":
        rebind["obligations"][1]["assertions"] = ["EDITABLE", "ENABLED", "VISIBLE"]
    elif change == "duplicate-id":
        rebind["obligations"][1]["obligationId"] = obligation["obligationId"]
    elif change == "duplicate-original":
        rebind["obligations"][1]["originalLocator"] = obligation["originalLocator"]
    elif change == "duplicate-semantic":
        duplicate = copy.deepcopy(obligation)
        duplicate["obligationId"] = "field:Other"
        duplicate["originalLocator"]["value"] = "other-original"
        rebind["obligations"].insert(1, duplicate)
    elif change == "unsorted":
        rebind["obligations"].reverse()
    elif change == "empty":
        rebind["obligations"] = []
    elif change == "too-many":
        rebind["obligations"] *= 33
    elif change == "allow-data-write":
        rebind["dataMutation"] = "ALLOW_SAVE"
    with pytest.raises(ValidationError):
        SourceOperationDeclarations.model_validate(document)


def test_complete_obligation_set_and_every_binding_change_target_identity() -> None:
    original = BrowserTarget(
        sourceEntityIds=("component:fixture",),
        applicationIntent="fixture",
        actionClass="LOCATOR_REBIND_PREVIEW",
        surfaceIntent="record",
        locatorRebind=_rebind_declaration(),
    )
    original_hash = derive_target_sha256(TargetPartition.BROWSER_INTENT, original)
    for change in ("obligations", "alternateValue", "componentIdentifier", "markerValue"):
        modified = original.model_dump(by_alias=True)
        rebind = modified["locatorRebind"]
        if change == "obligations":
            rebind[change] = rebind[change][:1]
        elif change == "markerValue":
            rebind["navigation"][change] = "another-exact-marker"
        else:
            rebind["metadataDrift"][change] = "another"
        target = BrowserTarget.model_validate(modified)
        assert derive_target_sha256(TargetPartition.BROWSER_INTENT, target) != original_hash


def test_generated_source_operation_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "config/source-profiles/source-operation-declarations.schema.json").read_bytes()
    )
    assert schema == SourceOperationDeclarations.model_json_schema()


@pytest.mark.parametrize(
    "change",
    [
        "missing-operation",
        "unsupported-operation",
        "missing-baseline",
        "missing-prestates",
        "empty-prestates",
        "duplicate-prestates",
        "unsorted-prestates",
        "unknown-prestate",
        "absent-with-value",
        "present-without-value",
        "unlisted-present-value",
        "alternate-is-baseline",
        "baseline-whitespace",
        "baseline-control",
        "baseline-overflow",
        "alternate-control",
        "present-control",
        "present-overflow",
        "caller-insertion-flag",
    ],
)
def test_scalar_set_prestates_are_explicit_closed_and_bounded(change: str) -> None:
    document = _declarations()
    drift = document["browserIntents"][0]["locatorRebind"]["metadataDrift"]
    if change == "missing-operation":
        del drift["operation"]
    elif change == "unsupported-operation":
        drift["operation"] = "INSERT"
    elif change == "missing-baseline":
        del drift["baselineValue"]
    elif change == "missing-prestates":
        del drift["allowedPreStates"]
    elif change == "empty-prestates":
        drift["allowedPreStates"] = []
    elif change == "duplicate-prestates":
        drift["allowedPreStates"] = [{"state": "ABSENT"}, {"state": "ABSENT"}]
    elif change == "unsorted-prestates":
        drift["allowedPreStates"].reverse()
    elif change == "unknown-prestate":
        drift["allowedPreStates"] = [{"state": "ANY"}]
    elif change == "absent-with-value":
        drift["allowedPreStates"][0]["value"] = "initial"
    elif change == "present-without-value":
        del drift["allowedPreStates"][1]["value"]
    elif change == "unlisted-present-value":
        drift["allowedPreStates"][1]["value"] = "unlisted"
    elif change == "alternate-is-baseline":
        drift["alternateValue"] = drift["baselineValue"]
    elif change.startswith("baseline-"):
        drift["baselineValue"] = {
            "baseline-whitespace": " initial",
            "baseline-control": "initial\n",
            "baseline-overflow": "x" * 101,
        }[change]
        drift["allowedPreStates"][1]["value"] = drift["baselineValue"]
    elif change == "alternate-control":
        drift["alternateValue"] = "alternate\n"
    elif change == "present-control":
        drift["allowedPreStates"][1]["value"] = "initial\n"
    elif change == "present-overflow":
        drift["allowedPreStates"][1]["value"] = "x" * 101
    elif change == "caller-insertion-flag":
        drift["allowInsert"] = True
    with pytest.raises(ValidationError):
        SourceOperationDeclarations.model_validate(document)


@pytest.mark.parametrize("state", ["ABSENT", "PRESENT"])
def test_narrower_source_prestates_remain_explicit_and_change_target_hash(state: str) -> None:
    original = BrowserTarget(
        sourceEntityIds=("component:fixture",),
        applicationIntent="fixture",
        actionClass="LOCATOR_REBIND_PREVIEW",
        surfaceIntent="record",
        locatorRebind=_rebind_declaration(),
    )
    document = original.model_dump(by_alias=True)
    drift = document["locatorRebind"]["metadataDrift"]
    drift["allowedPreStates"] = [
        value for value in drift["allowedPreStates"] if value["state"] == state
    ]
    narrowed = BrowserTarget.model_validate(document)
    assert tuple(
        value.state for value in narrowed.locator_rebind.metadata_drift.allowed_pre_states
    ) == (state,)
    assert derive_target_sha256(TargetPartition.BROWSER_INTENT, narrowed) != derive_target_sha256(
        TargetPartition.BROWSER_INTENT, original
    )


@pytest.mark.parametrize("field", ["baselineValue", "alternateValue", "presentValue"])
@pytest.mark.parametrize("value", [b"initial", 1, None])
def test_scalar_property_tokens_never_coerce_non_string_values(field: str, value) -> None:
    document = _declarations()
    drift = document["browserIntents"][0]["locatorRebind"]["metadataDrift"]
    if field == "presentValue":
        drift["allowedPreStates"][1]["value"] = value
    else:
        drift[field] = value
    with pytest.raises(ValidationError):
        SourceOperationDeclarations.model_validate(document)
