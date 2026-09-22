from __future__ import annotations
from typing import Any, TypedDict


class ProfileAgentState(TypedDict, total=False):
    run_id: str
    company_id: str
    mode: str
    fact_profile: dict[str, Any]
    tag_profile: dict[str, Any]
    capability_profile: dict[str, Any]
    decision_profile: dict[str, Any] | None
    task_requirements: list[dict[str, Any]]
    gap_analysis_request: dict[str, Any] | None
    gap_inventory: dict[str, Any] | None
    question_plan_request: dict[str, Any] | None
    question_plan: dict[str, Any] | None
    parent_question_plan_id: str | None
    user_responses: dict[str, Any] | None
    response_submission: dict[str, Any] | None
    response_receipt: dict[str, Any] | None
    processing_worklist: dict[str, Any] | None
    selected_decision_candidate_ids: list[str]
    decision_application_request: dict[str, Any] | None
    decision_application_result: dict[str, Any] | None
    updated_fact_profile: dict[str, Any] | None
    updated_capability_profile: dict[str, Any] | None
    updated_decision_profile: dict[str, Any] | None
    pending_review_items: list[dict[str, Any]]
    remaining_gap_inventory: dict[str, Any] | None
    latest_gap_inventory: dict[str, Any] | None
    current_node: str
    status: str
    workflow_state: str | None
    waiting_for: str | None
    termination_reason: str | None
    warnings: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    max_questions_per_batch: int
    include_optional: bool
    response_source: str | None
    error: dict[str, Any] | None
    round_index: int
    max_rounds: int
    rounds: list[dict[str, Any]]
    gap_snapshots: list[dict[str, Any]]
    unavailable_target_keys: list[str]
    question_plan_fingerprints: list[str]
    next_round_required: bool
    decision_context_policy: str
    decision_context_status: dict[str, Any] | None
    decision_reconfirmation_request: dict[str, Any] | None

    capability_ai_enabled: bool
    analysis_only: bool
    include_decision_questions: bool
    requested_capability_types: list[str]
    llm_semantic_candidates: dict[str, Any] | None
    llm_provider_audit: dict[str, Any] | None
    llm_node_executed: bool
    llm_review_completed: bool
