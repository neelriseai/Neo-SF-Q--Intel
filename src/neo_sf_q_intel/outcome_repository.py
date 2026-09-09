"""Append-only persistence adapters for non-authorizing outcome memory.

This module deliberately owns storage mechanics only.  It does not choose a
fallback backend, authorize historical claims, or turn an outcome candidate
into current release evidence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from neo_sf_q_intel.domain import AssuranceRun
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.outcomes import (
    OutcomeKind,
    OutcomeMemoryError,
    OutcomeRecord,
    load_outcome_evaluation_contract,
    load_outcome_policy,
    validate_outcome_record,
)
from neo_sf_q_intel.safety import (
    SensitiveTextError,
    contains_sensitive_text,
    require_no_sensitive_text,
)

MAX_QUERY_LIMIT = 100
MAX_SCOPE_CHARACTERS = 256
MAX_IDEMPOTENCY_KEY_CHARACTERS = 256
MAX_CURSOR_CHARACTERS = 4096
MAX_RECORD_BYTES = 1_048_576

_OUTCOME_ID = re.compile(r"^outcome:([a-f0-9]{64})$")
_RECORD_FILE = re.compile(r"^outcome-([a-f0-9]{64})\.json$")


class OutcomeRepositoryError(RuntimeError):
    """Base exception for outcome persistence failures."""


class OutcomeConflictError(OutcomeRepositoryError):
    """Raised when an immutable identity is reused for different content."""


class OutcomeCorruptionError(OutcomeRepositoryError):
    """Raised when persisted content fails strict replay validation."""


class OutcomeQueryError(OutcomeRepositoryError):
    """Raised when a query or cursor is invalid or outside configured bounds."""


class OutcomeReplayError(OutcomeRepositoryError):
    """Raised when canonical domain replay rejects an append request."""


class OutcomePredecessorError(OutcomeRepositoryError):
    """Raised when incident history is absent or differs from durable history."""


@dataclass(frozen=True, slots=True)
class OutcomePage:
    records: tuple[OutcomeRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class OutcomeAppendRequest:
    """Exact authority inputs required for canonical replay before persistence."""

    record: OutcomeRecord
    originating_run: AssuranceRun
    predecessor: OutcomeRecord | None = None
    predecessor_chain: tuple[OutcomeRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _ReplayedAppend:
    record: OutcomeRecord
    document: str
    predecessors: tuple[OutcomeRecord, ...]


class OutcomeRepository(Protocol):
    """Project-scoped append/query port; mutation and global enumeration are absent."""

    def append(self, request: OutcomeAppendRequest, idempotency_key: str) -> OutcomeRecord: ...

    def get(self, project_id: str, outcome_id: str) -> OutcomeRecord | None: ...

    def query(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage: ...


@dataclass(frozen=True, slots=True)
class _Query:
    project_id: str
    kinds: tuple[str, ...]
    source_snapshot: str | None
    limit: int
    after_recorded_at: str | None
    after_outcome_id: str | None
    scope_sha256: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OutcomeCorruptionError("Persisted outcome contains a duplicate JSON key")
        result[key] = value
    return result


def _validated_record(record: OutcomeRecord) -> tuple[OutcomeRecord, str]:
    try:
        body = record.model_dump(mode="json")
        require_no_sensitive_text(body)
        validated = OutcomeRecord.model_validate(body)
    except (AttributeError, SensitiveTextError, TypeError, ValidationError, ValueError) as exc:
        raise OutcomeCorruptionError("Outcome record failed immutable contract validation") from exc
    document = _canonical_json(validated.model_dump(mode="json"))
    if len(document) > MAX_RECORD_BYTES:
        raise OutcomeCorruptionError("Outcome record exceeds the persistence byte limit")
    return validated.model_copy(deep=True), document.decode("utf-8")


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _replay_append(
    request: OutcomeAppendRequest, clock: Callable[[], datetime]
) -> _ReplayedAppend:
    if not isinstance(request, OutcomeAppendRequest):
        raise OutcomeReplayError("Outcome append requires an authority replay request")
    try:
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Repository clock must return an aware datetime")
        policy = load_outcome_policy()
        evaluation = load_outcome_evaluation_contract(
            expected_sha256=policy.evaluation_set_sha256
        )
        if not isinstance(request.predecessor_chain, tuple):
            raise TypeError("Predecessor chain must be an immutable tuple")
        if len(request.predecessor_chain) > policy.limits.maximum_incident_chain_depth:
            raise ValueError("Predecessor chain exceeds the policy depth bound")
        run = AssuranceRun.model_validate(request.originating_run.model_dump(mode="json"))
        record = OutcomeRecord.model_validate(request.record.model_dump(mode="json"))
        predecessor = (
            OutcomeRecord.model_validate(request.predecessor.model_dump(mode="json"))
            if request.predecessor is not None
            else None
        )
        predecessor_chain = tuple(
            OutcomeRecord.model_validate(item.model_dump(mode="json"))
            for item in request.predecessor_chain
        )
        governance_policy = GovernancePolicy.load() if run.governance is not None else None
        validated = validate_outcome_record(
            record,
            run,
            policy,
            evaluation,
            governance_policy=governance_policy,
            predecessor=predecessor,
            predecessor_chain=predecessor_chain,
            evaluated_at=now.astimezone(UTC),
        )
        validated_record, document = _validated_record(validated)
        persisted_predecessors = predecessor_chain + (
            (predecessor,) if predecessor is not None else ()
        )
        return _ReplayedAppend(
            record=validated_record,
            document=document,
            predecessors=persisted_predecessors,
        )
    except OutcomeReplayError:
        raise
    except (
        AttributeError,
        OSError,
        OutcomeMemoryError,
        SensitiveTextError,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise OutcomeReplayError("Outcome append failed canonical authority replay") from exc


def replay_outcome_request(
    request: OutcomeAppendRequest,
    *,
    clock: Callable[[], datetime] | None = None,
) -> OutcomeRecord:
    """Replay an append envelope without performing persistence or idempotency effects."""

    return _replay_append(request, clock or _system_clock).record


def _require_persisted_predecessors(
    predecessors: tuple[OutcomeRecord, ...],
    lookup: Callable[[str, str], OutcomeRecord | None],
) -> None:
    for expected in predecessors:
        stored = lookup(expected.lineage.project_id, expected.outcome_id)
        if stored is None:
            raise OutcomePredecessorError(
                "Incident predecessor is absent from the active outcome repository"
            )
        if stored != expected:
            raise OutcomePredecessorError(
                "Incident predecessor differs from durable outcome history"
            )


def _parse_record(document: str | bytes | Mapping[str, Any]) -> tuple[OutcomeRecord, str]:
    try:
        if isinstance(document, Mapping):
            body: Any = dict(document)
        else:
            raw = document.encode("utf-8") if isinstance(document, str) else document
            if len(raw) > MAX_RECORD_BYTES:
                raise OutcomeCorruptionError("Persisted outcome exceeds the byte limit")
            body = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(body, dict):
            raise OutcomeCorruptionError("Persisted outcome is not a JSON object")
        record = OutcomeRecord.model_validate(body)
    except OutcomeCorruptionError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise OutcomeCorruptionError(
            "Persisted outcome failed immutable contract validation"
        ) from exc
    return _validated_record(record)


def _validate_scope(value: str, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_SCOPE_CHARACTERS:
        raise OutcomeQueryError(f"{label} is required and must be within configured bounds")
    if any(ord(character) < 32 for character in value) or contains_sensitive_text(value):
        raise OutcomeQueryError(f"{label} contains unsafe text")
    return value


def _idempotency_digest(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_IDEMPOTENCY_KEY_CHARACTERS:
        raise OutcomeQueryError("Idempotency key is required and must be within configured bounds")
    if any(ord(character) < 32 for character in value) or contains_sensitive_text(value):
        raise OutcomeQueryError("Idempotency key contains unsafe text")
    return _sha256(value)


def _outcome_digest(outcome_id: str) -> str:
    match = _OUTCOME_ID.fullmatch(outcome_id)
    if not match:
        raise OutcomeQueryError("Outcome ID is invalid")
    return match.group(1)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _query_scope_sha256(
    project_id: str, kinds: tuple[str, ...], source_snapshot: str | None
) -> str:
    return _sha256(
        _canonical_json(
            {"project_id": project_id, "kinds": kinds, "source_snapshot": source_snapshot}
        )
    )


def _make_cursor(query: _Query, record: OutcomeRecord) -> str:
    body = {
        "outcome_id": record.outcome_id,
        "recorded_at": _utc_text(record.recorded_at),
        "scope_sha256": query.scope_sha256,
        "version": 1,
    }
    encoded = base64.urlsafe_b64encode(_canonical_json(body)).decode("ascii").rstrip("=")
    if len(encoded) > MAX_CURSOR_CHARACTERS:
        raise OutcomeCorruptionError("Generated outcome cursor exceeds the configured bound")
    return encoded


def _decode_cursor(cursor: str, expected_scope_sha256: str) -> tuple[str, str]:
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_CURSOR_CHARACTERS:
        raise OutcomeQueryError("Outcome cursor is invalid or exceeds configured bounds")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        body = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OutcomeCorruptionError,
    ) as exc:
        raise OutcomeQueryError("Outcome cursor is invalid") from exc
    if not isinstance(body, dict) or set(body) != {
        "outcome_id",
        "recorded_at",
        "scope_sha256",
        "version",
    }:
        raise OutcomeQueryError("Outcome cursor has an invalid contract")
    if body["version"] != 1 or body["scope_sha256"] != expected_scope_sha256:
        raise OutcomeQueryError("Outcome cursor does not belong to this query scope")
    _outcome_digest(body["outcome_id"])
    try:
        parsed = datetime.fromisoformat(body["recorded_at"])
    except (TypeError, ValueError) as exc:
        raise OutcomeQueryError("Outcome cursor timestamp is invalid") from exc
    if parsed.tzinfo is None or _utc_text(parsed) != body["recorded_at"]:
        raise OutcomeQueryError("Outcome cursor timestamp is not canonical")
    return body["recorded_at"], body["outcome_id"]


def _normalize_query(
    *,
    project_id: str,
    kinds: Iterable[OutcomeKind | str] | None,
    source_snapshot: str | None,
    cursor: str | None,
    limit: int,
) -> _Query:
    project = _validate_scope(project_id, "project_id")
    snapshot = (
        _validate_scope(source_snapshot, "source_snapshot")
        if source_snapshot is not None
        else None
    )
    try:
        supplied_kinds: list[OutcomeKind | str] = []
        if kinds is not None:
            iterator = iter(kinds)
            for _ in range(len(OutcomeKind) + 1):
                try:
                    supplied_kinds.append(next(iterator))
                except StopIteration:
                    break
        if len(supplied_kinds) > len(OutcomeKind):
            raise OutcomeQueryError("Outcome kinds exceed the configured bound")
        normalized_kinds = tuple(sorted({OutcomeKind(item).value for item in supplied_kinds}))
    except OutcomeQueryError:
        raise
    except (TypeError, ValueError) as exc:
        raise OutcomeQueryError("Outcome kinds contain an unsupported value") from exc
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise OutcomeQueryError("Outcome query limit is outside configured bounds")
    scope_sha = _query_scope_sha256(project, normalized_kinds, snapshot)
    after_time: str | None = None
    after_id: str | None = None
    if cursor is not None:
        after_time, after_id = _decode_cursor(cursor, scope_sha)
    return _Query(
        project_id=project,
        kinds=normalized_kinds,
        source_snapshot=snapshot,
        limit=limit,
        after_recorded_at=after_time,
        after_outcome_id=after_id,
        scope_sha256=scope_sha,
    )


def _record_matches(record: OutcomeRecord, query: _Query) -> bool:
    if record.lineage.project_id != query.project_id:
        return False
    if query.kinds and record.payload.kind.value not in query.kinds:
        return False
    if (
        query.source_snapshot is not None
        and record.lineage.source_snapshot != query.source_snapshot
    ):
        return False
    key = (_utc_text(record.recorded_at), record.outcome_id)
    return not (
        query.after_recorded_at is not None
        and key >= (query.after_recorded_at, query.after_outcome_id or "")
    )


def _page(records: Iterable[OutcomeRecord], query: _Query) -> OutcomePage:
    ordered = sorted(
        records,
        key=lambda row: (_utc_text(row.recorded_at), row.outcome_id),
        reverse=True,
    )
    selected = ordered[: query.limit + 1]
    visible = selected[: query.limit]
    next_cursor = _make_cursor(query, visible[-1]) if len(selected) > query.limit else None
    return OutcomePage(
        records=tuple(record.model_copy(deep=True) for record in visible),
        next_cursor=next_cursor,
    )


def _validate_normalized_row(row: Mapping[str, Any]) -> OutcomeRecord:
    record, canonical = _parse_record(row["record_document"])
    expected = {
        "project_id": record.lineage.project_id,
        "source_snapshot": record.lineage.source_snapshot,
        "outcome_kind": record.payload.kind.value,
        "outcome_id": record.outcome_id,
        "outcome_sha256": record.outcome_sha256,
        "recorded_at_utc": _utc_text(record.recorded_at),
    }
    if any(str(row[key]) != value for key, value in expected.items()):
        raise OutcomeCorruptionError("Persisted outcome scope columns do not match its record")
    if isinstance(row["record_document"], str) and row["record_document"] != canonical:
        raise OutcomeCorruptionError("Persisted outcome document is not canonical JSON")
    return record


class InMemoryOutcomeRepository:
    """Declared process-cache adapter with defensive copies and append-only semantics."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._records: dict[tuple[str, str], OutcomeRecord] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = threading.RLock()
        self._clock = clock or _system_clock

    def append(self, request: OutcomeAppendRequest, idempotency_key: str) -> OutcomeRecord:
        replayed = _replay_append(request, self._clock)
        validated = replayed.record
        project = _validate_scope(validated.lineage.project_id, "project_id")
        key_digest = _idempotency_digest(idempotency_key)
        with self._lock:
            _require_persisted_predecessors(
                replayed.predecessors,
                lambda predecessor_project, outcome_id: self._records.get(
                    (predecessor_project, outcome_id)
                ),
            )
            receipt = self._idempotency.get((project, key_digest))
            if receipt is not None:
                if receipt != (validated.outcome_id, validated.outcome_sha256):
                    raise OutcomeConflictError("Idempotency key is bound to another outcome")
                existing = self._records.get((project, receipt[0]))
                if existing is None:
                    raise OutcomeCorruptionError("Idempotency receipt references a missing outcome")
                return _validated_record(existing)[0]
            existing = self._records.get((project, validated.outcome_id))
            if existing is not None and existing != validated:
                raise OutcomeConflictError("Outcome identity is bound to different content")
            self._records[(project, validated.outcome_id)] = validated.model_copy(deep=True)
            self._idempotency[(project, key_digest)] = (
                validated.outcome_id,
                validated.outcome_sha256,
            )
            return validated.model_copy(deep=True)

    def get(self, project_id: str, outcome_id: str) -> OutcomeRecord | None:
        project = _validate_scope(project_id, "project_id")
        _outcome_digest(outcome_id)
        with self._lock:
            record = self._records.get((project, outcome_id))
            return _validated_record(record)[0] if record is not None else None

    def query(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage:
        query = _normalize_query(
            project_id=project_id,
            kinds=kinds,
            source_snapshot=source_snapshot,
            cursor=cursor,
            limit=limit,
        )
        with self._lock:
            records = [
                _validated_record(record)[0]
                for (project, _), record in self._records.items()
                if project == query.project_id
            ]
        return _page((record for record in records if _record_matches(record, query)), query)


SQLITE_OUTCOME_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outcome_records (
    project_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    outcome_sha256 TEXT NOT NULL,
    outcome_kind TEXT NOT NULL,
    source_snapshot TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    record_document TEXT NOT NULL,
    PRIMARY KEY (project_id, outcome_id),
    UNIQUE (project_id, outcome_sha256)
);
CREATE TABLE IF NOT EXISTS outcome_idempotency (
    project_id TEXT NOT NULL,
    key_sha256 TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    outcome_sha256 TEXT NOT NULL,
    PRIMARY KEY (project_id, key_sha256),
    FOREIGN KEY (project_id, outcome_id)
        REFERENCES outcome_records(project_id, outcome_id)
);
CREATE INDEX IF NOT EXISTS outcome_records_project_time_idx
    ON outcome_records(project_id, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_kind_time_idx
    ON outcome_records(project_id, outcome_kind, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_snapshot_time_idx
    ON outcome_records(project_id, source_snapshot, recorded_at_utc DESC, outcome_id DESC);
CREATE TRIGGER IF NOT EXISTS outcome_records_no_update
BEFORE UPDATE ON outcome_records BEGIN SELECT RAISE(ABORT, 'outcome memory is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcome_records_no_delete
BEFORE DELETE ON outcome_records BEGIN SELECT RAISE(ABORT, 'outcome memory is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcome_idempotency_no_update
BEFORE UPDATE ON outcome_idempotency BEGIN
    SELECT RAISE(ABORT, 'outcome memory is append-only');
END;
CREATE TRIGGER IF NOT EXISTS outcome_idempotency_no_delete
BEFORE DELETE ON outcome_idempotency BEGIN
    SELECT RAISE(ABORT, 'outcome memory is append-only');
END;
"""


class SQLiteOutcomeRepository:
    """Self-creating SQLite adapter with database-enforced append-only rows."""

    def __init__(
        self, path: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.path = Path(path)
        self._clock = clock or _system_clock
        self.setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            connection.close()
            raise
        return connection

    def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(SQLITE_OUTCOME_SCHEMA_SQL)

    def append(self, request: OutcomeAppendRequest, idempotency_key: str) -> OutcomeRecord:
        replayed = _replay_append(request, self._clock)
        validated = replayed.record
        document = replayed.document
        project = _validate_scope(validated.lineage.project_id, "project_id")
        key_digest = _idempotency_digest(idempotency_key)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")

            def lookup(predecessor_project: str, outcome_id: str) -> OutcomeRecord | None:
                row = connection.execute(
                    """SELECT * FROM outcome_records
                       WHERE project_id = ? AND outcome_id = ?""",
                    (predecessor_project, outcome_id),
                ).fetchone()
                return _validate_normalized_row(row) if row is not None else None

            _require_persisted_predecessors(replayed.predecessors, lookup)
            receipt = connection.execute(
                """SELECT outcome_id, outcome_sha256 FROM outcome_idempotency
                   WHERE project_id = ? AND key_sha256 = ?""",
                (project, key_digest),
            ).fetchone()
            if receipt is not None:
                if (receipt["outcome_id"], receipt["outcome_sha256"]) != (
                    validated.outcome_id,
                    validated.outcome_sha256,
                ):
                    raise OutcomeConflictError("Idempotency key is bound to another outcome")
                row = connection.execute(
                    """SELECT * FROM outcome_records
                       WHERE project_id = ? AND outcome_id = ?""",
                    (project, validated.outcome_id),
                ).fetchone()
                if row is None:
                    raise OutcomeCorruptionError(
                        "Idempotency receipt references a missing outcome"
                    )
                return _validate_normalized_row(row)
            row = connection.execute(
                "SELECT * FROM outcome_records WHERE project_id = ? AND outcome_id = ?",
                (project, validated.outcome_id),
            ).fetchone()
            if row is not None:
                existing = _validate_normalized_row(row)
                if existing != validated:
                    raise OutcomeConflictError(
                        "Outcome identity is bound to different content"
                    )
            else:
                connection.execute(
                    """INSERT INTO outcome_records (
                           project_id, outcome_id, outcome_sha256, outcome_kind,
                           source_snapshot, recorded_at_utc, record_document
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project,
                        validated.outcome_id,
                        validated.outcome_sha256,
                        validated.payload.kind.value,
                        validated.lineage.source_snapshot,
                        _utc_text(validated.recorded_at),
                        document,
                    ),
                )
            connection.execute(
                """INSERT INTO outcome_idempotency (
                       project_id, key_sha256, outcome_id, outcome_sha256
                   ) VALUES (?, ?, ?, ?)""",
                (project, key_digest, validated.outcome_id, validated.outcome_sha256),
            )
        return validated.model_copy(deep=True)

    def get(self, project_id: str, outcome_id: str) -> OutcomeRecord | None:
        project = _validate_scope(project_id, "project_id")
        _outcome_digest(outcome_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM outcome_records WHERE project_id = ? AND outcome_id = ?",
                (project, outcome_id),
            ).fetchone()
        return _validate_normalized_row(row) if row is not None else None

    def query(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage:
        query = _normalize_query(
            project_id=project_id,
            kinds=kinds,
            source_snapshot=source_snapshot,
            cursor=cursor,
            limit=limit,
        )
        clauses = ["project_id = ?"]
        parameters: list[Any] = [query.project_id]
        if query.kinds:
            clauses.append(f"outcome_kind IN ({','.join('?' for _ in query.kinds)})")
            parameters.extend(query.kinds)
        if query.source_snapshot is not None:
            clauses.append("source_snapshot = ?")
            parameters.append(query.source_snapshot)
        if query.after_recorded_at is not None:
            clauses.append("(recorded_at_utc < ? OR (recorded_at_utc = ? AND outcome_id < ?))")
            parameters.extend(
                (query.after_recorded_at, query.after_recorded_at, query.after_outcome_id)
            )
        parameters.append(query.limit + 1)
        statement = (
            "SELECT * FROM outcome_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY recorded_at_utc DESC, outcome_id DESC LIMIT ?"
        )
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        records = [_validate_normalized_row(row) for row in rows]
        return _page(records, query)


POSTGRES_OUTCOME_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outcome_records (
    project_id text NOT NULL,
    outcome_id text NOT NULL,
    outcome_sha256 text NOT NULL,
    outcome_kind text NOT NULL,
    source_snapshot text NOT NULL,
    recorded_at_utc timestamptz NOT NULL,
    record_document jsonb NOT NULL,
    PRIMARY KEY (project_id, outcome_id),
    UNIQUE (project_id, outcome_sha256)
);
CREATE TABLE IF NOT EXISTS outcome_idempotency (
    project_id text NOT NULL,
    key_sha256 text NOT NULL,
    outcome_id text NOT NULL,
    outcome_sha256 text NOT NULL,
    PRIMARY KEY (project_id, key_sha256),
    CONSTRAINT outcome_idempotency_record_fk
        FOREIGN KEY (project_id, outcome_id)
        REFERENCES outcome_records(project_id, outcome_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS outcome_records_project_time_idx
    ON outcome_records(project_id, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_kind_time_idx
    ON outcome_records(project_id, outcome_kind, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_snapshot_time_idx
    ON outcome_records(project_id, source_snapshot, recorded_at_utc DESC, outcome_id DESC);
CREATE OR REPLACE FUNCTION reject_outcome_memory_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'outcome memory is append-only';
END;
$$;
DROP TRIGGER IF EXISTS outcome_records_no_mutation ON outcome_records;
CREATE TRIGGER outcome_records_no_mutation BEFORE UPDATE OR DELETE ON outcome_records
    FOR EACH ROW EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_idempotency_no_mutation ON outcome_idempotency;
CREATE TRIGGER outcome_idempotency_no_mutation BEFORE UPDATE OR DELETE ON outcome_idempotency
    FOR EACH ROW EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_records_no_truncate ON outcome_records;
CREATE TRIGGER outcome_records_no_truncate BEFORE TRUNCATE ON outcome_records
    FOR EACH STATEMENT EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_idempotency_no_truncate ON outcome_idempotency;
CREATE TRIGGER outcome_idempotency_no_truncate BEFORE TRUNCATE ON outcome_idempotency
    FOR EACH STATEMENT EXECUTE FUNCTION reject_outcome_memory_mutation();
INSERT INTO schema_migrations(version) VALUES ('003_outcome_memory')
    ON CONFLICT (version) DO NOTHING;
"""


class PostgresOutcomeRepository:
    """Primary relational adapter; PostgreSQL stores no vector representation."""

    def __init__(
        self, database_url: str, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.database_url = database_url
        self._clock = clock or _system_clock

    def setup(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(POSTGRES_OUTCOME_SCHEMA_SQL)

    def append(self, request: OutcomeAppendRequest, idempotency_key: str) -> OutcomeRecord:
        replayed = _replay_append(request, self._clock)
        validated = replayed.record
        document = replayed.document
        project = _validate_scope(validated.lineage.project_id, "project_id")
        key_digest = _idempotency_digest(idempotency_key)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            def lookup(predecessor_project: str, outcome_id: str) -> OutcomeRecord | None:
                row = connection.execute(
                    """SELECT * FROM outcome_records
                       WHERE project_id = %s AND outcome_id = %s FOR SHARE""",
                    (predecessor_project, outcome_id),
                ).fetchone()
                return (
                    _validate_normalized_row(_postgres_normalized_row(row))
                    if row is not None
                    else None
                )

            _require_persisted_predecessors(replayed.predecessors, lookup)
            claimed = connection.execute(
                """INSERT INTO outcome_idempotency (
                       project_id, key_sha256, outcome_id, outcome_sha256
                   ) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (project_id, key_sha256) DO NOTHING
                   RETURNING outcome_id, outcome_sha256""",
                (project, key_digest, validated.outcome_id, validated.outcome_sha256),
            ).fetchone()
            if claimed is None:
                receipt = connection.execute(
                    """SELECT outcome_id, outcome_sha256 FROM outcome_idempotency
                       WHERE project_id = %s AND key_sha256 = %s""",
                    (project, key_digest),
                ).fetchone()
                if receipt is None:
                    raise OutcomeCorruptionError("Idempotency claim disappeared during append")
                if (receipt["outcome_id"], receipt["outcome_sha256"]) != (
                    validated.outcome_id,
                    validated.outcome_sha256,
                ):
                    raise OutcomeConflictError("Idempotency key is bound to another outcome")
            inserted = connection.execute(
                """INSERT INTO outcome_records (
                       project_id, outcome_id, outcome_sha256, outcome_kind,
                       source_snapshot, recorded_at_utc, record_document
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (project_id, outcome_id) DO NOTHING
                   RETURNING *""",
                (
                    project,
                    validated.outcome_id,
                    validated.outcome_sha256,
                    validated.payload.kind.value,
                    validated.lineage.source_snapshot,
                    validated.recorded_at,
                    document,
                ),
            ).fetchone()
            row = inserted
            if row is None:
                row = connection.execute(
                    "SELECT * FROM outcome_records WHERE project_id = %s AND outcome_id = %s",
                    (project, validated.outcome_id),
                ).fetchone()
            if row is None:
                raise OutcomeCorruptionError("Outcome append did not produce a readable record")
            restored = _validate_normalized_row(_postgres_normalized_row(row))
            if restored != validated:
                raise OutcomeConflictError("Outcome identity is bound to different content")
            return restored

    def get(self, project_id: str, outcome_id: str) -> OutcomeRecord | None:
        project = _validate_scope(project_id, "project_id")
        _outcome_digest(outcome_id)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM outcome_records WHERE project_id = %s AND outcome_id = %s",
                (project, outcome_id),
            ).fetchone()
        return _validate_normalized_row(_postgres_normalized_row(row)) if row else None

    def query(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage:
        query = _normalize_query(
            project_id=project_id,
            kinds=kinds,
            source_snapshot=source_snapshot,
            cursor=cursor,
            limit=limit,
        )
        clauses = ["project_id = %s"]
        parameters: list[Any] = [query.project_id]
        if query.kinds:
            clauses.append("outcome_kind = ANY(%s)")
            parameters.append(list(query.kinds))
        if query.source_snapshot is not None:
            clauses.append("source_snapshot = %s")
            parameters.append(query.source_snapshot)
        if query.after_recorded_at is not None:
            clauses.append("(recorded_at_utc < %s OR (recorded_at_utc = %s AND outcome_id < %s))")
            parameters.extend(
                (
                    datetime.fromisoformat(query.after_recorded_at),
                    datetime.fromisoformat(query.after_recorded_at),
                    query.after_outcome_id,
                )
            )
        parameters.append(query.limit + 1)
        statement = (
            "SELECT * FROM outcome_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY recorded_at_utc DESC, outcome_id DESC LIMIT %s"
        )
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        records = [_validate_normalized_row(_postgres_normalized_row(row)) for row in rows]
        return _page(records, query)


def _postgres_normalized_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    recorded_at = normalized.get("recorded_at_utc")
    if isinstance(recorded_at, datetime):
        normalized["recorded_at_utc"] = _utc_text(recorded_at)
    return normalized


class JsonOutcomeRepository:
    """Immutable one-record-per-file fallback with content-derived safe paths."""

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.root = Path(root)
        self._clock = clock or _system_clock
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_directory(self, project_id: str) -> Path:
        return self.root / _sha256(project_id)

    def _record_path(self, project_id: str, outcome_id: str) -> Path:
        return self._project_directory(project_id) / f"outcome-{_outcome_digest(outcome_id)}.json"

    def _receipt_path(self, project_id: str, key_digest: str) -> Path:
        return self._project_directory(project_id) / "idempotency" / f"{key_digest}.json"

    @staticmethod
    def _exclusive_publish(path: Path, content: bytes) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=".pending-", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_json_object(path: Path, label: str) -> dict[str, Any]:
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_RECORD_BYTES:
                raise OutcomeCorruptionError(f"Persisted {label} exceeds the byte limit")
            body = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except OutcomeCorruptionError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OutcomeCorruptionError(f"Persisted {label} is unreadable") from exc
        if not isinstance(body, dict):
            raise OutcomeCorruptionError(f"Persisted {label} is not a JSON object")
        return body

    def _read_record(self, path: Path) -> OutcomeRecord:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise OutcomeCorruptionError("Persisted outcome is unreadable") from exc
        record, canonical = _parse_record(raw)
        if raw != canonical.encode("utf-8"):
            raise OutcomeCorruptionError("Persisted outcome document is not canonical JSON")
        match = _RECORD_FILE.fullmatch(path.name)
        if match is None or match.group(1) != record.outcome_sha256:
            raise OutcomeCorruptionError("Outcome file name does not match its immutable identity")
        return record

    def append(self, request: OutcomeAppendRequest, idempotency_key: str) -> OutcomeRecord:
        replayed = _replay_append(request, self._clock)
        validated = replayed.record
        document = replayed.document
        project = _validate_scope(validated.lineage.project_id, "project_id")
        key_digest = _idempotency_digest(idempotency_key)

        def lookup(predecessor_project: str, outcome_id: str) -> OutcomeRecord | None:
            path = self._record_path(predecessor_project, outcome_id)
            if not path.is_file():
                return None
            record = self._read_record(path)
            if record.lineage.project_id != predecessor_project:
                raise OutcomeCorruptionError(
                    "Incident predecessor is stored under the wrong project scope"
                )
            return record

        _require_persisted_predecessors(replayed.predecessors, lookup)
        receipt_path = self._receipt_path(project, key_digest)
        receipt = {
            "key_sha256": key_digest,
            "outcome_id": validated.outcome_id,
            "outcome_sha256": validated.outcome_sha256,
            "project_sha256": _sha256(project),
            "version": 1,
        }
        receipt_bytes = _canonical_json(receipt)
        if not self._exclusive_publish(receipt_path, receipt_bytes):
            persisted = self._read_json_object(receipt_path, "idempotency receipt")
            if set(persisted) != set(receipt) or any(
                not isinstance(persisted.get(key), type(value))
                for key, value in receipt.items()
            ):
                raise OutcomeCorruptionError(
                    "Persisted idempotency receipt has an invalid contract"
                )
            if persisted != receipt:
                raise OutcomeConflictError("Idempotency key is bound to another outcome")
        record_path = self._record_path(project, validated.outcome_id)
        if not self._exclusive_publish(record_path, document.encode("utf-8")):
            existing = self._read_record(record_path)
            if existing != validated:
                raise OutcomeConflictError("Outcome identity is bound to different content")
        return validated.model_copy(deep=True)

    def get(self, project_id: str, outcome_id: str) -> OutcomeRecord | None:
        project = _validate_scope(project_id, "project_id")
        path = self._record_path(project, outcome_id)
        if not path.is_file():
            return None
        record = self._read_record(path)
        if record.lineage.project_id != project:
            raise OutcomeCorruptionError("Outcome file is stored under the wrong project scope")
        return record

    def query(
        self,
        *,
        project_id: str,
        kinds: Iterable[OutcomeKind | str] | None = None,
        source_snapshot: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> OutcomePage:
        query = _normalize_query(
            project_id=project_id,
            kinds=kinds,
            source_snapshot=source_snapshot,
            cursor=cursor,
            limit=limit,
        )
        directory = self._project_directory(query.project_id)
        if not directory.exists():
            return OutcomePage(records=(), next_cursor=None)
        records: list[OutcomeRecord] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_dir() and path.name == "idempotency":
                continue
            if path.is_file() and path.name.startswith(".pending-") and path.suffix == ".tmp":
                continue
            if not path.is_file() or _RECORD_FILE.fullmatch(path.name) is None:
                raise OutcomeCorruptionError("Outcome project directory contains an unknown entry")
            record = self._read_record(path)
            if record.lineage.project_id != query.project_id:
                raise OutcomeCorruptionError("Outcome file is stored under the wrong project scope")
            records.append(record)
        return _page((record for record in records if _record_matches(record, query)), query)
