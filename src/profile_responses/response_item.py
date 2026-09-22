"""Stable response-item normalization, identifiers, and local payload checks."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id

ANSWER_TYPES = {"decision_confirmation", "structured_value", "conflict_selection", "clarification"}
_LAYER_ORDER = {"fact": 0, "capability": 1, "decision": 2}


def response_item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _LAYER_ORDER.get(str(item.get("target_layer")), 99),
        str(item.get("target_code") or ""),
        str(item.get("requirement_id") or ""),
        str(item.get("question_item_id") or ""),
    )


def normalized_material_references(value: Any) -> list[Any]:
    refs = deepcopy(value) if isinstance(value, list) else []
    return sorted(refs, key=lambda ref: (str(ref.get("material_id") or ""), str(ref.get("material_content_sha256") or "")) if isinstance(ref, dict) else ("", ""))


def response_item_content_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_item_id": item.get("question_item_id"),
        "question_item_content_hash": item.get("question_item_content_hash"),
        "gap_id": item.get("gap_id"),
        "gap_content_hash": item.get("gap_content_hash"),
        "requirement_id": item.get("requirement_id"),
        "target_layer": item.get("target_layer"),
        "target_code": item.get("target_code"),
        "response_outcome": item.get("response_outcome"),
        "answer": deepcopy(item.get("answer")),
        "material_references": normalized_material_references(item.get("material_references")),
        "unable_to_provide_note": item.get("unable_to_provide_note"),
        "response_status": item.get("response_status"),
    }


def response_item_content_hash(item: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(response_item_content_payload(item))).hexdigest()


def response_item_id(item: dict[str, Any], *, enterprise: dict[str, Any], submission_id: str, digest: str | None = None) -> str:
    content_digest = digest or response_item_content_hash(item)
    basis = {
        "enterprise_id": stable_enterprise_id(enterprise),
        "question_response_submission_id": submission_id,
        "question_item_id": item.get("question_item_id"),
        "response_item_content_hash": content_digest,
    }
    suffix = hashlib.sha256(canonical_json(basis)).hexdigest()[:24]
    return f"enterprise-profile-question-response-item:{item.get('target_layer')}:{suffix}"


def finalize_response_item(item: dict[str, Any], *, enterprise: dict[str, Any], submission_id: str) -> dict[str, Any]:
    result = deepcopy(item)
    result["material_references"] = normalized_material_references(result.get("material_references"))
    digest = response_item_content_hash(result)
    result["response_item_content_hash"] = digest
    result["response_item_id"] = response_item_id(result, enterprise=enterprise, submission_id=submission_id, digest=digest)
    return result
