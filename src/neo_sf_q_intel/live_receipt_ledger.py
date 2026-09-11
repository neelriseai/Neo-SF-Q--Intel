"""Append-only durable storage for non-authorizing live receipt evidence.

The ledger preserves exact validated receipt bytes.  It does not verify issuer authority,
calculate acceptance, satisfy requirements, or make a release decision; callers must replay
stored receipts through the current live-receipt validator.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Never, Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.live_receipts import (
    GateReceiptPayload,
    SignedLiveReceipt,
    SupportingReceiptPayload,
)
from neo_sf_q_intel.postgres_schema import (
    DEFAULT_POSTGRES_SCHEMA,
    initialize_postgres_schema,
    scoped_connection_string,
)
from neo_sf_q_intel.safety import SensitiveTextError, require_no_sensitive_text
from neo_sf_q_intel.temporal import UtcModel, aware_utc, parse_aware_utc

MAX_RECEIPT_BYTES = 65_536
MAX_REPLAY_RECEIPTS = 256
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_GATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ROLE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_RECEIPT_ID = re.compile(r"^live-receipt:[a-f0-9]{64}$")
_HEX64 = re.compile(r"^[a-f0-9]{64}$")


class LiveReceiptLedgerError(RuntimeError):
    """Base error for receipt-ledger operations."""


class LiveReceiptLedgerConflictError(LiveReceiptLedgerError):
    """Raised when an immutable receipt ID is reused with different exact bytes."""


class LiveReceiptLedgerCorruptionError(LiveReceiptLedgerError):
    """Raised when stored metadata or bytes fail strict replay validation."""


class LiveReceiptLedgerQueryError(LiveReceiptLedgerError):
    """Raised when a replay query is malformed or outside bounded scope."""


class LiveReceiptLedgerUnavailableError(LiveReceiptLedgerError):
    """Stable internal refusal when no durable receipt ledger can be opened."""


class QuarantineReason(StrEnum):
    DEPLOYMENT_DISPATCH_INCONSISTENT = "DEPLOYMENT_DISPATCH_INCONSISTENT"
    RECOVERY_PERMIT_INVALID = "RECOVERY_PERMIT_INVALID"
    RESTORE_REQUIRED = "RESTORE_REQUIRED"
    RESTORE_UNRESOLVED = "RESTORE_UNRESOLVED"
    EXTERNAL_JOB_STATE_UNKNOWN = "EXTERNAL_JOB_STATE_UNKNOWN"


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StoredLiveReceipt(_Model):
    sequence_number: int = Field(ge=1)
    receipt_id: str = Field(pattern=r"^live-receipt:[a-f0-9]{64}$")
    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    gate_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    receipt_role: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    evidence_phase: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    terminal_at: datetime
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_size_bytes: int = Field(ge=1, le=MAX_RECEIPT_BYTES)
    receipt_document: bytes = Field(min_length=1, max_length=MAX_RECEIPT_BYTES)
    appended_at: datetime

    @model_validator(mode="after")
    def validate_record(self) -> StoredLiveReceipt:
        _require_utc(self.terminal_at, "terminal_at")
        _require_utc(self.appended_at, "appended_at")
        if len(self.receipt_document) != self.document_size_bytes:
            raise ValueError("receipt document size differs from stored metadata")
        if _sha256(self.receipt_document) != self.document_sha256:
            raise ValueError("receipt document digest differs from stored metadata")
        receipt = _parse_receipt_document(self.receipt_document)
        expected = _receipt_metadata(receipt)
        if (
            self.receipt_id != receipt.receipt_id
            or self.campaign_id != expected.campaign_id
            or self.gate_id != expected.gate_id
            or self.receipt_role != expected.receipt_role
            or self.evidence_phase != expected.evidence_phase
            or self.terminal_at != expected.terminal_at
            or self.payload_sha256 != receipt.payload_sha256
        ):
            raise ValueError("stored receipt metadata differs from exact receipt bytes")
        return self

    def parsed_receipt(self) -> SignedLiveReceipt:
        return _parse_receipt_document(self.receipt_document)


class QuarantineRecord(_Model):
    sequence_number: int = Field(ge=1)
    quarantine_id: str = Field(pattern=r"^live-quarantine:[a-f0-9]{64}$")
    campaign_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    org_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: QuarantineReason
    triggering_receipt_id: str | None = Field(default=None, pattern=r"^live-receipt:[a-f0-9]{64}$")
    recorded_at: datetime
    record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> QuarantineRecord:
        _require_utc(self.recorded_at, "recorded_at")
        body = self.model_dump(
            mode="json",
            exclude={"sequence_number", "quarantine_id", "record_sha256"},
        )
        if _sha256(_canonical_json(body)) != self.record_sha256:
            raise ValueError("quarantine record digest is invalid")
        if self.quarantine_id != f"live-quarantine:{self.record_sha256}":
            raise ValueError("quarantine identity is invalid")
        return self


@dataclass(frozen=True, slots=True)
class QuarantineState:
    quarantined: bool
    records: tuple[QuarantineRecord, ...]


@dataclass(frozen=True, slots=True)
class LiveReceiptLedgerSelection:
    mode: str
    ledger: LiveReceiptLedger
    degradation_code: str | None


class LiveReceiptLedger(Protocol):
    """Storage-only append/replay port with no acceptance or release operation."""

    def append(self, receipt_document: bytes) -> StoredLiveReceipt: ...

    def replay(
        self, *, campaign_id: str, gate_id: str | None = None
    ) -> tuple[StoredLiveReceipt, ...]: ...

    def quarantine(
        self,
        *,
        campaign_id: str,
        org_fingerprint_sha256: str,
        reason: QuarantineReason,
        triggering_receipt_id: str | None = None,
    ) -> QuarantineRecord: ...

    def quarantine_state(self, *, campaign_id: str) -> QuarantineState: ...


class UnavailableLiveReceiptLedger:
    """Non-authoritative fail-closed port used only to keep the API available.

    This adapter deliberately stores and returns nothing.  It is not an in-memory
    receipt fallback and therefore can never become acceptance evidence.
    """

    @staticmethod
    def _raise() -> Never:
        raise LiveReceiptLedgerUnavailableError("LIVE_RECEIPT_LEDGER_UNAVAILABLE")

    def append(self, receipt_document: bytes) -> StoredLiveReceipt:
        del receipt_document
        self._raise()

    def replay(
        self, *, campaign_id: str, gate_id: str | None = None
    ) -> tuple[StoredLiveReceipt, ...]:
        del campaign_id, gate_id
        self._raise()

    def quarantine(
        self,
        *,
        campaign_id: str,
        org_fingerprint_sha256: str,
        reason: QuarantineReason,
        triggering_receipt_id: str | None = None,
    ) -> QuarantineRecord:
        del campaign_id, org_fingerprint_sha256, reason, triggering_receipt_id
        self._raise()

    def quarantine_state(self, *, campaign_id: str) -> QuarantineState:
        del campaign_id
        self._raise()


@dataclass(frozen=True, slots=True)
class _ReceiptMetadata:
    campaign_id: str
    gate_id: str | None
    receipt_role: str
    evidence_phase: str | None
    terminal_at: datetime


def serialize_live_receipt(receipt: SignedLiveReceipt) -> bytes:
    """Return the one canonical JSON representation used by product receipt producers."""

    if not isinstance(receipt, SignedLiveReceipt):
        raise LiveReceiptLedgerCorruptionError("receipt must use the signed receipt contract")
    document = _canonical_json(receipt.model_dump(mode="json"))
    _parse_receipt_document(document)
    return document


def _parse_receipt_document(document: bytes) -> SignedLiveReceipt:
    if not isinstance(document, bytes) or not document or len(document) > MAX_RECEIPT_BYTES:
        raise LiveReceiptLedgerCorruptionError("receipt bytes are absent or exceed the bound")
    try:
        text = document.decode("utf-8")
        body = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(body, dict):
            raise TypeError("receipt document must be an object")
        require_no_sensitive_text(body)
        receipt = SignedLiveReceipt.model_validate(body)
        _require_storage_safe_vocabulary(receipt)
    except (
        SensitiveTextError,
        TypeError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise LiveReceiptLedgerCorruptionError(
            "receipt bytes failed strict, secret-safe contract validation"
        ) from exc
    return receipt


def _require_storage_safe_vocabulary(receipt: SignedLiveReceipt) -> None:
    """Exclude free-form text channels from the durable evidence store.

    Live org results belong behind digests in the receipt contract.  The ledger therefore
    admits only identifier/code vocabulary in fields that the broader transport model keeps
    as strings; it never attempts to redact and then persist an unsafe document.
    """

    payload = receipt.payload
    values: list[tuple[str, str, re.Pattern[str]]] = []
    if isinstance(payload, GateReceiptPayload):
        values.extend(("requirement ID", value, _IDENTIFIER) for value in payload.requirement_ids)
        values.extend(("capability ID", value, _IDENTIFIER) for value in payload.capability_ids)
        values.extend(("gap code", value, _ROLE) for value in payload.gaps)
    else:
        values.extend(
            ("authorized gate ID", value, _IDENTIFIER) for value in payload.authorized_gate_ids
        )
        values.extend(
            ("authorized effect class", value, _ROLE) for value in payload.authorized_effect_classes
        )
    if any(not pattern.fullmatch(value) for _, value, pattern in values):
        raise LiveReceiptLedgerCorruptionError(
            "receipt contains non-code text in a durable evidence field"
        )


def _receipt_metadata(receipt: SignedLiveReceipt) -> _ReceiptMetadata:
    payload = receipt.payload
    if isinstance(payload, GateReceiptPayload):
        return _ReceiptMetadata(
            campaign_id=payload.scope.campaign_id,
            gate_id=payload.gate_id,
            receipt_role=payload.receipt_type,
            evidence_phase=payload.evidence_phase.value,
            terminal_at=payload.terminal_at,
        )
    assert isinstance(payload, SupportingReceiptPayload)
    return _ReceiptMetadata(
        campaign_id=payload.scope.campaign_id,
        gate_id=None,
        receipt_role=payload.receipt_role,
        evidence_phase=payload.evidence_phase.value if payload.evidence_phase else None,
        terminal_at=payload.terminal_at,
    )


def _receipt_values(document: bytes) -> tuple[SignedLiveReceipt, _ReceiptMetadata, str]:
    receipt = _parse_receipt_document(document)
    return receipt, _receipt_metadata(receipt), _sha256(document)


SQLITE_LIVE_RECEIPT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_receipt_records (
    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    gate_id TEXT,
    receipt_role TEXT NOT NULL,
    evidence_phase TEXT,
    terminal_at_utc TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    document_size_bytes INTEGER NOT NULL,
    receipt_document BLOB NOT NULL,
    appended_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS live_receipt_campaign_sequence_idx
    ON live_receipt_records(campaign_id, sequence_number);
CREATE INDEX IF NOT EXISTS live_receipt_campaign_gate_sequence_idx
    ON live_receipt_records(campaign_id, gate_id, sequence_number);
CREATE TABLE IF NOT EXISTS live_campaign_quarantine (
    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL,
    org_fingerprint_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    triggering_receipt_id TEXT,
    recorded_at_utc TEXT NOT NULL,
    record_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS live_quarantine_campaign_sequence_idx
    ON live_campaign_quarantine(campaign_id, sequence_number);
CREATE TRIGGER IF NOT EXISTS live_receipt_records_no_update
BEFORE UPDATE ON live_receipt_records BEGIN
    SELECT RAISE(ABORT, 'live receipt ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS live_receipt_records_no_delete
BEFORE DELETE ON live_receipt_records BEGIN
    SELECT RAISE(ABORT, 'live receipt ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS live_campaign_quarantine_no_update
BEFORE UPDATE ON live_campaign_quarantine BEGIN
    SELECT RAISE(ABORT, 'live receipt ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS live_campaign_quarantine_no_delete
BEFORE DELETE ON live_campaign_quarantine BEGIN
    SELECT RAISE(ABORT, 'live receipt ledger is append-only');
END;
"""


