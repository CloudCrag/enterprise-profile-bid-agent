from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from src.decision_candidate_application.application_builder import apply_decision_candidates as apply_selected_candidates
from src.ability_analysis_request import build_capability_analysis_request
from src.capability_ai.errors import CapabilityAIError
from src.capability_ai.provider_config import CapabilityAIConfig
from src.capability_ai.semantic_analysis_service import SemanticCapabilityAnalysisService
from src.capability_candidate_review import build_candidate_review_batch
from src.capability_profile_builder import build_capability_profile
from src.capability_review_pipeline import build_reviewed_capability_profile
from src.errors import InputDataError
from src.profile_gap.gap_analyzer import analyze_profile_gaps as core_analyze_gaps
from src.profile_questions.planner import build_question_plan as core_build_question_plan
from src.profile_response_processing.worklist_builder import build_response_processing_worklist
from src.profile_responses.receipt_builder import build_question_response_receipt

from ..app.constants import (
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_COMPLETED_WITH_PENDING_REVIEW,
    AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED,
    AGENT_STATUS_RUNNING,
    AGENT_STATUS_WAITING_REVIEW,
    AGENT_STATUS_WAITING_USER_INPUT,
    DECISION_CONTEXT_PAUSE_DECISION_LAYER,
)
from ..app.decision_context import decision_context_diagnostic
from ..app.profile_change_orchestrator import ProfileChangeOrchestrator
from ..app.repositories import ProfileRepository
from ..app.local_store import utc_now
from .tools import (
    build_application_request,
    build_gap_request,
    build_plan_request,
    build_submission_from_responses,
    stable_id,
)


def _small_summary(value: Any) -> Any:
    if isinstance(value, dict):
        keep = {}
        for key in (
            "gap_inventory_id", "question_plan_id", "question_response_submission_id",
            "question_response_receipt_id", "response_processing_worklist_id",
            "application_result_id", "decision_profile_id", "status", "plan_status",
        ):
            if key in value:
                keep[key] = value[key]
        for key in ("gap_summary", "plan_summary", "receipt_summary", "processing_summary", "decision_summary"):
            if key in value:
                keep[key] = value[key]
        return keep or {"keys": sorted(value.keys())[:12]}
    if isinstance(value, list):
        return {"count": len(value)}
    return value


