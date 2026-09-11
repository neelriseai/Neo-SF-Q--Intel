import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.candidate_assurance import (
    CandidateAssuranceBundle,
    build_candidate_assurance_view,
)
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest, DecisionCode
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.foundation_pipeline import CandidateFoundationPipeline
from neo_sf_q_intel.repository import InMemoryRunRepository
from neo_sf_q_intel.service import AssuranceService, LiveSalesforceDiagnosticView
from neo_sf_q_intel.specialist import (
    ProviderCallOutcome,
    ProviderCallStatus,
    ProviderFinishReason,
    ProviderProfile,
)
from tests.test_foundation_pipeline import NS, ROOT, _git, _pipeline, _repository, _write
from tests.test_specialist import _profile
from tests.test_workflow import source


def _service(tmp_path: Path) -> tuple[AssuranceService, InMemoryRunRepository]:
    return _service_for_repository(_repository(tmp_path))


def _service_for_repository(
    repository_root: Path,
) -> tuple[AssuranceService, InMemoryRunRepository]:
    repository = InMemoryRunRepository()
    return (
        AssuranceService(
            source(),
            repository,
            foundation_pipeline=_pipeline(repository_root),
        ),
        repository,
    )


class _RecordingSpecialistProvider:
    def __init__(self, profile: ProviderProfile | None = None) -> None:
        self._profile = profile or _profile()
        self.prompts: list[str] = []

    @property
    def profile(self) -> ProviderProfile:
        return self._profile

    def __call__(
        self,
        prompt: str,
        *,
        timeout_milliseconds: int,
        maximum_output_tokens: int,
    ) -> ProviderCallOutcome:
        self.prompts.append(prompt)
        return ProviderCallOutcome(
            status=ProviderCallStatus.SUCCESS,
            raw_response=json.dumps(
                {
                    "schema_version": "1.0.0",
                    "posture": "ANALYSIS_ONLY",
                    "candidate_state": "CANDIDATE",
                    "relationship_state": "INFERRED",
                    "may_authorize": False,
                    "may_satisfy_release_evidence": False,
                    "authority_eligible": False,
                    "conclusion": "No source-bound proposal from the test provider.",
                    "proposals": [],
                    "assumptions": [],
                    "gaps": ["test provider abstained"],
                    "abstained": True,
                },
                separators=(",", ":"),
            ),
            provider_profile_sha256=self._profile.profile_sha256,
            invoked_at="2026-01-01T00:00:00.000Z",
            completed_at="2026-01-01T00:00:00.001Z",
            finish_reason=ProviderFinishReason.STOP,
            input_tokens=10,
            output_tokens=8,
            duration_milliseconds=1,
        )


def _lwc_repository(tmp_path: Path, names: tuple[str, str]) -> Path:
    repository = _repository(tmp_path, dirty=False)
    for name in names:
        bundle = f"workspace/dx/package/main/default/lwc/{name}"
        _write(repository, f"{bundle}/{name}.js", b"export default class Component {}")
        _write(repository, f"{bundle}/{name}.html", b"<template></template>")
        _write(
            repository,
            f"{bundle}/{name}.js-meta.xml",
            f'<LightningComponentBundle xmlns="{NS}"><isExposed>true</isExposed>'
            "</LightningComponentBundle>".encode(),
        )
        _write(
            repository,
            f"{bundle}/__tests__/{name}.test.js",
            b"test('renders', () => expect(true).toBe(true));",
        )
    _write(
        repository,
        "workspace/dx/package/main/default/flexipages/Workspace.flexipage-meta.xml",
        (
            f'<FlexiPage xmlns="{NS}"><flexiPageRegions><itemInstances>'
            f"<componentInstance><componentName>c:{names[0]}</componentName>"
            "</componentInstance></itemInstances></flexiPageRegions></FlexiPage>"
        ).encode(),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "connected application graph")
    return repository


@pytest.fixture(scope="module")
def candidate_bundle(tmp_path_factory: pytest.TempPathFactory) -> CandidateAssuranceBundle:
    service, _ = _service(tmp_path_factory.mktemp("candidate-contract"))
    return service.analyze_current_candidate()


