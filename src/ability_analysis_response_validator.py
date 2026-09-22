"""Validate future AI capability candidates against an approved request envelope."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .ability_analysis_request import assert_valid_analysis_request
from .capability_evidence_policy import fact_allowed_for_domain, load_capability_evidence_policy
from .enterprise_capability_profile import CAPABILITY_TYPES
from .errors import InputDataError

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_capability_analysis_response.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())

_FORBIDDEN_KEYS = {
    "strategic_industry", "strategic_industries", "strategic_region", "strategic_regions",
    "budget_preference", "procurement_method_preference", "accepts_consortium", "risk_preference",
    "recommendation", "project_recommendation", "recommended_projects", "decision", "project_priority",
    "eligibility", "qualification_result", "winning_probability", "win_probability", "competition_analysis",
    "enterprise_score", "capability_score", "total_score", "rank", "ranking", "enterprise_grade", "enterprise_level",
}
_FORBIDDEN_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("enterprise_evaluation", re.compile(r"企业综合评分|企业得分|企业排名|企业综合等级")),
    ("project_recommendation", re.compile(r"强烈推荐投标|建议参与(?:该)?项目|建议放弃(?:该)?项目|推荐投标")),
    ("winning_probability", re.compile(r"中标概率|胜率\s*[:：]?\s*\d*\s*%?")),
    ("eligibility_decision", re.compile(r"资格\s*(?:PASS|FAIL)", re.IGNORECASE)),
    ("strategic_preference", re.compile(r"战略行业|战略地区|预算偏好|用户长期偏好|重点进入.+行业")),
    ("competition_conclusion", re.compile(r"竞争分析结论|竞争强度为|主要竞争对手是")),
)


def _walk_forbidden_keys(value: Any, path: str = "$") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in _FORBIDDEN_KEYS:
                issues.append({"code": "capability_analysis_forbidden_field", "field_path": child_path, "message": f"Forbidden field: {key}"})
            issues.extend(_walk_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_walk_forbidden_keys(child, f"{path}[{index}]"))
    return issues


def _iter_candidate_text(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_candidate_text(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_candidate_text(child, f"{path}[{index}]")


def _text_issues(candidate: dict[str, Any], base: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    fields = {
        "candidate_subject": candidate.get("candidate_subject"),
        "candidate_value": candidate.get("candidate_value"),
        "inference_summary": candidate.get("inference_summary"),
        "limitations": candidate.get("limitations"),
        "unknowns": candidate.get("unknowns"),
    }
    for field, value in fields.items():
        for path, text in _iter_candidate_text(value, f"{base}.{field}"):
            for reason, pattern in _FORBIDDEN_TEXT_PATTERNS:
                if pattern.search(text):
                    issues.append({"code": "capability_analysis_forbidden_text", "field_path": path, "message": reason})
                    break
    return issues


def validate_capability_analysis_response(response: Any, request: dict[str, Any]) -> list[dict[str, Any]]:
    assert_valid_analysis_request(request)
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(response), key=lambda item: (list(item.absolute_path), item.message)):
        issues.append({
            "code": "capability_analysis_response_schema_invalid",
            "field_path": ".".join(str(item) for item in error.absolute_path) or "$",
            "message": error.message,
        })
    if not isinstance(response, dict):
        return issues
    if response.get("request_content_hash") != request.get("request_content_hash"):
        issues.append({"code": "capability_analysis_request_hash_mismatch", "field_path": "request_content_hash", "message": "Response does not belong to the supplied request"})

    policy = load_capability_evidence_policy()
    fact_map = {fact.get("fact_id"): fact for fact in request.get("allowed_facts", []) if isinstance(fact, dict) and fact.get("fact_id")}
    tag_map = {tag.get("tag_code"): tag for tag in request.get("allowed_tags", []) if isinstance(tag, dict) and tag.get("tag_code")}
    allowed_evidence = set((request.get("evidence_index") or {}).keys())
    requested_types = set(request.get("requested_capability_types", []))

    candidates = response.get("capability_candidates", []) if isinstance(response.get("capability_candidates"), list) else []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        base = f"capability_candidates.{index}"
        capability_type = candidate.get("capability_type")
        if capability_type not in CAPABILITY_TYPES or capability_type not in requested_types:
            issues.append({"code": "capability_analysis_domain_not_allowed", "field_path": f"{base}.capability_type", "message": str(capability_type)})
            issues.extend(_text_issues(candidate, base))
            continue
        domain_policy = policy["domains"][capability_type]
        candidate_kind = candidate.get("candidate_kind")
        if candidate_kind not in domain_policy["allowed_candidate_kinds"]:
            issues.append({"code": "capability_analysis_candidate_kind_not_allowed", "field_path": f"{base}.candidate_kind", "message": str(candidate_kind)})

        fact_ids = candidate.get("source_fact_ids", []) if isinstance(candidate.get("source_fact_ids"), list) else []
        tag_codes = candidate.get("source_tag_codes", []) if isinstance(candidate.get("source_tag_codes"), list) else []
        evidence_ids = candidate.get("evidence_ids", []) if isinstance(candidate.get("evidence_ids"), list) else []
        linked_fact_evidence: set[str] = set()
        linked_tag_evidence: set[str] = set()

        for fact_id in fact_ids:
            fact = fact_map.get(fact_id)
            if fact is None:
                issues.append({"code": "capability_analysis_fact_reference_not_allowed", "field_path": f"{base}.source_fact_ids", "message": str(fact_id)})
                continue
            allowed, code = fact_allowed_for_domain(fact, capability_type, policy)
            if not allowed:
                issues.append({"code": code or "capability_analysis_fact_type_not_allowed", "field_path": f"{base}.source_fact_ids", "message": str(fact_id)})
            linked_fact_evidence.update(fact.get("evidence_ids", []))

        for tag_code in tag_codes:
            tag = tag_map.get(tag_code)
            if tag is None:
                issues.append({"code": "capability_analysis_tag_reference_not_allowed", "field_path": f"{base}.source_tag_codes", "message": str(tag_code)})
                continue
            if tag_code not in domain_policy["allowed_tag_codes"]:
                issues.append({"code": "capability_analysis_tag_not_allowed_for_domain", "field_path": f"{base}.source_tag_codes", "message": str(tag_code)})
            linked_tag_evidence.update(tag.get("evidence_ids", []))

        for evidence_id in evidence_ids:
            if evidence_id not in allowed_evidence:
                issues.append({"code": "capability_analysis_evidence_reference_not_allowed", "field_path": f"{base}.evidence_ids", "message": str(evidence_id)})
        linked = linked_fact_evidence | linked_tag_evidence
        if any(evidence_id not in linked for evidence_id in evidence_ids):
            issues.append({"code": "capability_analysis_evidence_not_linked_to_source", "field_path": f"{base}.evidence_ids", "message": "Candidate evidence must belong to referenced facts or tags"})
        if not fact_ids or not evidence_ids or not (set(evidence_ids) & linked_fact_evidence):
            issues.append({"code": "capability_analysis_basis_missing", "field_path": base, "message": "Every capability candidate must reference at least one allowed fact and one evidence item linked to that fact"})
        issues.extend(_text_issues(candidate, base))

    issues.extend(_walk_forbidden_keys(response))
    return issues


def assert_valid_capability_analysis_response(response: Any, request: dict[str, Any]) -> None:
    issues = validate_capability_analysis_response(response, request)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
