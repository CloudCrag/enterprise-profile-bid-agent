"""Stable, unapplied decision-profile update candidates."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any

from ..enterprise_capability_profile import canonical_json, stable_enterprise_id

DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION = "enterprise-decision-profile-update-candidate/1.0.0"
DECISION_UPDATE_CANDIDATE_TYPE = "decision_confirmation_input"
DECISION_UPDATE_CANDIDATE_STATUS = "ready_for_decision_profile_update"


def decision_candidate_content_payload(
    candidate: dict[str, Any], *, enterprise: dict[str, Any], response_item: dict[str, Any], question_item: dict[str, Any]
) -> dict[str, Any]:
    return {
        "enterprise_id": stable_enterprise_id(enterprise),
        "candidate_schema_version": candidate.get("candidate_schema_version"),
        "response_item_id": response_item.get("response_item_id"),
        "response_item_content_hash": response_item.get("response_item_content_hash"),
        "question_item_id": question_item.get("question_item_id"),
        "question_item_content_hash": question_item.get("question_item_content_hash"),
        "candidate_type": candidate.get("candidate_type"),
        "target_field": candidate.get("target_field"),
        "base_decision_profile_id": candidate.get("base_decision_profile_id"),
        "base_decision_profile_content_hash": candidate.get("base_decision_profile_content_hash"),
        "confirmation_input": deepcopy(candidate.get("confirmation_input")),
        "candidate_status": candidate.get("candidate_status"),
    }


def decision_candidate_content_hash(
    candidate: dict[str, Any], *, enterprise: dict[str, Any], response_item: dict[str, Any], question_item: dict[str, Any]
) -> str:
    return hashlib.sha256(canonical_json(decision_candidate_content_payload(
        candidate, enterprise=enterprise, response_item=response_item, question_item=question_item
    ))).hexdigest()


def decision_candidate_id(candidate: dict[str, Any], *, enterprise: dict[str, Any], digest: str) -> str:
    return f"enterprise-decision-profile-update-candidate:{stable_enterprise_id(enterprise)}:{digest[:24]}"


def build_decision_update_candidate(
    response_item: dict[str, Any], question_item: dict[str, Any], *, enterprise: dict[str, Any]
) -> dict[str, Any]:
    answer = response_item.get("answer") if isinstance(response_item.get("answer"), dict) else {}
    confirmation = answer.get("decision_confirmation_input") if isinstance(answer.get("decision_confirmation_input"), dict) else {}
    candidate = {
        "candidate_schema_version": DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION,
        "candidate_type": DECISION_UPDATE_CANDIDATE_TYPE,
        "target_field": question_item.get("target_code"),
        "base_decision_profile_id": confirmation.get("base_decision_profile_id"),
        "base_decision_profile_content_hash": confirmation.get("base_decision_profile_content_hash"),
        "confirmation_input": deepcopy(confirmation),
        "candidate_status": DECISION_UPDATE_CANDIDATE_STATUS,
    }
    digest = decision_candidate_content_hash(candidate, enterprise=enterprise, response_item=response_item, question_item=question_item)
    candidate["candidate_content_hash"] = digest
    candidate["candidate_id"] = decision_candidate_id(candidate, enterprise=enterprise, digest=digest)
    return candidate
