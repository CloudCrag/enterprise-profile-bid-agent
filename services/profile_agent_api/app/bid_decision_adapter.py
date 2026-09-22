"""Explicit enterprise-profile adapters for the bid-decision R6 gateway boundary.

This module is intentionally located in the profile service.  It converts the
profile service's versioned public data into the strict snapshot shape consumed
by the R6 service.  R6 never imports LocalJsonStore or profile-domain classes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from src.enterprise_fact_profile import build_fact
from src.fact_profile_builder import _summaries, detect_fact_conflicts
from src.fact_time_view import build_fact_view
from src.fact_validator import assert_valid_fact_profile

from .errors import ServiceError
from .local_store import utc_now


def _unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if isinstance(value, (str, int, float)) and str(value)))


def _profile_version(versions: dict[str, Any]) -> str:
    try:
        fact = int(versions.get("fact", 0))
        capability = int(versions.get("capability", 0))
        decision = int(versions.get("decision", 0))
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            "BID_PROFILE_VERSION_INVALID",
            "企业画像版本信息无法转换为投标决策快照版本。",
            details={"versions": versions},
            status_code=422,
        ) from exc
    if fact < 1 or capability < 1:
        raise ServiceError(
            "BID_PROFILE_VERSION_INCOMPLETE",
            "企业事实画像或能力画像版本缺失。",
            details={"versions": versions},
            status_code=422,
        )
    return f"profile-f{fact:04d}-c{capability:04d}-d{decision:04d}"


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(
            "BID_PROFILE_TIME_MISSING",
            f"企业画像字段 {field} 缺失，无法建立可追溯时间快照。",
            details={"field": field},
            status_code=422,
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServiceError(
            "BID_PROFILE_TIME_INVALID",
            f"企业画像字段 {field} 不是有效 ISO 时间。",
            details={"field": field},
            status_code=422,
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("normalized_value", "value", "amount", "min", "max"):
            if value.get(key) not in (None, ""):
                value = value[key]
                break
        else:
            return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


_APPROVED_FACT_VERIFICATION_STATUSES = {
    "verified",
    "partially_verified",
    "source_declared",
    "SOURCE_DECLARED",
}
_APPROVED_CAPABILITY_SUPPORT_STATUSES = {"supported", "partially_supported"}


def _normalise_as_of(value: Any, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max, tzinfo=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ServiceError(
                "BID_PROFILE_AS_OF_TIME_INVALID",
                "as_of_time 不是有效 ISO 时间。",
                details={"as_of_time": value},
                status_code=422,
            ) from exc
    else:
        raise ServiceError(
            "BID_PROFILE_AS_OF_TIME_INVALID",
            "as_of_time 类型无效。",
            details={"type": type(value).__name__},
            status_code=422,
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fact_is_effective(fact: dict[str, Any], as_of: datetime) -> bool:
    if fact.get("fact_status") != "active":
        return False
    verification = str(fact.get("verification_status") or "")
    if verification not in _APPROVED_FACT_VERIFICATION_STATUSES:
        return False
    temporal = fact.get("temporal") if isinstance(fact.get("temporal"), dict) else {}
    available_at = temporal.get("available_at") or temporal.get("observed_at")
    if available_at:
        try:
            if _parse_datetime(available_at, field="fact.temporal.available_at") > as_of:
                return False
        except ServiceError:
            return False
    valid_from = temporal.get("valid_from")
    valid_until = temporal.get("valid_until")
    try:
        if valid_from and date.fromisoformat(str(valid_from)[:10]) > as_of.date():
            return False
        if valid_until and date.fromisoformat(str(valid_until)[:10]) < as_of.date():
            return False
    except ValueError:
        return False
    return True


def _fact_is_unknown_candidate(fact: dict[str, Any], as_of: datetime) -> bool:
    """Return pending/unverified facts that may be shown only as UNKNOWN context."""
    if str(fact.get("fact_status") or "").lower() not in {"active", "ambiguous", "pending"}:
        return False
    verification = str(fact.get("verification_status") or "").lower()
    if verification in {str(item).lower() for item in _APPROVED_FACT_VERIFICATION_STATUSES}:
        return False
    if verification in {"rejected", "invalid", "fraudulent"}:
        return False
    temporal = fact.get("temporal") if isinstance(fact.get("temporal"), dict) else {}
    available_at = temporal.get("available_at") or temporal.get("observed_at")
    if available_at:
        try:
            if _parse_datetime(available_at, field="fact.temporal.available_at") > as_of:
                return False
        except ServiceError:
            return False
    valid_until = temporal.get("valid_until")
    if valid_until:
        try:
            if date.fromisoformat(str(valid_until)[:10]) < as_of.date():
                return False
        except ValueError:
            return False
    return True


def _unknown_fact_payload(fact: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(fact)
    item["decision_use_status"] = "UNKNOWN_ONLY"
    item["requires_verification"] = True
    item["decision_note"] = "该信息尚未通过正式审核，不得用于资格 PASS 或确定性评分。"
    return item


def _collect_pending_capability_items(capability: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    approved = {str(item).lower() for item in _APPROVED_CAPABILITY_SUPPORT_STATUSES}
    rejected = {"rejected", "unsupported", "invalid"}
    for domain in capability.get("capability_domains") or []:
        if not isinstance(domain, dict):
            continue
        capability_type = str(domain.get("capability_type") or "unknown")
        for kind, rows in (("observation", domain.get("observations") or []), ("claim", domain.get("capability_claims") or [])):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                status = str(row.get("support_status") or "unknown").lower()
                if status in approved or status in rejected:
                    continue
                pending.append({
                    "capability_type": capability_type,
                    "item_kind": kind,
                    "support_status": status or "unknown",
                    "source_fact_ids": [str(x) for x in row.get("source_fact_ids") or [] if x],
                    "evidence_ids": [str(x) for x in row.get("evidence_ids") or [] if x],
                    "value": deepcopy(row.get("observation_value") if kind == "observation" else row.get("capability_value")),
                    "decision_use_status": "UNKNOWN_ONLY",
                    "requires_verification": True,
                })
    return pending


def _collect_domain_values(
    capability: dict[str, Any],
    capability_type: str,
    *,
    approved_fact_ids: set[str],
    approved_evidence_ids: set[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    domains = [
        item for item in capability.get("capability_domains") or []
        if isinstance(item, dict) and item.get("capability_type") == capability_type
    ]
    strings: list[str] = []
    raw_values: list[dict[str, Any]] = []
    for domain in domains:
        for observation in domain.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            if observation.get("support_status") not in _APPROVED_CAPABILITY_SUPPORT_STATUSES:
                continue
            source_fact_ids = {
                str(item) for item in observation.get("source_fact_ids") or [] if item
            }
            evidence_ids = {
                str(item) for item in observation.get("evidence_ids") or [] if item
            }
            if not source_fact_ids.intersection(approved_fact_ids):
                continue
            if evidence_ids and not evidence_ids.intersection(approved_evidence_ids):
                continue
            value = observation.get("observation_value")
            raw_values.append(observation)
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        strings.append(item)
                    elif isinstance(item, dict):
                        for key in ("name", "industry_name", "region_name", "business_description"):
                            if isinstance(item.get(key), str):
                                strings.append(item[key])
            elif isinstance(value, dict):
                for key in ("regions", "participation_industries", "participation_regions", "products"):
                    nested = value.get(key)
                    if isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, str):
                                strings.append(item)
                            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                                strings.append(item["name"])
        for claim in domain.get("capability_claims") or []:
            if not isinstance(claim, dict):
                continue
            if claim.get("support_status") not in _APPROVED_CAPABILITY_SUPPORT_STATUSES:
                continue
            source_fact_ids = {
                str(item) for item in claim.get("source_fact_ids") or [] if item
            }
            evidence_ids = {
                str(item) for item in claim.get("evidence_ids") or [] if item
            }
            if source_fact_ids and not source_fact_ids.intersection(approved_fact_ids):
                continue
            if evidence_ids and not evidence_ids.intersection(approved_evidence_ids):
                continue
            raw_values.append(claim)
            value = claim.get("capability_value")
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, str):
                                strings.append(item)
                            elif isinstance(item, dict):
                                name = item.get("name") or item.get("industry_name") or item.get("region_name")
                                if isinstance(name, str):
                                    strings.append(name)
    return _unique_strings(strings), raw_values


def _decision_fields(decision: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not decision:
        return result
    for item in decision.get("decision_fields") or []:
        if not isinstance(item, dict) or item.get("confirmation_status") != "confirmed":
            continue
        code = item.get("field_code")
        if isinstance(code, str):
            result[code] = item.get("value")
    return result


def _list_names(value: Any, key: str) -> list[str]:
    if not isinstance(value, dict):
        return []
    rows = value.get(key)
    if not isinstance(rows, list):
        return []
    names = []
    for row in rows:
        if isinstance(row, str):
            names.append(row)
        elif isinstance(row, dict):
            candidate = row.get("industry_name") or row.get("region_name") or row.get("name")
            if isinstance(candidate, str):
                names.append(candidate)
    return _unique_strings(names)


def _decision_snapshot(decision: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    fields = _decision_fields(decision)
    missing: list[str] = []
    strategic_industries = _list_names(fields.get("strategic_industries"), "industries")
    strategic_regions = _list_names(fields.get("strategic_regions"), "regions")

    budget = fields.get("budget_preference")
    budget_min = budget_max = None
    if isinstance(budget, dict):
        budget_min = _decimal(budget.get("min_amount") or budget.get("budget_min") or budget.get("min"))
        budget_max = _decimal(budget.get("max_amount") or budget.get("budget_max") or budget.get("max"))
    else:
        missing.append("decision_profile.budget_preference")

    max_concurrent = fields.get("max_concurrent_projects")
    if isinstance(max_concurrent, dict):
        max_concurrent = max_concurrent.get("count") or max_concurrent.get("value")
    try:
        max_concurrent_value = max(1, int(max_concurrent))
    except (TypeError, ValueError):
        max_concurrent_value = 2
        missing.append("decision_profile.max_concurrent_projects")

    personnel = fields.get("personnel_resource_constraints")
    slots = None
    if isinstance(personnel, dict):
        slots = personnel.get("available_bid_team_slots") or personnel.get("bid_team_slots")
    try:
        slot_value = max(0, int(slots))
    except (TypeError, ValueError):
        slot_value = 1
        missing.append("decision_profile.personnel_resource_constraints.available_bid_team_slots")

    risk = fields.get("risk_preference")
    if isinstance(risk, dict):
        risk = risk.get("level") or risk.get("risk_level")
    risk_value = str(risk).upper() if isinstance(risk, str) else "MEDIUM"
    if risk_value not in {"LOW", "MEDIUM", "HIGH"}:
        risk_value = "MEDIUM"
    if "risk_preference" not in fields:
        missing.append("decision_profile.risk_preference")

    exclusions = fields.get("explicit_exclusions")
    if isinstance(exclusions, dict):
        exclusions = exclusions.get("conditions") or exclusions.get("items")
    excluded_conditions = _unique_strings(exclusions if isinstance(exclusions, list) else [])

    return {
        "strategic_industries": strategic_industries,
        "strategic_regions": strategic_regions,
        "budget_min": str(budget_min) if budget_min is not None else None,
        "budget_max": str(budget_max) if budget_max is not None else None,
        "max_concurrent_bids": max_concurrent_value,
        "available_bid_team_slots": slot_value,
        "risk_preference": risk_value,
        "excluded_conditions": excluded_conditions,
    }, missing


def _capability_snapshot(
    capability: dict[str, Any],
    *,
    approved_fact_ids: set[str],
    approved_evidence_ids: set[str],
) -> dict[str, Any]:
    kwargs = {
        "approved_fact_ids": approved_fact_ids,
        "approved_evidence_ids": approved_evidence_ids,
    }
    industry, _ = _collect_domain_values(capability, "industry_capability", **kwargs)
    technical, _ = _collect_domain_values(capability, "technical_capability", **kwargs)
    similar, _ = _collect_domain_values(capability, "similar_performance_capability", **kwargs)
    regions, _ = _collect_domain_values(capability, "regional_delivery_capability", **kwargs)
    buyers, _ = _collect_domain_values(capability, "buyer_relationship_capability", **kwargs)

    amount_rows, amount_raw = _collect_domain_values(capability, "amount_experience_capability", **kwargs)
    amounts: list[Decimal] = []
    for row in amount_raw:
        value = row.get("observation_value") or row.get("capability_value")
        if isinstance(value, dict):
            for candidate in value.values():
                parsed = _decimal(candidate)
                if parsed is not None:
                    amounts.append(parsed)
    amount_profile = {
        "min_amount": str(min(amounts)) if amounts else None,
        "max_amount": str(max(amounts)) if amounts else None,
    }

    _, personnel_raw = _collect_domain_values(capability, "personnel_resource_capability", **kwargs)
    personnel_numbers: dict[str, int] = {}
    for row in personnel_raw:
        value = row.get("observation_value") or row.get("capability_value")
        if isinstance(value, dict):
            for key, candidate in value.items():
                if isinstance(candidate, bool):
                    continue
                try:
                    personnel_numbers[str(key)] = int(candidate)
                except (TypeError, ValueError):
                    continue

    _, tender_raw = _collect_domain_values(capability, "tender_performance_capability", **kwargs)
    tender_numbers: dict[str, int | float] = {}
    for row in tender_raw:
        value = row.get("observation_value") or row.get("capability_value")
        if isinstance(value, dict):
            for key, candidate in value.items():
                if isinstance(candidate, bool):
                    continue
                if isinstance(candidate, (int, float)):
                    tender_numbers[str(key)] = candidate

    return {
        "industry_capability": industry,
        "technical_capability": technical,
        "similar_performance_capability": similar,
        "regional_delivery_capability": regions,
        "amount_experience_capability": amount_profile,
        "personnel_resource_capability": personnel_numbers,
        "buyer_relationship_capability": buyers,
        "tender_performance_capability": tender_numbers,
        "pending_items": _collect_pending_capability_items(capability),
    }


def build_company_profile_snapshot(
    service: Any,
    company_id: str,
    *,
    requested_profile_version: str | None = None,
    as_of_time: Any = None,
    current_task_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only decision-safe enterprise profile contract.

    Approved facts and supported capabilities remain the only inputs to
    deterministic qualification and scoring. Pending or ambiguous items are
    transmitted separately as UNKNOWN-only context so the bid Agent can request
    verification without treating them as satisfied facts. Rejected, invalid
    and expired items remain excluded.
    """
    source = service.company_profile(company_id)
    if source.get("company_id") != company_id:
        raise ServiceError(
            "BID_PROFILE_COMPANY_MISMATCH",
            "企业画像响应中的 company_id 与请求不一致。",
            details={"requested": company_id, "returned": source.get("company_id")},
            status_code=409,
        )
    fact = source.get("fact_profile")
    capability = source.get("capability_profile")
    if not isinstance(fact, dict) or not isinstance(capability, dict):
        raise ServiceError(
            "BID_PROFILE_SCHEMA_INVALID",
            "企业画像缺少 fact_profile 或 capability_profile。",
            details={},
            status_code=422,
        )

    versions = source.get("versions") or {}
    profile_version = _profile_version(versions)
    if requested_profile_version and requested_profile_version != profile_version:
        raise ServiceError(
            "BID_PROFILE_VERSION_MISMATCH",
            "请求的企业画像版本不是当前正式版本。",
            details={"requested": requested_profile_version, "current": profile_version},
            status_code=409,
        )

    fact_generated_at = _parse_datetime(
        fact.get("generated_at_utc"), field="fact_profile.generated_at_utc"
    )
    effective_as_of = _normalise_as_of(as_of_time, fallback=fact_generated_at)
    all_facts = [item for item in fact.get("facts") or [] if isinstance(item, dict)]
    facts = [
        deepcopy(item) for item in all_facts if _fact_is_effective(item, effective_as_of)
    ]
    pending_facts = [
        _unknown_fact_payload(item) for item in all_facts
        if _fact_is_unknown_candidate(item, effective_as_of)
    ]
    approved_fact_ids = {
        str(item.get("fact_id")) for item in facts if item.get("fact_id")
    }
    referenced_evidence_ids = {
        str(evidence_id)
        for item in facts
        for evidence_id in item.get("evidence_ids") or []
        if evidence_id
    }
    source_evidence = fact.get("evidence_index") or {}
    approved_evidence_index = {
        str(evidence_id): deepcopy(record)
        for evidence_id, record in source_evidence.items()
        if evidence_id in referenced_evidence_ids
        and isinstance(record, dict)
        and str(record.get("record_status") or "valid").lower() not in {"rejected", "invalid"}
    }
    approved_evidence_ids = set(approved_evidence_index)
    pending_evidence_ids = {
        str(evidence_id)
        for item in pending_facts
        for evidence_id in item.get("evidence_ids") or []
        if evidence_id
    }
    pending_evidence_index = {
        str(evidence_id): deepcopy(record)
        for evidence_id, record in source_evidence.items()
        if evidence_id in pending_evidence_ids
        and isinstance(record, dict)
        and str(record.get("record_status") or "valid").lower() not in {"rejected", "invalid"}
    }
    # Do not pass approved facts whose evidence disappeared during validation.
    facts = [
        item for item in facts
        if not item.get("evidence_ids")
        or set(map(str, item.get("evidence_ids") or [])).intersection(approved_evidence_ids)
    ]
    approved_fact_ids = {
        str(item.get("fact_id")) for item in facts if item.get("fact_id")
    }

    conflicts_raw = fact.get("conflicts") or []
    safe_conflicts: list[Any] = []
    for item in conflicts_raw:
        if not isinstance(item, dict):
            safe_conflicts.append(item)
            continue
        referenced = {
            str(value)
            for key in ("fact_ids", "source_fact_ids", "conflicting_fact_ids")
            for value in (item.get(key) or [])
        }
        if not referenced or referenced.intersection(approved_fact_ids):
            safe_conflicts.append(deepcopy(item))

    safe_fact_profile = {
        "fact_profile_schema_version": fact.get("fact_profile_schema_version"),
        "enterprise": deepcopy(fact.get("enterprise") or {}),
        "as_of_date": effective_as_of.date().isoformat(),
        "as_of_date_status": "requested_as_of_time" if as_of_time is not None else fact.get("as_of_date_status"),
        "facts": facts,
        "pending_facts": pending_facts,
        "conflicts": safe_conflicts,
        "evidence_index": approved_evidence_index,
        "pending_evidence_index": pending_evidence_index,
        "source_summary": deepcopy(fact.get("source_summary") or {}),
        "generated_at_utc": fact.get("generated_at_utc"),
    }

    decision, decision_missing = _decision_snapshot(source.get("decision_profile"))
    capability_snapshot = _capability_snapshot(
        capability,
        approved_fact_ids=approved_fact_ids,
        approved_evidence_ids=approved_evidence_ids,
    )
    evidence_ids = sorted(approved_evidence_ids)
    quality = fact.get("data_quality_summary") or {}
    missing_fact_types = [str(item) for item in quality.get("missing_fact_types") or []]
    missing_fields = [f"fact_profile.{item}" for item in missing_fact_types] + decision_missing
    for field_name, values in {
        "capability_profile.industry_capability": capability_snapshot["industry_capability"],
        "capability_profile.technical_capability": capability_snapshot["technical_capability"],
        "capability_profile.similar_performance_capability": capability_snapshot["similar_performance_capability"],
        "capability_profile.regional_delivery_capability": capability_snapshot["regional_delivery_capability"],
    }.items():
        if not values:
            missing_fields.append(field_name)
    excluded_count = max(0, len(fact.get("facts") or []) - len(facts) - len(pending_facts))
    if excluded_count:
        missing_fields.append("fact_profile.non_effective_or_rejected_facts_excluded")
    if pending_facts:
        missing_fields.append("fact_profile.pending_facts_require_verification")
    if capability_snapshot.get("pending_items"):
        missing_fields.append("capability_profile.pending_items_require_verification")
    missing_fields = _unique_strings(missing_fields)

    fact_type_counts = {}
    for item in facts:
        fact_type = str(item.get("fact_type") or "unknown")
        fact_type_counts[fact_type] = fact_type_counts.get(fact_type, 0) + 1
    total_fact_types = max(1, len(missing_fact_types) + len(fact_type_counts))
    completeness = max(0.0, min(1.0, 1.0 - len(missing_fact_types) / total_fact_types))
    age_days = max(0.0, (datetime.now(timezone.utc) - fact_generated_at).total_seconds() / 86400)
    freshness = 1.0 if age_days <= 30 else 0.8 if age_days <= 90 else 0.5 if age_days <= 365 else 0.2
    conflicts = _unique_strings([
        (item.get("conflict_id") or item.get("conflict_type") or json.dumps(item, ensure_ascii=False, sort_keys=True))
        if isinstance(item, dict) else item
        for item in safe_conflicts
    ])
    reliability = 1.0
    if not quality.get("all_fact_evidence_resolved", False):
        reliability -= 0.25
    if conflicts:
        reliability -= min(0.35, 0.1 * len(conflicts))
    if quality.get("contains_mock_data"):
        reliability -= 0.15
    if quality.get("contains_anonymized_data"):
        reliability -= 0.05
    reliability = max(0.0, min(1.0, reliability))

    data_sources = _unique_strings([
        value
        for item in facts
        for value in (
            (item.get("source") or {}).get("source_type"),
            (item.get("source") or {}).get("source_platform"),
            (item.get("source") or {}).get("dataset_id"),
        )
        if value
    ])
    generated_candidates = [fact_generated_at]
    for profile_key in ("capability_profile", "decision_profile"):
        generated = (source.get(profile_key) or {}).get("generated_at_utc")
        if generated:
            try:
                generated_candidates.append(_parse_datetime(generated, field=f"{profile_key}.generated_at_utc"))
            except ServiceError:
                pass
    generated_at = max(generated_candidates)
    enterprise = fact.get("enterprise") if isinstance(fact.get("enterprise"), dict) else {}

    return {
        "company_id": company_id,
        "company_name": enterprise.get("name") or enterprise.get("enterprise_name"),
        "profile_version": profile_version,
        "fact_profile_version": f"fact-v{int(versions.get('fact', 0)):04d}",
        "capability_profile_version": f"capability-v{int(versions.get('capability', 0)):04d}",
        "decision_profile_version": f"decision-v{int(versions.get('decision', 0)):04d}",
        "fact_profile": safe_fact_profile,
        "capability_profile": capability_snapshot,
        "decision_profile": decision,
        "evidence_ids": evidence_ids,
        "data_quality": {
            "completeness": round(completeness, 6),
            "freshness": round(freshness, 6),
            "reliability": round(reliability, 6),
            "missing_fields": missing_fields,
        },
        "conflicts": conflicts,
        "as_of_time": effective_as_of.isoformat(),
        "generated_at": generated_at.isoformat(),
        "current_task_constraints": deepcopy(current_task_constraints or {}),
        "data_sources": data_sources,
        "data_updated_at": generated_at.isoformat(),
        "data_provider": "HOST_ENTERPRISE_PROFILE_ADAPTER",
        "unknown_items": [
            *[{
                "type": "PENDING_FACT",
                "fact_id": item.get("fact_id"),
                "fact_type": item.get("fact_type"),
                "verification_status": item.get("verification_status"),
                "evidence_ids": item.get("evidence_ids") or [],
                "decision_use_status": "UNKNOWN_ONLY",
            } for item in pending_facts],
            *[{"type": "PENDING_CAPABILITY", **item} for item in capability_snapshot.get("pending_items") or []],
        ],
    }


