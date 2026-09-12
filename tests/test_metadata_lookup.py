"""T-META: field metadata lookup from the versioned Salesforce source project.

The healing model needs a field's declared identity to scope candidates: its type, its
visible label, whether it is required, and its permitted values. The evidence graph already
carries type and required, but digests the label and drops picklist values, so this feed is
not a projection of the graph.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neo_sf_q_intel.context_feeds import ContextFeedError, metadata_lookup
from neo_sf_q_intel.salesforce_cli import SalesforceCLI

PICKLIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
    <fullName>Evaluation_Source__c</fullName>
    <type>Picklist</type>
    <valueSet>
        <restricted>true</restricted>
        <valueSetDefinition>
            <sorted>false</sorted>
            <value><fullName>Flow</fullName><default>true</default><label>Flow</label></value>
            <value><fullName>REST</fullName><default>false</default><label>REST API</label></value>
        </valueSetDefinition>
    </valueSet>
    <label>Evaluation Source</label>
</CustomField>
"""

LOOKUP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
    <fullName>Regional_VP_Approver__c</fullName>
    <deleteConstraint>SetNull</deleteConstraint>
    <referenceTo>User</referenceTo>
    <relationshipName>Strategic_Opportunities</relationshipName>
    <required>false</required>
    <type>Lookup</type>
    <label>Regional VP Approver</label>
</CustomField>
"""

CURRENCY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
    <fullName>Amount_INR__c</fullName>
    <type>Currency</type>
    <precision>18</precision>
    <scale>2</scale>
    <required>true</required>
    <label>Amount (INR)</label>
</CustomField>
"""


def _write_field(root: Path, object_name: str, field_name: str, body: str) -> Path:
    directory = root / "force-app" / "main" / "default" / "objects" / object_name / "fields"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{field_name}.field-meta.xml"
    path.write_bytes(body.encode("utf-8"))
    return path


@pytest.fixture(name="salesforce_root")
def _salesforce_root(tmp_path: Path) -> Path:
    _write_field(tmp_path, "Strategic_Deal_Evaluation__c", "Evaluation_Source__c", PICKLIST_XML)
    _write_field(tmp_path, "Opportunity", "Regional_VP_Approver__c", LOOKUP_XML)
    _write_field(tmp_path, "Strategic_Deal_Evaluation__c", "Amount_INR__c", CURRENCY_XML)
    return tmp_path


def test_lookup_field_reports_identity_and_reference(salesforce_root: Path) -> None:
    result = metadata_lookup(salesforce_root, "Opportunity", "Regional_VP_Approver__c")

    assert result.object_api_name == "Opportunity"
    assert result.field_api_name == "Regional_VP_Approver__c"
    assert result.field_type == "Lookup"
    assert result.label == "Regional VP Approver"
    assert result.required is False
    assert result.reference_to == ["User"]
    assert result.relationship_name == "Strategic_Opportunities"
    assert result.picklist_values == []
    assert result.source_path.endswith("Regional_VP_Approver__c.field-meta.xml")
    assert "\\" not in result.source_path


def test_picklist_values_are_returned_with_labels_and_default(salesforce_root: Path) -> None:
    result = metadata_lookup(
        salesforce_root, "Strategic_Deal_Evaluation__c", "Evaluation_Source__c"
    )

    assert result.field_type == "Picklist"
    assert result.restricted_picklist is True
    assert [item.value for item in result.picklist_values] == ["Flow", "REST"]
    assert [item.label for item in result.picklist_values] == ["Flow", "REST API"]
    assert [item.default for item in result.picklist_values] == [True, False]


def test_absent_required_element_reads_as_not_required(salesforce_root: Path) -> None:
    result = metadata_lookup(
        salesforce_root, "Strategic_Deal_Evaluation__c", "Evaluation_Source__c"
    )

    assert result.required is False


