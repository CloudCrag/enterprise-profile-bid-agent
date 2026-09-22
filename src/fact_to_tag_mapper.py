"""Map normalized enterprise facts to the formal 60-tag catalog."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from calendar import monthrange
from typing import Any, Iterable

from .enterprise_fact_profile import fact_profile_content_hash
from .evidence import validate_evidence_references

DATA_STATUSES = {"available", "partial", "ambiguous", "missing_data"}
CORE_TAG_CODES = {
    "A1.1", "A1.2", "A1.4", "B1.1", "B1.2", "B2.1", "B4.1", "B4.3",
    "B4.5", "B5.2", "C1.1", "C2.1", "C2.2", "C2.5", "C3.4",
}


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed



def _subtract_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)

def _years_between(start: Any, end: Any) -> int | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        established = date.fromisoformat(start[:10])
        as_of = date.fromisoformat(end[:10])
    except ValueError:
        return None
    if as_of < established:
        return None
    return as_of.year - established.year - ((as_of.month, as_of.day) < (established.month, established.day))


def _included_fact_ids(fact_profile: dict[str, Any]) -> set[str]:
    view = fact_profile.get("fact_view") if isinstance(fact_profile.get("fact_view"), dict) else {}
    values = view.get("included_fact_ids") if isinstance(view.get("included_fact_ids"), list) else []
    return {str(item) for item in values}


def _facts_by_type(fact_profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group only facts admitted by the current fact time view."""
    allowed = _included_fact_ids(fact_profile)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in fact_profile.get("facts", []):
        if (
            isinstance(fact, dict)
            and fact.get("fact_type")
            and fact.get("fact_id") in allowed
        ):
            grouped[str(fact["fact_type"])].append(fact)
    return grouped


def _view_excluded_counts(fact_profile: dict[str, Any], fact_type: str) -> dict[str, int]:
    view = fact_profile.get("fact_view") if isinstance(fact_profile.get("fact_view"), dict) else {}
    reasons = view.get("exclusion_reasons") if isinstance(view.get("exclusion_reasons"), dict) else {}
    by_id = {fact.get("fact_id"): fact for fact in fact_profile.get("facts", []) if isinstance(fact, dict)}
    counts = {"future": 0, "unknown": 0, "conflicting": 0, "invalid": 0}
    for fact_id, reason in reasons.items():
        fact = by_id.get(fact_id)
        if not fact or fact.get("fact_type") != fact_type:
            continue
        if reason == "future_available_relative_to_as_of_date":
            counts["future"] += 1
        elif reason == "availability_time_unknown":
            counts["unknown"] += 1
        elif reason == "availability_time_conflicting":
            counts["conflicting"] += 1
        elif reason == "availability_time_invalid":
            counts["invalid"] += 1
    return counts


