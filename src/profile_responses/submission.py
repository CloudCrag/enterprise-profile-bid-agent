"""Stable identifiers and finalization for question-response submissions."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from .response_item import finalize_response_item, response_item_content_payload, response_item_sort_key

QUESTION_RESPONSE_SUBMISSION_SCHEMA_VERSION = "enterprise-profile-question-response-submission/1.0.0"
QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION = "enterprise-profile-question-response-receipt/1.0.0"


def normalized_response_item_payloads(items: Any) -> list[dict[str, Any]]:
    values = [response_item_content_payload(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return sorted(values, key=response_item_sort_key)


def question_response_submission_content_payload(submission: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_response_submission_schema_version": submission.get("question_response_submission_schema_version"),
        "enterprise": deepcopy(submission.get("enterprise")),
        "question_plan_id": submission.get("question_plan_id"),
        "question_plan_content_hash": submission.get("question_plan_content_hash"),
        "submission_mode": submission.get("submission_mode"),
        "submitted_by": deepcopy(submission.get("submitted_by")),
        "submitted_at_utc": submission.get("submitted_at_utc"),
        "response_items": normalized_response_item_payloads(submission.get("response_items")),
    }


def question_response_submission_content_hash(submission: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(question_response_submission_content_payload(submission))).hexdigest()


def question_response_submission_id(submission: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or question_response_submission_content_hash(submission)
    return f"enterprise-profile-question-response-submission:{stable_enterprise_id(submission.get('enterprise') or {})}:{content_digest[:16]}"


def finalize_question_response_submission(submission: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(submission)
    raw_items = [deepcopy(item) for item in result.get("response_items", []) if isinstance(item, dict)]
    # Hash and submission ID deliberately use response-item business payloads, not item IDs, avoiding a cycle.
    result["response_items"] = sorted(raw_items, key=response_item_sort_key)
    digest = question_response_submission_content_hash(result)
    submission_id = question_response_submission_id(result, digest)
    result["response_items"] = sorted(
        [finalize_response_item(item, enterprise=result.get("enterprise") or {}, submission_id=submission_id) for item in raw_items],
        key=response_item_sort_key,
    )
    result["question_response_submission_content_hash"] = digest
    result["question_response_submission_id"] = submission_id
    result["generated_at_utc"] = generated_at_utc
    return result
