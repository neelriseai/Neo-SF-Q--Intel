from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from neo_sf_q_intel.domain import (
    AssuranceRun,
    ChangeRequest,
    EvidenceRef,
    EvidenceState,
    ImpactFinding,
    RiskSeverity,
)
from neo_sf_q_intel.outcome_repository import (
    MAX_QUERY_LIMIT,
    POSTGRES_OUTCOME_SCHEMA_SQL,
    InMemoryOutcomeRepository,
    JsonOutcomeRepository,
    OutcomeAppendRequest,
    OutcomeConflictError,
    OutcomeCorruptionError,
    OutcomePredecessorError,
    OutcomeQueryError,
    OutcomeReplayError,
    PostgresOutcomeRepository,
    SQLiteOutcomeRepository,
)
from neo_sf_q_intel.outcomes import (
    CorrectionTargetKind,
    HumanCorrectionClaimInput,
    IncidentEventInput,
    IncidentLifecycleEvent,
    IncidentTargetInput,
    OutcomeKind,
    OutcomeMemoryService,
    OutcomeRecord,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
CLOCK = NOW + timedelta(hours=2)
_RUNS_BY_ID: dict[str, AssuranceRun] = {}


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def _record(
    suffix: str = "alpha",
    *,
    project_id: str = "project-alpha",
    snapshot: str = "snapshot-alpha",
    recorded_at: datetime = NOW,
    kind: OutcomeKind = OutcomeKind.INCIDENT_EVENT,
) -> OutcomeRecord:
    if kind not in {OutcomeKind.INCIDENT_EVENT, OutcomeKind.HUMAN_CORRECTION_CLAIM}:
        raise ValueError("Fixture supports the two non-governance outcome kinds")
    entity_id = f"component:{suffix}"
    evidence_id = f"evidence:{suffix}"
    identity_seed = f"{project_id}|{snapshot}|{suffix}|{recorded_at.isoformat()}"
    run = AssuranceRun(
        run_id=uuid5(NAMESPACE_URL, "run|" + identity_seed),
        trace_id=uuid5(NAMESPACE_URL, "trace|" + identity_seed),
        created_at=recorded_at - timedelta(minutes=10),
        reasoning_policy_version="1.0.0",
        reasoning_policy_sha256="1" * 64,
        reasoning_eval_set_id="reasoning-eval",
        reasoning_eval_set_sha256="2" * 64,
        source_snapshot=snapshot,
        source_graph_sha256="3" * 64,
        ontology_id="ontology-core",
        ontology_version="1.0.0",
        ontology_sha256="4" * 64,
        source_profile_id="profile-source",
        source_profile_version="1.0.0",
        source_profile_sha256="5" * 64,
        normalized_graph_sha256="6" * 64,
        request=ChangeRequest(requirement="Assess configured metadata", project_id=project_id),
        evidence=[
            EvidenceRef(
                evidence_id=evidence_id,
                kind="source",
                label="Configured source evidence",
                source="source:configured",
                state=EvidenceState.CONFIRMED,
            )
        ],
        impacts=[
            ImpactFinding(
                entity_id=entity_id,
                label="Configured component",
                kind="component",
                relation="depends_on",
                severity=RiskSeverity.MEDIUM,
                evidence_strength=1.0,
                strength_basis="Confirmed source evidence",
                evidence_ids=[evidence_id],
            )
        ],
    )
    service = OutcomeMemoryService.from_repository(clock=lambda: recorded_at)
    if kind is OutcomeKind.HUMAN_CORRECTION_CLAIM:
        record = service.derive_human_correction_claim(
            run,
            HumanCorrectionClaimInput(
                correction_id=f"correction-{suffix}",
                target_kind=CorrectionTargetKind.IMPACT,
                target_id=entity_id,
                prior_assertion=f"Prior assertion for {suffix}.",
                claimed_correction=f"Corrected assertion for {suffix}.",
                rationale="A reviewer supplied an evidence-grounded correction candidate.",
                evidence_ids=(evidence_id,),
                authority_receipt_ref=f"receipt:{suffix}",
            ),
        )
    else:
        record = service.derive_incident_event(
            run,
            IncidentEventInput(
                incident_id=f"incident-{suffix}",
                event=IncidentLifecycleEvent.OPENED,
                summary=f"A configured validation incident was observed for {suffix}.",
                observed_at=recorded_at - timedelta(minutes=1),
                targets=(
                    IncidentTargetInput(
                        target_kind=CorrectionTargetKind.IMPACT,
                        target_id=entity_id,
                    ),
                ),
                evidence_ids=(evidence_id,),
            ),
        )
    _RUNS_BY_ID[record.lineage.run_id] = run
    return record


def _append(
    repository,
    record: OutcomeRecord,
    idempotency_key: str,
    *,
    predecessor: OutcomeRecord | None = None,
    predecessor_chain: tuple[OutcomeRecord, ...] = (),
) -> OutcomeRecord:
    return repository.append(
        OutcomeAppendRequest(
            record=record,
            originating_run=_RUNS_BY_ID[record.lineage.run_id],
            predecessor=predecessor,
            predecessor_chain=predecessor_chain,
        ),
        idempotency_key,
    )


def _incident_successor(
    predecessor: OutcomeRecord,
    event: IncidentLifecycleEvent,
    *,
    predecessor_chain: tuple[OutcomeRecord, ...] = (),
) -> OutcomeRecord:
    run = _RUNS_BY_ID[predecessor.lineage.run_id]
    recorded_at = predecessor.recorded_at + timedelta(minutes=1)
    target = predecessor.payload.targets[0]
    record = OutcomeMemoryService.from_repository(clock=lambda: recorded_at).derive_incident_event(
        run,
        IncidentEventInput(
            incident_id=predecessor.payload.incident_id,
            event=event,
            summary=f"The incident advanced to {event.value} after durable verification.",
            observed_at=recorded_at,
            targets=(
                IncidentTargetInput(
                    target_kind=target.target_kind,
                    target_id=target.target_id,
                ),
            ),
            evidence_ids=predecessor.payload.evidence_ids,
        ),
        predecessor=predecessor,
        predecessor_chain=predecessor_chain,
    )
    _RUNS_BY_ID[record.lineage.run_id] = run
    return record


def _rehash_record(
    record: OutcomeRecord, mutate: Callable[[dict[str, Any]], None]
) -> OutcomeRecord:
    body = record.model_dump(mode="json")
    body.pop("outcome_id")
    body.pop("outcome_sha256")
    mutate(body)
    digest = _canonical_hash(body)
    return OutcomeRecord.model_validate(
        {**body, "outcome_id": f"outcome:{digest}", "outcome_sha256": digest}
    )


class _FakeResult:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> Any:
        return self.one

    def fetchall(self) -> list[Any]:
        return self.many


class _FakePostgresState:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.receipts: dict[tuple[str, str], tuple[str, str]] = {}
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []

    def connection(self, *_args: Any, **_kwargs: Any) -> _FakePostgresConnection:
        return _FakePostgresConnection(self)


class _FakePostgresConnection:
    def __init__(self, state: _FakePostgresState) -> None:
        self.state = state

    def __enter__(self) -> _FakePostgresConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, parameters: tuple[Any, ...] | None = None) -> _FakeResult:
        rendered = statement.as_string() if hasattr(statement, "as_string") else str(statement)
        self.state.statements.append((rendered, parameters))
        compact = " ".join(rendered.split())
        if parameters is None:
            return _FakeResult()
        if compact.startswith("SELECT pg_advisory_xact_lock"):
            return _FakeResult()
        if compact.startswith("INSERT INTO outcome_idempotency"):
            project, key, outcome_id, outcome_sha = parameters
            identity = (project, key)
            if identity in self.state.receipts:
                return _FakeResult(one=None)
            self.state.receipts[identity] = (outcome_id, outcome_sha)
            return _FakeResult(one={"outcome_id": outcome_id, "outcome_sha256": outcome_sha})
        if compact.startswith("SELECT outcome_id, outcome_sha256 FROM outcome_idempotency"):
            receipt = self.state.receipts.get((parameters[0], parameters[1]))
            return _FakeResult(
                one=({"outcome_id": receipt[0], "outcome_sha256": receipt[1]} if receipt else None)
            )
        if compact.startswith("INSERT INTO outcome_records"):
            project, outcome_id, outcome_sha, kind, snapshot, recorded_at, document = parameters
            identity = (project, outcome_id)
            if identity in self.state.records:
                return _FakeResult(one=None)
            row = {
                "project_id": project,
                "outcome_id": outcome_id,
                "outcome_sha256": outcome_sha,
                "outcome_kind": kind,
                "source_snapshot": snapshot,
                "recorded_at_utc": recorded_at,
                "record_document": document,
            }
            self.state.records[identity] = row
            return _FakeResult(one=dict(row))
        if compact.startswith("SELECT * FROM outcome_records WHERE project_id"):
            if "ORDER BY" not in compact:
                row = self.state.records.get((parameters[0], parameters[1]))
                return _FakeResult(one=dict(row) if row else None)
            project = parameters[0]
            rows = [dict(row) for (scope, _), row in self.state.records.items() if scope == project]
            parameter_index = 1
            if "outcome_kind = ANY" in compact:
                allowed = set(parameters[parameter_index])
                rows = [row for row in rows if row["outcome_kind"] in allowed]
                parameter_index += 1
            if "source_snapshot = %s" in compact:
                snapshot = parameters[parameter_index]
                rows = [row for row in rows if row["source_snapshot"] == snapshot]
                parameter_index += 1
            if "recorded_at_utc < %s" in compact:
                after_time, _, after_id = parameters[parameter_index : parameter_index + 3]
                rows = [
                    row
                    for row in rows
                    if (row["recorded_at_utc"], row["outcome_id"]) < (after_time, after_id)
                ]
            rows.sort(key=lambda row: (row["recorded_at_utc"], row["outcome_id"]), reverse=True)
            return _FakeResult(many=rows[: parameters[-1]])
        raise AssertionError(f"Unexpected SQL in fake adapter contract: {compact}")


