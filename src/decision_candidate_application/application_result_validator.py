"""Structure-only and strict validation for decision-candidate application results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..decision_confirmation_input import validate_confirmation_input
from ..decision_profile_builder import build_decision_profile
from ..decision_profile_validator import validate_decision_profile
from ..enterprise_decision_profile import DECISION_FIELDS, decision_profile_content_payload
from ..errors import InputDataError
from .application_builder import build_decision_application_result_payload
from .application_request import APPLICATION_RESULT_SCHEMA_VERSION
from .application_request_validator import validate_decision_application_request
from .application_result import (
    APPLICATION_STATUS,
    application_result_content_hash,
    application_result_id,
    application_summary,
    finalize_application_result,
    selected_candidate_ids,
)
from .candidate_selector import aggregate_decision_candidates, select_decision_candidates
from .errors import issue

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_decision_profile_update_application_result.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())
_FIELD_ORDER = {code: index for index, (code, _name) in enumerate(DECISION_FIELDS)}


def _local_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [issue("decision_application_result_schema_invalid", "$", "Application result must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("decision_application_result_schema_invalid", path, error.message))
    if value.get("application_result_schema_version") != APPLICATION_RESULT_SCHEMA_VERSION:
        issues.append(issue("decision_application_result_schema_invalid", "application_result_schema_version", "Unsupported application result schema version"))

    selected = value.get("selected_candidate_ids") if isinstance(value.get("selected_candidate_ids"), list) else []
    if len(selected) != len(set(selected)):
        issues.append(issue("decision_application_result_schema_invalid", "selected_candidate_ids", "Selected candidate IDs must be unique"))
    if selected != sorted(selected, key=str):
        issues.append(issue("decision_application_result_schema_invalid", "selected_candidate_ids", "Selected candidate IDs must use stable sorted order"))

    aggregate = value.get("aggregate_confirmation_input")
    if isinstance(aggregate, dict):
        nested = validate_confirmation_input(aggregate)
        if nested:
            issues.append(issue("decision_application_result_schema_invalid", "aggregate_confirmation_input", nested[0]["message"]))
        updates = aggregate.get("updates") if isinstance(aggregate.get("updates"), list) else []
        codes = [item.get("field_code") for item in updates if isinstance(item, dict)]
        if codes != sorted(codes, key=lambda code: _FIELD_ORDER.get(str(code), 999)):
            issues.append(issue("decision_application_result_schema_invalid", "aggregate_confirmation_input.updates", "Aggregate updates must use the fixed decision-field order"))
    else:
        updates = []
        codes = []

    expected_summary = {
        "selected_candidate_count": len(selected),
        "updated_field_count": len(codes),
        "updated_field_codes": codes,
    }
    if value.get("application_summary") != expected_summary:
        issues.append(issue("decision_application_result_summary_mismatch", "application_summary", f"Expected {expected_summary}"))
    if value.get("application_status") != APPLICATION_STATUS:
        issues.append(issue("decision_application_result_schema_invalid", "application_status", "application_status must be decision_profile_updated"))
    try:
        digest = application_result_content_hash(value)
        expected_id = application_result_id(value, digest)
    except (InputDataError, TypeError, AttributeError) as exc:
        issues.append(issue("decision_application_result_hash_mismatch", "application_result_content_hash", str(exc)))
    else:
        if value.get("application_result_content_hash") != digest:
            issues.append(issue("decision_application_result_hash_mismatch", "application_result_content_hash", "Application result content hash mismatch"))
        if value.get("application_result_id") != expected_id:
            issues.append(issue("decision_application_result_id_mismatch", "application_result_id", "Application result ID mismatch"))
    return issues


def validate_decision_application_result_structure_only(value: Any) -> list[dict[str, Any]]:
    return _local_issues(value)


def assert_valid_decision_application_result_structure_only(value: Any) -> None:
    issues = validate_decision_application_result_structure_only(value)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _context_issues(
    *, application_request: Any, result_decision_profile: Any, worklist: Any, receipt: Any,
    submission: Any, question_plan: Any, question_plan_request: Any, gap_inventory: Any,
    gap_analysis_request: Any, fact_profile: Any, capability_profile: Any,
    base_decision_profile: Any,
) -> list[dict[str, Any]]:
    required = (
        ("application_request", application_request),
        ("result_decision_profile", result_decision_profile),
        ("worklist", worklist), ("receipt", receipt), ("submission", submission),
        ("question_plan", question_plan), ("question_plan_request", question_plan_request),
        ("gap_inventory", gap_inventory), ("gap_analysis_request", gap_analysis_request),
        ("fact_profile", fact_profile), ("capability_profile", capability_profile),
    )
    missing = [issue("decision_application_validation_context_required", name, f"Strict application result validation requires {name}") for name, item in required if item is None]
    if missing:
        return missing
    invalid = [issue("decision_application_result_dependency_mismatch", name, f"{name} must be an object") for name, item in required if not isinstance(item, dict)]
    if base_decision_profile is not None and not isinstance(base_decision_profile, dict):
        invalid.append(issue("decision_application_base_profile_mismatch", "base_decision_profile", "base_decision_profile must be an object"))
    return invalid


def validate_decision_application_result(
    value: Any,
    *,
    application_request: Any = None,
    result_decision_profile: Any = None,
    worklist: Any = None,
    receipt: Any = None,
    submission: Any = None,
    question_plan: Any = None,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    base_decision_profile: Any = None,
) -> list[dict[str, Any]]:
    local = _local_issues(value)
    if not isinstance(value, dict):
        return local
    context = _context_issues(
        application_request=application_request,
        result_decision_profile=result_decision_profile,
        worklist=worklist,
        receipt=receipt,
        submission=submission,
        question_plan=question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        base_decision_profile=base_decision_profile,
    )
    if context:
        return local + context
    request_issues = validate_decision_application_request(
        application_request,
        worklist=worklist,
        receipt=receipt,
        submission=submission,
        question_plan=question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        base_decision_profile=base_decision_profile,
    )
    if request_issues:
        return local + request_issues
    issues = list(local)
    try:
        selected = select_decision_candidates(application_request, worklist)
        aggregate = aggregate_decision_candidates(
            selected,
            enterprise=application_request.get("enterprise") or {},
            base_decision_profile=base_decision_profile,
        )
        expected_profile = build_decision_profile(
            fact_profile,
            capability_profile,
            aggregate,
            base_decision_profile=base_decision_profile,
            generated_at_utc=(
                result_decision_profile.get("generated_at_utc")
                if isinstance(result_decision_profile.get("generated_at_utc"), str)
                else "2000-01-01T00:00:00+00:00"
            ),
        )
    except InputDataError as exc:
        text = str(exc)
        prefix = text.split(":", 1)[0]
        code = prefix if prefix.startswith("decision_") else "decision_application_result_profile_mismatch"
        return issues + [issue(code, "result_decision_profile", text)]

    profile_issues = validate_decision_profile(
        result_decision_profile,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        previous_decision_profile=base_decision_profile,
        confirmation_input=aggregate,
    )
    if profile_issues:
        issues.append(issue("decision_application_result_profile_mismatch", "result_decision_profile", profile_issues[0]["message"]))
    if decision_profile_content_payload(result_decision_profile) != decision_profile_content_payload(expected_profile):
        issues.append(issue("decision_application_result_profile_mismatch", "result_decision_profile", "Result decision profile differs from atomic application output"))

    expected_payload = build_decision_application_result_payload(
        application_request,
        worklist,
        selected,
        aggregate,
        expected_profile,
        base_decision_profile=base_decision_profile,
    )
    if value.get("enterprise") != expected_payload["enterprise"]:
        issues.append(issue("decision_application_enterprise_mismatch", "enterprise", "Application result enterprise differs"))
    if value.get("source_dependencies") != expected_payload["source_dependencies"]:
        issues.append(issue("decision_application_result_dependency_mismatch", "source_dependencies", "Application result dependencies differ"))
    if value.get("selected_candidate_ids") != expected_payload["selected_candidate_ids"]:
        issues.append(issue("decision_application_result_dependency_mismatch", "selected_candidate_ids", "Selected candidates differ from application request"))
    if value.get("aggregate_confirmation_input") != aggregate:
        issues.append(issue("decision_application_result_dependency_mismatch", "aggregate_confirmation_input", "Aggregate confirmation input differs from selected candidates"))
    if value.get("result_decision_profile") != expected_payload["result_decision_profile"]:
        issues.append(issue("decision_application_result_profile_mismatch", "result_decision_profile", "Result profile identity summary differs"))
    if value.get("application_summary") != application_summary(selected):
        issues.append(issue("decision_application_result_summary_mismatch", "application_summary", f"Expected {application_summary(selected)}"))
    if value.get("application_status") != APPLICATION_STATUS or value.get("warnings") != []:
        issues.append(issue("decision_application_result_dependency_mismatch", "application_status", "Application status or warnings differ from successful atomic application"))
    return issues


def assert_valid_decision_application_result(value: Any, **kwargs: Any) -> None:
    issues = validate_decision_application_result(value, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
