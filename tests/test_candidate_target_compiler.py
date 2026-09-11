from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.candidate_target_compiler as compiler_module
import neo_sf_q_intel.live_target_plan as live_target_module
from neo_sf_q_intel.candidate_target_compiler import (
    CandidateTargetCompilerError,
    CandidateTargetCompilerInputs,
    CapturedSourceArtifact,
    ExpectedRunnerProvenance,
    HostOrganizationClassification,
    HostOwnedSourceContractPort,
    ProductionCandidateTargetCompiler,
    TargetDerivationReason,
    TargetDerivationState,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.execution_assertions import ExecutionToolVersion
from neo_sf_q_intel.live_receipts import ReceiptScope
from neo_sf_q_intel.live_target_plan import (
    TargetPartition,
    derive_target_sha256,
    load_live_target_policy,
)
from neo_sf_q_intel.ontology import (
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
)
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import AssuranceService
from tests.test_foundation_pipeline import NS, ROOT, _git, _pipeline, _repository, _write
from tests.test_workflow import source


def _connected_repository(
    tmp_path: Path,
    *,
    component: str = "dealPanel",
    apex_class: str = "DealPolicy",
    unrelated: bool = False,
    declarations: dict | None = None,
    browser_component: str | None = None,
    include_jest: bool = False,
    method_parameter: str = "",
    extra_changes: tuple[tuple[str, bytes, bytes], ...] = (),
) -> Path:
    repository = _repository(tmp_path, dirty=False, entity_name="Opportunity")
    field = "workspace/dx/package/main/default/objects/Opportunity/fields/Amount.field-meta.xml"
    _write(
        repository,
        field,
        (
            f'<CustomField xmlns="{NS}"><fullName>Amount</fullName><label>Amount</label>'
            "<type>Currency</type></CustomField>"
        ).encode(),
    )
    classes = "workspace/dx/package/main/default/classes"
    _write(
        repository,
        f"{classes}/{apex_class}.cls",
        (
            f"public class {apex_class} {{ public static void run() {{ "
            "[SELECT Amount FROM Opportunity LIMIT 1]; } }"
        ).encode(),
    )
    _write(
        repository,
        f"{classes}/{apex_class}Test.cls",
        (
            f"@isTest public class {apex_class}Test {{ "
            f"@isTest static void verifies({method_parameter}) {{ "
            f"{apex_class}.run(); }} }}"
        ).encode(),
    )
    for name in (apex_class, f"{apex_class}Test"):
        _write(
            repository,
            f"{classes}/{name}.cls-meta.xml",
            (
                f'<ApexClass xmlns="{NS}"><apiVersion>67.0</apiVersion>'
                "<status>Active</status></ApexClass>"
            ).encode(),
        )
    for name in (component, *(("unrelatedWidget",) if unrelated else ())):
        bundle = f"workspace/dx/package/main/default/lwc/{name}"
        javascript = (
            "export default class Component {}"
            if name == "unrelatedWidget"
            else (
                f"import run from '@salesforce/apex/{apex_class}.run'; "
                "export default class Component {}"
            )
        )
        _write(
            repository,
            f"{bundle}/{name}.js",
            javascript.encode(),
        )
        _write(repository, f"{bundle}/{name}.html", b"<template></template>")
        _write(
            repository,
            f"{bundle}/{name}.js-meta.xml",
            (
                f'<LightningComponentBundle xmlns="{NS}">'
                "<targets><target>lightning__RecordPage</target></targets><targetConfigs>"
                '<targetConfig targets="lightning__RecordPage"><property name="presentation" '
                'type="String" default="initial" datasource="initial,alternate"/>'
                "</targetConfig></targetConfigs></LightningComponentBundle>"
            ).encode(),
        )
    _write(
        repository,
        "workspace/dx/package/main/default/flexipages/Fixture_Record.flexipage-meta.xml",
        (
            f'<FlexiPage xmlns="{NS}"><type>RecordPage</type><sobjectType>Opportunity</sobjectType>'
            "<flexiPageRegions><itemInstances><componentInstance>"
            f"<componentName>c:{component}</componentName><identifier>fixturePanel</identifier>"
            "</componentInstance></itemInstances></flexiPageRegions></FlexiPage>"
        ).encode(),
    )
    if include_jest:
        _write(
            repository,
            f"workspace/dx/package/main/default/lwc/{component}/__tests__/test.test.js",
            f"import c from 'c/{component}'; test('renders', () => {{}});".encode(),
        )
    contract = {
        "schemaVersion": "1.0.0",
        "application": "Fixture Project",
        "sourceOperations": {"schemaVersion": "1.0.0", "locator": "contracts/operations.json"},
    }
    _write(repository, "contracts/agent-interface.json", json.dumps(contract).encode())
    _write(
        repository,
        "contracts/operations.json",
        json.dumps(
            declarations
            if declarations is not None
            else _declarations(browser_component or component)
        ).encode(),
    )
    _git(repository, "add", ".")
    for path, before, _after in extra_changes:
        _write(repository, path, before)
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "connected source graph")
    _write(
        repository,
        f"{classes}/{apex_class}Test.cls",
        (
            f"@isTest public class {apex_class}Test {{ "
            f"@isTest static void verifies({method_parameter}) {{ "
            f"{apex_class}.run(); System.assert(true); }} }}"
        ).encode(),
    )
    if include_jest:
        _write(
            repository,
            f"workspace/dx/package/main/default/lwc/{component}/{component}.html",
            b"<template><span>Changed candidate surface</span></template>",
        )
    for path, _before, after in extra_changes:
        _write(repository, path, after)
    return repository


