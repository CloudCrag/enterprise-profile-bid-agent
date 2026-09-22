"""Strict response parser for provider output.

It intentionally does not repair malformed JSON, strip Markdown, or extract JSON from
surrounding prose. Future network providers must obey the same boundary.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import CapabilityAIError


class ResponseParser:
    def __init__(self, *, max_response_bytes: int) -> None:
        self.max_response_bytes = max_response_bytes

    def parse(self, response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._check_size(len(encoded))
            return response
        if not isinstance(response, str):
            raise CapabilityAIError(
                code="provider_response_type_invalid",
                stage="response_parsing",
                message="Provider response must be a Python dict or UTF-8 JSON string",
            )
        encoded = response.encode("utf-8")
        self._check_size(len(encoded))
        stripped = response.strip()
        if stripped.startswith("```") or "```json" in stripped.lower():
            raise CapabilityAIError(
                code="provider_response_markdown_not_allowed",
                stage="response_parsing",
                message="Markdown-wrapped provider responses are not allowed",
            )
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CapabilityAIError(
                code="provider_response_json_invalid",
                stage="response_parsing",
                message=f"Provider response is not one complete JSON object at line {exc.lineno}, column {exc.colno}",
            ) from exc
        if not isinstance(parsed, dict):
            raise CapabilityAIError(
                code="provider_response_top_level_invalid",
                stage="response_parsing",
                message="Provider response JSON top level must be an object",
            )
        return parsed

    def _check_size(self, size: int) -> None:
        if size > self.max_response_bytes:
            raise CapabilityAIError(
                code="provider_response_too_large",
                stage="response_parsing",
                message="Provider response exceeds configured maximum size",
                details={"response_bytes": size, "max_response_bytes": self.max_response_bytes},
            )