@pytest.fixture(params=("memory", "sqlite", "json", "postgres"))
def repository(request, tmp_path: Path, monkeypatch):
    if request.param == "memory":
        return InMemoryOutcomeRepository(clock=lambda: CLOCK)
    if request.param == "sqlite":
        return SQLiteOutcomeRepository(tmp_path / "sqlite" / "outcomes.db", clock=lambda: CLOCK)
    if request.param == "json":
        return JsonOutcomeRepository(tmp_path / "json", clock=lambda: CLOCK)
    state = _FakePostgresState()
    monkeypatch.setattr("neo_sf_q_intel.outcome_repository.psycopg.connect", state.connection)
    return PostgresOutcomeRepository("postgresql://configured", clock=lambda: CLOCK)


def test_adapter_contract_append_get_scope_filter_and_idempotency(repository) -> None:
    first = _record("first", recorded_at=NOW - timedelta(minutes=2))
    second = _record(
        "second",
        recorded_at=NOW - timedelta(minutes=1),
        kind=OutcomeKind.HUMAN_CORRECTION_CLAIM,
    )
    foreign = _record("foreign", project_id="project-beta", recorded_at=NOW)

    inserted = _append(repository, first, "operation:first")
    replayed = _append(repository, first, "operation:first")
    _append(repository, second, "operation:second")
    _append(repository, foreign, "operation:foreign")

    assert inserted == first == replayed
    assert inserted is not first and replayed is not inserted
    assert repository.get("project-alpha", first.outcome_id) == first
    assert repository.get("project-beta", first.outcome_id) is None
    page = repository.query(project_id="project-alpha", limit=10)
    assert page.records == (second, first)
    assert page.next_cursor is None
    assert repository.query(
        project_id="project-alpha", kinds=(OutcomeKind.HUMAN_CORRECTION_CLAIM,), limit=10
    ).records == (second,)
    assert (
        repository.query(
            project_id="project-alpha", source_snapshot="snapshot-missing", limit=10
        ).records
        == ()
    )