def _declarations(component="dealPanel"):
    rest = {
        "sourceNodeId": "object:Opportunity",
        "datasetId": "synthetic",
        "method": "GET",
        "routeTemplate": "/services/data/v{apiVersion}/sobjects/Opportunity/{record}",
        "variables": [
            {
                "name": "apiVersion",
                "valueSource": "API_VERSION",
                "dataType": "STRING",
                "datasetField": None,
            },
            {
                "name": "record",
                "valueSource": "SYNTHETIC_DATASET_RECEIPT",
                "dataType": "ID",
                "datasetField": "Id",
            },
        ],
        "responseFields": [
            {"path": "Id", "dataType": "STRING", "required": True, "nullable": False}
        ],
        "requestExpansion": "EACH_DATASET_RECORD",
        "responseRecordPath": "",
        "datasetFieldPaths": {"Id": "Id"},
        "parentBindings": [],
        "minimumCardinality": 1,
        "maximumCardinality": 1,
        "maximumResponseBytes": 65536,
    }
    custom = {
        **rest,
        "routeTemplate": "/services/apexrest/example/{record}?limit=1",
        "variables": [rest["variables"][1]],
        "responseFields": [
            {"path": "status", "dataType": "STRING", "required": True, "nullable": False},
            {"path": "Id", "dataType": "STRING", "required": True, "nullable": False},
        ],
    }
    return {
        "schemaVersion": "1.0.0",
        "nonRuntimeFiles": [],
        "localTestObligations": [],
        "standardRest": [rest],
        "customRest": [custom],
        "syntheticDatasets": [
            {
                "datasetId": "synthetic",
                "sourceNodeId": "object:Opportunity",
                "objectApiName": "Opportunity",
                "fieldProjection": ["Id", "Fixture_Key__c", "Amount", "OwnerId"],
                "identityField": "Id",
                "predicates": [
                    {
                        "field": "Fixture_Key__c",
                        "operator": "IN_SET",
                        "valueSource": "SOURCE_LITERAL_SET",
                        "values": ["fixture-one"],
                    },
                    {
                        "field": "OwnerId",
                        "operator": "EQUALS",
                        "valueSource": "CURRENT_ENROLLED_ACTOR",
                        "values": [],
                    },
                ],
                "ownershipMarkerField": "Fixture_Key__c",
                "exactCardinality": 1,
            }
        ],
        "browserIntents": [
            {
                "sourceNodeId": f"lwc:{component}",
                "applicationIntent": component,
                "actionClass": "LOCATOR_REBIND_PREVIEW",
                "surfaceIntent": "policy-summary",
                "locatorRebind": _rebind_declaration(component),
            }
        ],
    }


def _rebind_declaration(component="dealPanel"):
    return {
        "navigation": {
            "kind": "DATASET_RECORD_PAGE",
            "datasetId": "synthetic",
            "objectApiName": "Opportunity",
            "identityField": "Id",
            "markerField": "Fixture_Key__c",
            "markerValue": "fixture-one",
            "resolution": "EXACTLY_ONE_AUTHORIZED_DATASET_MEMBER",
        },
        "metadataDrift": {
            "metadataType": "FlexiPage",
            "member": "Fixture_Record",
            "componentName": f"c:{component}",
            "componentIdentifier": "fixturePanel",
            "propertyName": "presentation",
            "operation": "SET_SCALAR",
            "baselineValue": "initial",
            "allowedPreStates": [{"state": "ABSENT"}, {"state": "PRESENT", "value": "initial"}],
            "alternateValue": "alternate",
            "restoration": "PREIMAGE_EXACT",
            "maximumFiles": 1,
            "maximumBytes": 65536,
        },
        "obligationPolicy": "COMPLETE_DECLARED_SET",
        "dataMutation": "FORBIDDEN",
        "obligations": [
            {
                "obligationId": "field:Amount",
                "originalLocator": {
                    "kind": "ATTRIBUTE_EQUALS",
                    "attribute": "data-testid",
                    "value": "initial-amount",
                },
                "semanticIdentity": {
                    "kind": "FIELD",
                    "objectApiName": "Opportunity",
                    "fieldApiName": "Amount",
                },
                "assertions": ["EDITABLE", "ENABLED", "VISIBLE"],
            },
            {
                "obligationId": "probe:inspect",
                "originalLocator": {
                    "kind": "ATTRIBUTE_EQUALS",
                    "attribute": "data-testid",
                    "value": "initial-action",
                },
                "semanticIdentity": {
                    "kind": "ACTION",
                    "objectApiName": "Opportunity",
                    "action": "inspect",
                },
                "assertions": ["ENABLED", "VISIBLE"],
            },
        ],
    }


def _bundle(tmp_path: Path, **repository_options):
    repository = _connected_repository(tmp_path, **repository_options)
    service = AssuranceService(
        source(), InMemoryRunRepository(), foundation_pipeline=_pipeline(repository)
    )
    return repository, service.analyze_current_candidate()


def _profile():
    ontology = load_canonical_ontology(ROOT / "config/ontology/canonical-ontology.json")
    return load_source_graph_profile(
        ROOT / "config/source-profiles/salesforce-dx-semantic-graph.json", ontology
    )


def _inputs(pair):
    repository, bundle = pair
    return HostOwnedSourceContractPort(repository).capture(bundle, _profile())


