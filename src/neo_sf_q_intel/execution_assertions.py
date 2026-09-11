"""Typed, byte-preserving execution assertion artifacts.

Gate receipt producers resolve these durable artifacts and derive their receipt outcome and
digests from exact stored content.  A caller-provided ``PASSED`` value or opaque digest is never
accepted as execution evidence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import ConfigDict, Field, ValidationError, computed_field, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptOutcome, ReceiptScope
from neo_sf_q_intel.postgres_schema import (
    DEFAULT_POSTGRES_SCHEMA,
    initialize_postgres_schema,
    scoped_connection_string,
)
from neo_sf_q_intel.safety import SensitiveTextError, require_no_sensitive_text
from neo_sf_q_intel.temporal import UtcModel, aware_utc, parse_aware_utc

MAX_EXECUTION_ASSERTION_BYTES = 262_144
MAX_EXECUTION_ASSERTIONS = 256
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_ROLE = r"^[A-Z][A-Z0-9_]{0,127}$"
_HEX64 = r"^[a-f0-9]{64}$"
_ARTIFACT_ID = r"^execution-assertion:[a-f0-9]{64}$"
_VERSION = r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$"


class ExecutionAssertionError(RuntimeError):
    """Base sanitized assertion-artifact failure."""


class ExecutionAssertionCorruptionError(ExecutionAssertionError):
    """Exact stored bytes or metadata failed strict replay."""


class ExecutionAssertionConflictError(ExecutionAssertionError):
    """An immutable artifact identity was reused with different bytes."""


class ExecutionAssertionNotFoundError(ExecutionAssertionError):
    """The requested durable artifact does not exist."""


class ExecutionAssertionUnavailableError(ExecutionAssertionError):
    """No durable assertion store can be opened; evidence production must stop."""


class ExecutionAssertionReplayError(ExecutionAssertionError):
    """A stored artifact does not satisfy the current expected roots."""


class AssertionSubjectKind(StrEnum):
    TEST_METHOD = "TEST_METHOD"
    HTTP_ASSERTION = "HTTP_ASSERTION"
    METADATA_ASSERTION = "METADATA_ASSERTION"
    BROWSER_ASSERTION = "BROWSER_ASSERTION"
    RESTORATION_ASSERTION = "RESTORATION_ASSERTION"
    CLEANUP_ASSERTION = "CLEANUP_ASSERTION"


class AssertionOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    INCONCLUSIVE = "INCONCLUSIVE"


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionToolVersion(_Model):
    tool_id: str = Field(pattern=_IDENTIFIER)
    version: str = Field(pattern=_VERSION)


class ExecutionArtifactReference(_Model):
    artifact_role: str = Field(pattern=_ROLE)
    media_type: str = Field(pattern=r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$")
    content_sha256: str = Field(pattern=_HEX64)
    content_size_bytes: int = Field(ge=1, le=16_777_216)
    content_base64: str = Field(min_length=4, max_length=349_528)

    @model_validator(mode="after")
    def verify_exact_content(self) -> ExecutionArtifactReference:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
            if base64.b64encode(content).decode("ascii") != self.content_base64:
                raise ValueError
            if len(content) != self.content_size_bytes or _sha256(content) != self.content_sha256:
                raise ValueError
            if self.media_type == "application/json":
                value = json.loads(
                    content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
                )
                require_no_sensitive_text(value)
        except Exception as exc:
            raise ValueError("result artifact content failed exact secret-safe validation") from exc
        return self


class ExecutionAssertion(_Model):
    assertion_id: str = Field(pattern=_IDENTIFIER)
    subject_kind: AssertionSubjectKind
    subject_id: str = Field(pattern=_IDENTIFIER)
    target_sha256: str = Field(pattern=_HEX64)
    predicate: str = Field(pattern=_ROLE)
    outcome: AssertionOutcome
    expected_sha256: str = Field(pattern=_HEX64)
    observed_sha256: str = Field(pattern=_HEX64)
    result_artifact_sha256: str = Field(pattern=_HEX64)
    result_artifact_role: str = Field(pattern=_ROLE)
    predicate_sha256: str = Field(pattern=_HEX64)
    observed_cardinality: int = Field(ge=0, le=4096)
    observed_invocation_count: int = Field(default=1, ge=1, le=1000)
    observed_projection: tuple[str, ...] = Field(max_length=256)
    metadata_type: str | None = Field(default=None, max_length=200)
    metadata_member: str | None = Field(default=None, max_length=500)
    compatible_dataset_target_sha256s: tuple[str, ...] = Field(default=(), max_length=256)
    duration_ms: int = Field(ge=0, le=86_400_000)
    detail_code: str | None = Field(default=None, pattern=_ROLE)

    @model_validator(mode="after")
    def require_outcome_detail(self) -> ExecutionAssertion:
        if self.outcome is AssertionOutcome.PASSED and self.detail_code is not None:
            raise ValueError("passing assertion cannot carry a detail code")
        if self.outcome is not AssertionOutcome.PASSED and self.detail_code is None:
            raise ValueError("nonpassing assertion requires a detail code")
        if self.observed_projection != tuple(sorted(set(self.observed_projection))):
            raise ValueError("observed projection must be sorted and unique")
        if self.compatible_dataset_target_sha256s != tuple(
            sorted(set(self.compatible_dataset_target_sha256s))
        ):
            raise ValueError("compatible dataset roots must be sorted and unique")
        return self


class ExecutionAssertionArtifact(_Model):
    """Canonical runner output whose terminal outcome is derived from exact assertions."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    expected_contract_bytes_sha256: str = Field(pattern=_HEX64)
    producer_id: str = Field(pattern=_IDENTIFIER)
    runner_id: str = Field(pattern=_IDENTIFIER)
    runner_key_id: str = Field(pattern=_IDENTIFIER)
    execution_id: str = Field(pattern=_IDENTIFIER)
    runner_version: str = Field(pattern=_VERSION)
    adapter_version: str = Field(pattern=_VERSION)
    tool_versions: tuple[ExecutionToolVersion, ...] = Field(min_length=1, max_length=16)
    gate_id: str = Field(pattern=_IDENTIFIER)
    evidence_phase: EvidencePhase
    scope: ReceiptScope
    expected_assertion_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_EXECUTION_ASSERTIONS,
    )
    assertions: tuple[ExecutionAssertion, ...] = Field(
        min_length=1, max_length=MAX_EXECUTION_ASSERTIONS
    )
    result_artifacts: tuple[ExecutionArtifactReference, ...] = Field(min_length=1, max_length=64)
    runner_result_sha256: str = Field(pattern=_HEX64)
    started_at: datetime
    terminal_at: datetime
    expires_at: datetime
    gap_codes: tuple[str, ...] = Field(default=(), max_length=64)
    runner_signature_sha256: str = Field(pattern=_HEX64)

    @computed_field
    @property
    def derived_outcome(self) -> ReceiptOutcome:
        outcomes = {item.outcome for item in self.assertions}
        if outcomes == {AssertionOutcome.PASSED}:
            return ReceiptOutcome.PASSED
        for assertion_outcome, receipt_outcome in (
            (AssertionOutcome.TIMED_OUT, ReceiptOutcome.TIMED_OUT),
            (AssertionOutcome.CANCELLED, ReceiptOutcome.CANCELLED),
            (AssertionOutcome.FAILED, ReceiptOutcome.FAILED),
            (AssertionOutcome.BLOCKED, ReceiptOutcome.BLOCKED),
        ):
            if assertion_outcome in outcomes:
                return receipt_outcome
        return ReceiptOutcome.NOT_RUN

    @computed_field
    @property
    def assertions_sha256(self) -> str:
        return stable_sha256([item.model_dump(mode="json") for item in self.assertions])

    @computed_field
    @property
    def artifact_index_sha256(self) -> str:
        return stable_sha256([item.model_dump(mode="json") for item in self.result_artifacts])

    @model_validator(mode="after")
    def validate_artifact(self) -> ExecutionAssertionArtifact:
        _require_utc(self.started_at, "started_at")
        _require_utc(self.terminal_at, "terminal_at")
        _require_utc(self.expires_at, "expires_at")
        if not self.started_at <= self.terminal_at < self.expires_at:
            raise ValueError("execution assertion timestamps are invalid")
        assertion_ids = tuple(item.assertion_id for item in self.assertions)
        if self.expected_assertion_ids != assertion_ids:
            raise ValueError("assertions must exactly match the ordered expected set")
        _require_unique(self.expected_assertion_ids, "assertion IDs")
        _require_unique(tuple(item.tool_id for item in self.tool_versions), "tool IDs")
        _require_unique(
            tuple(item.artifact_role for item in self.result_artifacts), "artifact roles"
        )
        artifacts = {item.artifact_role: item for item in self.result_artifacts}
        if any(
            assertion.result_artifact_role not in artifacts
            or assertion.result_artifact_sha256
            != artifacts[assertion.result_artifact_role].content_sha256
            or assertion.observed_sha256 != artifacts[assertion.result_artifact_role].content_sha256
            for assertion in self.assertions
        ):
            raise ValueError("assertion result roots differ from exact stored artifact bytes")
        if self.runner_result_sha256 != self.artifact_index_sha256:
            raise ValueError("runner result root differs from exact stored artifact index")
        _require_unique(self.gap_codes, "gap codes")
        if any(not re.fullmatch(_ROLE, code) for code in self.gap_codes):
            raise ValueError("gap codes must use bounded code vocabulary")
        if self.derived_outcome is ReceiptOutcome.PASSED and self.gap_codes:
            raise ValueError("passing assertion artifact cannot carry gaps")
        if self.derived_outcome is not ReceiptOutcome.PASSED and not self.gap_codes:
            raise ValueError("nonpassing assertion artifact requires a gap code")
        return self


