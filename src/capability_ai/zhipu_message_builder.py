"""Stable, bounded message construction for the official Zhipu GLM provider."""
from __future__ import annotations

import json
from typing import Any

from .errors import CapabilityAIError
from .message_contract import build_capability_response_contract

_MAX_INPUT_BYTES = 750_000


class ZhipuGLMMessageBuilder:
    """Build the vendor-neutral business contract used by all LLM providers."""

    def build(
        self,
        *,
        request: dict[str, Any],
        prompt: dict[str, Any],
        repair_error: Any = None,
    ) -> list[dict[str, str]]:
        del prompt  # Prompt versioning is validated before this boundary.
        system = (
            "你是企业能力语义候选分析器。你只根据输入的企业事实、标签与证据工作，不得补充不存在的事实。"
            "不得生成企业评分、等级、排名、中标概率、资格结论、项目推荐或用户偏好；不得修改事实、标签或决策画像。"
            "只生成请求中指定能力领域的待审核语义候选。每个候选必须引用输入中存在的fact_id和evidence_id。"
            "candidate_kind必须从输入给出的白名单中选择；support_status只能是partially_supported或ambiguous，"
            "证据不足时使用 ambiguous。limitations、unknowns、global_unknowns中的元素必须是字符串。"
            "rejected_or_unsupported_candidates中的每一项必须包含capability_type、candidate_subject和非空reason。"
            "只输出一个合法JSON对象，不输出Markdown代码块，不输出解释性前后缀。"
            "JSON必须严格符合enterprise-capability-analysis-response/1.1.0。"
        )
        contract = build_capability_response_contract(request)
        repair_instruction = None
        if repair_error:
            if isinstance(repair_error, dict):
                repair_instruction = {
                    "previous_error": repair_error,
                    "instruction": (
                        "上一次输出未通过校验。根据previous_error和output_contract重新输出完整JSON；"
                        "不要复述错误，不要增加白名单以外字段或枚举。"
                    ),
                }
            else:
                repair_instruction = {
                    "previous_error": {"code": str(repair_error)},
                    "instruction": "重新输出完整且严格符合output_contract的JSON对象。",
                }
        user = {
            "enterprise": request["enterprise"],
            "as_of_date": request.get("as_of_date"),
            "requested_capability_types": request["requested_capability_types"],
            "allowed_facts": request["allowed_facts"],
            "allowed_tags": request["allowed_tags"],
            "evidence_index": request["evidence_index"],
            "deterministic_baseline": request["deterministic_baseline"],
            "output_contract": contract,
            # Keep the old field name with only the legacy fields required by
            # test HTTP transports; the full contract is carried once above.
            "output_schema_summary": {
                "response_schema_version": contract["response_schema_version"],
                "request_content_hash": contract["request_content_hash"],
            },
            "repair_instruction": repair_instruction,
            "input_trimming": {
                "strategy": "none",
                "critical_evidence_preserved": True,
            },
        }
        content = json.dumps(
            user, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(content.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise CapabilityAIError(
                "zhipu_input_too_large",
                "message_building",
                "Capability analysis input exceeds safe message limit",
                {"max_input_bytes": _MAX_INPUT_BYTES},
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
