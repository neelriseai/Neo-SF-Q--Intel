from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from neo_sf_q_intel.live_receipt_ledger import (
    MAX_RECEIPT_BYTES,
    POSTGRES_LIVE_RECEIPT_SCHEMA_SQL,
    SQLITE_LIVE_RECEIPT_SCHEMA_SQL,
    LiveReceiptLedgerConflictError,
    LiveReceiptLedgerCorruptionError,
    LiveReceiptLedgerQueryError,
    LiveReceiptLedgerUnavailableError,
    PostgresLiveReceiptLedger,
    QuarantineReason,
    SQLiteLiveReceiptLedger,
    UnavailableLiveReceiptLedger,
    open_live_receipt_ledger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipts import (
    GateReceiptPayload,
    ReceiptOutcome,
    SignedLiveReceipt,
    sign_live_receipt,
)
from tests.test_live_receipts import PRODUCT_KEY, _build_bundle


def _gate(receipts: list[SignedLiveReceipt], gate_id: str) -> SignedLiveReceipt:
    return next(
        receipt
        for receipt in receipts
        if isinstance(receipt.payload, GateReceiptPayload) and receipt.payload.gate_id == gate_id
    )


def _receipt_documents() -> tuple[bytes, bytes]:
    _, _, receipts, _ = _build_bundle()
    return (
        serialize_live_receipt(_gate(receipts, "SF-L01")),
        serialize_live_receipt(_gate(receipts, "SF-L02")),
    )


def test_sqlite_setup_is_idempotent_and_replay_preserves_exact_ordered_bytes(
    tmp_path: Path,
) -> None:
    first, second = _receipt_documents()
    ledger = SQLiteLiveReceiptLedger(tmp_path / "state" / "receipts.sqlite3")
    ledger.setup()

    stored_first = ledger.append(first)
    stored_second = ledger.append(second)

    replay = ledger.replay(campaign_id="campaign-001")
    assert [item.sequence_number for item in replay] == [
        stored_first.sequence_number,
        stored_second.sequence_number,
    ]
    assert [item.receipt_document for item in replay] == [first, second]
    assert replay[0].parsed_receipt().receipt_id == stored_first.receipt_id
    assert replay[0].appended_at >= replay[0].terminal_at
    assert ledger.replay(campaign_id="campaign-001", gate_id="SF-L02") == (stored_second,)


def test_exact_duplicate_is_idempotent_but_same_identity_different_bytes_conflicts(
    tmp_path: Path,
) -> None:
    _, _, receipts, _ = _build_bundle()
    receipt = _gate(receipts, "SF-L01")
    exact = serialize_live_receipt(receipt)
    forged = serialize_live_receipt(receipt.model_copy(update={"signature_sha256": "0" * 64}))
    assert exact != forged
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    first = ledger.append(exact)
    duplicate = ledger.append(exact)

    assert duplicate == first
    assert len(ledger.replay(campaign_id="campaign-001")) == 1
    with pytest.raises(
        LiveReceiptLedgerConflictError,
        match="identity is bound to different exact bytes",
    ):
        ledger.append(forged)


def test_concurrent_exact_duplicate_has_one_durable_sequence(tmp_path: Path) -> None:
    document, _ = _receipt_documents()
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: ledger.append(document), range(16)))

    assert {item.sequence_number for item in records} == {1}
    assert len(ledger.replay(campaign_id="campaign-001")) == 1


def test_concurrent_identity_conflict_never_creates_two_rows(tmp_path: Path) -> None:
    _, _, receipts, _ = _build_bundle()
    receipt = _gate(receipts, "SF-L01")
    documents = (
        serialize_live_receipt(receipt),
        serialize_live_receipt(receipt.model_copy(update={"signature_sha256": "0" * 64})),
    )
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    def append(document: bytes) -> str:
        try:
            ledger.append(document)
        except LiveReceiptLedgerConflictError:
            return "CONFLICT"
        return "STORED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, documents))

    assert sorted(outcomes) == ["CONFLICT", "STORED"]
    assert len(ledger.replay(campaign_id="campaign-001")) == 1


