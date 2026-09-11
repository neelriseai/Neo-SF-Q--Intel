"""Offset portability without org calls or changing the machine/database timezone."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.classification_bootstrap import (
    CLASSIFICATION_OPERATION_PLAN_SHA256,
    ClassificationAuthority,
)
from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest, EvidenceState
from neo_sf_q_intel.execution_assertions import (
    ExecutionAssertionArtifact,
    StoredExecutionAssertion,
    serialize_execution_assertion,
)
from neo_sf_q_intel.governance import _valid_evidence_at_release
from neo_sf_q_intel.live_read_evidence import HostOwnedLiveReadExecutor, _parse_time
from neo_sf_q_intel.live_receipt_ledger import (
    SQLiteLiveReceiptLedger,
    StoredLiveReceipt,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipt_producer import TrustedLiveReceiptProducer
from neo_sf_q_intel.live_receipts import SignedLiveReceipt
from neo_sf_q_intel.live_target_plan import _parse_timestamp
from neo_sf_q_intel.local_validation_runner import _utc_now
from neo_sf_q_intel.repository import SQLiteRunRepository
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.temporal import aware_utc, parse_aware_utc
from tests.test_execution_assertions import _artifact
from tests.test_live_receipts import _build_bundle
from tests.test_live_target_plan import T0 as PLAN_TIME
from tests.test_live_target_plan import _producer, _profile, _scope

T0 = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
OFFSETS = (timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-7)), UTC)


@pytest.mark.parametrize("zone", OFFSETS)
def test_explicit_source_offsets_compare_the_same_instant(zone):
    stamp = T0.astimezone(zone)
    assert aware_utc(stamp) == T0
    for parser in (parse_aware_utc, _parse_time, _parse_timestamp):
        parsed = parser(stamp.isoformat())
        assert parsed == T0 and parsed.tzinfo is UTC


@pytest.mark.parametrize("text", ["2030-01-01T12:00:00", "2030-01-01", "2030-01-01T12:00:00-00:00"])
@pytest.mark.parametrize("parser", [parse_aware_utc, _parse_time, _parse_timestamp])
def test_naive_or_unknown_offset_text_never_uses_machine_timezone(parser, text):
    with pytest.raises(ValueError):
        parser(text)


@pytest.mark.parametrize("zone", OFFSETS)
def test_host_live_and_local_clocks_accept_offsets_and_reject_naive(zone):
    stamp = T0.astimezone(zone)
    for value in (stamp, T0.replace(tzinfo=None)):
        host = SimpleNamespace(clock=lambda value=value: value, _clock=lambda value=value: value)
        operations = (
            lambda host=host: HostOwnedLiveReadExecutor._now(host),
            lambda host=host: TrustedLiveReceiptProducer._now(host),
            lambda value=value: _utc_now(lambda: value),
        )
        for operation in operations:
            if value.tzinfo is None:
                with pytest.raises(RuntimeError):
                    operation()
            else:
                assert operation() == T0 and operation().tzinfo is UTC


@pytest.mark.parametrize("zone", OFFSETS)
def test_plan_generation_is_offset_invariant_and_naive_clock_fails(zone):
    scope = _scope()
    producer, _ = _producer(_profile(scope), scope)
    original = producer.produce()
    producer._clock = lambda: PLAN_TIME.astimezone(zone)
    assert producer.produce().model_dump(mode="json") == original.model_dump(mode="json")
    producer._clock = lambda: PLAN_TIME.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        producer.produce()


def _offset_times(value, zone):
    if isinstance(value, datetime):
        return value.astimezone(zone)
    if isinstance(value, dict):
        return {key: _offset_times(item, zone) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_offset_times(item, zone) for item in value)
    return value


@pytest.mark.parametrize("zone", OFFSETS)
def test_offset_normalization_preserves_receipt_ids_signatures_and_canonical_bytes(zone):
    _, _, receipts, registry = _build_bundle()
    for original in receipts:
        before = serialize_live_receipt(original)
        parsed = SignedLiveReceipt.model_validate(
            _offset_times(original.model_dump(mode="python"), zone)
        )
        assert serialize_live_receipt(parsed) == before
        assert parsed.receipt_id == original.receipt_id
        assert registry.verify(parsed) is None
        assert parsed.payload.issued_at.tzinfo is UTC


@pytest.mark.parametrize("zone", OFFSETS)
def test_assertion_and_postgres_row_metadata_normalize_without_rewriting_evidence(zone):
    original = _artifact()
    document = serialize_execution_assertion(original)
    restored = ExecutionAssertionArtifact.model_validate(
        _offset_times(original.model_dump(mode="python", exclude_computed_fields=True), zone)
    )
    assert serialize_execution_assertion(restored) == document
    stored = StoredExecutionAssertion(
        sequence_number=1,
        assertion_artifact_id="execution-assertion:" + hashlib.sha256(document).hexdigest(),
        campaign_id=original.scope.campaign_id,
        gate_id=original.gate_id,
        evidence_phase=original.evidence_phase,
        runner_id=original.runner_id,
        execution_id=original.execution_id,
        terminal_at=original.terminal_at.astimezone(zone),
        document_sha256=hashlib.sha256(document).hexdigest(),
        document_size_bytes=len(document),
        assertion_document=document,
        appended_at=datetime.now(UTC).astimezone(zone),
    )
    assert stored.terminal_at.tzinfo is UTC
    assert stored.assertion_document == document


def test_ledger_rehydrates_offset_metadata_preserving_exact_document(tmp_path):
    _, _, receipts, _ = _build_bundle()
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.db")
    ledger.setup()
    stored = ledger.append(serialize_live_receipt(receipts[0]))
    restored = StoredLiveReceipt.model_validate(
        _offset_times(stored.model_dump(mode="python"), OFFSETS[0])
    )
    assert restored.receipt_document == stored.receipt_document
    assert restored.terminal_at.tzinfo is UTC and restored.appended_at.tzinfo is UTC
    body = stored.model_dump(mode="python")
    body["terminal_at"] = body["terminal_at"].replace(tzinfo=None)
    with pytest.raises(ValidationError):
        StoredLiveReceipt.model_validate(body)


class _FoldZone(tzinfo):
    """A fall-back clock without dependence on OS/IANA timezone data installation."""

    def utcoffset(self, value):
        return timedelta(hours=-5 if value.fold else -4)

    def dst(self, value):
        return timedelta(0)


def test_authority_chronology_compares_instants_across_fold():
    zone = _FoldZone()
    issued = datetime(2030, 11, 3, 1, 30, tzinfo=zone, fold=0)
    expires = datetime(2030, 11, 3, 1, 15, tzinfo=zone, fold=1)
    authority = ClassificationAuthority(
        schema_version="1.0.0",
        authority_class="FIXED_SYSTEM_CLASSIFICATION_ONLY",
        task_authority_sha256="a" * 64,
        pins_sha256="b" * 64,
        operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
        issued_at=issued,
        expires_at=expires,
        maximum_response_bytes=1024,
    )
    assert authority.expires_at - authority.issued_at == timedelta(minutes=45)
    assert authority.issued_at.tzinfo is UTC
    body = authority.model_dump(mode="python")
    body.update(issued_at=expires, expires_at=issued)
    with pytest.raises(ValidationError):
        ClassificationAuthority.model_validate(body)


@pytest.mark.parametrize(
    "expiry,valid",
    [
        ("2099-01-01T00:00:00", False),
        ("2099-01-01T00:00:00-00:00", False),
        ("2099-01-01T00:00:00+05:30", True),
        ("2000-01-01T00:00:00-07:00", False),
    ],
)
def test_graph_evidence_expiry_rejects_ambiguous_and_accepts_aware(expiry, valid):
    receipt = {
        "kind": "graph-edge",
        "evidence_state": EvidenceState.CONFIRMED,
        "source_hash": "abc",
        "source_snapshot": "s",
        "valid_until": expiry,
    }
    evidence = SimpleNamespace(
        state=EvidenceState.CONFIRMED,
        attributes={"source_hash": "abc", "snapshot_id": "s", "relevance_receipt": receipt},
    )
    assert _valid_evidence_at_release(evidence) is valid
    record = {
        "id": "node",
        "source": "source.xml",
        "extractorId": "extractor",
        "evidenceState": EvidenceState.CONFIRMED,
        "sourceHash": "abc",
        "sourceSnapshot": "s",
        "validUntil": expiry,
    }
    retriever = SimpleNamespace(source_graph_sha256="abc", source_snapshot="s")
    gaps = EvidenceRetriever._record_trust_gaps(retriever, record, record_type="NODE")
    assert (not gaps) is valid


def _run(created_at):
    return AssuranceRun(
        created_at=created_at,
        request=ChangeRequest(requirement="Assess changed metadata", project_id="test-project"),
        reasoning_policy_version="1.1.0",
        reasoning_policy_sha256="0" * 64,
        reasoning_eval_set_id="test-eval",
        reasoning_eval_set_sha256="1" * 64,
        source_snapshot="test-snapshot",
        source_graph_sha256="2" * 64,
        ontology_id="test-ontology",
        ontology_version="1.0.0",
        ontology_sha256="3" * 64,
        source_profile_id="test-profile",
        source_profile_version="1.0.0",
        source_profile_sha256="4" * 64,
        normalized_graph_sha256="5" * 64,
    )


def test_sqlite_orders_mixed_offsets_by_instant_and_preserves_run_documents(tmp_path):
    repository = SQLiteRunRepository(tmp_path / "runs.db")
    repository.setup()
    earlier = _run(datetime.fromisoformat("2030-01-02T01:00:00+05:30"))
    later = _run(datetime.fromisoformat("2030-01-01T22:00:00+00:00"))
    repository.save(earlier)
    repository.save(later)
    assert [run.run_id for run in repository.list_recent()] == [later.run_id, earlier.run_id]
    assert repository.get(earlier.run_id).model_dump_json() == earlier.model_dump_json()
    with sqlite3.connect(repository.path) as connection:
        values = connection.execute("SELECT created_at FROM assurance_runs").fetchall()
    assert all(value[0].endswith("+00:00") for value in values)
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.save(_run(T0.replace(tzinfo=None)))
    assert len(repository.list_recent()) == 2
