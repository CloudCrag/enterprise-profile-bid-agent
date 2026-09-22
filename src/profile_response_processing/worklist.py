"""Stable identities and local summaries for response-processing worklists."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from .processing_item import processing_item_sort_key

RESPONSE_PROCESSING_WORKLIST_SCHEMA_VERSION = "enterprise-profile-response-processing-worklist/1.0.0"


def response_processing_worklist_content_payload(worklist: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_processing_worklist_schema_version": worklist.get("response_processing_worklist_schema_version"),
        "enterprise": deepcopy(worklist.get("enterprise")),
        "source_dependencies": deepcopy(worklist.get("source_dependencies")),
        "processing_items": sorted(deepcopy(worklist.get("processing_items") or []), key=processing_item_sort_key),
        "processing_summary": deepcopy(worklist.get("processing_summary")),
        "warnings": deepcopy(worklist.get("warnings") or []),
    }


def response_processing_worklist_content_hash(worklist: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(response_processing_worklist_content_payload(worklist))).hexdigest()


def response_processing_worklist_id(worklist: dict[str, Any], digest: str | None = None) -> str:
    value = digest or response_processing_worklist_content_hash(worklist)
    return f"enterprise-profile-response-processing-worklist:{stable_enterprise_id(worklist.get('enterprise') or {})}:{value[:16]}"


def processing_summary(items: list[dict[str, Any]], *, recorded_response_item_count: int) -> dict[str, Any]:
    statuses = [item.get("processing_status") for item in items]
    material_ids = {
        ref.get("material_id")
        for item in items
        for ref in (item.get("material_references") or [])
        if isinstance(ref, dict)
    }
    pending = sum(status in {"evidence_processing_required", "conflict_review_required", "capability_review_required"} for status in statuses)
    return {
        "recorded_response_item_count": recorded_response_item_count,
        "processing_item_count": len(items),
        "decision_update_candidate_ready_count": statuses.count("decision_update_candidate_ready"),
        "evidence_processing_required_count": statuses.count("evidence_processing_required"),
        "conflict_review_required_count": statuses.count("conflict_review_required"),
        "capability_review_required_count": statuses.count("capability_review_required"),
        "unavailable_recorded_count": statuses.count("unavailable_recorded"),
        "response_with_material_count": sum(1 for item in items if item.get("material_references")),
        "unique_material_count": len(material_ids),
        "decision_update_candidate_count": sum(1 for item in items if item.get("decision_update_candidate") is not None),
        "pending_processing_count": pending,
        "has_pending_processing": pending > 0,
    }


def finalize_response_processing_worklist(worklist: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(worklist)
    result["processing_items"] = sorted(result.get("processing_items") or [], key=processing_item_sort_key)
    digest = response_processing_worklist_content_hash(result)
    result["response_processing_worklist_content_hash"] = digest
    result["response_processing_worklist_id"] = response_processing_worklist_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
