"""Deterministic, task-scoped enterprise profile gap analysis."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..capability_validator import assert_valid_capability_profile
from ..decision_profile_validator import assert_valid_decision_profile
from ..enterprise_capability_profile import (
    CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,
    CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION,
)
from ..enterprise_decision_profile import DECISION_PROFILE_SCHEMA_VERSION
from ..enterprise_fact_profile import FACT_PROFILE_SCHEMA_VERSION, fact_profile_content_hash
from ..errors import InputDataError
from ..fact_validator import assert_valid_fact_profile
from .gap_inventory import GAP_INVENTORY_SCHEMA_VERSION, finalize_gap, finalize_gap_inventory
from .request import GAP_ANALYSIS_REQUEST_SCHEMA_VERSION, normalize_requirements
from .request_validator import assert_valid_gap_analysis_request
from .requirement_evaluator import evaluate_requirement

_ALLOWED_CAPABILITY_VERSIONS = {
    CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,
    CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION,
}


def _validate_context(
    request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> None:
    assert_valid_gap_analysis_request(request)
    try:
        assert_valid_fact_profile(fact_profile)
    except InputDataError as exc:
        raise InputDataError(f"gap_fact_dependency_mismatch: {exc}") from exc
    try:
        assert_valid_capability_profile(capability_profile)
    except InputDataError as exc:
        raise InputDataError(f"gap_capability_dependency_mismatch: {exc}") from exc
    if fact_profile.get("fact_profile_schema_version") != FACT_PROFILE_SCHEMA_VERSION:
        raise InputDataError("gap_fact_dependency_mismatch: unsupported fact profile version")
    if capability_profile.get("capability_profile_schema_version") not in _ALLOWED_CAPABILITY_VERSIONS:
        raise InputDataError("gap_capability_dependency_mismatch: capability profile must be 1.1.0 or 1.2.0")
    enterprises = [request.get("enterprise"), fact_profile.get("enterprise"), capability_profile.get("enterprise")]
    if any(item != enterprises[0] for item in enterprises[1:]):
        raise InputDataError("gap_context_enterprise_mismatch: request, fact and capability enterprises differ")
    if fact_profile.get("as_of_date") != capability_profile.get("as_of_date"):
        raise InputDataError("gap_context_as_of_date_mismatch: fact and capability as_of_date differ")
    deps = capability_profile.get("source_dependencies") or {}
    actual_fact_hash = fact_profile_content_hash(fact_profile)
    if deps.get("fact_profile_schema_version") != fact_profile.get("fact_profile_schema_version") or deps.get("fact_profile_content_hash") != actual_fact_hash:
        raise InputDataError("gap_fact_dependency_mismatch: capability profile does not depend on the supplied fact profile")
    if decision_profile is not None:
        try:
            assert_valid_decision_profile(decision_profile)
        except InputDataError as exc:
            raise InputDataError(f"gap_decision_dependency_mismatch: {exc}") from exc
        if decision_profile.get("decision_profile_schema_version") != DECISION_PROFILE_SCHEMA_VERSION:
            raise InputDataError("gap_decision_dependency_mismatch: unsupported decision profile version")
        if decision_profile.get("enterprise") != fact_profile.get("enterprise"):
            raise InputDataError("gap_context_enterprise_mismatch: decision profile belongs to another enterprise")
        if decision_profile.get("as_of_date") != fact_profile.get("as_of_date"):
            raise InputDataError("gap_context_as_of_date_mismatch: decision profile as_of_date differs")
        context = decision_profile.get("confirmation_context_dependencies") or {}
        expected = {
            "fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
            "fact_profile_content_hash": actual_fact_hash,
            "capability_profile_schema_version": capability_profile.get("capability_profile_schema_version"),
            "capability_profile_id": capability_profile.get("capability_profile_id"),
            "capability_profile_content_hash": capability_profile.get("capability_content_hash"),
        }
        if any(context.get(key) != expected_value for key, expected_value in expected.items()):
            raise InputDataError("gap_decision_context_dependency_mismatch: decision profile context differs from supplied fact or capability profile")


def _source_dependencies(request: dict[str, Any], fact_profile: dict[str, Any], capability_profile: dict[str, Any], decision_profile: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
        "gap_analysis_request_id": request.get("request_id"),
        "gap_analysis_request_content_hash": request.get("request_content_hash"),
        "fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
        "fact_profile_content_hash": fact_profile_content_hash(fact_profile),
        "capability_profile_schema_version": capability_profile.get("capability_profile_schema_version"),
        "capability_profile_id": capability_profile.get("capability_profile_id"),
        "capability_profile_content_hash": capability_profile.get("capability_content_hash"),
        "decision_profile_schema_version": decision_profile.get("decision_profile_schema_version") if decision_profile else None,
        "decision_profile_id": decision_profile.get("decision_profile_id") if decision_profile else None,
        "decision_profile_content_hash": decision_profile.get("decision_profile_content_hash") if decision_profile else None,
    }


def _summary(requirements: list[dict[str, Any]], gaps: list[dict[str, Any]], satisfied: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_requirement_count": len(requirements),
        "satisfied_requirement_count": len(satisfied),
        "gap_count": len(gaps),
        "blocking_gap_count": sum(1 for item in gaps if item["importance"] == "blocking"),
        "important_gap_count": sum(1 for item in gaps if item["importance"] == "important"),
        "optional_gap_count": sum(1 for item in gaps if item["importance"] == "optional"),
        "fact_gap_count": sum(1 for item in gaps if item["target_layer"] == "fact"),
        "capability_gap_count": sum(1 for item in gaps if item["target_layer"] == "capability"),
        "decision_gap_count": sum(1 for item in gaps if item["target_layer"] == "decision"),
    }


def analyze_profile_gaps(
    request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    *,
    decision_profile: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    _validate_context(request, fact_profile, capability_profile, decision_profile)
    requirements = normalize_requirements((request.get("task_context") or {}).get("requirements") or [])
    gaps: list[dict[str, Any]] = []
    satisfied: list[dict[str, Any]] = []
    for requirement in requirements:
        evaluated = evaluate_requirement(
            requirement,
            fact_profile=fact_profile,
            capability_profile=capability_profile,
            decision_profile=decision_profile,
        )
        outcome, item = evaluated["outcome"], evaluated["result"]
        if outcome == "gap":
            gaps.append(finalize_gap(item, enterprise=fact_profile["enterprise"], gap_analysis_request_id=request["request_id"]))
        else:
            satisfied.append(item)

    inventory = {
        "gap_inventory_schema_version": GAP_INVENTORY_SCHEMA_VERSION,
        "enterprise": deepcopy(fact_profile["enterprise"]),
        "task_context": {
            "task_context_id": request["task_context"]["task_context_id"],
            "task_goal": request["task_context"]["task_goal"],
        },
        "source_dependencies": _source_dependencies(request, fact_profile, capability_profile, decision_profile),
        "gaps": gaps,
        "satisfied_requirements": satisfied,
        "gap_summary": _summary(requirements, gaps, satisfied),
        "warnings": [],
    }
    result = finalize_gap_inventory(
        inventory,
        generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat(),
    )
    from .gap_validator import assert_valid_gap_inventory
    assert_valid_gap_inventory(
        result,
        request=request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    return result
