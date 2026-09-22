"""Build enterprise fact profiles and express unresolved multi-source conflicts."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
from typing import Any

from .enterprise_fact_profile import FACT_PROFILE_SCHEMA_VERSION, canonical_json
from .errors import InputDataError
from .evidence import validate_evidence_index
from .fact_time_view import build_fact_view
from .fact_validator import assert_valid_fact_profile
from .legacy_input_to_facts import convert_legacy_input_to_facts


def _validate_as_of_date(value: Any, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputDataError(f"as_of_date_invalid: {source} as_of_date must use YYYY-MM-DD")
    text = value.strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise InputDataError(f"as_of_date_invalid: {source} as_of_date must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise InputDataError(f"as_of_date_invalid: {source} as_of_date is not a valid ISO date: {text}") from exc
    return parsed.isoformat()


def resolve_as_of_date(input_data: dict[str, Any], cli_as_of_date: str | None = None) -> tuple[str | None, str]:
    """Resolve the explicit view cutoff without deriving it from fact timestamps."""
    input_value = _validate_as_of_date(input_data.get("as_of_date"), source="input")
    cli_value = _validate_as_of_date(cli_as_of_date, source="CLI")
    if cli_value and input_value and cli_value != input_value:
        raise InputDataError(
            f"as_of_date_conflict: CLI as_of_date {cli_value} differs from input as_of_date {input_value}"
        )
    if cli_value:
        return cli_value, "provided_cli"
    if input_value:
        return input_value, "provided_input"
    return None, "not_provided"


def detect_fact_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect basic conflicts without choosing a preferred source."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if fact.get("fact_type") != "qualification":
            continue
        payload = fact.get("payload") if isinstance(fact.get("payload"), dict) else {}
        name = str(payload.get("name") or "").strip().casefold()
        if name:
            groups[name].append(fact)
    conflicts: list[dict[str, Any]] = []
    for name, group in groups.items():
        values = {
            str((fact.get("payload") or {}).get("valid_until"))
            for fact in group if (fact.get("payload") or {}).get("valid_until")
        }
        if len(group) < 2 or len(values) < 2:
            continue
        fact_ids = sorted(fact["fact_id"] for fact in group)
        basis = {"type": "qualification_valid_until_mismatch", "name": name, "fact_ids": fact_ids}
        digest = hashlib.sha256(canonical_json(basis)).hexdigest()
        conflict_id = f"conflict:qualification:{digest[:24]}"
        for fact in group:
            fact["fact_status"] = "conflicted"
            fact["conflict_group_id"] = conflict_id
            fact["quality_flags"] = list(dict.fromkeys(
                fact.get("quality_flags", []) + ["qualification_valid_until_conflict"]
            ))
        conflicts.append({
            "conflict_id": conflict_id,
            "conflict_type": "qualification_valid_until_mismatch",
            "fact_ids": fact_ids,
            "status": "unresolved",
            "resolution": None,
            "quality_flags": ["manual_or_agent_verification_required"],
            "created_at_utc": None,
        })
    return conflicts


def _summaries(facts: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    statuses = Counter(fact.get("fact_status") for fact in facts)
    types = Counter(fact.get("fact_type") for fact in facts)
    sources = Counter((fact.get("source") or {}).get("source_type") for fact in facts)
    categories = Counter((fact.get("source") or {}).get("source_data_category") for fact in facts)
    datasets = Counter(
        (fact.get("source") or {}).get("dataset_id")
        for fact in facts if (fact.get("source") or {}).get("dataset_id")
    )
    return (
        {
            "total_fact_count": len(facts),
            "active_fact_count": statuses["active"],
            "expired_fact_count": statuses["expired"],
            "ambiguous_fact_count": statuses["ambiguous"],
            "conflicted_fact_count": statuses["conflicted"],
            "fact_status_counts": dict(sorted((str(k), v) for k, v in statuses.items() if k)),
            "fact_type_counts": dict(sorted((str(k), v) for k, v in types.items() if k)),
        },
        {
            "source_type_counts": dict(sorted((str(k), v) for k, v in sources.items() if k)),
            "source_data_category_counts": dict(sorted((str(k), v) for k, v in categories.items() if k)),
            "dataset_id_counts": dict(sorted(datasets.items())),
        },
    )


def build_fact_profile(
    input_data: dict[str, Any],
    *,
    as_of_date: str | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    if not isinstance(input_data, dict):
        raise InputDataError("Input JSON root must be an object")
    enterprise = input_data.get("enterprise")
    if not isinstance(enterprise, dict):
        raise InputDataError("Root field 'enterprise' must be an object")
    evidence_index = deepcopy(input_data.get("evidence_index") or {})
    if not isinstance(evidence_index, dict):
        raise InputDataError("Root field 'evidence_index' must be an object when provided")

    resolved_as_of_date, as_of_status = resolve_as_of_date(input_data, as_of_date)
    facts = convert_legacy_input_to_facts(input_data, resolved_as_of_date)
    conflicts = detect_fact_conflicts(facts)
    fact_view, view_warnings = build_fact_view(facts, resolved_as_of_date)
    fact_summary, source_summary = _summaries(facts)

    evidence_issues = validate_evidence_index(evidence_index)
    fact_refs = list(dict.fromkeys(eid for fact in facts for eid in fact.get("evidence_ids", [])))
    missing_refs = [eid for eid in fact_refs if eid not in evidence_index]
    warnings = list(evidence_issues)
    warnings.extend({"code": "fact_evidence_reference_unresolved", "evidence_id": eid} for eid in missing_refs)
    warnings.extend(view_warnings)
    if conflicts:
        warnings.append({"code": "unresolved_fact_conflicts", "conflict_count": len(conflicts)})

    meta = input_data.get("_meta") if isinstance(input_data.get("_meta"), dict) else {}
    profile = {
        "fact_profile_schema_version": FACT_PROFILE_SCHEMA_VERSION,
        "enterprise": {
            "name": enterprise.get("name"),
            "unified_social_credit_code": enterprise.get("unified_social_credit_code"),
            "internal_enterprise_id": enterprise.get("internal_enterprise_id"),
        },
        "as_of_date": resolved_as_of_date,
        "as_of_date_status": as_of_status,
        "facts": facts,
        "fact_view": fact_view,
        "fact_summary": fact_summary,
        "conflicts": conflicts,
        "evidence_index": evidence_index,
        "source_summary": source_summary,
        "warnings": warnings,
        "data_quality_summary": {
            "all_fact_evidence_resolved": not missing_refs,
            "unresolved_fact_evidence_count": len(missing_refs),
            "facts_without_collection_time_count": sum(
                1 for fact in facts if not (fact.get("temporal") or {}).get("collected_at")
            ),
            "facts_without_verification_time_count": sum(
                1 for fact in facts if not (fact.get("temporal") or {}).get("verified_at")
            ),
            "facts_without_known_availability_count": sum(
                1 for fact in facts if (fact.get("temporal") or {}).get("availability_status") != "known"
            ),
            "time_view_included_fact_count": len(fact_view["included_fact_ids"]),
            "time_view_excluded_fact_count": len(fact_view["excluded_fact_ids"]),
            "unresolved_conflict_count": len(conflicts),
            "contains_mock_data": bool(
                meta.get("mock_data") or meta.get("anonymized") or any(fact.get("is_mock") for fact in facts)
            ),
            "contains_anonymized_data": bool(meta.get("anonymized")),
            "missing_fact_types": sorted({
                "business_registration", "qualification", "personnel", "personnel_certificate",
                "performance", "bid_participation", "bid_award", "fulfillment",
                "buyer_relationship", "risk_penalty_credit", "enterprise_material",
                "other_enterprise_fact",
            } - {fact.get("fact_type") for fact in facts}),
        },
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
    }
    assert_valid_fact_profile(profile)
    return profile
