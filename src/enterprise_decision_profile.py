"""Stable identifiers, field catalog, and hashes for explicit enterprise decision profiles."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .enterprise_capability_profile import canonical_json, stable_enterprise_id
from .errors import InputDataError

DECISION_PROFILE_SCHEMA_VERSION = "enterprise-decision-profile/1.0.0"
DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION = "enterprise-decision-confirmation-input/1.0.0"
DECISION_FIELD_CATALOG_VERSION = "enterprise-decision-field-catalog/1.0.0"
GENERATION_MODE = "explicit_user_confirmation"

DECISION_FIELDS = (
    ("strategic_industries", "战略行业"),
    ("strategic_regions", "战略地区"),
    ("budget_preference", "预算偏好"),
    ("procurement_method_preferences", "招标方式偏好"),
    ("consortium_acceptance", "是否接受联合体"),
    ("risk_preference", "风险偏好"),
    ("max_concurrent_projects", "同时可投入项目数量"),
    ("personnel_resource_constraints", "人员资源约束"),
    ("explicit_exclusions", "明确排除项"),
    ("key_buyers", "重点采购人"),
    ("current_business_goals", "当前经营目标"),
)
DECISION_FIELD_CODES = {code for code, _ in DECISION_FIELDS}
DECISION_FIELD_NAMES = dict(DECISION_FIELDS)

_LIST_VALUE_KEYS = {
    "strategic_industries": "industries",
    "strategic_regions": "regions",
    "procurement_method_preferences": "methods",
    "personnel_resource_constraints": "constraints",
    "explicit_exclusions": "items",
    "key_buyers": "buyers",
    "current_business_goals": "goals",
}


def load_decision_field_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "enterprise_decision_fields.json"
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputDataError(f"Cannot load enterprise decision field catalog: {catalog_path}") from exc
    expected = [{"field_code": code, "field_name": name} for code, name in DECISION_FIELDS]
    if data.get("schema_version") != DECISION_FIELD_CATALOG_VERSION or data.get("fields") != expected:
        raise InputDataError("Enterprise decision field catalog must contain exactly the fixed eleven fields")
    return data


def normalize_decision_value(field_code: str, value: Any) -> Any:
    """Normalize unordered list fields without inventing or changing business values."""
    result = deepcopy(value)
    list_key = _LIST_VALUE_KEYS.get(field_code)
    if list_key and isinstance(result, dict) and isinstance(result.get(list_key), list):
        result[list_key] = sorted(result[list_key], key=lambda item: canonical_json(item))
    return result


def normalize_confirmation_updates(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for update in updates:
        item = deepcopy(update)
        if item.get("action") == "set" and "value" in item:
            item["value"] = normalize_decision_value(str(item.get("field_code")), item["value"])
        normalized.append(item)
    return sorted(normalized, key=lambda item: str(item.get("field_code")))


def confirmation_input_content_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "confirmation_input_schema_version": value.get("confirmation_input_schema_version"),
        "enterprise": deepcopy(value.get("enterprise")),
        "base_decision_profile_id": value.get("base_decision_profile_id"),
        "base_decision_profile_content_hash": value.get("base_decision_profile_content_hash"),
        "confirmation_actor": deepcopy(value.get("confirmation_actor")),
        "confirmed_at": value.get("confirmed_at"),
        "updates": normalize_confirmation_updates(value.get("updates") or []),
    }


def confirmation_input_content_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(confirmation_input_content_payload(value))).hexdigest()


def finalize_confirmation_input(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["updates"] = normalize_confirmation_updates(result.get("updates") or [])
    result["confirmation_input_content_hash"] = confirmation_input_content_hash(result)
    return result


def decision_profile_content_payload(profile: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "decision_profile_schema_version",
        "profile_version",
        "previous_decision_profile_id",
        "previous_decision_profile_content_hash",
        "enterprise",
        "as_of_date",
        "confirmation_context_dependencies",
        "generation",
        "decision_fields",
        "decision_summary",
        "warnings",
    )
    return {key: deepcopy(profile.get(key)) for key in keys}


def decision_profile_content_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(decision_profile_content_payload(profile))).hexdigest()


def decision_profile_id(profile: dict[str, Any], digest: str | None = None) -> str:
    content_digest = digest or decision_profile_content_hash(profile)
    return f"enterprise-decision-profile:{stable_enterprise_id(profile.get('enterprise') or {})}:{content_digest[:16]}"


def finalize_decision_profile(profile: dict[str, Any], *, generated_at_utc: str) -> dict[str, Any]:
    result = deepcopy(profile)
    digest = decision_profile_content_hash(result)
    result["decision_profile_content_hash"] = digest
    result["decision_profile_id"] = decision_profile_id(result, digest)
    result["generated_at_utc"] = generated_at_utc
    return result
