from __future__ import annotations

from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from neo_sf_q_intel.domain import AssuranceRun

SCHEMA_SQL = """
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
    embedding double precision[],
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
    ON knowledge_chunks USING gin (search_vector);
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


class PostgresRunRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def setup(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(SCHEMA_SQL)

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
