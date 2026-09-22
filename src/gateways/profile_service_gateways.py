"""Gateways for the local enterprise-profile service.

The profile service owns the real local enterprise JSON.  These adapters use the
local Node gateway only; they never substitute fixture, mock or replay data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from urllib.parse import quote

import httpx

from src.shared.errors import GatewayError
from src.shared.schemas import CompanyProfileSnapshot, EnterpriseEvaluationSnapshot


@dataclass(slots=True)
class NodeEnvelopeClient:
    base_url: str
    timeout_seconds: float = 15.0

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = httpx.request(
                method,
                url,
                json=json_body,
                params=query,
                headers={"x-trace-id": "bid-agent-profile-adapter"},
                timeout=self.timeout_seconds,
                trust_env=False,
            )
        except httpx.TimeoutException as exc:
            raise GatewayError(
                "profile_service_timeout",
                "企业画像服务请求超时。",
                {"method": method, "path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayError(
                "profile_service_unavailable",
                "企业画像服务连接失败。",
                {"method": method, "path": path, "type": type(exc).__name__},
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GatewayError(
                "agent_schema_validation_failed",
                "企业画像服务返回了无效 JSON。",
                {"method": method, "path": path, "status_code": response.status_code},
            ) from exc
        if not isinstance(payload, dict):
            raise GatewayError(
                "agent_schema_validation_failed",
                "企业画像服务响应必须是对象。",
                {"method": method, "path": path},
            )
        if response.is_error or payload.get("success") is not True:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise GatewayError(
                str(payload.get("error_code") or error.get("code") or "agent_tool_failed"),
                str(payload.get("message") or error.get("message") or "企业画像服务请求失败。"),
                payload.get("details") if isinstance(payload.get("details"), dict) else {},
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GatewayError(
                "agent_schema_validation_failed",
                "企业画像服务成功响应 data 必须是对象。",
                {"method": method, "path": path},
            )
        return data


class ProfileServiceCompanyGateway:
    def __init__(self, client: NodeEnvelopeClient):
        self.client = client

    def get_company_profile(
        self,
        company_id: str,
        *,
        requested_profile_version: str | None = None,
        as_of_time: datetime | None = None,
        current_task_constraints: dict[str, Any] | None = None,
    ) -> CompanyProfileSnapshot:
        data = self.client.request(
            "POST",
            f"api/integration/companies/{quote(company_id, safe='')}/bid-profile-snapshot",
            json_body={
                "requestedProfileVersion": requested_profile_version,
                "asOfTime": as_of_time.isoformat() if as_of_time is not None else None,
                "currentTaskConstraints": current_task_constraints or {},
            },
        )
        profile = CompanyProfileSnapshot.model_validate_json(json.dumps(data, ensure_ascii=False))
        if profile.company_id != company_id:
            raise GatewayError(
                "agent_schema_validation_failed",
                "企业画像服务返回了不同的企业 ID。",
                {"requested": company_id, "returned": profile.company_id},
            )
        return profile


class ProfileServiceEvaluationGateway:
    def __init__(self, client: NodeEnvelopeClient):
        self.client = client

    def get_enterprise_evaluation(
        self,
        company_id: str,
        profile_version: str,
    ) -> EnterpriseEvaluationSnapshot:
        data = self.client.request(
            "GET",
            f"api/integration/companies/{quote(company_id, safe='')}/bid-evaluation-snapshot",
            query={"profile_version": profile_version},
        )
        evaluation = EnterpriseEvaluationSnapshot.model_validate_json(json.dumps(data, ensure_ascii=False))
        if evaluation.company_id != company_id or evaluation.profile_version != profile_version:
            raise GatewayError(
                "agent_schema_validation_failed",
                "企业评价快照与请求版本不一致。",
                {
                    "requested_company_id": company_id,
                    "requested_profile_version": profile_version,
                    "returned_company_id": evaluation.company_id,
                    "returned_profile_version": evaluation.profile_version,
                },
            )
        return evaluation


class ProfileServiceUpdateGateway:
    def __init__(self, client: NodeEnvelopeClient):
        self.client = client
        self.supplied_fields: dict[str, dict[str, Any]] = {}

    def update_profile(
        self,
        company_id: str,
        current_profile_version: str,
        provided_fields: dict[str, Any],
    ) -> CompanyProfileSnapshot:
        if not provided_fields:
            raise GatewayError("agent_invalid_request", "provided_fields must not be empty", {})
        data = self.client.request(
            "POST",
            f"api/integration/companies/{quote(company_id, safe='')}/profile-updates",
            json_body={
                "currentProfileVersion": current_profile_version,
                "providedFields": provided_fields,
            },
        )
        updated = CompanyProfileSnapshot.model_validate_json(json.dumps(data, ensure_ascii=False))
        if updated.company_id != company_id or updated.profile_version == current_profile_version:
            raise GatewayError(
                "agent_schema_validation_failed",
                "画像补充必须为同一家企业生成新版本。",
                {
                    "company_id": company_id,
                    "current_profile_version": current_profile_version,
                    "returned_company_id": updated.company_id,
                    "returned_profile_version": updated.profile_version,
                },
            )
        accumulated = dict(self.supplied_fields.get(company_id, {}))
        accumulated.update(provided_fields)
        self.supplied_fields[company_id] = accumulated
        return updated
