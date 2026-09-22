"""Core helpers for the enterprise fact-profile standard layer."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Iterable

from .errors import InputDataError

FACT_PROFILE_SCHEMA_VERSION = "enterprise-fact-profile/1.3.0"
FACT_TYPE_CATALOG_VERSION = "enterprise-fact-type-catalog/1.3.0"

FACT_TYPES = {
    "business_registration",
    "qualification",
    "personnel",
    "personnel_certificate",
    "performance",
    "bid_participation",
    "bid_award",
    "fulfillment",
    "buyer_relationship",
    "risk_penalty_credit",
    "enterprise_material",
    "other_enterprise_fact",
}
SOURCE_TYPES = {
    "crawler",
    "official_api",
    "external_data",
    "user_upload",
    "manual_review",
    "legacy_excel",
    "system_derived",
}
SOURCE_DATA_CATEGORIES = {
    "business_registration",
    "qualification_registry",
    "personnel_registry",
    "performance_record",
    "tender_notice",
    "award_notice",
    "fulfillment_record",
    "buyer_relationship",
    "risk_credit",
    "enterprise_material",
    "manual_review",
    "other",
}
AVAILABILITY_BASES = {
    "source_published_at",
    "source_collected_at",
    "source_observed_at",
    "user_received_at",
    "manual_verified_at",
    "upstream_provided",
    "unknown",
}
AVAILABILITY_STATUSES = {"known", "unknown", "conflicting"}
FACT_STATUSES = {
    "active",
    "expired",
    "revoked",
    "superseded",
    "ambiguous",
    "conflicted",
    "unknown",
}
VERIFICATION_STATUSES = {
    "verified",
    "partially_verified",
    "unverified",
    "rejected",
    "pending_review",
}
FACT_SCHEMA_VERSIONS = {fact_type: f"{fact_type.replace('_', '-')}-fact/1.0.0" for fact_type in FACT_TYPES}
FACT_SCHEMA_VERSIONS["other_enterprise_fact"] = "other-enterprise-fact/1.0.0"
FACT_SCHEMA_VERSIONS.update({
    "performance": "performance-fact/1.1.0",
    "bid_award": "bid-award-fact/1.1.0",
    "fulfillment": "fulfillment-fact/1.1.0",
})
_DATASET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def stable_enterprise_key(enterprise: dict[str, Any]) -> str:
    raw = enterprise.get("unified_social_credit_code") or enterprise.get("name")
    if not isinstance(raw, str) or not raw.strip():
        raise InputDataError("Enterprise name or unified social credit code is required")
    return raw.strip()


def validate_dataset_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or not _DATASET_RE.fullmatch(value.strip()):
        raise InputDataError("source dataset_id must be a stable non-empty identifier")
    return value.strip()


def stable_fact_id(
    *,
    enterprise: dict[str, Any],
    fact_type: str,
    payload: dict[str, Any],
    source: dict[str, Any],
    temporal: dict[str, Any],
    evidence_ids: Iterable[str],
) -> tuple[str, str]:
    """Return a deterministic fact ID and full normalized content digest."""
    basis = {
        "enterprise_key": stable_enterprise_key(enterprise),
        "fact_type": fact_type,
        "payload": payload,
        "source": {
            "source_type": source.get("source_type"),
            "source_data_category": source.get("source_data_category"),
            "source_platform": source.get("source_platform"),
            "source_record_id": source.get("source_record_id"),
            "source_url": source.get("source_url"),
            "dataset_id": source.get("dataset_id"),
        },
        "temporal": {
            "published_at": temporal.get("published_at"),
            "collected_at": temporal.get("collected_at"),
            "verified_at": temporal.get("verified_at"),
            "valid_from": temporal.get("valid_from"),
            "valid_until": temporal.get("valid_until"),
            "observed_at": temporal.get("observed_at"),
            "available_at": temporal.get("available_at"),
            "availability_basis": temporal.get("availability_basis"),
            "availability_status": temporal.get("availability_status"),
        },
        "evidence_ids": sorted({str(item) for item in evidence_ids if item}),
    }
    digest = hashlib.sha256(canonical_json(basis)).hexdigest()
    return f"fact:{fact_type}:{digest[:24]}", digest


def build_fact(
    *,
    enterprise: dict[str, Any],
    fact_type: str,
    payload: dict[str, Any],
    source: dict[str, Any],
    temporal: dict[str, Any] | None = None,
    fact_status: str = "active",
    verification_status: str = "unverified",
    evidence_ids: Iterable[str] | None = None,
    quality_flags: Iterable[str] | None = None,
    conflict_group_id: str | None = None,
    created_from: str = "normalized_input",
    is_mock: bool = False,
) -> dict[str, Any]:
    if fact_type not in FACT_TYPES:
        raise InputDataError(f"Unsupported fact_type: {fact_type}")
    if fact_status not in FACT_STATUSES:
        raise InputDataError(f"Unsupported fact_status: {fact_status}")
    if verification_status not in VERIFICATION_STATUSES:
        raise InputDataError(f"Unsupported verification_status: {verification_status}")
    if not isinstance(payload, dict) or not payload:
        raise InputDataError("A fact payload must be a non-empty object")
    source_copy = {
        "source_type": source.get("source_type"),
        "source_data_category": source.get("source_data_category"),
        "source_platform": source.get("source_platform"),
        "source_record_id": source.get("source_record_id"),
        "source_url": source.get("source_url"),
        "dataset_id": validate_dataset_id(source.get("dataset_id")),
        "provider_id": source.get("provider_id"),
        "api_id": source.get("api_id"),
        "api_call_id": source.get("api_call_id"),
        "raw_response_ref": source.get("raw_response_ref"),
    }
    if source_copy["source_type"] not in SOURCE_TYPES:
        raise InputDataError(f"Unsupported fact source_type: {source_copy['source_type']}")
    if source_copy["source_data_category"] not in SOURCE_DATA_CATEGORIES:
        raise InputDataError(f"Unsupported source_data_category: {source_copy['source_data_category']}")
    if source_copy["source_type"] == "system_derived" and created_from != "system_derived":
        raise InputDataError("system_derived facts must declare created_from=system_derived")
    temporal_copy = {
        "published_at": None,
        "collected_at": None,
        "verified_at": None,
        "valid_from": None,
        "valid_until": None,
        "observed_at": None,
        "available_at": None,
        "availability_basis": None,
        "availability_status": "unknown",
        **deepcopy(temporal or {}),
    }
    ids = list(dict.fromkeys(str(item) for item in (evidence_ids or []) if item))
    fact_id, content_hash = stable_fact_id(
        enterprise=enterprise,
        fact_type=fact_type,
        payload=payload,
        source=source_copy,
        temporal=temporal_copy,
        evidence_ids=ids,
    )
    fact = {
        "fact_id": fact_id,
        "fact_content_hash": content_hash,
        "fact_type": fact_type,
        "fact_schema_version": FACT_SCHEMA_VERSIONS[fact_type],
        "enterprise_key": {
            "unified_social_credit_code": enterprise.get("unified_social_credit_code"),
            "enterprise_name": enterprise.get("name"),
        },
        "payload": deepcopy(payload),
        "source": source_copy,
        "temporal": temporal_copy,
        "fact_status": fact_status,
        "verification_status": verification_status,
        "evidence_ids": ids,
        "quality_flags": list(dict.fromkeys(str(item) for item in (quality_flags or []) if item)),
        "conflict_group_id": conflict_group_id,
        "created_from": created_from,
        "is_mock": bool(is_mock),
    }
    from .fact_validator import assert_valid_fact
    assert_valid_fact(fact)
    return fact


def fact_profile_content_hash(profile: dict[str, Any]) -> str:
    """Hash stable business content, including the selected time view."""
    payload = {
        "fact_profile_schema_version": profile.get("fact_profile_schema_version"),
        "enterprise": profile.get("enterprise"),
        "as_of_date": profile.get("as_of_date"),
        "as_of_date_status": profile.get("as_of_date_status"),
        "facts": profile.get("facts"),
        "fact_view": profile.get("fact_view"),
        "fact_summary": profile.get("fact_summary"),
        "conflicts": profile.get("conflicts"),
        "evidence_index": profile.get("evidence_index"),
        "source_summary": profile.get("source_summary"),
        "warnings": profile.get("warnings"),
        "data_quality_summary": profile.get("data_quality_summary"),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()
