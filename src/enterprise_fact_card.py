"""Extract a compact enterprise fact card from a fact-and-tag snapshot."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import InputDataError
from .profile_snapshot import SNAPSHOT_SCHEMA_VERSION

FACT_CARD_SCHEMA_VERSION = "enterprise-fact-card/1.1.0"


def _tags_by_code(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["tag_code"]: item for item in snapshot["tags"] if isinstance(item, dict) and item.get("tag_code")}


def _unknowns(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    tags = snapshot.get("tags", [])
    groups: list[dict[str, Any]] = []
    for status, code, reason in [
        ("missing_data", "tag_data_missing", "部分正式标签缺少直接企业事实；缺失不代表负面结论。"),
        ("partial", "tag_data_partial", "部分正式标签只有局部事实，不能形成完整标签值。"),
        ("ambiguous", "tag_data_ambiguous", "部分正式标签存在数据或冲突，但语义尚不能可靠确定。"),
    ]:
        related = [tag["tag_code"] for tag in tags if tag.get("data_status") == status]
        if related:
            groups.append({"code": code, "related_tags": related, "reason": reason})
    if snapshot.get("as_of_date") is None:
        groups.append({
            "code": "as_of_date_missing",
            "related_tags": ["A1.2", "B1.1", "C3.4"],
            "reason": "未提供画像业务基准日期，近期窗口和依赖基准日的事实不能计算。",
        })
    if snapshot.get("conflicts"):
        groups.append({
            "code": "unresolved_fact_conflicts",
            "related_tags": ["B2.1", "C3.4"],
            "reason": "多来源事实存在未解决冲突，系统未自动选择来源。",
            "conflict_ids": [item.get("conflict_id") for item in snapshot["conflicts"]],
        })
    view = snapshot.get("active_fact_view_summary") if isinstance(snapshot.get("active_fact_view_summary"), dict) else {}
    unknown_availability = int(view.get("unknown_availability_fact_count") or 0)
    conflicting_availability = int(view.get("conflicting_availability_fact_count") or 0)
    if unknown_availability or conflicting_availability:
        groups.append({
            "code": "fact_availability_excluded",
            "related_tags": [],
            "reason": "部分事实因首次可用时间未知或冲突，未进入当前时间冻结视图；这不表示事实不存在。",
            "unknown_availability_fact_count": unknown_availability,
            "conflicting_availability_fact_count": conflicting_availability,
        })
    fact_quality = (snapshot.get("data_quality_summary") or {}).get("fact_profile", {})
    if fact_quality.get("facts_without_collection_time_count"):
        groups.append({"code": "collection_time_missing", "related_tags": [], "reason": "部分事实未提供真实采集时间。"})
    market = next((tag for tag in tags if tag.get("tag_code") == "B1.1"), {})
    supporting = market.get("supporting_facts") if isinstance(market.get("supporting_facts"), dict) else {}
    if supporting.get("buyer_count_status") == "pending_split_definition":
        groups.append({"code": "buyer_agency_split_pending", "related_tags": ["B1.1", "B1.3"], "reason": "采购单位与代理机构尚未可靠拆分。"})
    if supporting.get("business_unique_deduplication_applied") is False:
        groups.append({"code": "business_unique_deduplication_pending", "related_tags": ["B1.1", "B1.2"], "reason": "当前仅排除完全重复记录。"})
    return groups


def _known_risks(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fact in facts:
        if fact.get("fact_type") != "risk_penalty_credit" or fact.get("fact_status") in {"conflicted", "ambiguous", "superseded"}:
            continue
        payload = fact.get("payload") or {}
        raw = payload.get("status")
        if raw in {"none", "no_record", "none_confirmed"}:
            status = "none_confirmed"
        elif raw in {"historical", "historical_removed", "historical_resolved"}:
            status = "historical"
        elif raw is None:
            status = "unknown"
        else:
            status = "current"
        temporal = fact.get("temporal") or {}
        result.append({
            "risk_type": payload.get("category"),
            "status": status,
            "severity": payload.get("severity_raw"),
            "fact_id": fact.get("fact_id"),
            "evidence_ids": deepcopy(fact.get("evidence_ids", [])),
            "source_types": [((fact.get("source") or {}).get("source_type"))] if (fact.get("source") or {}).get("source_type") else [],
            "updated_at": temporal.get("verified_at") or temporal.get("collected_at") or temporal.get("observed_at"),
        })
    return result


def build_enterprise_fact_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise InputDataError("Input must be a company fact-and-tag snapshot")
    if not isinstance(snapshot.get("facts"), list) or not isinstance(snapshot.get("tags"), list) or len(snapshot["tags"]) != 60:
        raise InputDataError("Snapshot must contain facts and exactly 60 tags")
    evidence_index = snapshot.get("evidence_index")
    if not isinstance(evidence_index, dict):
        raise InputDataError("Snapshot evidence_index must be an object")
    by_code = _tags_by_code(snapshot)
    active_qualifications = [
        {
            "fact_id": fact.get("fact_id"),
            "name": (fact.get("payload") or {}).get("name"),
            "qualification_level": (fact.get("payload") or {}).get("qualification_level"),
            "valid_until": (fact.get("payload") or {}).get("valid_until"),
            "evidence_ids": deepcopy(fact.get("evidence_ids", [])),
        }
        for fact in snapshot["facts"]
        if fact.get("fact_type") == "qualification" and fact.get("fact_status") == "active"
    ]
    market = by_code.get("B1.1", {})
    fact_refs = {
        fact["fact_id"]: deepcopy(fact.get("evidence_ids", []))
        for fact in snapshot["facts"] if fact.get("evidence_ids")
    }
    tag_refs = {
        tag["tag_code"]: deepcopy(tag.get("evidence_ids", []))
        for tag in snapshot["tags"] if tag.get("evidence_ids")
    }
    all_refs = list(dict.fromkeys(eid for values in [*fact_refs.values(), *tag_refs.values()] for eid in values))
    missing = [eid for eid in all_refs if eid not in evidence_index]
    return {
        "fact_card_schema_version": FACT_CARD_SCHEMA_VERSION,
        "profile_snapshot_id": snapshot["snapshot_id"],
        "profile_content_hash": snapshot["profile_content_hash"],
        "enterprise": deepcopy(snapshot["enterprise"]),
        "as_of_date": snapshot.get("as_of_date"),
        "as_of_date_status": snapshot.get("as_of_date_status"),
        "fact_store_summary": deepcopy(snapshot.get("fact_store_summary", {})),
        "fact_view_summary": deepcopy(snapshot.get("active_fact_view_summary", {})),
        "fact_summary": deepcopy(snapshot.get("fact_summary", {})),
        "tag_summary": {
            **deepcopy(snapshot.get("tag_coverage_summary", {})),
            "core_tag_count": (snapshot.get("tag_scope") or {}).get("core_tag_count"),
            "non_core_tag_count": (snapshot.get("tag_scope") or {}).get("non_core_tag_count"),
        },
        "business_registration_facts": [
            deepcopy(fact) for fact in snapshot["facts"]
            if fact.get("fact_type") == "business_registration" and fact.get("fact_status") not in {"superseded", "rejected"}
        ],
        "current_qualification_facts": active_qualifications,
        "market_activity": {
            **deepcopy(market.get("tag_value") or {}),
            "historical_project_industries": deepcopy((market.get("supporting_facts") or {}).get("historical_project_industries", [])),
            "historical_project_regions": deepcopy((market.get("supporting_facts") or {}).get("historical_project_regions", [])),
            "procurement_methods": deepcopy((market.get("supporting_facts") or {}).get("procurement_methods", [])),
            "deduplication_basis": (market.get("supporting_facts") or {}).get("deduplication_basis"),
            "business_unique_deduplication_applied": (market.get("supporting_facts") or {}).get("business_unique_deduplication_applied"),
            "fact_ids": deepcopy((market.get("supporting_facts") or {}).get("fact_ids", [])),
            "evidence_ids": deepcopy(market.get("evidence_ids", [])),
        },
        "known_risks": _known_risks(snapshot["facts"]),
        "unknowns": _unknowns(snapshot),
        "conflicts": deepcopy(snapshot.get("conflicts", [])),
        "evidence_refs": {"facts": fact_refs, "tags": tag_refs},
        "evidence_source_types": sorted({
            evidence_index[eid].get("source_type")
            for eid in all_refs if eid in evidence_index and evidence_index[eid].get("source_type")
        }),
        "evidence_reference_status": {
            "all_references_resolved": not missing,
            "unresolved_reference_count": len(missing),
        },
        "data_quality": deepcopy(snapshot.get("data_quality_summary", {})),
        "warnings": deepcopy(snapshot.get("warnings", [])),
    }
