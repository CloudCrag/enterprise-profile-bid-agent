"""Enterprise evaluation data provider for the current real local profile store.

The current product does not synthesize, replay, or fall back to enterprise
records. Evaluation consumes only the verified enterprise profiles already
present in ``runtime_data/enterprise_profiles``. Missing external API data is
reported as unavailable and remains a data gap.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol


class EnterpriseDataProvider(Protocol):
    provider_id: str
    provider_version: str
    def search_enterprises(self, *, keyword: str) -> dict[str, Any]: ...
    def fetch_api(self, *, api_id: str, enterprise: dict[str, Any], query_params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...
    def describe_capabilities(self) -> dict[str, Any]: ...


class LocalProfileEnterpriseDataProvider:
    provider_id = "LOCAL_PROFILE"
    provider_version = "1.0.0"

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def describe_capabilities(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "display_name": "本地真实企业画像",
            "is_mock": False,
            "capabilities": ["enterprise_search", "verified_profile_read"],
        }

    def health(self) -> dict[str, Any]:
        companies = self.repository.list_companies()
        return {
            "status": "UP",
            "company_count": len(companies),
            **self.describe_capabilities(),
        }

    def search_enterprises(self, *, keyword: str) -> dict[str, Any]:
        token = str(keyword or "").strip().lower()
        records: list[dict[str, Any]] = []
        for company in self.repository.list_companies():
            enterprise = company.get("enterprise") or {}
            company_id = str(company.get("company_id") or "")
            name = str(enterprise.get("name") or enterprise.get("company_name") or "")
            code = str(enterprise.get("unified_social_credit_code") or "")
            haystack = " ".join((company_id, name, code)).lower()
            if not token or token in haystack:
                records.append({
                    "external_company_id": company_id,
                    "company_id": company_id,
                    "company_name": name,
                    "unified_social_credit_code": code,
                    "provider_id": self.provider_id,
                    "source_reference": f"runtime-profile:{company_id}",
                    "is_mock": False,
                })
        return {
            "status": "FOUND" if records else "EMPTY_RESULT",
            "records": records[:100],
            **self.describe_capabilities(),
        }

    def fetch_api(
        self,
        *,
        api_id: str,
        enterprise: dict[str, Any],
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # External enterprise API access is intentionally not invented. The
        # evaluation engine keeps the corresponding indicator as a data gap.
        return {
            "status": "DATA_NOT_AVAILABLE",
            "api_id": str(api_id),
            "data": None,
            "query_params": deepcopy(query_params or {}),
            "reason": "当前阶段仅使用本地真实企业画像，未接入该外部企业 API。",
            **self.describe_capabilities(),
        }


class ProviderRegistry:
    def __init__(self, repository: Any, providers: list[EnterpriseDataProvider] | None = None) -> None:
        values = providers or [LocalProfileEnterpriseDataProvider(repository)]
        self._providers = {provider.provider_id: provider for provider in values}

    def register(self, provider: EnterpriseDataProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> EnterpriseDataProvider:
        if provider_id not in self._providers:
            raise KeyError(f"EVALUATION_PROVIDER_NOT_REGISTERED:{provider_id}")
        return self._providers[provider_id]

    def list(self) -> list[dict[str, Any]]:
        return [{**provider.describe_capabilities(), "health": provider.health().get("status")} for provider in self._providers.values()]
