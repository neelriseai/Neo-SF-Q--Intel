from __future__ import annotations

import json
from typing import Any, Protocol

from openai import AsyncAzureOpenAI, AsyncOpenAI

from neo_sf_q_intel.config import (
    PROVIDER_CREDENTIAL_SOURCE_CONFLICT,
    AIProvider,
    Settings,
)


class ProviderConfigurationBlockedError(RuntimeError):
    """Raised before network dispatch when provider configuration is unsafe."""

    code = PROVIDER_CREDENTIAL_SOURCE_CONFLICT


class ModelProvider(Protocol):
    async def reason_json(
        self, *, instructions: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIModelProvider:
    def __init__(self, settings: Settings) -> None:
        if settings.provider_calls_blocked:
            raise ProviderConfigurationBlockedError(PROVIDER_CREDENTIAL_SOURCE_CONFLICT)
        self.settings = settings
        if settings.ai_provider is AIProvider.AZURE_OPENAI:
            self.client = AsyncAzureOpenAI(
                api_key=settings.azure_openai_api_key.get_secret_value(),  # type: ignore[union-attr]
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
            )
            self.chat_model = settings.azure_openai_chat_deployment
            self.embedding_model = settings.azure_openai_embedding_deployment
        else:
            self.client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value()  # type: ignore[union-attr]
            )
            self.chat_model = settings.openai_chat_model
            self.embedding_model = settings.openai_embedding_model

    async def reason_json(self, *, instructions: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.responses.create(
            model=self.chat_model,
            instructions=instructions,
            input=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        )
        parsed = json.loads(response.output_text)
        if not isinstance(parsed, dict):
            raise ValueError("Model response must be one JSON object")
        return parsed

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(model=self.embedding_model, input=texts)
        return [row.embedding for row in response.data]


def create_model_provider(settings: Settings) -> ModelProvider | None:
    if not settings.allow_llm:
        return None
    if settings.provider_calls_blocked:
        raise ProviderConfigurationBlockedError(PROVIDER_CREDENTIAL_SOURCE_CONFLICT)
    return OpenAIModelProvider(settings)