def test_host_owned_candidate_runs_both_modify_sides_and_persists(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)

    bundle = service.analyze_current_candidate()

    assert tuple(item.side.value for item in bundle.analyses) == ("BASE", "CANDIDATE")
    assert all(item.operation_scope == ("MODIFY",) for item in bundle.analyses)
    assert all(
        item.run.request.change_intent is ChangeIntent.VERIFIED_CHANGE for item in bundle.analyses
    )
    assert all(item.run.impacts for item in bundle.analyses)
    assert all(item.run.evidence for item in bundle.analyses)
    assert all(
        all(evidence.state.value == "CONFIRMED" for evidence in item.run.evidence)
        for item in bundle.analyses
    )
    assert all(
        "MISSING_SOURCE_HASH" not in {gap.code for gap in item.run.analysis_gaps}
        for item in bundle.analyses
    )
    for analysis in bundle.analyses:
        relevance_receipts = [
            evidence.attributes["relevance_receipt"]
            for evidence in analysis.run.evidence
            if "relevance_receipt" in evidence.attributes
        ]
        assert relevance_receipts
        assert all(receipt.get("source_hash") for receipt in relevance_receipts)
        assert all(
            receipt.get("valid_until") == bundle.graph_production_artifact.valid_until
            for receipt in relevance_receipts
        )
    assert all(item.run.decision.code is DecisionCode.INCOMPLETE for item in bundle.analyses)
    assert all(
        "RELEASE_EVIDENCE_MODEL_INCOMPLETE" in {gap.code for gap in item.run.analysis_gaps}
        for item in bundle.analyses
    )
    assert "CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE" in bundle.blocking_gap_codes
    assert bundle.checkpoint_commit_scope == "EXCLUDED_FROM_BUNDLE_TRANSACTION"
    assert set(repository.runs) == {item.run.run_id for item in bundle.analyses}
    assert bundle.release_eligible is False
    assert CandidateAssuranceBundle.model_validate(bundle.model_dump(mode="json")) == bundle


def test_candidate_endpoint_invokes_and_persists_specialist_capture(tmp_path: Path) -> None:
    repository_root = _repository(tmp_path)
    repository = InMemoryRunRepository()
    provider = _RecordingSpecialistProvider()
    service = AssuranceService(
        source(),
        repository,
        foundation_pipeline=_pipeline(repository_root),
        specialist_provider=provider,
    )

    bundle = service.analyze_current_candidate()
    view = build_candidate_assurance_view(bundle)

    assert len(provider.prompts) == sum(len(item.specialist_captures) for item in bundle.analyses)
    assert len(provider.prompts) == 6
    assert all(item.specialist_captures for item in bundle.analyses)
    for analysis in bundle.analyses:
        capture = analysis.specialist_captures[0]
        assert capture.provider_raw_response is not None
        assert capture.workflow_result.run_id == analysis.run.run_id
        assert capture.workflow_result.may_authorize is False
        assert capture.workflow_result.may_satisfy_release_evidence is False
        assert capture.artifact.may_authorize is False
        assert capture.expected_provider_profile_sha256 == provider.profile.profile_sha256
        assert capture.capture_sha256
    assert all(item.specialist_capture_count == 3 for item in view.analyses)
    assert all(len(item.specialist_artifact_sha256s) == 3 for item in view.analyses)


def test_live_operator_advisory_combines_live_read_and_real_candidate_advisory(
    tmp_path: Path,
) -> None:
    repository_root = _repository(tmp_path)
    repository = InMemoryRunRepository()
    provider = _RecordingSpecialistProvider()
    service = AssuranceService(
        source(),
        repository,
        foundation_pipeline=_pipeline(repository_root),
        specialist_provider=provider,
        live_diagnostic_reader=lambda: LiveSalesforceDiagnosticView(
            status="PASSED",
            target_alias_configured=True,
            connected=True,
        ),
    )

    view = service.run_live_operator_advisory_demo()

    assert view.authority_scope == "DIAGNOSTIC_ADVISORY_ONLY"
    assert view.live_salesforce.status == "PASSED"
    assert view.live_salesforce.release_eligible is False
    assert view.candidate_available is True
    assert view.llm_advisory_available is True
    assert view.candidate is not None
    assert view.candidate_analysis_count == 2
    assert view.specialist_capture_count == 6
    assert len(provider.prompts) == 6
    assert view.release_eligible is False
    assert view.gap_codes == ()


def test_live_operator_advisory_records_independent_blockers(tmp_path: Path) -> None:
    service = AssuranceService(
        source(),
        InMemoryRunRepository(),
        live_diagnostic_reader=lambda: LiveSalesforceDiagnosticView(
            status="BLOCKED",
            target_alias_configured=True,
            error_code="LIVE_SALESFORCE_NOT_CONNECTED",
        ),
    )

    view = service.run_live_operator_advisory_demo()

    assert view.live_salesforce.status == "BLOCKED"
    assert view.candidate_available is False
    assert view.llm_advisory_available is False
    assert view.specialist_capture_count == 0
    assert view.gap_codes == (
        "CANDIDATE_ADVISORY_UNAVAILABLE",
        "LIVE_SALESFORCE_NOT_CONNECTED",
    )


