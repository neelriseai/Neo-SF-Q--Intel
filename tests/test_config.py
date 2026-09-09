from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.config import AIProvider, Settings


def test_openai_and_azure_are_explicit_profiles() -> None:
    openai = Settings(allow_llm=False, ai_provider="openai")
    azure = Settings(
        ai_provider="azure_openai",
        azure_openai_api_key="secret",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_version="your-supported-api-version",
        azure_openai_chat_deployment="chat",
        azure_openai_embedding_deployment="embedding",
    )
    assert openai.ai_provider is AIProvider.OPENAI
    assert azure.ai_provider is AIProvider.AZURE_OPENAI


def test_selected_provider_requires_only_its_credentials() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(ai_provider="openai", openai_api_key=None)


def test_relative_salesforce_root_is_resolved_from_repository() -> None:
    settings = Settings(allow_llm=False, salesforce_app_root=Path("../app"))
    expected = Path("workspace/app").resolve()
    assert settings.resolved_salesforce_root(Path("workspace/agent")) == expected


def test_distinct_repository_and_nested_app_roots_are_validated() -> None:
    settings = Settings(
        allow_llm=False,
        salesforce_repository_root=Path("../app-repository"),
        salesforce_app_root=Path("../app-repository/dx-project"),
    )

    git_root, app_root = settings.resolved_salesforce_roots(Path("workspace/agent"))

    assert git_root == Path("workspace/app-repository").resolve()
    assert app_root == Path("workspace/app-repository/dx-project").resolve()


def test_salesforce_app_root_outside_repository_is_rejected() -> None:
    settings = Settings(
        allow_llm=False,
        salesforce_repository_root=Path("../app-repository"),
        salesforce_app_root=Path("../different-project"),
    )

    with pytest.raises(ValueError, match="must be within"):
        settings.resolved_salesforce_roots(Path("workspace/agent"))


def test_source_project_must_be_explicitly_configured() -> None:
    with pytest.raises(ValueError, match="SALESFORCE_APP_ROOT"):
        Settings(
            allow_llm=False, salesforce_app_root=None, _env_file=None
        ).resolved_salesforce_root()


def test_source_repository_must_be_explicitly_configured() -> None:
    with pytest.raises(ValueError, match="SALESFORCE_REPOSITORY_ROOT"):
        Settings(
            allow_llm=False, salesforce_repository_root=None, _env_file=None
        ).resolved_salesforce_repository_root()


def test_source_graph_digest_must_be_explicitly_configured() -> None:
    with pytest.raises(ValueError, match="SOURCE_GRAPH_SHA256"):
        Settings(allow_llm=False, source_graph_sha256=None, _env_file=None).require_graph_sha256()


def test_sqlite_fallback_path_is_repository_relative() -> None:
    settings = Settings(allow_llm=False, sqlite_path=Path("runtime/fallback.db"))

    assert settings.resolved_sqlite_path(Path("workspace")).is_absolute()
    assert settings.resolved_sqlite_path(Path("workspace")).name == "fallback.db"


def test_outcome_fallback_paths_are_repository_relative() -> None:
    settings = Settings(
        allow_llm=False,
        outcome_sqlite_path=Path("runtime/outcomes.db"),
        outcome_json_path=Path("runtime/outcomes"),
    )

    assert (
        settings.resolved_outcome_sqlite_path(Path("workspace"))
        == Path("workspace/runtime/outcomes.db").resolve()
    )
    assert (
        settings.resolved_outcome_json_path(Path("workspace"))
        == Path("workspace/runtime/outcomes").resolve()
    )


def test_ontology_and_source_profile_paths_are_repository_relative() -> None:
    settings = Settings(allow_llm=False)

    ontology = settings.resolved_canonical_ontology_path(Path("workspace"))
    profile = settings.resolved_source_graph_profile_path(Path("workspace"))

    assert ontology == Path("workspace/config/ontology/canonical-ontology.json").resolve()
    assert (
        profile
        == Path("workspace/config/source-profiles/salesforce-application-graph.json").resolve()
    )
    assert len(settings.require_source_profile_sha256()) == 64


def test_source_profile_digest_pin_must_be_valid() -> None:
    settings = Settings(allow_llm=False, source_graph_profile_sha256="not-a-digest")

    with pytest.raises(ValueError, match="SOURCE_GRAPH_PROFILE_SHA256"):
        settings.require_source_profile_sha256()
