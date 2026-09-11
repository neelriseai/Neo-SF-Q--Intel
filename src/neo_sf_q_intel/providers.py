from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Any, Protocol

from openai import APITimeoutError, AsyncAzureOpenAI, AsyncOpenAI, AzureOpenAI, OpenAI

from neo_sf_q_intel.config import (
    PROVIDER_CREDENTIAL_SOURCE_CONFLICT,
    AIProvider,
    Settings,
)
from neo_sf_q_intel.specialist import (
    PromptEnvelope,
    ProviderCallOutcome,
    ProviderCallStatus,
    ProviderErrorCode,
    ProviderFinishReason,
    ProviderProfile,
    ProviderRefusalCode,
    provider_response_schema_document,
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


class OpenAISpecialistProvider:
    """Synchronous specialist ProviderPort backed by OpenAI Responses."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        if settings.provider_calls_blocked:
            raise ProviderConfigurationBlockedError(PROVIDER_CREDENTIAL_SOURCE_CONFLICT)
        self.settings = settings
        if settings.ai_provider is AIProvider.AZURE_OPENAI:
            self.client = client or AzureOpenAI(
                api_key=settings.azure_openai_api_key.get_secret_value(),  # type: ignore[union-attr]
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                max_retries=0,
            )
            provider_kind = "azure_openai"
            model_id = settings.azure_openai_chat_deployment or "azure-chat-deployment"
            deployment_id = model_id
            api_version = settings.azure_openai_api_version or "azure-api-version"
        else:
            self.client = client or OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),  # type: ignore[union-attr]
                max_retries=0,
            )
            provider_kind = "openai"
            model_id = settings.openai_chat_model
            deployment_id = settings.openai_chat_model
            api_version = "responses-v1"
        self.chat_model = model_id
        body = {
            "provider_kind": provider_kind,
            "model_id": model_id,
            "deployment_id": deployment_id,
            "model_version": model_id,
            "api_version": api_version,
            "response_format": "STRICT_JSON_SCHEMA",
            "temperature_milli": 0,
            "top_p_milli": 1000,
            "reasoning_profile": "bounded-graph-specialist-v1",
            "tools_enabled": False,
        }
        self._profile = ProviderProfile(**body, profile_sha256=_stable_hash(body))

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
        invoked = datetime.now(UTC)
        started = perf_counter_ns()
        parsed_prompt = _parse_prompt_envelope(prompt)
        if parsed_prompt is None:
            return self._failure(invoked, started, ProviderCallStatus.OUTAGE)
        instructions, provider_input = parsed_prompt
        try:
            response = self.client.responses.create(
                model=self.chat_model,
                instructions=instructions,
                input=provider_input,
                max_output_tokens=maximum_output_tokens,
                store=False,
                parallel_tool_calls=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "graph_reasoning_proposal",
                        "schema": provider_response_schema_document(),
                        "strict": True,
                    }
                },
                temperature=0,
                top_p=1,
                timeout=timeout_milliseconds / 1000,
            )
        except (APITimeoutError, TimeoutError):
            return self._failure(invoked, started, ProviderCallStatus.TIMEOUT)
        except Exception:
            return self._failure(invoked, started, ProviderCallStatus.OUTAGE)
        completed = datetime.now(UTC)
        elapsed = _elapsed_milliseconds(started)
        duration = _attested_duration(invoked, completed)
        if elapsed > timeout_milliseconds:
            return ProviderCallOutcome(
                status=ProviderCallStatus.TIMEOUT,
                provider_profile_sha256=self.profile.profile_sha256,
                invoked_at=_format_timestamp(invoked),
                completed_at=_format_timestamp(completed),
                duration_milliseconds=min(duration, timeout_milliseconds),
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
            )
        body = _model_dump(response)
        tool_count = _tool_call_count(body)
        if tool_count:
            return self._failure(invoked, started, ProviderCallStatus.OUTAGE)
        status = str(body.get("status") or "").lower()
        if _contains_refusal(body):
            return ProviderCallOutcome(
                status=ProviderCallStatus.REFUSAL,
                provider_profile_sha256=self.profile.profile_sha256,
                invoked_at=_format_timestamp(invoked),
                completed_at=_format_timestamp(completed),
                duration_milliseconds=duration,
                refusal_code=ProviderRefusalCode.PROVIDER_REFUSAL,
            )
        if status != "completed" or body.get("error"):
            return self._failure(invoked, started, ProviderCallStatus.OUTAGE)
        finish_reason = _finish_reason(body)
        output_text = getattr(response, "output_text", None)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        input_tokens = _int_or_none(usage.get("input_tokens"))
        output_tokens = _int_or_none(usage.get("output_tokens"))
        if not isinstance(output_text, str) or input_tokens is None or output_tokens is None:
            return self._failure(invoked, started, ProviderCallStatus.OUTAGE)
        return ProviderCallOutcome(
            status=ProviderCallStatus.SUCCESS,
            raw_response=output_text,
            provider_profile_sha256=self.profile.profile_sha256,
            invoked_at=_format_timestamp(invoked),
            completed_at=_format_timestamp(completed),
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_milliseconds=duration,
        )

    def _failure(
        self,
        invoked: datetime,
        started: int,
        status: ProviderCallStatus,
    ) -> ProviderCallOutcome:
        completed = datetime.now(UTC)
        return ProviderCallOutcome(
            status=status,
            provider_profile_sha256=self.profile.profile_sha256,
            invoked_at=_format_timestamp(invoked),
            completed_at=_format_timestamp(completed),
            duration_milliseconds=_attested_duration(invoked, completed),
            error_code=(
                ProviderErrorCode.PROVIDER_TIMEOUT
                if status is ProviderCallStatus.TIMEOUT
                else ProviderErrorCode.PROVIDER_OUTAGE
            ),
        )


def create_model_provider(settings: Settings) -> ModelProvider | None:
    if not settings.allow_llm:
        return None
    if settings.provider_calls_blocked:
        raise ProviderConfigurationBlockedError(PROVIDER_CREDENTIAL_SOURCE_CONFLICT)
    return OpenAIModelProvider(settings)


def create_specialist_provider(settings: Settings) -> OpenAISpecialistProvider | None:
    if not settings.allow_llm:
        return None
    if settings.provider_calls_blocked:
        raise ProviderConfigurationBlockedError(PROVIDER_CREDENTIAL_SOURCE_CONFLICT)
    return OpenAISpecialistProvider(settings)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _elapsed_milliseconds(started: int) -> int:
    return (perf_counter_ns() - started + 999_999) // 1_000_000


def _attested_duration(invoked: datetime, completed: datetime) -> int:
    return max(0, int((completed - invoked).total_seconds() * 1000))


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
    elif isinstance(value, dict):
        dumped = value
    else:
        dumped = {}
    return dumped if isinstance(dumped, dict) else {}


def _parse_prompt_envelope(prompt: str) -> tuple[str, str] | None:
    try:
        body = json.loads(
            prompt,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json,
        )
    except (ValueError, json.JSONDecodeError):
        return None
    try:
        envelope = PromptEnvelope.model_validate(body)
    except ValueError:
        return None
    data_body = envelope.model_dump(mode="json")
    instructions = data_body["instructions"]
    data_body.pop("instructions")
    return "\n".join(instructions), _canonical_json(data_body).decode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _tool_call_count(body: dict[str, Any]) -> int:
    output = body.get("output")
    if not isinstance(output, list):
        return 0
    return sum(
        1
        for item in output
        if isinstance(item, dict) and str(item.get("type") or "").endswith("_call")
    )


def _contains_refusal(value: Any) -> bool:
    if isinstance(value, dict):
        if "refusal" in value or value.get("type") == "refusal":
            return True
        return any(_contains_refusal(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_refusal(item) for item in value)
    return False


def _finish_reason(body: dict[str, Any]) -> ProviderFinishReason:
    status = str(body.get("status") or "").lower()
    incomplete = body.get("incomplete_details")
    reason = ""
    if isinstance(incomplete, dict):
        reason = str(incomplete.get("reason") or "").lower()
    if status == "incomplete" and "max" in reason:
        return ProviderFinishReason.LENGTH
    if "filter" in reason:
        return ProviderFinishReason.CONTENT_FILTER
    return ProviderFinishReason.STOP


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
