"""Structure-only and strict validation for immutable question-response receipts."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from .errors import issue
from .receipt import (
    not_in_submission_sort_key,
    question_response_receipt_content_hash,
    question_response_receipt_id,
)
from .receipt_builder import expected_receipt_components, response_receipt_source_dependencies
from .response_item import response_item_sort_key, response_item_content_hash, response_item_id
from .material_reference import material_ids_and_hashes
from .submission_validator import _answer_local_issues
from .submission import QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION
from .submission_validator import validate_question_response_submission

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_question_response_receipt.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _local_integrity_issues(receipt: Any) -> list[dict[str, Any]]:
    if not isinstance(receipt, dict):
        return [issue("question_response_receipt_schema_invalid", "$", "Question response receipt must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(receipt), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("question_response_receipt_schema_invalid", path, error.message))
    if receipt.get("question_response_receipt_schema_version") != QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION:
        issues.append(issue("question_response_receipt_schema_invalid", "question_response_receipt_schema_version", "Unsupported receipt schema version"))
    recorded_value = receipt.get("recorded_response_items")
    not_in_value = receipt.get("not_in_submission_question_items")
    recorded = recorded_value if isinstance(recorded_value, list) else []
    not_in = not_in_value if isinstance(not_in_value, list) else []
    if isinstance(recorded_value, list) and all(isinstance(item, dict) for item in recorded):
        if recorded != sorted(recorded, key=response_item_sort_key):
            issues.append(issue("question_response_receipt_schema_invalid", "recorded_response_items", "Recorded response items must use stable question-item order"))
        deps = receipt.get("source_dependencies") if isinstance(receipt.get("source_dependencies"), dict) else {}
        submission_id = str(deps.get("question_response_submission_id") or "")
        enterprise = receipt.get("enterprise") if isinstance(receipt.get("enterprise"), dict) else {}
        response_ids: list[Any] = []
        for index, item in enumerate(recorded):
            path = f"recorded_response_items.{index}"
            issues.extend(_answer_local_issues(item, path=path))
            digest = response_item_content_hash(item)
            if item.get("response_item_content_hash") != digest:
                issues.append(issue("question_response_item_hash_mismatch", f"{path}.response_item_content_hash", "Recorded response item content hash mismatch"))
            try:
                expected_item_id = response_item_id(item, enterprise=enterprise, submission_id=submission_id, digest=digest)
            except InputDataError as exc:
                issues.append(issue("question_response_item_id_mismatch", f"{path}.response_item_id", str(exc)))
            else:
                if item.get("response_item_id") != expected_item_id:
                    issues.append(issue("question_response_item_id_mismatch", f"{path}.response_item_id", "Recorded response item ID mismatch"))
            response_ids.append(item.get("response_item_id"))
        if len(response_ids) != len(set(response_ids)):
            issues.append(issue("question_response_item_id_mismatch", "recorded_response_items", "Recorded response item IDs must be unique"))
        _, material_issues = material_ids_and_hashes(recorded)
        issues.extend(material_issues)
    if isinstance(not_in_value, list) and all(isinstance(item, dict) for item in not_in):
        if not_in != sorted(not_in, key=not_in_submission_sort_key):
            issues.append(issue("question_response_receipt_schema_invalid", "not_in_submission_question_items", "Not-in-submission items must use stable order"))
    recorded_question_ids = [item.get("question_item_id") for item in recorded if isinstance(item, dict)]
    not_in_ids = [item.get("question_item_id") for item in not_in if isinstance(item, dict)]
    if set(recorded_question_ids) & set(not_in_ids):
        issues.append(issue("question_response_receipt_coverage_mismatch", "recorded_response_items", "recorded and not_in_submission question items must be disjoint"))
    if len(recorded_question_ids) != len(set(recorded_question_ids)) or len(not_in_ids) != len(set(not_in_ids)):
        issues.append(issue("question_response_receipt_coverage_mismatch", "recorded_response_items", "Question item coverage must not contain duplicates"))
    summary = receipt.get("receipt_summary") if isinstance(receipt.get("receipt_summary"), dict) else {}
    response_count = len(recorded)
    not_count = len(not_in)
    provided = sum(1 for item in recorded if isinstance(item, dict) and item.get("response_outcome") == "provided")
    unable = sum(1 for item in recorded if isinstance(item, dict) and item.get("response_outcome") == "unable_to_provide")
    answer_types = [item.get("answer", {}).get("answer_type") for item in recorded if isinstance(item, dict) and isinstance(item.get("answer"), dict)]
    material_ids = {ref.get("material_id") for item in recorded if isinstance(item, dict) for ref in (item.get("material_references") or []) if isinstance(ref, dict)}
    expected_summary = {
        "question_count_in_plan": response_count + not_count,
        "response_item_count": response_count,
        "provided_response_count": provided,
        "unable_to_provide_count": unable,
        "decision_confirmation_count": answer_types.count("decision_confirmation"),
        "structured_value_count": answer_types.count("structured_value"),
        "conflict_selection_count": answer_types.count("conflict_selection"),
        "clarification_count": answer_types.count("clarification"),
        "response_with_material_count": sum(1 for item in recorded if isinstance(item, dict) and item.get("material_references")),
        "unique_material_count": len(material_ids),
        "not_in_submission_count": not_count,
        "covers_full_current_batch": not_count == 0,
    }
    if summary != expected_summary:
        issues.append(issue("question_response_receipt_summary_mismatch", "receipt_summary", f"Expected {expected_summary}"))
    expected_coverage = "full_current_batch" if not_count == 0 else "partial_current_batch"
    if receipt.get("coverage_status") != expected_coverage:
        issues.append(issue("question_response_receipt_coverage_mismatch", "coverage_status", f"Expected {expected_coverage}"))
    try:
        digest = question_response_receipt_content_hash(receipt)
    except (TypeError, AttributeError, InputDataError) as exc:
        issues.append(issue("question_response_receipt_hash_mismatch", "question_response_receipt_content_hash", str(exc)))
    else:
        if receipt.get("question_response_receipt_content_hash") != digest:
            issues.append(issue("question_response_receipt_hash_mismatch", "question_response_receipt_content_hash", "Receipt content hash mismatch"))
        try:
            expected_id = question_response_receipt_id(receipt, digest)
        except InputDataError as exc:
            issues.append(issue("question_response_receipt_id_mismatch", "question_response_receipt_id", str(exc)))
        else:
            if receipt.get("question_response_receipt_id") != expected_id:
                issues.append(issue("question_response_receipt_id_mismatch", "question_response_receipt_id", "Receipt ID mismatch"))
    return issues


def validate_question_response_receipt_structure_only(receipt: Any) -> list[dict[str, Any]]:
    return _local_integrity_issues(receipt)


def assert_valid_question_response_receipt_structure_only(receipt: Any) -> None:
    issues = validate_question_response_receipt_structure_only(receipt)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def validate_question_response_receipt(
    receipt: Any,
    *,
    submission: Any = None,
    question_plan: Any = None,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> list[dict[str, Any]]:
    local = _local_integrity_issues(receipt)
    if not isinstance(receipt, dict):
        return local
    required = (
        ("submission", submission), ("question_plan", question_plan),
        ("question_plan_request", question_plan_request), ("gap_inventory", gap_inventory),
        ("gap_analysis_request", gap_analysis_request), ("fact_profile", fact_profile),
        ("capability_profile", capability_profile),
    )
    missing = [issue("question_response_validation_context_required", name, f"Strict receipt validation requires {name}") for name, value in required if value is None]
    if isinstance(question_plan, dict):
        deps = question_plan.get("source_dependencies") if isinstance(question_plan.get("source_dependencies"), dict) else {}
        if deps.get("decision_profile_id") is not None and decision_profile is None:
            missing.append(issue("question_response_validation_context_required", "decision_profile", "Question plan records a decision profile dependency"))
    if missing:
        return local + missing
    for name, value in (*required, ("decision_profile", decision_profile)):
        if value is not None and not isinstance(value, dict):
            return local + [issue("question_response_receipt_dependency_mismatch", name, f"{name} must be an object")]
    submission_issues = validate_question_response_submission(
        submission,
        question_plan=question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if submission_issues:
        return local + submission_issues
    issues = list(local)
    expected_deps = response_receipt_source_dependencies(submission, question_plan)
    if receipt.get("source_dependencies") != expected_deps:
        issues.append(issue("question_response_receipt_dependency_mismatch", "source_dependencies", "Receipt source dependencies differ from supplied inputs"))
    if receipt.get("enterprise") != question_plan.get("enterprise"):
        issues.append(issue("question_response_enterprise_mismatch", "enterprise", "Receipt and question plan enterprises differ"))
    if receipt.get("submission_mode") != submission.get("submission_mode") or receipt.get("submitted_by") != submission.get("submitted_by") or receipt.get("submitted_at_utc") != submission.get("submitted_at_utc"):
        issues.append(issue("question_response_receipt_dependency_mismatch", "submission_mode", "Receipt submission event metadata differs from submission"))
    expected = expected_receipt_components(submission, question_plan)
    if receipt.get("coverage_status") != expected["coverage_status"]:
        issues.append(issue("question_response_receipt_coverage_mismatch", "coverage_status", f"Expected {expected['coverage_status']}"))
    if receipt.get("recorded_response_items") != expected["recorded_response_items"]:
        issues.append(issue("question_response_receipt_dependency_mismatch", "recorded_response_items", "Recorded response items differ from the strictly validated submission"))
    if receipt.get("not_in_submission_question_items") != expected["not_in_submission_question_items"]:
        issues.append(issue("question_response_receipt_coverage_mismatch", "not_in_submission_question_items", "Not-in-submission coverage differs from the current question plan"))
    if receipt.get("receipt_summary") != expected["receipt_summary"]:
        issues.append(issue("question_response_receipt_summary_mismatch", "receipt_summary", f"Expected {expected['receipt_summary']}"))
    return issues


def assert_valid_question_response_receipt(receipt: Any, **kwargs: Any) -> None:
    issues = validate_question_response_receipt(receipt, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
