"""Official Zhipu GLM Chat Completions provider.

Only ``choices[0].message.content`` is accepted as business output.
``reasoning_content`` is intentionally ignored and is never persisted in audit data.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Any

import httpx

from .errors import CapabilityAIError
from .provider_config import CapabilityAIConfig
from .zhipu_message_builder import ZhipuGLMMessageBuilder

_RETRY_STATUS = {429, 500, 502, 503, 504}


class ZhipuGLMCapabilityAnalysisProvider:
    provider_name = "zhipu"
    provider_mode = "external"
    network_used = True

    def __init__(self, config: CapabilityAIConfig) -> None:
        self.config = config.validate()
        self.last_call_audit: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self, context: dict[str, Any]):
        if context.get("http_client") is not None:
            return context["http_client"], False
        return (
            httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=context.get("http_transport"),
            ),
            True,
        )

    def analyze(
        self,
        *,
        request: dict[str, Any],
        prompt: dict[str, Any],
        invocation_context: dict[str, Any],
    ) -> Any:
        # Zhipu does not require GET /models before a chat request. Configuration
        # validation is deliberately local and no discovery call is made here.
        messages = ZhipuGLMMessageBuilder().build(
            request=request,
            prompt=prompt,
            repair_error=invocation_context.get("repair_error"),
        )
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            # GLM-5.2 enables Thinking by default. This provider only accepts
            # choices[0].message.content as business output and intentionally
            # ignores reasoning_content, so disable Thinking for this bounded
            # structured-extraction request.
            "thinking": {"type": "disabled"},
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        client, close = self._client(invocation_context)
        attempt = 0
        started = time.monotonic()
        last_status: int | None = None
        try:
            while True:
                attempt += 1
                try:
                    response = client.post(
                        f"{self.config.effective_base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    last_status = response.status_code
                    if (
                        response.status_code in _RETRY_STATUS
                        and attempt <= self.config.max_retries
                    ):
                        time.sleep(
                            min(
                                0.05 * (2 ** (attempt - 1))
                                + random.random() * 0.01,
                                0.25,
                            )
                        )
                        continue
                    self._raise_http(response)
                    if len(response.content) > self.config.max_response_bytes:
                        raise CapabilityAIError(
                            "provider_response_too_large",
                            "provider_invocation",
                            "Zhipu response exceeds configured maximum",
                            {
                                "response_bytes": len(response.content),
                                "max_response_bytes": self.config.max_response_bytes,
                            },
                        )
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise CapabilityAIError(
                            "zhipu_response_json_invalid",
                            "provider_invocation",
                            "Zhipu HTTP envelope is not valid JSON",
                        ) from exc
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if not isinstance(choices, list) or not choices:
                        raise CapabilityAIError(
                            "zhipu_empty_response",
                            "provider_invocation",
                            "Zhipu response choices are missing",
                        )
                    first = choices[0] if isinstance(choices[0], dict) else {}
                    message = first.get("message") if isinstance(first, dict) else None
                    content = message.get("content") if isinstance(message, dict) else None
                    # reasoning_content is intentionally neither read nor copied.
                    if not isinstance(content, str) or not content.strip():
                        if attempt <= self.config.max_retries:
                            time.sleep(0.05)
                            continue
                        raise CapabilityAIError(
                            "zhipu_empty_response",
                            "provider_invocation",
                            "Zhipu response content is empty",
                        )
                    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                    self.last_call_audit = {
                        "provider": "zhipu",
                        "model": self.config.model_name,
                        "request_hash": request_hash,
                        "attempt_count": attempt,
                        "http_status_code": response.status_code,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "token_usage": {
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                        },
                        "finish_reason": first.get("finish_reason"),
                        "network_used": True,
                        "validation_result": "pending",
                        "error_code": None,
                    }
                    return content
                except httpx.TimeoutException as exc:
                    if attempt <= self.config.max_retries:
                        time.sleep(0.05)
                        continue
                    raise CapabilityAIError(
                        "zhipu_timeout",
                        "provider_invocation",
                        "Zhipu request timed out",
                    ) from exc
                except httpx.NetworkError as exc:
                    if attempt <= self.config.max_retries:
                        time.sleep(0.05)
                        continue
                    raise CapabilityAIError(
                        "zhipu_network_error",
                        "provider_invocation",
                        "Zhipu network request failed",
                    ) from exc
        except CapabilityAIError as exc:
            self.last_call_audit = {
                "provider": "zhipu",
                "model": self.config.model_name,
                "request_hash": request_hash,
                "attempt_count": attempt,
                "http_status_code": last_status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "token_usage": {},
                "finish_reason": None,
                "network_used": True,
                "validation_result": "failed",
                "error_code": exc.code,
            }
            raise
        finally:
            if close:
                client.close()

    @staticmethod
    def _raise_http(response: httpx.Response) -> None:
        code = {
            400: "zhipu_invalid_request",
            401: "zhipu_authentication_failed",
            402: "zhipu_insufficient_balance",
            403: "zhipu_permission_denied",
            422: "zhipu_invalid_request",
            429: "zhipu_rate_limited",
            500: "zhipu_server_error",
            502: "zhipu_server_error",
            503: "zhipu_service_overloaded",
            504: "zhipu_timeout",
        }.get(response.status_code)
        if code:
            raise CapabilityAIError(
                code,
                "provider_invocation",
                f"Zhipu HTTP request failed with status {response.status_code}",
                {"http_status_code": response.status_code},
            )
        if response.status_code >= 400:
            raise CapabilityAIError(
                "zhipu_invalid_request"
                if response.status_code < 500
                else "zhipu_server_error",
                "provider_invocation",
                f"Zhipu HTTP request failed with status {response.status_code}",
                {"http_status_code": response.status_code},
            )
