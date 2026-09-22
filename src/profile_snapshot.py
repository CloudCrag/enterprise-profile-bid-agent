"""Build deterministic snapshots containing the current fact view and 60 tags."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from .enterprise_fact_profile import FACT_PROFILE_SCHEMA_VERSION, fact_profile_content_hash
from .errors import InputDataError
from .fact_validator import assert_valid_fact_profile
from .tag_profile_generator import TAG_SET_SCHEMA_VERSION

SNAPSHOT_SCHEMA_VERSION = "company-profile-snapshot/3.1.0"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stable_enterprise_id(enterprise: dict[str, Any]) -> str:
    raw = enterprise.get("unified_social_credit_code") or enterprise.get("name")
    if not isinstance(raw, str) or not raw.strip():
        raise InputDataError("Enterprise name or unified social credit code is required for a stable snapshot ID")
    return re.sub(r"[^0-9A-Za-z._-]+", "-", raw.strip()).strip("-") or "enterprise"


def _included_ids(fact_profile: dict[str, Any]) -> set[str]:
    view = fact_profile.get("fact_view") if isinstance(fact_profile.get("fact_view"), dict) else {}
    return {str(item) for item in view.get("included_fact_ids", [])}


def _validate_pair(fact_profile: dict[str, Any], tag_profile: dict[str, Any]) -> None:
    assert_valid_fact_profile(fact_profile)
    if not isinstance(tag_profile, dict) or tag_profile.get("tag_set_schema_version") != TAG_SET_SCHEMA_VERSION:
        raise InputDataError("Tag input must be a formal 60-tag profile generated from facts")
    if not isinstance(tag_profile.get("tags"), list) or len(tag_profile["tags"]) != 60:
        raise InputDataError("Tag profile must contain exactly 60 tags")
    if fact_profile.get("enterprise") != tag_profile.get("enterprise"):
        raise InputDataError("Fact profile and tag profile enterprise identifiers do not match")
    expected_hash = fact_profile_content_hash(fact_profile)
    if tag_profile.get("source_fact_profile_content_hash") != expected_hash:
        raise InputDataError("Tag profile was not generated from the supplied fact profile content")
    evidence_index = fact_profile.get("evidence_index") or {}
    allowed_fact_ids = _included_ids(fact_profile)
    for tag in tag_profile["tags"]:
        for evidence_id in tag.get("evidence_ids", []):
            if evidence_id not in evidence_index:
                raise InputDataError(f"Tag evidence reference is missing from fact profile: {evidence_id}")
        supporting = tag.get("supporting_facts") if isinstance(tag.get("supporting_facts"), dict) else {}
        for fact_id in supporting.get("fact_ids", []):
            if fact_id not in allowed_fact_ids:
                raise InputDataError(f"Tag fact reference is outside the active fact view: {fact_id}")


def _active_fact_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(fact.get("fact_status") for fact in facts)
    types = Counter(fact.get("fact_type") for fact in facts)
    return {
        "total_fact_count": len(facts),
        "fact_status_counts": dict(sorted((str(k), v) for k, v in statuses.items() if k)),
        "fact_type_counts": dict(sorted((str(k), v) for k, v in types.items() if k)),
    }


def _active_source_summary(facts: list[dict[str, Any]]) -> dict[str, Any]:
    source_types = Counter((fact.get("source") or {}).get("source_type") for fact in facts)
    categories = Counter((fact.get("source") or {}).get("source_data_category") for fact in facts)
    return {
        "source_type_counts": dict(sorted((str(k), v) for k, v in source_types.items() if k)),
        "source_data_category_counts": dict(sorted((str(k), v) for k, v in categories.items() if k)),
    }


def build_profile_snapshot(
    fact_profile: dict[str, Any],
    tag_profile: dict[str, Any],
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    _validate_pair(fact_profile, tag_profile)
    included_ids = _included_ids(fact_profile)
    active_facts = [
        deepcopy(fact) for fact in fact_profile.get("facts", [])
        if fact.get("fact_id") in included_ids
    ]
    active_conflicts = [
        deepcopy(conflict) for conflict in fact_profile.get("conflicts", [])
        if set(conflict.get("fact_ids", [])) and set(conflict.get("fact_ids", [])).issubset(included_ids)
    ]
    active_evidence_ids = {
        evidence_id
        for fact in active_facts for evidence_id in fact.get("evidence_ids", [])
    }
    active_evidence_ids.update(
        evidence_id
        for tag in tag_profile.get("tags", []) for evidence_id in tag.get("evidence_ids", [])
    )
    full_evidence_index = fact_profile.get("evidence_index") or {}
    active_evidence_index = {
        evidence_id: deepcopy(full_evidence_index[evidence_id])
        for evidence_id in sorted(active_evidence_ids) if evidence_id in full_evidence_index
    }
    view = fact_profile.get("fact_view") or {}
    excluded_summary = deepcopy(view.get("excluded_summary", {}))
    active_view_summary = {
        "as_of_date": fact_profile.get("as_of_date"),
        "business_timezone": view.get("business_timezone"),
        "view_mode": view.get("view_mode"),
        "included_fact_count": len(active_facts),
        "excluded_fact_count": len(view.get("excluded_fact_ids", [])),
        **excluded_summary,
    }
    payload = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "enterprise": deepcopy(fact_profile["enterprise"]),
        "as_of_date": fact_profile.get("as_of_date"),
        "as_of_date_status": fact_profile.get("as_of_date_status"),
        "schema_versions": {
            "enterprise_fact_profile": FACT_PROFILE_SCHEMA_VERSION,
            "enterprise_profile_tags": TAG_SET_SCHEMA_VERSION,
            "tag_catalog": tag_profile.get("tag_catalog_version"),
            "evidence": "enterprise-profile-evidence/1.1.0",
        },
        "enterprise_fact_view_content_hash": hashlib.sha256(_canonical({
            "enterprise": fact_profile.get("enterprise"),
            "as_of_date": fact_profile.get("as_of_date"),
            "included_fact_ids": sorted(included_ids),
            "facts": active_facts,
            "conflicts": active_conflicts,
            "evidence_index": active_evidence_index,
        })).hexdigest(),
        "fact_store_summary": {
            "total_fact_count": len(fact_profile.get("facts", [])),
        },
        "active_fact_view_summary": active_view_summary,
        "fact_scope": {
            "fact_count": len(active_facts),
            "fact_type_count": len({fact.get("fact_type") for fact in active_facts}),
            "conflict_count": len(active_conflicts),
        },
        "tag_scope": deepcopy(tag_profile["tag_scope"]),
        "facts": active_facts,
        "fact_summary": _active_fact_summary(active_facts),
        "conflicts": active_conflicts,
        "tags": deepcopy(tag_profile["tags"]),
        "tag_coverage_summary": deepcopy(tag_profile["tag_coverage_summary"]),
        "evidence_index": active_evidence_index,
        "evidence_summary": deepcopy(tag_profile.get("evidence_summary", {})),
        "source_summary": _active_source_summary(active_facts),
        "warnings": deepcopy(fact_profile.get("warnings", [])) + deepcopy(tag_profile.get("warnings", [])),
        "data_quality_summary": {
            "fact_profile": deepcopy(fact_profile.get("data_quality_summary", {})),
            "tag_profile": deepcopy(tag_profile.get("data_quality_summary", {})),
        },
        "data_classification": {
            "contains_mock_data": bool((fact_profile.get("data_quality_summary") or {}).get("contains_mock_data")),
            "contains_anonymized_data": bool((fact_profile.get("data_quality_summary") or {}).get("contains_anonymized_data")),
        },
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    snapshot = deepcopy(payload)
    snapshot["snapshot_id"] = f"profile:{_stable_enterprise_id(fact_profile['enterprise'])}:{digest[:16]}"
    snapshot["profile_content_hash"] = digest
    snapshot["generated_at_utc"] = generated_at_utc or datetime.now(timezone.utc).isoformat()
    return snapshot
