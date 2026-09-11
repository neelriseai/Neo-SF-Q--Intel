from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle
from neo_sf_q_intel.domain import RunStatus
from neo_sf_q_intel.repository import (
    CandidatePersistenceConflictError,
    InMemoryRunRepository,
    PostgresRunRepository,
    SQLiteRunRepository,
)
from neo_sf_q_intel.service import AssuranceService
from tests.test_foundation_pipeline import _pipeline, _repository
from tests.test_workflow import source


def _bundle(tmp_path: Path) -> CandidateAssuranceBundle:
    service = AssuranceService(
        source(),
        InMemoryRunRepository(),
        foundation_pipeline=_pipeline(_repository(tmp_path)),
    )
    return service.analyze_current_candidate()


@pytest.fixture(scope="module")
def candidate_bundle(tmp_path_factory: pytest.TempPathFactory) -> CandidateAssuranceBundle:
    return _bundle(tmp_path_factory.mktemp("candidate-persistence"))


def test_in_memory_candidate_persistence_is_exact_and_idempotent(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    bundle = candidate_bundle
    repository = InMemoryRunRepository()

    repository.save_candidate_bundle(bundle)
    repository.save_candidate_bundle(bundle)

    assert set(repository.runs) == {item.run.run_id for item in bundle.analyses}
    assert tuple(repository.candidate_bundles) == (bundle.bundle_sha256,)
    stored = json.loads(repository.candidate_bundles[bundle.bundle_sha256])
    assert stored == bundle.model_dump(mode="json")


def test_candidate_service_uses_only_atomic_repository_operation(tmp_path: Path) -> None:
    class AtomicOnlyRepository(InMemoryRunRepository):
        def __init__(self) -> None:
            super().__init__()
            self.atomic_calls = 0

        def save(self, _run: Any) -> None:
            raise AssertionError("candidate service attempted sequential run persistence")

        def save_candidate_bundle(self, bundle: CandidateAssuranceBundle) -> None:
            self.atomic_calls += 1
            super().save_candidate_bundle(bundle)

    repository = AtomicOnlyRepository()
    service = AssuranceService(
        source(),
        repository,
        foundation_pipeline=_pipeline(_repository(tmp_path)),
    )

    bundle = service.analyze_current_candidate()

    assert repository.atomic_calls == 1
    assert set(repository.runs) == {item.run.run_id for item in bundle.analyses}


def test_candidate_persistence_rejects_tampered_declared_digest(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    bundle = candidate_bundle
    tampered = bundle.model_copy(update={"bundle_sha256": "f" * 64})
    repository = InMemoryRunRepository()

    with pytest.raises(CandidatePersistenceConflictError, match="contract validation"):
        repository.save_candidate_bundle(tampered)

    assert repository.runs == {}
    assert repository.candidate_bundles == {}


def test_candidate_persistence_fully_revalidates_model_copy(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    invalid_run = candidate_bundle.analyses[0].run.model_copy(
        update={"reasoning_policy_sha256": "a" * 64}
    )
    invalid_side = candidate_bundle.analyses[0].model_copy(update={"run": invalid_run})
    invalid_bundle = candidate_bundle.model_copy(
        update={"analyses": (invalid_side, *candidate_bundle.analyses[1:])}
    )
    repository = InMemoryRunRepository()

    with pytest.raises(CandidatePersistenceConflictError, match="contract validation"):
        repository.save_candidate_bundle(invalid_bundle)

    assert repository.runs == {}


def test_in_memory_candidate_conflict_does_not_partially_persist(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    bundle = candidate_bundle
    repository = InMemoryRunRepository()
    conflicting = bundle.analyses[0].run.model_copy(update={"status": RunStatus.FAILED})
    repository.save(conflicting)

    with pytest.raises(CandidatePersistenceConflictError, match="Component run ID"):
        repository.save_candidate_bundle(bundle)

    assert set(repository.runs) == {conflicting.run_id}
    assert repository.get(conflicting.run_id) == conflicting
    assert repository.candidate_bundles == {}


def test_in_memory_candidate_component_cannot_be_overwritten_later(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    repository = InMemoryRunRepository()
    repository.save_candidate_bundle(candidate_bundle)
    original = candidate_bundle.analyses[0].run
    changed = original.model_copy(update={"status": RunStatus.FAILED})

    with pytest.raises(CandidatePersistenceConflictError, match="immutable"):
        repository.save(changed)

    assert repository.get(original.run_id) == original


def test_sqlite_candidate_bundle_round_trip_is_atomic_and_idempotent(
    tmp_path: Path, candidate_bundle: CandidateAssuranceBundle
) -> None:
    bundle = candidate_bundle
    database = tmp_path / "state" / "runs.db"
    repository = SQLiteRunRepository(database)
    repository.setup()

    repository.save_candidate_bundle(bundle)
    repository.save_candidate_bundle(bundle)

    with sqlite3.connect(database) as connection:
        bundle_rows = connection.execute(
            "SELECT bundle_sha256, bundle_document FROM candidate_assurance_bundles"
        ).fetchall()
        run_count = connection.execute("SELECT COUNT(*) FROM assurance_runs").fetchone()[0]
    assert len(bundle_rows) == 1
    assert bundle_rows[0][0] == bundle.bundle_sha256
    assert json.loads(bundle_rows[0][1]) == bundle.model_dump(mode="json")
    assert run_count == len(bundle.analyses)


def test_sqlite_injected_bundle_failure_rolls_back_component_runs(
    tmp_path: Path, candidate_bundle: CandidateAssuranceBundle
) -> None:
    bundle = candidate_bundle
    database = tmp_path / "runs.db"
    repository = SQLiteRunRepository(database)
    repository.setup()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_candidate_bundle
            BEFORE INSERT ON candidate_assurance_bundles
            BEGIN SELECT RAISE(ABORT, 'injected bundle failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected bundle failure"):
        repository.save_candidate_bundle(bundle)

    with sqlite3.connect(database) as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM assurance_runs").fetchone()[0]
        bundle_count = connection.execute(
            "SELECT COUNT(*) FROM candidate_assurance_bundles"
        ).fetchone()[0]
    assert (run_count, bundle_count) == (0, 0)


def test_sqlite_candidate_component_cannot_be_overwritten_later(
    tmp_path: Path, candidate_bundle: CandidateAssuranceBundle
) -> None:
    database = tmp_path / "runs.db"
    repository = SQLiteRunRepository(database)
    repository.setup()
    repository.save_candidate_bundle(candidate_bundle)
    original = candidate_bundle.analyses[0].run
    changed = original.model_copy(update={"status": RunStatus.FAILED})

    with pytest.raises(CandidatePersistenceConflictError, match="immutable"):
        repository.save(changed)

    assert repository.get(original.run_id) == original


class _PostgresState:
    def __init__(self, *, fail_bundle_insert: bool = False) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.bundles: dict[str, tuple[str, list[str]]] = {}
        self.links: dict[str, str] = {}
        self.fail_bundle_insert = fail_bundle_insert


class _PostgresConnection:
    def __init__(self, state: _PostgresState) -> None:
        self.state = state
        self._fetchone: tuple[Any, ...] | None = None

    def __enter__(self) -> _PostgresConnection:
        self._before_runs = copy.deepcopy(self.state.runs)
        self._before_bundles = copy.deepcopy(self.state.bundles)
        self._before_links = copy.deepcopy(self.state.links)
        return self

    def __exit__(self, exc_type: object, *_args: object) -> None:
        if exc_type is not None:
            self.state.runs = self._before_runs
            self.state.bundles = self._before_bundles
            self.state.links = self._before_links

    def execute(
        self, statement: str, parameters: tuple[Any, ...] | None = None
    ) -> _PostgresConnection:
        normalized = " ".join(statement.lower().split())
        values = parameters or ()
        self._fetchone = None
        if normalized.startswith("select pg_advisory_xact_lock"):
            pass
        elif normalized.startswith("insert into assurance_runs"):
            run_id = str(values[0])
            self.state.runs.setdefault(run_id, json.loads(values[5]))
        elif normalized.startswith(
            "select c.bundle_sha256, r.run_document from candidate_assurance_component_runs"
        ):
            run_id = str(values[0])
            bundle = self.state.links.get(run_id)
            document = self.state.runs.get(run_id)
            self._fetchone = (bundle, document) if bundle is not None else None
        elif normalized.startswith("select run_document from assurance_runs"):
            document = self.state.runs.get(str(values[0]))
            self._fetchone = (document,) if document is not None else None
        elif normalized.startswith("insert into candidate_assurance_bundles"):
            if self.state.fail_bundle_insert:
                raise RuntimeError("injected PostgreSQL failure")
            digest = str(values[0])
            self.state.bundles.setdefault(digest, (str(values[1]), json.loads(values[2])))
        elif normalized.startswith(
            "select bundle_document, component_run_ids from candidate_assurance_bundles"
        ):
            self._fetchone = self.state.bundles.get(str(values[0]))
        elif normalized.startswith("insert into candidate_assurance_component_runs"):
            self.state.links.setdefault(str(values[0]), str(values[1]))
        elif normalized.startswith("select bundle_sha256 from candidate_assurance_component_runs"):
            bundle = self.state.links.get(str(values[0]))
            self._fetchone = (bundle,) if bundle is not None else None
        else:
            raise AssertionError(f"Unexpected SQL: {normalized}")
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._fetchone


def test_postgresql_candidate_persistence_is_atomic_and_idempotent(
    candidate_bundle: CandidateAssuranceBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = candidate_bundle
    state = _PostgresState()
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect",
        lambda _database_url: _PostgresConnection(state),
    )
    repository = PostgresRunRepository("postgresql://configured")

    repository.save_candidate_bundle(bundle)
    repository.save_candidate_bundle(bundle)

    assert set(state.runs) == {str(item.run.run_id) for item in bundle.analyses}
    assert tuple(state.bundles) == (bundle.bundle_sha256,)
    assert state.links == {str(item.run.run_id): bundle.bundle_sha256 for item in bundle.analyses}
    assert json.loads(state.bundles[bundle.bundle_sha256][0]) == bundle.model_dump(mode="json")


def test_postgresql_injected_failure_rolls_back_component_runs(
    candidate_bundle: CandidateAssuranceBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = candidate_bundle
    state = _PostgresState(fail_bundle_insert=True)
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect",
        lambda _database_url: _PostgresConnection(state),
    )

    with pytest.raises(RuntimeError, match="injected PostgreSQL failure"):
        PostgresRunRepository("postgresql://configured").save_candidate_bundle(bundle)

    assert state.runs == {}
    assert state.bundles == {}
    assert state.links == {}


def test_postgresql_candidate_component_cannot_be_overwritten_later(
    candidate_bundle: CandidateAssuranceBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _PostgresState()
    monkeypatch.setattr(
        "neo_sf_q_intel.repository.psycopg.connect",
        lambda _database_url: _PostgresConnection(state),
    )
    repository = PostgresRunRepository("postgresql://configured")
    repository.save_candidate_bundle(candidate_bundle)
    original = candidate_bundle.analyses[0].run
    changed = original.model_copy(update={"status": RunStatus.FAILED})

    with pytest.raises(CandidatePersistenceConflictError, match="immutable"):
        repository.save(changed)

    assert state.runs[str(original.run_id)] == json.loads(original.model_dump_json())