@pytest.mark.parametrize("component_name", ["panel", "accountSummary"])
def test_candidate_code_change_selects_graph_connected_test_without_name_rules(
    tmp_path: Path,
    component_name: str,
) -> None:
    repository = _lwc_repository(tmp_path, (component_name, "reviewPanel"))
    relative = f"workspace/dx/package/main/default/lwc/{component_name}/{component_name}.js"
    _write(repository, relative, b"export default class Component { changed = true; }")
    service, _ = _service_for_repository(repository)

    bundle = service.analyze_current_candidate()

    expected_test = (
        "component-test:workspace/dx/package/main/default/lwc/"
        f"{component_name}/__tests__/{component_name}.test.js"
    )
    for analysis in bundle.analyses:
        assert any(item.entity_id == f"lwc:{component_name}" for item in analysis.run.impacts)
        assert any(item.test_id == expected_test for item in analysis.run.selected_tests)
        assert "MISSING_SOURCE_HASH" not in {gap.code for gap in analysis.run.analysis_gaps}
        graph_receipts = [
            evidence.attributes.get("relevance_receipt", {})
            for evidence in analysis.run.evidence
            if evidence.attributes.get("relevance_receipt", {}).get("kind") == "graph-edge"
        ]
        assert graph_receipts
        assert all(receipt.get("source_hash") for receipt in graph_receipts)
        assert all(
            receipt.get("valid_until") == bundle.graph_production_artifact.valid_until
            for receipt in graph_receipts
        )


def test_candidate_non_code_metadata_change_selects_tests_through_application_graph(
    tmp_path: Path,
) -> None:
    repository = _lwc_repository(tmp_path, ("panel", "reviewPanel"))
    _write(
        repository,
        "workspace/dx/package/main/default/flexipages/Workspace.flexipage-meta.xml",
        (
            f'<FlexiPage xmlns="{NS}"><flexiPageRegions><itemInstances>'
            "<componentInstance><componentName>c:reviewPanel</componentName>"
            "</componentInstance></itemInstances></flexiPageRegions></FlexiPage>"
        ).encode(),
    )
    service, _ = _service_for_repository(repository)

    bundle = service.analyze_current_candidate()

    expected_by_side = {
        "BASE": (
            "component-test:workspace/dx/package/main/default/lwc/panel/__tests__/panel.test.js"
        ),
        "CANDIDATE": (
            "component-test:workspace/dx/package/main/default/lwc/reviewPanel/"
            "__tests__/reviewPanel.test.js"
        ),
    }
    for analysis in bundle.analyses:
        assert any(item.entity_id == "page:Workspace" for item in analysis.run.impacts)
        assert any(
            item.test_id == expected_by_side[analysis.side.value]
            for item in analysis.run.selected_tests
        )
        assert all(evidence.state.value == "CONFIRMED" for evidence in analysis.run.evidence)


def test_unsupported_salesforce_family_abstains_without_persisting_partial_analysis(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write(
        repository,
        "workspace/dx/package/main/default/aura/Panel/Panel.cmp",
        b"<aura:component/>",
    )
    service, run_repository = _service_for_repository(repository)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post("/api/v1/assurance-runs/analyze-current-candidate")

    assert response.status_code == 503
    assert response.json()["code"] == "FOUNDATION_PIPELINE_UNAVAILABLE"
    assert run_repository.runs == {}


def test_bundle_rejects_semantic_splice_even_with_recomputed_outer_digest(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    body = candidate_bundle.model_dump(mode="json")
    body["analyses"][0]["operation_scope"] = ["DELETE"]
    body.pop("bundle_sha256")
    body["bundle_sha256"] = stable_sha256(body)

    with pytest.raises(ValidationError, match="Operation scope"):
        CandidateAssuranceBundle.model_validate(body)


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (
            ("analyses", 0, "run", "request", "project_id"),
            "other-project",
            "artifact bindings",
        ),
        (("analyses", 0, "run", "ontology_sha256"), "a" * 64, "artifact bindings"),
        (
            ("analyses", 0, "run", "reasoning_policy_sha256"),
            "b" * 64,
            "artifact bindings",
        ),
    ],
)
def test_bundle_rejects_cross_identity_splice_with_recomputed_outer_digest(
    candidate_bundle: CandidateAssuranceBundle,
    path: tuple[str | int, ...],
    replacement: str,
    message: str,
) -> None:
    body = candidate_bundle.model_dump(mode="json")
    target: object = body
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]
    body.pop("bundle_sha256")
    body["bundle_sha256"] = stable_sha256(body)

    with pytest.raises(ValidationError, match=message):
        CandidateAssuranceBundle.model_validate(body)


