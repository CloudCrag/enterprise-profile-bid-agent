"""Structure-only and strict business validation for question plans."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from ..profile_gap.gap_validator import validate_gap_inventory
from .errors import issue
from .plan import (
    gap_sort_key,
    question_item_sort_key,
    question_plan_content_hash,
    question_plan_id,
)
from .planner import expected_plan_components, source_dependencies
from .question_item import (
    RESOLUTION_TO_REQUEST_TYPE,
    expected_response_for,
    prompt_template_key_for,
    question_item_content_hash,
    question_item_id,
)
from .request import QUESTION_PLAN_SCHEMA_VERSION
from .request_validator import validate_question_plan_request

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_question_plan.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _summary_from_local(plan: dict[str, Any]) -> dict[str, Any]:
    questions = [item for item in plan.get("question_items", []) if isinstance(item, dict)]
    deferred = [item for item in plan.get("deferred_required_gaps", []) if isinstance(item, dict)]
    excluded = [item for item in plan.get("excluded_optional_gaps", []) if isinstance(item, dict)]
    selected_required = sum(1 for item in questions if item.get("importance") in {"blocking", "important"})
    selected_optional = sum(1 for item in questions if item.get("importance") == "optional")
    return {
        "total_gap_count": len(questions) + len(deferred) + len(excluded),
        "required_gap_count": selected_required + len(deferred),
        "optional_gap_count": selected_optional + len(excluded),
        "selected_question_count": len(questions),
        "selected_blocking_count": sum(1 for item in questions if item.get("importance") == "blocking"),
        "selected_important_count": sum(1 for item in questions if item.get("importance") == "important"),
        "selected_optional_count": selected_optional,
        "deferred_required_count": len(deferred),
        "excluded_optional_count": len(excluded),
        "has_more_required_gaps": bool(deferred),
    }


def _local_integrity_issues(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return [issue("question_plan_schema_invalid", "$", "Question plan must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(plan), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("question_plan_schema_invalid", path, error.message))
    if plan.get("question_plan_schema_version") != QUESTION_PLAN_SCHEMA_VERSION:
        issues.append(issue("question_plan_schema_invalid", "question_plan_schema_version", "Unsupported question plan schema version"))

    questions_value = plan.get("question_items")
    deferred_value = plan.get("deferred_required_gaps")
    excluded_value = plan.get("excluded_optional_gaps")
    questions = questions_value if isinstance(questions_value, list) else []
    deferred = deferred_value if isinstance(deferred_value, list) else []
    excluded = excluded_value if isinstance(excluded_value, list) else []
    arrays_valid = all(isinstance(value, list) for value in (questions_value, deferred_value, excluded_value))
    objects_valid = arrays_valid and all(isinstance(item, dict) for item in questions + deferred + excluded)
    deps = plan.get("source_dependencies") if isinstance(plan.get("source_dependencies"), dict) else {}
    enterprise = plan.get("enterprise") if isinstance(plan.get("enterprise"), dict) else {}
    plan_request_id = str(deps.get("question_plan_request_id") or "")

    if objects_valid:
        if questions != sorted(questions, key=question_item_sort_key):
            issues.append(issue("question_plan_schema_invalid", "question_items", "Question items must use the stable gap order"))
        if deferred != sorted(deferred, key=gap_sort_key):
            issues.append(issue("question_plan_schema_invalid", "deferred_required_gaps", "Deferred required gaps must use stable order"))
        if excluded != sorted(excluded, key=gap_sort_key):
            issues.append(issue("question_plan_schema_invalid", "excluded_optional_gaps", "Excluded optional gaps must use stable order"))

        item_ids: list[Any] = []
        for index, item in enumerate(questions):
            digest = question_item_content_hash(item)
            if item.get("question_item_content_hash") != digest:
                issues.append(issue("question_plan_item_hash_mismatch", f"question_items.{index}.question_item_content_hash", "Question item content hash mismatch"))
            try:
                expected_id = question_item_id(
                    item,
                    enterprise=enterprise,
                    question_plan_request_id=plan_request_id,
                    digest=digest,
                )
            except InputDataError as exc:
                issues.append(issue("question_plan_item_id_mismatch", f"question_items.{index}.question_item_id", str(exc)))
            else:
                if item.get("question_item_id") != expected_id:
                    issues.append(issue("question_plan_item_id_mismatch", f"question_items.{index}.question_item_id", "Question item ID mismatch"))
            request_type = RESOLUTION_TO_REQUEST_TYPE.get(str(item.get("resolution_type") or ""))
            if request_type is None:
                issues.append(issue("question_plan_resolution_type_invalid", f"question_items.{index}.resolution_type", "Unknown resolution type"))
            else:
                if item.get("information_request_type") != request_type:
                    issues.append(issue("question_plan_selection_mismatch", f"question_items.{index}.information_request_type", f"Expected {request_type}"))
                expected_response = expected_response_for(request_type, target_code=str(item.get("target_code") or ""))
                if item.get("expected_response") != expected_response:
                    issues.append(issue("question_plan_selection_mismatch", f"question_items.{index}.expected_response", "Expected response contract mismatch"))
                expected_template = prompt_template_key_for(request_type, item)
                if item.get("prompt_template_key") != expected_template:
                    issues.append(issue("question_plan_selection_mismatch", f"question_items.{index}.prompt_template_key", f"Expected {expected_template}"))
            item_ids.append(item.get("question_item_id"))
        duplicates = sorted({str(value) for value in item_ids if item_ids.count(value) > 1})
        if duplicates:
            issues.append(issue("question_plan_item_id_duplicate", "question_items", f"Duplicate question_item_id values: {duplicates}"))

        gap_ids = [item.get("gap_id") for item in questions + deferred + excluded]
        duplicate_gap_ids = sorted({str(value) for value in gap_ids if gap_ids.count(value) > 1})
        if duplicate_gap_ids:
            issues.append(issue("question_plan_gap_coverage_mismatch", "question_items", f"Gap IDs appear more than once: {duplicate_gap_ids}"))

        options = plan.get("planning_options") if isinstance(plan.get("planning_options"), dict) else {}
        max_count = options.get("max_questions_per_batch")
        include_optional = options.get("include_optional")
        if isinstance(max_count, int) and not isinstance(max_count, bool) and len(questions) > max_count:
            issues.append(issue("question_plan_summary_mismatch", "question_items", "Selected question count exceeds max_questions_per_batch"))
        if include_optional is False and any(item.get("importance") == "optional" for item in questions):
            issues.append(issue("question_plan_selection_mismatch", "question_items", "Optional gaps may not be selected when include_optional is false"))
        for index, item in enumerate(deferred):
            if item.get("importance") not in {"blocking", "important"} or item.get("defer_reason") != "batch_limit":
                issues.append(issue("question_plan_selection_mismatch", f"deferred_required_gaps.{index}", "Deferred required gaps must be blocking/important with batch_limit"))
        for index, item in enumerate(excluded):
            expected_reason = "batch_limit_optional" if include_optional is True else "optional_not_requested"
            if item.get("importance") != "optional" or item.get("exclude_reason") != expected_reason:
                issues.append(issue("question_plan_selection_mismatch", f"excluded_optional_gaps.{index}", f"Excluded optional gap must use {expected_reason}"))

        expected_summary = _summary_from_local(plan)
        if plan.get("plan_summary") != expected_summary:
            issues.append(issue("question_plan_summary_mismatch", "plan_summary", f"Expected {expected_summary}"))
        if expected_summary["total_gap_count"] == 0:
            expected_status = "no_gaps"
        elif expected_summary["selected_question_count"] > 0:
            expected_status = "questions_planned"
        else:
            expected_status = "required_gaps_complete"
        if plan.get("plan_status") != expected_status:
            issues.append(issue("question_plan_selection_mismatch", "plan_status", f"Expected {expected_status}"))

        digest = question_plan_content_hash(plan)
        if plan.get("question_plan_content_hash") != digest:
            issues.append(issue("question_plan_content_hash_mismatch", "question_plan_content_hash", "Question plan content hash mismatch"))
        try:
            expected_plan_id = question_plan_id(plan, digest)
        except InputDataError as exc:
            issues.append(issue("question_plan_id_mismatch", "question_plan_id", str(exc)))
        else:
            if plan.get("question_plan_id") != expected_plan_id:
                issues.append(issue("question_plan_id_mismatch", "question_plan_id", "Question plan ID mismatch"))
    return issues


def validate_question_plan_structure_only(plan: Any) -> list[dict[str, Any]]:
    return _local_integrity_issues(plan)


def assert_valid_question_plan_structure_only(plan: Any) -> None:
    issues = validate_question_plan_structure_only(plan)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _required_context_issues(
    plan: dict[str, Any],
    *,
    question_plan_request: Any,
    gap_inventory: Any,
    gap_analysis_request: Any,
    fact_profile: Any,
    capability_profile: Any,
    decision_profile: Any,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for name, value in (
        ("question_plan_request", question_plan_request),
        ("gap_inventory", gap_inventory),
        ("gap_analysis_request", gap_analysis_request),
        ("fact_profile", fact_profile),
        ("capability_profile", capability_profile),
    ):
        if value is None:
            issues.append(issue("question_plan_validation_context_required", name, f"Strict question plan validation requires {name}"))
    deps = plan.get("source_dependencies") if isinstance(plan.get("source_dependencies"), dict) else {}
    if deps.get("decision_profile_id") is not None and decision_profile is None:
        issues.append(issue("question_plan_validation_context_required", "decision_profile", "Question plan records a decision profile dependency"))
    return issues


def _context_type_issues(
    *, question_plan_request: Any, gap_inventory: Any, gap_analysis_request: Any,
    fact_profile: Any, capability_profile: Any, decision_profile: Any,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checks = (
        ("question_plan_request", question_plan_request, "question_plan_request_schema_invalid"),
        ("gap_inventory", gap_inventory, "question_plan_gap_dependency_mismatch"),
        ("gap_analysis_request", gap_analysis_request, "gap_analysis_request_schema_invalid"),
        ("fact_profile", fact_profile, "question_plan_fact_dependency_mismatch"),
        ("capability_profile", capability_profile, "question_plan_capability_dependency_mismatch"),
        ("decision_profile", decision_profile, "question_plan_decision_dependency_mismatch"),
    )
    for name, value, code in checks:
        if value is not None and not isinstance(value, dict):
            issues.append(issue(code, name, f"{name} must be an object"))
    return issues


def validate_question_plan(
    plan: Any,
    *,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> list[dict[str, Any]]:
    local = _local_integrity_issues(plan)
    if not isinstance(plan, dict):
        return local
    missing = _required_context_issues(
        plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if missing:
        return local + missing
    type_issues = _context_type_issues(
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if type_issues:
        return local + type_issues

    request_obj: dict[str, Any] = question_plan_request
    inventory_obj: dict[str, Any] = gap_inventory
    gap_request_obj: dict[str, Any] = gap_analysis_request
    fact_obj: dict[str, Any] = fact_profile
    capability_obj: dict[str, Any] = capability_profile
    decision_obj: dict[str, Any] | None = decision_profile
    issues = list(local)

    request_issues = validate_question_plan_request(request_obj)
    if request_issues:
        issues.extend(request_issues)
        return issues
    gap_issues = validate_gap_inventory(
        inventory_obj,
        request=gap_request_obj,
        fact_profile=fact_obj,
        capability_profile=capability_obj,
        decision_profile=decision_obj,
    )
    if gap_issues:
        issues.extend(gap_issues)
        return issues

    if request_obj.get("enterprise") != inventory_obj.get("enterprise") or plan.get("enterprise") != inventory_obj.get("enterprise"):
        issues.append(issue("question_plan_context_enterprise_mismatch", "enterprise", "Plan request, gap inventory and plan enterprises must match"))
    if request_obj.get("gap_inventory_id") != inventory_obj.get("gap_inventory_id"):
        issues.append(issue("question_plan_gap_dependency_mismatch", "question_plan_request.gap_inventory_id", "Plan request references another gap inventory"))
    if request_obj.get("gap_inventory_content_hash") != inventory_obj.get("gap_inventory_content_hash"):
        issues.append(issue("question_plan_gap_dependency_mismatch", "question_plan_request.gap_inventory_content_hash", "Plan request gap inventory hash mismatch"))

    expected_deps = source_dependencies(request_obj, inventory_obj)
    if plan.get("source_dependencies") != expected_deps:
        issues.append(issue("question_plan_source_dependency_mismatch", "source_dependencies", "Question plan source dependencies differ from supplied inputs"))
    expected = expected_plan_components(inventory_obj, question_plan_request=request_obj)
    if plan.get("planning_options") != expected["planning_options"]:
        issues.append(issue("question_plan_selection_mismatch", "planning_options", "Planning options differ from request"))
    if plan.get("plan_status") != expected["plan_status"]:
        issues.append(issue("question_plan_selection_mismatch", "plan_status", f"Expected {expected['plan_status']}"))
    if plan.get("question_items") != expected["question_items"]:
        issues.append(issue("question_plan_selection_mismatch", "question_items", "Selected question items differ from deterministic planning result"))
    if plan.get("deferred_required_gaps") != expected["deferred_required_gaps"]:
        issues.append(issue("question_plan_selection_mismatch", "deferred_required_gaps", "Deferred required gaps differ from deterministic planning result"))
    if plan.get("excluded_optional_gaps") != expected["excluded_optional_gaps"]:
        issues.append(issue("question_plan_selection_mismatch", "excluded_optional_gaps", "Excluded optional gaps differ from deterministic planning result"))
    if plan.get("plan_summary") != expected["plan_summary"]:
        issues.append(issue("question_plan_summary_mismatch", "plan_summary", f"Expected {expected['plan_summary']}"))
    return issues


def assert_valid_question_plan(
    plan: Any,
    *,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> None:
    issues = validate_question_plan(
        plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
