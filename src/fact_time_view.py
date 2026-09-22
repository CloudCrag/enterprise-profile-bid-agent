"""Build deterministic as-of fact views without deleting normalized facts."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _parse_as_business_time(value: Any, *, date_at_end: bool) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if "T" not in text:
            parsed_date = date.fromisoformat(text)
            selected_time = time.max if date_at_end else time.min
            return datetime.combine(parsed_date, selected_time, tzinfo=BUSINESS_TIMEZONE)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE)


def as_of_cutoff(as_of_date: str) -> datetime:
    parsed = _parse_as_business_time(as_of_date, date_at_end=True)
    if parsed is None:
        raise ValueError(f"Invalid as_of_date: {as_of_date}")
    return parsed


def available_time(value: Any) -> datetime | None:
    return _parse_as_business_time(value, date_at_end=True)


def build_fact_view(facts: list[dict[str, Any]], as_of_date: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Classify every fact into an included or excluded time-view bucket."""
    all_ids = [str(fact.get("fact_id")) for fact in facts if fact.get("fact_id")]
    if as_of_date is None:
        return (
            {
                "as_of_date": None,
                "business_timezone": "Asia/Shanghai",
                "view_mode": "all_known_facts",
                "included_fact_ids": all_ids,
                "excluded_fact_ids": [],
                "excluded_summary": {
                    "future_available_fact_count": 0,
                    "unknown_availability_fact_count": 0,
                    "conflicting_availability_fact_count": 0,
                    "invalid_availability_fact_count": 0,
                },
                "exclusion_reasons": {},
            },
            [],
        )

    cutoff = as_of_cutoff(as_of_date)
    included: list[str] = []
    excluded: list[str] = []
    reasons: dict[str, str] = {}
    counts = {
        "future_available_fact_count": 0,
        "unknown_availability_fact_count": 0,
        "conflicting_availability_fact_count": 0,
        "invalid_availability_fact_count": 0,
    }
    warnings: list[dict[str, Any]] = []

    for fact in facts:
        fact_id = fact.get("fact_id")
        if not fact_id:
            continue
        temporal = fact.get("temporal") if isinstance(fact.get("temporal"), dict) else {}
        status = temporal.get("availability_status")
        raw = temporal.get("available_at")
        if status == "conflicting":
            excluded.append(fact_id)
            reasons[fact_id] = "availability_time_conflicting"
            counts["conflicting_availability_fact_count"] += 1
            warnings.append({
                "code": "fact_excluded_from_time_view",
                "fact_id": fact_id,
                "reason": "availability_time_conflicting",
            })
            continue
        if status != "known" or raw is None:
            excluded.append(fact_id)
            reasons[fact_id] = "availability_time_unknown"
            counts["unknown_availability_fact_count"] += 1
            warnings.append({
                "code": "fact_excluded_from_time_view",
                "fact_id": fact_id,
                "reason": "availability_time_unknown",
            })
            continue
        parsed = available_time(raw)
        if parsed is None:
            excluded.append(fact_id)
            reasons[fact_id] = "availability_time_invalid"
            counts["invalid_availability_fact_count"] += 1
            warnings.append({
                "code": "fact_excluded_from_time_view",
                "fact_id": fact_id,
                "reason": "availability_time_invalid",
            })
            continue
        if parsed > cutoff:
            excluded.append(fact_id)
            reasons[fact_id] = "future_available_relative_to_as_of_date"
            counts["future_available_fact_count"] += 1
            continue
        included.append(fact_id)

    return (
        {
            "as_of_date": as_of_date,
            "business_timezone": "Asia/Shanghai",
            "view_mode": "as_of",
            "included_fact_ids": included,
            "excluded_fact_ids": excluded,
            "excluded_summary": counts,
            "exclusion_reasons": reasons,
        },
        warnings,
    )