def test_bundle_rejects_source_snapshot_splice_with_recomputed_outer_digest(
    candidate_bundle: CandidateAssuranceBundle,
) -> None:
    body = candidate_bundle.model_dump(mode="json")
    body["analyses"][0]["run"]["source_snapshot"] = "c" * 64
    body.pop("bundle_sha256")
    body["bundle_sha256"] = stable_sha256(body)

    with pytest.raises(ValidationError, match="artifact bindings"):
        CandidateAssuranceBundle.model_validate(body)


def test_candidate_project_identity_uses_source_canonical_form(tmp_path: Path) -> None:
    repository_root = _repository(tmp_path)
    pipeline = CandidateFoundationPipeline.from_host_configuration(
        project_id="fixture.project_name",
        repository_root=repository_root,
        salesforce_app_root=repository_root / "workspace" / "dx",
        implementation_root=ROOT,
    )
    repository = InMemoryRunRepository()
    service = AssuranceService(source(), repository, foundation_pipeline=pipeline)

    bundle = service.analyze_current_candidate()

    assert {item.run.request.project_id for item in bundle.analyses} == {"fixture-project-name"}


def test_verified_intent_requires_exact_artifact_and_seed_bindings() -> None:
    with pytest.raises(ValidationError):
        ChangeRequest(
            requirement="Analyze verified candidate",
            change_intent="VERIFIED_CHANGE",
        )


def test_public_request_cannot_claim_verified_change_authority() -> None:
    service = AssuranceService(source())
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/assurance-runs",
        json={
            "requirement": "Analyze verified candidate",
            "change_intent": "VERIFIED_CHANGE",
            "verified_change_manifest_sha256": "a" * 64,
            "verified_operation_seed_sha256": "b" * 64,
            "verified_seed_ids": ["object:Entity__c"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE"


def test_shared_service_rejects_forged_verified_change_authority() -> None:
    service = AssuranceService(source())
    request = ChangeRequest(
        requirement="Analyze verified candidate",
        change_intent="VERIFIED_CHANGE",
        verified_change_manifest_sha256="a" * 64,
        verified_operation_seed_sha256="b" * 64,
        verified_seed_ids=["object:Entity__c"],
    )

    with pytest.raises(ValueError, match="VERIFIED_CHANGE_REQUIRES_HOST_CAPTURE"):
        service.analyze(request)


def test_candidate_api_is_zero_body_and_returns_bounded_receipt_view(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    operation = client.get("/openapi.json").json()["paths"][
        "/api/v1/assurance-runs/analyze-current-candidate"
    ]["post"]
    response = client.post("/api/v1/assurance-runs/analyze-current-candidate")

    assert "requestBody" not in operation
    assert response.status_code == 200
    payload = response.json()
    assert payload["authority_scope"] == "ANALYSIS_ONLY"
    assert len(payload["analyses"]) == 2
    assert len(response.content) <= 32_768
    assert "foundation" not in payload
    assert "graph_production_artifact" not in payload
    assert "operation_seed_artifact" not in payload
    assert "run" not in payload["analyses"][0]
    assert payload["analyses"][0]["run_id"]
    run_response = client.get(f"/api/v1/assurance-runs/{payload['analyses'][0]['run_id']}")
    assert run_response.status_code == 200
    run = run_response.json()
    assert run["request"]["project_id"] == payload["analysis_identity"]["project_id"]
    assert run["source_snapshot"] == payload["analyses"][0]["source_snapshot"]
    assert run["ontology_sha256"] == payload["analysis_identity"]["ontology_sha256"]
    assert len(repository.runs) == 2


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"content": b"{}", "headers": {"content-type": "application/json"}},
        {"params": {"path": "private"}},
        {"headers": {"x-source-ref": "candidate"}},
    ],
)
def test_candidate_api_rejects_caller_scope_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict,
) -> None:
    service, repository = _service(tmp_path)
    calls = 0

    def forbidden() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(service, "analyze_current_candidate_view", forbidden)
    client = TestClient(create_app(settings=Settings(allow_llm=False), service=service))

    response = client.post(
        "/api/v1/assurance-runs/analyze-current-candidate",
        **request_kwargs,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "FOUNDATION_SCOPE_INPUT_FORBIDDEN"
    assert calls == 0
    assert repository.runs == {}
