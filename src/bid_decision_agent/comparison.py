from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from src.shared.schemas import (
    CompetitiveIntensity,
    EligibilityStatus,
    InterpretedUserGoal,
    ProjectComparisonResult,
    ScoreDimensionDetail,
    ScoreItemDetail,
)

SCORE_RULE_VERSION = "TEMP-BID-SCORE-V2"


def _round(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_missing_text(value: Any) -> bool:
    text = _clean(value)
    return not text or "待补充" in text or text in {"未知", "unknown", "none", "null"}


def _matches(value: str, candidates: Iterable[str]) -> bool:
    target = _clean(value)
    if not target:
        return False
    for candidate in candidates:
        term = _clean(candidate)
        if term and (term in target or target in term):
            return True
    return False


def _item(
    *,
    name: str,
    score: float,
    max_score: float,
    status: str,
    rule: str,
    reason: str,
    data_sources: list[str],
    missing_data: list[str] | None = None,
) -> ScoreItemDetail:
    return ScoreItemDetail(
        name=name,
        score=round(score, 2),
        max_score=max_score,
        status=status,
        rule=rule,
        reason=reason,
        data_sources=data_sources,
        missing_data=missing_data or [],
    )


def _dimension(name: str, max_score: float, items: list[ScoreItemDetail]) -> ScoreDimensionDetail:
    return ScoreDimensionDetail(
        name=name,
        score=round(sum(item.score for item in items), 2),
        max_score=max_score,
        items=items,
    )




def _iter_fact_fields(fact_profile: dict[str, Any]):
    for fact in fact_profile.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        payload = fact.get("payload") or {}
        if fact.get("fact_type") == "business_registration":
            yield "business_registration", payload
            continue
        details = payload.get("details") if isinstance(payload, dict) else None
        if isinstance(details, dict):
            fields = details.get("fields")
            if isinstance(fields, dict):
                yield str(details.get("indicator_code") or "other"), fields


def _split_business_scope(value: Any) -> list[str]:
    text = str(value or "").replace("\n", "；")
    for prefix in ("许可项目：", "一般项目："):
        text = text.replace(prefix, "")
    values = []
    for item in text.replace("。", "；").split("；"):
        item = item.strip(" ，、。")
        if len(item) >= 4 and "依法须经批准" not in item and "凭营业执照" not in item:
            values.append(item)
    return values


def _fact_context(fact_profile: dict[str, Any]) -> dict[str, Any]:
    industries: list[str] = []
    technical_terms: list[str] = []
    regions: list[str] = []
    personnel_total: int | None = None
    for code, fields in _iter_fact_fields(fact_profile):
        if code == "business_registration":
            industry = fields.get("registered_industry")
            if industry:
                industries.append(str(industry))
            technical_terms.extend(_split_business_scope(fields.get("business_scope")))
            continue
        for key in ("所属行业", "行业分类"):
            value = fields.get(key)
            if value:
                industries.extend(value if isinstance(value, list) else [str(value)])
        scope = fields.get("经营范围")
        if scope:
            technical_terms.extend(_split_business_scope(scope))
        for key in ("专利名称", "作品名称", "标准名称", "认证项目", "标签名称"):
            value = fields.get(key)
            if value:
                technical_terms.extend(value if isinstance(value, list) else [str(value)])
        addresses = fields.get("注册地址")
        if isinstance(addresses, list):
            for row in addresses:
                if isinstance(row, dict) and row.get("Value"):
                    regions.append(str(row["Value"]))
                elif isinstance(row, str):
                    regions.append(row)
        for key in ("参保人数", "人员规模"):
            value = fields.get(key)
            try:
                if value is not None:
                    personnel_total = max(personnel_total or 0, int(value))
            except (TypeError, ValueError):
                pass
    def unique(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result
    return {
        "industries": unique(industries),
        "technical_terms": unique(technical_terms),
        "regions": unique(regions),
        "personnel_total": personnel_total,
    }


def _rejected_critical_project(state, project_id: str) -> bool:
    confirmation = state.get("unknown_resolution_confirmation")
    return bool(
        confirmation
        and confirmation.action == "REJECT"
        and project_id in state.get("critical_unknown_project_ids", [])
    )


def resolve_available_bid_team_slots(state) -> int:
    """Use one authoritative team-slot precedence across scoring and validation."""
    request = state["request"]
    constraints = request.resource_constraints
    if "available_bid_team_slots" in constraints:
        value = constraints["available_bid_team_slots"]
    else:
        interpreted_goal = state.get("interpreted_user_goal")
        if interpreted_goal is not None:
            value = interpreted_goal.available_bid_team_slots
        else:
            value = state["company_profile"].decision_profile.available_bid_team_slots
    return max(0, int(value))


def validate_portfolio_team_slots(state) -> None:
    selected = [item for item in state["project_comparisons"] if item.selected_for_portfolio]
    available_slots = resolve_available_bid_team_slots(state)
    if sum(item.team_slots_required for item in selected) > available_slots:
        raise ValueError("portfolio exceeds available bid team slots")


def _eligibility_dimension(status: EligibilityStatus) -> ScoreDimensionDetail:
    score = {
        EligibilityStatus.PASS: 40.0,
        EligibilityStatus.UNKNOWN: 20.0,
        EligibilityStatus.FAIL: 0.0,
    }[status]
    reason = {
        EligibilityStatus.PASS: "所有已结构化强制资格均有充分证据满足。",
        EligibilityStatus.UNKNOWN: "仍有资格要求缺少证据或尚未完成逐项核验。",
        EligibilityStatus.FAIL: "存在明确强制资格不满足，其他分项不能抵消。",
    }[status]
    return _dimension(
        "资格可投性",
        40.0,
        [
            _item(
                name="资格三态",
                score=score,
                max_score=40.0,
                status=status.value,
                rule="PASS=40分；UNKNOWN=20分；FAIL=0分且固定NO_GO。",
                reason=reason,
                data_sources=["项目资格要求", "企业证明材料", "资格核验结果"],
                missing_data=["未核验资格证据"] if status == EligibilityStatus.UNKNOWN else [],
            )
        ],
    )


def _industry_item(project, capability, fact_context: dict[str, Any]) -> ScoreItemDetail:
    confirmed = list(capability.industry_capability)
    background = list(fact_context.get("industries") or [])
    technical_terms = list(fact_context.get("technical_terms") or [])
    if _is_missing_text(project.industry):
        return _item(
            name="行业匹配", score=7.5, max_score=15, status="INSUFFICIENT_DATA",
            rule="明确匹配15分；经营范围相关但缺少交付证据7.5分；数据不足7.5分；明确不匹配0分。",
            reason="项目行业字段缺失，暂时无法完成行业匹配。",
            data_sources=["项目行业"], missing_data=["项目行业"],
        )
    if confirmed and _matches(project.industry, confirmed):
        return _item(
            name="行业匹配", score=15.0, max_score=15, status="MATCH",
            rule="明确匹配15分；经营范围相关但缺少交付证据7.5分；数据不足7.5分；明确不匹配0分。",
            reason=f"项目行业“{project.industry}”与企业已确认行业能力匹配。",
            data_sources=["项目行业", "企业已确认行业能力"],
        )
    if _matches(project.industry, technical_terms) or _matches(project.industry, background):
        return _item(
            name="行业匹配", score=7.5, max_score=15, status="PARTIAL",
            rule="明确匹配15分；经营范围相关但缺少交付证据7.5分；数据不足7.5分；明确不匹配0分。",
            reason=f"企业工商行业或经营范围与“{project.industry}”相关，但缺少已核验交付业绩，因此按相关但待核验处理。",
            data_sources=["项目行业", "企业工商行业", "企业经营范围"],
            missing_data=["对应行业的已核验交付业绩"],
        )
    if not confirmed and not background and not technical_terms:
        return _item(
            name="行业匹配", score=7.5, max_score=15, status="INSUFFICIENT_DATA",
            rule="明确匹配15分；经营范围相关但缺少交付证据7.5分；数据不足7.5分；明确不匹配0分。",
            reason="企业行业能力及经营范围数据不足，按中性分处理。",
            data_sources=["项目行业", "企业行业能力"], missing_data=["企业行业能力或经营范围"],
        )
    return _item(
        name="行业匹配", score=0.0, max_score=15, status="NO_MATCH",
        rule="明确匹配15分；经营范围相关但缺少交付证据7.5分；数据不足7.5分；明确不匹配0分。",
        reason=f"项目行业“{project.industry}”未匹配企业已确认行业能力、工商行业或经营范围。",
        data_sources=["项目行业", "企业行业能力", "企业工商行业", "企业经营范围"],
    )


def _technical_performance_item(project, capability, fact_context: dict[str, Any]) -> ScoreItemDetail:
    structured_project_terms = [item for item in project.technical_scope if not _is_missing_text(item)]
    structured_project_terms.extend(
        str(item.get("text") or "").strip()
        for item in project.performance_requirements
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    )
    fallback_project_terms = [project.project_name, project.project_type, project.industry]
    project_terms = structured_project_terms or [item for item in fallback_project_terms if not _is_missing_text(item)]
    company_terms = [item for item in capability.technical_capability if not _is_missing_text(item)]
    company_terms.extend(item for item in capability.similar_performance_capability if not _is_missing_text(item))
    company_terms.extend(item for item in fact_context.get("technical_terms") or [] if not _is_missing_text(item))
    if not project_terms or not company_terms:
        return _item(
            name="技术与同类业绩", score=7.5, max_score=15, status="INSUFFICIENT_DATA",
            rule="明确匹配15分；部分匹配8分；数据不足7.5分；明确不匹配0分。",
            reason="项目技术信息或企业技术/经营范围信息不完整，按中性分处理。",
            data_sources=["项目技术范围或项目名称", "企业技术能力或经营范围"],
            missing_data=["项目技术要求或企业技术能力"],
        )
    matched = sum(1 for requirement in project_terms if _matches(requirement, company_terms))
    if structured_project_terms and matched == len(project_terms):
        score, status, reason = 15.0, "MATCH", "项目结构化技术与同类业绩要求均能在企业已确认能力或经营范围中找到对应。"
    elif matched > 0:
        score, status = 8.0, "PARTIAL"
        reason = f"{len(project_terms)}项项目描述中匹配{matched}项；部分依据来自经营范围或项目名称，因此按部分匹配处理。"
    else:
        score, status, reason = 0.0, "NO_MATCH", "未找到项目技术或同类业绩要求与企业已确认能力、经营范围的对应关系。"
    missing = [] if structured_project_terms else ["项目结构化技术范围"]
    return _item(
        name="技术与同类业绩", score=score, max_score=15, status=status,
        rule="明确匹配15分；部分匹配8分；数据不足7.5分；明确不匹配0分。",
        reason=reason,
        data_sources=["项目技术范围/业绩要求/项目名称", "企业技术能力/同类业绩/经营范围"],
        missing_data=missing,
    )


def _region_item(project, capability, fact_context: dict[str, Any]) -> ScoreItemDetail:
    if _is_missing_text(project.region):
        return _item(
            name="地区交付能力", score=2.5, max_score=5, status="INSUFFICIENT_DATA",
            rule="已核验交付区域匹配5分；注册地址相关2.5分；数据不足2.5分；明确不具备0分。",
            reason="项目地区字段缺失，暂时无法判断。", data_sources=["项目地区"], missing_data=["项目地区"],
        )
    if capability.regional_delivery_capability and _matches(project.region, capability.regional_delivery_capability):
        return _item(
            name="地区交付能力", score=5.0, max_score=5, status="MATCH",
            rule="已核验交付区域匹配5分；注册地址相关2.5分；数据不足2.5分；明确不具备0分。",
            reason=f"企业已确认交付区域覆盖“{project.region}”。",
            data_sources=["项目地区", "企业已核验交付区域"],
        )
    registered_regions = list(fact_context.get("regions") or [])
    if registered_regions and _matches(project.region, registered_regions):
        return _item(
            name="地区交付能力", score=2.5, max_score=5, status="PARTIAL",
            rule="已核验交付区域匹配5分；注册地址相关2.5分；数据不足2.5分；明确不具备0分。",
            reason=f"企业注册地址与“{project.region}”相关，但注册地址不能替代已核验交付业绩，因此按部分支持处理。",
            data_sources=["项目地区", "企业注册地址"], missing_data=["该地区已核验交付记录"],
        )
    if not capability.regional_delivery_capability and not registered_regions:
        return _item(
            name="地区交付能力", score=2.5, max_score=5, status="INSUFFICIENT_DATA",
            rule="已核验交付区域匹配5分；注册地址相关2.5分；数据不足2.5分；明确不具备0分。",
            reason="企业交付区域和注册地址数据不足，按中性分处理。",
            data_sources=["项目地区", "企业交付区域"], missing_data=["企业地区交付记录"],
        )
    return _item(
        name="地区交付能力", score=0.0, max_score=5, status="NO_MATCH",
        rule="已核验交付区域匹配5分；注册地址相关2.5分；数据不足2.5分；明确不具备0分。",
        reason=f"企业已确认交付区域及注册地址中未找到“{project.region}”。",
        data_sources=["项目地区", "企业交付区域", "企业注册地址"],
    )


def _amount_item(project, capability) -> ScoreItemDetail:
    project_amount = project.maximum_price or project.budget
    max_amount = capability.amount_experience_capability.get("max_amount")
    if project_amount is None or max_amount is None:
        return _item(
            name="项目规模承接能力",
            score=2.5,
            max_score=5,
            status="INSUFFICIENT_DATA",
            rule="明确可承接5分；数据不足2.5分；明确超过能力0分。",
            reason="项目金额或企业已确认最大承接金额缺失，按中性分处理。",
            data_sources=["project.budget/maximum_price", "company.amount_experience_capability.max_amount"],
            missing_data=["项目金额或企业最大承接金额"],
        )
    try:
        matched = Decimal(project_amount) <= Decimal(max_amount)
    except Exception:
        return _item(
            name="项目规模承接能力",
            score=2.5,
            max_score=5,
            status="INSUFFICIENT_DATA",
            rule="明确可承接5分；数据不足2.5分；明确超过能力0分。",
            reason="金额字段格式无法可靠比较，按中性分处理。",
            data_sources=["project.budget/maximum_price", "company.amount_experience_capability.max_amount"],
            missing_data=["可比较的金额字段"],
        )
    return _item(
        name="项目规模承接能力",
        score=5.0 if matched else 0.0,
        max_score=5,
        status="MATCH" if matched else "NO_MATCH",
        rule="明确可承接5分；数据不足2.5分；明确超过能力0分。",
        reason=(
            "项目金额未超过企业已确认最大承接金额。"
            if matched
            else "项目金额超过企业已确认最大承接金额。"
        ),
        data_sources=["project.budget/maximum_price", "company.amount_experience_capability.max_amount"],
    )


def _capability_dimension(project, capability, fact_profile: dict[str, Any]) -> ScoreDimensionDetail:
    return _dimension(
        "企业能力匹配",
        40.0,
        [
            _industry_item(project, capability, _fact_context(fact_profile)),
            _technical_performance_item(project, capability, _fact_context(fact_profile)),
            _region_item(project, capability, _fact_context(fact_profile)),
            _amount_item(project, capability),
        ],
    )


def _structured_personnel_requirements(project, request_requirements: dict[str, Any]) -> dict[str, int]:
    explicit = request_requirements.get(project.project_id, {})
    if isinstance(explicit, dict) and explicit:
        return {str(role): max(0, int(required)) for role, required in explicit.items()}
    parsed: dict[str, int] = {}
    for item in project.personnel_requirements:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or item.get("name") or item.get("title") or item.get("certificate")
        count = item.get("count") or item.get("required_count") or item.get("min_count")
        if role and count is not None:
            try:
                parsed[str(role)] = max(parsed.get(str(role), 0), int(count))
            except (TypeError, ValueError):
                continue
    return parsed


def _resource_dimension(
    *,
    slots_required: int,
    available_slots: int,
    personnel_requirements: dict[str, int],
    available_personnel: dict[str, int],
) -> tuple[ScoreDimensionDetail, list[str]]:
    team_ok = slots_required <= available_slots
    team_item = _item(
        name="投标团队槽位",
        score=10.0 if team_ok else 0.0,
        max_score=10,
        status="MATCH" if team_ok else "NO_MATCH",
        rule="槽位充足10分；槽位不足0分。",
        reason=f"项目需要{slots_required}个团队槽位，当前可用{available_slots}个。",
        data_sources=["request.available_bid_team_slots", "project_team_requirements"],
    )
    deficits: list[str] = []
    if not personnel_requirements or not available_personnel:
        personnel_item = _item(
            name="关键人员和证书资源",
            score=5.0,
            max_score=10,
            status="INSUFFICIENT_DATA",
            rule="明确满足10分；数据不足5分；明确不满足0分。",
            reason="项目人员要求或企业人员可用状态不完整，按中性分处理。",
            data_sources=["project.personnel_requirements", "company.personnel_resource_capability"],
            missing_data=["人员数量、证书或可用状态"],
        )
    else:
        for role, required in personnel_requirements.items():
            if required > int(available_personnel.get(role, 0)):
                deficits.append(f"{role}需要{required}人、可用{int(available_personnel.get(role, 0))}人")
        personnel_item = _item(
            name="关键人员和证书资源",
            score=0.0 if deficits else 10.0,
            max_score=10,
            status="NO_MATCH" if deficits else "MATCH",
            rule="明确满足10分；数据不足5分；明确不满足0分。",
            reason="；".join(deficits) if deficits else "项目所需关键人员数量在企业已确认可用资源范围内。",
            data_sources=["project.personnel_requirements", "company.personnel_resource_capability"],
        )
    return _dimension("资源可执行性", 20.0, [team_item, personnel_item]), deficits


def _project_text(project) -> str:
    chunks = [project.project_name, project.project_type, project.industry, project.region]
    chunks.extend(project.technical_scope)
    for collection in (
        project.qualification_requirements,
        project.personnel_requirements,
        project.performance_requirements,
    ):
        for item in collection:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or ""))
    return " ".join(chunks).lower()


