"""Stable identities for explicit decision-candidate application requests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id

APPLICATION_REQUEST_SCHEMA_VERSION = "enterprise-decision-profile-update-application-request/1.0.0"
APPLICATION_RESULT_SCHEMA_VERSION = "enterprise-decision-profile-update-application-result/1.0.0"


def normalized_selected_candidate_ids(value: Any) -> list[Any]:
    values = deepcopy(value) if isinstance(value, list) else []
    return sorted(values, key=lambda item: str(item))


def application_request_content_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_request_schema_version": request.get("application_request_schema_version"),
        "enterprise": deepcopy(request.get("enterprise")),
        "response_processing_worklist_id": request.get("response_processing_worklist_id"),
        "response_processing_worklist_content_hash": request.get("response_processing_worklist_content_hash"),
        "selected_candidate_ids": normalized_selected_candidate_ids(request.get("selected_candidate_ids")),
        "requested_at_utc": request.get("requested_at_utc"),
    }


def application_request_content_hash(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(application_request_content_payload(request))).hexdigest()


def application_request_id(request: dict[str, Any], digest: str | None = None) -> str:
    value = digest or application_request_content_hash(request)
    return (
        "enterprise-decision-profile-update-application-request:"
        f"{stable_enterprise_id(request.get('enterprise') or {})}:{value[:16]}"
    )


def finalize_application_request(request: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(request)
    result["selected_candidate_ids"] = normalized_selected_candidate_ids(result.get("selected_candidate_ids"))
    digest = application_request_content_hash(result)
    result["application_request_content_hash"] = digest
    result["application_request_id"] = application_request_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
