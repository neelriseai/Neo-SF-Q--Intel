import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import (
    ChangeIntent,
    ChangeRequest,
    DecisionCode,
    ReleaseDecision,
)
from neo_sf_q_intel.outcome_repository import (
    InMemoryOutcomeRepository,
    JsonOutcomeRepository,
    OutcomeConflictError,
    OutcomeCorruptionError,
    OutcomeReplayError,
    SQLiteOutcomeRepository,
)
from neo_sf_q_intel.outcomes import (
    CorrectionTargetKind,
    IncidentEventInput,
    IncidentLifecycleEvent,
    IncidentTargetInput,
    OutcomeMemoryService,
    OutcomeRecord,
)
from neo_sf_q_intel.repository import (
    InMemoryRunRepository,
    PersistenceSchemaError,
    SQLiteRunRepository,
)
from neo_sf_q_intel.service import AssuranceService, create_service
from tests.test_workflow import source


def fallback_settings(tmp_path: Path) -> Settings:
    return Settings(
        allow_llm=False,
        database_url="postgresql://unavailable.invalid/database",
        sqlite_path=tmp_path / "fallback.db",
        outcome_sqlite_path=tmp_path / "outcomes.db",
        outcome_json_path=tmp_path / "outcomes-json",
        salesforce_app_root=Path("source"),
        source_graph_sha256="fixture",
    )


def outcome_record(
    run_service: AssuranceService | None = None,
) -> OutcomeRecord:
    service = run_service or AssuranceService(source())
    run = service.analyze(
        ChangeRequest(
            requirement="Change configured workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    return OutcomeMemoryService.from_repository().derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident:configured-layout",
            event=IncidentLifecycleEvent.OPENED,
            summary="A configured validation incident was observed.",
            observed_at=datetime.now(UTC),
            targets=(
                IncidentTargetInput(
                    target_kind=CorrectionTargetKind.IMPACT,
                    target_id="lwc:Workbench",
                ),
            ),
            evidence_ids=("graph:demo:lwc:Workbench",),
        ),
    )


def self_rehashed_lineage_tamper(record: OutcomeRecord) -> OutcomeRecord:
    body = record.model_dump(mode="json", exclude={"outcome_id", "outcome_sha256"})
    body["lineage"]["run_sha256"] = "f" * 64
    digest = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return OutcomeRecord.model_validate(
        {**body, "outcome_id": f"outcome:{digest}", "outcome_sha256": digest}
    )


def acknowledged_record(
    service: AssuranceService,
    opened: OutcomeRecord,
) -> OutcomeRecord:
    run = service.repository.get(UUID(opened.lineage.run_id))
    assert run is not None
    return OutcomeMemoryService.from_repository().derive_incident_event(
        run,
        IncidentEventInput(
            incident_id="incident:configured-layout",
            event=IncidentLifecycleEvent.ACKNOWLEDGED,
            summary="The configured incident was acknowledged.",
            observed_at=datetime.now(UTC),
            targets=(
                IncidentTargetInput(
                    target_kind=CorrectionTargetKind.IMPACT,
                    target_id="lwc:Workbench",
                ),
            ),
            evidence_ids=("graph:demo:lwc:Workbench",),
        ),
        predecessor=opened,
    )


@pytest.fixture(autouse=True)
def configured_outcome_postgresql_is_offline(monkeypatch) -> None:
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresOutcomeRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )


def test_postgresql_failure_falls_back_to_auto_created_sqlite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)
    run = service.analyze(ChangeRequest(requirement="Assess workbench layout"))
    outcome = outcome_record(service)
    appended = service.append_outcome(outcome, "operation:alpha")

    assert service.persistence_mode == "sqlite-fallback"
    assert service.persistence_warning and "ConnectionError" in service.persistence_warning
    assert isinstance(service.repository, SQLiteRunRepository)
    assert isinstance(service.outcome_repository, SQLiteOutcomeRepository)
    assert service.run_persistence == "sqlite-fallback"
    assert service.outcome_persistence == "sqlite"
    assert service.outcome_durable is True
    assert service.degradation_codes == (
        "RUN_POSTGRESQL_UNAVAILABLE",
        "OUTCOME_POSTGRESQL_UNAVAILABLE",
    )
    assert service.get(run.run_id) == run
    assert appended == outcome
    assert service.get_outcome("workflow-fixture", outcome.outcome_id) == outcome
    assert service.query_outcomes(project_id="workflow-fixture").records == (outcome,)
    assert (tmp_path / "fallback.db").is_file()
    assert (tmp_path / "outcomes.db").is_file()


