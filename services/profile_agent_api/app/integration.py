"""Stable external-input adapters for fact candidates and structured task requirements."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_FACT_FORMAT_CHECKER = FormatChecker()

@_FACT_FORMAT_CHECKER.checks("iso-date-time")
def _is_iso_date_time(value: object) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True

from src.enterprise_capability_profile import canonical_json
from src.enterprise_fact_profile import FACT_TYPES, build_fact
from src.profile_gap.request import TARGET_CODE_CATALOG

from ..langgraph.tools import stable_id
from .constants import ERROR_FACT_CANDIDATE_INVALID, ERROR_TASK_REQUIREMENTS_INVALID
from .errors import ServiceError
from .repositories import ProfileRepository
from .local_store import ROOT, utc_now

_FACT_CATEGORY = {
    "business_registration": "business_registration",
    "qualification": "qualification_registry",
    "personnel": "personnel_registry",
    "personnel_certificate": "personnel_registry",
    "performance": "performance_record",
    "bid_participation": "tender_notice",
    "bid_award": "award_notice",
    "fulfillment": "fulfillment_record",
    "buyer_relationship": "buyer_relationship",
    "risk_penalty_credit": "risk_credit",
    "enterprise_material": "enterprise_material",
    "other_enterprise_fact": "other",
}


def _schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _validate(value: Any, schema_name: str, code: str) -> None:
    issues = sorted(
        Draft202012Validator(_schema(schema_name), format_checker=_FACT_FORMAT_CHECKER).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if issues:
        issue = issues[0]
        path = ".".join(str(item) for item in issue.absolute_path)
        raise ServiceError(code, issue.message, details={"field_path": path, "issue_count": len(issues)})


def normalize_task_requirements(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    source = value.get("core_requirements") if isinstance(value.get("core_requirements"), list) else value.get("requirements")
    result: list[dict[str, Any]] = []
    for item in source or []:
        if not isinstance(item, dict):
            continue
        if {"requirement_id", "target_layer", "target_code", "importance", "reason"} <= set(item):
            result.append({key: deepcopy(item[key]) for key in ("requirement_id", "target_layer", "target_code", "importance", "reason")})
        elif {"requirement_id", "target_layer", "target_code", "importance", "description"} <= set(item):
            result.append({
                "requirement_id": item["requirement_id"],
                "target_layer": item["target_layer"],
                "target_code": item["target_code"],
                "importance": item["importance"],
                "reason": item["description"],
            })
    return result


DEFAULT_PROFILE_COMPLETION_REQUIREMENTS = [
    {
        "requirement_id": "profile-completion-performance",
        "target_layer": "fact",
        "target_code": "performance",
        "importance": "blocking",
        "reason": "完善企业画像需要至少一条可核验的历史项目业绩。",
    },
    {
        "requirement_id": "profile-completion-regional-delivery",
        "target_layer": "capability",
        "target_code": "regional_delivery_capability",
        "importance": "important",
        "reason": "投标决策需要了解企业在目标地区的交付经验。",
    },
    {
        "requirement_id": "profile-completion-business-goal",
        "target_layer": "decision",
        "target_code": "current_business_goals",
        "importance": "important",
        "reason": "项目推荐需要用户确认企业当前的经营目标。",
    },
]


def effective_task_requirements(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return stored requirements, or a safe general-purpose profile task.

    Imported enterprise rows do not carry a specific tender task yet.  The
    default keeps the profile-completion Agent usable without pretending that a
    project-specific requirement has already been supplied.
    """
    normalized = normalize_task_requirements(value)
    return normalized or deepcopy(DEFAULT_PROFILE_COMPLETION_REQUIREMENTS)


