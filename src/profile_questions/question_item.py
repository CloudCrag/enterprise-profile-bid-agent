"""Deterministic structured information-request item creation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from ..errors import InputDataError

RESOLUTION_TO_REQUEST_TYPE = {
    "user_confirmation_required": "decision_confirmation",
    "supporting_material_required": "supporting_material",
    "verified_fact_required": "fact_verification",
    "conflict_review_required": "conflict_clarification",
    "capability_review_required": "capability_clarification",
}


def expected_response_for(request_type: str, *, target_code: str) -> dict[str, Any]:
    if request_type == "decision_confirmation":
        return {
            "response_mode": "structured_value",
            "target_schema_reference": f"enterprise-decision-confirmation-input/1.0.0#{target_code}",
            "material_allowed": False,
            "confirmation_required": True,
        }
    if request_type in {"supporting_material", "fact_verification"}:
        return {
            "response_mode": "structured_value_or_material",
            "target_schema_reference": None,
            "material_allowed": True,
            "confirmation_required": True,
        }
    if request_type == "conflict_clarification":
        return {
            "response_mode": "conflict_selection",
            "target_schema_reference": None,
            "material_allowed": True,
            "confirmation_required": True,
        }
    if request_type == "capability_clarification":
        return {
            "response_mode": "clarification",
            "target_schema_reference": None,
            "material_allowed": True,
            "confirmation_required": True,
        }
    raise InputDataError(f"question_plan_resolution_type_invalid: unknown request type {request_type!r}")


def prompt_template_key_for(request_type: str, gap: dict[str, Any]) -> str:
    target_code = str(gap.get("target_code") or "")
    target_layer = str(gap.get("target_layer") or "")
    if request_type == "decision_confirmation":
        return f"decision.confirm.{target_code}"
    if request_type == "supporting_material":
        return f"profile.material.{target_layer}.{target_code}"
    if request_type == "fact_verification":
        return f"fact.verify.{target_code}"
    if request_type == "conflict_clarification":
        return f"profile.conflict.{target_layer}.{target_code}"
    if request_type == "capability_clarification":
        return f"capability.clarify.{target_code}"
    raise InputDataError(f"question_plan_resolution_type_invalid: unknown request type {request_type!r}")


def question_item_content_payload(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "gap_id", "gap_content_hash", "requirement_id", "target_layer", "target_code",
        "importance", "gap_status", "reason_code", "resolution_type",
        "information_request_type", "expected_response", "question_status", "prompt_template_key",
    )
    return {key: deepcopy(item.get(key)) for key in keys}


def question_item_content_hash(item: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(question_item_content_payload(item))).hexdigest()


def question_item_id(
    item: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    question_plan_request_id: str,
    digest: str | None = None,
) -> str:
    content_digest = digest or question_item_content_hash(item)
    basis = {
        "enterprise_id": stable_enterprise_id(enterprise),
        "question_plan_request_id": question_plan_request_id,
        "gap_id": item.get("gap_id"),
        "question_item_content_hash": content_digest,
    }
    suffix = hashlib.sha256(canonical_json(basis)).hexdigest()[:24]
    return f"enterprise-profile-question-item:{item.get('target_layer')}:{suffix}"


def question_item_from_gap(
    gap: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    question_plan_request_id: str,
) -> dict[str, Any]:
    resolution_type = str(gap.get("resolution_type") or "")
    request_type = RESOLUTION_TO_REQUEST_TYPE.get(resolution_type)
    if request_type is None:
        raise InputDataError(
            f"question_plan_resolution_type_invalid: unknown resolution_type {resolution_type!r}"
        )
    item = {
        "gap_id": gap.get("gap_id"),
        "gap_content_hash": gap.get("gap_content_hash"),
        "requirement_id": gap.get("requirement_id"),
        "target_layer": gap.get("target_layer"),
        "target_code": gap.get("target_code"),
        "importance": gap.get("importance"),
        "gap_status": gap.get("gap_status"),
        "reason_code": gap.get("reason_code"),
        "resolution_type": resolution_type,
        "information_request_type": request_type,
        "expected_response": expected_response_for(request_type, target_code=str(gap.get("target_code") or "")),
        "question_status": "planned",
        "prompt_template_key": prompt_template_key_for(request_type, gap),
    }
    digest = question_item_content_hash(item)
    item["question_item_content_hash"] = digest
    item["question_item_id"] = question_item_id(
        item,
        enterprise=enterprise,
        question_plan_request_id=question_plan_request_id,
        digest=digest,
    )
    return item
