"""Stable identifiers and hashes for enterprise profile question plans."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from ..profile_gap.request import requirement_sort_key
from .question_item import question_item_content_hash, question_item_id
from .request import QUESTION_PLAN_SCHEMA_VERSION


def gap_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (*requirement_sort_key(item), str(item.get("gap_id") or ""))


def question_item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (*requirement_sort_key(item), str(item.get("gap_id") or ""), str(item.get("question_item_id") or ""))


def question_plan_content_payload(plan: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "question_plan_schema_version", "enterprise", "source_dependencies", "planning_options",
        "plan_status", "question_items", "deferred_required_gaps", "excluded_optional_gaps",
        "plan_summary", "warnings",
    )
    result = {key: deepcopy(plan.get(key)) for key in keys}
    result["question_items"] = sorted(result.get("question_items") or [], key=question_item_sort_key)
    result["deferred_required_gaps"] = sorted(result.get("deferred_required_gaps") or [], key=gap_sort_key)
    result["excluded_optional_gaps"] = sorted(result.get("excluded_optional_gaps") or [], key=gap_sort_key)
    return result


def question_plan_content_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(question_plan_content_payload(plan))).hexdigest()


def question_plan_id(plan: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or question_plan_content_hash(plan)
    return (
        "enterprise-profile-question-plan:"
        f"{stable_enterprise_id(plan.get('enterprise') or {})}:{content_digest[:16]}"
    )


def finalize_question_plan(plan: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(plan)
    result["question_plan_schema_version"] = QUESTION_PLAN_SCHEMA_VERSION
    result["question_items"] = sorted(result.get("question_items") or [], key=question_item_sort_key)
    result["deferred_required_gaps"] = sorted(result.get("deferred_required_gaps") or [], key=gap_sort_key)
    result["excluded_optional_gaps"] = sorted(result.get("excluded_optional_gaps") or [], key=gap_sort_key)
    digest = question_plan_content_hash(result)
    result["question_plan_content_hash"] = digest
    result["question_plan_id"] = question_plan_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result


def rehash_question_items(
    items: list[dict[str, Any]],
    *,
    enterprise: dict[str, Any],
    question_plan_request_id_value: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in items:
        item = deepcopy(raw)
        item.pop("question_item_id", None)
        item.pop("question_item_content_hash", None)
        digest = question_item_content_hash(item)
        item["question_item_content_hash"] = digest
        item["question_item_id"] = question_item_id(
            item,
            enterprise=enterprise,
            question_plan_request_id=question_plan_request_id_value,
            digest=digest,
        )
        result.append(item)
    return result
