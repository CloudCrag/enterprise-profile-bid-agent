"""Stable identifiers for explicit enterprise profile question-plan requests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id

QUESTION_PLAN_REQUEST_SCHEMA_VERSION = "enterprise-profile-question-plan-request/1.0.0"
QUESTION_PLAN_SCHEMA_VERSION = "enterprise-profile-question-plan/1.0.0"


def normalized_planning_options(value: Any) -> dict[str, Any]:
    options = value if isinstance(value, dict) else {}
    return {
        "max_questions_per_batch": options.get("max_questions_per_batch"),
        "include_optional": options.get("include_optional", False),
    }


def question_plan_request_content_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_plan_request_schema_version": request.get("question_plan_request_schema_version"),
        "enterprise": deepcopy(request.get("enterprise")),
        "gap_inventory_id": request.get("gap_inventory_id"),
        "gap_inventory_content_hash": request.get("gap_inventory_content_hash"),
        "planning_options": normalized_planning_options(request.get("planning_options")),
    }


def question_plan_request_content_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(question_plan_request_content_payload(request))).hexdigest()


def question_plan_request_id(request: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or question_plan_request_content_hash(request)
    return (
        "enterprise-profile-question-plan-request:"
        f"{stable_enterprise_id(request.get('enterprise') or {})}:{content_digest[:16]}"
    )


def finalize_question_plan_request(request: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(request)
    result["planning_options"] = normalized_planning_options(result.get("planning_options"))
    digest = question_plan_request_content_hash(result)
    result["question_plan_request_content_hash"] = digest
    result["question_plan_request_id"] = question_plan_request_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
