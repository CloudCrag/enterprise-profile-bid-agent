"""Runtime JSON-Schema validation for enterprise fact profiles and fact payloads."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .errors import InputDataError
from .evidence import validate_evidence_index

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "enterprise_fact_profile.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_FORMAT_CHECKER = FormatChecker()

@_FORMAT_CHECKER.checks("iso-date-time")
def _is_iso_date_time(value: object) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
_PROFILE_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=_FORMAT_CHECKER)
_FACT_SCHEMA = {
    "$schema": _SCHEMA["$schema"],
    "$defs": _SCHEMA["$defs"],
    "$ref": "#/$defs/fact",
}
_FACT_VALIDATOR = Draft202012Validator(_FACT_SCHEMA, format_checker=_FORMAT_CHECKER)

_REQUIRED_RE = re.compile(r"^'([^']+)' is a required property$")


def _path_text(path: list[Any], error: ValidationError) -> str:
    parts = [str(item) for item in path]
    match = _REQUIRED_RE.match(error.message)
    if match:
        parts.append(match.group(1))
    return ".".join(parts) or "$"


def _leaf_error(error: ValidationError) -> ValidationError:
    """Prefer the most actionable child error from oneOf/anyOf branches."""
    if not error.context:
        return error
    required = [item for item in error.context if item.validator == "required"]
    if required:
        return _leaf_error(required[0])
    non_type = [item for item in error.context if item.validator not in {"oneOf", "anyOf", "type"}]
    if non_type:
        return _leaf_error(non_type[0])
    return _leaf_error(error.context[0])


def _issue_from_error(error: ValidationError, *, fact: dict[str, Any] | None = None) -> dict[str, Any]:
    leaf = _leaf_error(error)
    path = list(leaf.absolute_path)
    # A standalone fact validator starts directly at the fact object. A profile
    # validator includes facts.<index>; both paths are kept explicit.
    path_text = _path_text(path, leaf)
    fact_obj = fact
    if fact_obj is None and len(path) >= 2 and path[0] == "facts" and isinstance(path[1], int):
        fact_obj = None
    code = "fact_payload_schema_invalid" if "payload" in [str(item) for item in path] else "fact_schema_invalid"
    issue = {
        "code": code,
        "fact_id": (fact_obj or {}).get("fact_id"),
        "fact_type": (fact_obj or {}).get("fact_type"),
        "field_path": path_text,
        "message": leaf.message,
    }
    return issue


def validate_fact(fact: Any) -> list[dict[str, Any]]:
    if not isinstance(fact, dict):
        return [{
            "code": "fact_schema_invalid",
            "fact_id": None,
            "fact_type": None,
            "field_path": "$",
            "message": "fact must be a JSON object",
        }]
    errors = sorted(_FACT_VALIDATOR.iter_errors(fact), key=lambda item: (list(item.absolute_path), item.message))
    return [_issue_from_error(error, fact=fact) for error in errors]


def assert_valid_fact(fact: Any) -> None:
    issues = validate_fact(fact)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def validate_fact_profile(profile: Any) -> list[dict[str, Any]]:
    if not isinstance(profile, dict):
        return [{"code": "fact_profile_schema_invalid", "field_path": "$", "message": "profile must be a JSON object"}]

    issues: list[dict[str, Any]] = []
    facts = profile.get("facts") if isinstance(profile.get("facts"), list) else []
    for error in sorted(_PROFILE_VALIDATOR.iter_errors(profile), key=lambda item: (list(item.absolute_path), item.message)):
        fact_obj = None
        path = list(error.absolute_path)
        if len(path) >= 2 and path[0] == "facts" and isinstance(path[1], int) and path[1] < len(facts):
            fact_obj = facts[path[1]] if isinstance(facts[path[1]], dict) else None
        issue = _issue_from_error(error, fact=fact_obj)
        if not fact_obj and not any(str(part) == "payload" for part in path):
            issue["code"] = "fact_profile_schema_invalid"
        issues.append(issue)

    evidence_index = profile.get("evidence_index")
    if isinstance(evidence_index, dict):
        issues.extend(validate_evidence_index(evidence_index))
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            for evidence_id in fact.get("evidence_ids", []):
                if evidence_id not in evidence_index:
                    issues.append({
                        "code": "fact_evidence_reference_unresolved",
                        "fact_id": fact.get("fact_id"),
                        "fact_type": fact.get("fact_type"),
                        "field_path": "evidence_ids",
                        "evidence_id": evidence_id,
                        "message": "evidence reference does not exist in evidence_index",
                    })

    fact_ids = {fact.get("fact_id") for fact in facts if isinstance(fact, dict) and fact.get("fact_id")}
    seen: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_id = fact.get("fact_id")
        if fact_id in seen:
            issues.append({"code": "duplicate_fact_id", "fact_id": fact_id, "field_path": "facts", "message": "fact_id must be unique"})
        if fact_id:
            seen.add(fact_id)


    fact_view = profile.get("fact_view") if isinstance(profile.get("fact_view"), dict) else {}
    included = fact_view.get("included_fact_ids") if isinstance(fact_view.get("included_fact_ids"), list) else []
    excluded = fact_view.get("excluded_fact_ids") if isinstance(fact_view.get("excluded_fact_ids"), list) else []
    included_set, excluded_set = set(included), set(excluded)
    overlap = included_set & excluded_set
    if overlap:
        issues.append({
            "code": "fact_view_overlap",
            "field_path": "fact_view",
            "message": "included_fact_ids and excluded_fact_ids must not overlap",
            "fact_ids": sorted(overlap),
        })
    unknown_ids = (included_set | excluded_set) - fact_ids
    if unknown_ids:
        issues.append({
            "code": "fact_view_reference_unresolved",
            "field_path": "fact_view",
            "message": "fact_view references facts that do not exist",
            "fact_ids": sorted(unknown_ids),
        })
    missing_classification = fact_ids - (included_set | excluded_set)
    if missing_classification:
        issues.append({
            "code": "fact_view_incomplete",
            "field_path": "fact_view",
            "message": "every fact must be classified as included or excluded",
            "fact_ids": sorted(missing_classification),
        })
    reasons = fact_view.get("exclusion_reasons") if isinstance(fact_view.get("exclusion_reasons"), dict) else {}
    if set(reasons) != excluded_set:
        issues.append({
            "code": "fact_view_exclusion_reason_mismatch",
            "field_path": "fact_view.exclusion_reasons",
            "message": "every excluded fact must have exactly one exclusion reason",
        })
    if fact_view.get("as_of_date") != profile.get("as_of_date"):
        issues.append({
            "code": "fact_view_as_of_date_mismatch",
            "field_path": "fact_view.as_of_date",
            "message": "fact_view.as_of_date must equal profile.as_of_date",
        })
    expected_mode = "as_of" if profile.get("as_of_date") else "all_known_facts"
    if fact_view.get("view_mode") != expected_mode:
        issues.append({
            "code": "fact_view_mode_mismatch",
            "field_path": "fact_view.view_mode",
            "message": f"fact_view.view_mode must be {expected_mode}",
        })

    for conflict in profile.get("conflicts", []) if isinstance(profile.get("conflicts"), list) else []:
        if not isinstance(conflict, dict):
            continue
        for fact_id in conflict.get("fact_ids", []) if isinstance(conflict.get("fact_ids"), list) else []:
            if fact_id not in fact_ids:
                issues.append({
                    "code": "conflict_fact_reference_unresolved",
                    "fact_id": fact_id,
                    "field_path": "conflicts.fact_ids",
                    "message": "conflict references a fact that does not exist",
                })
    return issues


def assert_valid_fact_profile(profile: Any) -> None:
    issues = validate_fact_profile(profile)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
