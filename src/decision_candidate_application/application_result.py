"""Stable application-result identities and summaries."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id
from ..enterprise_decision_profile import DECISION_FIELDS
from .application_request import APPLICATION_RESULT_SCHEMA_VERSION
from .candidate_selector import candidate_sort_key

_FIELD_ORDER = {code: index for index, (code, _name) in enumerate(DECISION_FIELDS)}
APPLICATION_STATUS = "decision_profile_updated"


def application_result_source_dependencies(
    application_request: dict[str, Any],
    worklist: dict[str, Any],
    base_decision_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "application_request_schema_version": application_request.get("application_request_schema_version"),
        "application_request_id": application_request.get("application_request_id"),
        "application_request_content_hash": application_request.get("application_request_content_hash"),
        "response_processing_worklist_schema_version": worklist.get("response_processing_worklist_schema_version"),
        "response_processing_worklist_id": worklist.get("response_processing_worklist_id"),
        "response_processing_worklist_content_hash": worklist.get("response_processing_worklist_content_hash"),
        "base_decision_profile_schema_version": (
            base_decision_profile.get("decision_profile_schema_version") if isinstance(base_decision_profile, dict) else None
        ),
        "base_decision_profile_id": (
            base_decision_profile.get("decision_profile_id") if isinstance(base_decision_profile, dict) else None
        ),
        "base_decision_profile_content_hash": (
            base_decision_profile.get("decision_profile_content_hash") if isinstance(base_decision_profile, dict) else None
        ),
    }


def selected_candidate_ids(candidates: list[dict[str, Any]]) -> list[str]:
    return sorted(str(item.get("candidate_id")) for item in candidates)


def application_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [str(candidate.get("target_field")) for candidate in candidates]
    fields = sorted(fields, key=lambda code: _FIELD_ORDER.get(code, 999))
    return {
        "selected_candidate_count": len(candidates),
        "updated_field_count": len(fields),
        "updated_field_codes": fields,
    }


def result_decision_profile_reference(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_profile_schema_version": profile.get("decision_profile_schema_version"),
        "decision_profile_id": profile.get("decision_profile_id"),
        "decision_profile_content_hash": profile.get("decision_profile_content_hash"),
        "profile_version": profile.get("profile_version"),
    }


def application_result_content_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_result_schema_version": result.get("application_result_schema_version"),
        "enterprise": deepcopy(result.get("enterprise")),
        "source_dependencies": deepcopy(result.get("source_dependencies")),
        "selected_candidate_ids": deepcopy(result.get("selected_candidate_ids")),
        "aggregate_confirmation_input": deepcopy(result.get("aggregate_confirmation_input")),
        "result_decision_profile": deepcopy(result.get("result_decision_profile")),
        "application_summary": deepcopy(result.get("application_summary")),
        "application_status": result.get("application_status"),
        "warnings": deepcopy(result.get("warnings") or []),
    }


def application_result_content_hash(result: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(application_result_content_payload(result))).hexdigest()


def application_result_id(result: dict[str, Any], digest: str | None = None) -> str:
    value = digest or application_result_content_hash(result)
    return (
        "enterprise-decision-profile-update-application-result:"
        f"{stable_enterprise_id(result.get('enterprise') or {})}:{value[:16]}"
    )


def finalize_application_result(result: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    value = deepcopy(result)
    digest = application_result_content_hash(value)
    value["application_result_content_hash"] = digest
    value["application_result_id"] = application_result_id(value, digest)
    value["generated_at_utc"] = generated_at_utc
    return value
