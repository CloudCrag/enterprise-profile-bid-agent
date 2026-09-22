"""Deterministic candidate selection and atomic aggregation rules."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..decision_confirmation_input import assert_valid_confirmation_input
from ..enterprise_decision_profile import (
    DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
    DECISION_FIELDS,
    confirmation_input_content_hash,
    finalize_confirmation_input,
)
from ..errors import InputDataError
from ..profile_response_processing.decision_candidate import (
    DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION,
    DECISION_UPDATE_CANDIDATE_STATUS,
    DECISION_UPDATE_CANDIDATE_TYPE,
)

_FIELD_ORDER = {code: index for index, (code, _name) in enumerate(DECISION_FIELDS)}


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _FIELD_ORDER.get(str(candidate.get("target_field")), 999),
        str(candidate.get("target_field") or ""),
        str(candidate.get("candidate_id") or ""),
    )


def worklist_decision_candidate_map(worklist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in worklist.get("processing_items") or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("decision_update_candidate")
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if candidate_id in result:
            raise InputDataError(
                f"decision_application_candidate_duplicate: duplicate candidate ID {candidate_id}"
            )
        if item.get("processing_status") != "decision_update_candidate_ready":
            raise InputDataError(
                f"decision_application_candidate_not_ready: candidate {candidate_id} is not ready"
            )
        result[candidate_id] = candidate
    return result


def select_decision_candidates(
    application_request: dict[str, Any], worklist: dict[str, Any]
) -> list[dict[str, Any]]:
    candidate_map = worklist_decision_candidate_map(worklist)
    selected_ids = application_request.get("selected_candidate_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise InputDataError("decision_application_candidate_not_found: candidate selection must not be empty")
    if len(selected_ids) != len(set(selected_ids)):
        raise InputDataError("decision_application_candidate_duplicate: selected candidate IDs must be unique")
    selected: list[dict[str, Any]] = []
    for candidate_id in selected_ids:
        candidate = candidate_map.get(candidate_id)
        if not isinstance(candidate, dict):
            raise InputDataError(
                f"decision_application_candidate_not_found: candidate {candidate_id!r} is not in the current worklist"
            )
        selected.append(deepcopy(candidate))
    return sorted(selected, key=candidate_sort_key)


def _validate_candidate_shape(candidate: dict[str, Any]) -> None:
    if candidate.get("candidate_schema_version") != DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION:
        raise InputDataError("decision_application_candidate_not_ready: unsupported candidate schema version")
    if candidate.get("candidate_type") != DECISION_UPDATE_CANDIDATE_TYPE:
        raise InputDataError("decision_application_candidate_not_ready: candidate type is not decision confirmation")
    if candidate.get("candidate_status") != DECISION_UPDATE_CANDIDATE_STATUS:
        raise InputDataError("decision_application_candidate_not_ready: candidate is not ready for application")
    confirmation = candidate.get("confirmation_input")
    try:
        assert_valid_confirmation_input(confirmation)
    except InputDataError as exc:
        raise InputDataError(f"decision_application_confirmation_input_invalid: {exc}") from exc
    updates = confirmation.get("updates") if isinstance(confirmation, dict) else None
    if not isinstance(updates, list) or len(updates) != 1:
        raise InputDataError(
            "decision_application_confirmation_input_invalid: each candidate must contain exactly one update"
        )
    update = updates[0]
    if not isinstance(update, dict) or update.get("action") != "set":
        raise InputDataError(
            "decision_application_confirmation_input_invalid: candidate update action must be set"
        )
    if update.get("field_code") != candidate.get("target_field"):
        raise InputDataError(
            "decision_application_confirmation_input_invalid: update field must equal candidate target_field"
        )


def _base_pair(candidate: dict[str, Any]) -> tuple[Any, Any]:
    return (
        candidate.get("base_decision_profile_id"),
        candidate.get("base_decision_profile_content_hash"),
    )


def aggregate_decision_candidates(
    selected_candidates: list[dict[str, Any]],
    *,
    enterprise: dict[str, Any],
    base_decision_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Aggregate selected, already confirmed candidates into one existing confirmation-input protocol."""
    if not selected_candidates:
        raise InputDataError("decision_application_candidate_not_found: no candidates selected")
    candidates = sorted((deepcopy(item) for item in selected_candidates), key=candidate_sort_key)
    for candidate in candidates:
        _validate_candidate_shape(candidate)
        confirmation = candidate["confirmation_input"]
        if confirmation.get("enterprise") != enterprise:
            raise InputDataError("decision_application_enterprise_mismatch: candidate enterprise differs")
        if _base_pair(candidate) != (
            confirmation.get("base_decision_profile_id"),
            confirmation.get("base_decision_profile_content_hash"),
        ):
            raise InputDataError(
                "decision_application_base_profile_mismatch: candidate base dependency differs from confirmation input"
            )

    target_fields = [str(candidate.get("target_field")) for candidate in candidates]
    duplicates = sorted({field for field in target_fields if target_fields.count(field) > 1})
    if duplicates:
        raise InputDataError(
            f"decision_application_duplicate_target_field: duplicate target fields {duplicates}"
        )

    base_pairs = {_base_pair(candidate) for candidate in candidates}
    if len(base_pairs) != 1:
        raise InputDataError("decision_application_base_profile_mismatch: selected candidates use different base profiles")
    base_id, base_hash = next(iter(base_pairs))
    if (base_id is None) != (base_hash is None):
        raise InputDataError("decision_application_base_profile_mismatch: base ID and hash must both be null or non-null")
    if base_id is None:
        if base_decision_profile is not None:
            raise InputDataError("decision_application_base_profile_mismatch: initial candidates require no base profile")
    else:
        if not isinstance(base_decision_profile, dict):
            raise InputDataError("decision_application_validation_context_required: base_decision_profile is required")
        if (
            base_decision_profile.get("decision_profile_id") != base_id
            or base_decision_profile.get("decision_profile_content_hash") != base_hash
            or base_decision_profile.get("enterprise") != enterprise
        ):
            raise InputDataError("decision_application_base_profile_mismatch: supplied base profile does not match candidates")

    actors = [candidate["confirmation_input"].get("confirmation_actor") for candidate in candidates]
    if any(actor != actors[0] for actor in actors[1:]):
        raise InputDataError("decision_application_actor_mismatch: selected candidates have different confirmation actors")
    confirmed_times = [candidate["confirmation_input"].get("confirmed_at") for candidate in candidates]
    if any(value != confirmed_times[0] for value in confirmed_times[1:]):
        raise InputDataError(
            "decision_application_confirmation_time_mismatch: selected candidates have different confirmed_at values"
        )

    updates = [deepcopy(candidate["confirmation_input"]["updates"][0]) for candidate in candidates]
    updates.sort(key=lambda item: _FIELD_ORDER.get(str(item.get("field_code")), 999))
    aggregate = {
        "confirmation_input_schema_version": DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
        "enterprise": deepcopy(enterprise),
        "base_decision_profile_id": base_id,
        "base_decision_profile_content_hash": base_hash,
        "confirmation_actor": deepcopy(actors[0]),
        "confirmed_at": confirmed_times[0],
        "updates": updates,
    }
    # Reuse the existing finalizer and hashing logic, then restore catalog order for the serialized aggregate.
    aggregate = finalize_confirmation_input(aggregate)
    aggregate["updates"] = sorted(
        aggregate["updates"], key=lambda item: _FIELD_ORDER.get(str(item.get("field_code")), 999)
    )
    aggregate["confirmation_input_content_hash"] = confirmation_input_content_hash(aggregate)
    try:
        assert_valid_confirmation_input(aggregate)
    except InputDataError as exc:
        raise InputDataError(f"decision_application_confirmation_input_invalid: {exc}") from exc
    return aggregate
