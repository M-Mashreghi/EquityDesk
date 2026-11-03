"""Typed application settings loaded from environment variables (and an optional `.env` file).

Secrets are held as `SecretStr` so they never appear in `repr()` / logs. Invalid or missing
required configuration fails fast with a single `ConfigError` listing every problem.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

CONFIG_SCHEMA_VERSION = "1"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    FAKE = "fake"
    ANTHROPIC = "anthropic"


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid. Never includes secret values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid EquityDesk configuration:\n- " + "\n- ".join(problems))


# env var name -> settings field name
ENV_KEYS: dict[str, str] = {
    "EQUITYDESK_ENV": "env",
    "EQUITYDESK_DB_PATH": "db_path",
    "EQUITYDESK_DATA_DIR": "data_dir",
    "EQUITYDESK_CACHE_DIR": "cache_dir",
    "EQUITYDESK_LOG_LEVEL": "log_level",
    "EQUITYDESK_LOG_FORMAT": "log_format",
    "SEC_USER_AGENT": "sec_user_agent",
    "SEC_MAX_REQUESTS_PER_SECOND": "sec_max_requests_per_second",
    "LLM_PROVIDER": "llm_provider",
    "LLM_MODEL": "llm_model",
    "LLM_API_KEY": "llm_api_key",
    "LLM_TIMEOUT_SECONDS": "llm_timeout_seconds",
    "LLM_MAX_RETRIES": "llm_max_retries",
    "ALERT_WEBHOOK_URL": "alert_webhook_url",
    "ALERT_WEBHOOK_TOKEN": "alert_webhook_token",
    "EQUITYDESK_API_URL": "api_url",
}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    env: Environment = Environment.DEVELOPMENT
    db_path: Path = Path("data/equitydesk.db")
    data_dir: Path = Path("data")
    cache_dir: Path = Path("data/cache")
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: str = Field(default="json", pattern=r"^(json|text)$")

    # SEC EDGAR fair-access policy requires a descriptive User-Agent with contact details.
    sec_user_agent: str | None = None
    sec_max_requests_per_second: float = Field(default=5.0, gt=0, le=10)

    llm_provider: LLMProviderName = LLMProviderName.FAKE
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=10)

    alert_webhook_url: SecretStr | None = None
    alert_webhook_token: SecretStr | None = None

    # Base URL the dashboard uses to reach the research API.
    api_url: str = Field(default="http://localhost:8000", pattern=r"^https?://")

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Settings:
        problems: list[str] = []
        if self.llm_provider is not LLMProviderName.FAKE:
            if self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip():
                problems.append("LLM_API_KEY is required when LLM_PROVIDER is not 'fake'")
            if not self.llm_model:
                problems.append("LLM_MODEL is required when LLM_PROVIDER is not 'fake'")
        if self.env is Environment.PRODUCTION:
            if self.llm_provider is LLMProviderName.FAKE:
                problems.append("LLM_PROVIDER=fake is not allowed in production")
            if not self.sec_user_agent:
                problems.append("SEC_USER_AGENT is required in production")
        if self.sec_user_agent is not None and "@" not in self.sec_user_agent:
            problems.append("SEC_USER_AGENT must contain a contact e-mail (SEC fair-access policy)")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def secret_values(self) -> list[str]:
        """All configured secret values, for registration with the log scrubber."""
        secrets = [self.llm_api_key, self.alert_webhook_url, self.alert_webhook_token]
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal `.env` parser: KEY=VALUE lines, `#` comments, optional surrounding quotes."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> Settings:
    """Build settings from an optional `.env` file overridden by process environment."""
    merged: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        merged.update(parse_env_file(env_file))
    merged.update(os.environ if environ is None else environ)

    raw: dict[str, Any] = {}
    for env_key, field in ENV_KEYS.items():
        value = merged.get(env_key)
        if value is not None and value != "":
            raw[field] = value
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"])
            env_name = next((k for k, v in ENV_KEYS.items() if v == field), field)
            message = err["msg"].removeprefix("Value error, ")
            problems.append(f"{env_name}: {message}" if field else message)
        raise ConfigError(problems) from None
