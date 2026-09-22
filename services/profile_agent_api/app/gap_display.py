"""Read-only presentation enrichment for task-scoped profile gaps."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

TARGET_NAMES = {
    "business_registration": "工商主体",
    "qualification": "资质证书",
    "personnel": "人员",
    "personnel_certificate": "人员证书",
    "performance": "历史业绩",
    "bid_participation": "历史投标",
    "bid_award": "历史中标",
    "fulfillment": "履约记录",
    "buyer_relationship": "采购人关系事实",
    "risk_penalty_credit": "风险与信用",
    "enterprise_material": "企业材料",
    "other_enterprise_fact": "其他企业事实",
    "industry_capability": "行业能力",
    "technical_capability": "技术能力",
    "similar_performance_capability": "同类业绩能力",
    "regional_delivery_capability": "地区交付能力",
    "amount_experience_capability": "金额承接能力",
    "personnel_resource_capability": "人员与资源能力",
    "buyer_relationship_capability": "采购人关系能力",
    "tender_performance_capability": "历史投标表现",
    "strategic_industries": "战略行业",
    "strategic_regions": "战略地区",
    "budget_preference": "预算偏好",
    "procurement_method_preferences": "招标方式偏好",
    "consortium_acceptance": "联合体接受情况",
    "risk_preference": "风险偏好",
    "max_concurrent_projects": "并行项目数量",
    "personnel_resource_constraints": "人员资源约束",
    "explicit_exclusions": "明确排除项",
    "key_buyers": "重点采购人",
    "current_business_goals": "当前经营目标",
}


def enrich_gap_inventory(
    inventory: dict[str, Any],
    *,
    run: dict[str, Any] | None,
    review_tasks: list[dict[str, Any]],
    decision_context_status: dict[str, Any] | None,
) -> dict[str, Any]:
    question_by_key: dict[tuple[str, str], str] = {}
    for round_item in (run or {}).get("rounds") or []:
        for item in ((round_item.get("question_plan") or {}).get("question_items") or []):
            question_by_key[(str(item.get("target_layer")), str(item.get("target_code")))] = str(item.get("question_item_id"))
    for item in ((run or {}).get("question_plan") or {}).get("question_items") or []:
        question_by_key[(str(item.get("target_layer")), str(item.get("target_code")))] = str(item.get("question_item_id"))
    review_by_key: dict[tuple[str, str], list[str]] = {}
    for task in review_tasks:
        if task.get("status") in {"PENDING", "NEEDS_MORE_INFORMATION"}:
            key = (str(task.get("target_layer")), str(task.get("target_code")))
            review_by_key.setdefault(key, []).append(str(task.get("review_task_id")))
    details = []
    for gap in inventory.get("gaps") or []:
        key = (str(gap.get("target_layer")), str(gap.get("target_code")))
        review_ids = sorted(review_by_key.get(key, []))
        details.append({
            **deepcopy(gap),
            "target_name_zh": TARGET_NAMES.get(key[1], key[1]),
            "question_item_id": question_by_key.get(key),
            "review_task_ids": review_ids,
            "waiting_for_user": bool(question_by_key.get(key)) and not review_ids,
            "waiting_for_review": bool(review_ids),
        })
    grouped = {"fact": [], "capability": [], "decision": []}
    for item in details:
        grouped.setdefault(str(item.get("target_layer")), []).append(item)
    return {
        **deepcopy(inventory),
        "gap_details": details,
        "grouped_gap_details": grouped,
        "business_status": (
            "DECISION_CONTEXT_RECONFIRMATION_REQUIRED"
            if decision_context_status and decision_context_status.get("is_stale")
            else "NO_GAPS" if not details else "GAPS_PRESENT"
        ),
        "decision_context_status": deepcopy(decision_context_status),
    }
