"""Opt-in functional coverage for the element signature store.

Unit tests use a fake connection and prove call shape only. This module proves the real
contract: the migration guard passes in the claimed schema, jsonb adapts, a saved
signature round-trips identically, an actually unreachable PostgreSQL fails closed with no
fallback artefact, and a row tampered with directly in SQL is refused on read. Skipped unless
NEO_SIGNATURE_FUNCTIONAL=1 and a DATABASE_URL is configured, matching the repository's other
opt-in integration checks.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.element_signature import (
    ElementSignatureRepository,
    SignatureCorrupt,
    SignatureStoreUnavailable,
    build_signature,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NEO_SIGNATURE_FUNCTIONAL") != "1",
    reason="Set NEO_SIGNATURE_FUNCTIONAL=1 to run the live PostgreSQL signature checks",
)

PROJECT = "signature-functional-probe"
FIELD = "Regional_VP_Approver__c"
KEY = {
    "project_id": PROJECT,
    "page_key": "strategic-deal-workbench",
    "object_api_name": "Opportunity",
    "field_api_name": FIELD,
}


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


@pytest.fixture(name="repository")
def _repository(connection_factory: Callable[[], object]) -> ElementSignatureRepository:
    migration = Path("migrations/004_element_signature.sql").read_text(encoding="utf-8")
    with connection_factory() as connection:
        connection.execute(migration)
    repository = ElementSignatureRepository(connection_factory, Settings().postgres_schema)
    yield repository
    with connection_factory() as connection:
        connection.execute("DELETE FROM element_signature WHERE project_id = %s", (PROJECT,))


def _signature(field: str = "Regional_VP_Approver__c"):
    return build_signature(
        project_id=PROJECT,
        page_key="strategic-deal-workbench",
        object_api_name="Opportunity",
        field_api_name=field,
        obligation_id="ob-functional",
        structure="lightning-input-field>input[role=combobox]",
        attributes={"data-value": "005fj00000N5t8IAAR", "title": "Synthetic Regional VP"},
        nearby=["label", "lightning-icon"],
        snapshot_root="b" * 64,
        captured_at_utc="2026-09-12T18:20:00Z",
    )


def test_signature_round_trips_through_postgresql(repository) -> None:
    signature = _signature()
    repository.save(signature)

    stored = repository.lookup(
        project_id=PROJECT,
        page_key="strategic-deal-workbench",
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
    )

    assert stored == signature
    assert "005fj00000N5t8IAAR" not in str(stored.model_dump(mode="json"))
    assert "Synthetic Regional VP" not in str(stored.model_dump(mode="json"))


def test_latest_capture_wins_on_the_same_key(repository) -> None:
    first = _signature()
    repository.save(first)
    repository.save(first)

    stored = repository.lookup(
        project_id=PROJECT,
        page_key="strategic-deal-workbench",
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
    )

    assert stored == first


def test_secondary_obligation_index_returns_every_element(repository) -> None:
    repository.save(_signature())
    repository.save(_signature(field="Amount"))

    rows = repository.lookup_by_obligation(project_id=PROJECT, obligation_id="ob-functional")

    assert len(rows) == 2
    assert {row.field_api_name for row in rows} == {"Regional_VP_Approver__c", "Amount"}


def test_missing_key_returns_none(repository) -> None:
    assert (
        repository.lookup(
            project_id=PROJECT,
            page_key="strategic-deal-workbench",
            object_api_name="Opportunity",
            field_api_name="Absent__c",
        )
        is None
    )


# REQ-HEAL-02: PostgreSQL is the only store. An unreachable database is reported, never
# quietly replaced by SQLite, a JSON file, or an in-process cache.


def _refused_port() -> int:
    """Bind a loopback port and release it, so a connection there is refused rather than hung."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _tree(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


@pytest.fixture(name="unreachable_repository")
def _unreachable_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import psycopg

    refused_port = _refused_port()
    dsn = "".join(
        [
            "postgresql",
            "://",
            "neo",
            ":",
            "neo",
            f"@127.0.0.1:{refused_port}/neo?connect_timeout=2",
        ]
    )
    # Precondition: the port really refuses, so a later failure cannot be a mocking artefact.
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(dsn)

    def factory() -> object:
        return psycopg.connect(dsn, autocommit=True)

    repository = ElementSignatureRepository(factory, Settings().postgres_schema)
    # Every configured fallback location (sqlite_path, outcome_json_path, .runtime) is
    # repository-relative, so an empty working directory makes any of them visible.
    monkeypatch.chdir(tmp_path)
    return repository


def test_save_against_an_unreachable_database_is_reported_not_degraded(
    unreachable_repository, tmp_path: Path
) -> None:
    import psycopg

    before = _tree(tmp_path)

    with pytest.raises(SignatureStoreUnavailable) as error:
        unreachable_repository.save(_signature())

    assert error.value.code == "STORE_UNAVAILABLE"
    # The refusal came from a real driver attempt, not from an early configuration check.
    assert isinstance(error.value.__cause__, psycopg.OperationalError)
    assert _tree(tmp_path) == before == set()