def test_sqlite_failure_uses_declared_process_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.service.SQLiteRunRepository.setup",
        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("read-only")),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)

    assert service.persistence_mode == "memory-cache"
    assert service.persistence_warning and "SQLite unavailable" in service.persistence_warning
    assert isinstance(service.repository, InMemoryRunRepository)
    assert service.outcome_durable is True


def test_outcome_sqlite_unavailability_falls_back_to_json(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.service.SQLiteOutcomeRepository.setup",
        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("read-only")),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)

    assert isinstance(service.outcome_repository, JsonOutcomeRepository)
    assert service.outcome_persistence == "json"
    assert service.outcome_durable is True
    assert "OUTCOME_SQLITE_UNAVAILABLE" in service.degradation_codes
    assert (tmp_path / "outcomes-json").is_dir()


def test_configured_outcome_postgresql_is_selected_when_setup_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    class _Checkpointer(InMemorySaver):
        def setup(self) -> None:
            return None

    class _CheckpointContext:
        def __enter__(self):  # noqa: ANN204
            return _Checkpointer()

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr("neo_sf_q_intel.service.PostgresRunRepository.setup", lambda self: None)
    monkeypatch.setattr("neo_sf_q_intel.service.PostgresOutcomeRepository.setup", lambda self: None)
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresSaver.from_conn_string",
        lambda value: _CheckpointContext(),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)

    assert service.run_persistence == "postgresql-runs-checkpoints"
    assert service.outcome_persistence == "postgresql"
    assert service.outcome_durable is True
    assert service.degradation_codes == ()
    assert service.gap_codes == ()
    assert not (tmp_path / "outcomes.db").exists()
    service.close()


def test_outcome_json_unavailability_uses_declared_process_cache(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.service.SQLiteOutcomeRepository.setup",
        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("read-only")),
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.service.JsonOutcomeRepository.__init__",
        lambda self, root: (_ for _ in ()).throw(OSError("unavailable")),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)

    assert isinstance(service.outcome_repository, InMemoryOutcomeRepository)
    assert service.outcome_persistence == "process-cache"
    assert service.outcome_durable is False
    assert "OUTCOME_JSON_UNAVAILABLE" in service.degradation_codes
    assert service.gap_codes.count("OUTCOME_PROCESS_CACHE_NON_DURABLE") == 1


