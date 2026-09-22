"""Decision-profile context dependency diagnostics without weakening validation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.enterprise_fact_profile import fact_profile_content_hash


def decision_context_diagnostic(
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    current = {
        "fact_profile_schema_version": fact_profile.get("fact_profile_schema_version"),
        "fact_profile_content_hash": fact_profile_content_hash(fact_profile),
        "capability_profile_schema_version": capability_profile.get("capability_profile_schema_version"),
        "capability_profile_id": capability_profile.get("capability_profile_id"),
        "capability_profile_content_hash": capability_profile.get("capability_content_hash"),
    }
    recorded = deepcopy((decision_profile or {}).get("confirmation_context_dependencies") or {})
    mismatches = [
        {
            "field": key,
            "recorded": recorded.get(key),
            "current": value,
        }
        for key, value in current.items()
        if recorded.get(key) != value
    ] if decision_profile else []
    confirmed_fields = [
        {
            "field_code": item.get("field_code"),
            "field_name": item.get("field_name"),
            "value": deepcopy(item.get("value")),
            "last_user_update": deepcopy(item.get("last_user_update")),
        }
        for item in (decision_profile or {}).get("decision_fields") or []
        if item.get("confirmation_status") == "confirmed"
    ]
    return {
        "is_stale": bool(mismatches),
        "decision_profile_id": (decision_profile or {}).get("decision_profile_id"),
        "decision_profile_content_hash": (decision_profile or {}).get("decision_profile_content_hash"),
        "recorded_dependencies": recorded,
        "current_dependencies": current,
        "mismatches": mismatches,
        "confirmed_decision_fields": confirmed_fields,
    }