class SQLiteLiveReceiptLedger:
    """Self-creating SQLite fallback with transactional append-only semantics."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
        except Exception:
            connection.close()
            raise
        return connection

    def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(SQLITE_LIVE_RECEIPT_SCHEMA_SQL)

    def append(self, receipt_document: bytes) -> StoredLiveReceipt:
        receipt, metadata, document_sha256 = _receipt_values(receipt_document)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM live_receipt_records WHERE receipt_id = ?",
                (receipt.receipt_id,),
            ).fetchone()
            if row is not None:
                existing = _sqlite_receipt_row(row)
                if (
                    existing.document_sha256 != document_sha256
                    or existing.receipt_document != receipt_document
                ):
                    raise LiveReceiptLedgerConflictError(
                        "receipt identity is bound to different exact bytes"
                    )
                return existing
            cursor = connection.execute(
                """INSERT INTO live_receipt_records (
                       receipt_id, campaign_id, gate_id, receipt_role, evidence_phase,
                       terminal_at_utc, payload_sha256, document_sha256,
                       document_size_bytes, receipt_document, appended_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.receipt_id,
                    metadata.campaign_id,
                    metadata.gate_id,
                    metadata.receipt_role,
                    metadata.evidence_phase,
                    _utc_text(metadata.terminal_at),
                    receipt.payload_sha256,
                    document_sha256,
                    len(receipt_document),
                    receipt_document,
                    _utc_text(datetime.now(UTC)),
                ),
            )
            row = connection.execute(
                "SELECT * FROM live_receipt_records WHERE sequence_number = ?",
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise LiveReceiptLedgerCorruptionError("receipt append produced no durable row")
            return _sqlite_receipt_row(row)

    def replay(
        self, *, campaign_id: str, gate_id: str | None = None
    ) -> tuple[StoredLiveReceipt, ...]:
        campaign, gate = _validate_replay_scope(campaign_id, gate_id)
        statement = "SELECT * FROM live_receipt_records WHERE campaign_id = ?"
        parameters: list[Any] = [campaign]
        if gate is not None:
            statement += " AND gate_id = ?"
            parameters.append(gate)
        statement += " ORDER BY sequence_number ASC LIMIT ?"
        parameters.append(MAX_REPLAY_RECEIPTS + 1)
        with closing(self._connect()) as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        if len(rows) > MAX_REPLAY_RECEIPTS:
            raise LiveReceiptLedgerQueryError("receipt replay exceeds the campaign bound")
        return tuple(_sqlite_receipt_row(row) for row in rows)

    def quarantine(
        self,
        *,
        campaign_id: str,
        org_fingerprint_sha256: str,
        reason: QuarantineReason,
        triggering_receipt_id: str | None = None,
    ) -> QuarantineRecord:
        values = _quarantine_values(
            campaign_id=campaign_id,
            org_fingerprint_sha256=org_fingerprint_sha256,
            reason=reason,
            triggering_receipt_id=triggering_receipt_id,
        )
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM live_campaign_quarantine WHERE quarantine_id = ?",
                (values["quarantine_id"],),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """INSERT INTO live_campaign_quarantine (
                           quarantine_id, campaign_id, org_fingerprint_sha256, reason,
                           triggering_receipt_id, recorded_at_utc, record_sha256
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    tuple(values[key] for key in _QUARANTINE_COLUMNS),
                )
                row = connection.execute(
                    "SELECT * FROM live_campaign_quarantine WHERE sequence_number = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            if row is None:
                raise LiveReceiptLedgerCorruptionError("quarantine append produced no durable row")
            return _sqlite_quarantine_row(row)

    def quarantine_state(self, *, campaign_id: str) -> QuarantineState:
        campaign = _validate_identifier(campaign_id, "campaign_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM live_campaign_quarantine
                   WHERE campaign_id = ? ORDER BY sequence_number ASC
                   LIMIT ?""",
                (campaign, MAX_REPLAY_RECEIPTS + 1),
            ).fetchall()
        if len(rows) > MAX_REPLAY_RECEIPTS:
            raise LiveReceiptLedgerQueryError("quarantine replay exceeds the campaign bound")
        records = tuple(_sqlite_quarantine_row(row) for row in rows)
        return QuarantineState(quarantined=bool(records), records=records)


