from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from neo_sf_q_intel.domain import AssuranceRun
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.postgres_schema import (
    DEFAULT_POSTGRES_SCHEMA,
    initialize_postgres_schema,
    scoped_connection_string,
)
from neo_sf_q_intel.temporal import aware_utc

if TYPE_CHECKING:
    from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle


class CandidatePersistenceConflictError(RuntimeError):
    """Stored candidate evidence differs from an exact idempotent retry."""


def _canonical_document(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _strict_json_loads(document: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(document, object_pairs_hook=reject_duplicates)


def _bundle_payload(
    bundle: CandidateAssuranceBundle,
) -> tuple[str, tuple[AssuranceRun, ...]]:
    from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle

    supplied_document = _canonical_document(bundle)
    try:
        validated = CandidateAssuranceBundle.model_validate_json(supplied_document)
    except Exception as exc:
        raise CandidatePersistenceConflictError(
            "Candidate bundle fails canonical contract validation"
        ) from exc
    document = _canonical_document(validated)
    if document != supplied_document:
        raise CandidatePersistenceConflictError(
            "Candidate bundle changes during canonical round-trip validation"
        )
    body = json.loads(document)
    declared_digest = body.pop("bundle_sha256", None)
    if declared_digest != validated.bundle_sha256 or stable_sha256(body) != validated.bundle_sha256:
        raise CandidatePersistenceConflictError("Candidate bundle digest is not exact")
    runs = tuple(analysis.run for analysis in validated.analyses)
    for run in runs:
        aware_utc(run.created_at)
    run_ids = tuple(run.run_id for run in runs)
    if len(set(run_ids)) != len(run_ids):
        raise CandidatePersistenceConflictError("Candidate bundle repeats a component run")
    return document, runs


def _candidate_bundle_documents_equal(stored: str | dict, expected: str) -> bool:
    from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle

    try:
        document = _canonical_document(
            _strict_json_loads(stored) if isinstance(stored, str) else stored
        )
        CandidateAssuranceBundle.model_validate_json(document)
        return document == expected
    except Exception:
        return False


def _documents_equal(stored: str | dict, expected: str) -> bool:
    try:
        return (
            _canonical_document(json.loads(stored) if isinstance(stored, str) else stored)
            == expected
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _parse_run_document(document: str | dict) -> AssuranceRun:
    body = json.loads(document) if isinstance(document, str) else dict(document)
    if "schema_version" not in body:
        body["schema_version"] = "1.0.0"
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
            body.setdefault(field, None)
    return AssuranceRun.model_validate(body)


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
CREATE TABLE IF NOT EXISTS candidate_assurance_bundles (
    bundle_sha256 text PRIMARY KEY,
    bundle_document text NOT NULL,
    component_run_ids jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS candidate_assurance_component_runs (
    run_id uuid PRIMARY KEY REFERENCES assurance_runs(run_id) ON DELETE RESTRICT,
    bundle_sha256 text NOT NULL REFERENCES candidate_assurance_bundles(bundle_sha256)
        ON DELETE RESTRICT
);
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
  AND table_name IN (
    'assurance_runs',
    'candidate_assurance_bundles',
    'evidence_edges',
    'knowledge_chunks',
    'tool_audit'
  )
  AND (
    column_name ILIKE '%embedding%'
    OR column_name ILIKE '%vector%'
    OR udt_name IN ('vector', 'halfvec', 'sparsevec')
  )
"""

POSTGRES_VECTOR_UDTS = frozenset({"vector", "halfvec", "sparsevec"})
NEO_OWNED_POSTGRES_TABLES = frozenset(
    {
        "assurance_runs",
        "candidate_assurance_bundles",
        "candidate_assurance_component_runs",
        "evidence_edges",
        "knowledge_chunks",
        "tool_audit",
    }
)
POSTGRES_VECTOR_PAYLOAD_UDTS = frozenset(
    {"_float4", "_float8", "_numeric", "json", "jsonb", "bytea", "text", "varchar"}
)


def _forbidden_vector_state(candidates: list[tuple[str, str, str, str]]) -> list[str]:
    forbidden: list[str] = []
    for finding_kind, table_name, column_name, udt_name in candidates:
        if finding_kind != "column" or table_name not in NEO_OWNED_POSTGRES_TABLES:
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

    def save_candidate_bundle(self, bundle: CandidateAssuranceBundle) -> None: ...


class InMemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, AssuranceRun] = {}
        self.candidate_bundles: dict[str, str] = {}
        self.candidate_component_runs: dict[UUID, str] = {}
        self._lock = RLock()

    def save(self, run: AssuranceRun) -> None:
        aware_utc(run.created_at)
        with self._lock:
            bundle_sha256 = self.candidate_component_runs.get(run.run_id)
            if bundle_sha256 is not None:
                stored = self.runs.get(run.run_id)
                if stored is None or _canonical_document(stored) != _canonical_document(run):
                    raise CandidatePersistenceConflictError(
                        "Candidate component run is immutable after bundle persistence"
                    )
                return
            self.runs[run.run_id] = run.model_copy(deep=True)

    def get(self, run_id: UUID) -> AssuranceRun | None:
        with self._lock:
            run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        with self._lock:
            ordered = sorted(
                self.runs.values(), key=lambda row: aware_utc(row.created_at), reverse=True
            )
        return [row.model_copy(deep=True) for row in ordered[:limit]]

    def save_candidate_bundle(self, bundle: CandidateAssuranceBundle) -> None:
        document, runs = _bundle_payload(bundle)
        with self._lock:
            existing_bundle = self.candidate_bundles.get(bundle.bundle_sha256)
            if existing_bundle is not None and not _candidate_bundle_documents_equal(
                existing_bundle, document
            ):
                raise CandidatePersistenceConflictError(
                    "Candidate bundle digest is already bound to different evidence"
                )
            for run in runs:
                stored = self.runs.get(run.run_id)
                if stored is not None and _canonical_document(stored) != _canonical_document(run):
                    raise CandidatePersistenceConflictError(
                        "Component run ID is already bound to different evidence"
                    )
            staged_runs = dict(self.runs)
            staged_bundles = dict(self.candidate_bundles)
            staged_components = dict(self.candidate_component_runs)
            for run in runs:
                staged_runs[run.run_id] = run.model_copy(deep=True)
                existing_link = staged_components.get(run.run_id)
                if existing_link not in (None, bundle.bundle_sha256):
                    raise CandidatePersistenceConflictError(
                        "Component run is already linked to another candidate bundle"
                    )
                staged_components[run.run_id] = bundle.bundle_sha256
            staged_bundles[bundle.bundle_sha256] = document
            self.runs = staged_runs
            self.candidate_bundles = staged_bundles
            self.candidate_component_runs = staged_components


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
CREATE TABLE IF NOT EXISTS candidate_assurance_bundles (
    bundle_sha256 TEXT PRIMARY KEY,
    bundle_document TEXT NOT NULL,
    component_run_ids TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS candidate_assurance_component_runs (
    run_id TEXT PRIMARY KEY REFERENCES assurance_runs(run_id) ON DELETE RESTRICT,
    bundle_sha256 TEXT NOT NULL REFERENCES candidate_assurance_bundles(bundle_sha256)
        ON DELETE RESTRICT
);
"""


class SQLiteRunRepository:
    """Auto-creating durable fallback when PostgreSQL is unavailable."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SQLITE_SCHEMA_SQL)

    def save(self, run: AssuranceRun) -> None:
        aware_utc(run.created_at)
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
        expected_run = _canonical_document(run)
        with sqlite3.connect(self.path, isolation_level=None) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            linked = connection.execute(
                """
                SELECT c.bundle_sha256, r.run_document
                FROM candidate_assurance_component_runs c
                LEFT JOIN assurance_runs r ON r.run_id = c.run_id
                WHERE c.run_id = ?
                """,
                (str(run.run_id),),
            ).fetchone()
            if linked is not None:
                if linked[1] is None or not _documents_equal(linked[1], expected_run):
                    raise CandidatePersistenceConflictError(
                        "Candidate component run is immutable after bundle persistence"
                    )
                connection.commit()
                return
            connection.execute(
                statement,
                (
                    str(run.run_id),
                    str(run.trace_id),
                    run.request.project_id,
                    run.status,
                    run.decision.code if run.decision else None,
                    expected_run,
                    aware_utc(run.created_at).isoformat(),
                ),
            )

    def save_candidate_bundle(self, bundle: CandidateAssuranceBundle) -> None:
        """Persist a bundle and its runs in one exact, append-safe transaction."""

        document, runs = _bundle_payload(bundle)
        component_run_ids = _canonical_document([str(run.run_id) for run in runs])
        with sqlite3.connect(self.path, isolation_level=None) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_bundle = connection.execute(
                    """
                    SELECT bundle_document, component_run_ids
                    FROM candidate_assurance_bundles WHERE bundle_sha256 = ?
                    """,
                    (bundle.bundle_sha256,),
                ).fetchone()
                if existing_bundle is not None:
                    if (
                        not _candidate_bundle_documents_equal(existing_bundle[0], document)
                        or existing_bundle[1] != component_run_ids
                    ):
                        raise CandidatePersistenceConflictError(
                            "Candidate bundle digest is already bound to different evidence"
                        )
                    for run in runs:
                        row = connection.execute(
                            "SELECT run_document FROM assurance_runs WHERE run_id = ?",
                            (str(run.run_id),),
                        ).fetchone()
                        if row is None or not _documents_equal(row[0], _canonical_document(run)):
                            raise CandidatePersistenceConflictError(
                                "Persisted candidate bundle has a missing or conflicting run"
                            )
                        link = connection.execute(
                            """
                            SELECT bundle_sha256
                            FROM candidate_assurance_component_runs WHERE run_id = ?
                            """,
                            (str(run.run_id),),
                        ).fetchone()
                        if link != (bundle.bundle_sha256,):
                            raise CandidatePersistenceConflictError(
                                "Persisted candidate bundle has a missing component link"
                            )
                    connection.commit()
                    return

                for run in runs:
                    expected_run = _canonical_document(run)
                    row = connection.execute(
                        "SELECT run_document FROM assurance_runs WHERE run_id = ?",
                        (str(run.run_id),),
                    ).fetchone()
                    if row is not None:
                        if not _documents_equal(row[0], expected_run):
                            raise CandidatePersistenceConflictError(
                                "Component run ID is already bound to different evidence"
                            )
                        continue
                    connection.execute(
                        """
                        INSERT INTO assurance_runs (
                            run_id, trace_id, project_id, status, decision_code,
                            run_document, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(run.run_id),
                            str(run.trace_id),
                            run.request.project_id,
                            run.status,
                            run.decision.code if run.decision else None,
                            expected_run,
                            aware_utc(run.created_at).isoformat(),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO candidate_assurance_bundles (
                        bundle_sha256, bundle_document, component_run_ids
                    ) VALUES (?, ?, ?)
                    """,
                    (bundle.bundle_sha256, document, component_run_ids),
                )
                for run in runs:
                    connection.execute(
                        """
                        INSERT INTO candidate_assurance_component_runs (
                            run_id, bundle_sha256
                        ) VALUES (?, ?)
                        """,
                        (str(run.run_id), bundle.bundle_sha256),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def get(self, run_id: UUID) -> AssuranceRun | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT run_document FROM assurance_runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return _parse_run_document(row[0]) if row else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT run_document FROM assurance_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_parse_run_document(row[0]) for row in rows]


class PostgresRunRepository:
    def __init__(self, database_url: str, *, schema: str = DEFAULT_POSTGRES_SCHEMA) -> None:
        self.database_url = database_url
        self.schema = schema
        self.connection_string = scoped_connection_string(database_url, schema)

    def setup(self) -> None:
        """Create/migrate the schema and reject PostgreSQL-hosted vector state."""
        with psycopg.connect(self.database_url) as connection:
            initialize_postgres_schema(connection, self.schema)
            connection.execute(SCHEMA_SQL)
            candidates = connection.execute(VECTOR_COLUMN_CHECK_SQL).fetchall()
            forbidden = _forbidden_vector_state(candidates)
            if forbidden:
                raise PersistenceSchemaError(
                    "PostgreSQL contains forbidden persistent vector state: " + ", ".join(forbidden)
                )

    def save(self, run: AssuranceRun) -> None:
        aware_utc(run.created_at)
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
        expected_run = _canonical_document(run)
        with psycopg.connect(self.connection_string) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(run.run_id),),
            )
            linked = connection.execute(
                """
                SELECT c.bundle_sha256, r.run_document
                FROM candidate_assurance_component_runs c
                LEFT JOIN assurance_runs r ON r.run_id = c.run_id
                WHERE c.run_id = %s FOR SHARE OF c
                """,
                (run.run_id,),
            ).fetchone()
            if linked is not None:
                if linked[1] is None or not _documents_equal(linked[1], expected_run):
                    raise CandidatePersistenceConflictError(
                        "Candidate component run is immutable after bundle persistence"
                    )
                return
            connection.execute(
                statement,
                (
                    run.run_id,
                    run.trace_id,
                    run.request.project_id,
                    run.status,
                    run.decision.code if run.decision else None,
                    expected_run,
                    aware_utc(run.created_at),
                ),
            )

    def save_candidate_bundle(self, bundle: CandidateAssuranceBundle) -> None:
        """Persist exact bundle evidence with all component runs atomically."""

        document, runs = _bundle_payload(bundle)
        component_run_ids = [str(run.run_id) for run in runs]
        run_statement = """
        INSERT INTO assurance_runs (
            run_id, trace_id, project_id, status, decision_code, run_document, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (run_id) DO NOTHING
        """
        bundle_statement = """
        INSERT INTO candidate_assurance_bundles (
            bundle_sha256, bundle_document, component_run_ids
        ) VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (bundle_sha256) DO NOTHING
        """
        with psycopg.connect(self.connection_string) as connection:
            for run_id in sorted(str(run.run_id) for run in runs):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (run_id,),
                )
            existing_bundle = connection.execute(
                """
                SELECT bundle_document, component_run_ids
                FROM candidate_assurance_bundles
                WHERE bundle_sha256 = %s FOR UPDATE
                """,
                (bundle.bundle_sha256,),
            ).fetchone()
            if existing_bundle is not None:
                stored_run_ids = existing_bundle[1]
                if isinstance(stored_run_ids, str):
                    stored_run_ids = json.loads(stored_run_ids)
                if (
                    not _candidate_bundle_documents_equal(existing_bundle[0], document)
                    or stored_run_ids != component_run_ids
                ):
                    raise CandidatePersistenceConflictError(
                        "Candidate bundle digest is already bound to different evidence"
                    )
                for run in runs:
                    expected_run = _canonical_document(run)
                    row = connection.execute(
                        "SELECT run_document FROM assurance_runs WHERE run_id = %s FOR UPDATE",
                        (run.run_id,),
                    ).fetchone()
                    if row is None or not _documents_equal(row[0], expected_run):
                        raise CandidatePersistenceConflictError(
                            "Persisted candidate bundle has a missing or conflicting run"
                        )
                    link = connection.execute(
                        """
                        SELECT bundle_sha256
                        FROM candidate_assurance_component_runs
                        WHERE run_id = %s FOR SHARE
                        """,
                        (run.run_id,),
                    ).fetchone()
                    if link != (bundle.bundle_sha256,):
                        raise CandidatePersistenceConflictError(
                            "Persisted candidate bundle has a missing component link"
                        )
                return

            for run in runs:
                expected_run = _canonical_document(run)
                connection.execute(
                    run_statement,
                    (
                        run.run_id,
                        run.trace_id,
                        run.request.project_id,
                        run.status,
                        run.decision.code if run.decision else None,
                        expected_run,
                        aware_utc(run.created_at),
                    ),
                )
                row = connection.execute(
                    "SELECT run_document FROM assurance_runs WHERE run_id = %s FOR UPDATE",
                    (run.run_id,),
                ).fetchone()
                if row is None or not _documents_equal(row[0], expected_run):
                    raise CandidatePersistenceConflictError(
                        "Component run ID is already bound to different evidence"
                    )
            connection.execute(
                bundle_statement,
                (
                    bundle.bundle_sha256,
                    document,
                    _canonical_document(component_run_ids),
                ),
            )
            row = connection.execute(
                """
                SELECT bundle_document, component_run_ids
                FROM candidate_assurance_bundles
                WHERE bundle_sha256 = %s FOR UPDATE
                """,
                (bundle.bundle_sha256,),
            ).fetchone()
            if row is None:
                raise CandidatePersistenceConflictError("Candidate bundle was not persisted")
            stored_run_ids = row[1]
            if isinstance(stored_run_ids, str):
                stored_run_ids = json.loads(stored_run_ids)
            if (
                not _candidate_bundle_documents_equal(row[0], document)
                or stored_run_ids != component_run_ids
            ):
                raise CandidatePersistenceConflictError(
                    "Candidate bundle digest is already bound to different evidence"
                )
            for run in runs:
                connection.execute(
                    """
                    INSERT INTO candidate_assurance_component_runs (
                        run_id, bundle_sha256
                    ) VALUES (%s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (run.run_id, bundle.bundle_sha256),
                )
                link = connection.execute(
                    """
                    SELECT bundle_sha256
                    FROM candidate_assurance_component_runs
                    WHERE run_id = %s FOR SHARE
                    """,
                    (run.run_id,),
                ).fetchone()
                if link != (bundle.bundle_sha256,):
                    raise CandidatePersistenceConflictError(
                        "Component run is already linked to another candidate bundle"
                    )

    def get(self, run_id: UUID) -> AssuranceRun | None:
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT run_document FROM assurance_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        return _parse_run_document(row["run_document"]) if row else None

    def list_recent(self, limit: int = 20) -> list[AssuranceRun]:
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT run_document FROM assurance_runs
                ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_parse_run_document(row["run_document"]) for row in rows]