def test_adapter_contract_conflicting_idempotency_key_is_rejected(repository) -> None:
    _append(repository, _record("first"), "operation:shared")

    with pytest.raises(OutcomeConflictError, match="Idempotency"):
        _append(repository, _record("second"), "operation:shared")


def test_adapter_contract_rejects_orphan_incident_before_any_effect(repository) -> None:
    opened = _record("durable-chain", recorded_at=NOW - timedelta(minutes=3))
    acknowledged = _incident_successor(opened, IncidentLifecycleEvent.ACKNOWLEDGED)

    with pytest.raises(OutcomePredecessorError, match="absent"):
        _append(
            repository,
            acknowledged,
            "operation:acknowledged",
            predecessor=opened,
        )
    assert repository.query(project_id="project-alpha").records == ()

    _append(repository, opened, "operation:opened")
    assert (
        _append(
            repository,
            acknowledged,
            "operation:acknowledged",
            predecessor=opened,
        )
        == acknowledged
    )


def test_adapter_contract_accepts_only_fully_persisted_incident_chain(repository) -> None:
    opened = _record("full-chain", recorded_at=NOW - timedelta(minutes=4))
    acknowledged = _incident_successor(opened, IncidentLifecycleEvent.ACKNOWLEDGED)
    resolved = _incident_successor(
        acknowledged,
        IncidentLifecycleEvent.RESOLVED,
        predecessor_chain=(opened,),
    )

    _append(repository, opened, "operation:opened")
    _append(
        repository,
        acknowledged,
        "operation:acknowledged",
        predecessor=opened,
    )
    assert (
        _append(
            repository,
            resolved,
            "operation:resolved",
            predecessor=acknowledged,
            predecessor_chain=(opened,),
        )
        == resolved
    )