class ProfileAgentNodes:
    def __init__(self, store: ProfileRepository) -> None:
        self.store = store
        self.orchestrator = ProfileChangeOrchestrator(store)

    def _run_node(self, state: dict[str, Any], name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        started = utc_now()
        state["current_node"] = name
        trace = list(state.get("trace") or [])
        try:
            update = fn() or {}
            completed = utc_now()
            trace.append({
                "node_name": name,
                "status": "SUCCEEDED",
                "started_at_utc": started,
                "completed_at_utc": completed,
                "input_summary": {
                    "run_id": state.get("run_id"),
                    "company_id": state.get("company_id"),
                    "mode": state.get("mode"),
                },
                "output_summary": _small_summary(next(iter(update.values()), update)),
                "error": None,
            })
            update["trace"] = trace
            update["current_node"] = name
            return update
        except Exception as exc:
            completed = utc_now()
            trace.append({
                "node_name": name,
                "status": "FAILED",
                "started_at_utc": started,
                "completed_at_utc": completed,
                "input_summary": {"run_id": state.get("run_id"), "company_id": state.get("company_id")},
                "output_summary": {},
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })
            state.update({
                "trace": trace,
                "current_node": name,
                "status": AGENT_STATUS_FAILED,
                "workflow_state": AGENT_STATUS_FAILED,
                "error": {"code": "PROFILE_AGENT_NODE_FAILED", "message": str(exc), "node": name},
            })
            self.store.save_run(state)
            raise

    def load_company_context(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            context = self.store.read_company_context(state["company_id"])
            req = state.get("task_requirements") or (context.get("task_requirements") or {}).get("requirements") or []
            return {
                "fact_profile": context["fact_profile"],
                "tag_profile": context["tag_profile"],
                "capability_profile": context["capability_profile"],
                "decision_profile": context["decision_profile"],
                "task_requirements": deepcopy(req),
                "status": AGENT_STATUS_RUNNING,
                "workflow_state": AGENT_STATUS_RUNNING,
                "warnings": list(state.get("warnings") or []),
            }
        return self._run_node(state, "load_company_context", work)

    def analyze_capability_semantics_with_llm(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            capability_profile = state["capability_profile"]
            if capability_profile.get("as_of_date") != state["fact_profile"].get("as_of_date"):
                capability_profile = build_capability_profile(state["fact_profile"], state["tag_profile"])
                self.store.write_profile(
                    state["company_id"],
                    "capability",
                    capability_profile,
                    update_source="FACT_PROFILE_DEPENDENCY_REALIGN",
                    actor={"actor_type": "system", "actor_id": "profile-agent"},
                )
            config = CapabilityAIConfig.from_env()
            requested = list(state.get("requested_capability_types") or []) or None
            analysis_request = build_capability_analysis_request(
                state["fact_profile"], state["tag_profile"], capability_profile,
                requested_capability_types=requested,
            )
            result = SemanticCapabilityAnalysisService(config).run(analysis_request)
            artifact = result.candidates
            candidates = artifact.get("capability_candidates") or []
            audit = {**result.audit, "provider_call": deepcopy(result.provider_call_audit or {})}
            if not candidates:
                return {"llm_node_executed": True, "llm_semantic_candidates": artifact, "llm_provider_audit": audit, "llm_review_completed": True, "capability_profile": capability_profile}
            generated_at = utc_now()
            review_input = {
                "review_input_schema_version": "enterprise-capability-candidate-review-input/1.0.0",
                "semantic_analysis_run_id": artifact["semantic_analysis_run_id"],
                "semantic_analysis_content_hash": artifact["semantic_analysis_content_hash"],
                "review_actor": {
                    "actor_type": "human",
                    "actor_id": "LOCAL_OPERATOR",
                },
                "review_scope": "full",
                "decisions": [{
                    "candidate_id": candidate["candidate_id"],
                    "candidate_content_hash": candidate["candidate_content_hash"],
                    "decision": "approved",
                    "reason_code": "evidence_and_inference_accepted",
                    "reason_summary": "模型输出已通过结构、事实引用和证据约束校验。",
                } for candidate in candidates],
            }
            review = build_candidate_review_batch(
                state["fact_profile"],
                state["tag_profile"],
                capability_profile,
                artifact,
                review_input,
                generated_at_utc=generated_at,
            )
            try:
                updated = build_reviewed_capability_profile(
                    state["fact_profile"],
                    state["tag_profile"],
                    capability_profile,
                    artifact,
                    review,
                    generated_at_utc=generated_at,
                )
            except InputDataError as exc:
                if "semantic_candidate_conflicts_with_existing_claim" not in str(exc):
                    raise
                # Verified deterministic claims remain authoritative. Conflicting
                # model inferences are discarded without creating a user-facing
                # review task or blocking the rest of the profile refresh.
                for decision in review_input["decisions"]:
                    decision.update({
                        "decision": "rejected",
                        "reason_code": "conflicting_candidate",
                        "reason_summary": "该模型推断与已核验事实计算结果不一致，未写入能力画像。",
                    })
                review = build_candidate_review_batch(
                    state["fact_profile"],
                    state["tag_profile"],
                    capability_profile,
                    artifact,
                    review_input,
                    generated_at_utc=generated_at,
                )
                updated = build_reviewed_capability_profile(
                    state["fact_profile"],
                    state["tag_profile"],
                    capability_profile,
                    artifact,
                    review,
                    generated_at_utc=generated_at,
                )
            provider_name = str((artifact.get("provider") or {}).get("provider_name") or "AI").upper()
            self.store.write_profile(
                state["company_id"],
                "capability",
                updated,
                update_source=f"{provider_name}_SEMANTIC_AUTO",
                actor={"actor_type": "system", "actor_id": "profile-agent"},
            )
            return {
                "llm_node_executed": True,
                "llm_semantic_candidates": artifact,
                "llm_provider_audit": audit,
                "capability_profile": updated,
                "llm_review_completed": True,
                "pending_review_items": [],
                "status": AGENT_STATUS_RUNNING,
                "workflow_state": AGENT_STATUS_RUNNING,
                "waiting_for": None,
            }
        return self._run_node(state, "analyze_capability_semantics_with_llm", work)

    def analyze_profile_gaps(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            request = build_gap_request(
                state["fact_profile"]["enterprise"],
                state["task_requirements"],
                task_context_id=f"section6:{state['run_id']}:round-{int(state.get('round_index') or 0) + 1}",
                task_goal="完成当前任务所需的企业画像信息核对",
            )
            decision_profile = state.get("decision_profile") if state.get("include_decision_questions", True) else None
            diagnostic = decision_context_diagnostic(
                state["fact_profile"], state["capability_profile"], decision_profile
            )
            try:
                inventory = core_analyze_gaps(
                    request,
                    state["fact_profile"],
                    state["capability_profile"],
                    decision_profile=decision_profile,
                )
            except InputDataError as exc:
                if not any(code in str(exc) for code in (
                    "gap_decision_context_dependency_mismatch",
                    "gap_context_as_of_date_mismatch: decision profile as_of_date differs",
                )):
                    raise
                filtered = [item for item in state["task_requirements"] if item.get("target_layer") != "decision"]
                filtered_request = build_gap_request(
                    state["fact_profile"]["enterprise"],
                    filtered,
                    task_context_id=f"section6:{state['run_id']}:stale-decision",
                    task_goal="决策上下文待重新确认；核对事实和能力缺口",
                )
                inventory = core_analyze_gaps(
                    filtered_request,
                    state["fact_profile"],
                    state["capability_profile"],
                    decision_profile=None,
                )
                inventory.setdefault("warnings", []).append({
                    "code": "DECISION_CONTEXT_RECONFIRMATION_REQUIRED",
                    "message": "事实或能力画像已经更新，当前决策画像基于旧版本，需要重新确认。",
                    "details": diagnostic,
                })
                self.store.save_gap_inventory(state["company_id"], inventory)
                if state.get("decision_context_policy") == DECISION_CONTEXT_PAUSE_DECISION_LAYER and filtered:
                    return {
                        "gap_analysis_request": filtered_request,
                        "gap_inventory": inventory,
                        "decision_context_status": diagnostic,
                        "warnings": list(state.get("warnings") or []) + inventory["warnings"],
                        "status": AGENT_STATUS_RUNNING,
                    }
                return {
                    "gap_analysis_request": filtered_request,
                    "gap_inventory": inventory,
                    "remaining_gap_inventory": inventory,
                    "latest_gap_inventory": inventory,
                    "decision_context_status": diagnostic,
                    "warnings": list(state.get("warnings") or []) + inventory["warnings"],
                    "status": AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
                    "workflow_state": AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
                    "waiting_for": "decision_reconfirmation",
                    "termination_reason": "DECISION_CONTEXT_STALE",
                    "next_round_required": False,
                }
            self.store.save_gap_inventory(state["company_id"], inventory)
            snapshots = list(state.get("gap_snapshots") or []) + [{
                "round_index": int(state.get("round_index") or 0) + 1,
                "phase": "INITIAL_ANALYSIS" if not state.get("round_index") else "ROUND_ANALYSIS",
                "gap_inventory_id": inventory.get("gap_inventory_id"),
                "gap_inventory_content_hash": inventory.get("gap_inventory_content_hash"),
                "gap_summary": deepcopy(inventory.get("gap_summary") or {}),
                "captured_at_utc": utc_now(),
            }]
            return {
                "gap_analysis_request": request,
                "gap_inventory": inventory,
                "latest_gap_inventory": inventory,
                "decision_context_status": diagnostic,
                "gap_snapshots": snapshots,
            }
        return self._run_node(state, "analyze_profile_gaps", work)

    def build_question_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            previous_plan_id = (state.get("question_plan") or {}).get("question_plan_id")
            request = build_plan_request(
                state["fact_profile"]["enterprise"],
                state["gap_inventory"],
                max_questions_per_batch=int(state.get("max_questions_per_batch") or 3),
                include_optional=bool(state.get("include_optional", False)),
            )
            plan = core_build_question_plan(
                request,
                state["gap_inventory"],
                state["gap_analysis_request"],
                state["fact_profile"],
                state["capability_profile"],
                decision_profile=state.get("decision_profile") if state.get("include_decision_questions", True) else None,
            )
            round_index = int(state.get("round_index") or 0) + 1
            fingerprint_source = [
                (item.get("target_layer"), item.get("target_code"), item.get("gap_status"))
                for item in plan.get("question_items") or []
            ]
            fingerprint = hashlib.sha256(json.dumps(fingerprint_source, sort_keys=True).encode("utf-8")).hexdigest()
            rounds = list(state.get("rounds") or []) + [{
                "round_index": round_index,
                "parent_question_plan_id": previous_plan_id,
                "gap_inventory_id": state["gap_inventory"].get("gap_inventory_id"),
                "gap_snapshot": deepcopy(state["gap_inventory"].get("gap_summary") or {}),
                "question_plan": deepcopy(plan),
                "created_at_utc": utc_now(),
            }]
            return {
                "question_plan_request": request,
                "question_plan": plan,
                "parent_question_plan_id": previous_plan_id,
                "round_index": round_index,
                "rounds": rounds,
                "question_plan_fingerprints": list(state.get("question_plan_fingerprints") or []) + [fingerprint],
                "next_round_required": False,
            }
        return self._run_node(state, "build_question_plan", work)

    def wait_for_user_response(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            return {"status": AGENT_STATUS_WAITING_USER_INPUT, "workflow_state": AGENT_STATUS_WAITING_USER_INPUT, "waiting_for": "responses"}
        return self._run_node(state, "wait_for_user_response", work)

    def record_response(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            payload = state.get("user_responses") or {}
            decision_profile = state.get("decision_profile") if state.get("include_decision_questions", True) else None
            submission = build_submission_from_responses(
                question_plan=state["question_plan"],
                decision_profile=decision_profile,
                payload=payload,
                response_source=state.get("response_source") or "USER",
            )
            # response_source is local audit metadata; formal validator sees the schema fields only.
            formal_submission = {k: v for k, v in submission.items() if k != "response_source"}
            receipt = build_question_response_receipt(
                formal_submission,
                state["question_plan"],
                state["question_plan_request"],
                state["gap_inventory"],
                state["gap_analysis_request"],
                state["fact_profile"],
                state["capability_profile"],
                decision_profile=decision_profile,
            )
            receipt["response_source"] = state.get("response_source") or "USER"
            return {"response_submission": submission, "response_receipt": receipt, "status": "RUNNING", "waiting_for": None}
        return self._run_node(state, "record_response", work)

    def route_response_processing(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            submission = {k: v for k, v in state["response_submission"].items() if k != "response_source"}
            receipt = {k: v for k, v in state["response_receipt"].items() if k != "response_source"}
            worklist = build_response_processing_worklist(
                receipt,
                submission,
                state["question_plan"],
                state["question_plan_request"],
                state["gap_inventory"],
                state["gap_analysis_request"],
                state["fact_profile"],
                state["capability_profile"],
                decision_profile=state.get("decision_profile") if state.get("include_decision_questions", True) else None,
            )
            unavailable = set(state.get("unavailable_target_keys") or [])
            for item in worklist.get("processing_items") or []:
                if item.get("processing_status") == "unavailable_recorded":
                    unavailable.add(f"{item.get('target_layer')}:{item.get('target_code')}")
            return {"processing_worklist": worklist, "unavailable_target_keys": sorted(unavailable)}
        return self._run_node(state, "route_response_processing", work)

    def apply_decision_candidates(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            candidates = [
                item.get("decision_update_candidate")
                for item in (state.get("processing_worklist") or {}).get("processing_items") or []
                if isinstance(item, dict) and isinstance(item.get("decision_update_candidate"), dict)
            ]
            candidate_ids = [c["candidate_id"] for c in candidates]
            if not candidate_ids:
                return {"selected_decision_candidate_ids": [], "waiting_for": None}
            selected = list(state.get("selected_decision_candidate_ids") or [])
            if not selected:
                return {
                    "status": AGENT_STATUS_WAITING_USER_INPUT,
                    "workflow_state": AGENT_STATUS_WAITING_USER_INPUT,
                    "waiting_for": "decision_selection",
                    "selected_decision_candidate_ids": [],
                }
            request = build_application_request(
                state["fact_profile"]["enterprise"],
                state["processing_worklist"],
                selected,
            )
            submission = {k: v for k, v in state["response_submission"].items() if k != "response_source"}
            receipt = {k: v for k, v in state["response_receipt"].items() if k != "response_source"}
            new_profile, result = apply_selected_candidates(
                request,
                state["processing_worklist"],
                receipt,
                submission,
                state["question_plan"],
                state["question_plan_request"],
                state["gap_inventory"],
                state["gap_analysis_request"],
                state["fact_profile"],
                state["capability_profile"],
                base_decision_profile=state.get("decision_profile"),
            )
            orchestration = self.orchestrator.apply_decision_profile(
                state["company_id"],
                new_profile,
                update_source="USER_SELECTED_DECISION_CANDIDATES",
                updated_fields=result["application_summary"]["updated_field_codes"],
                actor=(result.get("aggregate_confirmation_input") or {}).get("confirmation_actor"),
            )
            return {
                "selected_decision_candidate_ids": selected,
                "decision_application_request": request,
                "decision_application_result": result,
                "updated_decision_profile": new_profile,
                "decision_profile": new_profile,
                                "waiting_for": None,
                "status": AGENT_STATUS_RUNNING,
                "latest_gap_inventory": orchestration.get("remaining_gap_inventory"),
            }
        return self._run_node(state, "apply_decision_candidates", work)

    def create_review_tasks(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            tasks: list[dict[str, Any]] = []
            response_map = {
                item.get("response_item_id"): item
                for item in (state.get("response_receipt") or {}).get("recorded_response_items") or []
                if isinstance(item, dict)
            }
            type_map = {
                "evidence_processing_required": "FACT",
                "conflict_review_required": "CONFLICT",
                "capability_review_required": "CAPABILITY",
                "unavailable_recorded": "UNAVAILABLE",
            }
            for item in (state.get("processing_worklist") or {}).get("processing_items") or []:
                status = item.get("processing_status")
                if status not in type_map:
                    continue
                task_id = stable_id("review-task", {
                    "run_id": state["run_id"],
                    "processing_item_id": item.get("processing_item_id"),
                })
                source_response = deepcopy(response_map.get(item.get("response_item_id")) or {})
                task = {
                    "review_task_id": task_id,
                    "company_id": state["company_id"],
                    "run_id": state["run_id"],
                    "processing_item_id": item.get("processing_item_id"),
                    "source_gap_id": item.get("gap_id"),
                    "question_item_id": item.get("question_item_id"),
                    "review_type": type_map[status],
                    "target_layer": item.get("target_layer"),
                    "target_code": item.get("target_code"),
                    "status": "PENDING",
                    "source_response": source_response,
                    "material_references": deepcopy(item.get("material_references") or []),
                    "suggested_payload": deepcopy((source_response.get("answer") or {}).get("value")),
                    "review_result": None,
                    "review_history": [],
                    "supplement_history": [],
                    "review_source": "USER",
                    "created_at_utc": utc_now(),
                    "updated_at_utc": utc_now(),
                }
                self.store.save_review_task(task)
                tasks.append(task)
            return {"pending_review_items": tasks}
        return self._run_node(state, "create_review_tasks", work)

    def rescan_gaps(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            context = self.store.read_company_context(state["company_id"])
            fact = context["fact_profile"]
            capability = context["capability_profile"]
            decision = context["decision_profile"] if state.get("include_decision_questions", True) else None
            full_request = build_gap_request(
                fact["enterprise"],
                state.get("task_requirements") or [],
                task_context_id=f"section6-rescan:{state['run_id']}:round-{state.get('round_index')}",
                task_goal="重新核对当前任务相关企业画像缺口",
            )
            diagnostic = decision_context_diagnostic(fact, capability, decision)
            try:
                remaining = core_analyze_gaps(full_request, fact, capability, decision_profile=decision)
                warnings = list(state.get("warnings") or [])
            except InputDataError as exc:
                if not any(code in str(exc) for code in (
                    "gap_decision_context_dependency_mismatch",
                    "gap_context_as_of_date_mismatch: decision profile as_of_date differs",
                )):
                    raise
                filtered = [item for item in state.get("task_requirements") or [] if item.get("target_layer") != "decision"]
                filtered_request = build_gap_request(
                    fact["enterprise"], filtered,
                    task_context_id=f"section6-review-rescan:{state['run_id']}",
                    task_goal="决策画像上下文待用户重新确认；复扫事实与能力缺口",
                )
                remaining = core_analyze_gaps(filtered_request, fact, capability, decision_profile=None)
                remaining.setdefault("warnings", []).append({
                    "code": "DECISION_CONTEXT_RECONFIRMATION_REQUIRED",
                    "message": "事实或能力画像已经更新，当前决策画像基于旧版本，需要重新确认。",
                    "details": diagnostic,
                })
                warnings = list(state.get("warnings") or []) + remaining["warnings"]
                self.store.save_gap_inventory(state["company_id"], remaining)
                if state.get("decision_context_policy") != DECISION_CONTEXT_PAUSE_DECISION_LAYER:
                    return {
                        "fact_profile": fact,
                        "tag_profile": context["tag_profile"],
                        "capability_profile": capability,
                        "decision_profile": decision,
                        "remaining_gap_inventory": remaining,
                        "latest_gap_inventory": remaining,
                        "decision_context_status": diagnostic,
                        "warnings": warnings,
                        "status": AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
                        "workflow_state": AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
                        "waiting_for": "decision_reconfirmation",
                        "termination_reason": "DECISION_CONTEXT_STALE",
                        "next_round_required": False,
                    }
            self.store.save_gap_inventory(state["company_id"], remaining)
            snapshots = list(state.get("gap_snapshots") or []) + [{
                "round_index": int(state.get("round_index") or 0),
                "phase": "POST_RESPONSE_RESCAN",
                "gap_inventory_id": remaining.get("gap_inventory_id"),
                "gap_inventory_content_hash": remaining.get("gap_inventory_content_hash"),
                "gap_summary": deepcopy(remaining.get("gap_summary") or {}),
                "captured_at_utc": utc_now(),
            }]
            gaps = list(remaining.get("gaps") or [])
            pending_keys = {
                f"{item.get('target_layer')}:{item.get('target_code')}"
                for item in state.get("pending_review_items") or []
                if item.get("status") in {None, "PENDING", "NEEDS_MORE_INFORMATION"}
            }
            unavailable_keys = set(state.get("unavailable_target_keys") or [])
            actionable_gap_keys = {
                f"{gap.get('target_layer')}:{gap.get('target_code')}"
                for gap in gaps
                if f"{gap.get('target_layer')}:{gap.get('target_code')}" not in pending_keys | unavailable_keys
            }
            update: dict[str, Any] = {
                "fact_profile": fact,
                "tag_profile": context["tag_profile"],
                "capability_profile": capability,
                "decision_profile": decision,
                "remaining_gap_inventory": remaining,
                "latest_gap_inventory": remaining,
                "decision_context_status": diagnostic,
                "warnings": warnings,
                "gap_snapshots": snapshots,
                "next_round_required": False,
            }
            if not gaps:
                update.update({"status": AGENT_STATUS_COMPLETED, "workflow_state": AGENT_STATUS_COMPLETED, "termination_reason": "NO_TASK_RELEVANT_GAPS"})
                return update
            if not actionable_gap_keys:
                if pending_keys:
                    update.update({
                        "status": AGENT_STATUS_COMPLETED_WITH_PENDING_REVIEW,
                        "workflow_state": AGENT_STATUS_WAITING_REVIEW,
                        "waiting_for": "review",
                        "termination_reason": "ONLY_PENDING_REVIEW_GAPS",
                    })
                else:
                    update.update({"status": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "workflow_state": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "termination_reason": "ONLY_UNAVAILABLE_GAPS_REMAIN"})
                return update
            if int(state.get("round_index") or 0) >= int(state.get("max_rounds") or 1):
                update.update({"status": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "workflow_state": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "termination_reason": "MAX_ROUNDS_REACHED"})
                return update
            actionable_requirements = [
                item for item in state.get("task_requirements") or []
                if f"{item.get('target_layer')}:{item.get('target_code')}" in actionable_gap_keys
            ]
            actionable_request = build_gap_request(
                fact["enterprise"], actionable_requirements,
                task_context_id=f"section6-next-round:{state['run_id']}:{int(state.get('round_index') or 0)+1}",
                task_goal="继续补充尚未进入审核且仍影响当前任务的画像缺口",
            )
            actionable_inventory = core_analyze_gaps(
                actionable_request, fact, capability,
                decision_profile=decision if not diagnostic.get("is_stale") else None,
            )
            source = [(g.get("target_layer"), g.get("target_code"), g.get("gap_status")) for g in actionable_inventory.get("gaps") or []]
            fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()
            if state.get("question_plan_fingerprints") and fingerprint == state["question_plan_fingerprints"][-1]:
                update.update({"status": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "workflow_state": AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED, "termination_reason": "UNCHANGED_QUESTION_SET"})
                return update
            update.update({
                "gap_analysis_request": actionable_request,
                "gap_inventory": actionable_inventory,
                "status": AGENT_STATUS_RUNNING,
                "workflow_state": AGENT_STATUS_RUNNING,
                "waiting_for": None,
                "termination_reason": None,
                "next_round_required": True,
            })
            return update
        return self._run_node(state, "rescan_gaps", work)

    def persist_run(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            self.store.save_run(state)
            self.store.audit("agent_run_persisted", {
                "run_id": state["run_id"],
                "company_id": state["company_id"],
                "status": state.get("status"),
            })
            return {}
        return self._run_node(state, "persist_run", work)

    def finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            preserved = {
                AGENT_STATUS_WAITING_USER_INPUT,
                AGENT_STATUS_WAITING_REVIEW,
                AGENT_STATUS_MANUAL_INTERVENTION_REQUIRED,
                AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED,
                AGENT_STATUS_FAILED,
                AGENT_STATUS_COMPLETED,
            }
            if state.get("status") in preserved:
                status = state["status"]
            elif state.get("pending_review_items"):
                status = AGENT_STATUS_COMPLETED_WITH_PENDING_REVIEW
            else:
                status = AGENT_STATUS_COMPLETED
            workflow_state = state.get("workflow_state") if state.get("status") in preserved else status
            waiting = state.get("waiting_for") if workflow_state in {AGENT_STATUS_WAITING_USER_INPUT, AGENT_STATUS_WAITING_REVIEW, AGENT_STATUS_DECISION_CONTEXT_RECONFIRMATION_REQUIRED} else None
            update = {"status": status, "workflow_state": workflow_state, "waiting_for": waiting}
            merged = dict(state)
            merged.update(update)
            self.store.save_run(merged)
            return update
        return self._run_node(state, "finalize", work)