class StoredExecutionAssertion(_Model):
    sequence_number: int = Field(ge=1)
    assertion_artifact_id: str = Field(pattern=_ARTIFACT_ID)
    campaign_id: str = Field(pattern=_IDENTIFIER)
    gate_id: str = Field(pattern=_IDENTIFIER)
    evidence_phase: EvidencePhase
    runner_id: str = Field(pattern=_IDENTIFIER)
    execution_id: str = Field(pattern=_IDENTIFIER)
    terminal_at: datetime
    document_sha256: str = Field(pattern=_HEX64)
    document_size_bytes: int = Field(ge=1, le=MAX_EXECUTION_ASSERTION_BYTES)
    assertion_document: bytes = Field(min_length=1, max_length=MAX_EXECUTION_ASSERTION_BYTES)
    appended_at: datetime

    @model_validator(mode="after")
    def validate_stored_record(self) -> StoredExecutionAssertion:
        _require_utc(self.terminal_at, "terminal_at")
        _require_utc(self.appended_at, "appended_at")
        if len(self.assertion_document) != self.document_size_bytes:
            raise ValueError("assertion document size differs from stored metadata")
        if _sha256(self.assertion_document) != self.document_sha256:
            raise ValueError("assertion document digest differs from stored metadata")
        artifact = parse_execution_assertion(self.assertion_document)
        if (
            self.assertion_artifact_id != execution_assertion_id(self.assertion_document)
            or self.campaign_id != artifact.scope.campaign_id
            or self.gate_id != artifact.gate_id
            or self.evidence_phase is not artifact.evidence_phase
            or self.runner_id != artifact.runner_id
            or self.execution_id != artifact.execution_id
            or self.terminal_at != artifact.terminal_at
        ):
            raise ValueError("stored assertion metadata differs from exact document")
        return self

    def parsed_artifact(self) -> ExecutionAssertionArtifact:
        return parse_execution_assertion(self.assertion_document)


