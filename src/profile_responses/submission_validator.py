"""Structure-only and strict validation for question-response submissions."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..decision_confirmation_input import validate_confirmation_input
from ..errors import InputDataError
from ..profile_questions.plan_validator import validate_question_plan
from .errors import issue
from .material_reference import material_ids_and_hashes
from .response_item import response_item_content_hash, response_item_id, response_item_sort_key
from .submission import (
    QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION,
    question_response_submission_content_hash,
    question_response_submission_id,
)

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_question_response_submission.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _is_utc_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _answer_local_issues(item: dict[str, Any], *, path: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    outcome = item.get("response_outcome")
    answer = item.get("answer")
    materials = item.get("material_references")
    note = item.get("unable_to_provide_note")
    if outcome == "unable_to_provide":
        if answer is not None or materials != []:
            issues.append(issue("question_response_unable_payload_invalid", path, "unable_to_provide requires null answer and empty material_references"))
        if note is not None and (not isinstance(note, str) or not note.strip()):
            issues.append(issue("question_response_unable_payload_invalid", f"{path}.unable_to_provide_note", "unable_to_provide_note must be null or a non-empty string"))
        return issues
    if outcome != "provided":
        return issues
    if note is not None:
        issues.append(issue("question_response_unable_payload_invalid", f"{path}.unable_to_provide_note", "provided responses require null unable_to_provide_note"))
    has_answer = isinstance(answer, dict)
    has_material = isinstance(materials, list) and len(materials) > 0
    if not has_answer and not has_material:
        issues.append(issue("question_response_answer_required", path, "provided response requires an answer or at least one material reference"))
    if has_answer:
        answer_type = answer.get("answer_type")
        if answer_type == "structured_value":
            value = answer.get("value")
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(issue("question_response_answer_required", f"{path}.answer.value", "structured value must be non-null and non-empty when string"))
        elif answer_type == "clarification":
            text = answer.get("clarification_text")
            if not isinstance(text, str) or not text.strip():
                issues.append(issue("question_response_answer_required", f"{path}.answer.clarification_text", "clarification_text must be non-empty"))
        elif answer_type == "conflict_selection":
            refs = answer.get("selected_source_reference_ids")
            if not isinstance(refs, list) or not refs:
                issues.append(issue("question_response_conflict_reference_invalid", f"{path}.answer.selected_source_reference_ids", "At least one source reference must be selected"))
            elif len(refs) != len(set(refs)):
                issues.append(issue("question_response_conflict_reference_invalid", f"{path}.answer.selected_source_reference_ids", "Selected source reference IDs must be unique"))
        elif answer_type == "decision_confirmation":
            confirmation = answer.get("decision_confirmation_input")
            nested = validate_confirmation_input(confirmation)
            if nested:
                issues.append(issue("question_response_decision_confirmation_mismatch", f"{path}.answer.decision_confirmation_input", nested[0]["message"]))
        else:
            issues.append(issue("question_response_answer_type_invalid", f"{path}.answer.answer_type", f"Unknown answer_type {answer_type!r}"))
    return issues


def _local_integrity_issues(submission: Any) -> list[dict[str, Any]]:
    if not isinstance(submission, dict):
        return [issue("question_response_submission_schema_invalid", "$", "Question response submission must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(submission), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("question_response_submission_schema_invalid", path, error.message))
    if submission.get("question_response_submission_schema_version") != QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION:
        issues.append(issue("question_response_submission_schema_invalid", "question_response_submission_schema_version", "Unsupported submission schema version"))
    if not _is_utc_datetime(submission.get("submitted_at_utc")):
        issues.append(issue("question_response_submission_schema_invalid", "submitted_at_utc", "submitted_at_utc must be an explicit UTC datetime"))
    actor = submission.get("submitted_by") if isinstance(submission.get("submitted_by"), dict) else {}
    if actor.get("actor_type") != "user" or not isinstance(actor.get("actor_id"), str) or not actor.get("actor_id", "").strip():
        issues.append(issue("question_response_submission_schema_invalid", "submitted_by", "submitted_by must identify a non-empty user actor"))

    items_value = submission.get("response_items")
    items = items_value if isinstance(items_value, list) else []
    if isinstance(items_value, list) and all(isinstance(item, dict) for item in items):
        if items != sorted(items, key=response_item_sort_key):
            issues.append(issue("question_response_submission_schema_invalid", "response_items", "response_items must use stable question-item order"))
        question_ids = [item.get("question_item_id") for item in items]
        duplicates = sorted({str(value) for value in question_ids if question_ids.count(value) > 1})
        if duplicates:
            issues.append(issue("question_response_duplicate_question_item", "response_items", f"Duplicate question_item_id values: {duplicates}"))
        item_ids: list[Any] = []
        submission_id = str(submission.get("question_response_submission_id") or "")
        enterprise = submission.get("enterprise") if isinstance(submission.get("enterprise"), dict) else {}
        for index, item in enumerate(items):
            path = f"response_items.{index}"
            issues.extend(_answer_local_issues(item, path=path))
            digest = response_item_content_hash(item)
            if item.get("response_item_content_hash") != digest:
                issues.append(issue("question_response_item_hash_mismatch", f"{path}.response_item_content_hash", "Response item content hash mismatch"))
            try:
                expected_id = response_item_id(item, enterprise=enterprise, submission_id=submission_id, digest=digest)
            except InputDataError as exc:
                issues.append(issue("question_response_item_id_mismatch", f"{path}.response_item_id", str(exc)))
            else:
                if item.get("response_item_id") != expected_id:
                    issues.append(issue("question_response_item_id_mismatch", f"{path}.response_item_id", "Response item ID mismatch"))
            item_ids.append(item.get("response_item_id"))
        duplicate_item_ids = sorted({str(value) for value in item_ids if item_ids.count(value) > 1})
        if duplicate_item_ids:
            issues.append(issue("question_response_item_id_mismatch", "response_items", f"Duplicate response_item_id values: {duplicate_item_ids}"))
        _, material_issues = material_ids_and_hashes(items)
        issues.extend(material_issues)

    try:
        digest = question_response_submission_content_hash(submission)
    except (TypeError, AttributeError, InputDataError) as exc:
        issues.append(issue("question_response_submission_hash_mismatch", "question_response_submission_content_hash", str(exc)))
    else:
        if submission.get("question_response_submission_content_hash") != digest:
            issues.append(issue("question_response_submission_hash_mismatch", "question_response_submission_content_hash", "Submission content hash mismatch"))
        try:
            expected_id = question_response_submission_id(submission, digest)
        except InputDataError as exc:
            issues.append(issue("question_response_submission_id_mismatch", "question_response_submission_id", str(exc)))
        else:
            if submission.get("question_response_submission_id") != expected_id:
                issues.append(issue("question_response_submission_id_mismatch", "question_response_submission_id", "Submission ID mismatch"))
    return issues


def validate_question_response_submission_structure_only(submission: Any) -> list[dict[str, Any]]:
    return _local_integrity_issues(submission)


def assert_valid_question_response_submission_structure_only(submission: Any) -> None:
    issues = validate_question_response_submission_structure_only(submission)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _required_context_issues(question_plan: Any, question_plan_request: Any, gap_inventory: Any, gap_analysis_request: Any, fact_profile: Any, capability_profile: Any, decision_profile: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for name, value in (
        ("question_plan", question_plan), ("question_plan_request", question_plan_request),
        ("gap_inventory", gap_inventory), ("gap_analysis_request", gap_analysis_request),
        ("fact_profile", fact_profile), ("capability_profile", capability_profile),
    ):
        if value is None:
            issues.append(issue("question_response_validation_context_required", name, f"Strict response validation requires {name}"))
    if isinstance(question_plan, dict):
        deps = question_plan.get("source_dependencies") if isinstance(question_plan.get("source_dependencies"), dict) else {}
        if deps.get("decision_profile_id") is not None and decision_profile is None:
            issues.append(issue("question_response_validation_context_required", "decision_profile", "Question plan records a decision profile dependency"))
    return issues


def _context_type_issues(question_plan: Any, question_plan_request: Any, gap_inventory: Any, gap_analysis_request: Any, fact_profile: Any, capability_profile: Any, decision_profile: Any) -> list[dict[str, Any]]:
    checks = (
        ("question_plan", question_plan, "question_response_plan_dependency_mismatch"),
        ("question_plan_request", question_plan_request, "question_response_plan_dependency_mismatch"),
        ("gap_inventory", gap_inventory, "question_response_gap_dependency_mismatch"),
        ("gap_analysis_request", gap_analysis_request, "question_response_gap_dependency_mismatch"),
        ("fact_profile", fact_profile, "question_response_gap_dependency_mismatch"),
        ("capability_profile", capability_profile, "question_response_gap_dependency_mismatch"),
        ("decision_profile", decision_profile, "question_response_gap_dependency_mismatch"),
    )
    return [issue(code, name, f"{name} must be an object") for name, value, code in checks if value is not None and not isinstance(value, dict)]


def _all_source_reference_ids(gap: dict[str, Any]) -> set[str]:
    refs = gap.get("source_references") if isinstance(gap.get("source_references"), dict) else {}
    values: set[str] = set()
    for key in ("fact_ids", "capability_ids", "evidence_ids"):
        items = refs.get(key)
        if isinstance(items, list):
            values.update(str(item) for item in items)
    decision_id = refs.get("decision_profile_id")
    if isinstance(decision_id, str):
        values.add(decision_id)
    return values


def _mode_issues(
    item: dict[str, Any],
    question: dict[str, Any],
    gap: dict[str, Any],
    decision_profile: dict[str, Any] | None,
    enterprise: dict[str, Any],
    submission_actor: dict[str, Any],
    *,
    path: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if item.get("response_outcome") == "unable_to_provide":
        return issues
    request_type = question.get("information_request_type")
    answer = item.get("answer")
    answer_type = answer.get("answer_type") if isinstance(answer, dict) else None
    materials = item.get("material_references") if isinstance(item.get("material_references"), list) else []
    material_allowed = bool((question.get("expected_response") or {}).get("material_allowed"))
    if materials and not material_allowed:
        issues.append(issue("question_response_material_not_allowed", f"{path}.material_references", "Materials are not allowed for this question item"))
    expected_answer_type = {
        "decision_confirmation": "decision_confirmation",
        "conflict_clarification": "conflict_selection",
        "capability_clarification": "clarification",
    }.get(str(request_type))
    if expected_answer_type is not None and answer_type != expected_answer_type:
        issues.append(issue("question_response_mode_mismatch", f"{path}.answer", f"{request_type} requires answer_type={expected_answer_type}"))
    if request_type in {"supporting_material", "fact_verification"} and answer is not None and answer_type != "structured_value":
        issues.append(issue("question_response_mode_mismatch", f"{path}.answer", f"{request_type} allows only structured_value when an answer is supplied"))
    if request_type == "decision_confirmation" and isinstance(answer, dict):
        confirmation = answer.get("decision_confirmation_input")
        nested = validate_confirmation_input(confirmation)
        if nested:
            issues.append(issue("question_response_decision_confirmation_mismatch", f"{path}.answer.decision_confirmation_input", nested[0]["message"]))
        elif isinstance(confirmation, dict):
            updates = confirmation.get("updates") if isinstance(confirmation.get("updates"), list) else []
            if len(updates) != 1 or updates[0].get("field_code") != question.get("target_code") or updates[0].get("action") != "set":
                issues.append(issue("question_response_decision_confirmation_mismatch", f"{path}.answer.decision_confirmation_input.updates", "Decision confirmation must set exactly the current target field"))
            if confirmation.get("enterprise") != enterprise:
                issues.append(issue("question_response_decision_confirmation_mismatch", f"{path}.answer.decision_confirmation_input.enterprise", "Decision confirmation enterprise mismatch"))
            confirmation_actor = confirmation.get("confirmation_actor") if isinstance(confirmation.get("confirmation_actor"), dict) else {}
            if (
                confirmation_actor.get("actor_type") != submission_actor.get("actor_type")
                or confirmation_actor.get("actor_id") != submission_actor.get("actor_id")
            ):
                issues.append(issue(
                    "question_response_decision_actor_mismatch",
                    f"{path}.answer.decision_confirmation_input.confirmation_actor",
                    "Decision confirmation actor must match submission submitted_by actor",
                ))
            expected_base_id = decision_profile.get("decision_profile_id") if isinstance(decision_profile, dict) else None
            expected_base_hash = decision_profile.get("decision_profile_content_hash") if isinstance(decision_profile, dict) else None
            if confirmation.get("base_decision_profile_id") != expected_base_id or confirmation.get("base_decision_profile_content_hash") != expected_base_hash:
                issues.append(issue("question_response_decision_confirmation_mismatch", f"{path}.answer.decision_confirmation_input", "Decision confirmation base profile dependency mismatch"))
    if request_type == "conflict_clarification" and isinstance(answer, dict):
        selected = answer.get("selected_source_reference_ids") if isinstance(answer.get("selected_source_reference_ids"), list) else []
        allowed = _all_source_reference_ids(gap)
        if not allowed or any(str(value) not in allowed for value in selected):
            issues.append(issue("question_response_conflict_reference_invalid", f"{path}.answer.selected_source_reference_ids", "Selected source references must belong to the current gap"))
    return issues


def validate_question_response_submission(
    submission: Any,
    *,
    question_plan: Any = None,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> list[dict[str, Any]]:
    local = _local_integrity_issues(submission)
    if not isinstance(submission, dict):
        return local
    missing = _required_context_issues(question_plan, question_plan_request, gap_inventory, gap_analysis_request, fact_profile, capability_profile, decision_profile)
    if missing:
        return local + missing
    type_issues = _context_type_issues(question_plan, question_plan_request, gap_inventory, gap_analysis_request, fact_profile, capability_profile, decision_profile)
    if type_issues:
        return local + type_issues
    plan_issues = validate_question_plan(
        question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if plan_issues:
        return local + plan_issues
    issues = list(local)
    if submission.get("enterprise") != question_plan.get("enterprise"):
        issues.append(issue("question_response_enterprise_mismatch", "enterprise", "Submission and question plan enterprises differ"))
    if submission.get("question_plan_id") != question_plan.get("question_plan_id") or submission.get("question_plan_content_hash") != question_plan.get("question_plan_content_hash"):
        issues.append(issue("question_response_plan_dependency_mismatch", "question_plan_id", "Submission references another question plan"))

    questions = question_plan.get("question_items") if isinstance(question_plan.get("question_items"), list) else []
    question_map = {item.get("question_item_id"): item for item in questions if isinstance(item, dict)}
    gaps = gap_inventory.get("gaps") if isinstance(gap_inventory.get("gaps"), list) else []
    gap_map = {item.get("gap_id"): item for item in gaps if isinstance(item, dict)}
    items = submission.get("response_items") if isinstance(submission.get("response_items"), list) else []
    submitted_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        path = f"response_items.{index}"
        question = question_map.get(item.get("question_item_id"))
        if question is None:
            issues.append(issue("question_response_question_item_not_found", f"{path}.question_item_id", "Response item is not a current question item; deferred and excluded gaps cannot be answered"))
            continue
        submitted_ids.add(str(item.get("question_item_id")))
        expected_fields = ("question_item_content_hash", "gap_id", "gap_content_hash", "requirement_id", "target_layer", "target_code")
        for key in expected_fields:
            if item.get(key) != question.get(key):
                code = "question_response_gap_dependency_mismatch" if key.startswith("gap_") else "question_response_question_item_mismatch"
                issues.append(issue(code, f"{path}.{key}", f"Expected {question.get(key)!r}"))
        gap = gap_map.get(question.get("gap_id"))
        if not isinstance(gap, dict):
            issues.append(issue("question_response_gap_dependency_mismatch", f"{path}.gap_id", "Current question gap is missing from the strictly validated inventory"))
            continue
        submission_actor = submission.get("submitted_by") if isinstance(submission.get("submitted_by"), dict) else {}
        issues.extend(_mode_issues(
            item,
            question,
            gap,
            decision_profile if isinstance(decision_profile, dict) else None,
            question_plan.get("enterprise") or {},
            submission_actor,
            path=path,
        ))
    expected_ids = {str(item.get("question_item_id")) for item in questions if isinstance(item, dict)}
    if submission.get("submission_mode") == "full_current_batch" and submitted_ids != expected_ids:
        issues.append(issue("question_response_full_submission_incomplete", "response_items", "full_current_batch must cover every current question item exactly once"))
    if not submitted_ids <= expected_ids:
        issues.append(issue("question_response_question_item_not_found", "response_items", "Submission contains question items outside the current question plan"))
    return issues


def assert_valid_question_response_submission(submission: Any, **kwargs: Any) -> None:
    issues = validate_question_response_submission(submission, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