def _allow_all_policy(compilation):
    policy = load_live_target_policy(ROOT / "config/live-target-policy.json")
    profile = compilation.source_operation_profile
    groups = (
        (TargetPartition.STANDARD_REST, profile.standard_rest),
        (TargetPartition.CUSTOM_REST, profile.custom_rest),
        (TargetPartition.METADATA, profile.metadata),
        (TargetPartition.APEX_TEST, profile.apex_tests),
        (TargetPartition.BROWSER_INTENT, profile.browser_intents),
        (TargetPartition.SYNTHETIC_DATASET, profile.synthetic_datasets),
    )
    body = policy.model_dump(by_alias=True, mode="json")
    body["authorizedTargetSha256s"] = sorted(
        derive_target_sha256(partition, target)
        for partition, targets in groups
        for target in targets
    )
    body["allowedMetadataTypes"] = sorted(
        {item.metadata_type for item in profile.metadata}
        | {
            item.locator_rebind.metadata_drift.metadata_type
            for item in profile.browser_intents
            if item.locator_rebind is not None
        }
    )
    body["producerImplementationSha256"] = live_target_module._producer_implementation_sha256()
    body["allowedApplicationIntents"] = sorted(
        {item.application_intent for item in profile.browser_intents}
    )
    body["sha256"] = contract_sha256(body)
    return type(policy).model_validate(body)


def _classification(compilation, policy):
    now = datetime.now(UTC).replace(microsecond=0)
    scope = ReceiptScope(
        campaign_id="candidate-live",
        project_id=compilation.verified_scope.project_id,
        source_contract_sha256=compilation.source_contract_sha256,
        candidate_sha256=compilation.candidate_bundle_sha256,
        build_sha256="a" * 64,
        operation_plan_sha256="b" * 64,
        restore_scope_sha256="c" * 64,
        policy_sha256=policy.sha256,
        profile_sha256=policy.acceptance_profile_sha256,
        org_fingerprint_sha256="d" * 64,
        actor_fingerprint_sha256="e" * 64,
        recovery_deadline=now + timedelta(minutes=10),
    )
    return HostOrganizationClassification(
        receiptSha256="f" * 64,
        environmentClass="DEVELOPER_EDITION",
        validUntil=compilation.verified_scope.valid_until,
        executionScope=scope,
    )


def test_browser_rebind_captures_exact_page_and_property_source_bytes(
    captured, compilation
) -> None:
    xml = tuple(
        value
        for value in captured.referenced_artifacts
        if value.locator.endswith((".flexipage-meta.xml", ".js-meta.xml"))
    )
    assert len(xml) == 2
    derivation = next(
        value
        for value in compilation.derivations
        if value.partition is TargetPartition.BROWSER_INTENT
    )
    assert {value.artifact_sha256 for value in xml}.issubset(derivation.source_artifact_sha256s)
    assert not compilation.authorizes_execution


def test_browser_page_binds_directly_to_component_at_the_impact_radius_boundary(captured) -> None:
    parsed = compiler_module.SourceOperationDeclarations.model_validate(_declarations())
    nodes, _ = compiler_module._candidate_graph(captured.candidate_bundle)
    artifacts = {value.locator: value for value in captured.referenced_artifacts}
    declaration = parsed.browser_intents[0]
    scope = ("changed:boundary",)
    adjacency = {
        "page:Fixture_Record": {declaration.source_node_id},
        declaration.source_node_id: {"page:Fixture_Record", "bridge:one"},
        "bridge:one": {declaration.source_node_id, "bridge:two"},
        "bridge:two": {"bridge:one", scope[0]},
        scope[0]: {"bridge:two", "object:Opportunity"},
        "object:Opportunity": {scope[0]},
    }
    assert compiler_module._connected_scope_ids("page:Fixture_Record", scope, adjacency) == ()
    assert (
        compiler_module._browser_rebind_source_proof(
            declaration, parsed, nodes, adjacency, scope, artifacts
        )
        is not None
    )
    adjacency["page:Fixture_Record"] = {scope[0]}
    assert (
        compiler_module._browser_rebind_source_proof(
            declaration, parsed, nodes, adjacency, scope, artifacts
        )
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        "member",
        "componentName",
        "componentIdentifier",
        "propertyName",
        "alternateValue",
        "duplicate-component",
        "wrong-page-object",
        "wrong-page-type",
        "property-not-exported",
        "record-page-not-exposed",
        "component-wrong-object",
        "unknown-alternate",
        "same-as-baseline",
        "missing-xml",
    ],
)
def test_browser_rebind_requires_exact_graph_connected_source_metadata(captured, change) -> None:
    declarations = _declarations()
    if change in {
        "member",
        "componentName",
        "componentIdentifier",
        "propertyName",
        "alternateValue",
    }:
        declarations["browserIntents"][0]["locatorRebind"]["metadataDrift"][change] = (
            "c:other" if change == "componentName" else "other"
        )
    parsed = compiler_module.SourceOperationDeclarations.model_validate(declarations)
    nodes, adjacency = compiler_module._candidate_graph(captured.candidate_bundle)
    artifacts = {value.locator: value for value in captured.referenced_artifacts}
    for path, artifact in tuple(artifacts.items()):
        raw = artifact.artifact_bytes
        if path.endswith(".flexipage-meta.xml"):
            if change == "duplicate-component":
                root = ElementTree.fromstring(raw)
                instance = next(root.iter(f"{{{NS}}}componentInstance"))
                root.append(instance)
                raw = ElementTree.tostring(root)
            elif change == "wrong-page-object":
                raw = raw.replace(b">Opportunity<", b">Other__c<")
            elif change == "wrong-page-type":
                raw = raw.replace(b">RecordPage<", b">AppPage<")
            elif change == "missing-xml":
                del artifacts[path]
                continue
        if path.endswith(".js-meta.xml"):
            if change == "property-not-exported":
                raw = raw.replace(b'name="presentation"', b'name="other"')
            elif change == "record-page-not-exposed":
                raw = raw.replace(b">lightning__RecordPage<", b">lightning__AppPage<")
            elif change == "component-wrong-object":
                raw = raw.replace(
                    b"</targetConfig>",
                    b"<objects><object>Other__c</object></objects></targetConfig>",
                )
            elif change == "unknown-alternate":
                raw = raw.replace(b'datasource="initial,alternate"', b'datasource="initial,other"')
            elif change == "same-as-baseline":
                raw = raw.replace(b'default="initial"', b'default="alternate"')
        artifacts[path] = CapturedSourceArtifact(
            locator=path, artifactBytes=raw, artifactSha256=hashlib.sha256(raw).hexdigest()
        )
    assert (
        compiler_module._browser_rebind_source_proof(
            parsed.browser_intents[0], parsed, nodes, adjacency, tuple(nodes), artifacts
        )
        is None
    )