class ExecutionAssertionStore(Protocol):
    """Durable exact-byte store; it grants no runner trust or gate acceptance."""

    def append(self, assertion_document: bytes) -> StoredExecutionAssertion: ...

    def get(self, assertion_artifact_id: str) -> StoredExecutionAssertion: ...


class UnavailableExecutionAssertionStore:
    """Fail-closed adapter used when neither durable configured backend can open."""

    def append(self, assertion_document: bytes) -> StoredExecutionAssertion:
        raise ExecutionAssertionUnavailableError("execution assertion store is unavailable")

    def get(self, assertion_artifact_id: str) -> StoredExecutionAssertion:
        raise ExecutionAssertionUnavailableError("execution assertion store is unavailable")


@dataclass(frozen=True, slots=True)
class ExecutionAssertionStoreSelection:
    mode: str
    store: ExecutionAssertionStore
    degradation_code: str | None


@dataclass(frozen=True, slots=True, repr=False)
class TrustedExecutionRunnerKey:
    """Host trust binding for one runner signing key; key bytes are never represented."""

    key_id: str
    producer_id: str
    runner_id: str
    allowed_gate_ids: frozenset[str]
    allowed_receipt_roles: frozenset[str]
    hmac_key: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key_id, str)
            or not re.fullmatch(_IDENTIFIER, self.key_id)
            or not isinstance(self.producer_id, str)
            or not re.fullmatch(_IDENTIFIER, self.producer_id)
            or not isinstance(self.runner_id, str)
            or not re.fullmatch(_IDENTIFIER, self.runner_id)
            or not isinstance(self.allowed_gate_ids, frozenset)
            or not self.allowed_gate_ids
            or len(self.allowed_gate_ids) > 64
            or any(not re.fullmatch(_IDENTIFIER, item) for item in self.allowed_gate_ids)
            or not isinstance(self.allowed_receipt_roles, frozenset)
            or not self.allowed_receipt_roles
            or len(self.allowed_receipt_roles) > 64
            or any(not re.fullmatch(_ROLE, item) for item in self.allowed_receipt_roles)
        ):
            raise ValueError("runner key trust binding is invalid")
        _validated_runner_key(self.hmac_key)


