from __future__ import annotations
from dataclasses import dataclass


@dataclass(slots=True)
class AgentException(Exception):
    error_code: str
    message: str
    details: dict

    def __str__(self) -> str:
        return f"{self.error_code}: {self.message}"


class GatewayError(AgentException):
    pass


class ValidationError(AgentException):
    pass


ERROR_CODES = {
    "agent_invalid_request", "agent_task_not_found", "agent_checkpoint_not_found",
    "agent_schema_validation_failed", "agent_evidence_validation_failed",
    "agent_insufficient_data", "agent_waiting_confirmation", "agent_tool_failed",
    "agent_policy_blocked", "competition_data_unavailable", "competition_coverage_low",
    "competition_schema_invalid", "zhipu_api_key_missing", "zhipu_authentication_failed",
    "zhipu_rate_limited", "zhipu_timeout", "zhipu_network_error", "zhipu_empty_response",
    "zhipu_invalid_request", "zhipu_insufficient_balance", "zhipu_permission_denied",
    "zhipu_server_error", "zhipu_service_overloaded", "zhipu_response_too_large",
    "zhipu_response_json_invalid", "zhipu_response_schema_invalid",
}