def build_evaluation_snapshot(service: Any, company_id: str, profile_version: str) -> dict[str, Any]:
    current = build_company_profile_snapshot(service, company_id)
    if current["profile_version"] != profile_version:
        raise ServiceError(
            "BID_EVALUATION_PROFILE_VERSION_MISMATCH",
            "企业评价请求的画像版本与当前画像版本不一致。",
            details={"requested": profile_version, "current": current["profile_version"]},
            status_code=409,
        )
    try:
        source = service.latest_evaluation(company_id)
    except ServiceError as exc:
        if exc.code == "EVALUATION_NOT_FOUND":
            return {
                "company_id": company_id,
                "profile_version": profile_version,
                "evaluation_version": None,
                "status": "NOT_AVAILABLE",
                "coverage": 0.0,
                "dimension_results": {},
                "risk_indicators": [],
                "official_total_score": None,
                "official_grade": None,
                "publication_status": "POLICY_NOT_CONFIRMED",
                "evidence_ids": [],
            }
        raise

    source_company = source.get("company_id") or source.get("enterprise_id")
    if source_company not in (None, company_id):
        raise ServiceError(
            "BID_EVALUATION_COMPANY_MISMATCH",
            "企业评价快照属于其他企业。",
            details={"requested": company_id, "returned": source_company},
            status_code=409,
        )
    source_profile_version = source.get("company_profile_version") or source.get("profile_version")
    if source_profile_version and str(source_profile_version) != profile_version:
        raise ServiceError(
            "BID_EVALUATION_PROFILE_VERSION_MISMATCH",
            "企业评价版本依赖与当前画像版本冲突。",
            details={"evaluation_profile_version": source_profile_version, "current": profile_version},
            status_code=409,
        )
    coverage = source.get("coverage")
    if coverage is None:
        summary = source.get("coverage_summary") or source.get("data_quality_summary") or {}
        coverage = summary.get("coverage") or summary.get("coverage_ratio") or summary.get("completeness") or 0.0
    try:
        coverage_value = max(0.0, min(1.0, float(coverage)))
    except (TypeError, ValueError):
        coverage_value = 0.0
    publication = str(source.get("publication_status") or "DRAFT").upper()
    if publication not in {"PUBLISHED", "DRAFT", "POLICY_NOT_CONFIRMED"}:
        publication = "DRAFT"
    published = publication == "PUBLISHED"
    dimensions = source.get("dimension_results") or source.get("dimensions") or source.get("indicator_results") or {}
    if isinstance(dimensions, list):
        dimensions = {str(index): value for index, value in enumerate(dimensions)}
    risks = source.get("risk_indicators") or source.get("risks") or []
    evidence_ids = source.get("evidence_ids") or list((source.get("evidence_index") or {}).keys())
    evaluation_version = source.get("evaluation_version")
    return {
        "company_id": company_id,
        "profile_version": profile_version,
        "evaluation_version": f"evaluation-v{int(evaluation_version):04d}" if isinstance(evaluation_version, int) else (str(evaluation_version) if evaluation_version else None),
        "status": "AVAILABLE" if published and coverage_value >= 0.8 else "PARTIAL",
        "coverage": coverage_value,
        "dimension_results": dimensions if isinstance(dimensions, dict) else {},
        "risk_indicators": _unique_strings(risks if isinstance(risks, list) else []),
        "official_total_score": (source.get("official_total_score") or source.get("total_score")) if published else None,
        "official_grade": (source.get("official_grade") or source.get("grade")) if published else None,
        "publication_status": publication,
        "evidence_ids": _unique_strings(evidence_ids if isinstance(evidence_ids, list) else []),
    }


