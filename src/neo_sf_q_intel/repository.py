from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from neo_sf_q_intel.domain import AssuranceRun

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS assurance_runs (
    run_id uuid PRIMARY KEY,
    trace_id uuid NOT NULL,
    project_id text NOT NULL,
    status text NOT NULL,
    decision_code text,
    run_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assurance_runs_project_created_idx
    ON assurance_runs (project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    snapshot_id text NOT NULL,
    entity_id text,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
    ON knowledge_chunks USING gin (search_vector);
ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding;
INSERT INTO schema_migrations (version) VALUES ('002_remove_postgres_embeddings')
    ON CONFLICT (version) DO NOTHING;
CREATE TABLE IF NOT EXISTS evidence_edges (
    snapshot_id text NOT NULL,
    source_id text NOT NULL,
    relation text NOT NULL,
    target_id text NOT NULL,
    evidence_state text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, source_id, relation, target_id)
);
CREATE TABLE IF NOT EXISTS tool_audit (
    audit_id bigserial PRIMARY KEY,
    run_id uuid,
    tool_name text NOT NULL,
    outcome text NOT NULL,
    evidence_ids text[] NOT NULL DEFAULT '{}',
    occurred_at timestamptz NOT NULL DEFAULT now()
);
"""

VECTOR_COLUMN_CHECK_SQL = """
SELECT 'column' AS finding_kind, table_name, column_name, udt_name
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND (
    column_name ILIKE '%embedding%'
    OR column_name ILIKE '%vector%'
    OR udt_name IN ('vector', 'halfvec', 'sparsevec')
  )
UNION ALL
SELECT 'extension' AS finding_kind, '' AS table_name, '' AS column_name, extname AS udt_name
FROM pg_extension
WHERE extname = 'vector'
"""

POSTGRES_VECTOR_UDTS = frozenset({"vector", "halfvec", "sparsevec"})
POSTGRES_VECTOR_PAYLOAD_UDTS = frozenset(
    {"_float4", "_float8", "_numeric", "json", "jsonb", "bytea", "text", "varchar"}
)


def _forbidden_vector_state(candidates: list[tuple[str, str, str, str]]) -> list[str]:
    forbidden: list[str] = []
    for finding_kind, table_name, column_name, udt_name in candidates:
        if finding_kind == "extension":
            forbidden.append(f"extension {udt_name}")
            continue
        lowered_name = column_name.casefold()
        lowered_udt = udt_name.casefold()
        named_vector_payload = (
            "embedding" in lowered_name or "vector" in lowered_name
        ) and lowered_udt in POSTGRES_VECTOR_PAYLOAD_UDTS
        if lowered_udt in POSTGRES_VECTOR_UDTS or named_vector_payload:
            forbidden.append(f"{table_name}.{column_name} ({udt_name})")
    return forbidden


class PersistenceSchemaError(RuntimeError):
    """Raised when durable storage violates the configured persistence boundary."""


class RunRepository(Protocol):
    def save(self, run: AssuranceRun) -> None: ...

    def get(self, run_id: UUID) -> AssuranceRun | None: ...

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]: ...


class InMemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, AssuranceRun] = {}

    def save(self, run: AssuranceRun) -> None:
        self.runs[run.run_id] = run.model_copy(deep=True)

    def get(self, run_id: UUID) -> AssuranceRun | None:
        run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        ordered = sorted(self.runs.values(), key=lambda row: row.created_at, reverse=True)
        return [row.model_copy(deep=True) for row in ordered[:limit]]


SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assurance_runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_code TEXT,
    run_document TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS assurance_runs_project_created_idx
    ON assurance_runs (project_id, created_at DESC);
"""


class SQLiteRunRepository:
    """Auto-creating durable fallback when PostgreSQL is unavailable."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(SQLITE_SCHEMA_SQL)

    def save(self, run: AssuranceRun) -> None:
        statement = """
        INSERT INTO assurance_runs (
            run_id, trace_id, project_id, status, decision_code, run_document, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            status = excluded.status,
            decision_code = excluded.decision_code,
            run_document = excluded.run_document,
            updated_at = CURRENT_TIMESTAMP
        """
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                statement,
                (
                    str(run.run_id),
                    str(run.trace_id),
                    run.request.project_id,
                    run.status,
                    run.decision.code if run.decision else None,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )

    def get(self, run_id: UUID) -> AssuranceRun | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT run_document FROM assurance_runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return AssuranceRun.model_validate_json(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT run_document FROM assurance_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [AssuranceRun.model_validate_json(row[0]) for row in rows]


class PostgresRunRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def setup(self) -> None:
        """Create/migrate the schema and reject PostgreSQL-hosted vector state."""
        with psycopg.connect(self.database_url) as connection:
            connection.execute(SCHEMA_SQL)
            candidates = connection.execute(VECTOR_COLUMN_CHECK_SQL).fetchall()
            forbidden = _forbidden_vector_state(candidates)
            if forbidden:
                raise PersistenceSchemaError(
                    "PostgreSQL contains forbidden persistent vector state: " + ", ".join(forbidden)
                )

    def save(self, run: AssuranceRun) -> None:
        statement = """
        INSERT INTO assurance_runs (
            run_id, trace_id, project_id, status, decision_code, run_document, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            decision_code = EXCLUDED.decision_code,
            run_document = EXCLUDED.run_document,
            updated_at = now()
        """
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                statement,
                (
                    run.run_id,
                    run.trace_id,
                    run.request.project_id,
                    run.status,
                    run.decision.code if run.decision else None,
                    run.model_dump_json(),
                    run.created_at,
                ),
            )

    def get(self, run_id: UUID) -> AssuranceRun | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT run_document FROM assurance_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        return AssuranceRun.model_validate(row["run_document"]) if row else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT run_document FROM assurance_runs
                ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [AssuranceRun.model_validate(row["run_document"]) for row in rows]