def test_adapter_contract_cursor_is_stable_and_scope_bound(repository) -> None:
    records = [
        _record(f"item-{index}", recorded_at=NOW + timedelta(minutes=index)) for index in range(4)
    ]
    for index, record in enumerate(records):
        _append(repository, record, f"operation:{index}")

    first_page = repository.query(project_id="project-alpha", limit=2)
    assert first_page.records == (records[3], records[2])
    assert first_page.next_cursor is not None
    _append(
        repository,
        _record("new", recorded_at=NOW + timedelta(hours=1)),
        "operation:new",
    )
    second_page = repository.query(
        project_id="project-alpha", cursor=first_page.next_cursor, limit=2
    )
    assert second_page.records == (records[1], records[0])

    with pytest.raises(OutcomeQueryError, match="scope"):
        repository.query(project_id="project-beta", cursor=first_page.next_cursor, limit=2)


@pytest.mark.parametrize("limit", (0, MAX_QUERY_LIMIT + 1, True))
def test_query_bounds_are_enforced(repository, limit: int) -> None:
    with pytest.raises(OutcomeQueryError, match="limit"):
        repository.query(project_id="project-alpha", limit=limit)


def test_query_rejects_invalid_cursor_and_kind(repository) -> None:
    with pytest.raises(OutcomeQueryError, match="cursor"):
        repository.query(project_id="project-alpha", cursor="not-json", limit=1)
    with pytest.raises(OutcomeQueryError, match="kinds"):
        repository.query(project_id="project-alpha", kinds=("UNSUPPORTED",), limit=1)


