from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def _schema_specific_contract(output_schema: type[BaseModel], user_payload: dict[str, Any]) -> dict[str, Any]:
    schema_name = output_schema.__name__
    if schema_name == "DecisionExplanation":
        project_ids = [
            item.get("project_id")
            for item in user_payload.get("project_decisions", [])
            if isinstance(item, dict) and isinstance(item.get("project_id"), str)
        ]
        example_id = project_ids[0] if project_ids else "project-id"
        return {
            "mapping_key_rule": {
                "decision_reasons": project_ids,
                "risk_explanations": project_ids,
                "recommended_actions": project_ids,
            },
            "mapping_key_instruction": "三个映射字段的键只能是上述项目ID，不得使用分类词作为键。",
            "shape_example": {
                "comparison_summary": "仅解释输入中已经确定的比较结果",
                "decision_reasons": {example_id: ["已存在的确定性原因"]},
                "risk_explanations": {example_id: ["已存在的风险或未知项"]},
                "recommended_actions": {example_id: ["合法且可执行的建议"]},
                "portfolio_explanation": "仅解释已确定的组合",
                "evidence_ids": user_payload.get("evidence_ids", [])[:2],
            },
        }
    if schema_name == "CompetitionSemanticOutput":
        return {
            "strategy_rule": "strategies只能写正向、合规、公开可执行的投标准备动作，不得输出违法竞争策略。",
            "shape_example": {
                "competitive_intensity": "MEDIUM",
                "company_advantages": ["基于输入事实的优势表述"],
                "company_weaknesses": ["基于输入事实的短板表述"],
                "strategies": ["对评分办法逐项建立证据索引"],
            },
        }
    if schema_name == "InterpretedUserGoal":
        return {
            "constraint_rule": "只解析用户目标，不得代替确定性规则输出资格、决策、概率或证据结论。",
            "shape_example": {
                "target_industries": [],
                "target_regions": [],
                "budget_preferences": {"min_amount": None, "max_amount": None},
                "available_bid_team_slots": 1,
                "risk_preferences": {"level": "MEDIUM", "avoid_conditions": []},
                "explicit_exclusions": [],
                "current_task_constraints": {},
            },
        }
    return {}


def build_messages(
    system_prompt: str,
    user_payload: dict[str, Any],
    output_schema: type[BaseModel],
) -> list[dict[str, str]]:
    user = {
        "input": user_payload,
        "required_output_json_schema": output_schema.model_json_schema(by_alias=False),
        "output_contract": _schema_specific_contract(output_schema, user_payload),
        "rules": [
            "只输出一个完整JSON对象，不输出Markdown代码块或解释性前后缀。",
            "字段名和嵌套结构必须严格匹配required_output_json_schema。",
            "不得创造输入中不存在的evidence_id。",
            "不得输出隐藏思维过程字段。",
            "若input.validation_feedback存在，必须修正问题后重新输出完整对象。",
        ],
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]
