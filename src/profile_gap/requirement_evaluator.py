"""Pure deterministic evaluation rules for one task-scoped profile requirement.

This module is intentionally shared by both the inventory generator and validator so
that externally supplied inventories are checked against the exact same business
rules used during generation.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..errors import InputDataError

_VERIFIED = {"verified", "partially_verified"}
_AMBIGUOUS_VERIFICATION = {"unverified", "pending_review"}
_UNUSABLE_STATUSES = {"rejected", "conflicted", "ambiguous"}


def empty_references() -> dict[str, Any]:
    return {"fact_ids": [], "capability_ids": [], "decision_profile_id": None, "evidence_ids": []}


def fact_references(facts: list[dict[str, Any]], *, expose: bool = True) -> dict[str, Any]:
    if not expose:
        return empty_references()
    return {
        "fact_ids": sorted({str(fact.get("fact_id")) for fact in facts if fact.get("fact_id")}),
        "capability_ids": [],
        "decision_profile_id": None,
        "evidence_ids": sorted({str(eid) for fact in facts for eid in fact.get("evidence_ids", []) if eid}),
    }


def capability_references(domain: dict[str, Any], *, formal_only: bool) -> dict[str, Any]:
    items = list(domain.get("capability_claims") or [])
    if not formal_only:
        items += list(domain.get("reviewed_semantic_observations") or [])
    return {
        "fact_ids": sorted({str(fid) for item in items for fid in item.get("source_fact_ids", []) if fid}),
        "capability_ids": sorted({
            str(item.get("capability_id") or item.get("semantic_observation_id"))
            for item in items
            if item.get("capability_id") or item.get("semantic_observation_id")
        }),
        "decision_profile_id": None,
        "evidence_ids": sorted({str(eid) for item in items for eid in item.get("evidence_ids", []) if eid}),
    }


def base_gap(
    requirement: dict[str, Any],
    *,
    status: str,
    reason_code: str,
    reason_summary: str,
    references: dict[str, Any],
    resolution_type: str,
) -> dict[str, Any]:
    return {
        "requirement_id": requirement["requirement_id"],
        "target_layer": requirement["target_layer"],
        "target_code": requirement["target_code"],
        "importance": requirement["importance"],
        "gap_status": status,
        "reason_code": reason_code,
        "reason_summary": reason_summary,
        "source_references": references,
        "resolution_type": resolution_type,
    }


def satisfied(
    requirement: dict[str, Any],
    references: dict[str, Any],
    *,
    limitations: list[str] | None = None,
    unknown_count: int = 0,
) -> dict[str, Any]:
    limitation_values = list(limitations or [])
    return {
        "requirement_id": requirement["requirement_id"],
        "target_layer": requirement["target_layer"],
        "target_code": requirement["target_code"],
        "importance": requirement["importance"],
        "satisfaction_status": "satisfied",
        "source_references": references,
        "limitation_count": len(limitation_values),
        "unknown_count": int(unknown_count),
        "limitations": limitation_values,
    }


def analyze_fact_requirement(
    requirement: dict[str, Any], fact_profile: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    target = requirement["target_code"]
    facts = [
        fact
        for fact in fact_profile.get("facts", [])
        if isinstance(fact, dict) and fact.get("fact_type") == target
    ]
    view = fact_profile.get("fact_view") or {}
    included_ids = set(view.get("included_fact_ids") or [])
    reasons = view.get("exclusion_reasons") or {}
    included = [fact for fact in facts if fact.get("fact_id") in included_ids]
    usable = [
        fact
        for fact in included
        if fact.get("verification_status") in _VERIFIED
        and fact.get("fact_status") not in _UNUSABLE_STATUSES
    ]
    if usable:
        return "satisfied", satisfied(requirement, fact_references(usable))

    conflicted = [fact for fact in included if fact.get("fact_status") == "conflicted"]
    if conflicted:
        return "gap", base_gap(
            requirement,
            status="conflicted",
            reason_code="fact_conflict_unresolved",
            reason_summary=f"当前任务需要{target}事实，但相关事实仍存在未解决冲突。",
            references=fact_references(conflicted),
            resolution_type="conflict_review_required",
        )

    ambiguous = [
        fact
        for fact in included
        if fact.get("verification_status") in _AMBIGUOUS_VERIFICATION
        or fact.get("fact_status") == "ambiguous"
    ]
    if ambiguous:
        return "gap", base_gap(
            requirement,
            status="ambiguous",
            reason_code="fact_not_verified",
            reason_summary=f"当前任务需要{target}事实，但现有相关事实尚未核验或仍待复核。",
            references=fact_references(ambiguous),
            resolution_type="verified_fact_required",
        )

    unknown = [fact for fact in facts if reasons.get(fact.get("fact_id")) == "availability_time_unknown"]
    if unknown:
        return "gap", base_gap(
            requirement,
            status="unknown_availability",
            reason_code="fact_availability_unknown",
            reason_summary=f"当前任务需要{target}事实，但相关事实的可用时间未知，不能纳入当前时间视图。",
            references=fact_references(unknown),
            resolution_type="verified_fact_required",
        )

    future = [
        fact
        for fact in facts
        if reasons.get(fact.get("fact_id")) == "future_available_relative_to_as_of_date"
    ]
    if future:
        return "gap", base_gap(
            requirement,
            status="missing",
            reason_code="fact_not_available_in_current_view",
            reason_summary=f"当前任务需要{target}事实，但现有相关记录在当前时间视图尚不可用。",
            references=fact_references(future, expose=False),
            resolution_type="supporting_material_required",
        )

    return "gap", base_gap(
        requirement,
        status="missing",
        reason_code="required_fact_missing",
        reason_summary=f"当前任务需要{target}事实，但当前事实画像中不存在可用的相关事实。",
        references=empty_references(),
        resolution_type="supporting_material_required",
    )


def analyze_capability_requirement(
    requirement: dict[str, Any], capability_profile: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    target = requirement["target_code"]
    domain = next(
        (item for item in capability_profile.get("capability_domains", []) if item.get("capability_type") == target),
        None,
    )
    if domain is None:
        raise InputDataError(f"gap_requirement_target_invalid: capability domain not found: {target}")

    gap_summary = capability_profile.get("capability_gap_summary") or {}
    evidence_gaps = [
        item for item in gap_summary.get("items", []) if item.get("capability_type") == target
    ]
    if evidence_gaps:
        selected = sorted(
            evidence_gaps,
            key=lambda item: (str(item.get("candidate_id")), str(item.get("reason_code"))),
        )[0]
        return "gap", base_gap(
            requirement,
            status="needs_more_evidence",
            reason_code=str(selected.get("reason_code") or "additional_evidence_required"),
            reason_summary=str(selected.get("reason_summary") or "该能力候选仍需要补充证据。"),
            references=capability_references(domain, formal_only=False),
            resolution_type="supporting_material_required",
        )

    status = domain.get("support_status")
    if status in {"supported", "partially_supported"}:
        limitations = list(domain.get("limitations") or [])
        return "satisfied", satisfied(
            requirement,
            capability_references(domain, formal_only=True),
            limitations=limitations,
            unknown_count=len(domain.get("unknowns") or []),
        )
    if status == "ambiguous":
        return "gap", base_gap(
            requirement,
            status="ambiguous",
            reason_code="capability_support_ambiguous",
            reason_summary=f"当前任务需要{target}信息，但现有能力支持仍为歧义状态。",
            references=capability_references(domain, formal_only=False),
            resolution_type="capability_review_required",
        )
    if status == "insufficient_data":
        return "gap", base_gap(
            requirement,
            status="insufficient_data",
            reason_code="capability_data_insufficient",
            reason_summary=f"当前任务需要{target}信息，但现有数据不足以形成能力判断。",
            references=capability_references(domain, formal_only=False),
            resolution_type="supporting_material_required",
        )
    raise InputDataError(
        f"gap_requirement_target_invalid: unsupported capability support_status: {status}"
    )


def analyze_decision_requirement(
    requirement: dict[str, Any], decision_profile: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    target = requirement["target_code"]
    if decision_profile is None:
        return "gap", base_gap(
            requirement,
            status="not_provided",
            reason_code="decision_profile_absent",
            reason_summary=f"当前任务需要用户确认{target}，但尚未提供企业决策画像。",
            references=empty_references(),
            resolution_type="user_confirmation_required",
        )
    field = next(
        (item for item in decision_profile.get("decision_fields", []) if item.get("field_code") == target),
        None,
    )
    if field is None:
        raise InputDataError(f"gap_requirement_target_invalid: decision field not found: {target}")
    references = empty_references()
    references["decision_profile_id"] = decision_profile.get("decision_profile_id")
    if field.get("confirmation_status") == "confirmed":
        return "satisfied", satisfied(requirement, references)
    return "gap", base_gap(
        requirement,
        status="not_provided",
        reason_code="decision_field_not_provided",
        reason_summary=f"当前任务需要用户确认{target}，但该决策字段尚未提供。",
        references=references,
        resolution_type="user_confirmation_required",
    )


def evaluate_requirement(
    requirement: dict[str, Any],
    *,
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return one canonical deterministic outcome without mutating any input."""
    normalized_requirement = deepcopy(requirement)
    layer = normalized_requirement["target_layer"]
    if layer == "fact":
        outcome, result = analyze_fact_requirement(normalized_requirement, fact_profile)
    elif layer == "capability":
        outcome, result = analyze_capability_requirement(normalized_requirement, capability_profile)
    elif layer == "decision":
        outcome, result = analyze_decision_requirement(normalized_requirement, decision_profile)
    else:
        raise InputDataError(f"gap_requirement_target_invalid: unsupported target layer: {layer}")
    return {"outcome": outcome, "result": result}


def evaluate_all_requirements(
    requirements: list[dict[str, Any]],
    *,
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return [
        evaluate_requirement(
            requirement,
            fact_profile=fact_profile,
            capability_profile=capability_profile,
            decision_profile=decision_profile,
        )
        for requirement in requirements
    ]
