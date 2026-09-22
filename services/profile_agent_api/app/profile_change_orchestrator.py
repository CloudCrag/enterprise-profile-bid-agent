"""Unified profile-change orchestration for the local Section 6 MVP.

All existing builders/validators remain the source of business truth.  This
class only coordinates version writes, deterministic downstream recomputation,
gap refresh and audit/rollback boundaries.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from src.capability_profile_builder import build_capability_profile
from src.capability_validator import assert_valid_capability_profile
from src.errors import InputDataError
from src.profile_gap.gap_analyzer import analyze_profile_gaps
from src.profile_gap.request import GAP_ANALYSIS_REQUEST_SCHEMA_VERSION, finalize_gap_analysis_request
from src.tag_profile_generator import generate_tag_profile, load_tag_catalog

from .decision_context import decision_context_diagnostic
from .integration import normalize_task_requirements
from .local_store import ROOT, utc_now
from .repositories import ProfileRepository


class ProfileChangeOrchestrator:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def _refresh_gaps(self, company_id: str, *, reason: str, actor: dict[str, Any] | None) -> dict[str, Any] | None:
        context = self.repository.read_company_context(company_id)
        requirements = normalize_task_requirements(context.get("task_requirements"))
        runs = self.repository.list_runs(company_id=company_id, limit=1)
        if runs and runs[0].get("task_requirements"):
            requirements = deepcopy(runs[0]["task_requirements"])
        if not requirements:
            return None
        request = finalize_gap_analysis_request({
            "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
            "enterprise": deepcopy(context["fact_profile"]["enterprise"]),
            "task_context": {
                "task_context_id": f"profile-change-refresh:{company_id}:{reason}",
                "task_goal": "画像变更后重新核对当前任务相关缺口",
                "requirements": requirements,
            },
        }, generated_at_utc=utc_now())
        stale = decision_context_diagnostic(context["fact_profile"], context["capability_profile"], context.get("decision_profile"))
        try:
            inventory = analyze_profile_gaps(
                request,
                context["fact_profile"],
                context["capability_profile"],
                decision_profile=context.get("decision_profile"),
            )
        except InputDataError as exc:
            if not any(code in str(exc) for code in (
                "gap_decision_dependency_mismatch",
                "gap_decision_context_dependency_mismatch",
                "gap_context_as_of_date_mismatch: decision profile as_of_date differs",
            )):
                raise
            non_decision = [item for item in requirements if item.get("target_layer") != "decision"]
            filtered_request = finalize_gap_analysis_request({
                "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
                "enterprise": deepcopy(context["fact_profile"]["enterprise"]),
                "task_context": {
                    "task_context_id": f"profile-change-refresh-non-decision:{company_id}:{reason}",
                    "task_goal": "决策上下文待重新确认；仅复扫事实和能力缺口",
                    "requirements": non_decision,
                },
            }, generated_at_utc=utc_now())
            inventory = analyze_profile_gaps(
                filtered_request,
                context["fact_profile"],
                context["capability_profile"],
                decision_profile=None,
            )
            inventory.setdefault("warnings", []).append({
                "code": "DECISION_CONTEXT_RECONFIRMATION_REQUIRED",
                "message": "决策画像上下文已过期，系统未自动修改长期决策。",
                "details": stale,
            })
        self.repository.save_gap_inventory(company_id, inventory)
        if runs:
            run = runs[0]
            run["post_review_gap_inventory"] = inventory
            run["latest_gap_inventory"] = inventory
            if stale["is_stale"]:
                run["decision_context_status"] = stale
            self.repository.save_run(run)
        self.repository.audit("profile_change_gaps_refreshed", {
            "company_id": company_id,
            "reason": reason,
            "actor": deepcopy(actor),
            "gap_count": (inventory.get("gap_summary") or {}).get("gap_count"),
            "decision_context_stale": stale["is_stale"],
        })
        return inventory

    def apply_fact_review(
        self,
        task: dict[str, Any],
        request: dict[str, Any],
        build_reviewed_profile: Callable[[dict[str, Any], dict[str, Any], dict[str, Any], str], dict[str, Any]],
    ) -> dict[str, Any]:
        company_id = task["company_id"]
        context = self.repository.read_company_context(company_id)
        fact = build_reviewed_profile(
            context["fact_profile"],
            task,
            request.get("payload") or task.get("suggested_payload") or {},
            request.get("reviewed_at_utc") or utc_now(),
        )
        # Precompute and validate all deterministic downstream objects before writing.
        tag = generate_tag_profile(fact, load_tag_catalog(ROOT / "config" / "enterprise_profile_tags.json"))
        capability = build_capability_profile(fact, tag)
        assert_valid_capability_profile(capability)
        backup = self.repository.current_bundle(company_id)
        try:
            fact_version = self.repository.write_profile(
                company_id,
                "fact",
                fact,
                update_source="MANUAL_REVIEW",
                updated_fields=[str(task.get("target_code"))],
                actor=request.get("reviewer_actor"),
            )
            tag_version = self.repository.write_profile(
                company_id,
                "tag",
                tag,
                update_source="FACT_PROFILE_RECOMPUTE",
                updated_fields=[str(task.get("target_code"))],
                actor=request.get("reviewer_actor"),
            )
            capability_version = self.repository.write_profile(
                company_id,
                "capability",
                capability,
                update_source="FACT_PROFILE_RECOMPUTE",
                updated_fields=[str(task.get("target_code"))],
                actor=request.get("reviewer_actor"),
            )
            gaps = self._refresh_gaps(company_id, reason="FACT_REVIEW_APPROVED", actor=request.get("reviewer_actor"))
            return {"fact": fact_version, "tag": tag_version, "capability": capability_version, "remaining_gap_inventory": gaps}
        except Exception:
            self.repository.restore_current_bundle(company_id, backup)
            self.repository.audit("profile_change_failed", {"company_id": company_id, "change_type": "FACT_REVIEW"})
            raise

    def apply_capability_review(self, task: dict[str, Any], request: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        assert_valid_capability_profile(profile)
        company_id = task["company_id"]
        version = self.repository.write_profile(
            company_id,
            "capability",
            profile,
            update_source="MANUAL_CAPABILITY_REVIEW",
            updated_fields=[str(task.get("target_code"))],
            actor=request.get("reviewer_actor"),
        )
        gaps = self._refresh_gaps(company_id, reason="CAPABILITY_REVIEW_APPROVED", actor=request.get("reviewer_actor"))
        return {"capability": version, "remaining_gap_inventory": gaps}

    def apply_decision_profile(
        self,
        company_id: str,
        profile: dict[str, Any],
        *,
        update_source: str,
        updated_fields: list[str],
        actor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        version = self.repository.write_profile(
            company_id,
            "decision",
            profile,
            update_source=update_source,
            updated_fields=updated_fields,
            actor=actor,
        )
        gaps = self._refresh_gaps(company_id, reason="DECISION_CONFIRMATION_APPLIED", actor=actor)
        return {"decision": version, "remaining_gap_inventory": gaps}
