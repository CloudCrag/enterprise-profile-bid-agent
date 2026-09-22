"""Schema and cryptographic validation for question-plan requests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from .errors import issue
from .request import (
    QUESTION_PLAN_REQUEST_SCHEMA_VERSION,
    question_plan_request_content_hash,
    question_plan_request_id,
)

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_question_plan_request.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def validate_question_plan_request(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [issue("question_plan_request_schema_invalid", "$", "Question plan request must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("question_plan_request_schema_invalid", path, error.message))
    if value.get("question_plan_request_schema_version") != QUESTION_PLAN_REQUEST_SCHEMA_VERSION:
        issues.append(issue(
            "question_plan_request_schema_invalid",
            "question_plan_request_schema_version",
            "Unsupported question plan request schema version",
        ))
    digest = question_plan_request_content_hash(value)
    if value.get("question_plan_request_content_hash") != digest:
        issues.append(issue(
            "question_plan_request_hash_mismatch",
            "question_plan_request_content_hash",
            "Question plan request content hash mismatch",
        ))
    try:
        expected_id = question_plan_request_id(value, digest)
    except InputDataError as exc:
        issues.append(issue("question_plan_request_id_mismatch", "question_plan_request_id", str(exc)))
    else:
        if value.get("question_plan_request_id") != expected_id:
            issues.append(issue(
                "question_plan_request_id_mismatch",
                "question_plan_request_id",
                "Question plan request ID mismatch",
            ))
    return issues


def assert_valid_question_plan_request(value: Any) -> None:
    issues = validate_question_plan_request(value)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
