"""Hash-only audit summaries for semantic capability analysis runs."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from src.enterprise_capability_profile import canonical_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_content_hash(response: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(response)).hexdigest()


def build_audit_summary(
    *,
    provider_name: str,
    provider_mode: str,
    model_name: str | None,
    prompt_version: str,
    request_content_hash: str,
    prompt_content_hash: str,
    response_hash: str | None,
    started_at_utc: str,
    finished_at_utc: str,
    duration_ms: int,
    attempt_count: int,
    network_used: bool,
    validation_result: str,
    config_source: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider_name": provider_name,
        "provider_mode": provider_mode,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "request_content_hash": request_content_hash,
        "prompt_content_hash": prompt_content_hash,
        "response_content_hash": response_hash,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "duration_ms": max(0, int(duration_ms)),
        "attempt_count": max(0, int(attempt_count)),
        "network_used": bool(network_used),
        "validation_result": validation_result,
        "configuration_source": config_source,
    }
    if error_code:
        result["error_code"] = error_code
    return result