def _fact_metadata(facts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = [fact for fact in facts if isinstance(fact, dict)]
    evidence_ids = list(dict.fromkeys(eid for fact in selected for eid in fact.get("evidence_ids", []) if eid))
    source_types = sorted({
        str((fact.get("source") or {}).get("source_type"))
        for fact in selected if (fact.get("source") or {}).get("source_type")
    })
    times: list[str] = []
    for fact in selected:
        temporal = fact.get("temporal") if isinstance(fact.get("temporal"), dict) else {}
        for key in ("verified_at", "collected_at", "observed_at"):
            value = temporal.get(key)
            if isinstance(value, str) and value:
                times.append(value)
                break
    return {
        "fact_ids": [fact["fact_id"] for fact in selected if fact.get("fact_id")],
        "evidence_ids": evidence_ids,
        "source_types": source_types,
        "updated_at": max(times) if times else None,
        "updated_at_status": "provided" if times else "not_provided",
    }


def _tag_base(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_code": item["primary_code"],
        "primary_name": item["primary_name"],
        "secondary_code": item["secondary_code"],
        "secondary_name": item["secondary_name"],
        "tag_code": item["tag_code"],
        "tag_name": item["tag_name"],
        "is_core": bool(item.get("is_core")),
        "tag_value": None,
        "data_status": "missing_data",
        "source_types": [],
        "evidence_ids": [],
        "updated_at": None,
        "updated_at_status": "not_provided",
        "missing_required_fields": deepcopy(item.get("required_data_fields") or ["该标签所需的直接企业事实"]),
        "quality_flags": [],
        "supporting_facts": {},
    }


def _apply(
    tag: dict[str, Any],
    *,
    value: Any,
    status: str,
    facts: list[dict[str, Any]],
    missing: list[str] | None = None,
    flags: list[str] | None = None,
    supporting: dict[str, Any] | None = None,
) -> None:
    if status not in DATA_STATUSES:
        raise ValueError(f"Unsupported tag data_status: {status}")
    meta = _fact_metadata(facts)
    tag["tag_value"] = deepcopy(value)
    tag["data_status"] = status
    tag["source_types"] = meta["source_types"]
    tag["evidence_ids"] = meta["evidence_ids"]
    tag["updated_at"] = meta["updated_at"]
    tag["updated_at_status"] = meta["updated_at_status"]
    tag["missing_required_fields"] = deepcopy(missing or [])
    tag["quality_flags"] = list(dict.fromkeys(flags or []))
    tag["supporting_facts"] = {"fact_ids": meta["fact_ids"], **deepcopy(supporting or {})}


def _conflicted(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [fact for fact in facts if fact.get("fact_status") == "conflicted"]


def _market_activity(facts: list[dict[str, Any]], as_of_date: str | None) -> dict[str, Any]:
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for fact in facts:
        payload = fact.get("payload") if isinstance(fact.get("payload"), dict) else {}
        if fact.get("fact_status") == "ambiguous" or payload.get("record_status") == "invalid_record":
            excluded.append({"fact": fact, "reason": "invalid_record"})
            continue
        duplicate = payload.get("duplicate_flags") if isinstance(payload.get("duplicate_flags"), dict) else {}
        if duplicate.get("exact_duplicate") and int(duplicate.get("exact_duplicate_occurrence") or 1) > 1:
            excluded.append({"fact": fact, "reason": "exact_duplicate_after_first"})
            continue
        if int(duplicate.get("exact_duplicate_occurrence") or 1) > 1:
            excluded.append({"fact": fact, "reason": "exact_duplicate_after_first"})
            continue
        included.append(fact)

    as_of = _parse_datetime(as_of_date) if as_of_date else None
    recent_12 = 0 if as_of else None
    recent_24 = 0 if as_of else None
    without_time = 0
    record_times: list[datetime] = []
    time_fields: Counter[str] = Counter()
    industries: set[str] = set()
    regions: set[str] = set()
    methods: set[str] = set()
    project_types: Counter[str] = Counter()
    budgets: list[dict[str, Any]] = []
    buyers: set[str] = set()
    future_facts: list[dict[str, Any]] = []
    for fact in included:
        payload = fact.get("payload") or {}
        temporal = fact.get("temporal") or {}
        parsed = None
        used_field = None
        for field in ("published_at", "observed_at", "valid_until"):
            parsed = _parse_datetime(temporal.get(field))
            if parsed:
                used_field = f"fact.temporal.{field}"
                break
        if parsed:
            record_times.append(parsed)
            time_fields[used_field] += 1
            if as_of:
                record_date = parsed.date()
                reference_date = as_of.date()
                if record_date > reference_date:
                    future_facts.append(fact)
                else:
                    if record_date >= _subtract_months(reference_date, 12):
                        recent_12 += 1
                    if record_date >= _subtract_months(reference_date, 24):
                        recent_24 += 1
        else:
            without_time += 1
        if payload.get("project_industry"):
            industries.add(str(payload["project_industry"]))
        region = payload.get("project_region")
        if isinstance(region, dict):
            for value in [region.get("province"), region.get("city"), region.get("original")]:
                if value:
                    regions.add(str(value))
        elif region:
            regions.add(str(region))
        if payload.get("procurement_method"):
            methods.add(str(payload["procurement_method"]))
        if payload.get("project_classification"):
            project_types[str(payload["project_classification"])] += 1
        budget = payload.get("project_budget")
        if isinstance(budget, dict) and _present(budget.get("value")):
            budgets.append({
                "value": budget.get("value"),
                "unit_raw": budget.get("unit_raw"),
                "fact_id": fact.get("fact_id"),
                "evidence_ids": deepcopy(fact.get("evidence_ids", [])),
            })
        buyer_raw = payload.get("buyer_and_agency_raw")
        if buyer_raw:
            buyers.add(str(buyer_raw))

    included_meta = _fact_metadata(included)
    excluded_facts = [item["fact"] for item in excluded]
    excluded_meta = _fact_metadata(excluded_facts)
    collected = [
        (fact.get("temporal") or {}).get("collected_at")
        for fact in included if (fact.get("temporal") or {}).get("collected_at")
    ]
    return {
        "source_record_count": len(facts),
        "included_record_count": len(included),
        "valid_non_exact_duplicate_record_count": len(included),
        "recent_12_months_count": recent_12,
        "recent_24_months_count": recent_24,
        "invalid_records_excluded": sum(1 for item in excluded if item["reason"] == "invalid_record"),
        "exact_duplicates_excluded": sum(1 for item in excluded if item["reason"] == "exact_duplicate_after_first"),
        "records_without_usable_time": without_time,
        "future_record_count": len(future_facts),
        "future_fact_ids": [fact.get("fact_id") for fact in future_facts if fact.get("fact_id")],
        "future_evidence_ids": list(dict.fromkeys(eid for fact in future_facts for eid in fact.get("evidence_ids", []) if eid)),
        "time_window_status": "computed" if as_of else "reference_date_missing",
        "included_fact_ids": included_meta["fact_ids"],
        "excluded_fact_ids": excluded_meta["fact_ids"],
        "evidence_ids": included_meta["evidence_ids"],
        "excluded_evidence_ids": excluded_meta["evidence_ids"],
        "source_types": included_meta["source_types"],
        "updated_at": max(collected) if collected else None,
        "updated_at_status": "provided" if collected else "not_provided",
        "time_coverage": {
            "earliest_record_time": min(record_times).isoformat() if record_times else None,
            "latest_record_time": max(record_times).isoformat() if record_times else None,
            "record_time_fields_used": dict(sorted(time_fields.items())),
        },
        "historical_project_industries": sorted(industries),
        "historical_project_regions": sorted(regions),
        "procurement_methods": sorted(methods),
        "project_type_distribution": dict(sorted(project_types.items())),
        "budget_observations": budgets,
        "deduplication_basis": "exact_duplicate_only",
        "business_unique_deduplication_applied": False,
        "buyer_count": None,
        "buyer_count_status": "pending_split_definition",
        "buyer_and_agency_raw_distinct_count": len(buyers),
        "excluded_records": [
            {"reason": item["reason"], "fact_id": item["fact"].get("fact_id"), "evidence_ids": deepcopy(item["fact"].get("evidence_ids", []))}
            for item in excluded
        ],
    }


def _registration_fact(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    facts = [fact for fact in grouped.get("business_registration", []) if fact.get("fact_status") not in {"superseded", "rejected"}]
    return facts[0] if facts else None


def _identity_observation_facts(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        fact for fact in grouped.get("other_enterprise_fact", [])
        if (fact.get("payload") or {}).get("subtype") == "enterprise_identity_observation"
        and fact.get("fact_status") not in {"superseded", "rejected"}
    ]


def _map_core_tag(
    tag: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]],
    fact_profile: dict[str, Any],
    market: dict[str, Any],
) -> None:
    code = tag["tag_code"]
    registration = _registration_fact(grouped)
    registration_payload = (registration or {}).get("payload") or {}
    as_of_date = fact_profile.get("as_of_date")

    if code == "A1.1":
        if registration:
            value = {
                "enterprise_name": registration_payload.get("enterprise_name"),
                "unified_social_credit_code": registration_payload.get("unified_social_credit_code"),
                "legal_representative": registration_payload.get("legal_representative"),
                "verification_result": registration_payload.get("verification_result"),
                "verification_elements": registration_payload.get("verification_elements"),
            }
            status = "available" if _present(value["verification_result"]) else "partial"
            missing = [] if status == "available" else ["权威主体核验结果", "法定代表人等核验要素"]
            _apply(tag, value=value, status=status, facts=[registration], missing=missing)
        else:
            observations = _identity_observation_facts(grouped)
            if observations:
                payload = observations[0].get("payload") or {}
                _apply(
                    tag,
                    value={
                        "enterprise_name": payload.get("enterprise_name"),
                        "unified_social_credit_code": payload.get("unified_social_credit_code"),
                        "identity_observation_context": payload.get("observation_context"),
                        "authority_verification_result": None,
                    },
                    status="partial",
                    facts=observations,
                    missing=["权威工商主体核验结果", "法定代表人", "经营状态", "成立日期"],
                    flags=["identity_observed_in_tender_dataset", "business_registration_not_verified"],
                    supporting={"identity_observation_only": True},
                )
    elif code == "A1.2" and registration:
        value = {
            "operation_status_raw": registration_payload.get("operation_status_raw"),
            "normalized_operation_status": registration_payload.get("normalized_operation_status"),
            "established_date": registration_payload.get("established_date"),
            "operating_years_as_of_date": _years_between(registration_payload.get("established_date"), as_of_date),
        }
        if any(_present(v) for v in value.values()):
            status = "available" if _present(value["operation_status_raw"]) and _present(value["established_date"]) else "partial"
            _apply(tag, value=value, status=status, facts=[registration], missing=[] if status == "available" else ["经营状态", "成立日期"])
    elif code == "A1.4" and registration:
        value = {
            "registered_industry": registration_payload.get("registered_industry"),
            "business_scope": registration_payload.get("business_scope"),
        }
        if any(_present(v) for v in value.values()):
            status = "available" if all(_present(v) for v in value.values()) else "partial"
            _apply(tag, value=value, status=status, facts=[registration], missing=[] if status == "available" else ["企业工商行业", "企业经营范围"], supporting={"project_match_not_performed": True})
    elif code == "B1.1":
        participation = grouped.get("bid_participation", [])
        view_excluded = _view_excluded_counts(fact_profile, "bid_participation")
        if participation:
            value = {key: market[key] for key in [
                "source_record_count", "included_record_count", "recent_12_months_count",
                "recent_24_months_count", "invalid_records_excluded", "exact_duplicates_excluded",
                "records_without_usable_time",
            ]}
            included = [fact for fact in participation if fact.get("fact_id") in market["included_fact_ids"]]
            if not market["included_record_count"]:
                status = "partial"
                missing = ["可用招投标参与事实"]
            elif not as_of_date:
                status = "partial"
                missing = ["画像时间窗口计算基准日as_of_date"]
            else:
                status = "available"
                missing = []
            _apply(
                tag,
                value=value,
                status=status,
                facts=included,
                missing=missing,
                flags=[
                    *(["invalid_records_excluded"] if market["invalid_records_excluded"] else []),
                    *(["exact_duplicates_excluded"] if market["exact_duplicates_excluded"] else []),
                    *(["records_without_usable_time"] if market["records_without_usable_time"] else []),
                    *(["as_of_date_not_provided", "recent_window_statistics_not_computed"] if not as_of_date else []),
                    *(["future_record_relative_to_as_of_date"] if market["future_record_count"] else []),
                    "business_unique_deduplication_not_applied",
                ],
                supporting={
                    "included_fact_ids": market["included_fact_ids"],
                    "excluded_fact_ids": market["excluded_fact_ids"],
                    "excluded_evidence_ids": market["excluded_evidence_ids"],
                    "excluded_records": market["excluded_records"],
                    "time_coverage": market["time_coverage"],
                    "time_window_status": market["time_window_status"],
                    "future_record_count": market["future_record_count"],
                    "future_fact_ids": market["future_fact_ids"],
                    "future_evidence_ids": market["future_evidence_ids"],
                    "historical_project_industries": market["historical_project_industries"],
                    "historical_project_regions": market["historical_project_regions"],
                    "procurement_methods": market["procurement_methods"],
                    "project_type_distribution": market["project_type_distribution"],
                    "budget_observations": market["budget_observations"],
                    "deduplication_basis": market["deduplication_basis"],
                    "business_unique_deduplication_applied": False,
                    "buyer_count": None,
                    "buyer_count_status": market["buyer_count_status"],
                    "buyer_and_agency_raw_distinct_count": market["buyer_and_agency_raw_distinct_count"],
                    "fact_view_as_of_date": fact_profile.get("as_of_date"),
                    "future_facts_excluded": _view_excluded_counts(fact_profile, "bid_participation")["future"],
                    "unknown_availability_facts_excluded": _view_excluded_counts(fact_profile, "bid_participation")["unknown"],
                },
            )
            tag["updated_at"] = market["updated_at"]
            tag["updated_at_status"] = market["updated_at_status"]
        elif any(view_excluded.values()):
            _apply(
                tag,
                value=None,
                status="missing_data",
                facts=[],
                missing=["当前时间视图内可用招投标参与事实"],
                flags=[
                    *( ["future_facts_excluded_from_view"] if view_excluded["future"] else [] ),
                    *( ["unknown_availability_facts_excluded_from_view"] if view_excluded["unknown"] else [] ),
                ],
                supporting={
                    "fact_view_as_of_date": fact_profile.get("as_of_date"),
                    "future_facts_excluded": view_excluded["future"],
                    "unknown_availability_facts_excluded": view_excluded["unknown"],
                    "time_window_status": "no_included_facts_in_current_view",
                },
            )
    elif code == "B1.2":
        awards = [fact for fact in grouped.get("bid_award", []) if fact.get("fact_status") == "active" and fact.get("verification_status") == "verified"]
        participation = grouped.get("bid_participation", [])
        if awards:
            _apply(
                tag,
                value={
                    "confirmed_award_fact_count": len(awards),
                    "award_facts": [deepcopy(fact.get("payload") or {}) for fact in awards],
                },
                status="available",
                facts=awards,
                supporting={"statistics_source": "explicit_verified_bid_award_facts"},
            )
        elif participation:
            _apply(
                tag,
                value=None,
                status="ambiguous",
                facts=participation,
                missing=["可靠中标角色事实", "最终金额业务口径"],
                flags=["role_evidence_insufficient", "winning_amount_semantics_not_confirmed"],
                supporting={
                    "winning_statistics_generated": False,
                    "source_record_count": len(participation),
                    "evidence_usable_for_market_history_only": True,
                },
            )
    elif code in {"B2.1", "C3.4"}:
        all_qualifications = grouped.get("qualification", [])
        qualifications = (
            [fact for fact in all_qualifications if fact.get("fact_status") in {"active", "conflicted"}]
            if code == "B2.1" else all_qualifications
        )
        if not qualifications:
            return
        conflicts = _conflicted(qualifications)
        if conflicts:
            _apply(
                tag,
                value=None,
                status="ambiguous",
                facts=qualifications,
                missing=["冲突资质事实的人工或后续流程核验结果"],
                flags=["qualification_fact_conflict"],
                supporting={
                    "conflicting_fact_ids": [fact["fact_id"] for fact in conflicts],
                    "conflict_group_ids": sorted({fact.get("conflict_group_id") for fact in conflicts if fact.get("conflict_group_id")}),
                    "conflicting_payloads": [deepcopy(fact.get("payload") or {}) for fact in conflicts],
                },
            )
            return
        if code == "B2.1":
            value = [
                {
                    "name": (fact.get("payload") or {}).get("name"),
                    "qualification_level": (fact.get("payload") or {}).get("qualification_level"),
                    "certificate_number": (fact.get("payload") or {}).get("certificate_number"),
                    "status": (fact.get("payload") or {}).get("status"),
                    "issue_date": (fact.get("payload") or {}).get("issue_date"),
                    "expiry_date": (fact.get("payload") or {}).get("valid_until"),
                    "fact_status": fact.get("fact_status"),
                    "fact_id": fact.get("fact_id"),
                }
                for fact in qualifications
            ]
            _apply(tag, value=value, status="available", facts=qualifications, supporting={"target_project_requirements_not_applied": True})
        else:
            as_of = _parse_datetime(as_of_date) if as_of_date else None
            value = []
            for fact in qualifications:
                payload = fact.get("payload") or {}
                expiry = _parse_datetime(payload.get("valid_until"))
                value.append({
                    "name": payload.get("name"),
                    "status": payload.get("status"),
                    "fact_status": fact.get("fact_status"),
                    "expiry_date": payload.get("valid_until"),
                    "days_until_expiry": (expiry.date() - as_of.date()).days if expiry and as_of else None,
                    "fact_id": fact.get("fact_id"),
                })
            _apply(tag, value=value, status="available", facts=qualifications, supporting={"near_expiry_classification_not_produced": True})
    elif code == "B4.1":
        personnel = grouped.get("personnel", [])
        if personnel:
            payload = personnel[0].get("payload") or {}
            value = {"employee_count": payload.get("employee_count"), "social_insurance_count": payload.get("social_insurance_count")}
            status = "available" if all(_present(v) for v in value.values()) else "partial"
            _apply(tag, value=value, status=status, facts=personnel, missing=[] if status == "available" else ["员工人数", "社保人数"])
    elif code == "B4.3":
        network = [fact for fact in grouped.get("other_enterprise_fact", []) if (fact.get("payload") or {}).get("subtype") == "organization_network"]
        if network:
            payload = network[0].get("payload") or {}
            branches = payload.get("branches") if isinstance(payload.get("branches"), list) else []
            active = [item for item in branches if isinstance(item, dict) and item.get("status") == "active"]
            value = {"active_branch_count": len(active), "branches": active, "service_regions": payload.get("service_regions") or []}
            _apply(tag, value=value, status="available", facts=network)
    elif code == "B4.5":
        product = [fact for fact in grouped.get("other_enterprise_fact", []) if (fact.get("payload") or {}).get("subtype") == "product_business"]
        if product:
            payload = product[0].get("payload") or {}
            value = {
                "business_description": payload.get("business_description"),
                "products": payload.get("products") or [],
                "product_business_tags": payload.get("product_business_tags") or [],
            }
            status = "available" if _present(value["business_description"]) else "partial"
            _apply(tag, value=value, status=status, facts=product, missing=[] if status == "available" else ["企业业务说明"])
    elif code == "B5.2":
        personnel = grouped.get("personnel", [])
        payload = (personnel[0].get("payload") if personnel else {}) or {}
        value = {
            "registered_capital": registration_payload.get("registered_capital") if registration else None,
            "paid_in_capital": registration_payload.get("paid_in_capital") if registration else None,
            "enterprise_size_classification": registration_payload.get("enterprise_size_classification") if registration else None,
            "employee_count": payload.get("employee_count"),
        }
        source_facts = ([registration] if registration else []) + personnel
        if source_facts and any(_present(v) for v in value.values()):
            status = "available" if _present(value["registered_capital"]) else "partial"
            _apply(tag, value=value, status=status, facts=source_facts, missing=[] if status == "available" else ["注册资本"])
    elif code in {"C1.1", "C2.1", "C2.2", "C2.5"}:
        category_by_code = {
            "C1.1": "dishonesty",
            "C2.1": "administrative_penalty",
            "C2.2": "abnormal_operation",
            "C2.5": "bankruptcy_liquidation",
        }
        category = category_by_code[code]
        risk_facts = [fact for fact in grouped.get("risk_penalty_credit", []) if (fact.get("payload") or {}).get("category") == category]
        if risk_facts:
            if _conflicted(risk_facts):
                _apply(tag, value=None, status="ambiguous", facts=risk_facts, missing=["风险事实冲突核验结果"], flags=["risk_fact_conflict"])
            else:
                payload = risk_facts[0].get("payload") or {}
                value = {"status": payload.get("status"), "records": deepcopy(payload.get("records") or [])}
                if code == "C2.1":
                    value.update({"severity_raw": payload.get("severity_raw"), "remediation_status": payload.get("remediation_status")})
                _apply(tag, value=value, status="available", facts=risk_facts)


def map_fact_profile_to_tags(fact_profile: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    grouped = _facts_by_type(fact_profile)
    market = _market_activity(grouped.get("bid_participation", []), fact_profile.get("as_of_date"))
    tags: list[dict[str, Any]] = []
    for item in catalog["tags"]:
        tag = _tag_base(item)
        if item["tag_code"] in CORE_TAG_CODES:
            _map_core_tag(tag, grouped, fact_profile, market)
        tags.append(tag)

    counts = Counter(tag["data_status"] for tag in tags)
    if sum(counts.values()) != 60:
        raise RuntimeError("Tag coverage did not cover all 60 formal tags")
    evidence_index = deepcopy(fact_profile.get("evidence_index") or {})
    references = list(dict.fromkeys(eid for tag in tags for eid in tag.get("evidence_ids", [])))
    resolved, ref_warnings = validate_evidence_references(evidence_index, references)
    fact_ids = _included_fact_ids(fact_profile)
    fact_ref_warnings: list[dict[str, Any]] = []
    for tag in tags:
        supporting = tag.get("supporting_facts") if isinstance(tag.get("supporting_facts"), dict) else {}
        for fact_id in supporting.get("fact_ids", []):
            if fact_id not in fact_ids:
                fact_ref_warnings.append({"code": "tag_fact_reference_unresolved", "tag_code": tag.get("tag_code"), "fact_id": fact_id})
                tag["quality_flags"] = list(dict.fromkeys(tag.get("quality_flags", []) + ["unresolved_fact_reference"]))
    included_ids = _included_fact_ids(fact_profile)
    source_types = sorted({
        str((fact.get("source") or {}).get("source_type"))
        for fact in fact_profile.get("facts", [])
        if fact.get("fact_id") in included_ids and (fact.get("source") or {}).get("source_type")
    })
    evidence_without_collection = sum(1 for item in evidence_index.values() if isinstance(item, dict) and not item.get("collected_at"))
    evidence_with_url = sum(1 for item in evidence_index.values() if isinstance(item, dict) and item.get("source_url"))
    warnings = deepcopy(fact_profile.get("warnings", [])) + ref_warnings + fact_ref_warnings
    return {
        "tag_set_schema_version": "enterprise-profile-tags/1.1.0",
        "tag_catalog_version": catalog["schema_version"],
        "source_fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
        "source_fact_profile_content_hash": fact_profile_content_hash(fact_profile),
        "enterprise": deepcopy(fact_profile["enterprise"]),
        "as_of_date": fact_profile.get("as_of_date"),
        "as_of_date_status": fact_profile.get("as_of_date_status"),
        "fact_view_summary": {
            "view_mode": (fact_profile.get("fact_view") or {}).get("view_mode"),
            "included_fact_count": len((fact_profile.get("fact_view") or {}).get("included_fact_ids", [])),
            "excluded_fact_count": len((fact_profile.get("fact_view") or {}).get("excluded_fact_ids", [])),
            "excluded_summary": deepcopy((fact_profile.get("fact_view") or {}).get("excluded_summary", {})),
        },
        "tag_scope": {
            "primary_dimension_count": 3,
            "secondary_dimension_count": 11,
            "tag_count": 60,
            "core_tag_count": 15,
            "non_core_tag_count": 45,
        },
        "tags": tags,
        "tag_coverage_summary": {
            "total_tag_count": 60,
            "available_count": counts["available"],
            "partial_count": counts["partial"],
            "ambiguous_count": counts["ambiguous"],
            "missing_data_count": counts["missing_data"],
        },
        "fact_reference_summary": {
            "referenced_fact_count": len({fid for tag in tags for fid in (tag.get("supporting_facts") or {}).get("fact_ids", [])}),
            "all_fact_references_resolved": not fact_ref_warnings,
            "unresolved_fact_reference_count": len(fact_ref_warnings),
        },
        "evidence_index": evidence_index,
        "evidence_summary": {
            "total_evidence_count": len(evidence_index),
            "referenced_evidence_count": len(resolved),
            "market_activity": {
                "included_evidence_count": len(market["evidence_ids"]),
                "excluded_evidence_count": len(market["excluded_evidence_ids"]),
            },
            "source_types": source_types,
            "evidence_with_source_url_count": evidence_with_url,
            "evidence_without_collection_time_count": evidence_without_collection,
            "all_references_resolved": not ref_warnings,
            "unresolved_reference_count": len(ref_warnings),
            "evidence_schema_status": "temporary_protocol",
        },
        "warnings": warnings,
        "data_quality_summary": {
            "tag_coverage": {
                "available": counts["available"],
                "partial": counts["partial"],
                "ambiguous": counts["ambiguous"],
                "missing_data": counts["missing_data"],
            },
            "unresolved_fact_conflict_count": len(fact_profile.get("conflicts", [])),
            "evidence_warning_count": len(ref_warnings),
            "fact_reference_warning_count": len(fact_ref_warnings),
            "contains_mock_data": bool((fact_profile.get("data_quality_summary") or {}).get("contains_mock_data")),
            "contains_anonymized_data": bool((fact_profile.get("data_quality_summary") or {}).get("contains_anonymized_data")),
        },
    }