def test_kinds_iterable_is_rejected_after_bounded_consumption(repository) -> None:
    consumed = 0

    def adversarial_kinds():
        nonlocal consumed
        while True:
            consumed += 1
            if consumed > len(OutcomeKind) + 1:
                raise AssertionError("Repository exhausted an unbounded kinds iterable")
            yield OutcomeKind.INCIDENT_EVENT

    with pytest.raises(OutcomeQueryError, match="bound"):
        repository.query(project_id="project-alpha", kinds=adversarial_kinds())
    assert consumed == len(OutcomeKind) + 1


def test_finite_overbound_kinds_are_rejected(repository) -> None:
    with pytest.raises(OutcomeQueryError, match="bound"):
        repository.query(
            project_id="project-alpha",
            kinds=(OutcomeKind.INCIDENT_EVENT,) * (len(OutcomeKind) + 1),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("outcome_policy_sha256", "a" * 64),
        ("outcome_evaluation_set_sha256", "b" * 64),
    ),
)
def test_append_replay_rejects_forged_policy_and_evaluation_roots(field: str, value: str) -> None:
    original = _record()
    forged = _rehash_record(original, lambda body: body["lineage"].__setitem__(field, value))
    repository = InMemoryOutcomeRepository(clock=lambda: CLOCK)

    with pytest.raises(OutcomeReplayError, match="replay"):
        repository.append(
            OutcomeAppendRequest(
                record=forged,
                originating_run=_RUNS_BY_ID[original.lineage.run_id],
            ),
            "operation:forged-root",
        )
    assert repository.query(project_id="project-alpha").records == ()


def test_append_replay_rejects_forged_evidence_and_future_time() -> None:
    original = _record()
    run = _RUNS_BY_ID[original.lineage.run_id]
    forged_evidence = _rehash_record(
        original,
        lambda body: body["payload"].__setitem__("evidence_ids", ["evidence:forged"]),
    )
    forged_time = _rehash_record(
        original,
        lambda body: body.__setitem__(
            "recorded_at",
            (CLOCK + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        ),
    )
    for forged in (forged_evidence, forged_time):
        repository = InMemoryOutcomeRepository(clock=lambda: CLOCK)
        with pytest.raises(OutcomeReplayError, match="replay"):
            repository.append(
                OutcomeAppendRequest(record=forged, originating_run=run),
                "operation:forged",
            )
        assert repository.query(project_id="project-alpha").records == ()


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "C:\\private\\record",
        "path=C:\\private\\record",
        "\\\\server\\share\\record",
        "//server/share/record",
        "/tmp/record",
        "/HOME/user/record",
    ),
)
def test_append_replay_rejects_all_machine_path_variants(unsafe_value: str) -> None:
    original = _record()
    forged = _rehash_record(
        original, lambda body: body["lineage"].__setitem__("trace_id", unsafe_value)
    )

    with pytest.raises(OutcomeReplayError, match="replay"):
        InMemoryOutcomeRepository(clock=lambda: CLOCK).append(
            OutcomeAppendRequest(
                record=forged,
                originating_run=_RUNS_BY_ID[original.lineage.run_id],
            ),
            "operation:path",
        )


def test_bare_record_cannot_bypass_append_replay() -> None:
    repository = InMemoryOutcomeRepository(clock=lambda: CLOCK)

    with pytest.raises(OutcomeReplayError, match="authority replay"):
        repository.append(_record(), "operation:bare")  # type: ignore[arg-type]
    assert repository.query(project_id="project-alpha").records == ()