def test_outcome_sqlite_schema_defect_is_not_hidden_by_json_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    monkeypatch.setattr(
        "neo_sf_q_intel.service.SQLiteOutcomeRepository.setup",
        lambda self: (_ for _ in ()).throw(
            sqlite3.OperationalError("malformed database schema")
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match="malformed database schema"):
        create_service(fallback_settings(tmp_path), tmp_path)

    assert not (tmp_path / "outcomes-json").exists()


class _FailClosedOutcomeRepository:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def append(self, request, idempotency_key):  # noqa: ANN001, ANN201
        raise self.failure

    def get(self, project_id, outcome_id):  # noqa: ANN001, ANN201
        raise self.failure

    def query(self, **kwargs):  # noqa: ANN003, ANN201
        raise self.failure


@pytest.mark.parametrize(
    "failure",
    (
        OutcomeConflictError("idempotency conflict"),
        OSError("ambiguous append"),
    ),
)
def test_outcome_append_failures_never_switch_repository(failure: BaseException) -> None:
    repository = _FailClosedOutcomeRepository(failure)
    service = AssuranceService(
        source(),
        outcome_repository=repository,
        outcome_persistence="configured-primary",
    )
    record = outcome_record(service)

    with pytest.raises(type(failure)):
        service.append_outcome(record, "operation:alpha")

    assert service.outcome_repository is repository
    assert service.outcome_persistence == "configured-primary"
    assert service.outcome_durable is False
    assert "OUTCOME_PROCESS_CACHE_NON_DURABLE" in service.gap_codes


def test_outcome_record_corruption_never_switches_repository() -> None:
    failure = OutcomeCorruptionError("corrupt record")
    repository = _FailClosedOutcomeRepository(failure)
    service = AssuranceService(
        source(),
        outcome_repository=repository,
        outcome_persistence="configured-primary",
    )

    with pytest.raises(OutcomeCorruptionError, match="corrupt record"):
        service.get_outcome("workflow-fixture", "outcome:" + "0" * 64)

    assert service.outcome_repository is repository


def test_outcome_append_requires_originating_run_in_run_persistence() -> None:
    source_service = AssuranceService(source())
    record = outcome_record(source_service)
    destination_service = AssuranceService(source())

    with pytest.raises(OutcomeReplayError, match="not present in run persistence"):
        destination_service.append_outcome(record, "operation:missing-run")

    assert destination_service.query_outcomes(project_id="workflow-fixture").records == ()


def test_outcome_reads_replay_lineage_and_fail_closed_on_self_rehashed_tamper() -> None:
    service = AssuranceService(source())
    record = outcome_record(service)
    service.append_outcome(record, "operation:trusted")
    assert isinstance(service.outcome_repository, InMemoryOutcomeRepository)
    forged = self_rehashed_lineage_tamper(record)
    service.outcome_repository._records[("workflow-fixture", record.outcome_id)] = forged

    with pytest.raises(OutcomeReplayError, match="canonical authority replay"):
        service.get_outcome("workflow-fixture", record.outcome_id)
    with pytest.raises(OutcomeReplayError, match="canonical authority replay"):
        service.query_outcomes(project_id="workflow-fixture")


def test_incident_append_requires_persisted_history_and_reads_reconstruct_it() -> None:
    service = AssuranceService(source())
    opened = outcome_record(service)
    acknowledged = acknowledged_record(service, opened)

    with pytest.raises(OutcomeReplayError, match="not present in outcome persistence"):
        service.append_outcome(
            acknowledged,
            "operation:acknowledged-before-opened",
            predecessor=opened,
        )

    service.append_outcome(opened, "operation:opened")
    service.append_outcome(
        acknowledged,
        "operation:acknowledged",
        predecessor=opened,
    )
    assert (
        service.get_outcome("workflow-fixture", acknowledged.outcome_id)
        == acknowledged
    )
    assert isinstance(service.outcome_repository, InMemoryOutcomeRepository)
    service.outcome_repository._records.pop(("workflow-fixture", opened.outcome_id))
    with pytest.raises(OutcomeReplayError, match="missing outcome"):
        service.get_outcome("workflow-fixture", acknowledged.outcome_id)


def test_outcome_query_corruption_never_switches_repository() -> None:
    failure = OutcomeCorruptionError("corrupt query result")
    repository = _FailClosedOutcomeRepository(failure)
    service = AssuranceService(
        source(),
        outcome_repository=repository,
        outcome_persistence="configured-primary",
    )

    with pytest.raises(OutcomeCorruptionError, match="corrupt query result"):
        service.query_outcomes(project_id="workflow-fixture")

    assert service.outcome_repository is repository


def test_outcome_queries_are_scoped_to_the_loaded_source() -> None:
    service = AssuranceService(source())

    with pytest.raises(ValueError, match="does not match"):
        service.query_outcomes(project_id="another-project")


@pytest.mark.parametrize(
    "sensitive",
    ("sk-" + "q" * 24, "file:" + "///workspace/private/project"),
)
def test_outcome_query_rejects_sensitive_project_without_echoing_it(
    sensitive: str,
) -> None:
    service = AssuranceService(source())

    with pytest.raises(ValueError) as captured:
        service.query_outcomes(project_id=sensitive)

    assert sensitive not in str(captured.value)


@pytest.mark.parametrize(
    "sensitive",
    ("sk-" + "q" * 24, "file:" + "///workspace/private/project"),
)
def test_outcome_append_rejects_sensitive_project_without_echoing_it(
    sensitive: str,
) -> None:
    service = AssuranceService(source())
    record = outcome_record(service)
    unsafe_lineage = record.lineage.model_copy(update={"project_id": sensitive})
    unsafe_record = record.model_copy(update={"lineage": unsafe_lineage})

    with pytest.raises(ValueError) as captured:
        service.append_outcome(unsafe_record, "operation:unsafe-project")

    assert sensitive not in str(captured.value)


def test_postgresql_schema_defect_is_not_hidden_by_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(PersistenceSchemaError("invalid schema")),
    )

    with pytest.raises(PersistenceSchemaError, match="invalid schema"):
        create_service(fallback_settings(tmp_path), tmp_path)

    assert not (tmp_path / "fallback.db").exists()


def test_sqlite_history_is_preserved_but_service_reads_use_current_release_policy(
    tmp_path: Path,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "runs.db")
    repository.setup()
    service = AssuranceService(source(), repository)
    run = service.analyze(ChangeRequest(requirement="Assess durable metadata"))
    historical = run.model_copy(
        update={
            "decision": ReleaseDecision(
                code=DecisionCode.GO,
                reasons=["historical-policy-result"],
            )
        },
        deep=True,
    )
    repository.save(historical)

    effective = service.get(run.run_id)
    stored = repository.get(run.run_id)

    assert effective and effective.decision
    assert effective.decision.code is DecisionCode.INCOMPLETE
    assert effective.recorded_decision == historical.decision
    assert stored and stored.decision == historical.decision
    assert stored.recorded_decision is None
