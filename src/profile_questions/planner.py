"""Deterministic selection of one structured information-request item per gap."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..errors import InputDataError
from ..profile_gap.gap_validator import assert_valid_gap_inventory
from ..profile_gap.request import GAP_INVENTORY_SCHEMA_VERSION
from .plan import finalize_question_plan, gap_sort_key
from .question_item import question_item_from_gap
from .request import QUESTION_PLAN_REQUEST_SCHEMA_VERSION, normalized_planning_options
from .request_validator import assert_valid_question_plan_request


def _gap_reference(gap: dict[str, Any]) -> dict[str, Any]:
    return {
        "gap_id": gap.get("gap_id"),
        "gap_content_hash": gap.get("gap_content_hash"),
        "requirement_id": gap.get("requirement_id"),
        "target_layer": gap.get("target_layer"),
        "target_code": gap.get("target_code"),
        "importance": gap.get("importance"),
    }


def _deferred_required(gap: dict[str, Any]) -> dict[str, Any]:
    result = _gap_reference(gap)
    result["defer_reason"] = "batch_limit"
    return result


def _excluded_optional(gap: dict[str, Any], *, reason: str) -> dict[str, Any]:
    result = _gap_reference(gap)
    result["exclude_reason"] = reason
    return result


def expected_plan_components(
    gap_inventory: dict[str, Any],
    *,
    question_plan_request: dict[str, Any],
) -> dict[str, Any]:
    options = normalized_planning_options(question_plan_request.get("planning_options"))
    max_count = options.get("max_questions_per_batch")
    include_optional = options.get("include_optional")
    if not isinstance(max_count, int) or isinstance(max_count, bool) or max_count <= 0:
        raise InputDataError("question_plan_request_schema_invalid: max_questions_per_batch must be a positive integer")
    if not isinstance(include_optional, bool):
        raise InputDataError("question_plan_request_schema_invalid: include_optional must be boolean")

    gaps = sorted((deepcopy(item) for item in gap_inventory.get("gaps", [])), key=gap_sort_key)
    required = [gap for gap in gaps if gap.get("importance") in {"blocking", "important"}]
    optional = [gap for gap in gaps if gap.get("importance") == "optional"]
    selectable = required + (optional if include_optional else [])
    selected = selectable[:max_count]
    selected_ids = {item.get("gap_id") for item in selected}

    deferred_required = [
        _deferred_required(item)
        for item in required
        if item.get("gap_id") not in selected_ids
    ]
    excluded_optional = [
        _excluded_optional(
            item,
            reason="batch_limit_optional" if include_optional else "optional_not_requested",
        )
        for item in optional
        if item.get("gap_id") not in selected_ids
    ]
    question_items = [
        question_item_from_gap(
            item,
            enterprise=gap_inventory["enterprise"],
            question_plan_request_id=question_plan_request["question_plan_request_id"],
        )
        for item in selected
    ]

    if not gaps:
        plan_status = "no_gaps"
    elif question_items:
        plan_status = "questions_planned"
    else:
        plan_status = "required_gaps_complete"

    summary = {
        "total_gap_count": len(gaps),
        "required_gap_count": len(required),
        "optional_gap_count": len(optional),
        "selected_question_count": len(question_items),
        "selected_blocking_count": sum(1 for item in question_items if item.get("importance") == "blocking"),
        "selected_important_count": sum(1 for item in question_items if item.get("importance") == "important"),
        "selected_optional_count": sum(1 for item in question_items if item.get("importance") == "optional"),
        "deferred_required_count": len(deferred_required),
        "excluded_optional_count": len(excluded_optional),
        "has_more_required_gaps": bool(deferred_required),
    }
    return {
        "planning_options": options,
        "plan_status": plan_status,
        "question_items": question_items,
        "deferred_required_gaps": deferred_required,
        "excluded_optional_gaps": excluded_optional,
        "plan_summary": summary,
    }


def source_dependencies(
    question_plan_request: dict[str, Any],
    gap_inventory: dict[str, Any],
) -> dict[str, Any]:
    gap_deps = gap_inventory.get("source_dependencies") or {}
    return {
        "question_plan_request_schema_version": QUESTION_PLAN_REQUEST_SCHEMA_VERSION,
        "question_plan_request_id": question_plan_request.get("question_plan_request_id"),
        "question_plan_request_content_hash": question_plan_request.get("question_plan_request_content_hash"),
        "gap_inventory_schema_version": GAP_INVENTORY_SCHEMA_VERSION,
        "gap_inventory_id": gap_inventory.get("gap_inventory_id"),
        "gap_inventory_content_hash": gap_inventory.get("gap_inventory_content_hash"),
        "gap_analysis_request_schema_version": gap_deps.get("gap_analysis_request_schema_version"),
        "gap_analysis_request_id": gap_deps.get("gap_analysis_request_id"),
        "gap_analysis_request_content_hash": gap_deps.get("gap_analysis_request_content_hash"),
        "fact_profile_schema_version": gap_deps.get("fact_profile_schema_version"),
        "fact_profile_content_hash": gap_deps.get("fact_profile_content_hash"),
        "capability_profile_schema_version": gap_deps.get("capability_profile_schema_version"),
        "capability_profile_id": gap_deps.get("capability_profile_id"),
        "capability_profile_content_hash": gap_deps.get("capability_profile_content_hash"),
        "decision_profile_schema_version": gap_deps.get("decision_profile_schema_version"),
        "decision_profile_id": gap_deps.get("decision_profile_id"),
        "decision_profile_content_hash": gap_deps.get("decision_profile_content_hash"),
    }


def build_question_plan(
    question_plan_request: dict[str, Any],
    gap_inventory: dict[str, Any],
    gap_analysis_request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    *,
    decision_profile: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    assert_valid_question_plan_request(question_plan_request)
    assert_valid_gap_inventory(
        gap_inventory,
        request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if question_plan_request.get("enterprise") != gap_inventory.get("enterprise"):
        raise InputDataError("question_plan_context_enterprise_mismatch: plan request and gap inventory enterprises differ")
    if question_plan_request.get("gap_inventory_id") != gap_inventory.get("gap_inventory_id"):
        raise InputDataError("question_plan_gap_dependency_mismatch: gap inventory ID mismatch")
    if question_plan_request.get("gap_inventory_content_hash") != gap_inventory.get("gap_inventory_content_hash"):
        raise InputDataError("question_plan_gap_dependency_mismatch: gap inventory content hash mismatch")

    components = expected_plan_components(gap_inventory, question_plan_request=question_plan_request)
    plan = {
        "question_plan_schema_version": "enterprise-profile-question-plan/1.0.0",
        "enterprise": deepcopy(gap_inventory["enterprise"]),
        "source_dependencies": source_dependencies(question_plan_request, gap_inventory),
        **components,
        "warnings": [],
    }
    result = finalize_question_plan(
        plan,
        generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat(),
    )
    from .plan_validator import assert_valid_question_plan
    assert_valid_question_plan(
        result,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    return result
