"""Environment-first, secret-safe configuration for the only supported capability provider: Zhipu GLM."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping

from src.config.environment_loader import load_project_environment

from .errors import CapabilityAIError

load_project_environment()

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_PROMPT_VERSION = "enterprise-capability-semantic-prompt/1.0.0"
DEFAULT_ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 4096


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_int(name: str, value: str | None, default: int, minimum: int, maximum: int) -> int:
    cleaned = _clean(value)
    if cleaned is None:
        return default
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise CapabilityAIError(
            "capability_ai_config_invalid",
            "configuration",
            f"{name} must be an integer",
            {"field": name},
        ) from exc
    if not minimum <= parsed <= maximum:
        raise CapabilityAIError(
            "capability_ai_config_invalid",
            "configuration",
            f"{name} must be between {minimum} and {maximum}",
            {"field": name},
        )
    return parsed


def _parse_float(name: str, value: str | None, default: float, minimum: float, maximum: float) -> float:
    cleaned = _clean(value)
    if cleaned is None:
        return default
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise CapabilityAIError(
            "capability_ai_config_invalid",
            "configuration",
            f"{name} must be numeric",
            {"field": name},
        ) from exc
    if not minimum <= parsed <= maximum:
        raise CapabilityAIError(
            "capability_ai_config_invalid",
            "configuration",
            f"{name} must be between {minimum} and {maximum}",
            {"field": name},
        )
    return parsed


@dataclass(frozen=True, slots=True)
class CapabilityAIConfig:
    provider: str = "zhipu"
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    prompt_version: str = DEFAULT_PROMPT_VERSION
    provider_source: str = "environment"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CapabilityAIConfig":
        source = os.environ if env is None else env
        raw_provider = (_clean(source.get("CAPABILITY_AI_PROVIDER")) or "zhipu").lower()
        return cls(
            provider=raw_provider,
            model_name=_clean(source.get("CAPABILITY_AI_MODEL")),
            base_url=_clean(source.get("CAPABILITY_AI_BASE_URL")),
            api_key=_clean(source.get("CAPABILITY_AI_API_KEY")),
            timeout_seconds=_parse_int(
                "CAPABILITY_AI_TIMEOUT_SECONDS",
                source.get("CAPABILITY_AI_TIMEOUT_SECONDS"),
                DEFAULT_TIMEOUT_SECONDS,
                1,
                600,
            ),
            max_retries=_parse_int(
                "CAPABILITY_AI_MAX_RETRIES",
                source.get("CAPABILITY_AI_MAX_RETRIES"),
                DEFAULT_MAX_RETRIES,
                0,
                10,
            ),
            max_response_bytes=_parse_int(
                "CAPABILITY_AI_MAX_RESPONSE_BYTES",
                source.get("CAPABILITY_AI_MAX_RESPONSE_BYTES"),
                DEFAULT_MAX_RESPONSE_BYTES,
                1024,
                10_485_760,
            ),
            temperature=_parse_float(
                "CAPABILITY_AI_TEMPERATURE",
                source.get("CAPABILITY_AI_TEMPERATURE"),
                DEFAULT_TEMPERATURE,
                0,
                2,
            ),
            max_tokens=_parse_int(
                "CAPABILITY_AI_MAX_TOKENS",
                source.get("CAPABILITY_AI_MAX_TOKENS"),
                DEFAULT_MAX_TOKENS,
                1,
                65_536,
            ),
            prompt_version=_clean(source.get("CAPABILITY_AI_PROMPT_VERSION")) or DEFAULT_PROMPT_VERSION,
            provider_source="environment",
        ).validate()

    def with_cli_overrides(
        self,
        *,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> "CapabilityAIConfig":
        result = self
        if _clean(provider):
            result = replace(result, provider=_clean(provider).lower(), provider_source="cli")
        if _clean(model_name):
            result = replace(result, model_name=_clean(model_name))
        return result.validate()

    def validate(self) -> "CapabilityAIConfig":
        if self.provider != "zhipu":
            raise CapabilityAIError(
                "provider_unknown",
                "configuration",
                "Only CAPABILITY_AI_PROVIDER=zhipu is supported",
                {"provider": self.provider},
            )
        if self.prompt_version != DEFAULT_PROMPT_VERSION:
            raise CapabilityAIError(
                "prompt_version_not_supported",
                "configuration",
                f"Unsupported prompt version: {self.prompt_version}",
                {"prompt_version": self.prompt_version},
            )
        if not self.api_key:
            raise CapabilityAIError(
                "zhipu_api_key_missing",
                "configuration",
                "CAPABILITY_AI_API_KEY is not configured",
            )
        if not self.model_name:
            raise CapabilityAIError(
                "zhipu_model_missing",
                "configuration",
                "CAPABILITY_AI_MODEL is not configured",
            )
        return self

    @property
    def effective_base_url(self) -> str:
        return (self.base_url or DEFAULT_ZHIPU_BASE_URL).rstrip("/")

    def safe_summary(self) -> dict[str, object]:
        return {
            "provider": "zhipu",
            "model_name": self.model_name,
            "base_url_configured": bool(self.base_url),
            "api_key_configured": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "prompt_version": self.prompt_version,
        }