@pytest.mark.parametrize(
    "sensitive_gap",
    [
        r"C:\\Users\\operator\\secret.txt",
        "https://example.test/secur/frontdoor.jsp?sid=00Dxx!very-secret-token",
        "api_key=super-secret-value",
    ],
)
def test_sensitive_receipt_content_is_rejected_before_persistence(
    tmp_path: Path, sensitive_gap: str
) -> None:
    _, _, receipts, _ = _build_bundle()
    original = _gate(receipts, "SF-L01")
    payload = original.payload.model_copy(
        update={"outcome": ReceiptOutcome.FAILED, "gaps": (sensitive_gap,)}
    )
    unsafe = sign_live_receipt(
        payload,
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    with pytest.raises(
        LiveReceiptLedgerCorruptionError,
        match="secret-safe contract validation",
    ):
        ledger.append(json.dumps(unsafe.model_dump(mode="json"), separators=(",", ":")).encode())
    assert ledger.replay(campaign_id="campaign-001") == ()


def test_raw_org_payload_cannot_use_free_form_gap_as_storage_channel(
    tmp_path: Path,
) -> None:
    _, _, receipts, _ = _build_bundle()
    original = _gate(receipts, "SF-L01")
    payload = original.payload.model_copy(
        update={
            "outcome": ReceiptOutcome.FAILED,
            "gaps": ('{"Account":{"Name":"Example Customer"}}',),
        }
    )
    receipt = sign_live_receipt(
        payload,
        issuer_id="product-receipt-issuer",
        hmac_key=PRODUCT_KEY,
    )
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    with pytest.raises(
        LiveReceiptLedgerCorruptionError,
        match="non-code text in a durable evidence field",
    ):
        ledger.append(json.dumps(receipt.model_dump(mode="json"), separators=(",", ":")).encode())
    assert ledger.replay(campaign_id="campaign-001") == ()


def test_unknown_key_and_duplicate_json_key_are_rejected(tmp_path: Path) -> None:
    document, _ = _receipt_documents()
    body = json.loads(document)
    body["signing_key"] = "not-permitted"
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    with pytest.raises(LiveReceiptLedgerCorruptionError):
        ledger.append(json.dumps(body).encode())
    with pytest.raises(LiveReceiptLedgerCorruptionError):
        ledger.append(document[:-1] + b',"issuer_id":"second"}')
    with pytest.raises(LiveReceiptLedgerCorruptionError, match="exceed the bound"):
        ledger.append(b"{" + (b"x" * MAX_RECEIPT_BYTES) + b"}")


def test_database_triggers_refuse_update_and_delete(tmp_path: Path) -> None:
    document, _ = _receipt_documents()
    path = tmp_path / "receipts.sqlite3"
    ledger = SQLiteLiveReceiptLedger(path)
    ledger.append(document)

    with (
        sqlite3.connect(path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute(
            "UPDATE live_receipt_records SET document_sha256 = ?",
            ("0" * 64,),
        )
    with (
        sqlite3.connect(path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM live_receipt_records")


def test_replay_detects_metadata_tamper_even_if_database_trigger_is_removed(
    tmp_path: Path,
) -> None:
    document, _ = _receipt_documents()
    path = tmp_path / "receipts.sqlite3"
    ledger = SQLiteLiveReceiptLedger(path)
    ledger.append(document)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER live_receipt_records_no_update")
        connection.execute("UPDATE live_receipt_records SET campaign_id = ?", ("campaign-forged",))

    with pytest.raises(
        LiveReceiptLedgerCorruptionError,
        match="failed replay validation",
    ):
        ledger.replay(campaign_id="campaign-forged")


def test_quarantine_is_append_only_and_never_clears_itself(tmp_path: Path) -> None:
    document, _ = _receipt_documents()
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")
    stored = ledger.append(document)

    record = ledger.quarantine(
        campaign_id="campaign-001",
        org_fingerprint_sha256="7" * 64,
        reason=QuarantineReason.RESTORE_REQUIRED,
        triggering_receipt_id=stored.receipt_id,
    )
    state = ledger.quarantine_state(campaign_id="campaign-001")

    assert state.quarantined is True
    assert state.records == (record,)
    assert not hasattr(ledger, "unquarantine")
    with (
        sqlite3.connect(ledger.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM live_campaign_quarantine")


def test_replay_scope_is_strict_and_bounded(tmp_path: Path) -> None:
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    for campaign, gate in (
        ("", None),
        ("campaign/other", None),
        ("campaign-001", ""),
        ("campaign-001", "../../SF-L01"),
    ):
        with pytest.raises(LiveReceiptLedgerQueryError):
            ledger.replay(campaign_id=campaign, gate_id=gate)


def test_repository_has_no_acceptance_or_release_promotion_surface(tmp_path: Path) -> None:
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")

    assert not hasattr(ledger, "accept")
    assert not hasattr(ledger, "promote")
    assert not hasattr(ledger, "release")
    assert "release_eligible" not in SQLITE_LIVE_RECEIPT_SCHEMA_SQL
    assert "requirements_satisfied" not in POSTGRES_LIVE_RECEIPT_SCHEMA_SQL


def test_postgres_schema_and_append_sql_preserve_primary_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, receipts, _ = _build_bundle()
    receipt = _gate(receipts, "SF-L01")
    document = serialize_live_receipt(receipt)
    conflict_document = serialize_live_receipt(
        receipt.model_copy(update={"signature_sha256": "0" * 64})
    )
    statements: list[tuple[str, tuple[object, ...] | None]] = []
    stored_row: dict[str, object] | None = None

    class Result:
        def __init__(self, row: dict[str, object] | None = None) -> None:
            self.row = row

        def fetchone(self):
            return self.row

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, statement: object, parameters=None):
            nonlocal stored_row
            rendered = statement.as_string() if hasattr(statement, "as_string") else str(statement)
            params = tuple(parameters) if parameters is not None else None
            statements.append((rendered, params))
            if "INSERT INTO live_receipt_records" in rendered:
                if stored_row is not None:
                    return Result(None)
                assert params is not None
                stored_row = {
                    "sequence_number": 1,
                    "receipt_id": params[0],
                    "campaign_id": params[1],
                    "gate_id": params[2],
                    "receipt_role": params[3],
                    "evidence_phase": params[4],
                    "terminal_at_utc": params[5],
                    "payload_sha256": params[6],
                    "document_sha256": params[7],
                    "document_size_bytes": params[8],
                    "receipt_document": params[9],
                    "appended_at_utc": params[10],
                }
                return Result(stored_row)
            if "WHERE receipt_id" in rendered:
                return Result(stored_row)
            return Result()

    connection = Connection()
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    ledger = PostgresLiveReceiptLedger("postgresql://redacted")

    ledger.setup()
    first = ledger.append(document)
    duplicate = ledger.append(document)

    assert first == duplicate
    with pytest.raises(LiveReceiptLedgerConflictError):
        ledger.append(conflict_document)
    insert_statements = [
        item for item, _ in statements if "INSERT INTO live_receipt_records" in item
    ]
    assert insert_statements
    assert all("ON CONFLICT (receipt_id) DO NOTHING" in item for item in insert_statements)
    assert statements[0][0].startswith("SELECT pg_advisory_xact_lock")
    assert statements[1][0] == 'CREATE SCHEMA IF NOT EXISTS "neo_sf_q_intel"'
    assert statements[2][0] == 'SET search_path TO "neo_sf_q_intel"'
    assert "receipt_document bytea NOT NULL" in POSTGRES_LIVE_RECEIPT_SCHEMA_SQL
    assert "BEFORE UPDATE OR DELETE" in POSTGRES_LIVE_RECEIPT_SCHEMA_SQL
    assert "BEFORE TRUNCATE" in POSTGRES_LIVE_RECEIPT_SCHEMA_SQL


def test_backend_selection_falls_back_for_postgres_connectivity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        PostgresLiveReceiptLedger,
        "setup",
        lambda self: (_ for _ in ()).throw(psycopg.OperationalError("unavailable")),
    )

    selection = open_live_receipt_ledger(
        database_url="postgresql://redacted",
        sqlite_path=tmp_path / "fallback.sqlite3",
    )

    assert selection.mode == "SQLITE"
    assert selection.degradation_code == "POSTGRES_UNAVAILABLE"
    assert (tmp_path / "fallback.sqlite3").is_file()
    with sqlite3.connect(tmp_path / "fallback.sqlite3") as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"live_receipt_records", "live_campaign_quarantine"} <= table_names


def test_backend_selection_falls_back_from_postgres_setup_failure_without_accepting_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        PostgresLiveReceiptLedger,
        "setup",
        lambda self: (_ for _ in ()).throw(
            LiveReceiptLedgerCorruptionError("schema integrity failure")
        ),
    )

    selection = open_live_receipt_ledger(
        database_url="postgresql://redacted",
        sqlite_path=tmp_path / "fallback.sqlite3",
    )

    assert selection.mode == "SQLITE"
    assert selection.degradation_code == "POSTGRES_UNAVAILABLE"
    assert isinstance(selection.ledger, SQLiteLiveReceiptLedger)


@pytest.mark.parametrize("database_url", [None, "postgresql://redacted"])
@pytest.mark.parametrize(
    ("failure_type", "failure_message"),
    [
        (sqlite3.DatabaseError, "database disk image is malformed"),
        (PermissionError, "ledger directory is read-only"),
    ],
)
def test_backend_selection_is_explicitly_unavailable_when_sqlite_setup_fails(
    database_url: str | None,
    failure_type: type[Exception],
    failure_message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if database_url:
        monkeypatch.setattr(
            PostgresLiveReceiptLedger,
            "setup",
            lambda self: (_ for _ in ()).throw(RuntimeError("primary setup failed")),
        )
    monkeypatch.setattr(
        SQLiteLiveReceiptLedger,
        "setup",
        lambda self: (_ for _ in ()).throw(failure_type(failure_message)),
    )

    selection = open_live_receipt_ledger(
        database_url=database_url,
        sqlite_path=tmp_path / "corrupt.sqlite3",
    )

    assert selection.mode == "UNAVAILABLE"
    assert selection.degradation_code == "LIVE_RECEIPT_LEDGER_UNAVAILABLE"
    assert isinstance(selection.ledger, UnavailableLiveReceiptLedger)
    with pytest.raises(
        LiveReceiptLedgerUnavailableError,
        match="LIVE_RECEIPT_LEDGER_UNAVAILABLE",
    ):
        selection.ledger.replay(campaign_id="campaign-safe")
