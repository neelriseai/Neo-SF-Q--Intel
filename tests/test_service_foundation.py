from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import (
    AssuranceService,
    FoundationPipelineUnavailable,
    create_service,
)
from tests.test_foundation_pipeline import _pipeline, _repository
from tests.test_workflow import source

ROOT = Path(__file__).resolve().parents[1]


class RecordingPipeline:
    def __init__(self, captured: Any, *, verify_error: Exception | None = None) -> None:
        self.captured = captured
        self.verify_error = verify_error
        self.capture_calls = 0
        self.verify_calls = 0

    def capture_current(self) -> Any:
        self.capture_calls += 1
        return self.captured

    def verify_current(self, candidate: Any) -> Any:
        self.verify_calls += 1
        assert candidate is self.captured
        if self.verify_error is not None:
            raise self.verify_error
        return self.captured


def test_service_freshly_captures_and_verifies_without_run_persistence(
    tmp_path: Path,
) -> None:
    captured = _pipeline(_repository(tmp_path)).capture_current()
    pipeline = RecordingPipeline(captured)
    repository = InMemoryRunRepository()
    service = AssuranceService(
        source(),
        repository,
        foundation_pipeline=pipeline,  # type: ignore[arg-type]
    )

    first = service.capture_candidate_foundation()
    second = service.capture_candidate_foundation()

    assert first == second == captured.evidence
    assert pipeline.capture_calls == pipeline.verify_calls == 2
    assert repository.runs == {}
    projection = first.model_dump(mode="json")
    assert "verified_change" not in projection
    assert "produced_graph" not in projection
    assert "operation_seeds" not in projection


def test_service_returns_fresh_abstention_without_promoting_or_verifying(
    tmp_path: Path,
) -> None:
    captured = _pipeline(_repository(tmp_path, dirty=False)).capture_current()
    pipeline = RecordingPipeline(captured)
    service = AssuranceService(
        source(),
        foundation_pipeline=pipeline,  # type: ignore[arg-type]
    )

    evidence = service.capture_candidate_foundation()

    assert evidence.foundation_execution_complete is False
    assert pipeline.capture_calls == 1
    assert pipeline.verify_calls == 0


def test_service_sanitizes_current_verification_failure(tmp_path: Path) -> None:
    captured = _pipeline(_repository(tmp_path)).capture_current()
    pipeline = RecordingPipeline(
        captured,
        verify_error=RuntimeError("token=secret C:\\private\\source.json"),
    )
    service = AssuranceService(
        source(),
        foundation_pipeline=pipeline,  # type: ignore[arg-type]
    )

    with pytest.raises(FoundationPipelineUnavailable) as caught:
        service.capture_candidate_foundation()

    assert str(caught.value) == "FOUNDATION_PIPELINE_UNAVAILABLE"


def test_service_without_host_pipeline_is_stably_unavailable() -> None:
    service = AssuranceService(source())

    with pytest.raises(FoundationPipelineUnavailable) as caught:
        service.capture_candidate_foundation()

    assert str(caught.value) == "FOUNDATION_PIPELINE_UNAVAILABLE"


def test_service_factory_binds_loaded_project_and_configured_nested_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_root = tmp_path / "aut"
    app_root = git_root / "nested" / "dx"
    app_root.mkdir(parents=True)
    captured_arguments: dict[str, Any] = {}
    sentinel = object()

    def build_pipeline(_cls: type, **kwargs: Any) -> object:
        captured_arguments.update(kwargs)
        return sentinel

    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    monkeypatch.setattr(
        "neo_sf_q_intel.service.CandidateFoundationPipeline.from_host_configuration",
        classmethod(build_pipeline),
    )
    settings = Settings(
        allow_llm=False,
        database_url=None,
        salesforce_repository_root=Path("aut"),
        salesforce_app_root=Path("aut/nested/dx"),
        source_graph_sha256="fixture",
        sqlite_path=tmp_path / "runs.db",
        outcome_sqlite_path=tmp_path / "outcomes.db",
        live_receipt_sqlite_path=tmp_path / "live-receipts.db",
        live_acceptance_profile_path=(ROOT / "config" / "live-salesforce-acceptance-profile.json"),
    )

    service = create_service(settings, tmp_path)

    assert service.foundation_capture_configured is True
    assert service.foundation_configuration_code is None
    assert service.live_receipt_ledger_mode == "SQLITE"
    assert service.live_receipt_ledger_degradation_code == "POSTGRES_NOT_CONFIGURED"
    assert captured_arguments == {
        "project_id": source().project_id,
        "repository_root": git_root.resolve(),
        "salesforce_app_root": app_root.resolve(),
        "implementation_root": tmp_path,
    }


def test_service_factory_keeps_legacy_service_when_foundation_roots_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "aut").mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr("neo_sf_q_intel.service.load_salesforce_source", lambda *a, **k: source())
    settings = Settings(
        allow_llm=False,
        database_url=None,
        salesforce_repository_root=Path("aut"),
        salesforce_app_root=Path("outside"),
        source_graph_sha256="fixture",
        sqlite_path=tmp_path / "runs.db",
        outcome_sqlite_path=tmp_path / "outcomes.db",
        live_receipt_sqlite_path=tmp_path / "live-receipts.db",
        live_acceptance_profile_path=(ROOT / "config" / "live-salesforce-acceptance-profile.json"),
    )

    service = create_service(settings, tmp_path)

    assert service.foundation_capture_configured is False
    assert service.foundation_configuration_code == "FOUNDATION_PIPELINE_CONFIGURATION_INVALID"
    assert service.analyze.__name__ == "analyze"
    with pytest.raises(FoundationPipelineUnavailable):
        service.capture_candidate_foundation()
