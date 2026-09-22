from __future__ import annotations

import json
import time

import httpx
from pydantic import ValidationError as PydanticValidationError

from src.llm.zhipu_message_builder import build_messages
from src.shared.errors import AgentException


def _safe_validation_errors(exc: PydanticValidationError) -> list[dict]:
    try:
        errors = exc.errors(include_input=False, include_url=False)
    except TypeError:
        try:
            errors = exc.errors(include_input=False)
        except TypeError:
            errors = exc.errors()
    sanitized: list[dict] = []
    for item in errors:
        clean = dict(item)
        clean.pop("input", None)
        clean.pop("url", None)
        sanitized.append(clean)
    return sanitized


class ZhipuGLMProvider:
    provider_name = "zhipu"
    provider_mode = "external"
    network_used = True

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: int = 180,
        max_retries: int = 2,
        max_response_bytes: int = 1_048_576,
        temperature: float = 0,
        max_tokens: int = 4096,
        transport=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.transport = transport
        self.calls: list[dict[str, str]] = []

    def generate_structured(self, *, system_prompt: str, user_payload: dict, output_schema):
        call_record = {"schema": output_schema.__name__, "model": self.model, "status": "STARTED"}
        self.calls.append(call_record)
        if not self.api_key:
            call_record["status"] = "FAILED"
            raise AgentException("zhipu_api_key_missing", "LLM_API_KEY is not configured", {})
        payload = {
            "model": self.model,
            "messages": build_messages(system_prompt, user_payload, output_schema),
            "thinking": {"type": "disabled"},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions", json=payload, headers=headers
                    )
                if response.status_code in {401, 403}:
                    raise AgentException("zhipu_authentication_failed", "Zhipu authentication failed", {})
                if response.status_code == 429:
                    raise AgentException("zhipu_rate_limited", "Zhipu rate limited", {})
                if response.status_code >= 500:
                    raise AgentException(
                        "zhipu_network_error",
                        "Zhipu server error",
                        {"status": response.status_code},
                    )
                response.raise_for_status()
                if len(response.content) > self.max_response_bytes:
                    raise AgentException(
                        "zhipu_network_error",
                        "Zhipu response exceeds configured byte limit",
                        {},
                    )
                try:
                    body = response.json()
                except (ValueError, json.JSONDecodeError) as exc:
                    raise AgentException(
                        "zhipu_response_json_invalid",
                        "Zhipu HTTP response is not valid JSON",
                        {},
                    ) from exc
                # Only choices[0].message.content is formal output. the hidden reasoning field is ignored.
                content = body.get("choices", [{}])[0].get("message", {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise AgentException(
                        "zhipu_empty_response",
                        "choices[0].message.content is empty",
                        {},
                    )
                try:
                    result = output_schema.model_validate_json(content)
                    call_record["status"] = "COMPLETED"
                    return result
                except PydanticValidationError as exc:
                    errors = _safe_validation_errors(exc)
                    if any(error.get("type") == "json_invalid" for error in errors):
                        raise AgentException(
                            "zhipu_response_json_invalid",
                            "Zhipu content is not valid JSON",
                            {},
                        ) from exc
                    raise AgentException(
                        "zhipu_response_schema_invalid",
                        "Zhipu JSON failed output schema",
                        {"errors": errors},
                    ) from exc
            except httpx.TimeoutException:
                last = AgentException("zhipu_timeout", "Zhipu request timed out", {})
            except httpx.NetworkError:
                last = AgentException("zhipu_network_error", "Zhipu network error", {})
            except AgentException as exc:
                if exc.error_code in {
                    "zhipu_authentication_failed",
                    "zhipu_rate_limited",
                    "zhipu_empty_response",
                    "zhipu_response_json_invalid",
                    "zhipu_response_schema_invalid",
                }:
                    call_record["status"] = "FAILED"
                    raise
                last = exc
            if attempt < self.max_retries:
                time.sleep(min(0.2 * (2**attempt), 1.0))
        call_record["status"] = "FAILED"
        raise last or AgentException("zhipu_network_error", "Zhipu request failed", {})
