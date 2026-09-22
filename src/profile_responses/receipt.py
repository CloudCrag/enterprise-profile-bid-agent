"""Stable identifiers and immutable receipt payloads for recorded response submissions."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from .response_item import response_item_sort_key
from .submission import QUESTION_RESPONSE_RECEIPT_SCHEMA_VERSION

_LAYER_ORDER = {"fact": 0, "capability": 1, "decision": 2}


def not_in_submission_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _LAYER_ORDER.get(str(item.get("target_layer")), 99),
        str(item.get("target_code") or ""),
        str(item.get("requirement_id") or ""),
        str(item.get("question_item_id") or ""),
    )


def question_response_receipt_content_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_response_receipt_schema_version": receipt.get("question_response_receipt_schema_version"),
        "enterprise": deepcopy(receipt.get("enterprise")),
        "source_dependencies": deepcopy(receipt.get("source_dependencies")),
        "submission_mode": receipt.get("submission_mode"),
        "coverage_status": receipt.get("coverage_status"),
        "submitted_by": deepcopy(receipt.get("submitted_by")),
        "submitted_at_utc": receipt.get("submitted_at_utc"),
        "recorded_response_items": sorted(deepcopy(receipt.get("recorded_response_items") or []), key=response_item_sort_key),
        "not_in_submission_question_items": sorted(deepcopy(receipt.get("not_in_submission_question_items") or []), key=not_in_submission_sort_key),
        "receipt_summary": deepcopy(receipt.get("receipt_summary")),
        "warnings": deepcopy(receipt.get("warnings") or []),
    }


def question_response_receipt_content_hash(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(question_response_receipt_content_payload(receipt))).hexdigest()


def question_response_receipt_id(receipt: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or question_response_receipt_content_hash(receipt)
    return f"enterprise-profile-question-response-receipt:{stable_enterprise_id(receipt.get('enterprise') or {})}:{content_digest[:16]}"


def finalize_question_response_receipt(receipt: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(receipt)
    result["recorded_response_items"] = sorted(result.get("recorded_response_items") or [], key=response_item_sort_key)
    result["not_in_submission_question_items"] = sorted(result.get("not_in_submission_question_items") or [], key=not_in_submission_sort_key)
    digest = question_response_receipt_content_hash(result)
    result["question_response_receipt_content_hash"] = digest
    result["question_response_receipt_id"] = question_response_receipt_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
