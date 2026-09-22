"""Provider protocol for semantic capability analysis.

Providers are deliberately side-effect free: they receive immutable-style dictionaries
and return a response object. They may not mutate enterprise facts, tags, profiles,
preferences, databases, or external systems.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CapabilityAnalysisProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def provider_mode(self) -> str:
        ...

    @property
    def network_used(self) -> bool:
        ...

    def analyze(
        self,
        *,
        request: dict[str, Any],
        prompt: dict[str, Any],
        invocation_context: dict[str, Any],
    ) -> Any:
        ...
