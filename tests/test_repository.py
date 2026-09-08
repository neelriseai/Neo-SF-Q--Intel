import sqlite3
from pathlib import Path

import pytest

from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest
from neo_sf_q_intel.repository import (
    SCHEMA_SQL,
    VECTOR_COLUMN_CHECK_SQL,
    PersistenceSchemaError,
    PostgresRunRepository,
    SQLiteRunRepository,
)


class FakePostgresConnection:
    def __init__(self, forbidden_columns: list[tuple[str, str, str, str]]) -> None:
        self.forbidden_columns = forbidden_columns
        self.statements: list[str] = []

    def __enter__(self) -> FakePostgresConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str) -> FakePostgresConnection:
        self.statements.append(statement)
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


def test_postgresql_setup_auto_creates_and_applies_vector_boundary_migration(monkeypatch) -> None:
    connection = FakePostgresConnection(
        [("column", "knowledge_chunks", "search_vector", "tsvector")]
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect", lambda database_url: connection
    )

    PostgresRunRepository("postgresql://configured").setup()

    assert connection.statements == [SCHEMA_SQL, VECTOR_COLUMN_CHECK_SQL]
    assert "CREATE TABLE IF NOT EXISTS assurance_runs" in connection.statements[0]
    assert "DROP COLUMN IF EXISTS embedding" in connection.statements[0]
    assert "002_remove_postgres_embeddings" in connection.statements[0]
    assert "table_name = 'knowledge_chunks'" not in connection.statements[1]
    assert "pg_extension" in connection.statements[1]


@pytest.mark.parametrize(
    "finding",
    [
        ("column", "evidence_embeddings", "embedding", "_float8"),
        ("column", "feature_store", "coordinates", "vector"),
        ("extension", "", "", "vector"),
    ],
)
def test_postgresql_setup_rejects_unapproved_vector_state(monkeypatch, finding) -> None:
    connection = FakePostgresConnection([finding])
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect", lambda database_url: connection
    )

    with pytest.raises(PersistenceSchemaError, match="vector|embedding"):
        PostgresRunRepository("postgresql://configured").setup()
