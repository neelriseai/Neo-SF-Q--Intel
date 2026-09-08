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


def test_source_project_must_be_explicitly_configured() -> None:
    with pytest.raises(ValueError, match="SALESFORCE_APP_ROOT"):
        Settings(
            allow_llm=False, salesforce_app_root=None, _env_file=None
        ).resolved_salesforce_root()


def test_source_graph_digest_must_be_explicitly_configured() -> None:
    with pytest.raises(ValueError, match="SOURCE_GRAPH_SHA256"):
        Settings(allow_llm=False, source_graph_sha256=None, _env_file=None).require_graph_sha256()


def test_sqlite_fallback_path_is_repository_relative() -> None:
    settings = Settings(allow_llm=False, sqlite_path=Path("runtime/fallback.db"))

    assert settings.resolved_sqlite_path(Path("workspace")).is_absolute()
    assert settings.resolved_sqlite_path(Path("workspace")).name == "fallback.db"