def test_lookup_against_an_unreachable_database_is_reported_not_degraded(
    unreachable_repository, tmp_path: Path
) -> None:
    import psycopg

    before = _tree(tmp_path)

    with pytest.raises(SignatureStoreUnavailable) as error:
        unreachable_repository.lookup(**KEY)

    assert error.value.code == "STORE_UNAVAILABLE"
    assert isinstance(error.value.__cause__, psycopg.OperationalError)
    # An outage is transient and retryable; it must never be filed as a tamper signal.
    assert not isinstance(error.value, SignatureCorrupt)
    assert _tree(tmp_path) == before == set()


def test_no_sqlite_json_or_cache_artefact_is_written_when_the_store_is_down(
    unreachable_repository, tmp_path: Path
) -> None:
    signature = _signature()
    for call in (
        lambda: unreachable_repository.save(signature),
        lambda: unreachable_repository.lookup(**KEY),
        lambda: unreachable_repository.lookup_by_obligation(
            project_id=PROJECT, obligation_id="ob-functional"
        ),
    ):
        with pytest.raises(SignatureStoreUnavailable):
            call()

    created = _tree(tmp_path)
    assert created == set(), f"the store created a fallback artefact: {sorted(created)}"


# REQ-HEAL-03: a row tampered with directly in SQL is refused on read; a signature is never
# returned on the strength of columns that no longer agree with its digest.


def _mutate(
    connection_factory: Callable[[], object], column: str, value: str, cast: str = ""
) -> int:
    statement = (
        f"UPDATE element_signature SET {column} = %s{cast} "
        "WHERE project_id = %s AND page_key = %s "
        "AND object_api_name = %s AND field_api_name = %s"
    )
    with connection_factory() as connection:
        cursor = connection.execute(
            statement, (value, PROJECT, KEY["page_key"], KEY["object_api_name"], FIELD)
        )
        return int(cursor.rowcount)


def _column(connection_factory: Callable[[], object], column: str):
    statement = (
        f"SELECT {column} FROM element_signature "
        "WHERE project_id = %s AND page_key = %s "
        "AND object_api_name = %s AND field_api_name = %s"
    )
    with connection_factory() as connection:
        row = connection.execute(
            statement, (PROJECT, KEY["page_key"], KEY["object_api_name"], FIELD)
        ).fetchone()
    assert row is not None
    return row[0]


def test_a_tampered_structure_column_is_refused_on_read(repository, connection_factory) -> None:
    signature = _signature()
    repository.save(signature)
    assert repository.lookup(**KEY) == signature, "precondition: the row was really stored"
    tampered = "div>input[role=textbox]"
    assert tampered != signature.structure

    assert _mutate(connection_factory, "structure", tampered) == 1
    assert _column(connection_factory, "structure") == tampered

    with pytest.raises(SignatureCorrupt) as error:
        repository.lookup(**KEY)

    assert error.value.code == "SIGNATURE_CORRUPT"
    # Tamper is not a transient store outage, so it must not arrive as the retryable type.
    assert not isinstance(error.value, SignatureStoreUnavailable)


def test_a_tampered_attrs_hashed_column_is_refused_on_read(repository, connection_factory) -> None:
    signature = _signature()
    repository.save(signature)
    assert repository.lookup(**KEY) == signature, "precondition: the row was really stored"
    tampered = '{"data-value":"0000000000000000","title":"1111111111111111"}'

    assert _mutate(connection_factory, "attrs_hashed", tampered, cast="::jsonb") == 1
    assert _column(connection_factory, "attrs_hashed") == {
        "data-value": "0000000000000000",
        "title": "1111111111111111",
    }

    with pytest.raises(SignatureCorrupt) as error:
        repository.lookup(**KEY)

    assert error.value.code == "SIGNATURE_CORRUPT"
    assert not isinstance(error.value, SignatureStoreUnavailable)


def test_a_tampered_signature_digest_is_refused_on_read(repository, connection_factory) -> None:
    signature = _signature()
    repository.save(signature)
    assert repository.lookup(**KEY) == signature, "precondition: the row was really stored"
    tampered = "c" * 64
    assert tampered != signature.signature_sha256

    assert _mutate(connection_factory, "signature_sha256", tampered) == 1
    assert _column(connection_factory, "signature_sha256") == tampered

    with pytest.raises(SignatureCorrupt) as error:
        repository.lookup(**KEY)

    assert error.value.code == "SIGNATURE_CORRUPT"
    assert not isinstance(error.value, SignatureStoreUnavailable)


def test_a_tampered_row_is_also_refused_through_the_obligation_index(
    repository, connection_factory
) -> None:
    signature = _signature()
    repository.save(signature)
    assert repository.lookup_by_obligation(project_id=PROJECT, obligation_id="ob-functional")
    tampered = "2026-01-01T00:00:00Z"
    assert tampered != signature.captured_at_utc

    assert _mutate(connection_factory, "captured_at_utc", tampered) == 1
    assert _column(connection_factory, "captured_at_utc") == tampered

    with pytest.raises(SignatureCorrupt) as error:
        repository.lookup_by_obligation(project_id=PROJECT, obligation_id="ob-functional")

    assert error.value.code == "SIGNATURE_CORRUPT"
    assert not isinstance(error.value, SignatureStoreUnavailable)
