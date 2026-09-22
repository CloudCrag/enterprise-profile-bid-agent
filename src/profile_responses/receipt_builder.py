"""Build immutable, auditable receipts without applying any profile updates."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .material_reference import material_ids_and_hashes
from .receipt import finalize_question_response_receipt, not_in_submission_sort_key
from .response_item import response_item_sort_key
from .submission import QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION, QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION
from .submission_validator import assert_valid_question_response_submission


def response_receipt_source_dependencies(submission: dict[str, Any], question_plan: dict[str, Any]) -> dict[str, Any]:
    deps = question_plan.get("source_dependencies") if isinstance(question_plan.get("source_dependencies"), dict) else {}
    return {
        "question_response_submission_schema_version": QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION,
        "question_response_submission_id": submission.get("question_response_submission_id"),
        "question_response_submission_content_hash": submission.get("question_response_submission_content_hash"),
        "question_plan_schema_version": question_plan.get("question_plan_schema_version"),
        "question_plan_id": question_plan.get("question_plan_id"),
        "question_plan_content_hash": question_plan.get("question_plan_content_hash"),
        "question_plan_request_schema_version": deps.get("question_plan_request_schema_version"),
        "question_plan_request_id": deps.get("question_plan_request_id"),
        "question_plan_request_content_hash": deps.get("question_plan_request_content_hash"),
        "gap_inventory_schema_version": deps.get("gap_inventory_schema_version"),
        "gap_inventory_id": deps.get("gap_inventory_id"),
        "gap_inventory_content_hash": deps.get("gap_inventory_content_hash"),
        "gap_analysis_request_schema_version": deps.get("gap_analysis_request_schema_version"),
        "gap_analysis_request_id": deps.get("gap_analysis_request_id"),
        "gap_analysis_request_content_hash": deps.get("gap_analysis_request_content_hash"),
        "fact_profile_schema_version": deps.get("fact_profile_schema_version"),
        "fact_profile_content_hash": deps.get("fact_profile_content_hash"),
        "capability_profile_schema_version": deps.get("capability_profile_schema_version"),
        "capability_profile_id": deps.get("capability_profile_id"),
        "capability_profile_content_hash": deps.get("capability_profile_content_hash"),
        "decision_profile_schema_version": deps.get("decision_profile_schema_version"),
        "decision_profile_id": deps.get("decision_profile_id"),
        "decision_profile_content_hash": deps.get("decision_profile_content_hash"),
    }


def _not_in_submission_item(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_item_id": question.get("question_item_id"),
        "question_item_content_hash": question.get("question_item_content_hash"),
        "gap_id": question.get("gap_id"),
        "requirement_id": question.get("requirement_id"),
        "target_layer": question.get("target_layer"),
        "target_code": question.get("target_code"),
        "not_in_submission_reason": "not_submitted_in_current_event",
    }


def receipt_summary(question_plan: dict[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    items = submission.get("response_items") if isinstance(submission.get("response_items"), list) else []
    answer_types = [item.get("answer", {}).get("answer_type") for item in items if isinstance(item, dict) and isinstance(item.get("answer"), dict)]
    materials, _ = material_ids_and_hashes([item for item in items if isinstance(item, dict)])
    question_count = len(question_plan.get("question_items") or [])
    response_count = len(items)
    not_count = question_count - response_count
    return {
        "question_count_in_plan": question_count,
        "response_item_count": response_count,
        "provided_response_count": sum(1 for item in items if item.get("response_outcome") == "provided"),
        "unable_to_provide_count": sum(1 for item in items if item.get("response_outcome") == "unable_to_provide"),
        "decision_confirmation_count": answer_types.count("decision_confirmation"),
        "structured_value_count": answer_types.count("structured_value"),
        "conflict_selection_count": answer_types.count("conflict_selection"),
        "clarification_count": answer_types.count("clarification"),
        "response_with_material_count": sum(1 for item in items if item.get("material_references")),
        "unique_material_count": len(materials),
        "not_in_submission_count": not_count,
        "covers_full_current_batch": not_count == 0,
    }


def expected_receipt_components(submission: dict[str, Any], question_plan: dict[str, Any]) -> dict[str, Any]:
    submitted_ids = {item.get("question_item_id") for item in submission.get("response_items") or [] if isinstance(item, dict)}
    not_in = [_not_in_submission_item(question) for question in question_plan.get("question_items") or [] if isinstance(question, dict) and question.get("question_item_id") not in submitted_ids]
    summary = receipt_summary(question_plan, submission)
    return {
        "coverage_status": "full_current_batch" if summary["covers_full_current_batch"] else "partial_current_batch",
        "recorded_response_items": sorted(deepcopy(submission.get("response_items") or []), key=response_item_sort_key),
        "not_in_submission_question_items": sorted(not_in, key=not_in_submission_sort_key),
        "receipt_summary": summary,
    }


def build_question_response_receipt(
    submission: dict[str, Any],
    question_plan: dict[str, Any],
    question_plan_request: dict[str, Any],
    gap_inventory: dict[str, Any],
    gap_analysis_request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    *,
    decision_profile: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    context = {
        "question_plan": question_plan,
        "question_plan_request": question_plan_request,
        "gap_inventory": gap_inventory,
        "gap_analysis_request": gap_analysis_request,
        "fact_profile": fact_profile,
        "capability_profile": capability_profile,
        "decision_profile": decision_profile,
    }
    assert_valid_question_response_submission(submission, **context)
    components = expected_receipt_components(submission, question_plan)
    receipt = {
        "question_response_receipt_schema_version": QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION,
        "enterprise": deepcopy(question_plan["enterprise"]),
        "source_dependencies": response_receipt_source_dependencies(submission, question_plan),
        "submission_mode": submission.get("submission_mode"),
        **components,
        "submitted_by": deepcopy(submission.get("submitted_by")),
        "submitted_at_utc": submission.get("submitted_at_utc"),
        "warnings": [],
    }
    result = finalize_question_response_receipt(receipt, generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat())
    from .receipt_validator import assert_valid_question_response_receipt
    assert_valid_question_response_receipt(result, submission=submission, **context)
    return result
