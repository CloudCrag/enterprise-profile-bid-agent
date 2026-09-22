"""Build the vendor-neutral, policy-backed LLM response contract."""
from __future__ import annotations

from typing import Any

from src.capability_evidence_policy import load_capability_evidence_policy


def build_capability_response_contract(request: dict[str, Any]) -> dict[str, Any]:
    policy = load_capability_evidence_policy()
    requested = list(request.get("requested_capability_types") or [])
    allowed_kinds = {
        capability_type: list(policy["domains"][capability_type]["allowed_candidate_kinds"])
        for capability_type in requested
        if capability_type in policy["domains"]
    }
    return {
        "response_schema_version": "enterprise-capability-analysis-response/1.1.0",
        "request_content_hash": request["request_content_hash"],
        "allowed_top_level_fields": [
            "response_schema_version",
            "request_content_hash",
            "capability_candidates",
            "rejected_or_unsupported_candidates",
            "global_unknowns",
        ],
        "candidate_required_fields": [
            "capability_type",
            "candidate_kind",
            "candidate_subject",
            "candidate_value",
            "inference_summary",
            "support_status",
            "source_fact_ids",
            "source_tag_codes",
            "evidence_ids",
            "limitations",
            "unknowns",
            "review_status",
        ],
        "requested_capability_types": requested,
        "allowed_candidate_kinds_by_capability_type": allowed_kinds,
        "support_status_allowed": ["partially_supported", "ambiguous"],
        "support_status_rule": "证据不足时使用 ambiguous，不得使用 insufficient_data。",
        "review_status_required": "pending_review",
        "text_array_fields": ["limitations", "unknowns", "global_unknowns"],
        "text_array_rule": "数组元素必须是字符串，不得输出对象。",
        "rejected_candidate_required_fields": [
            "capability_type",
            "candidate_subject",
            "reason",
        ],
        "rejected_candidate_rule": (
            "rejected_or_unsupported_candidates中的每一项必须同时包含"
            "capability_type、candidate_subject和非空reason。"
        ),
        "reference_rule": (
            "source_fact_ids、source_tag_codes、evidence_ids只能逐字使用输入中真实存在的ID；"
            "每个候选至少引用一个允许的fact_id和一个与该fact关联的evidence_id。"
        ),
        "candidate_kind_rule": (
            "candidate_kind必须从对应capability_type的allowed_candidate_kinds中选择，"
            "不得创造白名单之外的新枚举。"
        ),
        "response_example": {
            "response_schema_version": "enterprise-capability-analysis-response/1.1.0",
            "request_content_hash": request["request_content_hash"],
            "capability_candidates": [],
            "rejected_or_unsupported_candidates": [],
            "global_unknowns": [],
        },
    }
