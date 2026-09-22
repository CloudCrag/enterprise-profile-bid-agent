from __future__ import annotations

from src.config.environment_loader import load_project_environment

load_project_environment()

from datetime import datetime, timezone
import hashlib
from typing import Any

from src.enterprise_capability_profile import canonical_json

from ..app.constants import AGENT_STATUS_RUNNING
from ..app.repositories import ProfileRepository
from ..app.local_store import utc_now
from .graph import LANGGRAPH_BACKEND, build_profile_agent_graph
from .nodes import ProfileAgentNodes


def _run_id(company_id: str, mode: str) -> str:
    seed = {"company_id": company_id, "mode": mode, "started_at_utc": utc_now()}
    return f"profile-agent-run:{hashlib.sha256(canonical_json(seed)).hexdigest()[:24]}"


class ProfileAgentRunner:
    def __init__(self, store: ProfileRepository) -> None:
        self.store = store
        self.nodes = ProfileAgentNodes(store)

    @property
    def langgraph_backend(self) -> str:
        return LANGGRAPH_BACKEND

    def _invoke(self, state: dict[str, Any], *, entry: str) -> dict[str, Any]:
        graph = build_profile_agent_graph(self.nodes, entry=entry)
        result = graph.invoke(state)
        self.store.save_run(result)
        return result

    def start(
        self,
        *,
        company_id: str,
        mode: str,
        task_requirements: list[dict[str, Any]],
        max_questions_per_batch: int,
        include_optional: bool,
        max_rounds: int,
        decision_context_policy: str,
        capability_ai_enabled: bool = False,
        analysis_only: bool = False,
        include_decision_questions: bool = True,
        requested_capability_types: list[str] | None = None,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "run_id": _run_id(company_id, mode),
            "company_id": company_id,
            "mode": mode,
            "task_requirements": task_requirements,
            "max_questions_per_batch": max_questions_per_batch,
            "include_optional": include_optional,
            "selected_decision_candidate_ids": [],
            "pending_review_items": [],
            "warnings": [],
            "trace": [],
            "status": AGENT_STATUS_RUNNING,
            "workflow_state": AGENT_STATUS_RUNNING,
            "waiting_for": None,
            "termination_reason": None,
            "round_index": 0,
            "max_rounds": max_rounds,
            "rounds": [],
            "gap_snapshots": [],
            "unavailable_target_keys": [],
            "question_plan_fingerprints": [],
            "next_round_required": False,
            "parent_question_plan_id": None,
            "decision_context_policy": decision_context_policy,
            "decision_context_status": None,
            "response_source": None,
            "error": None,
            "capability_ai_enabled": bool(capability_ai_enabled),
            "analysis_only": bool(analysis_only),
            "include_decision_questions": bool(include_decision_questions),
            "requested_capability_types": list(requested_capability_types or []),
            "llm_semantic_candidates": None,
            "llm_provider_audit": None,
            "llm_node_executed": False,
            "llm_review_completed": False,
        }
        self.store.save_run(state)
        return self._invoke(state, entry="load_company_context")

    def resume_with_responses(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = self.store.read_run(run_id)
        if state.get("status") != "WAITING_USER_INPUT" or state.get("waiting_for") != "responses":
            raise ValueError("Agent run is not waiting for responses")
        state["user_responses"] = payload
        state["response_source"] = "USER"
        state["status"] = "RUNNING"
        state["waiting_for"] = None
        return self._invoke(state, entry="record_response")

    def resume_with_decision_selection(
        self,
        run_id: str,
        candidate_ids: list[str],
    ) -> dict[str, Any]:
        state = self.store.read_run(run_id)
        if state.get("status") != "WAITING_USER_INPUT" or state.get("waiting_for") != "decision_selection":
            raise ValueError("Agent run is not waiting for decision selection")
        state["selected_decision_candidate_ids"] = list(candidate_ids)
        state["status"] = "RUNNING"
        state["waiting_for"] = None
        return self._invoke(state, entry="apply_decision_candidates")

    def resume_after_review(self, run_id: str) -> dict[str, Any]:
        state = self.store.read_run(run_id)
        if state.get("status") not in {"WAITING_REVIEW", "COMPLETED_WITH_PENDING_REVIEW"}:
            raise ValueError("Agent run is not waiting for review")
        state["status"] = "RUNNING"
        state["workflow_state"] = "RUNNING"
        state["waiting_for"] = None
        state["pending_review_items"] = []
        if state.get("llm_node_executed"):
            state["llm_review_completed"] = True
        state["next_round_required"] = True
        state.setdefault("trace", []).append({
            "node": "resume_after_review",
            "occurred_at_utc": utc_now(),
            "details": {"reason": "REVIEW_TASKS_COMPLETED"},
        })
        return self._invoke(state, entry="load_company_context")
