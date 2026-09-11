from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from neo_sf_q_intel.postgres_schema import (
    DEFAULT_POSTGRES_SCHEMA,
    validate_postgres_schema,
)
from neo_sf_q_intel.safety import require_safe_repository_locator

PROVIDER_CREDENTIAL_SOURCE_CONFLICT = "PROVIDER_CREDENTIAL_SOURCE_CONFLICT"

_COMMON_PROVIDER_ENV_NAMES = ("AI_PROVIDER",)
_OPENAI_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_CHAT_MODEL",
    "OPENAI_EMBEDDING_MODEL",
)
_AZURE_OPENAI_ENV_NAMES = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
)


class AIProvider(StrEnum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    _provider_configuration_codes: tuple[str, ...] = PrivateAttr(default=())

    def __init__(self, **values: Any) -> None:
        configured_env_file = values.get("_env_file", self.model_config.get("env_file"))
        super().__init__(**values)
        has_conflict = _has_provider_source_conflict(
            provider=self.ai_provider,
            configured_env_file=configured_env_file,
        )
        self._provider_configuration_codes = (
            (PROVIDER_CREDENTIAL_SOURCE_CONFLICT,) if self.allow_llm and has_conflict else ()
        )

    ai_provider: AIProvider = AIProvider.OPENAI
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-5.4"
    openai_embedding_model: str = "text-embedding-3-large"

    azure_openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_openai_embedding_deployment: str | None = None

    database_url: SecretStr | None = None
    postgres_schema: str = DEFAULT_POSTGRES_SCHEMA
    sqlite_path: Path = Path(".runtime/neo_sf_q_intel.db")
    outcome_sqlite_path: Path = Path(".runtime/outcome_memory.db")
    outcome_json_path: Path = Path(".runtime/outcome_memory")
    live_receipt_sqlite_path: Path = Path(".runtime/live_receipts.db")
    live_acceptance_profile_path: Path = Path("config/live-salesforce-acceptance-profile.json")
    live_acceptance_profile_sha256: str = (
        "5e4a91adbf78ffbfa6a9d973c00c8ed857e875339118324d0a590b3af1c54bb4"
    )
    live_product_receipt_issuer_id: str | None = None
    live_product_receipt_hmac_key: SecretStr | None = None
    live_product_receipt_roles: str = ""
    live_host_receipt_issuer_id: str | None = None
    live_host_receipt_hmac_key: SecretStr | None = None
    live_host_receipt_roles: str = ""
    live_baseline_enabled: bool = False
    live_baseline_config_path: Path = Path(".runtime/host-live-baseline.json")
    live_baseline_config_sha256: str | None = None
    live_target_policy_path: Path = Path("config/live-target-policy.json")
    live_target_policy_sha256: str | None = None
    live_assertion_sqlite_path: Path = Path(".runtime/live_assertions.db")
    live_baseline_sqlite_path: Path = Path(".runtime/live_baseline.db")
    live_local_validation_config_path: Path = Path(".runtime/host-local-validation.json")
    live_local_validation_config_sha256: str | None = None
    live_local_validation_phase_policy_enabled: bool = False
    live_local_validation_phase_policy_path: Path = Path(
        ".runtime/host-local-validation-phase-policy.json"
    )
    live_local_validation_phase_policy_sha256: str | None = None
    live_local_validation_artifact_sqlite_path: Path = Path(
        ".runtime/live_local_validation_artifacts.db"
    )
    salesforce_app_root: Path | None = None
    salesforce_repository_root: Path | None = None
    source_min_contract_version: str = "1.0.0"
    source_required_capabilities: str = ""
    canonical_ontology_path: Path = Path("config/ontology/canonical-ontology.json")
    source_graph_profile_path: Path = Path(
        "config/source-profiles/salesforce-application-graph.json"
    )
    source_graph_profile_sha256: str = (
        "20f062050584fa4259485df0f5dc00c3ef186562495583f6c846cd9adaa6f7ae"
    )
    sf_operator_alias: str | None = None
    sf_vp_alias: str | None = None
    sf_min_cli_version: str = "2.148.3"

    allow_llm: bool = True
    allow_salesforce_writes: bool = False
    allow_ui_execution: bool = True
    evidence_top_k: int = Field(default=8, ge=1, le=50)
    log_level: str = "INFO"
    web_allowed_origins: str = "http://localhost:3000,http://localhost:3100"

    @model_validator(mode="after")
    def validate_selected_provider(self) -> Settings:
        self.postgres_schema = validate_postgres_schema(self.postgres_schema)
        phase_path = self.live_local_validation_phase_policy_path.as_posix()
        require_safe_repository_locator(phase_path)
        if not phase_path.startswith(".runtime/") or not phase_path.endswith(".json"):
            raise ValueError(
                "Live local phase policy must be a private .runtime relative JSON file"
            )
        phase_sha = self.live_local_validation_phase_policy_sha256 or None
        self.live_local_validation_phase_policy_sha256 = phase_sha
        if (phase_sha is not None and re.fullmatch(r"[a-f0-9]{64}", phase_sha) is None) or (
            self.live_local_validation_phase_policy_enabled and phase_sha is None
        ):
            raise ValueError("Enabled live local phase policy requires an exact SHA-256 file pin")
        issuer_groups = (
            (
                self.live_product_receipt_issuer_id,
                self.live_product_receipt_hmac_key,
                self.live_product_receipt_roles,
            ),
            (
                self.live_host_receipt_issuer_id,
                self.live_host_receipt_hmac_key,
                self.live_host_receipt_roles,
            ),
        )
        if any(any(group) and not all(group) for group in issuer_groups):
            raise ValueError(
                "Each configured live receipt issuer requires an ID, secret key, and role list"
            )
        if not self.allow_llm:
            return self
        if self.ai_provider is AIProvider.OPENAI and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        if self.ai_provider is AIProvider.AZURE_OPENAI:
            required = {
                "AZURE_OPENAI_API_KEY": self.azure_openai_api_key,
                "AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
                "AZURE_OPENAI_API_VERSION": self.azure_openai_api_version,
                "AZURE_OPENAI_CHAT_DEPLOYMENT": self.azure_openai_chat_deployment,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": self.azure_openai_embedding_deployment,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing Azure OpenAI settings: {', '.join(missing)}")
        return self

    def resolved_salesforce_root(self, repository_root: Path | None = None) -> Path:
        root = self.salesforce_app_root
        if root is None:
            raise ValueError("SALESFORCE_APP_ROOT must identify a configured source project")
        if root.is_absolute():
            return root.resolve()
        return ((repository_root or Path.cwd()) / root).resolve()

    def parsed_web_allowed_origins(self) -> tuple[str, ...]:
        """Return a strict, exact CORS allowlist without wildcard or path semantics."""

        origins: list[str] = []
        for raw in self.web_allowed_origins.split(","):
            origin = raw.strip()
            if not origin:
                continue
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("WEB_ALLOWED_ORIGINS must contain exact HTTP(S) origins")
            canonical = f"{parsed.scheme}://{parsed.netloc}"
            if canonical != origin.rstrip("/"):
                raise ValueError("WEB_ALLOWED_ORIGINS contains a non-canonical origin")
            if canonical not in origins:
                origins.append(canonical)
        if not origins:
            raise ValueError("WEB_ALLOWED_ORIGINS must contain at least one exact origin")
        return tuple(origins)

    def resolved_salesforce_repository_root(self, repository_root: Path | None = None) -> Path:
        root = self.salesforce_repository_root
        if root is None:
            raise ValueError(
                "SALESFORCE_REPOSITORY_ROOT must identify the configured Git repository"
            )
        if root.is_absolute():
            return root.resolve()
        return ((repository_root or Path.cwd()) / root).resolve()

    def resolved_salesforce_roots(self, repository_root: Path | None = None) -> tuple[Path, Path]:
        git_root = self.resolved_salesforce_repository_root(repository_root)
        app_root = self.resolved_salesforce_root(repository_root)
        try:
            app_root.relative_to(git_root)
        except ValueError as exc:
            raise ValueError(
                "SALESFORCE_APP_ROOT must be within SALESFORCE_REPOSITORY_ROOT"
            ) from exc
        return git_root, app_root

    def resolved_sqlite_path(self, repository_root: Path | None = None) -> Path:
        if self.sqlite_path.is_absolute():
            return self.sqlite_path.resolve()
        return ((repository_root or Path.cwd()) / self.sqlite_path).resolve()

    def resolved_outcome_sqlite_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.outcome_sqlite_path, repository_root)

    def resolved_outcome_json_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.outcome_json_path, repository_root)

    def resolved_live_receipt_sqlite_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.live_receipt_sqlite_path, repository_root)

    def resolved_live_acceptance_profile_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.live_acceptance_profile_path, repository_root)

    def require_live_acceptance_profile_sha256(self) -> str:
        value = self.live_acceptance_profile_sha256.casefold()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("LIVE_ACCEPTANCE_PROFILE_SHA256 must be a lowercase SHA-256 digest")
        return value

    def resolved_canonical_ontology_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.canonical_ontology_path, repository_root)

    def resolved_source_graph_profile_path(self, repository_root: Path | None = None) -> Path:
        return self._resolved_repository_path(self.source_graph_profile_path, repository_root)

    @staticmethod
    def _resolved_repository_path(path: Path, repository_root: Path | None) -> Path:
        if path.is_absolute():
            return path.resolve()
        return ((repository_root or Path.cwd()) / path).resolve()

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        return tuple(
            capability.strip()
            for capability in self.source_required_capabilities.split(",")
            if capability.strip()
        )

    def require_operator_alias(self) -> str:
        if not self.sf_operator_alias:
            raise ValueError("SF_OPERATOR_ALIAS is required for Salesforce inspection")
        return self.sf_operator_alias

    def require_source_profile_sha256(self) -> str:
        value = self.source_graph_profile_sha256.casefold()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("SOURCE_GRAPH_PROFILE_SHA256 must be a lowercase SHA-256 digest")
        return value

    @property
    def provider_configuration_codes(self) -> tuple[str, ...]:
        """Return secret-safe provider startup blockers discovered from configured sources."""

        return self._provider_configuration_codes

    @property
    def provider_calls_blocked(self) -> bool:
        return PROVIDER_CREDENTIAL_SOURCE_CONFLICT in self._provider_configuration_codes


