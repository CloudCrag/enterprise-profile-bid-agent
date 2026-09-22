"""Stable processing-item normalization, identifiers, and summaries."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from ..profile_responses.response_item import normalized_material_references

PROCESSING_STATUSES = {
    "decision_update_candidate_ready",
    "evidence_processing_required",
    "conflict_review_required",
    "capability_review_required",
    "unavailable_recorded",
}
REQUIRED_ACTION_CATALOG = [
    "record_unavailable_declaration",
    "no_profile_update",
    "prepare_decision_profile_update",
    "review_structured_response",
    "process_material_references",
    "verify_before_profile_update",
    "verify_fact_before_profile_update",
    "review_conflict_selection",
    "resolve_conflict_before_profile_update",
    "review_capability_clarification",
    "review_before_capability_update",
]
_ACTION_ORDER = {value: index for index, value in enumerate(REQUIRED_ACTION_CATALOG)}
_LAYER_ORDER = {"fact": 0, "capability": 1, "decision": 2}


def normalize_required_actions(actions: Any) -> list[str]:
    values = [str(value) for value in actions] if isinstance(actions, list) else []
    return sorted(values, key=lambda value: (_ACTION_ORDER.get(value, 999), value))


def processing_item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _LAYER_ORDER.get(str(item.get("target_layer")), 99),
        str(item.get("target_code") or ""),
        str(item.get("requirement_id") or ""),
        str(item.get("question_item_id") or ""),
        str(item.get("response_item_id") or ""),
    )


def processing_item_content_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_item_id": item.get("response_item_id"),
        "response_item_content_hash": item.get("response_item_content_hash"),
        "question_item_id": item.get("question_item_id"),
        "question_item_content_hash": item.get("question_item_content_hash"),
        "gap_id": item.get("gap_id"),
        "gap_content_hash": item.get("gap_content_hash"),
        "requirement_id": item.get("requirement_id"),
        "target_layer": item.get("target_layer"),
        "target_code": item.get("target_code"),
        "importance": item.get("importance"),
        "response_outcome": item.get("response_outcome"),
        "information_request_type": item.get("information_request_type"),
        "processing_status": item.get("processing_status"),
        "required_actions": normalize_required_actions(item.get("required_actions")),
        "material_references": normalized_material_references(item.get("material_references")),
        "decision_update_candidate": deepcopy(item.get("decision_update_candidate")),
    }


def processing_item_content_hash(item: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(processing_item_content_payload(item))).hexdigest()


def processing_item_id(
    item: dict[str, Any], *, enterprise: dict[str, Any], receipt_id: str, digest: str | None = None
) -> str:
    content_digest = digest or processing_item_content_hash(item)
    basis = {
        "enterprise_id": stable_enterprise_id(enterprise),
        "question_response_receipt_id": receipt_id,
        "response_item_id": item.get("response_item_id"),
        "processing_item_content_hash": content_digest,
    }
    suffix = hashlib.sha256(canonical_json(basis)).hexdigest()[:24]
    return f"enterprise-profile-response-processing-item:{item.get('target_layer')}:{suffix}"


def finalize_processing_item(item: dict[str, Any], *, enterprise: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    result = deepcopy(item)
    result["required_actions"] = normalize_required_actions(result.get("required_actions"))
    result["material_references"] = normalized_material_references(result.get("material_references"))
    digest = processing_item_content_hash(result)
    result["processing_item_content_hash"] = digest
    result["processing_item_id"] = processing_item_id(result, enterprise=enterprise, receipt_id=receipt_id, digest=digest)
    return result
