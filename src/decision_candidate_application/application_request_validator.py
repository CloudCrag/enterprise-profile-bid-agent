"""Structure-only and strict validation for explicit candidate application requests."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from ..profile_response_processing.worklist_validator import validate_response_processing_worklist
from .application_request import (
    APPLICATION_REQUEST_SCHEMA_VERSION,
    application_request_content_hash,
    application_request_id,
    normalized_selected_candidate_ids,
)
from .candidate_selector import aggregate_decision_candidates, select_decision_candidates
from .errors import issue

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_decision_profile_update_application_request.schema.json").read_text(encoding="utf-8"))
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


def _error_issue(exc: InputDataError, *, path: str, default_code: str) -> dict[str, Any]:
    text = str(exc)
    prefix = text.split(":", 1)[0]
    code = prefix if prefix.startswith("decision_application_") else default_code
    return issue(code, path, text)


def _local_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [issue("decision_application_request_schema_invalid", "$", "Application request must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("decision_application_request_schema_invalid", path, error.message))
    if value.get("application_request_schema_version") != APPLICATION_REQUEST_SCHEMA_VERSION:
        issues.append(issue("decision_application_request_schema_invalid", "application_request_schema_version", "Unsupported application request schema version"))
    ids = value.get("selected_candidate_ids") if isinstance(value.get("selected_candidate_ids"), list) else []
    if len(ids) != len(set(ids)):
        issues.append(issue("decision_application_candidate_duplicate", "selected_candidate_ids", "Selected candidate IDs must be unique"))
    if ids != normalized_selected_candidate_ids(ids):
        issues.append(issue("decision_application_request_schema_invalid", "selected_candidate_ids", "Selected candidate IDs must use stable sorted order"))
    if not _is_utc_datetime(value.get("requested_at_utc")):
        issues.append(issue("decision_application_request_schema_invalid", "requested_at_utc", "requested_at_utc must be a UTC datetime"))
    try:
        digest = application_request_content_hash(value)
        expected_id = application_request_id(value, digest)
    except (InputDataError, TypeError, AttributeError) as exc:
        issues.append(issue("decision_application_request_hash_mismatch", "application_request_content_hash", str(exc)))
    else:
        if value.get("application_request_content_hash") != digest:
            issues.append(issue("decision_application_request_hash_mismatch", "application_request_content_hash", "Application request content hash mismatch"))
        if value.get("application_request_id") != expected_id:
            issues.append(issue("decision_application_request_id_mismatch", "application_request_id", "Application request ID mismatch"))
    return issues


def validate_decision_application_request_structure_only(value: Any) -> list[dict[str, Any]]:
    return _local_issues(value)


def assert_valid_decision_application_request_structure_only(value: Any) -> None:
    issues = validate_decision_application_request_structure_only(value)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _context_issues(
    value: dict[str, Any], *, worklist: Any, receipt: Any, submission: Any, question_plan: Any,
    question_plan_request: Any, gap_inventory: Any, gap_analysis_request: Any,
    fact_profile: Any, capability_profile: Any, base_decision_profile: Any,
) -> list[dict[str, Any]]:
    required = (
        ("worklist", worklist), ("receipt", receipt), ("submission", submission),
        ("question_plan", question_plan), ("question_plan_request", question_plan_request),
        ("gap_inventory", gap_inventory), ("gap_analysis_request", gap_analysis_request),
        ("fact_profile", fact_profile), ("capability_profile", capability_profile),
    )
    missing = [issue("decision_application_validation_context_required", name, f"Strict application validation requires {name}") for name, item in required if item is None]
    if missing:
        return missing
    invalid = [issue("decision_application_worklist_dependency_mismatch", name, f"{name} must be an object") for name, item in required if not isinstance(item, dict)]
    if base_decision_profile is not None and not isinstance(base_decision_profile, dict):
        invalid.append(issue("decision_application_base_profile_mismatch", "base_decision_profile", "base_decision_profile must be an object"))
    if invalid:
        return invalid
    deps = worklist.get("source_dependencies") if isinstance(worklist, dict) and isinstance(worklist.get("source_dependencies"), dict) else {}
    if deps.get("decision_profile_id") is not None and base_decision_profile is None:
        return [issue("decision_application_validation_context_required", "base_decision_profile", "Selected worklist depends on a base decision profile")]
    return []


def validate_decision_application_request(
    value: Any,
    *,
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
        value, worklist=worklist, receipt=receipt, submission=submission,
        question_plan=question_plan, question_plan_request=question_plan_request,
        gap_inventory=gap_inventory, gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile, capability_profile=capability_profile,
        base_decision_profile=base_decision_profile,
    )
    if context:
        return local + context
    # Give the application boundary a precise base-profile error before validating the full source chain.
    try:
        provisional = select_decision_candidates(value, worklist)
        provisional_pairs = {(item.get("base_decision_profile_id"), item.get("base_decision_profile_content_hash")) for item in provisional}
    except InputDataError:
        provisional_pairs = set()
    if len(provisional_pairs) == 1:
        base_id, base_hash = next(iter(provisional_pairs))
        if base_id is None and base_decision_profile is not None:
            return local + [issue("decision_application_base_profile_mismatch", "base_decision_profile", "Initial candidates must not receive a base profile")]
        if base_id is not None and isinstance(base_decision_profile, dict) and (
            base_decision_profile.get("decision_profile_id") != base_id
            or base_decision_profile.get("decision_profile_content_hash") != base_hash
        ):
            return local + [issue("decision_application_base_profile_mismatch", "base_decision_profile", "Supplied base profile does not match selected candidates")]
    worklist_issues = validate_response_processing_worklist(
        worklist,
        receipt=receipt,
        submission=submission,
        question_plan=question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=base_decision_profile,
    )
    if worklist_issues:
        return local + worklist_issues
    issues = list(local)
    if value.get("enterprise") != worklist.get("enterprise"):
        issues.append(issue("decision_application_enterprise_mismatch", "enterprise", "Application request and worklist enterprises differ"))
    if (
        value.get("response_processing_worklist_id") != worklist.get("response_processing_worklist_id")
        or value.get("response_processing_worklist_content_hash") != worklist.get("response_processing_worklist_content_hash")
    ):
        issues.append(issue("decision_application_worklist_dependency_mismatch", "response_processing_worklist_id", "Application request does not reference the supplied worklist"))
    if issues:
        return issues
    try:
        selected = select_decision_candidates(value, worklist)
        aggregate_decision_candidates(
            selected,
            enterprise=value.get("enterprise") or {},
            base_decision_profile=base_decision_profile,
        )
    except InputDataError as exc:
        issues.append(_error_issue(exc, path="selected_candidate_ids", default_code="decision_application_candidate_not_found"))
    return issues


def assert_valid_decision_application_request(value: Any, **kwargs: Any) -> None:
    issues = validate_decision_application_request(value, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