@pytest.mark.parametrize(
    "case,expected",
    [
        ("absent-admitted", True),
        ("present-admitted", True),
        ("absent-not-admitted", False),
        ("present-not-admitted", False),
        ("present-unlisted", False),
        ("present-alternate", False),
        ("default-mismatch-absent", False),
        ("default-mismatch-present", False),
        ("nested-value", False),
        ("duplicate-value", False),
    ],
)
def test_scalar_set_compilation_requires_exact_source_default_and_admitted_prestate(
    captured, case, expected
) -> None:
    document = _declarations()
    drift = document["browserIntents"][0]["locatorRebind"]["metadataDrift"]
    if case == "absent-not-admitted":
        drift["allowedPreStates"] = [{"state": "PRESENT", "value": "initial"}]
    elif case == "present-not-admitted":
        drift["allowedPreStates"] = [{"state": "ABSENT"}]
    parsed = compiler_module.SourceOperationDeclarations.model_validate(document)
    nodes, adjacency = compiler_module._candidate_graph(captured.candidate_bundle)
    artifacts = {value.locator: value for value in captured.referenced_artifacts}
    absent = case in {"absent-admitted", "absent-not-admitted", "default-mismatch-absent"}
    for path, artifact in tuple(artifacts.items()):
        raw = artifact.artifact_bytes
        if path.endswith(".flexipage-meta.xml") and not absent:
            value = {
                "present-unlisted": "unlisted",
                "present-alternate": "alternate",
                "nested-value": "initial<unexpected/>",
                "duplicate-value": "initial</value><value>initial",
            }.get(case, "initial")
            property_xml = (
                "<componentInstanceProperties><name>presentation</name>"
                f"<value>{value}</value></componentInstanceProperties>"
            ).encode()
            raw = raw.replace(b"</componentInstance>", property_xml + b"</componentInstance>")
        if path.endswith(".js-meta.xml") and case.startswith("default-mismatch-"):
            raw = raw.replace(b'default="initial"', b'default="other"')
            raw = raw.replace(
                b'datasource="initial,alternate"', b'datasource="initial,alternate,other"'
            )
        artifacts[path] = CapturedSourceArtifact(
            locator=path, artifactBytes=raw, artifactSha256=hashlib.sha256(raw).hexdigest()
        )
    result = compiler_module._browser_rebind_source_proof(
        parsed.browser_intents[0], parsed, nodes, adjacency, tuple(nodes), artifacts
    )
    assert (result is not None) is expected


def test_custom_metadata_record_graph_kind_maps_to_salesforce_metadata_api() -> None:
    node = SimpleNamespace(
        raw_kind="custom-metadata-record",
        label="Strategic_Discount_Rule.Default",
    )

    assert compiler_module._metadata_identity(node) == (
        "CustomMetadata",
        "Strategic_Discount_Rule.Default",
    )


def _runner():
    return ExpectedRunnerProvenance(
        producerId="neo-live-evidence",
        runnerId="host-salesforce-cli",
        runnerKeyId="host-runner-key",
        runnerVersion="1.0.0",
        adapterVersion="1.0.0",
        toolVersions=(ExecutionToolVersion(tool_id="salesforce-cli", version="2.148.3"),),
    )


@pytest.fixture(scope="module")
def captured_pair(tmp_path_factory: pytest.TempPathFactory):
    return _bundle(tmp_path_factory.mktemp("target-compiler"))


@pytest.fixture(scope="module")
def captured(captured_pair):
    return _inputs(captured_pair)


@pytest.fixture(scope="module")
def compilation(captured):
    return ProductionCandidateTargetCompiler().compile(captured)