class ExternalInputService:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def submit_fact_candidate(self, submission: dict[str, Any]) -> dict[str, Any]:
        _validate(submission, "enterprise_fact_candidate_submission.schema.json", ERROR_FACT_CANDIDATE_INVALID)
        company_id = submission["company_id"]
        context = self.repository.read_company_context(company_id)
        existing = self.repository.list_fact_candidates(company_id=company_id)
        duplicate = next((item for item in existing if item.get("idempotency_key") == submission["idempotency_key"]), None)
        if duplicate is None:
            duplicate = next((item for item in existing if item.get("source_system") == submission["source_system"] and item.get("source_record_id") == submission["source_record_id"]), None)
        if duplicate:
            return {"status": "DUPLICATE", "duplicate": True, "candidate": duplicate, "review_task_id": duplicate.get("review_task_id")}

        # Build an unverified fact solely to reuse the existing strong payload schema.
        preview = build_fact(
            enterprise=context["fact_profile"]["enterprise"],
            fact_type=submission["fact_type"],
            payload=deepcopy(submission["payload"]),
            source={
                "source_type": submission["source_type"],
                "source_data_category": _FACT_CATEGORY[submission["fact_type"]],
                "source_platform": submission["source_system"],
                "source_record_id": submission["source_record_id"],
                "source_url": submission["source_url"],
                "dataset_id": f"external-candidate-{hashlib.sha256(submission['source_system'].encode()).hexdigest()[:12]}",
                "provider_id": submission.get("provider_id"),
                "api_id": submission.get("api_id"),
                "api_call_id": submission.get("api_call_id"),
                "raw_response_ref": submission.get("raw_response_ref"),
            },
            temporal={
                "collected_at": submission["collected_at"],
                "available_at": submission["collected_at"],
                "availability_basis": "source_collected_at",
                "availability_status": "known",
            },
            verification_status="pending_review",
            evidence_ids=[item["material_id"] for item in submission["material_refs"]],
            quality_flags=["external_fact_candidate_pending_review"],
            created_from="normalized_input",
            is_mock=bool(submission.get("is_mock")),
        )
        candidate_id = stable_id("enterprise-fact-candidate", {
            "company_id": company_id,
            "submission_id": submission["submission_id"],
            "idempotency_key": submission["idempotency_key"],
            "preview_fact_hash": preview["fact_content_hash"],
        })
        review_task_id = stable_id("review-task", {"candidate_id": candidate_id, "review_type": "FACT"})
        candidate = {
            "candidate_schema_version": "enterprise-fact-candidate/1.0.0",
            "candidate_id": candidate_id,
            **deepcopy(submission),
            "preview_fact": preview,
            "candidate_status": "PENDING_REVIEW",
            "review_task_id": review_task_id,
            "created_at_utc": utc_now(),
            "data_origin": "EXTERNAL_CANDIDATE",
        }
        task = {
            "review_task_id": review_task_id,
            "company_id": company_id,
            "run_id": None,
            "processing_item_id": None,
            "source_gap_id": None,
            "review_type": "FACT",
            "target_layer": "fact",
            "target_code": submission["fact_type"],
            "status": "PENDING",
            "source_response": {"fact_candidate": deepcopy(candidate)},
            "material_references": deepcopy(submission["material_refs"]),
            "suggested_payload": deepcopy(submission["payload"]),
            "review_result": None,
            "review_history": [],
            "supplement_history": [],
            "review_source": "EXTERNAL_CANDIDATE",
            "created_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
        }
        self.repository.save_fact_candidate(candidate)
        self.repository.save_review_task(task)
        self.repository.audit("fact_candidate_submitted", {"candidate_id": candidate_id, "company_id": company_id, "review_task_id": review_task_id})
        return {"status": "CREATED", "duplicate": False, "candidate": candidate, "review_task_id": review_task_id}

    def submit_task_requirements(self, submission: dict[str, Any]) -> dict[str, Any]:
        _validate(submission, "enterprise_task_requirements_submission.schema.json", ERROR_TASK_REQUIREMENTS_INVALID)
        seen: set[str] = set()
        core: list[dict[str, Any]] = []
        for item in submission["requirements"]:
            if item["requirement_id"] in seen:
                raise ServiceError(ERROR_TASK_REQUIREMENTS_INVALID, "requirement_id must be unique", details={"requirement_id": item["requirement_id"]})
            seen.add(item["requirement_id"])
            if item["operator"] != "EXISTS":
                raise ServiceError("UNSUPPORTED_REQUIREMENT_OPERATOR", f"Unsupported operator: {item['operator']}")
            if item["required_value"] is not None:
                raise ServiceError("UNSUPPORTED_REQUIREMENT_OPERATOR", "EXISTS requirements must use required_value=null")
            if item["target_code"] not in TARGET_CODE_CATALOG[item["target_layer"]]:
                raise ServiceError(ERROR_TASK_REQUIREMENTS_INVALID, "target_code does not belong to target_layer")
            core.append({
                "requirement_id": item["requirement_id"],
                "target_layer": item["target_layer"],
                "target_code": item["target_code"],
                "importance": item["importance"],
                "reason": item["description"],
            })
        stored = {**deepcopy(submission), "core_requirements": core, "data_origin": "STRUCTURED_UPSTREAM", "stored_at_utc": utc_now()}
        self.repository.save_task_requirements(submission["company_id"], stored)
        return {"status": "SAVED", "requirement_set_id": submission["requirement_set_id"], "requirement_count": len(core), "core_requirements": core}