def test_declared_required_is_preserved(salesforce_root: Path) -> None:
    result = metadata_lookup(salesforce_root, "Strategic_Deal_Evaluation__c", "Amount_INR__c")

    assert result.required is True
    assert result.field_type == "Currency"


def test_standard_field_without_metadata_is_reported_not_found(salesforce_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Amount")

    assert error.value.code == "FIELD_METADATA_NOT_FOUND"


def test_unknown_object_is_reported_not_found(salesforce_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Absent__c", "Regional_VP_Approver__c")

    assert error.value.code == "FIELD_METADATA_NOT_FOUND"


@pytest.mark.parametrize(
    "object_name",
    ["", "../Opportunity", "Opportunity/fields", "1Opportunity", "Opp\x00", "A" * 201],
)
def test_object_names_outside_the_api_name_grammar_are_rejected(
    salesforce_root: Path, object_name: str
) -> None:
    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, object_name, "Regional_VP_Approver__c")

    assert error.value.code == "OBJECT_NAME_INVALID"


@pytest.mark.parametrize(
    "field_name",
    ["", "../../secrets", "Regional/VP", "9Field__c", "A" * 201],
)
def test_field_names_outside_the_api_name_grammar_are_rejected(
    salesforce_root: Path, field_name: str
) -> None:
    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", field_name)

    assert error.value.code == "FIELD_NAME_INVALID"


def test_full_name_disagreeing_with_the_file_name_is_rejected(salesforce_root: Path) -> None:
    _write_field(salesforce_root, "Opportunity", "Impostor__c", LOOKUP_XML)

    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Impostor__c")

    assert error.value.code == "FIELD_METADATA_INVALID"


def test_unparseable_metadata_is_reported_rather_than_raised_raw(salesforce_root: Path) -> None:
    _write_field(salesforce_root, "Opportunity", "Broken__c", "<CustomField><fullName>")

    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Broken__c")

    assert error.value.code == "FIELD_METADATA_UNREADABLE"


def test_wrong_root_element_is_rejected(salesforce_root: Path) -> None:
    _write_field(
        salesforce_root,
        "Opportunity",
        "Wrong__c",
        '<?xml version="1.0"?><CustomObject><fullName>Wrong__c</fullName></CustomObject>',
    )

    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Wrong__c")

    assert error.value.code == "FIELD_METADATA_INVALID"


def test_oversized_metadata_is_refused(salesforce_root: Path) -> None:
    padding = "<description>" + ("x" * 300_000) + "</description>"
    _write_field(
        salesforce_root,
        "Opportunity",
        "Huge__c",
        f'<?xml version="1.0"?><CustomField><fullName>Huge__c</fullName>{padding}</CustomField>',
    )

    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Huge__c")

    assert error.value.code == "FIELD_METADATA_TOO_LARGE"


def test_missing_salesforce_root_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(tmp_path / "absent", "Opportunity", "Regional_VP_Approver__c")

    assert error.value.code == "SALESFORCE_ROOT_UNAVAILABLE"


def test_global_value_set_reports_the_name_without_inventing_values(salesforce_root: Path) -> None:
    _write_field(
        salesforce_root,
        "Opportunity",
        "Global__c",
        '<?xml version="1.0"?><CustomField><fullName>Global__c</fullName>'
        "<type>Picklist</type><label>Global</label>"
        "<valueSet><valueSetName>Regions</valueSetName></valueSet></CustomField>",
    )

    result = metadata_lookup(salesforce_root, "Opportunity", "Global__c")

    assert result.picklist_values == []
    assert result.value_set_name == "Regions"


def _configured_root() -> Path | None:
    """Resolve the real source project, or None when it is not checked out beside this repo."""
    from neo_sf_q_intel.config import Settings

    try:
        root = Settings().resolved_salesforce_root(Path.cwd())
    except ValueError:
        return None
    return root if root.is_dir() else None


REAL_ROOT = _configured_root()
requires_source_project = pytest.mark.skipif(
    REAL_ROOT is None,
    reason="The configured Salesforce source project is not available here",
)


@requires_source_project
def test_real_lookup_field_is_read_from_the_source_project() -> None:
    assert REAL_ROOT is not None
    result = metadata_lookup(REAL_ROOT, "Opportunity", "Regional_VP_Approver__c")

    assert result.field_type == "Lookup"
    assert result.label == "Regional VP Approver"
    assert result.reference_to == ["User"]


@requires_source_project
def test_real_picklist_field_returns_its_declared_values() -> None:
    assert REAL_ROOT is not None
    result = metadata_lookup(REAL_ROOT, "Strategic_Deal_Evaluation__c", "Evaluation_Source__c")

    assert result.field_type == "Picklist"
    assert result.restricted_picklist is True
    assert "Flow" in [item.value for item in result.picklist_values]


@requires_source_project
def test_every_declared_field_in_the_source_project_parses() -> None:
    assert REAL_ROOT is not None
    objects_root = REAL_ROOT / "force-app" / "main" / "default" / "objects"
    declared = sorted(objects_root.glob("*/fields/*.field-meta.xml"))

    assert declared, "the source project declares no custom fields"
    for path in declared:
        object_name = path.parent.parent.name
        field_name = path.name.removesuffix(".field-meta.xml")
        result = metadata_lookup(REAL_ROOT, object_name, field_name)
        assert result.field_api_name == field_name
        assert result.field_type is not None


def test_serialized_output_uses_camel_case_aliases(salesforce_root: Path) -> None:
    payload = metadata_lookup(salesforce_root, "Opportunity", "Regional_VP_Approver__c").model_dump(
        by_alias=True, mode="json"
    )

    assert payload["objectApiName"] == "Opportunity"
    assert payload["fieldApiName"] == "Regional_VP_Approver__c"
    assert payload["type"] == "Lookup"
    assert payload["referenceTo"] == ["User"]


def _forbid_org_transports(monkeypatch: pytest.MonkeyPatch) -> str:
    """Fail the test outright if the lookup reaches a live org instead of the source project."""
    message = "Metadata lookup must not invoke a live Salesforce transport"

    def forbidden(*args, **kwargs):
        pytest.fail(message)

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.org_status", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.rest_get", forbidden)
    return message