POSTGRES_LIVE_RECEIPT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_receipt_records (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_id text NOT NULL UNIQUE,
    campaign_id text NOT NULL,
    gate_id text,
    receipt_role text NOT NULL,
    evidence_phase text,
    terminal_at_utc timestamptz NOT NULL,
    payload_sha256 text NOT NULL,
    document_sha256 text NOT NULL,
    document_size_bytes integer NOT NULL,
    receipt_document bytea NOT NULL,
    appended_at_utc timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS live_receipt_campaign_sequence_idx
    ON live_receipt_records(campaign_id, sequence_number);
CREATE INDEX IF NOT EXISTS live_receipt_campaign_gate_sequence_idx
    ON live_receipt_records(campaign_id, gate_id, sequence_number);
CREATE TABLE IF NOT EXISTS live_campaign_quarantine (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quarantine_id text NOT NULL UNIQUE,
    campaign_id text NOT NULL,
    org_fingerprint_sha256 text NOT NULL,
    reason text NOT NULL,
    triggering_receipt_id text,
    recorded_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL
);
CREATE INDEX IF NOT EXISTS live_quarantine_campaign_sequence_idx
    ON live_campaign_quarantine(campaign_id, sequence_number);
CREATE OR REPLACE FUNCTION reject_live_receipt_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'live receipt ledger is append-only';
END;
$$;
DROP TRIGGER IF EXISTS live_receipt_records_no_mutation ON live_receipt_records;
CREATE TRIGGER live_receipt_records_no_mutation
    BEFORE UPDATE OR DELETE ON live_receipt_records
    FOR EACH ROW EXECUTE FUNCTION reject_live_receipt_ledger_mutation();
DROP TRIGGER IF EXISTS live_receipt_records_no_truncate ON live_receipt_records;
CREATE TRIGGER live_receipt_records_no_truncate BEFORE TRUNCATE ON live_receipt_records
    FOR EACH STATEMENT EXECUTE FUNCTION reject_live_receipt_ledger_mutation();
DROP TRIGGER IF EXISTS live_campaign_quarantine_no_mutation ON live_campaign_quarantine;
CREATE TRIGGER live_campaign_quarantine_no_mutation
    BEFORE UPDATE OR DELETE ON live_campaign_quarantine
    FOR EACH ROW EXECUTE FUNCTION reject_live_receipt_ledger_mutation();
DROP TRIGGER IF EXISTS live_campaign_quarantine_no_truncate ON live_campaign_quarantine;
CREATE TRIGGER live_campaign_quarantine_no_truncate BEFORE TRUNCATE ON live_campaign_quarantine
    FOR EACH STATEMENT EXECUTE FUNCTION reject_live_receipt_ledger_mutation();
"""


class PostgresLiveReceiptLedger:
    """PostgreSQL primary adapter with byte-preserving append semantics."""

    def __init__(self, database_url: str, *, schema: str = DEFAULT_POSTGRES_SCHEMA) -> None:
        self.database_url = database_url
        self.schema = schema
        self.connection_string = scoped_connection_string(database_url, schema)

    def setup(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            initialize_postgres_schema(connection, self.schema)
            connection.execute(POSTGRES_LIVE_RECEIPT_SCHEMA_SQL)

    def append(self, receipt_document: bytes) -> StoredLiveReceipt:
        receipt, metadata, document_sha256 = _receipt_values(receipt_document)
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            row = connection.execute(
                """INSERT INTO live_receipt_records (
                       receipt_id, campaign_id, gate_id, receipt_role, evidence_phase,
                       terminal_at_utc, payload_sha256, document_sha256,
                       document_size_bytes, receipt_document, appended_at_utc
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (receipt_id) DO NOTHING RETURNING *""",
                (
                    receipt.receipt_id,
                    metadata.campaign_id,
                    metadata.gate_id,
                    metadata.receipt_role,
                    metadata.evidence_phase,
                    metadata.terminal_at,
                    receipt.payload_sha256,
                    document_sha256,
                    len(receipt_document),
                    receipt_document,
                    datetime.now(UTC),
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM live_receipt_records WHERE receipt_id = %s FOR SHARE",
                    (receipt.receipt_id,),
                ).fetchone()
            if row is None:
                raise LiveReceiptLedgerCorruptionError("receipt append produced no durable row")
            stored = _postgres_receipt_row(row)
            if (
                stored.document_sha256 != document_sha256
                or stored.receipt_document != receipt_document
            ):
                raise LiveReceiptLedgerConflictError(
                    "receipt identity is bound to different exact bytes"
                )
            return stored

    def replay(
        self, *, campaign_id: str, gate_id: str | None = None
    ) -> tuple[StoredLiveReceipt, ...]:
        campaign, gate = _validate_replay_scope(campaign_id, gate_id)
        statement = "SELECT * FROM live_receipt_records WHERE campaign_id = %s"
        parameters: list[Any] = [campaign]
        if gate is not None:
            statement += " AND gate_id = %s"
            parameters.append(gate)
        statement += " ORDER BY sequence_number ASC LIMIT %s"
        parameters.append(MAX_REPLAY_RECEIPTS + 1)
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        if len(rows) > MAX_REPLAY_RECEIPTS:
            raise LiveReceiptLedgerQueryError("receipt replay exceeds the campaign bound")
        return tuple(_postgres_receipt_row(row) for row in rows)

    def quarantine(
        self,
        *,
        campaign_id: str,
        org_fingerprint_sha256: str,
        reason: QuarantineReason,
        triggering_receipt_id: str | None = None,
    ) -> QuarantineRecord:
        values = _quarantine_values(
            campaign_id=campaign_id,
            org_fingerprint_sha256=org_fingerprint_sha256,
            reason=reason,
            triggering_receipt_id=triggering_receipt_id,
        )
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            row = connection.execute(
                """INSERT INTO live_campaign_quarantine (
                       quarantine_id, campaign_id, org_fingerprint_sha256, reason,
                       triggering_receipt_id, recorded_at_utc, record_sha256
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (quarantine_id) DO NOTHING RETURNING *""",
                tuple(values[key] for key in _QUARANTINE_COLUMNS),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM live_campaign_quarantine WHERE quarantine_id = %s FOR SHARE",
                    (values["quarantine_id"],),
                ).fetchone()
            if row is None:
                raise LiveReceiptLedgerCorruptionError("quarantine append produced no durable row")
            return _postgres_quarantine_row(row)

    def quarantine_state(self, *, campaign_id: str) -> QuarantineState:
        campaign = _validate_identifier(campaign_id, "campaign_id")
        with psycopg.connect(self.connection_string, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT * FROM live_campaign_quarantine
                   WHERE campaign_id = %s ORDER BY sequence_number ASC LIMIT %s""",
                (campaign, MAX_REPLAY_RECEIPTS + 1),
            ).fetchall()
        if len(rows) > MAX_REPLAY_RECEIPTS:
            raise LiveReceiptLedgerQueryError("quarantine replay exceeds the campaign bound")
        records = tuple(_postgres_quarantine_row(row) for row in rows)
        return QuarantineState(quarantined=bool(records), records=records)


def open_live_receipt_ledger(
    *,
    database_url: str | None,
    sqlite_path: Path,
    postgres_schema: str = DEFAULT_POSTGRES_SCHEMA,
) -> LiveReceiptLedgerSelection:
    """Open durable storage without allowing a ledger outage to abort API startup.

    PostgreSQL is preferred and SQLite is a byte-preserving durable fallback.  If
    neither can be initialized, the returned unavailable port refuses every operation;
    it never substitutes process memory for acceptance authority.
    """

    if database_url:
        try:
            primary = PostgresLiveReceiptLedger(database_url, schema=postgres_schema)
            primary.setup()
        except Exception:
            sqlite_degradation = "POSTGRES_UNAVAILABLE"
        else:
            return LiveReceiptLedgerSelection(
                mode="POSTGRESQL", ledger=primary, degradation_code=None
            )
    else:
        sqlite_degradation = "POSTGRES_NOT_CONFIGURED"

    try:
        # SQLiteLiveReceiptLedger initializes and verifies its schema in __init__.
        fallback = SQLiteLiveReceiptLedger(sqlite_path)
    except Exception:
        return LiveReceiptLedgerSelection(
            mode="UNAVAILABLE",
            ledger=UnavailableLiveReceiptLedger(),
            degradation_code="LIVE_RECEIPT_LEDGER_UNAVAILABLE",
        )
    return LiveReceiptLedgerSelection(
        mode="SQLITE", ledger=fallback, degradation_code=sqlite_degradation
    )


def _sqlite_receipt_row(row: Mapping[str, Any]) -> StoredLiveReceipt:
    try:
        return StoredLiveReceipt(
            sequence_number=row["sequence_number"],
            receipt_id=row["receipt_id"],
            campaign_id=row["campaign_id"],
            gate_id=row["gate_id"],
            receipt_role=row["receipt_role"],
            evidence_phase=row["evidence_phase"],
            terminal_at=_parse_utc(row["terminal_at_utc"]),
            payload_sha256=row["payload_sha256"],
            document_sha256=row["document_sha256"],
            document_size_bytes=row["document_size_bytes"],
            receipt_document=bytes(row["receipt_document"]),
            appended_at=_parse_utc(row["appended_at_utc"]),
        )
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise LiveReceiptLedgerCorruptionError(
            "persisted receipt row failed replay validation"
        ) from exc


def _postgres_receipt_row(row: Mapping[str, Any]) -> StoredLiveReceipt:
    try:
        normalized = dict(row)
        terminal = normalized["terminal_at_utc"]
        appended = normalized["appended_at_utc"]
        return StoredLiveReceipt(
            sequence_number=normalized["sequence_number"],
            receipt_id=normalized["receipt_id"],
            campaign_id=normalized["campaign_id"],
            gate_id=normalized.get("gate_id"),
            receipt_role=normalized["receipt_role"],
            evidence_phase=normalized.get("evidence_phase"),
            terminal_at=(terminal if isinstance(terminal, datetime) else _parse_utc(terminal)),
            payload_sha256=normalized["payload_sha256"],
            document_sha256=normalized["document_sha256"],
            document_size_bytes=normalized["document_size_bytes"],
            receipt_document=bytes(normalized["receipt_document"]),
            appended_at=(appended if isinstance(appended, datetime) else _parse_utc(appended)),
        )
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise LiveReceiptLedgerCorruptionError(
            "persisted receipt row failed replay validation"
        ) from exc


_QUARANTINE_COLUMNS = (
    "quarantine_id",
    "campaign_id",
    "org_fingerprint_sha256",
    "reason",
    "triggering_receipt_id",
    "recorded_at_utc",
    "record_sha256",
)


def _quarantine_values(
    *,
    campaign_id: str,
    org_fingerprint_sha256: str,
    reason: QuarantineReason,
    triggering_receipt_id: str | None,
) -> dict[str, Any]:
    campaign = _validate_identifier(campaign_id, "campaign_id")
    if not _HEX64.fullmatch(org_fingerprint_sha256):
        raise LiveReceiptLedgerQueryError("org fingerprint is invalid")
    if not isinstance(reason, QuarantineReason):
        raise LiveReceiptLedgerQueryError("quarantine reason is invalid")
    if triggering_receipt_id is not None and not _RECEIPT_ID.fullmatch(triggering_receipt_id):
        raise LiveReceiptLedgerQueryError("triggering receipt ID is invalid")
    recorded_at = datetime.now(UTC)
    body = {
        "campaign_id": campaign,
        "org_fingerprint_sha256": org_fingerprint_sha256,
        "reason": reason.value,
        "triggering_receipt_id": triggering_receipt_id,
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
    }
    record_sha256 = _sha256(_canonical_json(body))
    return {
        "quarantine_id": f"live-quarantine:{record_sha256}",
        "campaign_id": campaign,
        "org_fingerprint_sha256": org_fingerprint_sha256,
        "reason": reason.value,
        "triggering_receipt_id": triggering_receipt_id,
        "recorded_at_utc": _utc_text(recorded_at),
        "record_sha256": record_sha256,
    }


def _sqlite_quarantine_row(row: Mapping[str, Any]) -> QuarantineRecord:
    try:
        return _quarantine_record(dict(row))
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise LiveReceiptLedgerCorruptionError(
            "persisted quarantine row failed replay validation"
        ) from exc


def _postgres_quarantine_row(row: Mapping[str, Any]) -> QuarantineRecord:
    try:
        return _quarantine_record(dict(row))
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise LiveReceiptLedgerCorruptionError(
            "persisted quarantine row failed replay validation"
        ) from exc


def _quarantine_record(row: dict[str, Any]) -> QuarantineRecord:
    recorded = row["recorded_at_utc"]
    return QuarantineRecord(
        sequence_number=row["sequence_number"],
        quarantine_id=row["quarantine_id"],
        campaign_id=row["campaign_id"],
        org_fingerprint_sha256=row["org_fingerprint_sha256"],
        reason=row["reason"],
        triggering_receipt_id=row.get("triggering_receipt_id"),
        recorded_at=recorded if isinstance(recorded, datetime) else _parse_utc(recorded),
        record_sha256=row["record_sha256"],
    )


def _validate_replay_scope(campaign_id: str, gate_id: str | None) -> tuple[str, str | None]:
    campaign = _validate_identifier(campaign_id, "campaign_id")
    if gate_id is not None and not _GATE_ID.fullmatch(gate_id):
        raise LiveReceiptLedgerQueryError("gate_id is invalid")
    return campaign, gate_id


def _validate_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise LiveReceiptLedgerQueryError(f"{field} is invalid")
    return value


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiveReceiptLedgerCorruptionError("receipt contains a duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_text(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return parse_aware_utc(value)


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be timezone-aware UTC")


__all__ = [
    "LiveReceiptLedger",
    "LiveReceiptLedgerConflictError",
    "LiveReceiptLedgerCorruptionError",
    "LiveReceiptLedgerError",
    "LiveReceiptLedgerQueryError",
    "LiveReceiptLedgerSelection",
    "LiveReceiptLedgerUnavailableError",
    "MAX_RECEIPT_BYTES",
    "MAX_REPLAY_RECEIPTS",
    "POSTGRES_LIVE_RECEIPT_SCHEMA_SQL",
    "PostgresLiveReceiptLedger",
    "QuarantineReason",
    "QuarantineRecord",
    "QuarantineState",
    "SQLITE_LIVE_RECEIPT_SCHEMA_SQL",
    "SQLiteLiveReceiptLedger",
    "StoredLiveReceipt",
    "UnavailableLiveReceiptLedger",
    "open_live_receipt_ledger",
    "serialize_live_receipt",
]