def test_sqlite_self_creates_restarts_and_rejects_mutation(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "outcomes.db"
    first = SQLiteOutcomeRepository(path, clock=lambda: CLOCK)
    record = _record()
    _append(first, record, "operation:alpha")

    restarted = SQLiteOutcomeRepository(path, clock=lambda: CLOCK)
    restarted.setup()
    assert restarted.get("project-alpha", record.outcome_id) == record
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE outcome_records SET source_snapshot = ? WHERE project_id = ?",
                ("changed", "project-alpha"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM outcome_records WHERE project_id = ?", ("project-alpha",)
            )


def test_sqlite_releases_database_handles_after_every_operation(tmp_path: Path) -> None:
    path = tmp_path / "handles" / "outcomes.db"
    repository = SQLiteOutcomeRepository(path, clock=lambda: CLOCK)
    record = _record("released")
    _append(repository, record, "operation:released")
    assert repository.get("project-alpha", record.outcome_id) == record
    assert repository.query(project_id="project-alpha").records == (record,)

    restarted = SQLiteOutcomeRepository(path, clock=lambda: CLOCK)
    restarted.setup()
    assert restarted.get("project-alpha", record.outcome_id) == record
    assert restarted.query(project_id="project-alpha").records == (record,)

    moved = path.with_name("moved-outcomes.db")
    path.replace(moved)
    moved.unlink()
    assert not path.exists() and not moved.exists()


def test_sqlite_corruption_fails_closed(tmp_path: Path) -> None:
    repository = SQLiteOutcomeRepository(tmp_path / "outcomes.db", clock=lambda: CLOCK)
    record = _record()
    _append(repository, record, "operation:alpha")
    with sqlite3.connect(repository.path) as connection:
        connection.execute("DROP TRIGGER outcome_records_no_update")
        connection.execute(
            "UPDATE outcome_records SET outcome_kind = ? WHERE outcome_id = ?",
            (OutcomeKind.TEST_EXECUTION.value, record.outcome_id),
        )

    with pytest.raises(OutcomeCorruptionError, match="scope columns"):
        repository.get("project-alpha", record.outcome_id)


def test_json_uses_hashed_paths_restarts_and_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "outcomes"
    repository = JsonOutcomeRepository(root, clock=lambda: CLOCK)
    project = "../../project'; DROP TABLE outcomes;--"
    record = _record(project_id=project)
    _append(repository, record, "../../idempotency/path")

    assert list(root.iterdir())[0].name == hashlib.sha256(project.encode()).hexdigest()
    assert not (tmp_path / "idempotency").exists()
    restarted = JsonOutcomeRepository(root, clock=lambda: CLOCK)
    assert restarted.get(project, record.outcome_id) == record
    record_path = next(list(root.iterdir())[0].glob("outcome-*.json"))
    record_path.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(OutcomeCorruptionError, match="failed|unreadable"):
        restarted.query(project_id=project)


def test_json_get_rejects_record_copied_into_another_project_scope(tmp_path: Path) -> None:
    root = tmp_path / "outcomes"
    repository = JsonOutcomeRepository(root, clock=lambda: CLOCK)
    record = _record("scoped")
    _append(repository, record, "operation:scoped")
    alpha_path = next((root / hashlib.sha256(b"project-alpha").hexdigest()).glob("outcome-*.json"))
    beta_directory = root / hashlib.sha256(b"project-beta").hexdigest()
    beta_directory.mkdir(parents=True)
    (beta_directory / alpha_path.name).write_bytes(alpha_path.read_bytes())

    with pytest.raises(OutcomeCorruptionError, match="wrong project"):
        repository.get("project-beta", record.outcome_id)


def test_json_claim_prevents_dual_write_after_ambiguous_publish(
    tmp_path: Path, monkeypatch
) -> None:
    repository = JsonOutcomeRepository(tmp_path / "outcomes", clock=lambda: CLOCK)
    record = _record("first")
    original: Callable[[Path, bytes], bool] = repository._exclusive_publish
    failed_once = False

    def fail_record_once(path: Path, content: bytes) -> bool:
        nonlocal failed_once
        if path.name.startswith("outcome-") and not failed_once:
            failed_once = True
            raise OSError("simulated ambiguous storage failure")
        return original(path, content)

    monkeypatch.setattr(repository, "_exclusive_publish", fail_record_once)
    with pytest.raises(OSError, match="ambiguous"):
        _append(repository, record, "operation:shared")
    with pytest.raises(OutcomeConflictError, match="Idempotency"):
        _append(repository, _record("different"), "operation:shared")
    assert repository.query(project_id="project-alpha").records == ()


def test_sensitive_idempotency_keys_are_rejected_without_persistence(tmp_path: Path) -> None:
    repository = JsonOutcomeRepository(tmp_path / "outcomes", clock=lambda: CLOCK)
    secret = "sk-" + "a" * 48

    with pytest.raises(OutcomeQueryError, match="unsafe"):
        _append(repository, _record(), secret)
    assert list((tmp_path / "outcomes").iterdir()) == []


def test_self_hashed_record_with_sensitive_lineage_is_rejected() -> None:
    original = _record()
    body = original.model_dump(mode="json")
    body["lineage"]["run_id"] = "sk-" + "x" * 20
    body.pop("outcome_id")
    body.pop("outcome_sha256")
    digest = _canonical_hash(body)
    structurally_valid = OutcomeRecord.model_validate(
        {**body, "outcome_id": f"outcome:{digest}", "outcome_sha256": digest}
    )

    with pytest.raises(OutcomeReplayError, match="replay"):
        InMemoryOutcomeRepository(clock=lambda: CLOCK).append(
            OutcomeAppendRequest(
                record=structurally_valid,
                originating_run=_RUNS_BY_ID[original.lineage.run_id],
            ),
            "operation:sensitive",
        )


def test_postgres_setup_is_idempotent_append_only_and_vector_free(monkeypatch) -> None:
    state = _FakePostgresState()
    monkeypatch.setattr("neo_sf_q_intel.outcome_repository.psycopg.connect", state.connection)
    repository = PostgresOutcomeRepository("postgresql://configured", clock=lambda: CLOCK)

    repository.setup()
    repository.setup()

    setup_statements = [
        statement
        for statement, parameters in state.statements
        if parameters is None and statement == POSTGRES_OUTCOME_SCHEMA_SQL
    ]
    assert setup_statements == [POSTGRES_OUTCOME_SCHEMA_SQL, POSTGRES_OUTCOME_SCHEMA_SQL]
    lowered = POSTGRES_OUTCOME_SCHEMA_SQL.lower()
    assert "before update or delete" in lowered
    assert lowered.count("before truncate") == 2
    assert "outcome_records_no_truncate" in lowered
    assert "outcome_idempotency_no_truncate" in lowered
    assert "003_outcome_memory" in POSTGRES_OUTCOME_SCHEMA_SQL
    assert "pgvector" not in lowered and " vector" not in lowered

    migration = Path("migrations/003_outcome_memory.sql").read_text(encoding="utf-8").lower()
    assert migration.count("before truncate") == 2
    assert "outcome_records_no_truncate" in migration
    assert "outcome_idempotency_no_truncate" in migration


def test_postgres_uses_parameters_for_sql_metacharacters(monkeypatch) -> None:
    state = _FakePostgresState()
    monkeypatch.setattr("neo_sf_q_intel.outcome_repository.psycopg.connect", state.connection)
    repository = PostgresOutcomeRepository("postgresql://configured", clock=lambda: CLOCK)
    project = "tenant'; DROP TABLE outcome_records;--"
    record = _record(project_id=project)

    _append(repository, record, "operation:sql")
    assert repository.get(project, record.outcome_id) == record
    statements = " ".join(statement for statement, _ in state.statements)
    assert project not in statements


def test_repository_port_has_no_mutation_or_global_enumeration_surface(repository) -> None:
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")
    assert not hasattr(repository, "list_all")


def test_runtime_adapter_constructors_require_configured_storage_locations() -> None:
    with pytest.raises(TypeError):
        SQLiteOutcomeRepository()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        JsonOutcomeRepository()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        PostgresOutcomeRepository()  # type: ignore[call-arg]
