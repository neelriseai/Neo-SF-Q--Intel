from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    salesforce_app_root: Path = Path("../SalesForceAgentApp/strategic-deal-assurance")
    sf_operator_alias: str = "caip-dev"
    sf_vp_alias: str = "caip-vp"
    sf_min_cli_version: str = "2.148.3"

    allow_llm: bool = True
    allow_salesforce_writes: bool = False
    allow_ui_execution: bool = True
    evidence_top_k: int = Field(default=8, ge=1, le=50)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_selected_provider(self) -> Settings:
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
        if root.is_absolute():
            return root.resolve()
        return ((repository_root or Path.cwd()) / root).resolve()