def update_profile_snapshot(
    service: Any,
    company_id: str,
    current_profile_version: str,
    provided_fields: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(provided_fields, dict) or not provided_fields:
        raise ServiceError("BID_PROFILE_UPDATE_EMPTY", "补充字段不能为空。", status_code=422)
    current = build_company_profile_snapshot(service, company_id)
    if current["profile_version"] != current_profile_version:
        raise ServiceError(
            "BID_PROFILE_UPDATE_VERSION_MISMATCH",
            "补充材料基于的画像版本不是当前版本。",
            details={"expected": current["profile_version"], "actual": current_profile_version},
            status_code=409,
        )

    context = service.store.read_company_context(company_id)
    fact_profile = deepcopy(context["fact_profile"])
    now = utc_now()
    digest = hashlib.sha256(json.dumps(provided_fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    evidence_id = f"user-supplied:{company_id}:{digest[:24]}"
    evidence_index = deepcopy(fact_profile.get("evidence_index") or {})
    evidence_index[evidence_id] = {
        "evidence_schema_version": "enterprise-profile-evidence/1.1.0",
        "evidence_id": evidence_id,
        "source_type": "user_upload",
        "source_dataset_id": "bid-decision-profile-supplement",
        "source_file": "bid-decision-user-input",
        "source_sheet": "bid-decision-user-input",
        "source_row_number": 1,
        "source_locator": "provided_fields",
        "announcement_unique_id": None,
        "project_number": None,
        "source_url": None,
        "record_time": {"published_at": None, "tender_end_at": None, "opening_at": None},
        "collected_at": now,
        "collected_at_status": "provided",
        "is_mock": False,
        "anonymized": False,
        "record_status": "valid",
        "quality_flags": ["user_supplied_for_bid_decision", "direct_task_use", "not_externally_verified"],
        "material_content_sha256": digest,
        "media_type": "application/json",
        "size_bytes": len(json.dumps(provided_fields, ensure_ascii=False).encode("utf-8")),
        "description": "投标决策资格项的用户补充内容，提交后直接用于对应资格项重新核验，不进入人工审核流程。",
    }
    fact = build_fact(
        enterprise=fact_profile["enterprise"],
        fact_type="enterprise_material",
        payload={
            "material_type": "bid_decision_profile_supplement",
            "document_hash": digest,
            "declared_fields": deepcopy(provided_fields),
            "review_status": "not_required",
        },
        source={
            "source_type": "user_upload",
            "source_data_category": "enterprise_material",
            "source_platform": "bid_decision_agent",
            "source_record_id": digest,
            "source_url": None,
            "dataset_id": "bid-decision-profile-supplement",
        },
        temporal={
            "published_at": None,
            "collected_at": now,
            "verified_at": None,
            "valid_from": None,
            "valid_until": None,
            "observed_at": now,
            "available_at": now,
            "availability_basis": "user_received_at",
            "availability_status": "known",
        },
        fact_status="active",
        verification_status="partially_verified",
        evidence_ids=[evidence_id],
        quality_flags=["user_supplied_for_bid_decision", "direct_task_use", "not_externally_verified", "eligibility_recheck_required"],
        created_from="bid_decision_profile_update_gateway",
        is_mock=False,
    )
    facts = deepcopy(fact_profile.get("facts") or [])
    facts.append(fact)
    fact_profile["facts"] = facts
    fact_profile["evidence_index"] = evidence_index
    fact_profile["as_of_date"] = date.today().isoformat()
    fact_profile["as_of_date_status"] = "provided_input"
    fact_profile["generated_at_utc"] = now
    fact_profile["conflicts"] = detect_fact_conflicts(facts)
    view, view_warnings = build_fact_view(facts, fact_profile["as_of_date"])
    fact_profile["fact_view"] = view
    fact_profile["fact_summary"], fact_profile["source_summary"] = _summaries(facts)
    fact_profile["warnings"] = _unique_strings(list(fact_profile.get("warnings") or []) + view_warnings + ["bid_decision_user_supplement_directly_applied"])
    quality = deepcopy(fact_profile.get("data_quality_summary") or {})
    quality["time_view_included_fact_count"] = len(view.get("included_fact_ids") or [])
    quality["time_view_excluded_fact_count"] = len(view.get("excluded_fact_ids") or [])
    quality["unresolved_conflict_count"] = len(fact_profile["conflicts"])
    quality["missing_fact_types"] = [item for item in quality.get("missing_fact_types") or [] if item != "enterprise_material"]
    fact_profile["data_quality_summary"] = quality
    assert_valid_fact_profile(fact_profile)
    service.store.write_profile(
        company_id,
        "fact",
        fact_profile,
        update_source="BID_DECISION_PROFILE_UPDATE_GATEWAY",
        updated_fields=sorted(str(key) for key in provided_fields),
        actor={"actor_type": "user", "actor_id": "bid-decision-agent"},
    )
    return build_company_profile_snapshot(service, company_id)
