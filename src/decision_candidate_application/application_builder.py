"""Atomically apply explicitly selected decision candidates through the existing profile builder."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..decision_profile_builder import build_decision_profile
from .application_request_validator import assert_valid_decision_application_request
from .application_result import (
    APPLICATION_STATUS,
    application_result_source_dependencies,
    application_summary,
    finalize_application_result,
    result_decision_profile_reference,
    selected_candidate_ids,
)
from .application_request import APPLICATION_RESULT_SCHEMA_VERSION
from .candidate_selector import aggregate_decision_candidates, select_decision_candidates


def build_decision_application_result_payload(
    application_request: dict[str, Any],
    worklist: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
    aggregate_confirmation_input: dict[str, Any],
    result_decision_profile: dict[str, Any],
    *,
    base_decision_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "application_result_schema_version": APPLICATION_RESULT_SCHEMA_VERSION,
        "enterprise": deepcopy(application_request.get("enterprise")),
        "source_dependencies": application_result_source_dependencies(
            application_request, worklist, base_decision_profile
        ),
        "selected_candidate_ids": selected_candidate_ids(selected_candidates),
        "aggregate_confirmation_input": deepcopy(aggregate_confirmation_input),
        "result_decision_profile": result_decision_profile_reference(result_decision_profile),
        "application_summary": application_summary(selected_candidates),
        "application_status": APPLICATION_STATUS,
        "warnings": [],
    }


def apply_decision_candidates(
    application_request: dict[str, Any],
    worklist: dict[str, Any],
    receipt: dict[str, Any],
    submission: dict[str, Any],
    question_plan: dict[str, Any],
    question_plan_request: dict[str, Any],
    gap_inventory: dict[str, Any],
    gap_analysis_request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    *,
    base_decision_profile: dict[str, Any] | None = None,
    decision_profile_generated_at_utc: str | None = None,
    application_result_generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    context = {
        "worklist": worklist,
        "receipt": receipt,
        "submission": submission,
        "question_plan": question_plan,
        "question_plan_request": question_plan_request,
        "gap_inventory": gap_inventory,
        "gap_analysis_request": gap_analysis_request,
        "fact_profile": fact_profile,
        "capability_profile": capability_profile,
        "base_decision_profile": base_decision_profile,
    }
    assert_valid_decision_application_request(application_request, **context)
    selected = select_decision_candidates(application_request, worklist)
    aggregate = aggregate_decision_candidates(
        selected,
        enterprise=application_request["enterprise"],
        base_decision_profile=base_decision_profile,
    )
    result_profile = build_decision_profile(
        fact_profile,
        capability_profile,
        aggregate,
        base_decision_profile=base_decision_profile,
        generated_at_utc=(
            decision_profile_generated_at_utc or datetime.now(timezone.utc).isoformat()
        ),
    )
    result_payload = build_decision_application_result_payload(
        application_request,
        worklist,
        selected,
        aggregate,
        result_profile,
        base_decision_profile=base_decision_profile,
    )
    result = finalize_application_result(
        result_payload,
        generated_at_utc=(
            application_result_generated_at_utc or datetime.now(timezone.utc).isoformat()
        ),
    )
    from .application_result_validator import assert_valid_decision_application_result

    assert_valid_decision_application_result(
        result,
        application_request=application_request,
        result_decision_profile=result_profile,
        **context,
    )
    return result_profile, result
