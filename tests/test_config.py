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