def test_product_compiler_derives_all_target_families_from_current_sources(compilation) -> None:
    profile = compilation.source_operation_profile

    assert profile.standard_rest
    assert profile.custom_rest
    assert profile.metadata
    assert profile.apex_tests
    assert profile.browser_intents
    assert profile.synthetic_datasets
    assert profile.browser_intents[0].locator_rebind == (
        compiler_module.SourceOperationDeclarations.model_validate(_declarations())
        .browser_intents[0]
        .locator_rebind
    )
    assert all(
        item.state is TargetDerivationState.DERIVED
        for item in compilation.derivations
        if item.reason is not TargetDerivationReason.NON_APEX_TEST_OBLIGATION
    )
    assert (
        compilation.source_operation_profile_bytes
        == json.dumps(
            profile.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )


def test_host_policy_can_allow_complete_plan_and_expected_contract_is_exact(compilation) -> None:
    policy = _allow_all_policy(compilation)
    classification = _classification(compilation, policy)

    result = compose_candidate_live_plan(
        compilation,
        classification,
        policy,
        runner_provenance=_runner(),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )

    assert result.evaluation.state == "READY"
    assert result.evaluation.plan is not None
    assert result.expected_execution_contract is not None
    assert result.expected_execution_contract.plan_sha256 == result.evaluation.plan.plan_sha256
    assert (
        result.expected_execution_contract_bytes_sha256
        == hashlib.sha256(result.expected_execution_contract_bytes).hexdigest()
    )
    assert {item.receipt_role for item in result.support_requirements} >= {
        "SF-L01",
        "SF-L02",
        "LIVE_TARGET_PLAN_RECEIPT",
        "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
    }
    assert all(item.exact_projection for item in result.expected_execution_contract.assertions)
    rest_assertions = tuple(
        item
        for item in result.expected_execution_contract.assertions
        if item.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
    )
    assert rest_assertions
    assert all(item.resolution_contract_sha256 for item in rest_assertions)
    assert result.expected_execution_contract.datasets
    expected_requirement = next(
        item
        for item in result.support_requirements
        if item.receipt_role == "EXPECTED_EXECUTION_CONTRACT_RECEIPT"
    )
    assert expected_requirement.artifact_sha256 == (result.expected_execution_contract_bytes_sha256)


def test_default_deny_policy_blocks_whole_plan_without_partial_authority(compilation) -> None:
    policy = load_live_target_policy(ROOT / "config/live-target-policy.json")

    result = compose_candidate_live_plan(
        compilation,
        _classification(compilation, policy),
        policy,
        runner_provenance=_runner(),
    )

    assert result.evaluation.state == "BLOCKED"
    assert result.evaluation.plan is None
    assert not result.evaluation.authorized_target_sha256s
    assert result.expected_execution_contract is None
    assert {item.code.value for item in result.evaluation.gaps} >= {"DENIED_TARGET"}


def test_capability_permutation_does_not_change_derived_profile(tmp_path: Path) -> None:
    compiler = ProductionCandidateTargetCompiler()
    first = compiler.compile(_inputs(_bundle(tmp_path / "first")))
    declarations = _declarations()
    declarations["standardRest"][0]["variables"].reverse()
    declarations["syntheticDatasets"][0]["fieldProjection"].reverse()
    second = compiler.compile(_inputs(_bundle(tmp_path / "second", declarations=declarations)))

    first_targets = _allow_all_policy(first).authorized_target_sha256s
    second_targets = _allow_all_policy(second).authorized_target_sha256s
    assert first_targets == second_targets


def test_renamed_component_preserves_generic_derivation_shape(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, component="accountSummary", apex_class="AccountPolicy")

    result = ProductionCandidateTargetCompiler().compile(_inputs(bundle))

    assert result.source_operation_profile.browser_intents[0].application_intent == (
        "accountSummary"
    )
    assert result.source_operation_profile.apex_tests[0].apex_class == "AccountPolicyTest"


def test_unrelated_graph_node_does_not_change_candidate_targets(tmp_path: Path) -> None:
    first = ProductionCandidateTargetCompiler().compile(_inputs(_bundle(tmp_path / "first")))
    second = ProductionCandidateTargetCompiler().compile(
        _inputs(_bundle(tmp_path / "second", unrelated=True))
    )

    assert first.source_operation_profile.model_dump(
        exclude={"profile_id", "scope_artifact_sha256"}
    ) == second.source_operation_profile.model_dump(exclude={"profile_id", "scope_artifact_sha256"})


def test_input_omission_and_changed_bytes_are_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    inputs = _inputs(bundle)
    omitted = inputs.model_copy(update={"referenced_artifacts": inputs.referenced_artifacts[:1]})
    with pytest.raises(CandidateTargetCompilerError, match="capture is incomplete"):
        ProductionCandidateTargetCompiler().compile(omitted)

    changed = inputs.referenced_artifacts[0].model_copy(
        update={"artifact_bytes": inputs.referenced_artifacts[0].artifact_bytes + b" "}
    )
    changed_inputs = inputs.model_copy(
        update={"referenced_artifacts": (changed, *inputs.referenced_artifacts[1:])}
    )
    with pytest.raises(ValidationError, match="digest mismatch"):
        ProductionCandidateTargetCompiler().compile(changed_inputs)


def test_unconnected_browser_component_is_explicitly_unsupported(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, browser_component="absentComponent")

    result = ProductionCandidateTargetCompiler().compile(_inputs(bundle))

    assert not result.source_operation_profile.browser_intents
    assert any(
        item.partition is TargetPartition.BROWSER_INTENT
        and item.state is TargetDerivationState.UNSUPPORTED
        and item.reason is TargetDerivationReason.NO_GRAPH_CONNECTED_ENTITY
        for item in result.derivations
    )
    assert "NO_GRAPH_CONNECTED_ENTITY" in result.blocking_reason_codes


def test_rehashed_contract_from_same_project_cannot_replace_candidate_bytes(captured) -> None:
    raw = captured.source_contract_bytes + b" "
    tampered = captured.model_copy(
        update={
            "source_contract_bytes": raw,
            "source_contract_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    with pytest.raises(CandidateTargetCompilerError, match="exact verified candidate manifest"):
        ProductionCandidateTargetCompiler().compile(tampered)


def test_self_hashed_referenced_bytes_cannot_replace_candidate_artifact(captured) -> None:
    first = captured.referenced_artifacts[0]
    raw = first.artifact_bytes + b" "
    replaced = CapturedSourceArtifact(
        locator=first.locator, artifactBytes=raw, artifactSha256=hashlib.sha256(raw).hexdigest()
    )
    tampered = captured.model_copy(
        update={"referenced_artifacts": (replaced, *captured.referenced_artifacts[1:])}
    )
    with pytest.raises(CandidateTargetCompilerError, match="exact verified candidate manifest"):
        ProductionCandidateTargetCompiler().compile(tampered)


def test_equal_bytes_under_uncaptured_locator_are_rejected(captured) -> None:
    body = captured.model_dump(mode="python")
    body["source_contract_locator"] = "contracts/not-captured.json"
    with pytest.raises(CandidateTargetCompilerError, match="absent from exact"):
        CandidateTargetCompilerInputs.model_validate(body)


def test_stale_candidate_cannot_compile_even_with_valid_self_hashes(captured, monkeypatch) -> None:
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=2)

    monkeypatch.setattr(compiler_module, "datetime", FutureClock)
    with pytest.raises(CandidateTargetCompilerError, match="expired"):
        ProductionCandidateTargetCompiler().compile(captured)


def test_host_capture_refuses_reparse_before_open(captured_pair, monkeypatch) -> None:
    root, bundle = captured_pair
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path.name != "operations.json":
            return actual
        return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0x400)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(CandidateTargetCompilerError, match="reparse"):
        HostOwnedSourceContractPort(root).capture(bundle, _profile())


def test_host_capture_byte_cap_is_checked_before_open(captured_pair, monkeypatch) -> None:
    root, bundle = captured_pair

    def never_open(*args, **kwargs):
        pytest.fail("Oversized source was opened")

    monkeypatch.setattr(compiler_module.os, "open", never_open)
    with pytest.raises(CandidateTargetCompilerError, match="bounded read capacity"):
        compiler_module._read_candidate_file(root, "contracts/agent-interface.json", bundle, 1)


def test_source_file_mutation_during_read_is_rejected(captured_pair, monkeypatch) -> None:
    root, bundle = captured_pair
    original = compiler_module.os.fstat
    count = 0

    def fstat(descriptor):
        nonlocal count
        count += 1
        details = original(descriptor)
        if count == 1:
            return details
        return SimpleNamespace(
            st_dev=details.st_dev,
            st_ino=details.st_ino,
            st_size=details.st_size,
            st_mtime_ns=details.st_mtime_ns + 1,
        )

    monkeypatch.setattr(compiler_module.os, "fstat", fstat)
    with pytest.raises(CandidateTargetCompilerError, match="changed during bounded capture"):
        compiler_module._read_candidate_file(
            root, "contracts/agent-interface.json", bundle, 1048576
        )


@pytest.mark.parametrize("location", ["document", "rest", "variable", "dataset", "browser"])
def test_closed_declarations_reject_unknown_fields(location) -> None:
    declarations = _declarations()
    target = {
        "document": declarations,
        "rest": declarations["customRest"][0],
        "variable": declarations["customRest"][0]["variables"][0],
        "dataset": declarations["syntheticDatasets"][0],
        "browser": declarations["browserIntents"][0],
    }[location]
    target["inferredHint"] = "guess from prose"
    with pytest.raises(ValidationError, match="Extra inputs"):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


@pytest.mark.parametrize(
    "missing",
    [
        "variables",
        "responseFields",
        "minimumCardinality",
        "requestExpansion",
        "responseRecordPath",
        "datasetFieldPaths",
        "parentBindings",
    ],
)
def test_insufficient_rest_declarations_have_no_default_facts(missing) -> None:
    declarations = _declarations()
    del declarations["customRest"][0][missing]
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


def test_legacy_capability_prose_cannot_satisfy_explicit_operation_schema() -> None:
    legacy = {
        "capabilities": [
            {
                "id": "api.custom.rest",
                "status": "implemented",
                "route": "/services/apexrest/example/{OpportunityId}?limit=1..50",
            }
        ]
    }
    with pytest.raises(CandidateTargetCompilerError, match="Explicit versioned"):
        compiler_module._source_operations_reference(legacy)


@pytest.mark.parametrize("flag", ["required", "nullable"])
@pytest.mark.parametrize("value", [None, "false", 0])
def test_source_response_boolean_semantics_are_explicit(flag, value) -> None:
    declarations = _declarations()
    declarations["customRest"][0]["responseFields"][0][flag] = value
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


def test_source_nullable_does_not_default_or_remove_requiredness() -> None:
    declarations = _declarations()
    field = declarations["standardRest"][0]["responseFields"][0]
    field["nullable"] = True
    declaration = compiler_module.SourceOperationDeclarations.model_validate(declarations)
    assert declaration.standard_rest[0].response_fields[0].nullable is True
    assert declaration.standard_rest[0].response_fields[0].required is True
    del field["nullable"]
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


def test_source_literal_absence_differs_from_explicit_null_and_wrong_literal_type() -> None:
    declarations = _declarations()
    field = declarations["standardRest"][0]["responseFields"][0]
    field["nullable"] = True
    unconstrained = compiler_module.SourceOperationDeclarations.model_validate(declarations)
    assert "expectedLiteral" not in unconstrained.standard_rest[0].response_fields[0].model_dump(
        by_alias=True
    )
    field["expectedLiteral"] = {"value": None}
    constrained = compiler_module.SourceOperationDeclarations.model_validate(declarations)
    assert constrained.standard_rest[0].response_fields[0].expected_literal is not None
    assert constrained.standard_rest[0].response_fields[0].expected_literal.value is None
    field["expectedLiteral"] = {"value": 1}
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)
    field["expectedLiteral"] = {"value": "known", "fallback": "invented"}
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


