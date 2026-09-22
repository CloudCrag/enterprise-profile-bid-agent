"""Schema and integrity validation for task-scoped gap analysis requests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from .errors import issue
from .request import (
    GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
    TARGET_CODE_CATALOG,
    TARGET_LAYERS,
    gap_analysis_request_content_hash,
    gap_analysis_request_id,
    normalize_requirements,
)

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_gap_analysis_request.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def validate_gap_analysis_request(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [issue("gap_analysis_request_schema_invalid", "$", "Gap analysis request must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("gap_analysis_request_schema_invalid", path, error.message))

    if value.get("gap_analysis_request_schema_version") != GAP_ANALYSIS_REQUEST_SCHEMA_VERSION:
        issues.append(issue("gap_analysis_request_schema_invalid", "gap_analysis_request_schema_version", "Unsupported request schema version"))

    task_context = value.get("task_context") if isinstance(value.get("task_context"), dict) else {}
    requirements = task_context.get("requirements") if isinstance(task_context.get("requirements"), list) else []
    requirement_ids: list[Any] = []
    target_pairs: list[tuple[Any, Any]] = []
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            continue
        requirement_id = requirement.get("requirement_id")
        layer = requirement.get("target_layer")
        code = requirement.get("target_code")
        requirement_ids.append(requirement_id)
        target_pairs.append((layer, code))
        if layer not in TARGET_LAYERS or code not in TARGET_CODE_CATALOG.get(str(layer), frozenset()):
            issues.append(issue(
                "gap_requirement_target_invalid",
                f"task_context.requirements.{index}.target_code",
                f"target_code {code!r} is not valid for target_layer {layer!r}",
            ))
    duplicate_ids = sorted({str(item) for item in requirement_ids if requirement_ids.count(item) > 1})
    if duplicate_ids:
        issues.append(issue("gap_requirement_duplicate", "task_context.requirements", f"Duplicate requirement_id values: {duplicate_ids}"))
    duplicate_pairs = sorted({pair for pair in target_pairs if target_pairs.count(pair) > 1}, key=lambda item: (str(item[0]), str(item[1])))
    if duplicate_pairs:
        issues.append(issue("gap_requirement_duplicate", "task_context.requirements", f"Duplicate target layer/code pairs: {duplicate_pairs}"))
    if requirements and requirements != normalize_requirements(requirements):
        issues.append(issue("gap_analysis_request_schema_invalid", "task_context.requirements", "Requirements must use the canonical stable order"))

    digest = gap_analysis_request_content_hash(value)
    if value.get("request_content_hash") != digest:
        issues.append(issue("gap_analysis_request_hash_mismatch", "request_content_hash", "Gap analysis request content hash mismatch"))
    try:
        expected_id = gap_analysis_request_id(value, digest)
    except InputDataError as exc:
        issues.append(issue("gap_analysis_request_id_mismatch", "request_id", str(exc)))
    else:
        if value.get("request_id") != expected_id:
            issues.append(issue("gap_analysis_request_id_mismatch", "request_id", "Gap analysis request ID mismatch"))
    return issues


def assert_valid_gap_analysis_request(value: Any) -> None:
    issues = validate_gap_analysis_request(value)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
