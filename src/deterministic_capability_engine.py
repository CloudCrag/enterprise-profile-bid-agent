"""Deterministic, threshold-free enterprise capability baseline.

Only facts that are both lifecycle-usable and verified/partially verified may
enter formal capability claims. Unverified facts may be retained as ambiguous
observations, but are never promoted to confirmed capability conclusions.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
from statistics import median
from typing import Any, Iterable

from .enterprise_capability_profile import CAPABILITY_DOMAINS, stable_capability_item_id

_VERIFIED = {"verified", "partially_verified"}
_BLOCKED_FACT_STATUSES = {"expired", "revoked", "superseded", "conflicted", "ambiguous"}
_REJECTED_VERIFICATIONS = {"rejected"}
_VALID_UNTIL_CURRENT_STATE_FACT_TYPES = {"qualification", "personnel_certificate"}


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _included_facts(fact_profile: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = set((fact_profile.get("fact_view") or {}).get("included_fact_ids", []))
    return [fact for fact in fact_profile.get("facts", []) if fact.get("fact_id") in allowed]


def _by_type(facts: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        result.setdefault(str(fact.get("fact_type")), []).append(fact)
    return result


def _tag_map(tag_profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tag["tag_code"]: tag for tag in tag_profile.get("tags", [])}


def _refs(facts: Iterable[dict[str, Any]], tag_codes: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    fact_list = [fact for fact in facts if isinstance(fact, dict)]
    return (
        list(dict.fromkeys(fact.get("fact_id") for fact in fact_list if fact.get("fact_id"))),
        list(dict.fromkeys(str(code) for code in tag_codes if code)),
        list(dict.fromkeys(eid for fact in fact_list for eid in fact.get("evidence_ids", []) if eid)),
    )


def _is_current(fact: dict[str, Any], as_of_date: str | None) -> bool:
    if fact.get("fact_status") in _BLOCKED_FACT_STATUSES:
        return False
    payload = fact.get("payload") or {}
    if str(payload.get("status") or "").lower() in {"expired", "revoked", "invalid"}:
        return False
    # ``valid_until`` means current validity only for credential-like facts.
    # Historical event facts (bid participation, performance, fulfillment,
    # awards) remain usable as history after their event/deadline has passed.
    valid_until = payload.get("valid_until") or (fact.get("temporal") or {}).get("valid_until")
    if (
        fact.get("fact_type") in _VALID_UNTIL_CURRENT_STATE_FACT_TYPES
        and as_of_date
        and isinstance(valid_until, str)
    ):
        try:
            return date.fromisoformat(valid_until[:10]) >= date.fromisoformat(as_of_date)
        except ValueError:
            return False
    return True


def _claim_eligible(fact: dict[str, Any], as_of_date: str | None) -> bool:
    return _is_current(fact, as_of_date) and fact.get("verification_status") in _VERIFIED


def _unverified_observable(fact: dict[str, Any], as_of_date: str | None) -> bool:
    return (
        _is_current(fact, as_of_date)
        and fact.get("verification_status") in {"unverified", "pending_review"}
    )


def _observation(
    enterprise: dict[str, Any], capability_type: str, observation_type: str, value: Any,
    facts: list[dict[str, Any]], tag_codes: list[str], *, status: str = "partially_supported",
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    if any(fact.get("verification_status") in {"unverified", "pending_review"} for fact in facts):
        status = "ambiguous"
    fact_ids, tags, evidence_ids = _refs(facts, tag_codes)
    return {
        "observation_id": stable_capability_item_id(
            enterprise=enterprise,
            capability_type=capability_type,
            capability_subject=observation_type,
            capability_value=value,
            source_fact_ids=fact_ids,
            source_tag_codes=tags,
            derivation_method="deterministic_aggregation",
            prefix="observation",
        ),
        "observation_type": observation_type,
        "observation_value": deepcopy(value),
        "support_status": status,
        "source_fact_ids": fact_ids,
        "source_tag_codes": tags,
        "evidence_ids": evidence_ids,
        "limitations": list(limitations or []),
    }


def _claim(
    enterprise: dict[str, Any], capability_type: str, subject: str, value: Any,
    facts: list[dict[str, Any]], tag_codes: list[str], *, status: str,
    method: str = "deterministic_aggregation", basis: str, limitations: list[str] | None = None,
    as_of_date: str | None,
) -> dict[str, Any]:
    if not facts or any(not _claim_eligible(fact, as_of_date) for fact in facts):
        raise ValueError("Formal deterministic capability claims require current verified or partially verified facts")
    fact_ids, tags, evidence_ids = _refs(facts, tag_codes)
    return {
        "capability_id": stable_capability_item_id(
            enterprise=enterprise,
            capability_type=capability_type,
            capability_subject=subject,
            capability_value=value,
            source_fact_ids=fact_ids,
            source_tag_codes=tags,
            derivation_method=method,
        ),
        "capability_type": capability_type,
        "capability_subject": subject,
        "capability_value": deepcopy(value),
        "support_status": status,
        "derivation_method": method,
        "basis_summary": basis,
        "source_fact_ids": fact_ids,
        "source_tag_codes": tags,
        "evidence_ids": evidence_ids,
        "limitations": list(limitations or []),
        "time_scope": {"as_of_date": as_of_date},
    }


def _domain(capability_type: str, name: str) -> dict[str, Any]:
    return {
        "capability_type": capability_type,
        "capability_name": name,
        "support_status": "insufficient_data",
        "observations": [],
        "capability_claims": [],
        "semantic_capability_candidates": [],
        "unknowns": [],
        "source_fact_ids": [],
        "source_tag_codes": [],
        "evidence_ids": [],
        "limitations": [],
    }


def _finalize_domain(domain: dict[str, Any]) -> dict[str, Any]:
    all_items = domain["observations"] + domain["capability_claims"]
    domain["source_fact_ids"] = list(dict.fromkeys(fid for item in all_items for fid in item["source_fact_ids"]))
    domain["source_tag_codes"] = list(dict.fromkeys(code for item in all_items for code in item["source_tag_codes"]))
    domain["evidence_ids"] = list(dict.fromkeys(eid for item in all_items for eid in item["evidence_ids"]))
    return domain


def _domain_status_from_observations(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "insufficient_data"
    return "ambiguous" if all(item.get("support_status") == "ambiguous" for item in observations) else "partially_supported"


def _region_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("city", "province", "original", "county"):
            if value.get(key):
                return str(value[key])
    return None


def _normalized_amount(value: Any) -> tuple[float, str, str] | None:
    if not isinstance(value, dict):
        return None
    if value.get("semantic_status") != "confirmed":
        return None
    if value.get("value_parse_status") != "parsed":
        return None
    if value.get("normalization_status") != "normalized":
        return None
    number = value.get("normalized_value")
    unit = value.get("normalized_unit")
    currency = value.get("normalized_currency")
    if not isinstance(number, (int, float)) or isinstance(number, bool):
        return None
    if not isinstance(unit, str) or not unit.strip() or not isinstance(currency, str) or not currency.strip():
        return None
    return float(number), unit.strip(), currency.strip()


def build_deterministic_domains(fact_profile: dict[str, Any], tag_profile: dict[str, Any]) -> list[dict[str, Any]]:
    enterprise = fact_profile["enterprise"]
    as_of_date = fact_profile.get("as_of_date")
    facts = _included_facts(fact_profile)
    grouped = _by_type(facts)
    tags = _tag_map(tag_profile)
    domains = {key: _domain(key, name) for key, name in CAPABILITY_DOMAINS}

    verified_performance = [f for f in grouped.get("performance", []) if _claim_eligible(f, as_of_date)]
    verified_fulfillment = [f for f in grouped.get("fulfillment", []) if _claim_eligible(f, as_of_date)]
    verified_awards = [f for f in grouped.get("bid_award", []) if _claim_eligible(f, as_of_date)]

    # 1. Industry capability.
    d = domains["industry_capability"]
    registrations = [
        f for f in grouped.get("business_registration", [])
        if _is_current(f, as_of_date)
        and f.get("verification_status") not in _REJECTED_VERIFICATIONS
        and _present((f.get("payload") or {}).get("registered_industry"))
    ]
    participation_ids = set(((tags.get("B1.1") or {}).get("supporting_facts") or {}).get("included_fact_ids", []))
    participations = [
        f for f in grouped.get("bid_participation", [])
        if f.get("fact_id") in participation_ids
        and _is_current(f, as_of_date)
        and f.get("verification_status") not in _REJECTED_VERIFICATIONS
    ]
    delivered = [f for f in verified_performance + verified_fulfillment if _present((f.get("payload") or {}).get("industry"))]
    registered = sorted({str(f["payload"]["registered_industry"]) for f in registrations})
    market = sorted({str(f["payload"]["project_industry"]) for f in participations if _present(f["payload"].get("project_industry"))})
    delivered_industries = sorted({str(f["payload"]["industry"]) for f in delivered})
    if registered:
        d["observations"].append(_observation(enterprise, d["capability_type"], "registered_industry_context", registered, registrations, ["A1.4"], limitations=["工商登记行业仅作为企业背景，不能单独证明项目交付能力。"]))
    if market:
        d["observations"].append(_observation(enterprise, d["capability_type"], "market_participation_industries", market, participations, ["B1.1"], limitations=["历史投标参与行业不能单独证明已完成该行业项目交付。"]))
    if delivered_industries:
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "historical_delivered_project_industries", {"delivered_project_industries": delivered_industries}, delivered, [], status="partially_supported", basis="由当前时间视图内已核验的业绩或履约事实汇总历史已交付项目行业。", limitations=["未对目标项目执行同类语义判断。", "当前协议没有完整性证明，不能据此宣称行业能力已被完整支持。"], as_of_date=as_of_date))
        d["support_status"] = "partially_supported"
    elif d["observations"]:
        d["support_status"] = _domain_status_from_observations(d["observations"])
        d["unknowns"].append({"code": "delivered_industry_evidence_missing", "reason": "当前只有工商行业背景或市场参与行业，缺少已核验业绩/履约行业事实。", "required_data": ["已核验performance或fulfillment事实中的行业字段和证据"]})
    else:
        d["unknowns"].append({"code": "industry_capability_data_missing", "reason": "没有可用于行业能力判断的当前视图事实。", "required_data": ["工商登记行业", "历史业绩行业", "履约行业"]})
    _finalize_domain(d)

    # 2. Technical capability.
    d = domains["technical_capability"]
    all_current_quals = [f for f in grouped.get("qualification", []) if _is_current(f, as_of_date)]
    current_quals = [f for f in all_current_quals if f.get("verification_status") in _VERIFIED]
    unverified_quals = [f for f in all_current_quals if _unverified_observable(f, as_of_date)]
    all_current_certs = [f for f in grouped.get("personnel_certificate", []) if _is_current(f, as_of_date)]
    current_certs = [f for f in all_current_certs if f.get("verification_status") in _VERIFIED]
    unverified_certs = [f for f in all_current_certs if _unverified_observable(f, as_of_date)]
    products = [
        f for f in grouped.get("other_enterprise_fact", [])
        if (f.get("payload") or {}).get("subtype") == "product_business"
        and f.get("verification_status") not in _REJECTED_VERIFICATIONS
        and _is_current(f, as_of_date)
    ]
    verified_experience = [f for f in verified_performance if _present((f.get("payload") or {}).get("performance_scope"))]
    if current_quals:
        value = [{k: f["payload"].get(k) for k in ("name", "qualification_level", "certificate_number", "valid_until")} for f in current_quals]
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "current_qualification_capabilities", {"current_qualifications": value}, current_quals, ["B2.1"], status="partially_supported", basis="由当前时间视图内、生命周期有效且已核验或部分核验的资质事实形成。", limitations=["资质不能替代对具体项目资格条件的核验。"], as_of_date=as_of_date))
    if unverified_quals:
        value = [{k: f["payload"].get(k) for k in ("name", "qualification_level", "certificate_number", "valid_until")} for f in unverified_quals]
        d["observations"].append(_observation(enterprise, d["capability_type"], "unverified_qualification_observations", value, unverified_quals, ["B2.1"], status="ambiguous", limitations=["资质尚未核验，不能进入正式能力结论。", "需要权威来源或人工审核。"]))
    if current_certs:
        value = [{k: f["payload"].get(k) for k in ("person_id", "person_name", "certificate_name", "certificate_level", "valid_until")} for f in current_certs]
        d["observations"].append(_observation(enterprise, d["capability_type"], "current_personnel_certificates", value, current_certs, [], limitations=["人员证书是否满足具体项目要求需后续资格核验。"]))
    if unverified_certs:
        value = [{k: f["payload"].get(k) for k in ("person_id", "person_name", "certificate_name", "certificate_level", "valid_until")} for f in unverified_certs]
        d["observations"].append(_observation(enterprise, d["capability_type"], "unverified_personnel_certificate_observations", value, unverified_certs, [], status="ambiguous", limitations=["人员证书尚未核验，不能作为确认能力依据。"]))
    if products:
        value = [{"business_description": f["payload"].get("business_description"), "products": f["payload"].get("products", []), "product_business_tags": f["payload"].get("product_business_tags", []), "verification_status": f.get("verification_status")} for f in products]
        d["observations"].append(_observation(enterprise, d["capability_type"], "declared_products_and_services", value, products, ["B4.5"], status="ambiguous" if any(f.get("verification_status") not in _VERIFIED for f in products) else "partially_supported", limitations=["未核验产品业务描述只能作为候选业务描述，不能形成已确认技术能力。"]))
    if verified_experience:
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "verified_technical_experience", {"performance_scopes": sorted({str(f["payload"]["performance_scope"]) for f in verified_experience})}, verified_experience, [], status="partially_supported", basis="由已核验业绩事实中的技术或业务范围汇总。", limitations=["未进行自由文本语义扩展。", "当前事实集合不能证明技术能力覆盖完整。"], as_of_date=as_of_date))
    if verified_experience:
        d["support_status"] = "partially_supported"
    elif current_quals or current_certs:
        d["support_status"] = "partially_supported"
    elif d["observations"]:
        d["support_status"] = _domain_status_from_observations(d["observations"])
    else:
        d["unknowns"].append({"code": "technical_capability_data_missing", "reason": "缺少当前有效且已核验的资质、人员证书或技术业绩。", "required_data": ["已核验有效资质", "已核验人员证书", "已核验技术业绩"]})
    d["limitations"].append("本阶段不从企业名称或项目标题推断技术能力。")
    _finalize_domain(d)

    # 3. Similar performance.
    d = domains["similar_performance_capability"]
    performance = verified_performance
    fulfillment = verified_fulfillment
    records = performance + fulfillment
    if records:
        industries = sorted({str(f["payload"]["industry"]) for f in records if _present(f["payload"].get("industry"))})
        regions = sorted({r for f in records for r in [_region_value(f["payload"].get("region"))] if r})
        with_amount = sum(1 for f in records if _normalized_amount(f["payload"].get("contract_amount")) is not None)
        with_time = sum(1 for f in records if _present(f["payload"].get("start_date")) or _present(f["payload"].get("end_date")))
        value = {"performance_fact_count": len(performance), "fulfillment_fact_count": len(fulfillment), "performance_with_normalized_amount_count": with_amount, "performance_with_time_count": with_time, "performance_industries": industries, "performance_regions": regions}
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "historical_performance_inventory", value, records, [], status="partially_supported", basis="仅汇总当前时间视图内已核验的业绩与履约事实。", limitations=["尚未给定目标项目，不能确定哪些业绩属于同类。", "复杂同类语义需要后续受约束分析。"], as_of_date=as_of_date))
        d["support_status"] = "partially_supported"
        d["unknowns"].append({"code": "semantic_similarity_analysis_required", "reason": "确定性统计不能判断复杂业务范围是否同类。", "required_data": ["目标项目同类定义", "业绩范围语义比较结果"]})
    else:
        d["unknowns"].append({"code": "performance_facts_missing", "reason": "没有已核验performance或fulfillment事实，不能形成同类业绩能力。", "required_data": ["已核验历史合同或业绩事实", "已核验履约事实", "证据"]})
    _finalize_domain(d)

    # 4. Regional delivery.
    d = domains["regional_delivery_capability"]
    networks = [f for f in grouped.get("other_enterprise_fact", []) if (f.get("payload") or {}).get("subtype") == "organization_network" and f.get("verification_status") not in _REJECTED_VERIFICATIONS and _is_current(f, as_of_date)]
    presence_regions = sorted({str(region) for f in networks for region in (f.get("payload") or {}).get("service_regions", []) if region})
    market_regions = sorted({r for f in participations for r in [_region_value((f.get("payload") or {}).get("project_region"))] if r})
    delivery_facts = [f for f in performance + fulfillment if _region_value((f.get("payload") or {}).get("region"))]
    delivery_regions = sorted({_region_value(f["payload"].get("region")) for f in delivery_facts if _region_value(f["payload"].get("region"))})
    if presence_regions:
        d["observations"].append(_observation(enterprise, d["capability_type"], "service_presence_regions", presence_regions, networks, ["B4.3"], limitations=["分支机构或服务存在不能证明已完成当地项目交付。"]))
    if market_regions:
        d["observations"].append(_observation(enterprise, d["capability_type"], "market_participation_regions", market_regions, participations, ["B1.1"], limitations=["投标参与地区不能证明已在当地交付。"]))
    if delivery_regions:
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "historical_delivery_regions", {"historical_delivery_regions": delivery_regions}, delivery_facts, [], status="partially_supported", basis="由已核验业绩或履约事实中的地区字段汇总历史交付地区。", limitations=["历史记录不能证明地区交付覆盖已经完整。"], as_of_date=as_of_date))
        d["support_status"] = "partially_supported"
    elif d["observations"]:
        d["support_status"] = _domain_status_from_observations(d["observations"])
        d["unknowns"].append({"code": "historical_delivery_region_missing", "reason": "存在服务网点或投标地区观察，但缺少已核验业绩/履约地区事实。", "required_data": ["已核验performance或fulfillment中的地区与证据"]})
    else:
        d["unknowns"].append({"code": "regional_capability_data_missing", "reason": "没有分支机构、市场参与或历史交付地区事实。", "required_data": ["分支机构所在地", "历史业绩地区", "履约地区"]})
    _finalize_domain(d)

    # 5. Amount experience. No unit guessing and no exchange-rate conversion.
    d = domains["amount_experience_capability"]
    amount_sources: list[tuple[dict[str, Any], Any]] = []
    for f in grouped.get("performance", []):
        if _is_current(f, as_of_date) and f.get("verification_status") not in _REJECTED_VERIFICATIONS:
            amount_sources.append((f, (f.get("payload") or {}).get("contract_amount")))
    for f in grouped.get("fulfillment", []):
        if _is_current(f, as_of_date) and f.get("verification_status") not in _REJECTED_VERIFICATIONS:
            amount_sources.append((f, (f.get("payload") or {}).get("contract_amount")))
    for f in grouped.get("bid_award", []):
        if _is_current(f, as_of_date) and f.get("verification_status") not in _REJECTED_VERIFICATIONS:
            amount_sources.append((f, (f.get("payload") or {}).get("award_amount")))
    observed = [(f, value) for f, value in amount_sources if value is not None]
    normalized_records: list[tuple[dict[str, Any], float, str, str]] = []
    for fact, value in observed:
        if not _claim_eligible(fact, as_of_date):
            continue
        normalized = _normalized_amount(value)
        if normalized is not None:
            normalized_records.append((fact, *normalized))
    currencies = sorted({item[3] for item in normalized_records})
    units = sorted({item[2] for item in normalized_records})
    # A mixed set must not silently aggregate only the convenient subset.
    # Every observed amount must be verified, semantically confirmed and
    # normalized to the same unit/currency before statistics are calculated.
    aggregatable = (
        bool(normalized_records)
        and len(normalized_records) == len(observed)
        and len(currencies) == 1
        and len(units) == 1
    )
    if aggregatable:
        values = [item[1] for item in normalized_records]
        used = [item[0] for item in normalized_records]
        value = {
            "observed_amount_record_count": len(observed),
            "confirmed_amount_record_count": len(normalized_records),
            "min_confirmed_amount": min(values),
            "max_confirmed_amount": max(values),
            "average_confirmed_amount": sum(values) / len(values),
            "median_confirmed_amount": median(values),
            "normalized_currency": currencies[0],
            "normalized_unit": units[0],
            "amount_unit_normalization_status": "consistent",
        }
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "confirmed_historical_amount_experience", value, used, [], status="partially_supported", basis="只聚合已核验、语义确认且完成同币种同单位标准化的历史金额。", limitations=["不执行汇率转换。", "不猜测裸数字单位或币种。", "历史金额经验不能直接证明未来项目承接能力。", "未定义金额档位。", "历史金额记录集合不代表企业未来金额承接范围已经完整。"], as_of_date=as_of_date))
        d["support_status"] = "partially_supported"
    elif observed:
        observation_facts = [fact for fact, _ in observed if fact.get("verification_status") not in _REJECTED_VERIFICATIONS]
        value = {
            "observed_amount_record_count": len(observed),
            "confirmed_amount_record_count": len(normalized_records),
            "min_confirmed_amount": None,
            "max_confirmed_amount": None,
            "average_confirmed_amount": None,
            "median_confirmed_amount": None,
            "normalized_currency": currencies[0] if len(currencies) == 1 else None,
            "normalized_unit": units[0] if len(units) == 1 else None,
            "amount_unit_normalization_status": "inconsistent_or_incomplete",
        }
        if observation_facts:
            d["observations"].append(_observation(enterprise, d["capability_type"], "historical_amount_observations", value, observation_facts, [], status="ambiguous", limitations=["金额记录缺少统一标准单位、币种、确认语义或核验状态，未计算统计值。", "不执行汇率转换或单位猜测。"]))
        d["support_status"] = "ambiguous"
        d["unknowns"].append({"code": "amount_normalization_incomplete", "reason": "存在金额记录，但没有形成可统一聚合的标准化金额集合。", "required_data": ["semantic_status=confirmed", "value_parse_status=parsed", "normalization_status=normalized", "统一normalized_unit", "统一normalized_currency"]})
    else:
        d["unknowns"].append({"code": "confirmed_amount_facts_missing", "reason": "没有业务语义明确且完成标准化的合同、履约或已核验中标金额。", "required_data": ["已确认标准化金额", "标准单位与币种", "事实证据"]})
        d["limitations"].append("项目预算、最高限价和未确认中标金额未用于金额承接经验。")
    _finalize_domain(d)

    # 6. Personnel and resources.
    d = domains["personnel_resource_capability"]
    personnel_verified = [f for f in grouped.get("personnel", []) if _claim_eligible(f, as_of_date)]
    personnel_unverified = [f for f in grouped.get("personnel", []) if _unverified_observable(f, as_of_date)]
    verified_networks = [f for f in networks if f.get("verification_status") in _VERIFIED]
    unverified_networks = [f for f in networks if f.get("verification_status") not in _VERIFIED]
    if personnel_verified:
        values = [{k: f["payload"].get(k) for k in ("employee_count", "social_insurance_count", "count_scope") if k in f["payload"]} for f in personnel_verified]
        d["observations"].append(_observation(enterprise, d["capability_type"], "personnel_counts", values, personnel_verified, ["B4.1"], limitations=["人员数量仅保留为事实统计，不转换为强弱结论。"]))
    if personnel_unverified:
        values = [{k: f["payload"].get(k) for k in ("employee_count", "social_insurance_count", "count_scope") if k in f["payload"]} for f in personnel_unverified]
        d["observations"].append(_observation(enterprise, d["capability_type"], "unverified_personnel_observations", values, personnel_unverified, ["B4.1"], status="ambiguous", limitations=["人员统计尚未核验。"] ))
    if current_certs:
        distribution = dict(sorted(Counter(str(f["payload"].get("certificate_name")) for f in current_certs).items()))
        d["observations"].append(_observation(enterprise, d["capability_type"], "current_personnel_certificates", {"current_certificate_count": len(current_certs), "certificate_type_distribution": distribution}, current_certs, [], limitations=["证书是否满足具体项目要求需后续资格核验。"]))
    if unverified_certs:
        d["observations"].append(_observation(enterprise, d["capability_type"], "unverified_personnel_certificate_observations", {"observation_count": len(unverified_certs)}, unverified_certs, [], status="ambiguous", limitations=["未核验人员证书不能作为确认能力依据。"]))
    for network_group, status in ((verified_networks, "partially_supported"), (unverified_networks, "ambiguous")):
        branches = [(f, b) for f in network_group for b in (f.get("payload") or {}).get("branches", []) if isinstance(b, dict) and b.get("status") == "active"]
        if branches:
            d["observations"].append(_observation(enterprise, d["capability_type"], "active_branch_resources", {"active_branch_count": len(branches), "regions": sorted({str(b.get("region")) for _, b in branches if b.get("region")})}, list(dict.fromkeys(f["fact_id"] for f, _ in branches)) and network_group, ["B4.3"], status=status, limitations=["分支机构数量仅保留为资源观察，不转换为充足性结论。"]))
    if d["observations"]:
        d["support_status"] = _domain_status_from_observations(d["observations"])
    else:
        d["unknowns"].append({"code": "personnel_resource_data_missing", "reason": "没有可用人员统计、人员证书或组织资源事实。", "required_data": ["员工数量", "社保人数", "人员证书", "组织网络"]})
    _finalize_domain(d)

    # 7. Buyer relationships.
    d = domains["buyer_relationship_capability"]
    relationships = [f for f in grouped.get("buyer_relationship", []) if _claim_eligible(f, as_of_date)]
    buyer_records: list[tuple[dict[str, Any], str]] = []
    for f in verified_performance + verified_fulfillment + verified_awards:
        if _present((f.get("payload") or {}).get("buyer_name")):
            buyer_records.append((f, str(f["payload"]["buyer_name"])))
    for f in relationships:
        buyer_records.append((f, str(f["payload"]["buyer_name"])))
    if buyer_records:
        counts = Counter(name for _, name in buyer_records)
        used_ids = list(dict.fromkeys(f["fact_id"] for f, _ in buyer_records))
        used_facts = [f for f in facts if f["fact_id"] in used_ids]
        value = {"confirmed_buyer_count": len(counts), "confirmed_relationship_record_count": len(buyer_records), "repeat_buyer_count": sum(1 for count in counts.values() if count > 1), "buyer_relationships": [{"buyer_name": name, "record_count": count} for name, count in sorted(counts.items())], "data_coverage_status": "confirmed_records_only"}
        d["capability_claims"].append(_claim(enterprise, d["capability_type"], "confirmed_buyer_relationship_records", value, used_facts, [], status="partially_supported", basis="只使用已核验、明确buyer_name且企业角色可靠的关系、业绩、履约或中标事实。", limitations=["未拆分的采购单位与代理机构混合字段未使用。", "现有记录不能证明采购人关系数据覆盖完整。"], as_of_date=as_of_date))
        d["support_status"] = "partially_supported"
    else:
        d["unknowns"].append({"code": "buyer_relationship_data_missing", "reason": "没有已核验、可靠拆分且企业角色明确的采购人关系事实。", "required_data": ["buyer_name", "relationship_type或可靠企业角色", "核验状态", "证据"]})
        d["limitations"].append("buyer_and_agency_raw未用于采购人关系。")
    _finalize_domain(d)

    # 8. Tender performance.
    d = domains["tender_performance_capability"]
    b11 = tags.get("B1.1") or {}
    b12 = tags.get("B1.2") or {}
    b11_fids = ((b11.get("supporting_facts") or {}).get("fact_ids") or [])
    b11_facts = [f for f in facts if f.get("fact_id") in set(b11_fids) and f.get("verification_status") not in _REJECTED_VERIFICATIONS]
    if b11.get("tag_value") is not None:
        value = deepcopy(b11["tag_value"])
        value["participation_industries"] = deepcopy((b11.get("supporting_facts") or {}).get("historical_project_industries", []))
        value["participation_regions"] = deepcopy((b11.get("supporting_facts") or {}).get("historical_project_regions", []))
        value["confirmed_award_count"] = len(verified_awards) if verified_awards else None
        value["confirmed_fulfillment_count"] = len(verified_fulfillment) if verified_fulfillment else None
        value["winning_rate"] = None
        all_basis = b11_facts + verified_awards + verified_fulfillment
        if all_basis and all(_claim_eligible(f, as_of_date) for f in all_basis):
            d["capability_claims"].append(_claim(enterprise, d["capability_type"], "historical_tender_participation_summary", value, all_basis, ["B1.1", "B1.2"], status="partially_supported", basis="由B1.1及当前时间视图内已核验的投标参与、中标和履约事实汇总。", limitations=["没有可靠实际投标分母时不计算中标率。", "不输出投标能力强弱等级或中标概率。"], as_of_date=as_of_date))
            d["support_status"] = "partially_supported"
        elif b11_facts:
            d["observations"].append(_observation(enterprise, d["capability_type"], "historical_tender_participation_summary", value, b11_facts, ["B1.1", "B1.2"], status="ambiguous", limitations=["投标参与事实尚未核验，只能形成待核验历史观察。", "没有可靠实际投标分母时不计算中标率。", "不输出中标概率。"]))
            d["support_status"] = "ambiguous"
    else:
        d["unknowns"].append({"code": "tender_participation_data_missing", "reason": "当前时间视图没有可用投标参与事实。", "required_data": ["bid_participation事实"]})
    if b12.get("data_status") == "ambiguous":
        d["unknowns"].append({"code": "winning_role_and_amount_semantics_insufficient", "reason": "B1.2仍为ambiguous，不能生成中标次数、中标率或金额结论。", "required_data": ["明确bid_award事实", "可靠企业角色", "已确认金额口径"]})
        d["limitations"].append("继承B1.2的角色和金额语义限制。")
    _finalize_domain(d)

    # User-confirmed capability supplements are retained as explicit,
    # traceable observations. They improve the displayed profile immediately
    # without pretending that free text is equivalent to an independently
    # verified qualification or project record.
    supplements = [
        fact for fact in grouped.get("other_enterprise_fact", [])
        if (fact.get("payload") or {}).get("subtype") == "user_capability_input"
        and _claim_eligible(fact, as_of_date)
    ]
    for fact in supplements:
        details = (fact.get("payload") or {}).get("details") or {}
        target = str(details.get("target_code") or "")
        if target not in domains:
            continue
        domain = domains[target]
        content = str((fact.get("payload") or {}).get("business_description") or "").strip()
        if not content:
            continue
        domain["observations"].append(_observation(
            enterprise, target, "user_confirmed_capability_information",
            {"用户补充信息": content}, [fact], [],
            status="partially_supported",
            limitations=["该信息由用户提供，后续可补充合同、证书或其他材料进一步核验。"],
        ))
        domain["support_status"] = "partially_supported"
        domain["unknowns"] = []
        _finalize_domain(domain)

    return [domains[key] for key, _ in CAPABILITY_DOMAINS]