def validate_trusted_execution_runner_keys(
    values: Mapping[str, TrustedExecutionRunnerKey],
) -> dict[str, TrustedExecutionRunnerKey]:
    """Validate a bounded registry and reject ambiguous runner/gate key ownership."""

    try:
        bindings = dict(values)
    except (TypeError, ValueError):
        raise ValueError("trusted runner key registry is invalid") from None
    if (
        not bindings
        or len(bindings) > 64
        or any(
            not isinstance(key_id, str)
            or not isinstance(binding, TrustedExecutionRunnerKey)
            or key_id != binding.key_id
            for key_id, binding in bindings.items()
        )
    ):
        raise ValueError("trusted runner key registry is invalid")
    gate_claims: set[tuple[str, str, str]] = set()
    role_claims: set[tuple[str, str, str]] = set()
    for binding in bindings.values():
        for gate_id in binding.allowed_gate_ids:
            claim = (binding.producer_id, binding.runner_id, gate_id)
            if claim in gate_claims:
                raise ValueError("trusted runner key registry has ambiguous gate ownership")
            gate_claims.add(claim)
        for receipt_role in binding.allowed_receipt_roles:
            claim = (binding.producer_id, binding.runner_id, receipt_role)
            if claim in role_claims:
                raise ValueError("trusted runner key registry has ambiguous role ownership")
            role_claims.add(claim)
    return bindings


def build_execution_artifact_reference(
    *, artifact_role: str, media_type: str, content: bytes
) -> ExecutionArtifactReference:
    """Build a reference from the exact sanitized bytes that replay will preserve."""

    if not isinstance(content, bytes) or not content:
        raise ExecutionAssertionCorruptionError("result artifact bytes are absent")
    return ExecutionArtifactReference(
        artifact_role=artifact_role,
        media_type=media_type,
        content_sha256=_sha256(content),
        content_size_bytes=len(content),
        content_base64=base64.b64encode(content).decode("ascii"),
    )


def execution_artifact_index_sha256(
    references: Sequence[ExecutionArtifactReference],
) -> str:
    """Hash the exact byte-bearing artifact index in canonical order."""

    return stable_sha256([item.model_dump(mode="json") for item in references])


def sign_execution_assertion(
    artifact: ExecutionAssertionArtifact,
    *,
    runner_key: bytes,
) -> ExecutionAssertionArtifact:
    """Authenticate exact execution bytes with a host-configured runner key."""

    key = _validated_runner_key(runner_key)
    signature = hmac.new(key, _runner_signature_body(artifact), hashlib.sha256).hexdigest()
    return artifact.model_copy(update={"runner_signature_sha256": signature})


def serialize_execution_assertion(artifact: ExecutionAssertionArtifact) -> bytes:
    if not isinstance(artifact, ExecutionAssertionArtifact):
        raise ExecutionAssertionCorruptionError("artifact must use the typed assertion contract")
    document = _canonical_json(artifact.model_dump(mode="json", exclude_computed_fields=True))
    parse_execution_assertion(document)
    return document