@pytest.mark.parametrize(
    "mutation",
    [
        "marker_omitted",
        "actor_literal",
        "marker_duplicate",
        "identity_absent",
        "single_record_expansion",
        "unprojected_binding",
    ],
)
def test_source_ownership_fanout_cannot_be_narrowed_or_guessed(mutation) -> None:
    declarations = _declarations()
    dataset = declarations["syntheticDatasets"][0]
    rest = declarations["standardRest"][0]
    if mutation == "marker_omitted":
        dataset["exactCardinality"] = 2
    elif mutation == "actor_literal":
        dataset["predicates"][1]["values"] = ["invented-actor"]
    elif mutation == "marker_duplicate":
        dataset["predicates"][0]["values"] *= 2
    elif mutation == "identity_absent":
        dataset["fieldProjection"].remove("Id")
    elif mutation == "single_record_expansion":
        rest["requestExpansion"] = "FIRST_DATASET_RECORD"
    else:
        rest["datasetFieldPaths"] = {"Id": "invented.id"}
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


def test_all_members_fanout_preserves_seven_independent_invocations(tmp_path) -> None:
    declarations = _declarations()
    dataset = declarations["syntheticDatasets"][0]
    dataset["exactCardinality"] = 7
    dataset["predicates"][0]["values"] = [f"independent-member-{index}" for index in range(7)]
    declarations["browserIntents"][0]["locatorRebind"]["navigation"]["markerValue"] = (
        "independent-member-0"
    )
    compilation = ProductionCandidateTargetCompiler().compile(
        _inputs(_bundle(tmp_path, declarations=declarations))
    )
    policy = _allow_all_policy(compilation)
    result = compose_candidate_live_plan(
        compilation, _classification(compilation, policy), policy, runner_provenance=_runner()
    )
    assert result.expected_execution_contract is not None
    assertions = [
        value
        for value in result.expected_execution_contract.assertions
        if value.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
    ]
    assert len(assertions) == 2
    for value in assertions:
        assert value.invocation_count == value.minimum_cardinality == value.maximum_cardinality == 7
        assert value.request_expansion == "EACH_DATASET_RECORD"
        assert value.per_response_minimum_cardinality == value.per_response_maximum_cardinality == 1


