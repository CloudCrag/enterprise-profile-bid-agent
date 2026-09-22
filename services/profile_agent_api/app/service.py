"""Application service for the Section 6 enterprise-profile MVP."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
import re
import threading
from pathlib import Path
from typing import Any

from src.capability_profile_builder import build_capability_profile
from src.capability_validator import assert_valid_capability_profile
from src.enterprise_capability_profile import finalize_capability_profile
from src.enterprise_fact_profile import build_fact, fact_profile_content_hash
from src.errors import InputDataError
from src.fact_profile_builder import _summaries, detect_fact_conflicts
from src.fact_time_view import build_fact_view
from src.fact_validator import assert_valid_fact_profile
from src.profile_gap.gap_analyzer import analyze_profile_gaps
from src.profile_gap.request import finalize_gap_analysis_request, GAP_ANALYSIS_REQUEST_SCHEMA_VERSION
from src.tag_profile_generator import generate_tag_profile, load_tag_catalog
from src.evaluation_agent import EnterpriseEvaluationAgentService
from src.decision_profile_builder import build_decision_profile
from src.enterprise_decision_profile import (
    DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
    DECISION_FIELD_NAMES,
    finalize_confirmation_input,
)

from ..langgraph import LANGGRAPH_BACKEND, ProfileAgentRunner
from .constants import (
    AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
    DEFAULT_DECISION_CONTEXT_POLICY,
)
from .decision_context import decision_context_diagnostic
from .errors import ServiceError
from .integration import ExternalInputService, effective_task_requirements, normalize_task_requirements
from .gap_display import enrich_gap_inventory
from .local_store import LocalJsonStore, ROOT, utc_now
from .profile_change_orchestrator import ProfileChangeOrchestrator
from .repositories import ProfileRepository


class ProfileMvpService:
    def __init__(self, store: ProfileRepository | None = None) -> None:
        self.store = store or LocalJsonStore()
        self.store.ensure_initialized()
        self.runner = ProfileAgentRunner(self.store)
        self.integration = ExternalInputService(self.store)
        self.orchestrator = ProfileChangeOrchestrator(self.store)
        self.evaluation = EnterpriseEvaluationAgentService(
            self.store, fact_candidate_submitter=self.integration.submit_fact_candidate
        )
        # Profile generation and reviewed user updates mutate the writable profile layer.
        self._mutation_lock = threading.RLock()

    def health(self) -> dict[str, Any]:
        self.store.ensure_initialized()
        return {
            "service": "profile-agent-api",
            "status": "UP",
            "langgraph": LANGGRAPH_BACKEND,
            "langgraph_backend": LANGGRAPH_BACKEND,
            "store": "UP",
            "mode": "LOCAL_JSON",
            "evaluation_model": "UP",
            "evaluation_model_sha256": self.evaluation.catalog.source_sha256,
            "evaluation_graph_backend": self.evaluation.graph.backend,
        }

    def companies(self) -> list[dict[str, Any]]:
        return self.store.list_companies()

    def _latest_gap(self, company_id: str) -> dict[str, Any] | None:
        stored = self.store.read_gap_inventory(company_id)
        if stored:
            return stored
        runs = self.store.list_runs(company_id=company_id, limit=1)
        if not runs:
            return None
        return runs[0].get("post_review_gap_inventory") or runs[0].get("remaining_gap_inventory") or runs[0].get("gap_inventory")

    def company_profile(self, company_id: str) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        fact = context["fact_profile"]
        tag = context["tag_profile"]
        capability = context["capability_profile"]
        decision = context["decision_profile"]
        reviews = self.store.list_review_tasks(company_id=company_id)
        gap = self._latest_gap(company_id)
        latest_runs = self.store.list_runs(company_id=company_id, limit=1)
        history = self.store.history(company_id)
        capability_history = history.get("capability") or []
        missing_fact_types = (
            (fact.get("data_quality_summary") or {}).get("missing_fact_types") or []
        )
        return {
            "enterprise": deepcopy(fact.get("enterprise")),
            "company_id": company_id,
            "data_origin": context["metadata"].get("data_origin", "PROFILE_AGENT_VERIFIED_JSON"),
            "fact_profile": fact,
            "tag_profile": tag,
            "capability_profile": capability,
            "decision_profile": decision,
            "summaries": {
                "fact_count": len(fact.get("facts") or []),
                "active_tag_count": sum(1 for item in (tag.get("tags") or []) if item.get("data_status") in {"available", "partial"}),
                "tag_count": len(tag.get("tags") or []),
                "capability_domain_count": len(capability.get("capability_domains") or []),
                "confirmed_decision_count": (decision.get("decision_summary") or {}).get("confirmed_field_count", 0) if decision else 0,
                "gap_count": (
                    (gap.get("gap_summary") or {}).get("gap_count", 0)
                    if gap
                    else len(missing_fact_types)
                ),
                "pending_review_count": sum(1 for item in reviews if item.get("status") in {"PENDING", "NEEDS_MORE_INFORMATION"}),
            },
            "versions": context["metadata"].get("current_versions", {}),
            "processing": {
                "base_profile_generated_by_pipeline": True,
                "latest_agent_run": (
                    {
                        "run_id": latest_runs[0].get("run_id"),
                        "status": latest_runs[0].get("status"),
                        "llm_node_executed": bool(latest_runs[0].get("llm_node_executed")),
                    }
                    if latest_runs
                    else None
                ),
                "latest_capability_update_source": (
                    capability_history[-1].get("update_source") if capability_history else None
                ),
            },
        }

    def profile_history(self, company_id: str) -> dict[str, Any]:
        return self.store.history(company_id)

    def profile_diff(self, company_id: str, layer: str, from_version: int, to_version: int) -> dict[str, Any]:
        if layer not in {"fact", "capability", "decision"}:
            raise ServiceError("PROFILE_HISTORY_LAYER_INVALID", "不支持该画像类型。", status_code=400)
        try:
            before = self.store.history_entry(company_id, layer, from_version)["profile"]
            after = self.store.history_entry(company_id, layer, to_version)["profile"]
        except FileNotFoundError as exc:
            raise ServiceError(
                "PROFILE_HISTORY_VERSION_NOT_FOUND",
                "所选画像版本不存在，请从已有版本中重新选择。",
                details={"layer": layer, "from_version": from_version, "to_version": to_version},
                status_code=404,
            ) from exc
        if layer == "decision":
            left = {item["field_code"]: item for item in before.get("decision_fields") or []}
            right = {item["field_code"]: item for item in after.get("decision_fields") or []}
            changes = []
            for code in sorted(set(left) | set(right)):
                if left.get(code) != right.get(code):
                    changes.append({"field_code": code, "before": left.get(code), "after": right.get(code), "change_type": "modified"})
            return {"layer": layer, "from_version": from_version, "to_version": to_version, "changes": changes}
        key = "fact_id" if layer == "fact" else "capability_type"
        left_items = before.get("facts") if layer == "fact" else before.get("capability_domains")
        right_items = after.get("facts") if layer == "fact" else after.get("capability_domains")
        left = {item.get(key): item for item in left_items or []}
        right = {item.get(key): item for item in right_items or []}
        changes = []
        for item_id in sorted(set(left) | set(right), key=str):
            if item_id not in left:
                changes.append({"id": item_id, "change_type": "added", "after": right[item_id]})
            elif item_id not in right:
                changes.append({"id": item_id, "change_type": "removed", "before": left[item_id]})
            elif left[item_id] != right[item_id]:
                changes.append({"id": item_id, "change_type": "modified", "before": left[item_id], "after": right[item_id]})
        return {"layer": layer, "from_version": from_version, "to_version": to_version, "changes": changes}

    def dashboard(self, company_id: str | None = None) -> dict[str, Any]:
        companies = self.companies()
        current = next(
            (item for item in companies if item.get("company_id") == company_id),
            None,
        )
        if current is None:
            current = companies[0] if companies else None
        profile = self.company_profile(current["company_id"]) if current else None
        runs = self.store.list_runs(limit=5)
        return {
            "company_count": len(companies),
            "current_company": current,
            "profile": profile,
            "recent_agent_runs": [{
                "run_id": item.get("run_id"),
                "status": item.get("status"),
                "current_node": item.get("current_node"),
                "mode": item.get("mode"),
            } for item in runs],
            "python_service": self.health(),
        }

    def gaps(self, company_id: str) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        gap = self._latest_gap(company_id)
        run_values = self.store.list_runs(company_id=company_id, limit=1)
        run = run_values[0] if run_values else None
        decision_status = decision_context_diagnostic(
            context["fact_profile"], context["capability_profile"], context.get("decision_profile")
        )
        if gap is None:
            requirements = effective_task_requirements(context.get("task_requirements"))
            raw = {
                "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
                "enterprise": deepcopy(context["fact_profile"]["enterprise"]),
                "task_context": {
                    "task_context_id": "section6-dashboard-current",
                    "task_goal": "展示当前任务相关企业画像缺口",
                    "requirements": deepcopy(requirements),
                },
            }
            request = finalize_gap_analysis_request(raw, generated_at_utc=utc_now())
            try:
                gap = analyze_profile_gaps(
                    request,
                    context["fact_profile"],
                    context["capability_profile"],
                    decision_profile=context["decision_profile"],
                )
            except InputDataError as exc:
                if not any(code in str(exc) for code in (
                    "gap_decision_dependency_mismatch",
                    "gap_decision_context_dependency_mismatch",
                    "gap_context_as_of_date_mismatch: decision profile as_of_date differs",
                )):
                    raise
                filtered = [item for item in requirements if item.get("target_layer") != "decision"]
                filtered_request = finalize_gap_analysis_request({
                    "gap_analysis_request_schema_version": GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
                    "enterprise": deepcopy(context["fact_profile"]["enterprise"]),
                    "task_context": {
                        "task_context_id": "section6-dashboard-current-non-decision",
                        "task_goal": "决策上下文待重新确认；展示事实和能力缺口",
                        "requirements": filtered,
                    },
                }, generated_at_utc=utc_now())
                gap = analyze_profile_gaps(
                    filtered_request,
                    context["fact_profile"],
                    context["capability_profile"],
                    decision_profile=None,
                )
                gap.setdefault("warnings", []).append({
                    "code": "DECISION_CONTEXT_RECONFIRMATION_REQUIRED",
                    "message": "事实或能力画像已经更新，当前决策画像基于旧版本，需要重新确认。",
                    "details": decision_status,
                })
            self.store.save_gap_inventory(company_id, gap)
        return enrich_gap_inventory(
            gap,
            run=run,
            review_tasks=self.store.list_review_tasks(company_id=company_id),
            decision_context_status=decision_status,
        )

    def submit_fact_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.integration.submit_fact_candidate(payload)

    def submit_task_requirements(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.integration.submit_task_requirements(payload)

    def fact_candidates(self, company_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_fact_candidates(company_id=company_id)

    def request_decision_reconfirmation(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run.get("status") != AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED:
            raise ServiceError("DECISION_RECONFIRMATION_NOT_REQUIRED", "当前Agent运行不处于决策上下文重新确认状态。", status_code=409)
        # Contract only: final business semantics are intentionally not selected in this batch.
        run["decision_reconfirmation_request"] = deepcopy(payload)
        run["termination_reason"] = "AWAITING_CONFIRMED_RECONFIRMATION_POLICY"
        self.store.save_run(run)
        self.store.audit("decision_reconfirmation_requested", {"run_id": run_id, "strategy": payload.get("strategy")})
        return {
            "status": AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
            "applied": False,
            "policy_pending_confirmation": True,
            "decision_context_status": run.get("decision_context_status"),
            "request": deepcopy(payload),
        }

    @staticmethod
    def _decision_preference_value(field_code: str, value: Any) -> dict[str, Any]:
        if field_code == "strategic_industries":
            return {"industries": [{"industry_name": str(item), "industry_code": None} for item in value]}
        if field_code == "strategic_regions":
            return {"regions": [{"region_name": str(item), "region_code": None} for item in value]}
        if field_code == "budget_preference":
            def money(item: Any) -> dict[str, Any] | None:
                if item is None:
                    return None
                number = float(item)
                return {
                    "raw_value": f"{number:g}元",
                    "normalized_value": number,
                    "normalized_unit": "yuan",
                    "normalized_currency": "CNY",
                }
            return {"minimum": money(value.get("minimum")), "maximum": money(value.get("maximum"))}
        if field_code == "procurement_method_preferences":
            return {"methods": [{"method_name": str(item), "method_code": None} for item in value]}
        if field_code == "consortium_acceptance":
            return {"accepted": bool(value)}
        if field_code == "risk_preference":
            names = {"conservative": "谨慎", "balanced": "平衡", "aggressive": "积极"}
            return {"stated_preference": names.get(str(value), str(value)), "normalized_code": str(value)}
        if field_code == "max_concurrent_projects":
            return {"maximum": int(value)}
        if field_code == "personnel_resource_constraints":
            return {"constraints": [
                {"constraint_name": str(item), "constraint_value": str(item), "unit": None, "notes": None}
                for item in value
            ]}
        if field_code == "explicit_exclusions":
            return {"items": [
                {"exclusion_subject": str(item), "scope_type": None, "scope_value": None, "notes": None}
                for item in value
            ]}
        if field_code == "key_buyers":
            return {"buyers": [{"buyer_name": str(item), "buyer_identifier": None} for item in value]}
        if field_code == "current_business_goals":
            return {"goals": [{"goal_text": str(item), "target_date": None} for item in value]}
        raise ServiceError("DECISION_PREFERENCE_FIELD_UNSUPPORTED", f"不支持的经营偏好字段：{field_code}")

    def update_decision_preferences(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        preferences = payload.get("preferences") or {}
        cleared = list(dict.fromkeys(payload.get("cleared_fields") or []))
        unknown = sorted((set(preferences) | set(cleared)) - set(DECISION_FIELD_NAMES))
        if unknown:
            raise ServiceError(
                "DECISION_PREFERENCE_FIELD_UNSUPPORTED",
                "提交内容包含不支持的经营偏好字段。",
                details={"fields": unknown},
            )
        if set(preferences) & set(cleared):
            raise ServiceError("DECISION_PREFERENCE_UPDATE_CONFLICT", "同一经营偏好不能同时填写和清空。")
        updates = [
            {
                "field_code": field_code,
                "action": "set",
                "value": self._decision_preference_value(field_code, value),
            }
            for field_code, value in preferences.items()
        ]
        updates.extend({"field_code": field_code, "action": "clear"} for field_code in cleared)
        if not updates:
            raise ServiceError("DECISION_PREFERENCE_NO_CHANGE", "请至少修改一项经营偏好。")
        base = context.get("decision_profile")
        confirmation = finalize_confirmation_input({
            "confirmation_input_schema_version": DECISION_CONFIRMATION_INPUT_SCHEMA_VERSION,
            "enterprise": deepcopy(context["fact_profile"]["enterprise"]),
            "base_decision_profile_id": (base or {}).get("decision_profile_id"),
            "base_decision_profile_content_hash": (base or {}).get("decision_profile_content_hash"),
            "confirmation_actor": {
                "actor_type": payload["confirmation_actor"]["actor_type"],
                "actor_id": payload["confirmation_actor"]["actor_id"],
            },
            "confirmed_at": payload["confirmed_at_utc"],
            "updates": updates,
        })
        try:
            profile = build_decision_profile(
                context["fact_profile"],
                context["capability_profile"],
                confirmation,
                base_decision_profile=base,
                generated_at_utc=payload["confirmed_at_utc"],
            )
        except InputDataError as exc:
            raise ServiceError(
                "DECISION_PREFERENCE_INVALID",
                "经营偏好未能保存，请检查填写内容。",
                details={"reason": str(exc)},
            ) from exc
        applied = self.orchestrator.apply_decision_profile(
            company_id,
            profile,
            update_source="USER_CONFIRMED_PREFERENCES",
            updated_fields=[item["field_code"] for item in updates],
            actor=payload["confirmation_actor"],
        )
        return {"decision_profile": profile, "version": applied.get("decision"), "remaining_gap_inventory": applied.get("remaining_gap_inventory")}

    def submit_profile_performance(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        now = payload.get("submitted_at_utc") or utc_now()
        amount_value = payload.get("contract_amount")
        amount = None if amount_value is None else {
            "raw_value": amount_value,
            "value": amount_value,
            "unit_raw": "元",
            "currency_raw": "CNY",
            "normalized_value": amount_value,
            "normalized_unit": "yuan",
            "normalized_currency": "CNY",
            "value_parse_status": "parsed",
            "semantic_status": "confirmed",
            "normalization_status": "normalized",
        }
        source_seed = hashlib.sha256(
            f"{company_id}:{payload['project_name']}:{payload.get('region')}:{payload.get('start_date')}".encode("utf-8")
        ).hexdigest()[:24]
        fact = build_fact(
            enterprise=context["fact_profile"]["enterprise"],
            fact_type="performance",
            payload={
                "project_name": payload["project_name"],
                "contract_number": None,
                "buyer_name": payload.get("buyer_name"),
                "industry": payload.get("industry"),
                "region": payload["region"],
                "contract_amount": amount,
                "start_date": payload.get("start_date"),
                "end_date": payload.get("end_date"),
                "performance_scope": payload["performance_scope"],
            },
            source={
                "source_type": "user_upload",
                "source_data_category": "performance_record",
                "source_platform": "enterprise_profile_web",
                "source_record_id": f"profile-input:{source_seed}",
                "source_url": None,
                "dataset_id": "profile-assistant-user-confirmed",
            },
            temporal={
                "published_at": None,
                "collected_at": now,
                "verified_at": now,
                "valid_from": payload.get("start_date"),
                "valid_until": payload.get("end_date"),
                "observed_at": payload.get("end_date") or payload.get("start_date"),
                "available_at": now,
                "availability_basis": "user_received_at",
                "availability_status": "known",
            },
            fact_status="active",
            verification_status="verified",
            evidence_ids=[],
            quality_flags=["user_confirmed_profile_input", f"source:{payload['source_description']}"],
            created_from="user_confirmed_structured_input",
            is_mock=False,
        )
        fact_profile = deepcopy(context["fact_profile"])
        replace_fact_id = payload.get("_replace_fact_id")
        facts = [
            item for item in list(fact_profile.get("facts") or [])
            if not replace_fact_id or item.get("fact_id") != replace_fact_id
        ]
        if fact["fact_id"] not in {item.get("fact_id") for item in facts}:
            facts.insert(0, fact)
        fact_profile["facts"] = facts
        fact_profile["generated_at_utc"] = now
        fact_profile["as_of_date"] = now[:10]
        fact_profile["as_of_date_status"] = "provided_input"
        fact_profile["conflicts"] = detect_fact_conflicts(facts)
        view, warnings = build_fact_view(facts, fact_profile["as_of_date"])
        fact_profile["fact_view"] = view
        fact_profile["fact_summary"], fact_profile["source_summary"] = _summaries(facts)
        fact_profile["warnings"] = list(dict.fromkeys(list(fact_profile.get("warnings") or []) + warnings))
        assert_valid_fact_profile(fact_profile)
        tag = generate_tag_profile(fact_profile, load_tag_catalog(ROOT / "config" / "enterprise_profile_tags.json"))
        capability = build_capability_profile(fact_profile, tag)
        assert_valid_capability_profile(capability)
        with self._mutation_lock:
            fact_version = self.store.write_profile(company_id, "fact", fact_profile, update_source="USER_CONFIRMED_PROFILE_INPUT", updated_fields=["performance"], actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            self.store.write_profile(company_id, "tag", tag, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=["performance"])
            capability_version = self.store.write_profile(company_id, "capability", capability, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=["performance", "regional_delivery_capability"])
            gaps = self.orchestrator._refresh_gaps(company_id, reason="USER_PROFILE_INPUT", actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            evaluation = self.evaluation.start(
                company_id=company_id,
                provider_id="LOCAL_PROFILE",
                workflow_mode="automatic",
                max_api_calls=0,
                max_rounds=3,
            )
        return {
            "fact_version": fact_version,
            "capability_version": capability_version,
            "remaining_gap_inventory": gaps,
            "evaluation_profile": evaluation.get("profile"),
        }

    def supplement_profile_performance(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        performances = [
            item for item in (context["fact_profile"].get("facts") or [])
            if item.get("fact_type") == "performance" and item.get("fact_status", "active") == "active"
        ]
        if not performances:
            raise ServiceError(
                "PROFILE_PERFORMANCE_NOT_FOUND",
                "请先补充一条项目经历，再填写采购人和合同金额。",
                status_code=400,
            )
        target = performances[0]
        current = target.get("payload") or {}
        buyer_name = payload.get("buyer_name") or current.get("buyer_name")
        current_amount = current.get("contract_amount")
        amount_value = payload.get("contract_amount")
        if amount_value is None and isinstance(current_amount, dict):
            amount_value = current_amount.get("normalized_value")
        if not buyer_name and amount_value is None:
            raise ServiceError(
                "PROFILE_PERFORMANCE_SUPPLEMENT_EMPTY",
                "请至少填写采购人或合同金额。",
                status_code=400,
            )
        merged = {
            "project_name": current.get("project_name") or "已补充项目经历",
            "buyer_name": buyer_name,
            "industry": current.get("industry"),
            "region": current.get("region") or "地区待确认",
            "contract_amount": amount_value,
            "start_date": current.get("start_date"),
            "end_date": current.get("end_date"),
            "performance_scope": current.get("performance_scope") or "项目内容待进一步补充",
            "source_description": payload["source_description"],
            "submitted_at_utc": payload.get("submitted_at_utc") or utc_now(),
            "_replace_fact_id": target.get("fact_id"),
        }
        return self.submit_profile_performance(company_id, merged)

    def submit_profile_personnel(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("employee_count") is None and payload.get("social_insurance_count") is None:
            raise ServiceError(
                "PROFILE_PERSONNEL_INPUT_EMPTY",
                "请至少填写员工人数或社保缴纳人数。",
                status_code=400,
            )
        context = self.store.read_company_context(company_id)
        now = payload.get("submitted_at_utc") or utc_now()
        source_seed = hashlib.sha256(
            f"{company_id}:personnel:{now}:{payload.get('employee_count')}:{payload.get('social_insurance_count')}".encode("utf-8")
        ).hexdigest()[:24]
        fact = build_fact(
            enterprise=context["fact_profile"]["enterprise"],
            fact_type="personnel",
            payload={
                "employee_count": payload.get("employee_count"),
                "social_insurance_count": payload.get("social_insurance_count"),
                "count_scope": payload.get("count_scope") or "企业当前人员统计",
            },
            source={
                "source_type": "user_upload",
                "source_data_category": "personnel_registry",
                "source_platform": "enterprise_profile_web",
                "source_record_id": f"profile-input:{source_seed}",
                "source_url": None,
                "dataset_id": "profile-assistant-user-confirmed",
            },
            temporal={
                "published_at": None,
                "collected_at": now,
                "verified_at": now,
                "valid_from": None,
                "valid_until": None,
                "observed_at": now,
                "available_at": now,
                "availability_basis": "user_received_at",
                "availability_status": "known",
            },
            fact_status="active",
            verification_status="verified",
            evidence_ids=[],
            quality_flags=["user_confirmed_profile_input", f"source:{payload['source_description']}"],
            created_from="user_confirmed_structured_input",
            is_mock=False,
        )
        fact_profile = deepcopy(context["fact_profile"])
        facts = [fact, *list(fact_profile.get("facts") or [])]
        fact_profile["facts"] = facts
        fact_profile["generated_at_utc"] = now
        fact_profile["as_of_date"] = now[:10]
        fact_profile["as_of_date_status"] = "provided_input"
        fact_profile["conflicts"] = detect_fact_conflicts(facts)
        view, warnings = build_fact_view(facts, fact_profile["as_of_date"])
        fact_profile["fact_view"] = view
        fact_profile["fact_summary"], fact_profile["source_summary"] = _summaries(facts)
        fact_profile["warnings"] = list(dict.fromkeys(list(fact_profile.get("warnings") or []) + warnings))
        assert_valid_fact_profile(fact_profile)
        tag = generate_tag_profile(fact_profile, load_tag_catalog(ROOT / "config" / "enterprise_profile_tags.json"))
        capability = build_capability_profile(fact_profile, tag)
        assert_valid_capability_profile(capability)
        with self._mutation_lock:
            fact_version = self.store.write_profile(company_id, "fact", fact_profile, update_source="USER_CONFIRMED_PROFILE_INPUT", updated_fields=["personnel"], actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            self.store.write_profile(company_id, "tag", tag, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=["personnel"])
            capability_version = self.store.write_profile(company_id, "capability", capability, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=["personnel_resource_capability"])
            gaps = self.orchestrator._refresh_gaps(company_id, reason="USER_PROFILE_INPUT", actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            evaluation = self.evaluation.start(company_id=company_id, provider_id="LOCAL_PROFILE", workflow_mode="automatic", max_api_calls=0, max_rounds=3)
        return {
            "fact_version": fact_version,
            "capability_version": capability_version,
            "remaining_gap_inventory": gaps,
            "evaluation_profile": evaluation.get("profile"),
        }

    def submit_profile_general(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = self.store.read_company_context(company_id)
        now = payload.get("submitted_at_utc") or utc_now()
        target_layer = str(payload["target_layer"])
        target_code = str(payload["target_code"])
        content = str(payload["content"]).strip()
        source_description = str(payload["source_description"]).strip()
        source_seed = hashlib.sha256(
            f"{company_id}:{target_layer}:{target_code}:{content}:{source_description}".encode("utf-8")
        ).hexdigest()[:24]
        if target_layer == "fact" and target_code == "qualification":
            fact_type = "qualification"
            fact_payload = {
                "name": content,
                "qualification_level": None,
                "certificate_number": None,
                "issuing_authority": None,
                "valid_until": None,
                "status": "unknown",
            }
            source_category = "qualification_registry"
        elif target_layer == "capability":
            fact_type = "other_enterprise_fact"
            fact_payload = {
                "subtype": "user_capability_input",
                "business_description": content,
                "details": {"target_code": target_code, "source_description": source_description},
            }
            source_category = "enterprise_material"
        else:
            fact_type = "other_enterprise_fact"
            fact_payload = {
                "subtype": "user_fact_input",
                "business_description": content,
                "details": {"target_code": target_code, "source_description": source_description},
            }
            source_category = "enterprise_material"
        fact = build_fact(
            enterprise=context["fact_profile"]["enterprise"], fact_type=fact_type, payload=fact_payload,
            source={
                "source_type": "user_upload", "source_data_category": source_category,
                "source_platform": "enterprise_profile_web",
                "source_record_id": f"profile-input:{source_seed}", "source_url": None,
                "dataset_id": "profile-assistant-user-confirmed",
            },
            temporal={
                "published_at": None, "collected_at": now, "verified_at": now,
                "valid_from": None, "valid_until": None, "observed_at": now,
                "available_at": now, "availability_basis": "user_received_at",
                "availability_status": "known",
            },
            fact_status="active", verification_status="partially_verified",
            evidence_ids=[], quality_flags=["user_confirmed_profile_input", f"source:{source_description}"],
            created_from="user_confirmed_structured_input",
            is_mock=False,
        )
        fact_profile = deepcopy(context["fact_profile"])
        facts = [fact, *list(fact_profile.get("facts") or [])]
        fact_profile["facts"] = facts
        fact_profile["generated_at_utc"] = now
        fact_profile["as_of_date"] = now[:10]
        fact_profile["as_of_date_status"] = "provided_input"
        fact_profile["conflicts"] = detect_fact_conflicts(facts)
        fact_profile["fact_view"], warnings = build_fact_view(facts, fact_profile["as_of_date"])
        fact_profile["fact_summary"], fact_profile["source_summary"] = _summaries(facts)
        fact_profile["warnings"] = list(dict.fromkeys(list(fact_profile.get("warnings") or []) + warnings))
        assert_valid_fact_profile(fact_profile)
        tag = generate_tag_profile(fact_profile, load_tag_catalog(ROOT / "config" / "enterprise_profile_tags.json"))
        capability = build_capability_profile(fact_profile, tag)
        assert_valid_capability_profile(capability)
        with self._mutation_lock:
            fact_version = self.store.write_profile(company_id, "fact", fact_profile, update_source="USER_CONFIRMED_PROFILE_INPUT", updated_fields=[target_code], actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            self.store.write_profile(company_id, "tag", tag, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=[target_code])
            capability_version = self.store.write_profile(company_id, "capability", capability, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=[target_code])
            gaps = self.orchestrator._refresh_gaps(company_id, reason="USER_PROFILE_INPUT", actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            evaluation = self.evaluation.start(company_id=company_id, provider_id="LOCAL_PROFILE", workflow_mode="automatic", max_api_calls=0, max_rounds=3)
        return {
            "fact_version": fact_version, "capability_version": capability_version,
            "remaining_gap_inventory": gaps, "evaluation_profile": evaluation.get("profile"),
        }

    def submit_review_supplement(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            task = self.store.read_review_task(task_id)
        except FileNotFoundError as exc:
            raise ServiceError("REVIEW_TASK_NOT_FOUND", f"Review task not found: {task_id}", status_code=404) from exc
        if task.get("status") != "NEEDS_MORE_INFORMATION":
            raise ServiceError("REVIEW_TASK_NOT_WAITING_SUPPLEMENT", "审核任务当前不需要补充信息。", status_code=409)
        if payload.get("review_task_id") != task_id:
            raise ServiceError("SUPPLEMENT_REVIEW_TASK_MISMATCH", "supplement review_task_id does not match route")
        history = list(task.get("supplement_history") or [])
        if any(item.get("supplement_id") == payload.get("supplement_id") for item in history):
            return task
        history.append(deepcopy(payload))
        task.setdefault("review_history", []).append(deepcopy(task.get("review_result")))
        task["supplement_history"] = history
        if isinstance(payload.get("answer"), dict):
            task["suggested_payload"] = deepcopy(payload["answer"])
        if payload.get("material_refs"):
            task["material_references"] = list(task.get("material_references") or []) + deepcopy(payload["material_refs"])
        task["status"] = "PENDING"
        task["review_result"] = None
        task["updated_at_utc"] = utc_now()
        task["local_adapter_strategy"] = "LOCAL_REUSE_REVIEW_TASK"
        self.store.save_review_task(task)
        self.store.audit("review_supplement_submitted", {"review_task_id": task_id, "supplement_id": payload.get("supplement_id")})
        return task

    def start_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_id = str(payload.get("company_id") or "").strip()
        if not company_id:
            raise ServiceError("COMPANY_ID_REQUIRED", "必须指定真实企业 company_id。", status_code=400)
        context = self.store.read_company_context(company_id)
        supplied_requirements = payload.get("task_requirements")
        stored_requirements = normalize_task_requirements(context.get("task_requirements"))
        has_custom_stored_requirements = bool(stored_requirements) and not all(
            str(item.get("requirement_id") or "").startswith("profile-completion-")
            for item in stored_requirements
        )
        if supplied_requirements:
            requirements = supplied_requirements
        elif has_custom_stored_requirements:
            requirements = stored_requirements
        else:
            facts = context["fact_profile"].get("facts") or []
            fact_types = {str(item.get("fact_type")) for item in facts}
            requirements = []
            for code, importance, reason in (
                ("performance", "blocking", "缺少可核验的历史项目资料，补充一次即可同时完善业绩、地区、金额和采购人相关能力。"),
                ("qualification", "important", "缺少企业资质或证书资料。"),
                ("personnel", "important", "缺少员工、社保或专业人员资料。"),
            ):
                if code not in fact_types:
                    requirements.append({
                        "requirement_id": f"profile-dynamic-fact-{code}",
                        "target_layer": "fact", "target_code": code,
                        "importance": importance, "reason": reason,
                    })
            performance_exists = "performance" in fact_types or "fulfillment" in fact_types or "bid_award" in fact_types
            derived_from_performance = {
                "similar_performance_capability", "regional_delivery_capability",
                "amount_experience_capability", "buyer_relationship_capability",
                "tender_performance_capability",
            }
            for domain in context["capability_profile"].get("capability_domains") or []:
                capability_type = str(domain.get("capability_type") or "")
                if domain.get("support_status") not in {"insufficient_data", "ambiguous"}:
                    continue
                if capability_type in derived_from_performance and not performance_exists:
                    continue
                requirements.append({
                    "requirement_id": f"profile-dynamic-capability-{capability_type}",
                    "target_layer": "capability", "target_code": capability_type,
                    "importance": "important",
                    "reason": f"当前资料不足以说明{domain.get('capability_name') or capability_type}。",
                })
            if not requirements:
                requirements = effective_task_requirements(context.get("task_requirements"))
        if not bool(payload.get("include_decision_questions", True)):
            requirements = [
                requirement for requirement in requirements
                if requirement.get("target_layer") != "decision"
            ]
        with self._mutation_lock:
            return self.runner.start(
                company_id=company_id,
                mode=payload.get("mode", "interactive"),
                task_requirements=requirements,
                max_questions_per_batch=int(payload.get("max_questions_per_batch", 3)),
                include_optional=bool(payload.get("include_optional", False)),
                max_rounds=int(payload.get("max_rounds", 5)),
                decision_context_policy=str(payload.get("decision_context_policy") or DEFAULT_DECISION_CONTEXT_POLICY),
                capability_ai_enabled=bool(payload.get("capability_ai_enabled", False)),
                analysis_only=bool(payload.get("analysis_only", False)),
                include_decision_questions=bool(payload.get("include_decision_questions", True)),
                requested_capability_types=list(payload.get("requested_capability_types") or []),
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        try:
            return self.store.read_run(run_id)
        except FileNotFoundError as exc:
            raise ServiceError("AGENT_RUN_NOT_FOUND", f"Agent run not found: {run_id}", status_code=404) from exc

    def latest_run(self, company_id: str) -> dict[str, Any]:
        runs = self.store.list_runs(company_id=company_id, limit=1)
        if not runs:
            raise ServiceError("AGENT_RUN_NOT_FOUND", "该企业尚无画像助手运行记录。", status_code=404)
        return runs[0]

    def submit_responses(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._mutation_lock:
                return self.runner.resume_with_responses(run_id, payload)
        except ValueError as exc:
            raise ServiceError("AGENT_RUN_STATE_INVALID", str(exc), status_code=409) from exc

    def select_decisions(self, run_id: str, candidate_ids: list[str]) -> dict[str, Any]:
        try:
            with self._mutation_lock:
                return self.runner.resume_with_decision_selection(run_id, candidate_ids)
        except ValueError as exc:
            raise ServiceError("AGENT_RUN_STATE_INVALID", str(exc), status_code=409) from exc

    def review_tasks(self, company_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_review_tasks(company_id=company_id)

    def _reviewed_fact_profile(self, current: dict[str, Any], task: dict[str, Any], payload: dict[str, Any], reviewed_at: str) -> dict[str, Any]:
        fact_type = task.get("target_code") or "performance"
        fact_payload = deepcopy(payload)
        if fact_type == "performance" and isinstance(fact_payload.get("contract_amount"), dict):
            amount = fact_payload["contract_amount"]
            if "value" not in amount:
                amount = {
                    "raw_value": amount.get("raw_value"),
                    "value": amount.get("normalized_value"),
                    "unit_raw": amount.get("normalized_unit"),
                    "currency_raw": amount.get("normalized_currency"),
                    "normalized_value": amount.get("normalized_value"),
                    "normalized_unit": amount.get("normalized_unit"),
                    "normalized_currency": amount.get("normalized_currency"),
                    "value_parse_status": "parsed" if amount.get("normalized_value") is not None else "unknown",
                    "semantic_status": "confirmed" if amount.get("normalized_value") is not None else "unknown",
                    "normalization_status": "normalized" if amount.get("normalized_value") is not None else "not_normalized",
                }
                fact_payload["contract_amount"] = amount
        if fact_type == "performance" and not fact_payload:
            raise ServiceError("REVIEW_PAYLOAD_REQUIRED", "业绩审核必须提供真实材料内容。", status_code=400)
        candidate = ((task.get("source_response") or {}).get("fact_candidate") or {}) if isinstance(task.get("source_response"), dict) else {}
        preview = candidate.get("preview_fact") if isinstance(candidate, dict) else None
        is_external = task.get("review_source") == "EXTERNAL_CANDIDATE" and isinstance(preview, dict)
        if is_external:
            original_source = deepcopy(preview.get("source") or {})
            original_temporal = deepcopy(preview.get("temporal") or {})
            source = {
                "source_type": original_source.get("source_type") or candidate.get("source_type") or "external_data",
                "source_data_category": original_source.get("source_data_category") or ("performance_record" if fact_type == "performance" else "other"),
                "source_platform": original_source.get("source_platform") or candidate.get("source_system"),
                "source_record_id": original_source.get("source_record_id") or candidate.get("source_record_id") or task["review_task_id"],
                "source_url": original_source.get("source_url") or candidate.get("source_url"),
                "dataset_id": original_source.get("dataset_id") or f"external-reviewed-{candidate.get('submission_id', 'unknown')}",
                "provider_id": original_source.get("provider_id") or candidate.get("provider_id"),
                "api_id": original_source.get("api_id") or candidate.get("api_id"),
                "api_call_id": original_source.get("api_call_id") or candidate.get("api_call_id"),
                "raw_response_ref": original_source.get("raw_response_ref") or candidate.get("raw_response_ref"),
            }
            temporal = {
                "published_at": original_temporal.get("published_at"),
                "collected_at": original_temporal.get("collected_at") or candidate.get("collected_at"),
                "verified_at": reviewed_at,
                "valid_from": original_temporal.get("valid_from"),
                "valid_until": original_temporal.get("valid_until"),
                "observed_at": original_temporal.get("observed_at"),
                "available_at": original_temporal.get("available_at") or candidate.get("collected_at") or reviewed_at,
                "availability_basis": original_temporal.get("availability_basis") or "source_collected_at",
                "availability_status": original_temporal.get("availability_status") or "known",
            }
            evidence_ids = [item.get("material_id") for item in candidate.get("material_refs") or [] if isinstance(item, dict) and item.get("material_id")]
            quality_flags = ["external_fact_candidate_review_approved"]
            is_mock = bool(preview.get("is_mock"))
            created_from = "manual_review_of_external_candidate"
        else:
            source = {
                "source_type": "manual_review",
                "source_data_category": "performance_record" if fact_type == "performance" else "manual_review",
                "source_platform": "section6_mvp_review_center",
                "source_record_id": task["review_task_id"],
                "source_url": None,
                "dataset_id": "section6_manual_review",
            }
            temporal = {
                "published_at": None, "collected_at": None, "verified_at": reviewed_at,
                "valid_from": None, "valid_until": None, "observed_at": None,
                "available_at": reviewed_at, "availability_basis": "manual_verified_at", "availability_status": "known",
            }
            evidence_ids = []
            quality_flags = ["manual_review_approved"]
            is_mock = False
            created_from = "manual_review"
        fact = build_fact(
            enterprise=current["enterprise"], fact_type=fact_type, payload=fact_payload,
            source=source, temporal=temporal, fact_status="active", verification_status="verified",
            evidence_ids=evidence_ids, quality_flags=quality_flags, created_from=created_from, is_mock=is_mock,
        )
        result = deepcopy(current)
        facts = deepcopy(result.get("facts") or [])
        if fact["fact_id"] not in {item.get("fact_id") for item in facts}:
            facts.append(fact)
        conflicts = detect_fact_conflicts(facts)
        fact_view, view_warnings = build_fact_view(facts, result.get("as_of_date"))
        fact_summary, source_summary = _summaries(facts)
        result["facts"] = facts
        if is_external:
            evidence_index = deepcopy(result.get("evidence_index") or {})
            dataset_seed = str(candidate.get("submission_id") or candidate.get("source_record_id") or "external-reviewed")
            dataset_id = re.sub(r"[^A-Za-z0-9._-]+", "-", dataset_seed).strip("-.") or "external-reviewed"
            for index, material in enumerate(candidate.get("material_refs") or [], 1):
                if not isinstance(material, dict) or not material.get("material_id"):
                    continue
                evidence_id = str(material["material_id"])
                evidence_index.setdefault(evidence_id, {
                    "evidence_schema_version": "enterprise-profile-evidence/1.1.0",
                    "evidence_id": evidence_id,
                    "source_type": source.get("source_type") or "external_data",
                    "source_dataset_id": dataset_id,
                    "source_file": material.get("original_filename") or "external-material",
                    "source_sheet": "external_materials",
                    "source_row_number": index,
                    "source_locator": f"material_refs[{index - 1}]",
                    "announcement_unique_id": None,
                    "project_number": fact_payload.get("contract_number") or fact_payload.get("project_number"),
                    "source_url": source.get("source_url"),
                    "record_time": {"published_at": None, "tender_end_at": None, "opening_at": None},
                    "collected_at": temporal.get("collected_at"),
                    "collected_at_status": "provided" if temporal.get("collected_at") else "not_provided",
                    "is_mock": is_mock,
                    "anonymized": False,
                    "record_status": "valid",
                    "quality_flags": ["external_material_review_approved"],
                    "material_content_sha256": material.get("material_content_sha256"),
                    "media_type": material.get("media_type"),
                    "size_bytes": material.get("size_bytes"),
                    "description": material.get("description"),
                })
            result["evidence_index"] = evidence_index
        result["fact_view"] = fact_view
        result["fact_summary"] = fact_summary
        result["source_summary"] = source_summary
        result["conflicts"] = conflicts
        result["warnings"] = [w for w in result.get("warnings") or [] if not (isinstance(w, str) and w.startswith("manual_review_applied:"))]
        result["warnings"].extend(view_warnings)
        result["warnings"].append(f"manual_review_applied:{task['review_task_id']}")
        quality = deepcopy(result.get("data_quality_summary") or {})
        included = len(fact_view.get("included_fact_ids") or [])
        quality.update({
            "time_view_included_fact_count": included,
            "time_view_excluded_fact_count": len(fact_view.get("excluded_fact_ids") or []),
            "unresolved_conflict_count": len(conflicts),
            "contains_mock_data": any(bool(item.get("is_mock")) for item in facts),
            "missing_fact_types": sorted(set(quality.get("missing_fact_types") or []) - {fact_type}),
        })
        result["data_quality_summary"] = quality
        result["generated_at_utc"] = utc_now()
        assert_valid_fact_profile(result)
        return result

    def _apply_fact_review(self, task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        return self.orchestrator.apply_fact_review(task, request, self._reviewed_fact_profile)

    def _apply_capability_review(self, task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        current = self.store.read_company_context(task["company_id"])
        payload = request.get("payload")
        if isinstance(payload, dict) and payload.get("capability_profile_schema_version"):
            profile = deepcopy(payload)
            if profile.get("enterprise") != current["fact_profile"].get("enterprise"):
                raise ServiceError("CAPABILITY_REVIEW_ENTERPRISE_MISMATCH", "Reviewed capability payload belongs to another enterprise")
            assert_valid_capability_profile(profile, fact_profile=current["fact_profile"], tag_profile=current["tag_profile"])
        else:
            # Rebuild from the current verified fact/tag state. This is deterministic,
            # All candidate facts require evidence validation before they can affect the profile.
            profile = build_capability_profile(current["fact_profile"], current["tag_profile"])
        return self.orchestrator.apply_capability_review(task, request, profile)

    def _apply_semantic_capability_review(self, task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        from src.capability_candidate_review import build_candidate_review_batch
        from src.capability_review_pipeline import build_reviewed_capability_profile
        artifact = deepcopy(task.get("semantic_candidates") or {})
        context = self.store.read_company_context(task["company_id"])
        candidates = artifact.get("capability_candidates") or []
        selected = set((request.get("payload") or {}).get("selected_candidate_ids") or [c.get("candidate_id") for c in candidates])
        decisions = []
        for candidate in candidates:
            approved = candidate.get("candidate_id") in selected
            decisions.append({
                "candidate_id": candidate["candidate_id"],
                "candidate_content_hash": candidate["candidate_content_hash"],
                "decision": "approved" if approved else "rejected",
                "reason_code": "evidence_and_inference_accepted" if approved else "evidence_or_inference_rejected",
                "reason_summary": request.get("reason") or ("人工审核通过" if approved else "人工审核未选择"),
            })
        review_input = {
            "review_input_schema_version": "enterprise-capability-candidate-review-input/1.0.0",
            "semantic_analysis_run_id": artifact["semantic_analysis_run_id"],
            "semantic_analysis_content_hash": artifact["semantic_analysis_content_hash"],
            "review_actor": {
                "actor_type": request["reviewer_actor"]["actor_type"],
                "actor_id": request["reviewer_actor"]["actor_id"],
            },
            "review_scope": "full",
            "decisions": decisions,
        }
        generated_at = request.get("reviewed_at_utc") or utc_now()
        review = build_candidate_review_batch(
            context["fact_profile"], context["tag_profile"], context["capability_profile"],
            artifact, review_input, generated_at_utc=generated_at,
        )
        try:
            updated = build_reviewed_capability_profile(
                context["fact_profile"], context["tag_profile"], context["capability_profile"],
                artifact, review, generated_at_utc=generated_at,
            )
        except InputDataError as exc:
            if "semantic_candidate_conflicts_with_existing_claim" not in str(exc):
                raise
            # A semantic suggestion must never overwrite a verified claim with a
            # different value. Keep the suggestion for follow-up instead of
            # exposing an internal validation failure to the user.
            for decision in review_input["decisions"]:
                if decision["decision"] == "approved":
                    decision.update({
                        "decision": "needs_more_evidence",
                        "reason_code": "conflicting_candidate",
                        "reason_summary": "建议与现有已核验能力结论不一致，需要补充材料后再确认。",
                    })
            review = build_candidate_review_batch(
                context["fact_profile"], context["tag_profile"], context["capability_profile"],
                artifact, review_input, generated_at_utc=generated_at,
            )
            updated = build_reviewed_capability_profile(
                context["fact_profile"], context["tag_profile"], context["capability_profile"],
                artifact, review, generated_at_utc=generated_at,
            )
        provider_name = str((artifact.get("provider") or {}).get("provider_name") or "AI").upper()
        self.store.write_profile(task["company_id"], "capability", updated, update_source=f"{provider_name}_SEMANTIC_REVIEW", actor=request.get("reviewer_actor"))
        return updated

    def ai_provider_status(self) -> dict[str, Any]:
        from src.capability_ai.provider_config import CapabilityAIConfig
        from src.capability_ai.provider_registry import registered_provider_names

        try:
            config = CapabilityAIConfig.from_env()
            configured = True
            error = None
            provider = config.provider
            model = config.model_name
            key_configured = bool(config.api_key)
            base_url_configured = bool(config.base_url)
        except Exception as exc:
            provider = "zhipu"
            configured = False
            error = getattr(exc, "code", "capability_ai_config_invalid")
            model = os.getenv("CAPABILITY_AI_MODEL") or None
            key_configured = bool(os.getenv("CAPABILITY_AI_API_KEY"))
            base_url_configured = bool(os.getenv("CAPABILITY_AI_BASE_URL"))
        runs = self.store.list_runs(limit=50)
        llm_runs = [run for run in runs if run.get("llm_node_executed")]
        latest = llm_runs[0] if llm_runs else None
        audit = (latest or {}).get("llm_provider_audit") or {}
        return {
            "provider": provider,
            "configured": configured,
            "api_key_configured": key_configured,
            "model": model,
            "base_url_configured": base_url_configured,
            "network_provider": True,
            "registered_providers": list(registered_provider_names()),
            "last_live_check": audit.get("finished_at_utc"),
            "last_live_check_status": "PASS" if audit.get("validation_result") == "passed" else ("NOT_RUN" if latest is None else "FAILED"),
            "last_agent_run_used_llm": bool(latest),
            "configuration_error": error,
        }

    def review_action(self, task_id: str, action: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            task = self.store.read_review_task(task_id)
        except FileNotFoundError as exc:
            raise ServiceError("REVIEW_TASK_NOT_FOUND", f"Review task not found: {task_id}", status_code=404) from exc
        if task.get("status") != "PENDING":
            raise ServiceError("REVIEW_TASK_ALREADY_HANDLED", "Review task is no longer pending", status_code=409)
        reviewer = request.get("reviewer_actor")
        if not isinstance(reviewer, dict) or not reviewer.get("actor_id"):
            raise ServiceError("REVIEW_ACTOR_REQUIRED", "reviewer_actor.actor_id is required")
        result_profile = None
        if action == "needs-more-information" and not str(request.get("reason") or "").strip():
            raise ServiceError("REVIEW_SUPPLEMENT_REASON_REQUIRED", "选择需要补充信息时必须填写补充原因。")
        status_map = {
            "reject": "REJECTED",
            "needs-more-information": "NEEDS_MORE_INFORMATION",
        }
        if action == "approve":
            if task["review_type"] == "FACT":
                result_profile = self._apply_fact_review(task, request)
                status = "APPROVED"
            elif task["review_type"] == "CAPABILITY":
                result_profile = self._apply_capability_review(task, request)
                status = "APPROVED"
            elif task["review_type"] == "CAPABILITY_SEMANTIC":
                result_profile = self._apply_semantic_capability_review(task, request)
                status = "APPROVED"
            elif task["review_type"] == "UNAVAILABLE":
                status = "ACKNOWLEDGED"
            else:
                status = "APPROVED"
        else:
            status = status_map[action]
        task.setdefault("review_history", [])
        if task.get("review_result") is not None:
            task["review_history"].append(deepcopy(task["review_result"]))
        task.setdefault("supplement_history", [])
        task["status"] = status
        task["review_result"] = {
            "reviewer_actor": deepcopy(reviewer),
            "reviewed_at_utc": request.get("reviewed_at_utc") or utc_now(),
            "reason": request.get("reason"),
            "result": action,
            "resulting_profile": result_profile,
            "review_source": request.get("review_source") or task.get("review_source"),
        }
        task["updated_at_utc"] = utc_now()
        self.store.save_review_task(task)
        self.store.audit("review_task_updated", {
            "review_task_id": task_id,
            "status": status,
            "review_type": task["review_type"],
        })
        if status in {"APPROVED", "REJECTED", "ACKNOWLEDGED"} and task.get("run_id"):
            remaining = [item for item in self.store.list_review_tasks(company_id=task["company_id"]) if item.get("run_id") == task["run_id"] and item.get("status") in {"PENDING", "NEEDS_MORE_INFORMATION"}]
            if not remaining:
                try:
                    try:
                        self.store.get_evaluation_run(task["run_id"])
                    except FileNotFoundError:
                        resumed = self.runner.resume_after_review(task["run_id"])
                        task["resumed_agent_run"] = {
                            "run_id": resumed.get("run_id"),
                            "status": resumed.get("status"),
                            "workflow_state": resumed.get("workflow_state"),
                        }
                    else:
                        resumed = self.evaluation.resume(task["run_id"])
                        task["resumed_evaluation_run"] = {
                            "run_id": (resumed.get("run") or {}).get("run_id"),
                            "status": (resumed.get("run") or {}).get("status"),
                        }
                    self.store.save_review_task(task)
                except (ValueError, FileNotFoundError) as exc:
                    task["resume_warning"] = {"code": "AGENT_REVIEW_RESUME_SKIPPED", "message": str(exc)}
                    self.store.save_review_task(task)
        return task

    # Independent enterprise-evaluation domain. These methods never write scores
    # into fact, capability or decision profiles.
    def evaluation_model(self) -> dict[str, Any]:
        return self.evaluation.model()

    def evaluation_indicators(self) -> list[dict[str, Any]]:
        return self.evaluation.indicators()

    def evaluation_notes(self) -> dict[str, Any]:
        return self.evaluation.notes()

    def evaluation_api_catalog(self) -> dict[str, Any]:
        return self.evaluation.api_catalog()

    def start_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            company_id = payload["company_id"]
            workflow_mode = payload.get("workflow_mode", "legacy")
            provider_mode = "LOCAL_PROFILE"
            if workflow_mode in {"automatic", "interactive"}:
                provider_mode = "LOCAL_PROFILE"
            return self.evaluation.start(
                company_id=company_id,
                provider_id=provider_mode,
                workflow_mode=workflow_mode,
                max_api_calls=0 if workflow_mode in {"automatic", "interactive"} else int(payload.get("max_api_calls", 10)),
                max_rounds=int(payload.get("max_rounds", 3)),
            )
        except FileNotFoundError as exc:
            raise ServiceError("EVALUATION_COMPANY_NOT_FOUND", f"Company not found: {payload.get('company_id')}", status_code=404) from exc

    def get_evaluation_run(self, run_id: str) -> dict[str, Any]:
        try:
            return self.evaluation.get_run(run_id)
        except FileNotFoundError as exc:
            raise ServiceError("EVALUATION_RUN_NOT_FOUND", f"Evaluation run not found: {run_id}", status_code=404) from exc

    def resume_evaluation_run(self, run_id: str) -> dict[str, Any]:
        try:
            return self.evaluation.resume(run_id)
        except FileNotFoundError as exc:
            raise ServiceError("EVALUATION_RUN_NOT_FOUND", f"Evaluation run not found: {run_id}", status_code=404) from exc
        except ValueError as exc:
            raise ServiceError("EVALUATION_RUN_NOT_RESUMABLE", str(exc), status_code=409) from exc

    def submit_evaluation_answers(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._mutation_lock:
            return self._submit_evaluation_answers_locked(run_id, payload)

    def _submit_evaluation_answers_locked(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.get_evaluation_run(run_id)
        company_id = run["company_id"]
        context = self.store.read_company_context(company_id)
        now = utc_now()
        facts = list(context["fact_profile"].get("facts") or [])
        evidence_index = deepcopy(context["fact_profile"].get("evidence_index") or {})
        applied: list[str] = []
        skipped: list[str] = []
        unsupported: list[str] = []
        risk_categories = {
            "C1.1": "dishonesty", "C1.2": "enforcement", "C1.3": "terminal_case",
            "C1.5": "litigation", "C2.1": "administrative_penalty",
            "C2.2": "abnormal_operation", "C2.3": "tax_violation",
            "C2.4": "environment_safety", "C2.5": "bankruptcy_liquidation",
            "C3.1": "pledge_freeze", "C3.2": "key_person",
            "C3.3": "negative_news", "C3.5": "supply_chain",
        }
        for item in payload.get("answers") or []:
            code = str(item.get("indicator_code") or "")
            if item.get("action") == "skip":
                skipped.append(code)
                continue
            material = str(item.get("material_reference") or "").strip()
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            if not material:
                unsupported.append(code)
                continue
            # An interaction answer is the current value for one company/indicator,
            # not an append-only event. A stable identity makes a second round an
            # update and prevents old answers from reappearing after reopening.
            evidence_id = f"user_upload:evaluation-interaction:{hashlib.sha256(f'{company_id}:{code}'.encode()).hexdigest()[:24]}"
            evidence_row_number = int(hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:8], 16)
            evidence_index[evidence_id] = {
                "evidence_schema_version": "enterprise-profile-evidence/1.1.0",
                "evidence_id": evidence_id, "legacy_evidence_id": None,
                "source_type": "user_upload", "source_dataset_id": "evaluation-interaction",
                "source_file": material, "source_sheet": "evaluation_answers",
                "source_row_number": evidence_row_number, "source_locator": code,
                "announcement_unique_id": None, "project_number": None, "source_url": None,
                "record_time": {"published_at": None, "tender_end_at": None, "opening_at": None},
                "collected_at": now, "collected_at_status": "provided",
                "is_mock": False, "anonymized": False, "record_status": "valid",
                "quality_flags": ["user_confirmed_evaluation_input"],
            }
            fact_type = ""
            fact_payload: dict[str, Any] = {}
            category = "other"
            if code == "A1.3":
                registered = values.get("registered_capital")
                paid = values.get("paid_in_capital")
                if not isinstance(registered, (int, float)) or registered <= 0 or not isinstance(paid, (int, float)):
                    unsupported.append(code); continue
                existing = next((fact for fact in facts if fact.get("fact_type") == "business_registration"), None)
                enterprise = context["fact_profile"].get("enterprise") or {}
                fact_payload = {
                    **deepcopy((existing or {}).get("payload") or {}),
                    "enterprise_name": (
                        ((existing or {}).get("payload") or {}).get("enterprise_name")
                        or enterprise.get("name")
                    ),
                    "unified_social_credit_code": (
                        ((existing or {}).get("payload") or {}).get("unified_social_credit_code")
                        or enterprise.get("unified_social_credit_code")
                    ),
                    "registered_capital": registered,
                    "paid_in_capital": paid,
                }
                if not fact_payload["enterprise_name"]:
                    unsupported.append(code); continue
                fact_type, category = "business_registration", "business_registration"
            elif code == "A3.2":
                rating = str(values.get("rating") or "").upper()
                if rating not in {"A", "B", "M", "C", "D"}:
                    unsupported.append(code); continue
                fact_type, category = "risk_penalty_credit", "risk_credit"
                fact_payload = {"category": "tax_credit", "rating_type": "tax_credit_rating", "rating_value": rating}
            elif code in {"A3.3", "C3.4"}:
                required = ("qualification_name", "certificate_number", "issuer", "valid_until")
                if not all(values.get(key) for key in required):
                    unsupported.append(code); continue
                fact_type, category = "qualification", "qualification_registry"
                fact_payload = {"name": values["qualification_name"], "certificate_number": values["certificate_number"], "issuing_authority": values["issuer"], "valid_until": values["valid_until"], "status": "valid"}
            elif code in risk_categories:
                status = str(values.get("risk_status") or "")
                if status not in {"none", "resolved", "current"}:
                    unsupported.append(code); continue
                fact_type, category = "risk_penalty_credit", "risk_credit"
                fact_payload = {"category": risk_categories[code], "status": status, "records": [] if status == "none" else [{"status": status, "resolved": status == "resolved", "current_effective": status == "current"}]}
            else:
                situation = str(values.get("current_situation") or "").strip()
                if not situation:
                    unsupported.append(code); continue
                fact_type, category = "other_enterprise_fact", "other"
                fact_payload = {
                    "subtype": "evaluation_indicator_evidence",
                    "business_description": situation,
                    "details": {"indicator_code": code, "fields": {"用户补充情况": situation}},
                }
            replaced_evidence_ids: set[str] = set()
            retained_facts: list[dict[str, Any]] = []
            for existing_fact in facts:
                existing_source = existing_fact.get("source") or {}
                existing_payload = existing_fact.get("payload") or {}
                is_interaction = existing_source.get("dataset_id") == "evaluation-interaction"
                existing_code = None
                if existing_payload.get("subtype") == "evaluation_indicator_evidence":
                    existing_code = (existing_payload.get("details") or {}).get("indicator_code")
                same_stable_evidence = evidence_id in (existing_fact.get("evidence_ids") or [])
                if is_interaction and (existing_code == code or same_stable_evidence):
                    replaced_evidence_ids.update(existing_fact.get("evidence_ids") or [])
                    continue
                retained_facts.append(existing_fact)
            facts = retained_facts
            for replaced_id in replaced_evidence_ids:
                if replaced_id != evidence_id:
                    evidence_index.pop(replaced_id, None)
            fact = build_fact(
                enterprise=context["fact_profile"]["enterprise"], fact_type=fact_type, payload=fact_payload,
                source={"source_type": "user_upload", "source_data_category": category, "source_platform": "enterprise_profile_web", "source_record_id": evidence_id, "source_url": None, "dataset_id": "evaluation-interaction"},
                temporal={"collected_at": now, "verified_at": now, "available_at": now, "availability_basis": "user_received_at", "availability_status": "known"},
                verification_status="partially_verified", evidence_ids=[evidence_id],
                quality_flags=["user_confirmed_with_material_reference"], created_from="normalized_input",
            )
            facts.insert(0, fact)
            applied.append(code)
        if applied:
            fact_profile = deepcopy(context["fact_profile"])
            fact_profile["facts"] = facts
            fact_profile["evidence_index"] = evidence_index
            fact_profile["generated_at_utc"] = now
            fact_profile["as_of_date"] = now[:10]
            fact_profile["as_of_date_status"] = "provided_input"
            fact_profile["conflicts"] = detect_fact_conflicts(facts)
            view, warnings = build_fact_view(facts, fact_profile["as_of_date"])
            fact_profile["fact_view"] = view
            fact_profile["fact_summary"], fact_profile["source_summary"] = _summaries(facts)
            fact_profile["warnings"] = list(dict.fromkeys(list(fact_profile.get("warnings") or []) + warnings))
            assert_valid_fact_profile(fact_profile)
            tag = generate_tag_profile(fact_profile, load_tag_catalog(ROOT / "config" / "enterprise_profile_tags.json"))
            capability = build_capability_profile(fact_profile, tag)
            assert_valid_capability_profile(capability)
            self.store.write_profile(company_id, "fact", fact_profile, update_source="EVALUATION_INTERACTION", updated_fields=applied, actor={"actor_type": "user", "actor_id": "LOCAL_OPERATOR"})
            self.store.write_profile(company_id, "tag", tag, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=applied)
            self.store.write_profile(company_id, "capability", capability, update_source="FACT_PROFILE_RECOMPUTE", updated_fields=applied)
        refreshed = self.evaluation.start(company_id=company_id, provider_id="LOCAL_PROFILE", workflow_mode="interactive", max_api_calls=0, max_rounds=3)
        return {"applied_indicator_codes": applied, "skipped_indicator_codes": skipped, "unsupported_indicator_codes": unsupported, **refreshed}

    def latest_evaluation(self, company_id: str) -> dict[str, Any]:
        value = self.evaluation.latest_profile(company_id)
        if value is None:
            raise ServiceError("EVALUATION_NOT_FOUND", f"No evaluation profile for {company_id}", status_code=404)
        return value

    def evaluation_history(self, company_id: str) -> list[dict[str, Any]]:
        return self.evaluation.history(company_id)

    def latest_evaluation_card(self, company_id: str) -> dict[str, Any]:
        value = self.evaluation.latest_card(company_id)
        if value is None:
            raise ServiceError("EVALUATION_CARD_NOT_FOUND", f"No evaluation card for {company_id}", status_code=404)
        return value

    def evaluation_data_gaps(self, company_id: str) -> dict[str, Any]:
        return self.evaluation.data_gaps(company_id)

    def evaluation_api_plan(self, company_id: str) -> dict[str, Any]:
        value = self.evaluation.latest_plan(company_id)
        if value is None:
            raise ServiceError("EVALUATION_PLAN_NOT_FOUND", f"No evaluation plan for {company_id}", status_code=404)
        return value


    def evaluation_providers(self) -> list[dict[str, Any]]:
        return self.evaluation.provider_descriptors()

    def evaluation_normalizers(self) -> list[dict[str, Any]]:
        return self.evaluation.normalizer_descriptors()

    def evaluation_graph(self) -> dict[str, Any]:
        return self.evaluation.graph_description()

    def enterprise_search(self, provider_id: str, keyword: str) -> dict[str, Any]:
        try:
            return self.evaluation.search_enterprises(provider_id=provider_id, keyword=keyword)
        except KeyError as exc:
            raise ServiceError("EVALUATION_PROVIDER_NOT_REGISTERED", str(exc), status_code=400) from exc

    def confirm_enterprise_identity(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.evaluation.confirm_identity(payload)
        except (ValueError, KeyError) as exc:
            raise ServiceError("ENTERPRISE_IDENTITY_CONFIRMATION_FAILED", str(exc), status_code=400) from exc