def parse_execution_assertion(document: bytes) -> ExecutionAssertionArtifact:
    if (
        not isinstance(document, bytes)
        or not document
        or len(document) > MAX_EXECUTION_ASSERTION_BYTES
    ):
        raise ExecutionAssertionCorruptionError("assertion bytes are absent or exceed the bound")
    try:
        body = json.loads(document.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(body, dict):
            raise TypeError
        require_no_sensitive_text(body)
        return ExecutionAssertionArtifact.model_validate(body)
    except (
        SensitiveTextError,
        TypeError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ExecutionAssertionCorruptionError(
            "assertion bytes failed strict, secret-safe contract validation"
        ) from exc


def execution_assertion_id(document: bytes) -> str:
    parse_execution_assertion(document)
    return f"execution-assertion:{_sha256(document)}"


def replay_execution_assertion(
    store: ExecutionAssertionStore,
    *,
    assertion_artifact_id: str,
    expected_scope: ReceiptScope,
    expected_gate_id: str,
    expected_evidence_phase: EvidencePhase,
    trusted_runner_keys: Mapping[str, TrustedExecutionRunnerKey],
    expected_receipt_role: str,
    expected_contract_document: bytes | None = None,
    now: datetime | None = None,
) -> ExecutionAssertionArtifact:
    """Replay exact bytes against runner authentication and independent expectations."""

    try:
        if not re.fullmatch(_ARTIFACT_ID, assertion_artifact_id):
            raise ValueError
        runner_keys = validate_trusted_execution_runner_keys(trusted_runner_keys)
        if not isinstance(expected_receipt_role, str) or not re.fullmatch(
            _ROLE, expected_receipt_role
        ):
            raise ValueError
        observed_at = aware_utc(now if now is not None else datetime.now(UTC))
        _require_utc(observed_at, "now")
        stored = store.get(assertion_artifact_id)
        artifact = stored.parsed_artifact()
        runner_binding = runner_keys.get(artifact.runner_key_id)
        if (
            runner_binding is None
            or artifact.producer_id != runner_binding.producer_id
            or artifact.runner_id != runner_binding.runner_id
            or artifact.gate_id not in runner_binding.allowed_gate_ids
            or expected_receipt_role not in runner_binding.allowed_receipt_roles
            or not hmac.compare_digest(
                artifact.runner_signature_sha256,
                hmac.new(
                    runner_binding.hmac_key,
                    _runner_signature_body(artifact),
                    hashlib.sha256,
                ).hexdigest(),
            )
        ):
            raise ValueError
        if (
            stored.assertion_artifact_id != assertion_artifact_id
            or artifact.scope != expected_scope
            or artifact.gate_id != expected_gate_id
            or artifact.evidence_phase is not expected_evidence_phase
            or artifact.started_at > observed_at
            or artifact.terminal_at > observed_at
            or artifact.expires_at <= observed_at
        ):
            raise ValueError
        if expected_contract_document is not None:
            _verify_expected_execution_contract(
                artifact,
                expected_contract_document=expected_contract_document,
                observed_at=observed_at,
            )
        return artifact
    except ExecutionAssertionNotFoundError:
        raise
    except Exception as exc:
        raise ExecutionAssertionReplayError(
            "execution assertion does not satisfy current expected roots"
        ) from exc


SQLITE_EXECUTION_ASSERTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_assertion_artifacts (
    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
    assertion_artifact_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    evidence_phase TEXT NOT NULL,
    runner_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    terminal_at_utc TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    document_size_bytes INTEGER NOT NULL,
    assertion_document BLOB NOT NULL,
    appended_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_assertion_campaign_gate_idx
    ON execution_assertion_artifacts(campaign_id, gate_id, sequence_number);
CREATE TRIGGER IF NOT EXISTS execution_assertion_no_update
BEFORE UPDATE ON execution_assertion_artifacts BEGIN
    SELECT RAISE(ABORT, 'execution assertion store is append-only');
END;
CREATE TRIGGER IF NOT EXISTS execution_assertion_no_delete
BEFORE DELETE ON execution_assertion_artifacts BEGIN
    SELECT RAISE(ABORT, 'execution assertion store is append-only');
END;
"""


class SQLiteExecutionAssertionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(SQLITE_EXECUTION_ASSERTION_SCHEMA_SQL)

    def append(self, assertion_document: bytes) -> StoredExecutionAssertion:
        artifact = parse_execution_assertion(assertion_document)
        artifact_id = execution_assertion_id(assertion_document)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM execution_assertion_artifacts WHERE assertion_artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is not None:
                stored = _sqlite_row(row)
                if stored.assertion_document != assertion_document:
                    raise ExecutionAssertionConflictError(
                        "assertion identity is bound to different exact bytes"
                    )
                return stored
            cursor = connection.execute(
                """INSERT INTO execution_assertion_artifacts (
                       assertion_artifact_id, campaign_id, gate_id, evidence_phase,
                       runner_id, execution_id, terminal_at_utc, document_sha256,
                       document_size_bytes, assertion_document, appended_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _row_values(artifact_id, artifact, assertion_document),
            )
            row = connection.execute(
                "SELECT * FROM execution_assertion_artifacts WHERE sequence_number = ?",
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise ExecutionAssertionCorruptionError("assertion append produced no durable row")
            return _sqlite_row(row)

    def get(self, assertion_artifact_id: str) -> StoredExecutionAssertion:
        _validate_artifact_id(assertion_artifact_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_assertion_artifacts WHERE assertion_artifact_id = ?",
                (assertion_artifact_id,),
            ).fetchone()
        if row is None:
            raise ExecutionAssertionNotFoundError("execution assertion artifact is unavailable")
        return _sqlite_row(row)


POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_assertion_artifacts (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assertion_artifact_id text NOT NULL UNIQUE,
    campaign_id text NOT NULL,
    gate_id text NOT NULL,
    evidence_phase text NOT NULL,
    runner_id text NOT NULL,
    execution_id text NOT NULL,
    terminal_at_utc timestamptz NOT NULL,
    document_sha256 text NOT NULL,
    document_size_bytes integer NOT NULL,
    assertion_document bytea NOT NULL,
    appended_at_utc timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_assertion_campaign_gate_idx
    ON execution_assertion_artifacts(campaign_id, gate_id, sequence_number);
CREATE OR REPLACE FUNCTION reject_execution_assertion_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'execution assertion store is append-only';
END;
$$;
DROP TRIGGER IF EXISTS execution_assertion_no_mutation ON execution_assertion_artifacts;
CREATE TRIGGER execution_assertion_no_mutation
    BEFORE UPDATE OR DELETE ON execution_assertion_artifacts
    FOR EACH ROW EXECUTE FUNCTION reject_execution_assertion_mutation();
DROP TRIGGER IF EXISTS execution_assertion_no_truncate ON execution_assertion_artifacts;
CREATE TRIGGER execution_assertion_no_truncate BEFORE TRUNCATE ON execution_assertion_artifacts
    FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_assertion_mutation();
"""


class PostgresExecutionAssertionStore:
    def __init__(self, database_url: str, *, schema: str = DEFAULT_POSTGRES_SCHEMA) -> None:
        self.database_url = database_url
        self.schema = schema
        self.connection_string = scoped_connection_string(database_url, schema)

    def setup(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            initialize_postgres_schema(connection, self.schema)
            connection.execute(POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL)

    def append(self, assertion_document: bytes) -> StoredExecutionAssertion:
        artifact = parse_execution_assertion(assertion_document)
        artifact_id = execution_assertion_id(assertion_document)
        values = _row_values(artifact_id, artifact, assertion_document)
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            row = connection.execute(
                """INSERT INTO execution_assertion_artifacts (
                       assertion_artifact_id, campaign_id, gate_id, evidence_phase,
                       runner_id, execution_id, terminal_at_utc, document_sha256,
                       document_size_bytes, assertion_document, appended_at_utc
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (assertion_artifact_id) DO NOTHING RETURNING *""",
                values,
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT * FROM execution_assertion_artifacts
                       WHERE assertion_artifact_id = %s FOR SHARE""",
                    (artifact_id,),
                ).fetchone()
            if row is None:
                raise ExecutionAssertionCorruptionError("assertion append produced no durable row")
            stored = _postgres_row(row)
            if stored.assertion_document != assertion_document:
                raise ExecutionAssertionConflictError(
                    "assertion identity is bound to different exact bytes"
                )
            return stored

    def get(self, assertion_artifact_id: str) -> StoredExecutionAssertion:
        _validate_artifact_id(assertion_artifact_id)
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM execution_assertion_artifacts WHERE assertion_artifact_id = %s",
                (assertion_artifact_id,),
            ).fetchone()
        if row is None:
            raise ExecutionAssertionNotFoundError("execution assertion artifact is unavailable")
        return _postgres_row(row)


def open_execution_assertion_store(
    *,
    database_url: str | None,
    sqlite_path: Path,
    postgres_schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> ExecutionAssertionStoreSelection:
    """Prefer PostgreSQL, degrade explicitly to SQLite, and never accept volatile memory."""

    if database_url:
        try:
            primary = PostgresExecutionAssertionStore(
                database_url,
                schema=postgres_schema,
            )
            primary.setup()
            return ExecutionAssertionStoreSelection("POSTGRESQL", primary, None)
        except Exception:
            degradation_code = "POSTGRES_UNAVAILABLE"
    else:
        degradation_code = None
    try:
        fallback = SQLiteExecutionAssertionStore(sqlite_path)
        return ExecutionAssertionStoreSelection("SQLITE", fallback, degradation_code)
    except Exception:
        return ExecutionAssertionStoreSelection(
            "UNAVAILABLE",
            UnavailableExecutionAssertionStore(),
            "EXECUTION_ASSERTION_STORE_UNAVAILABLE",
        )


def _row_values(
    artifact_id: str,
    artifact: ExecutionAssertionArtifact,
    document: bytes,
) -> tuple[Any, ...]:
    return (
        artifact_id,
        artifact.scope.campaign_id,
        artifact.gate_id,
        artifact.evidence_phase.value,
        artifact.runner_id,
        artifact.execution_id,
        _utc_text(artifact.terminal_at),
        _sha256(document),
        len(document),
        document,
        _utc_text(datetime.now(UTC)),
    )


def _sqlite_row(row: sqlite3.Row) -> StoredExecutionAssertion:
    return StoredExecutionAssertion(
        sequence_number=row["sequence_number"],
        assertion_artifact_id=row["assertion_artifact_id"],
        campaign_id=row["campaign_id"],
        gate_id=row["gate_id"],
        evidence_phase=row["evidence_phase"],
        runner_id=row["runner_id"],
        execution_id=row["execution_id"],
        terminal_at=_parse_utc(row["terminal_at_utc"]),
        document_sha256=row["document_sha256"],
        document_size_bytes=row["document_size_bytes"],
        assertion_document=bytes(row["assertion_document"]),
        appended_at=_parse_utc(row["appended_at_utc"]),
    )


def _postgres_row(row: dict[str, Any]) -> StoredExecutionAssertion:
    return StoredExecutionAssertion(
        sequence_number=row["sequence_number"],
        assertion_artifact_id=row["assertion_artifact_id"],
        campaign_id=row["campaign_id"],
        gate_id=row["gate_id"],
        evidence_phase=row["evidence_phase"],
        runner_id=row["runner_id"],
        execution_id=row["execution_id"],
        terminal_at=row["terminal_at_utc"],
        document_sha256=row["document_sha256"],
        document_size_bytes=row["document_size_bytes"],
        assertion_document=bytes(row["assertion_document"]),
        appended_at=row["appended_at_utc"],
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _runner_signature_body(artifact: ExecutionAssertionArtifact) -> bytes:
    return _canonical_json(
        artifact.model_dump(
            mode="json",
            exclude={"runner_signature_sha256"},
            exclude_computed_fields=True,
        )
    )


def _validated_runner_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or not 32 <= len(value) <= 4096:
        raise ValueError("runner authentication key is invalid")
    return value


def _verify_expected_execution_contract(
    artifact: ExecutionAssertionArtifact,
    *,
    expected_contract_document: bytes,
    observed_at: datetime,
) -> None:
    if (
        not isinstance(expected_contract_document, bytes)
        or not expected_contract_document
        or len(expected_contract_document) > 2 * 1024 * 1024
    ):
        raise ValueError("expected execution contract is unavailable")
    body = json.loads(
        expected_contract_document.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
    )
    if not isinstance(body, dict) or _canonical_json(body) != expected_contract_document:
        raise ValueError("expected execution contract bytes are not canonical")
    require_no_sensitive_text(body)
    # Imported lazily to avoid the compiler's intentional dependency on ExecutionToolVersion.
    from neo_sf_q_intel.candidate_target_compiler import ExpectedExecutionContract

    contract = ExpectedExecutionContract.model_validate(body)
    valid_until = parse_aware_utc(contract.valid_until)
    _require_utc(valid_until, "expected contract valid_until")
    expected = tuple(item for item in contract.assertions if item.gate_id == artifact.gate_id)
    if (
        artifact.expected_contract_bytes_sha256 != _sha256(expected_contract_document)
        or artifact.execution_id != contract.execution_id
        or artifact.scope != contract.scope
        or artifact.evidence_phase is not contract.evidence_phase
        or artifact.gate_id not in contract.gate_ids
        or valid_until <= observed_at
        or artifact.expires_at > valid_until
        or artifact.producer_id != contract.runner_provenance.producer_id
        or artifact.runner_id != contract.runner_provenance.runner_id
        or artifact.runner_key_id != contract.runner_provenance.runner_key_id
        or artifact.runner_version != contract.runner_provenance.runner_version
        or artifact.adapter_version != contract.runner_provenance.adapter_version
        or artifact.tool_versions != contract.runner_provenance.tool_versions
        or not expected
        or artifact.expected_assertion_ids != tuple(item.assertion_id for item in expected)
    ):
        raise ValueError("execution assertion differs from independent contract")
    observed = {item.assertion_id: item for item in artifact.assertions}
    for expectation in expected:
        assertion = observed.get(expectation.assertion_id)
        if assertion is None:
            raise ValueError("expected execution assertion is missing")
        predicate_root = stable_sha256(expectation.model_dump(mode="json"))
        satisfies_contract = (
            assertion.target_sha256 == expectation.target_sha256
            and assertion.predicate == expectation.predicate.value
            and assertion.expected_sha256 == expectation.expected_sha256
            and assertion.predicate_sha256 == predicate_root
            and expectation.minimum_cardinality
            <= assertion.observed_cardinality
            <= expectation.maximum_cardinality
            and assertion.observed_invocation_count == expectation.invocation_count
            and assertion.observed_projection == expectation.exact_projection
            and assertion.metadata_type == expectation.metadata_type
            and assertion.metadata_member == expectation.metadata_member
            and assertion.compatible_dataset_target_sha256s
            == expectation.compatible_dataset_target_sha256s
        )
        if assertion.outcome is AssertionOutcome.PASSED and not satisfies_contract:
            raise ValueError("passing assertion does not satisfy independent predicate")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_artifact_id(value: str) -> None:
    if not re.fullmatch(_ARTIFACT_ID, value):
        raise ExecutionAssertionNotFoundError("execution assertion artifact is unavailable")


def _utc_text(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return parse_aware_utc(value)


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be timezone-aware UTC")


def _require_unique(values: Sequence[Any], field: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must be unique")


__all__ = [
    "AssertionOutcome",
    "AssertionSubjectKind",
    "ExecutionArtifactReference",
    "ExecutionAssertion",
    "ExecutionAssertionArtifact",
    "ExecutionAssertionConflictError",
    "ExecutionAssertionCorruptionError",
    "ExecutionAssertionError",
    "ExecutionAssertionNotFoundError",
    "ExecutionAssertionReplayError",
    "ExecutionAssertionStore",
    "ExecutionAssertionStoreSelection",
    "ExecutionAssertionUnavailableError",
    "ExecutionToolVersion",
    "MAX_EXECUTION_ASSERTION_BYTES",
    "POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL",
    "PostgresExecutionAssertionStore",
    "SQLITE_EXECUTION_ASSERTION_SCHEMA_SQL",
    "SQLiteExecutionAssertionStore",
    "StoredExecutionAssertion",
    "TrustedExecutionRunnerKey",
    "UnavailableExecutionAssertionStore",
    "build_execution_artifact_reference",
    "execution_artifact_index_sha256",
    "execution_assertion_id",
    "open_execution_assertion_store",
    "parse_execution_assertion",
    "replay_execution_assertion",
    "serialize_execution_assertion",
    "sign_execution_assertion",
    "validate_trusted_execution_runner_keys",
]