def _local_declarations():
    declarations = _declarations()
    declarations["nonRuntimeFiles"] = [
        {
            "locator": "workspace/dx/docs/guide.md",
            "category": "DOCUMENTATION",
            "localObligationIds": ["local:documentation"],
        }
    ]
    declarations["localTestObligations"] = [
        {
            "obligationId": "local:documentation",
            "runnerKind": "NODE_TEST",
            "workingDirectory": "workspace/dx",
            "testLocators": ["workspace/dx/catalog.test.mjs"],
            "maximumSeconds": 60,
            "regeneratedLocators": [],
            "requiredResult": "COMPLETE_PASS_NO_SKIP",
        }
    ]
    return declarations


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "category_spoof",
        "missing_obligation",
        "unknown_obligation",
        "selector",
        "regeneration_missing",
        "regeneration_renamed",
    ],
)
def test_local_source_dispositions_are_closed_and_cannot_drop_obligations(mutation):
    declarations = _local_declarations()
    file = declarations["nonRuntimeFiles"][0]
    obligation = declarations["localTestObligations"][0]
    if mutation == "duplicate":
        declarations["nonRuntimeFiles"].append(dict(file))
    elif mutation == "category_spoof":
        file["locator"] = "workspace/dx/unrelated.py"
    elif mutation == "missing_obligation":
        file["localObligationIds"] = []
    elif mutation == "unknown_obligation":
        file["localObligationIds"] = ["invented:local"]
    elif mutation == "selector":
        obligation["arguments"] = ["--test-name-pattern=one"]
    else:
        file.update(category="GENERATED_INDEX", locator="workspace/dx/knowledge/index.json")
        if mutation == "regeneration_renamed":
            obligation.update(
                runnerKind="NODE_GENERATED_CHECK",
                regeneratedLocators=["workspace/dx/knowledge/renamed.json"],
            )
    with pytest.raises(ValidationError):
        compiler_module.SourceOperationDeclarations.model_validate(declarations)


