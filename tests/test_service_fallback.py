import sqlite3
from pathlib import Path

import pytest

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import ChangeRequest
from neo_sf_q_intel.repository import (
    InMemoryRunRepository,
    PersistenceSchemaError,
    SQLiteRunRepository,
)
from neo_sf_q_intel.service import create_service
from tests.test_workflow import source


def fallback_settings(tmp_path: Path) -> Settings:
    return Settings(
        allow_llm=False,
        database_url="postgresql://unavailable.invalid/database",
        sqlite_path=tmp_path / "fallback.db",
        salesforce_app_root=Path("source"),
        source_graph_sha256="fixture",
    )


def test_postgresql_failure_falls_back_to_auto_created_sqlite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    service = create_service(fallback_settings(tmp_path), tmp_path)
    run = service.analyze(ChangeRequest(requirement="Assess workbench layout"))

    assert service.persistence_mode == "sqlite-fallback"
    assert service.persistence_warning and "ConnectionError" in service.persistence_warning
    assert isinstance(service.repository, SQLiteRunRepository)
    assert service.get(run.run_id) == run
    assert (tmp_path / "fallback.db").is_file()


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


def test_postgresql_schema_defect_is_not_hidden_by_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.PostgresRunRepository.setup",
        lambda self: (_ for _ in ()).throw(PersistenceSchemaError("invalid schema")),
    )

    with pytest.raises(PersistenceSchemaError, match="invalid schema"):
        create_service(fallback_settings(tmp_path), tmp_path)

    assert not (tmp_path / "fallback.db").exists()