def _has_provider_source_conflict(
    *,
    provider: AIProvider,
    configured_env_file: object,
) -> bool:
    """Compare process and configured dotenv values without retaining or deriving secret data."""

    dotenv_environment = _read_configured_dotenv(configured_env_file)
    if not dotenv_environment:
        return False
    process_environment = {
        name.casefold(): value
        for name, value in os.environ.items()
        if isinstance(value, str) and value.strip()
    }
    compared_names = _COMMON_PROVIDER_ENV_NAMES + (
        _OPENAI_ENV_NAMES if provider is AIProvider.OPENAI else _AZURE_OPENAI_ENV_NAMES
    )
    return any(
        (process_value := process_environment.get(name.casefold())) is not None
        and (dotenv_value := dotenv_environment.get(name.casefold())) is not None
        and process_value != dotenv_value
        for name in compared_names
    )


def _read_configured_dotenv(configured_env_file: object) -> dict[str, str]:
    if configured_env_file is None:
        return {}
    if isinstance(configured_env_file, (str, os.PathLike)):
        configured_paths = (configured_env_file,)
    elif isinstance(configured_env_file, (tuple, list)):
        configured_paths = tuple(configured_env_file)
    else:
        return {}

    merged: dict[str, str] = {}
    for configured_path in configured_paths:
        if not isinstance(configured_path, (str, os.PathLike)):
            continue
        path = Path(configured_path)
        if not path.is_file():
            continue
        values = dotenv_values(
            dotenv_path=path,
            encoding="utf-8",
            interpolate=False,
        )
        merged.update(
            {
                name.casefold(): value
                for name, value in values.items()
                if isinstance(value, str) and value.strip()
            }
        )
    return merged
