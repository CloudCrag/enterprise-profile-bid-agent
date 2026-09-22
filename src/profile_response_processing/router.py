"""Pure deterministic routing from recorded responses to pending processing work."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

from ..errors import InputDataError
from .decision_candidate import build_decision_update_candidate
from .processing_item import finalize_processing_item


def route_recorded_response_item(
    response_item: dict[str, Any],
    question_item: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    receipt_id: str,
    decision_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one normalized processing item without modifying any source artifact."""
    outcome = response_item.get("response_outcome")
    request_type = question_item.get("information_request_type")
    answer = response_item.get("answer") if isinstance(response_item.get("answer"), dict) else None
    answer_type = answer.get("answer_type") if isinstance(answer, dict) else None
    materials = deepcopy(response_item.get("material_references") or [])
    candidate = None

    if outcome == "unable_to_provide":
        status = "unavailable_recorded"
        actions = ["record_unavailable_declaration", "no_profile_update"]
    elif request_type == "decision_confirmation" and answer_type == "decision_confirmation":
        status = "decision_update_candidate_ready"
        actions = ["prepare_decision_profile_update"]
        candidate = build_decision_update_candidate(response_item, question_item, enterprise=enterprise)
    elif request_type == "supporting_material":
        status = "evidence_processing_required"
        actions = []
        if answer_type == "structured_value":
            actions.append("review_structured_response")
        if materials:
            actions.append("process_material_references")
        actions.append("verify_before_profile_update")
    elif request_type == "fact_verification":
        status = "evidence_processing_required"
        actions = []
        if answer_type == "structured_value":
            actions.append("review_structured_response")
        if materials:
            actions.append("process_material_references")
        actions.append("verify_fact_before_profile_update")
    elif request_type == "conflict_clarification" and answer_type == "conflict_selection":
        status = "conflict_review_required"
        actions = ["review_conflict_selection"]
        if materials:
            actions.append("process_material_references")
        actions.append("resolve_conflict_before_profile_update")
    elif request_type == "capability_clarification" and answer_type == "clarification":
        status = "capability_review_required"
        actions = ["review_capability_clarification"]
        if materials:
            actions.append("process_material_references")
        actions.append("review_before_capability_update")
    else:
        raise InputDataError(
            f"response_processing_status_mismatch: unsupported validated route outcome={outcome!r}, "
            f"information_request_type={request_type!r}, answer_type={answer_type!r}"
        )

    raw = {
        "response_item_id": response_item.get("response_item_id"),
        "response_item_content_hash": response_item.get("response_item_content_hash"),
        "question_item_id": question_item.get("question_item_id"),
        "question_item_content_hash": question_item.get("question_item_content_hash"),
        "gap_id": question_item.get("gap_id"),
        "gap_content_hash": question_item.get("gap_content_hash"),
        "requirement_id": question_item.get("requirement_id"),
        "target_layer": question_item.get("target_layer"),
        "target_code": question_item.get("target_code"),
        "importance": question_item.get("importance"),
        "response_outcome": outcome,
        "information_request_type": request_type,
        "processing_status": status,
        "required_actions": actions,
        "material_references": materials,
        "decision_update_candidate": candidate,
    }
    return finalize_processing_item(raw, enterprise=enterprise, receipt_id=receipt_id)
