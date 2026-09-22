"""Load and apply the fixed capability-domain evidence policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .enterprise_capability_profile import CAPABILITY_TYPES, EVIDENCE_POLICY_VERSION
from .errors import InputDataError

_ROOT = Path(__file__).resolve().parents[1]
_POLICY_PATH = _ROOT / "config" / "enterprise_capability_evidence_policy.json"


def load_capability_evidence_policy(path: str | Path | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path is not None else _POLICY_PATH
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputDataError(f"Cannot load capability evidence policy: {policy_path}") from exc
    if policy.get("schema_version") != EVIDENCE_POLICY_VERSION:
        raise InputDataError("Unsupported capability evidence policy version")
    domains = policy.get("domains")
    if not isinstance(domains, dict) or set(domains) != CAPABILITY_TYPES:
        raise InputDataError("Capability evidence policy must define exactly the fixed eight capability domains")
    required = {
        "allowed_fact_types",
        "allowed_other_enterprise_fact_subtypes",
        "allowed_tag_codes",
        "allowed_observation_types",
        "allowed_candidate_kinds",
    }
    for capability_type, item in domains.items():
        if not isinstance(item, dict) or not required.issubset(item):
            raise InputDataError(f"Incomplete evidence policy for {capability_type}")
        for key in required:
            values = item.get(key)
            if not isinstance(values, list) or len(values) != len(set(values)):
                raise InputDataError(f"Policy field {capability_type}.{key} must be a unique list")
    return policy


def domain_policy(capability_type: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    active = policy or load_capability_evidence_policy()
    try:
        return active["domains"][capability_type]
    except KeyError as exc:
        raise InputDataError(f"No evidence policy for capability domain: {capability_type}") from exc


def fact_allowed_for_domain(fact: dict[str, Any], capability_type: str, policy: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    item = domain_policy(capability_type, policy)
    fact_type = fact.get("fact_type")
    if fact_type not in item["allowed_fact_types"]:
        return False, "capability_analysis_fact_type_not_allowed"
    if fact_type == "other_enterprise_fact":
        subtype = (fact.get("payload") or {}).get("subtype")
        if subtype not in item["allowed_other_enterprise_fact_subtypes"]:
            return False, "capability_analysis_other_fact_subtype_not_allowed"
    return True, None
