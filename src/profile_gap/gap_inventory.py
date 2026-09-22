"""Stable identifiers and hashes for task-scoped enterprise profile gap inventories."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from .request import GAP_INVENTORY_SCHEMA_VERSION, requirement_sort_key

GAP_STATUSES = {
    "missing",
    "not_provided",
    "insufficient_data",
    "ambiguous",
    "conflicted",
    "unknown_availability",
    "needs_more_evidence",
    "pending_review",
}
RESOLUTION_TYPES = {
    "user_confirmation_required",
    "verified_fact_required",
    "supporting_material_required",
    "conflict_review_required",
    "capability_review_required",
}


def gap_content_payload(gap: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "requirement_id",
        "target_layer",
        "target_code",
        "importance",
        "gap_status",
        "reason_code",
        "reason_summary",
        "source_references",
        "resolution_type",
    )
    return {key: deepcopy(gap.get(key)) for key in keys}


def gap_content_hash(gap: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(gap_content_payload(gap))).hexdigest()


def gap_id(
    gap: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    gap_analysis_request_id: str,
    digest: str | None = None,
) -> str:
    content_digest = digest or gap_content_hash(gap)
    basis = {
        "enterprise_id": stable_enterprise_id(enterprise),
        "gap_analysis_request_id": gap_analysis_request_id,
        "gap_content_hash": content_digest,
        "target_layer": gap.get("target_layer"),
        "target_code": gap.get("target_code"),
    }
    suffix = hashlib.sha256(canonical_json(basis)).hexdigest()[:24]
    return f"enterprise-profile-gap:{gap.get('target_layer')}:{suffix}"


def finalize_gap(
    gap: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    gap_analysis_request_id: str,
) -> dict[str, Any]:
    result = deepcopy(gap)
    digest = gap_content_hash(result)
    result["gap_content_hash"] = digest
    result["gap_id"] = gap_id(
        result,
        enterprise=enterprise,
        gap_analysis_request_id=gap_analysis_request_id,
        digest=digest,
    )
    return result


def _result_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return requirement_sort_key(item)


def gap_inventory_content_payload(inventory: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "gap_inventory_schema_version",
        "enterprise",
        "task_context",
        "source_dependencies",
        "gaps",
        "satisfied_requirements",
        "gap_summary",
        "warnings",
    )
    result = {key: deepcopy(inventory.get(key)) for key in keys}
    result["gaps"] = sorted(result.get("gaps") or [], key=_result_sort_key)
    result["satisfied_requirements"] = sorted(result.get("satisfied_requirements") or [], key=_result_sort_key)
    return result


def gap_inventory_content_hash(inventory: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(gap_inventory_content_payload(inventory))).hexdigest()


def gap_inventory_id(inventory: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or gap_inventory_content_hash(inventory)
    return f"enterprise-profile-gap-inventory:{stable_enterprise_id(inventory.get('enterprise') or {})}:{content_digest[:16]}"


def finalize_gap_inventory(inventory: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(inventory)
    result["gaps"] = sorted(result.get("gaps") or [], key=_result_sort_key)
    result["satisfied_requirements"] = sorted(result.get("satisfied_requirements") or [], key=_result_sort_key)
    digest = gap_inventory_content_hash(result)
    result["gap_inventory_content_hash"] = digest
    result["gap_inventory_id"] = gap_inventory_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