def test_a_declared_field_is_read_without_invoking_any_org_transport(
    salesforce_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_org_transports(monkeypatch)

    result = metadata_lookup(salesforce_root, "Opportunity", "Regional_VP_Approver__c")

    assert result.field_type == "Lookup"
    assert result.label == "Regional VP Approver"
    assert result.reference_to == ["User"]


def test_an_absent_field_is_reported_not_found_rather_than_described_from_the_org(
    salesforce_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A standard field the project does not declare must never trigger a fallback describe."""
    _forbid_org_transports(monkeypatch)

    with pytest.raises(ContextFeedError) as error:
        metadata_lookup(salesforce_root, "Opportunity", "Amount")

    assert error.value.code == "FIELD_METADATA_NOT_FOUND"


@requires_source_project
def test_the_real_source_project_is_read_without_invoking_any_org_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert REAL_ROOT is not None
    _forbid_org_transports(monkeypatch)

    result = metadata_lookup(REAL_ROOT, "Opportunity", "Regional_VP_Approver__c")

    assert result.field_type == "Lookup"
    assert result.label == "Regional VP Approver"
    assert result.reference_to == ["User"]


def test_the_forbidden_transport_guard_fails_the_test_when_a_transport_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-check: a guard that is never tripped proves nothing, so prove this one bites."""
    message = _forbid_org_transports(monkeypatch)

    for invoke in (
        lambda: subprocess.run(["sf", "org", "display", "--json"], check=False),
        lambda: SalesforceCLI.org_status(None),
        lambda: SalesforceCLI.rest_get(None, "/services/data"),
    ):
        with pytest.raises(pytest.fail.Exception, match=message):
            invoke()
