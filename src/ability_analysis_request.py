"""Build and validate a constrained AI capability-analysis request.

This module prepares a future AI envelope only. It never calls a model.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .capability_evidence_policy import load_capability_evidence_policy
from .capability_validator import assert_valid_capability_profile, validate_fact_tag_pair
from .enterprise_capability_profile import (
    ANALYSIS_REQUEST_SCHEMA_VERSION,
    CAPABILITY_TYPES,
    EVIDENCE_POLICY_VERSION,
    canonical_json,
    tag_profile_content_hash,
)
from .enterprise_fact_profile import fact_profile_content_hash
from .errors import InputDataError

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_capability_analysis_request.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def analysis_request_content_hash(request: dict[str, Any]) -> str:
    payload = {key: value for key, value in request.items() if key != "request_content_hash"}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def validate_analysis_request(request: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(request), key=lambda item: (list(item.absolute_path), item.message)):
        issues.append({
            "code": "capability_analysis_request_invalid",
            "field_path": ".".join(str(item) for item in error.absolute_path) or "$",
            "message": error.message,
        })
    if not isinstance(request, dict):
        return issues
    if request.get("request_content_hash") != analysis_request_content_hash(request):
        issues.append({"code": "capability_analysis_request_hash_mismatch", "field_path": "request_content_hash", "message": "request_content_hash does not match normalized request content"})
    baseline = request.get("deterministic_baseline") if isinstance(request.get("deterministic_baseline"), dict) else {}
    if baseline.get("capability_profile_id") != request.get("capability_profile_id"):
        issues.append({"code": "capability_analysis_baseline_id_mismatch", "field_path": "deterministic_baseline.capability_profile_id", "message": "deterministic baseline ID differs from request dependency"})
    if baseline.get("capability_content_hash") != request.get("capability_content_hash"):
        issues.append({"code": "capability_analysis_baseline_hash_mismatch", "field_path": "deterministic_baseline.capability_content_hash", "message": "deterministic baseline hash differs from request dependency"})
    try:
        policy = load_capability_evidence_policy()
    except InputDataError as exc:
        issues.append({"code": "capability_analysis_policy_invalid", "field_path": "evidence_policy_version", "message": str(exc)})
        policy = None
    if request.get("evidence_policy_version") != EVIDENCE_POLICY_VERSION:
        issues.append({"code": "capability_analysis_policy_version_mismatch", "field_path": "evidence_policy_version", "message": "request does not use the current evidence policy version"})
    if policy is not None:
        for capability_type in request.get("requested_capability_types", []) if isinstance(request.get("requested_capability_types"), list) else []:
            if capability_type not in policy["domains"]:
                issues.append({"code": "capability_analysis_policy_domain_missing", "field_path": "requested_capability_types", "message": str(capability_type)})
    return issues


def assert_valid_analysis_request(request: Any) -> None:
    issues = validate_analysis_request(request)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def build_capability_analysis_request(
    fact_profile: dict[str, Any],
    tag_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    *,
    requested_capability_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    validate_fact_tag_pair(fact_profile, tag_profile)
    assert_valid_capability_profile(capability_profile, fact_profile=fact_profile, tag_profile=tag_profile)
    policy = load_capability_evidence_policy()
    requested = list(dict.fromkeys(requested_capability_types or [d["capability_type"] for d in capability_profile["capability_domains"]]))
    invalid = [item for item in requested if item not in CAPABILITY_TYPES or item not in policy["domains"]]
    if invalid:
        raise InputDataError(f"Unsupported requested capability types: {invalid}")
    included = set((fact_profile.get("fact_view") or {}).get("included_fact_ids", []))
    allowed_facts = [deepcopy(fact) for fact in fact_profile.get("facts", []) if fact.get("fact_id") in included]
    allowed_tags = deepcopy(tag_profile.get("tags", []))
    evidence_ids = {eid for fact in allowed_facts for eid in fact.get("evidence_ids", [])}
    evidence_ids.update(eid for tag in allowed_tags for eid in tag.get("evidence_ids", []))
    full_index = fact_profile.get("evidence_index") or {}
    evidence_index = {eid: deepcopy(full_index[eid]) for eid in sorted(evidence_ids) if eid in full_index}
    fact_hash = fact_profile_content_hash(fact_profile)
    tag_hash = tag_profile_content_hash(tag_profile)
    request = {
        "request_schema_version": ANALYSIS_REQUEST_SCHEMA_VERSION,
        "fact_profile_content_hash": fact_hash,
        "tag_profile_content_hash": tag_hash,
        "capability_profile_id": capability_profile["capability_profile_id"],
        "capability_content_hash": capability_profile["capability_content_hash"],
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "enterprise": deepcopy(fact_profile["enterprise"]),
        "as_of_date": fact_profile.get("as_of_date"),
        "requested_capability_types": requested,
        "allowed_facts": allowed_facts,
        "allowed_tags": allowed_tags,
        "evidence_index": evidence_index,
        "deterministic_baseline": {
            "capability_profile_id": capability_profile["capability_profile_id"],
            "capability_content_hash": capability_profile["capability_content_hash"],
            "capability_domains": deepcopy(capability_profile["capability_domains"]),
            "capability_summary": deepcopy(capability_profile["capability_summary"]),
        },
        "analysis_constraints": {
            "may_create_enterprise_facts": False,
            "may_infer_user_preferences": False,
            "may_score_enterprise": False,
            "may_recommend_projects": False,
            "must_reference_fact_ids": True,
            "must_reference_evidence_ids": True,
            "must_separate_fact_inference_unknown": True,
        },
    }
    request["request_content_hash"] = analysis_request_content_hash(request)
    ordered = {
        "request_schema_version": request["request_schema_version"],
        "request_content_hash": request["request_content_hash"],
        **{key: value for key, value in request.items() if key not in {"request_schema_version", "request_content_hash"}},
    }
    assert_valid_analysis_request(ordered)
    return ordered
