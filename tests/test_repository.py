import json
import sqlite3
from pathlib import Path

import pytest

from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.postgres_schema import POSTGRES_SCHEMA_OWNERSHIP_SQL
from neo_sf_q_intel.repository import (
    SCHEMA_SQL,
    VECTOR_COLUMN_CHECK_SQL,
    PersistenceSchemaError,
    PostgresRunRepository,
    SQLiteRunRepository,
    _parse_run_document,
)


class FakePostgresConnection:
    def __init__(self, forbidden_columns: list[tuple[str, str, str, str]]) -> None:
        self.forbidden_columns = forbidden_columns
        self.statements: list[str] = []

    def __enter__(self) -> FakePostgresConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, parameters: object = None) -> FakePostgresConnection:
        rendered = statement.as_string() if hasattr(statement, "as_string") else str(statement)
        self.statements.append(rendered)
        return self

    def fetchall(self) -> list[tuple[str, str, str, str]]:
        return self.forbidden_columns


def test_sqlite_repository_self_creates_and_round_trips_runs(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "fallback.db"
    repository = SQLiteRunRepository(database)
    run = AssuranceRun(
        request=ChangeRequest(requirement="Assess configured metadata", project_id="fixture"),
        reasoning_policy_version="1.1.0",
        reasoning_policy_sha256="0" * 64,
        reasoning_eval_set_id="unit-fixture",
        reasoning_eval_set_sha256="1" * 64,
        source_snapshot="fixture-snapshot",
        source_graph_sha256="5" * 64,
        ontology_id="fixture-ontology",
        ontology_version="1.0.0",
        ontology_sha256="2" * 64,
        source_profile_id="fixture-profile",
        source_profile_version="1.0.0",
        source_profile_sha256="3" * 64,
        normalized_graph_sha256="4" * 64,
    )

    repository.setup()
    repository.save(run)

    assert database.is_file()
    assert repository.get(run.run_id) == run
    assert repository.list_recent() == [run]
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(assurance_runs)").fetchall()
        }
    assert "run_document" in columns
    assert "embedding" not in columns


def test_pre_ontology_run_documents_remain_readable_as_explicit_legacy(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    repository = SQLiteRunRepository(database)
    run = AssuranceRun(
        request=ChangeRequest(requirement="Assess configured metadata", project_id="fixture"),
        reasoning_policy_version="1.1.0",
        reasoning_policy_sha256="0" * 64,
        reasoning_eval_set_id="unit-fixture",
        reasoning_eval_set_sha256="1" * 64,
        source_snapshot="fixture-snapshot",
        source_graph_sha256="5" * 64,
        ontology_id="fixture-ontology",
        ontology_version="1.0.0",
        ontology_sha256="2" * 64,
        source_profile_id="fixture-profile",
        source_profile_version="1.0.0",
        source_profile_sha256="3" * 64,
        normalized_graph_sha256="4" * 64,
    )
    repository.setup()
    repository.save(run)
    legacy = run.model_dump(mode="json")
    legacy.pop("schema_version")
    for field in (
        "source_snapshot",
        "source_graph_sha256",
        "ontology_id",
        "ontology_version",
        "ontology_sha256",
        "source_profile_id",
        "source_profile_version",
        "source_profile_sha256",
        "normalized_graph_sha256",
    ):
        legacy.pop(field)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE assurance_runs SET run_document = ? WHERE run_id = ?",
            (json.dumps(legacy), str(run.run_id)),
        )

    restored = repository.get(run.run_id)

    assert restored is not None
    assert restored.schema_version == "1.0.0"
    assert restored.ontology_id is None
    assert restored.source_graph_sha256 is None
    assert repository.list_recent() == [restored]
    assert _parse_run_document(legacy) == restored


def test_postgresql_setup_auto_creates_and_applies_vector_boundary_migration(monkeypatch) -> None:
    connection = FakePostgresConnection(
        [("column", "knowledge_chunks", "search_vector", "tsvector")]
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect", lambda database_url: connection
    )

    PostgresRunRepository("postgresql://configured").setup()

    assert connection.statements[-2:] == [SCHEMA_SQL, VECTOR_COLUMN_CHECK_SQL]
    assert connection.statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert connection.statements[1] == 'CREATE SCHEMA IF NOT EXISTS "neo_sf_q_intel"'
    assert connection.statements[2] == 'SET search_path TO "neo_sf_q_intel"'
    assert connection.statements[3] == POSTGRES_SCHEMA_OWNERSHIP_SQL
    assert "CREATE TABLE IF NOT EXISTS assurance_runs" in connection.statements[-2]
    assert "DROP COLUMN IF EXISTS embedding" in connection.statements[-2]
    assert "002_remove_postgres_embeddings" in connection.statements[-2]
    assert "'knowledge_chunks'" in connection.statements[-1]
    assert "'candidate_assurance_bundles'" in connection.statements[-1]
    assert "pg_extension" not in connection.statements[-1]


@pytest.mark.parametrize(
    "finding",
    [
        ("column", "knowledge_chunks", "embedding", "_float8"),
        ("column", "evidence_edges", "coordinates", "vector"),
    ],
)
def test_postgresql_setup_rejects_vector_state_in_neo_owned_tables(monkeypatch, finding) -> None:
    connection = FakePostgresConnection([finding])
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect", lambda database_url: connection
    )

    with pytest.raises(PersistenceSchemaError, match="vector|embedding"):
        PostgresRunRepository("postgresql://configured").setup()


@pytest.mark.parametrize(
    "finding",
    [
        ("column", "rag_documents", "embedding", "vector"),
        ("column", "feature_store", "coordinates", "vector"),
        ("extension", "", "", "vector"),
    ],
)
def test_postgresql_setup_ignores_vector_state_owned_by_other_apps(monkeypatch, finding) -> None:
    connection = FakePostgresConnection([finding])
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect", lambda database_url: connection
    )

    PostgresRunRepository("postgresql://configured").setup()