@pytest.mark.parametrize("add_unknown", [False, True])
def test_declared_nonruntime_is_accounted_without_fake_targets_and_unknown_code_blocks(
    tmp_path, add_unknown
):
    declarations = _local_declarations()
    changes = [
        ("workspace/dx/docs/guide.md", b"# Before\n", b"# Current\n"),
        (
            "workspace/dx/catalog.test.mjs",
            b"import test from 'node:test';test('links',()=>{});",
            b"import test from 'node:test';test('links',()=>{});",
        ),
    ]
    if add_unknown:
        changes.append(("workspace/dx/unrelated.py", b"print('before')", b"print('after')"))
    pair = _bundle(tmp_path, declarations=declarations, extra_changes=tuple(changes))
    compiled = ProductionCandidateTargetCompiler().compile(_inputs(pair))
    by_path = {value.locator: value for value in compiled.changed_file_dispositions}
    assert len(by_path) == len(pair[1].operation_seed_artifact.bindings)
    assert by_path["workspace/dx/docs/guide.md"].disposition == "NON_SALESFORCE_RUNTIME"
    assert all("guide.md" not in entity.entity_id for entity in compiled.verified_scope.entities)
    assert len(compiled.required_local_validations) == 1
    assert compiled.required_local_validations[0].satisfied is False
    if add_unknown:
        assert by_path["workspace/dx/unrelated.py"].disposition == "UNKNOWN_BLOCKING"
        assert "UNKNOWN_CHANGED_FILE" in compiled.blocking_reason_codes
    policy = _allow_all_policy(compiled)
    result = compose_candidate_live_plan(
        compiled, _classification(compiled, policy), policy, runner_provenance=_runner()
    )
    assert result.evaluation.state == "BLOCKED"
    assert result.evaluation.plan is None
    assert result.expected_execution_contract is None
    assert "LOCAL_SOURCE_VALIDATION_RECEIPT" in {
        item.receipt_role for item in result.support_requirements
    }
    required = compiled.required_local_validations[0]
    assert {value.locator for value in required.bound_files} == {
        value.path for value in pair[1].graph_production_artifact.candidate.dispositions
    }
    assert required.candidate_tree_sha256 == pair[1].operation_seed_artifact.candidate_tree_sha256

    # Signed unit-test evidence exercises composition, not a real source execution claim.
    from neo_sf_q_intel.local_validation import LocalValidationFileRoot
    from tests.test_local_validation import _case, _pair, _reseal

    case = _case(tmp_path)
    classification = _classification(compiled, policy)
    case.compilation = compiled
    case.scope = classification.execution_scope
    artifact = _reseal(
        case.artifact,
        candidate_bundle_sha256=compiled.candidate_bundle_sha256,
        candidate_tree_sha256=required.candidate_tree_sha256,
        obligation_id=required.obligation.obligation_id,
        command_contract_sha256=required.command_contract_sha256,
        bound_files=tuple(
            LocalValidationFileRoot(**value.model_dump()) for value in required.bound_files
        ),
        working_directory=required.obligation.working_directory,
        test_locators=required.obligation.test_locators,
        invocations=(
            case.artifact.invocations[0].model_copy(
                update={
                    "test_locator": required.obligation.test_locators[0],
                }
            ),
        ),
    )
    proof = _pair(case, artifact)
    before = compiled.model_dump(mode="json")
    result = compose_candidate_live_plan(
        compiled,
        classification,
        policy,
        runner_provenance=_runner(),
        local_validation_evidence=(proof,),
        local_validation_runner_pins=case.pins,
        local_validation_artifact_store=case.artifact_store,
        issuer_registry=case.registry,
        receipt_ledger=case.ledger,
    )
    assert compiled.model_dump(mode="json") == before
    assert required.satisfied is False
    assert len(result.verified_local_validations) == 1
    assert all(gap.code.value != "LOCAL_VALIDATION_REQUIRED" for gap in result.evaluation.gaps)
    if add_unknown:
        assert result.evaluation.plan is None
        assert result.evaluation.state == "BLOCKED"
    else:
        assert result.evaluation.plan is not None
        assert result.expected_execution_contract is not None
        assert (
            result.expected_execution_contract.verified_local_validations
            == result.verified_local_validations
        )
        assert (
            datetime.fromisoformat(
                result.expected_execution_contract.valid_until.replace("Z", "+00:00")
            )
            <= artifact.expires_at
        )


def test_dataset_ownership_and_cardinality_are_source_declared(compilation) -> None:
    dataset = compilation.source_operation_profile.synthetic_datasets[0]
    assert dataset.ownership_marker_field == "Fixture_Key__c"
    assert "Name" not in dataset.field_projection
    custom = compilation.source_operation_profile.custom_rest[0]
    assert {field.path for field in custom.response_fields} == {"Id", "status"}
    assert all(item.path != "schemaVersion" for item in custom.response_fields)
    assert custom.variables[0].name == "record"
    assert all(
        item.method_name == "verifies" for item in compilation.source_operation_profile.apex_tests
    )
    bindings = {item.locator: item for item in compilation.captured_source_files}
    assert (
        bindings[compilation.source_contract_locator].content_sha256
        == compilation.source_contract_sha256
    )


def test_non_apex_obligation_cannot_be_hidden_by_same_entity_apex_target(tmp_path) -> None:
    pair = _bundle(tmp_path, include_jest=True)
    compiled = ProductionCandidateTargetCompiler().compile(_inputs(pair))
    assert compiled.source_operation_profile.apex_tests
    assert "NON_APEX_TEST_OBLIGATION" in compiled.blocking_reason_codes
    expected = tuple(
        sorted(
            compiler_module._obligation_id(item)
            for item in compiler_module._selected_test_ids(pair[1])
        )
    )
    assert len(expected) >= 2
    assert compiled.verified_scope.mandatory_obligation_ids == expected
    policy = _allow_all_policy(compiled)
    result = compose_candidate_live_plan(
        compiled, _classification(compiled, policy), policy, runner_provenance=_runner()
    )
    assert result.evaluation.state == "BLOCKED"
    assert result.evaluation.plan is None
    assert result.expected_execution_contract is None
    assert not result.evaluation.authorized_target_sha256s


@pytest.mark.parametrize(
    "body, expected",
    [
        ("@isTest static void verifies() {}", ("verifies",)),
        ("static testMethod void older() {} @isTest static void newer() {}", ("newer", "older")),
        ("@isTest static void parameterized(Integer value) {}", ()),
        ("// @isTest static void ignored() {}\n @isTest static void verifies() {}", ("verifies",)),
        ("@isTest static void verifies() {} @isTest static void verifies() {}", ()),
    ],
)
def test_exact_apex_method_parser_abstains_on_unsupported_declarations(body, expected) -> None:
    raw = f"@isTest public class PolicyTest {{ {body} }}".encode()
    assert compiler_module._exact_apex_test_methods(raw, "PolicyTest") == expected


def test_unsupported_apex_signature_retains_obligation_and_blocks_plan(tmp_path) -> None:
    pair = _bundle(tmp_path, method_parameter="Integer parameter")
    compiled = ProductionCandidateTargetCompiler().compile(_inputs(pair))
    assert compiled.verified_scope.mandatory_obligation_ids
    assert not compiled.source_operation_profile.apex_tests
    assert "EXACT_APEX_METHODS_UNRESOLVED" in compiled.blocking_reason_codes
    policy = _allow_all_policy(compiled)
    result = compose_candidate_live_plan(
        compiled, _classification(compiled, policy), policy, runner_provenance=_runner()
    )
    assert result.evaluation.plan is None
    assert result.expected_execution_contract is None
