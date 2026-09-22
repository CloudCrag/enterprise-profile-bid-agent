from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "token", "access_token", "secret", "password", "input"}
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)\s*[:=]\s*[^\s,;]+")
_PYDANTIC_INPUT_PATTERN = re.compile(r",?\s*input_value=.*?(?=,\s*input_type=|\]\s*$)")
_PYDANTIC_URL_PATTERN = re.compile(r"\s*For further information visit https?://\S+")


def safe_diagnostic_message(value: Any, *, max_length: int = 1000) -> str:
    text = str(value)
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    text = _ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _PYDANTIC_INPUT_PATTERN.sub("", text)
    text = _PYDANTIC_URL_PATTERN.sub("", text)
    return text[:max_length]


def safe_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return safe_diagnostic_message(value)
    if isinstance(value, (list, tuple, set)):
        return [safe_diagnostic_value(item, depth=depth + 1) for item in list(value)[:30]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                continue
            result[key_text] = safe_diagnostic_value(item, depth=depth + 1)
        return result
    return safe_diagnostic_message(value)


def safe_exception_feedback(exc: Exception) -> dict[str, Any]:
    error_code = getattr(exc, "error_code", None) or getattr(exc, "code", None)
    details = safe_diagnostic_value(getattr(exc, "details", {}) or {})
    result: dict[str, Any] = {
        "reason_type": type(exc).__name__,
        "reason_message": safe_diagnostic_message(exc),
    }
    if error_code:
        result["error_code"] = safe_diagnostic_message(error_code, max_length=200)
    if details:
        result["reason_details"] = details
    return result