def _preference_assessment(project, goal: InterpretedUserGoal) -> tuple[float, str, list[str], bool]:
    reasons: list[str] = []
    weighted: list[tuple[float, float]] = []
    if goal.target_industries:
        matched = _matches(project.industry, goal.target_industries)
        weighted.append((30.0 if matched else 0.0, 30.0))
        reasons.append(f"行业偏好：{'匹配' if matched else '不匹配'}（{project.industry}）")
    if goal.target_regions:
        matched = _matches(project.region, goal.target_regions)
        weighted.append((30.0 if matched else 0.0, 30.0))
        reasons.append(f"地区偏好：{'匹配' if matched else '不匹配'}（{project.region}）")
    budget = project.maximum_price or project.budget
    minimum = goal.budget_preferences.min_amount
    maximum = goal.budget_preferences.max_amount
    if minimum is not None or maximum is not None:
        if budget is None:
            weighted.append((12.5, 25.0))
            reasons.append("预算偏好：项目金额缺失，暂不能完全判断")
        else:
            in_range = (minimum is None or budget >= minimum) and (maximum is None or budget <= maximum)
            weighted.append((25.0 if in_range else 0.0, 25.0))
            reasons.append(f"预算偏好：{'符合' if in_range else '不符合'}本次范围")
    corpus = _project_text(project)
    matched_exclusions = [term for term in goal.explicit_exclusions if _clean(term) and _clean(term) in corpus]
    excluded = bool(matched_exclusions)
    if excluded:
        reasons.append("明确排除条件命中：" + "、".join(matched_exclusions))
    if "risk" in goal.specified_preferences:
        reasons.append(f"风险偏好：{goal.risk_preferences.level}（用于AI风险解释，不计入客观基础分）")
    if goal.priority_factors:
        reasons.append("优先考虑因素：" + "、".join(goal.priority_factors))
    if not weighted:
        return 0.0, "NOT_SPECIFIED", reasons or ["本次目标未给出可结构化的行业、地区或预算偏好"], excluded
    score = round(sum(value for value, _ in weighted) / sum(maximum for _, maximum in weighted) * 100, 2)
    level = "HIGH" if score >= 80 else "MEDIUM" if score >= 50 else "LOW"
    return score, level, reasons, excluded


