"""Opt-in functional coverage for the healing context join (REQ-HEAL-13).

The unit suite proves the identity guard against hand-built Python objects on both sides.
This module proves the same guard when neither side is hand-built: the signature is read back
out of real PostgreSQL and the field metadata is parsed out of the real configured Salesforce
source project. A mismatch has to be refused across that boundary, and a match has to produce
a context that still carries no raw org value.

Skipped unless NEO_SIGNATURE_FUNCTIONAL=1, a DATABASE_URL is configured, and the source
project is checked out beside this repository.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.context_feeds import metadata_lookup
from neo_sf_q_intel.element_signature import ElementSignatureRepository, build_signature
from neo_sf_q_intel.locator_proposal import LocatorProposalError, build_healing_context

pytestmark = pytest.mark.skipif(
    os.environ.get("NEO_SIGNATURE_FUNCTIONAL") != "1",
    reason="Set NEO_SIGNATURE_FUNCTIONAL=1 to run the live PostgreSQL healing bridge checks",
)

PROJECT = "healing-bridge-functional"
PAGE_KEY = "strategic-deal-workbench"
OBJECT = "Opportunity"
SIGNED_FIELD = "Regional_VP_Approver__c"
OTHER_FIELD = "Approval_Status__c"

# Synthetic org values, never persisted: the store keeps their digests only.
RECORD_ID = "005fj00000N5t8IAAR"
PERSON_NAME = "Synthetic Regional VP"
RECORD_ID_DIGEST = "b167df2522f2e65b"
PERSON_NAME_DIGEST = "c5ecd76d4d625d98"


def _configured_root() -> Path | None:
    """Resolve the real source project, or None when it is not checked out beside this repo."""

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


@pytest.fixture(name="connection_factory")
def _connection_factory() -> Callable[[], object]:
    settings = Settings()
    database_url = settings.database_url
    if database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    import psycopg

    schema = settings.postgres_schema
    dsn = database_url.get_secret_value()

    def factory() -> object:
        connection = psycopg.connect(dsn, autocommit=True)
        connection.execute(f'SET search_path TO "{schema}"')
        return connection

    return factory


@pytest.fixture(name="stored_signature")
def _stored_signature(connection_factory: Callable[[], object]):
    """Save one signature and hand back what PostgreSQL actually returns, not what we built."""

    migration = Path("migrations/004_element_signature.sql").read_text(encoding="utf-8")
    with connection_factory() as connection:
        connection.execute(migration)
    repository = ElementSignatureRepository(connection_factory, Settings().postgres_schema)
    repository.save(
        build_signature(
            project_id=PROJECT,
            page_key=PAGE_KEY,
            object_api_name=OBJECT,
            field_api_name=SIGNED_FIELD,
            obligation_id="ob-vp",
            structure="lightning-input-field>input[role=combobox]",
            attributes={"data-value": RECORD_ID, "title": PERSON_NAME},
            nearby=["label"],
            snapshot_root="b" * 64,
            captured_at_utc="2026-09-12T18:20:00Z",
        )
    )
    signature = repository.lookup(
        project_id=PROJECT,
        page_key=PAGE_KEY,
        object_api_name=OBJECT,
        field_api_name=SIGNED_FIELD,
    )
    # Precondition: the join below is fed by a row that really came back out of PostgreSQL.
    assert signature is not None
    assert signature.field_api_name == SIGNED_FIELD
    assert signature.attrs_hashed == {
        "data-value": RECORD_ID_DIGEST,
        "title": PERSON_NAME_DIGEST,
    }
    yield signature
    with connection_factory() as connection:
        connection.execute("DELETE FROM element_signature WHERE project_id = %s", (PROJECT,))


def _real_metadata(field_api_name: str):
    assert REAL_ROOT is not None
    metadata = metadata_lookup(REAL_ROOT, OBJECT, field_api_name)
    # Precondition: the source project really resolved and really declares this field.
    assert metadata.field_api_name == field_api_name
    assert metadata.source_path.endswith(f"{field_api_name}.field-meta.xml")
    return metadata


def _dom_evidence() -> dict:
    return {
        "capturedForOutcome": "LOCATOR_NOT_FOUND",
        "candidateCount": 2,
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
            for index in range(2)
        ],
    }


@requires_source_project
def test_a_stored_signature_for_another_field_is_refused_against_real_metadata(
    stored_signature,
) -> None:
    metadata = _real_metadata(OTHER_FIELD)
    assert stored_signature.field_api_name != OTHER_FIELD

    with pytest.raises(LocatorProposalError) as error:
        build_healing_context(
            obligation_id="ob-vp",
            object_api_name=OBJECT,
            field_api_name=OTHER_FIELD,
            dom_evidence=_dom_evidence(),
            field_metadata=metadata,
            signature=stored_signature,
        )

    assert error.value.code == "SIGNATURE_IDENTITY_MISMATCH"


@requires_source_project
def test_real_metadata_for_another_field_is_refused_against_the_stored_signature(
    stored_signature,
) -> None:
    metadata = _real_metadata(OTHER_FIELD)
    assert stored_signature.field_api_name == SIGNED_FIELD

    with pytest.raises(LocatorProposalError) as error:
        build_healing_context(
            obligation_id="ob-vp",
            object_api_name=OBJECT,
            field_api_name=SIGNED_FIELD,
            dom_evidence=_dom_evidence(),
            field_metadata=metadata,
            signature=stored_signature,
        )

    assert error.value.code == "METADATA_IDENTITY_MISMATCH"


@requires_source_project
def test_matching_identity_joins_postgres_and_the_source_project(stored_signature) -> None:
    metadata = _real_metadata(SIGNED_FIELD)

    context = build_healing_context(
        obligation_id="ob-vp",
        object_api_name=OBJECT,
        field_api_name=SIGNED_FIELD,
        dom_evidence=_dom_evidence(),
        field_metadata=metadata,
        signature=stored_signature,
        graph_edges=["field:Opportunity.Regional_VP_Approver__c -> references -> object:User"],
        intent_section="The approver lookup must be editable before submission.",
    )

    assert context.field_api_name == SIGNED_FIELD
    assert context.field_label == metadata.label
    assert context.field_type == metadata.field_type
    assert context.signature_structure == stored_signature.structure
    assert len(context.candidates) == 2
    # The digest from PostgreSQL and the digest from the worker describe the same value.
    assert context.signature_attr_hashes["title"] == context.candidates[0].attr_hashes["title"]


@requires_source_project
def test_no_raw_org_value_survives_the_real_join(stored_signature) -> None:
    context = build_healing_context(
        obligation_id="ob-vp",
        object_api_name=OBJECT,
        field_api_name=SIGNED_FIELD,
        dom_evidence=_dom_evidence(),
        field_metadata=_real_metadata(SIGNED_FIELD),
        signature=stored_signature,
    )

    serialized = json.dumps(context.model_dump(by_alias=True, mode="json"))

    assert RECORD_ID not in serialized
    assert PERSON_NAME not in serialized
    # Precondition: the digests really travelled, so the absence above is not an empty context.
    assert RECORD_ID_DIGEST in serialized
    assert PERSON_NAME_DIGEST in serialized
