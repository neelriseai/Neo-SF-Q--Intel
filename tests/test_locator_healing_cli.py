from __future__ import annotations

from pathlib import Path

from neo_sf_q_intel.context_feeds import ContextFeedError, FieldMetadata
from neo_sf_q_intel.locator_healing_cli import _field_metadata_for


class _Settings:
    def __init__(self, *, root: Path | None = Path("source"), fail: bool = False) -> None:
        self.root = root
        self.fail = fail

    def resolved_salesforce_root(self, repository_root: Path | None = None) -> Path:
        if self.fail or self.root is None:
            raise ValueError("not configured")
        return (repository_root or Path.cwd()) / self.root


def test_cli_metadata_enrichment_reads_configured_source_root() -> None:
    calls: list[tuple[Path, str, str]] = []

    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        calls.append((root, object_api_name, field_api_name))
        return FieldMetadata(
            objectApiName=object_api_name,
            fieldApiName=field_api_name,
            type="Lookup",
            label="Regional VP Approver",
            required=False,
            sourcePath="force-app/main/default/objects/Opportunity/fields/x.field-meta.xml",
        )

    result = _field_metadata_for(
        _Settings(root=Path("dx")),
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
        lookup=lookup,
        repository_root=Path("repo"),
    )

    assert result is not None
    assert result.field_type == "Lookup"
    assert calls == [
        (
            Path("repo") / "dx",
            "Opportunity",
            "Regional_VP_Approver__c",
        )
    ]


def test_cli_metadata_enrichment_is_non_fatal_when_source_root_is_unconfigured() -> None:
    called = False

    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        nonlocal called
        called = True
        raise AssertionError("lookup should not be called")

    assert (
        _field_metadata_for(
            _Settings(fail=True),
            object_api_name="Opportunity",
            field_api_name="Regional_VP_Approver__c",
            lookup=lookup,
            repository_root=Path("repo"),
        )
        is None
    )
    assert called is False


def test_cli_metadata_enrichment_is_non_fatal_when_lookup_fails() -> None:
    def lookup(root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata:
        raise ContextFeedError("FIELD_NOT_FOUND")

    assert (
        _field_metadata_for(
            _Settings(),
            object_api_name="Opportunity",
            field_api_name="Absent__c",
            lookup=lookup,
            repository_root=Path("repo"),
        )
        is None
    )
