"""Build a worklist without applying any facts, abilities, decisions, or gap changes."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..profile_responses.receipt_validator import assert_valid_question_response_receipt
from .router import route_recorded_response_item
from .worklist import (
    RESPONSE_PROCESSING_WORKLIST_SCHEMA_VERSION,
    finalize_response_processing_worklist,
    processing_summary,
)


def response_processing_source_dependencies(receipt: dict[str, Any]) -> dict[str, Any]:
    deps = receipt.get("source_dependencies") if isinstance(receipt.get("source_dependencies"), dict) else {}
    return {
        "question_response_receipt_schema_version": receipt.get("question_response_receipt_schema_version"),
        "question_response_receipt_id": receipt.get("question_response_receipt_id"),
        "question_response_receipt_content_hash": receipt.get("question_response_receipt_content_hash"),
        "question_response_submission_schema_version": deps.get("question_response_submission_schema_version"),
        "question_response_submission_id": deps.get("question_response_submission_id"),
        "question_response_submission_content_hash": deps.get("question_response_submission_content_hash"),
        "question_plan_schema_version": deps.get("question_plan_schema_version"),
        "question_plan_id": deps.get("question_plan_id"),
        "question_plan_content_hash": deps.get("question_plan_content_hash"),
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


def expected_processing_items(
    receipt: dict[str, Any], question_plan: dict[str, Any], *, decision_profile: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    question_map = {
        item.get("question_item_id"): item
        for item in (question_plan.get("question_items") or [])
        if isinstance(item, dict)
    }
    values: list[dict[str, Any]] = []
    for response_item in receipt.get("recorded_response_items") or []:
        if not isinstance(response_item, dict):
            continue
        question = question_map.get(response_item.get("question_item_id"))
        if not isinstance(question, dict):
            continue
        values.append(route_recorded_response_item(
            response_item,
            question,
            enterprise=receipt.get("enterprise") or {},
            receipt_id=str(receipt.get("question_response_receipt_id") or ""),
            decision_profile=decision_profile,
        ))
    from .processing_item import processing_item_sort_key
    return sorted(values, key=processing_item_sort_key)


def build_response_processing_worklist(
    receipt: dict[str, Any],
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
        "submission": submission,
        "question_plan": question_plan,
        "question_plan_request": question_plan_request,
        "gap_inventory": gap_inventory,
        "gap_analysis_request": gap_analysis_request,
        "fact_profile": fact_profile,
        "capability_profile": capability_profile,
        "decision_profile": decision_profile,
    }
    assert_valid_question_response_receipt(receipt, **context)
    items = expected_processing_items(receipt, question_plan, decision_profile=decision_profile)
    worklist = {
        "response_processing_worklist_schema_version": RESPONSE_PROCESSING_WORKLIST_SCHEMA_VERSION,
        "enterprise": deepcopy(receipt.get("enterprise")),
        "source_dependencies": response_processing_source_dependencies(receipt),
        "processing_items": items,
        "processing_summary": processing_summary(items, recorded_response_item_count=len(receipt.get("recorded_response_items") or [])),
        "warnings": [],
    }
    result = finalize_response_processing_worklist(
        worklist, generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat()
    )
    from .worklist_validator import assert_valid_response_processing_worklist
    assert_valid_response_processing_worklist(result, receipt=receipt, **context)
    return result