def compare_project_portfolio(state):
    request = state["request"]
    company = state["company_profile"]
    goal = state["interpreted_user_goal"]
    candidate_map = {item.project_id: item for item in state["candidates"]}
    eligibility_map = {item.project_id: item for item in state["eligibility_results"]}
    competition_map = {item.project_id: item for item in state.get("competition_results", [])}
    win_map = {item.project_id: item for item in state.get("win_results", [])}
    critical_ids = set(state.get("critical_unknown_project_ids", []))
    unknown_action = getattr(state.get("unknown_resolution_confirmation"), "action", None)

    available_slots = resolve_available_bid_team_slots(state)
    max_concurrent_bids = max(
        0,
        int(request.resource_constraints.get("max_concurrent_bids", company.decision_profile.max_concurrent_bids)),
    )
    project_slot_requirements = request.resource_constraints.get("project_team_requirements", {})
    project_personnel_raw = request.resource_constraints.get("project_personnel_requirements", {})
    explicit_conflicts = {
        frozenset(pair)
        for pair in request.resource_constraints.get("project_resource_conflicts", [])
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    }
    available_personnel = {
        str(role): int(count)
        for role, count in company.capability_profile.personnel_resource_capability.items()
    }
    available_personnel.update(
        {str(role): int(count) for role, count in request.resource_constraints.get("available_personnel", {}).items()}
    )

    preliminary: list[dict[str, Any]] = []
    for project in state["projects"]:
        candidate = candidate_map[project.project_id]
        eligibility = eligibility_map[project.project_id]
        competition = competition_map.get(project.project_id)
        win = win_map.get(project.project_id)

        eligibility_dimension = _eligibility_dimension(eligibility.overall_status)
        capability_dimension = _capability_dimension(project, company.capability_profile, getattr(company, "fact_profile", {}) or {})
        slots_required = max(1, int(project_slot_requirements.get(project.project_id, 1)))
        personnel_requirements = _structured_personnel_requirements(project, project_personnel_raw)
        resource_dimension, personnel_deficits = _resource_dimension(
            slots_required=slots_required,
            available_slots=available_slots,
            personnel_requirements=personnel_requirements,
            available_personnel=available_personnel,
        )
        dimensions = [eligibility_dimension, capability_dimension, resource_dimension]
        composite = _round(sum(item.score for item in dimensions))
        gaps = sorted(
            {
                missing
                for dimension in dimensions
                for item in dimension.items
                for missing in item.missing_data
            }
        )
        preference_score, preference_level, preference_reasons, preference_excluded = _preference_assessment(project, goal)

        if competition is None:
            intensity = CompetitiveIntensity.UNKNOWN
            coverage = 0.0
            competition_demo = False
        else:
            intensity = competition.competitive_intensity
            coverage = competition.data_coverage
            competition_demo = bool(getattr(competition, "data_is_demo", False))
        opportunity_level = win.opportunity_level if win else "UNKNOWN"

        reasons = [
            f"资格可投性 {eligibility_dimension.score:.1f}/40",
            f"企业能力匹配 {capability_dimension.score:.1f}/40",
            f"资源可执行性 {resource_dimension.score:.1f}/20",
            f"本次偏好匹配：{preference_level}（{preference_score:.1f}/100，不计入客观基础分）",
        ]
        if personnel_deficits:
            reasons.append("人员资源不足：" + "；".join(personnel_deficits))
        if competition_demo:
            reasons.append("竞争对手为演示数据，仅展示，不参与客观基础分")
        if opportunity_level != "UNKNOWN":
            reasons.append(f"中标机会等级{opportunity_level}仅展示，不参与客观基础分")

        can_select = eligibility.overall_status == EligibilityStatus.PASS
        if eligibility.overall_status == EligibilityStatus.UNKNOWN:
            can_select = project.project_id not in critical_ids or unknown_action == "ACCEPT_CONDITIONS"
        if _rejected_critical_project(state, project.project_id) or preference_excluded:
            can_select = False

        preliminary.append(
            {
                "project": project,
                "candidate": candidate,
                "eligibility": eligibility,
                "coverage": coverage,
                "competitive_intensity": intensity,
                "win_opportunity_level": opportunity_level,
                "dimensions": dimensions,
                "scores": {
                    "eligibility": eligibility_dimension.score,
                    "capability": capability_dimension.score,
                    "resource": resource_dimension.score,
                    "composite": composite,
                },
                "slots_required": slots_required,
                "personnel_requirements": personnel_requirements,
                "can_select": can_select,
                "reasons": reasons,
                "gaps": gaps,
                "preference_score": preference_score,
                "preference_level": preference_level,
                "preference_reasons": preference_reasons,
                "preference_excluded": preference_excluded,
            }
        )

    # Current-task preferences affect final ordering and portfolio selection, but never alter the objective 100-point score.
    ranked = sorted(
        preliminary,
        key=lambda row: (
            row["preference_excluded"],
            -row["preference_score"],
            -row["scores"]["composite"],
            row["candidate"].rank,
        ),
    )
    selected_ids: list[str] = []
    selected_slots = 0
    allocated_personnel: dict[str, int] = {}
    conflicts_by_project: dict[str, list[str]] = {}
    resource_conflicts: list[str] = []
    portfolio_rank_by_id: dict[str, int] = {}

    for row in ranked:
        project_id = row["project"].project_id
        if not row["can_select"]:
            if row["preference_excluded"]:
                resource_conflicts.append(f"{project_id}未进入组合：命中本次目标中的明确排除条件")
            continue
        explicit_with_selected = [other for other in selected_ids if frozenset((project_id, other)) in explicit_conflicts]
        if explicit_with_selected:
            conflicts_by_project[project_id] = explicit_with_selected
            resource_conflicts.append(f"{project_id}与已选项目{','.join(explicit_with_selected)}存在人员或资源冲突")
            continue
        if len(selected_ids) >= max_concurrent_bids:
            conflicts_by_project[project_id] = list(selected_ids)
            resource_conflicts.append(f"{project_id}未进入组合：已达到同时投标项目上限{max_concurrent_bids}个")
            continue
        if selected_slots + row["slots_required"] > available_slots:
            conflicts_by_project[project_id] = list(selected_ids)
            resource_conflicts.append(
                f"{project_id}未进入组合：需要{row['slots_required']}个团队槽位，剩余{max(0, available_slots-selected_slots)}个"
            )
            continue
        personnel_conflicts: list[str] = []
        for role, required in row["personnel_requirements"].items():
            remaining = int(available_personnel.get(role, 0)) - allocated_personnel.get(role, 0)
            if required > remaining:
                personnel_conflicts.append(f"{role}需要{required}人、剩余{max(0, remaining)}人")
        if personnel_conflicts:
            conflicts_by_project[project_id] = list(selected_ids)
            resource_conflicts.append(f"{project_id}未进入组合：人员资源冲突（{'；'.join(personnel_conflicts)}）")
            continue
        selected_ids.append(project_id)
        selected_slots += row["slots_required"]
        for role, required in row["personnel_requirements"].items():
            allocated_personnel[role] = allocated_personnel.get(role, 0) + required
        portfolio_rank_by_id[project_id] = len(selected_ids)

    comparisons: list[ProjectComparisonResult] = []
    for row in preliminary:
        project = row["project"]
        candidate = row["candidate"]
        project_id = project.project_id
        scores = row["scores"]
        comparisons.append(
            ProjectComparisonResult(
                project_id=project_id,
                project_version=project.project_version,
                eligibility=row["eligibility"].overall_status,
                original_rank=candidate.rank,
                original_rank_score=candidate.rank_score,
                recommendation_score_breakdown=candidate.score_breakdown,
                capability_match_score=round(scores["capability"] / 40.0 * 100.0, 2),
                competitive_intensity=row["competitive_intensity"],
                competition_score=0.0,
                competition_data_coverage=row["coverage"],
                win_opportunity_level=row["win_opportunity_level"],
                win_opportunity_score=0.0,
                remaining_preparation_days=0.0,
                preparation_time_score=0.0,
                contract_risk_count=len(project.contract_risks),
                contract_risk_score=0.0,
                strategic_alignment_score=row["preference_score"],
                resource_fit_score=round(scores["resource"] / 20.0 * 100.0, 2),
                team_slots_required=row["slots_required"],
                composite_score=scores["composite"],
                portfolio_rank=portfolio_rank_by_id.get(project_id),
                selected_for_portfolio=project_id in selected_ids,
                conflict_project_ids=conflicts_by_project.get(project_id, []),
                reasons=row["reasons"],
                score_rule_version=SCORE_RULE_VERSION,
                score_is_temporary=True,
                temporary_score_breakdown={
                    "资格可投性": scores["eligibility"],
                    "企业能力匹配": scores["capability"],
                    "资源可执行性": scores["resource"],
                },
                score_details=row["dimensions"],
                preference_match_score=row["preference_score"],
                preference_match_level=row["preference_level"],
                preference_reasons=row["preference_reasons"],
                preference_excluded=row["preference_excluded"],
                data_gaps=row["gaps"],
            )
        )

    comparisons.sort(
        key=lambda item: (
            item.preference_excluded,
            -item.preference_match_score,
            -item.composite_score,
            item.original_rank,
        )
    )
    return {
        "project_comparisons": comparisons,
        "portfolio_project_ids": selected_ids,
        "resource_conflicts": resource_conflicts,
        "current_node": "compare_projects",
    }
