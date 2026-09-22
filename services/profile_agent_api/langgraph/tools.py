from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from typing import Any

from src.decision_candidate_application.application_request import (
    APPLICATION_REQUEST_SCHEMA_VERSION,
    finalize_application_request,
)
from src.enterprise_capability_profile import canonical_json
from src.enterprise_decision_profile import (
    DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
    finalize_confirmation_input,
)
from src.profile_gap.request import GAP_ANALYSIS_REQUEST_SCHEMA_VERSION, finalize_gap_analysis_request
from src.profile_questions.request import QUESTION_PLAN_REQUEST_SCHEMA_VERSION, finalize_question_plan_request
from src.profile_responses.submission import (
    QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION,
    finalize_question_response_submission,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return f"{prefix}:{hashlib.sha256(canonical_json(payload)).hexdigest()[:length]}"


def build_gap_request(enterprise: dict[str, Any], requirements: list[dict[str, Any]], *, task_context_id: str, task_goal: str, generated_at_utc: str | None = None) -> dict[str, Any]:
    raw = {
        "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
        "enterprise": deepcopy(enterprise),
        "task_context": {
            "task_context_id": task_context_id,
            "task_goal": task_goal,
            "requirements": deepcopy(requirements),
        },
    }
    return finalize_gap_analysis_request(raw, generated_at_utc=generated_at_utc or utc_now())


def build_plan_request(enterprise: dict[str, Any], gap_inventory: dict[str, Any], *, max_questions_per_batch: int, include_optional: bool, generated_at_utc: str | None = None) -> dict[str, Any]:
    raw = {
        "question_plan_request_schema_version": QUESTION_PLAN_REQUEST_SCHEMA_VERSION,
        "enterprise": deepcopy(enterprise),
        "gap_inventory_id": gap_inventory["gap_inventory_id"],
        "gap_inventory_content_hash": gap_inventory["gap_inventory_content_hash"],
        "planning_options": {
            "max_questions_per_batch": max_questions_per_batch,
            "include_optional": include_optional,
        },
    }
    return finalize_question_plan_request(raw, generated_at_utc=generated_at_utc or utc_now())


def _question_map(question_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["question_item_id"]: item
        for item in question_plan.get("question_items") or []
        if isinstance(item, dict) and item.get("question_item_id")
    }


def _decision_confirmation(
    question: dict[str, Any],
    enterprise: dict[str, Any],
    decision_profile: dict[str, Any] | None,
    actor: dict[str, Any],
    confirmed_at: str,
    value: Any,
) -> dict[str, Any]:
    confirmation = {
        "confirmation_input_schema_version": DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
        "enterprise": deepcopy(enterprise),
        "base_decision_profile_id": decision_profile.get("decision_profile_id") if decision_profile else None,
        "base_decision_profile_content_hash": decision_profile.get("decision_profile_content_hash") if decision_profile else None,
        "confirmation_actor": {
            "actor_type": "user",
            "actor_id": actor["actor_id"],
        },
        "confirmed_at": confirmed_at,
        "updates": [{
            "field_code": question["target_code"],
            "action": "set",
            "value": deepcopy(value),
        }],
    }
    return {
        "answer_type": "decision_confirmation",
        "decision_confirmation_input": finalize_confirmation_input(confirmation),
    }


def build_submission_from_responses(
    *,
    question_plan: dict[str, Any],
    decision_profile: dict[str, Any] | None,
    payload: dict[str, Any],
    response_source: str,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    question_map = _question_map(question_plan)
    submitted_by = deepcopy(payload.get("submitted_by") or {"actor_type": "user", "actor_id": "LOCAL_OPERATOR", "display_name": None})
    submitted_at = payload.get("submitted_at_utc") or utc_now()
    response_items: list[dict[str, Any]] = []
    for supplied in payload.get("responses") or payload.get("response_items") or []:
        if not isinstance(supplied, dict):
            continue
        question = question_map.get(supplied.get("question_item_id"))
        if question is None:
            # Keep a minimal forged reference so strict validation returns the canonical not-found error.
            question = {
                "question_item_id": supplied.get("question_item_id"),
                "question_item_content_hash": supplied.get("question_item_content_hash"),
                "gap_id": supplied.get("gap_id"),
                "gap_content_hash": supplied.get("gap_content_hash"),
                "requirement_id": supplied.get("requirement_id"),
                "target_layer": supplied.get("target_layer"),
                "target_code": supplied.get("target_code"),
                "information_request_type": supplied.get("information_request_type"),
            }
        outcome = supplied.get("response_outcome", "provided")
        answer = deepcopy(supplied.get("answer"))
        if outcome == "provided" and question.get("information_request_type") == "decision_confirmation" and answer is None:
            answer = _decision_confirmation(
                question,
                question_plan["enterprise"],
                decision_profile,
                submitted_by,
                submitted_at,
                supplied.get("decision_value", {"minimum": None, "maximum": None}),
            )
        item = {
            "question_item_id": question.get("question_item_id"),
            "question_item_content_hash": question.get("question_item_content_hash"),
            "gap_id": question.get("gap_id"),
            "gap_content_hash": question.get("gap_content_hash"),
            "requirement_id": question.get("requirement_id"),
            "target_layer": question.get("target_layer"),
            "target_code": question.get("target_code"),
            "response_outcome": outcome,
            "answer": answer if outcome == "provided" else None,
            "material_references": deepcopy(supplied.get("material_references") or []),
            "unable_to_provide_note": supplied.get("unable_to_provide_note") if outcome == "unable_to_provide" else None,
            "response_status": "submitted",
        }
        response_items.append(item)
    submission = {
        "question_response_submission_schema_version": QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION,
        "enterprise": deepcopy(question_plan["enterprise"]),
        "question_plan_id": question_plan["question_plan_id"],
        "question_plan_content_hash": question_plan["question_plan_content_hash"],
        "submission_mode": payload.get("submission_mode", "partial_current_batch"),
        "submitted_by": submitted_by,
        "submitted_at_utc": submitted_at,
        "response_items": response_items,
        "response_source": response_source,
    }
    # response_source is audit metadata and not part of the formal schema.
    formal = {k: v for k, v in submission.items() if k != "response_source"}
    result = finalize_question_response_submission(formal, generated_at_utc=generated_at_utc or utc_now())
    result["response_source"] = response_source
    return result


def build_application_request(enterprise: dict[str, Any], worklist: dict[str, Any], candidate_ids: list[str], *, requested_at_utc: str | None = None, generated_at_utc: str | None = None) -> dict[str, Any]:
    raw = {
        "application_request_schema_version": APPLICATION_REQUEST_SCHEMA_VERSION,
        "enterprise": deepcopy(enterprise),
        "response_processing_worklist_id": worklist["response_processing_worklist_id"],
        "response_processing_worklist_content_hash": worklist["response_processing_worklist_content_hash"],
        "selected_candidate_ids": list(candidate_ids),
        "requested_at_utc": requested_at_utc or utc_now(),
    }
    return finalize_application_request(raw, generated_at_utc=generated_at_utc or utc_now())
