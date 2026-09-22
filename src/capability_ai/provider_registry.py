"""Provider registry restricted to the official Zhipu GLM implementation."""
from __future__ import annotations

from collections.abc import Callable

from .errors import CapabilityAIError
from .provider import CapabilityAnalysisProvider
from .provider_config import CapabilityAIConfig
from .zhipu_provider import ZhipuGLMCapabilityAnalysisProvider

ProviderFactory = Callable[[CapabilityAIConfig], CapabilityAnalysisProvider]
_REGISTRY: dict[str, ProviderFactory] = {
    "zhipu": lambda config: ZhipuGLMCapabilityAnalysisProvider(config),
}


def normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized != "zhipu":
        raise CapabilityAIError(
            "provider_unknown",
            "provider_registry",
            "Only the zhipu capability provider is supported",
            {"provider": normalized or None},
        )
    return normalized


def register_provider(name: str, factory: ProviderFactory) -> None:
    normalized = normalize_provider_name(name)
    if normalized in _REGISTRY:
        raise CapabilityAIError(
            "provider_already_registered",
            "provider_registry",
            f"Provider is already registered: {normalized}",
            {"provider": normalized},
        )
    _REGISTRY[normalized] = factory


def get_provider(name: str, config: CapabilityAIConfig) -> CapabilityAnalysisProvider:
    normalized = normalize_provider_name(name)
    return _REGISTRY[normalized](config)


def registered_provider_names() -> tuple[str, ...]:
    return ("zhipu",)
