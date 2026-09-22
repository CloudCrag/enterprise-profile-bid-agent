"""Stable request identifiers and the fixed target-code catalogs for profile gap analysis."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import CAPABILITY_TYPES, canonical_json, stable_enterprise_id
from ..enterprise_decision_profile import DECISION_FIELD_CODES
from ..enterprise_fact_profile import FACT_TYPES

GAP_ANALYSIS_REQUEST_SCHEMA_VERSION = "enterprise-profile-gap-analysis-request/1.0.0"
GAP_INVENTORY_SCHEMA_VERSION = "enterprise-profile-gap-inventory/1.0.0"

TARGET_LAYERS = ("fact", "capability", "decision")
IMPORTANCE_VALUES = ("blocking", "important", "optional")
IMPORTANCE_ORDER = {value: index for index, value in enumerate(IMPORTANCE_VALUES)}
TARGET_LAYER_ORDER = {value: index for index, value in enumerate(TARGET_LAYERS)}
TARGET_CODE_CATALOG = {
    "fact": frozenset(FACT_TYPES),
    "capability": frozenset(CAPABILITY_TYPES),
    "decision": frozenset(DECISION_FIELD_CODES),
}


def requirement_sort_key(requirement: dict[str, Any]) -> tuple[Any, ...]:
    return (
        IMPORTANCE_ORDER.get(str(requirement.get("importance")), 99),
        TARGET_LAYER_ORDER.get(str(requirement.get("target_layer")), 99),
        str(requirement.get("target_code") or ""),
        str(requirement.get("requirement_id") or ""),
    )


def normalize_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((deepcopy(item) for item in requirements), key=requirement_sort_key)


def gap_analysis_request_content_payload(request: dict[str, Any]) -> dict[str, Any]:
    task_context = request.get("task_context") if isinstance(request.get("task_context"), dict) else {}
    return {
        "gap_analysis_request_schema_version": request.get("gap_analysis_request_schema_version"),
        "enterprise": deepcopy(request.get("enterprise")),
        "task_context": {
            "task_context_id": task_context.get("task_context_id"),
            "task_goal": task_context.get("task_goal"),
            "requirements": normalize_requirements(task_context.get("requirements") or []),
        },
    }


def gap_analysis_request_content_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(gap_analysis_request_content_payload(request))).hexdigest()


def gap_analysis_request_id(request: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or gap_analysis_request_content_hash(request)
    return f"enterprise-profile-gap-request:{stable_enterprise_id(request.get('enterprise') or {})}:{content_digest[:16]}"


def finalize_gap_analysis_request(request: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(request)
    context = result.setdefault("task_context", {})
    context["requirements"] = normalize_requirements(context.get("requirements") or [])
    digest = gap_analysis_request_content_hash(result)
    result["request_content_hash"] = digest
    result["request_id"] = gap_analysis_request_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
