from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ActorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_type: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    display_name: str | None = None


class MaterialReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: str = Field(min_length=1)
    material_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_filename: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    description: str | None = None


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    mode: Literal["interactive"] = "interactive"
    task_requirements: list[dict[str, Any]] = Field(default_factory=list)
    max_questions_per_batch: int = Field(default=3, gt=0)
    include_optional: bool = False
    max_rounds: int = Field(default=5, gt=0, le=20)
    decision_context_policy: Literal["STRICT_BLOCK", "PAUSE_DECISION_LAYER"] = "STRICT_BLOCK"
    capability_ai_enabled: bool = False
    analysis_only: bool = False
    include_decision_questions: bool = True
    requested_capability_types: list[str] = Field(default_factory=list)


class ResponseResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission: dict[str, Any]


class DecisionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_candidate_ids: list[str] = Field(min_length=1)
    requested_at_utc: str


class ReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    reviewer_actor: dict[str, Any]
    reviewed_at_utc: str
    reason: str | None = None
    payload: dict[str, Any] | None = None


class FactCandidateSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_system: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_url: str | None
    collected_at: str
    fact_type: str = Field(min_length=1)
    payload: dict[str, Any]
    material_refs: list[MaterialReferenceModel] = Field(default_factory=list)
    submitted_by: ActorModel
    idempotency_key: str = Field(min_length=1)
    provider_id: str | None = None
    api_id: str | None = None
    api_call_id: str | None = None
    raw_response_ref: str | None = None
    normalizer_id: str | None = None
    trust_policy: Literal["REVIEW_REQUIRED", "AUTO_VERIFIED", "SAMPLE_REVIEW", "REJECTED_SOURCE"] = "REVIEW_REQUIRED"


class StructuredRequirementModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(min_length=1)
    target_layer: Literal["fact", "capability", "decision"]
    target_code: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    required_value: Any
    description: str = Field(min_length=1)
    importance: Literal["blocking", "important", "optional"]
    evidence_source: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)
    parsing_status: str | None = None
    manual_review_status: str


class TaskRequirementsSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    requirement_set_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    generated_at: str
    requirements: list[StructuredRequirementModel] = Field(min_length=1)


class ReviewSupplementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplement_id: str = Field(min_length=1)
    review_task_id: str = Field(min_length=1)
    submitted_by: ActorModel
    submitted_at_utc: str
    answer: Any
    material_refs: list[MaterialReferenceModel] = Field(default_factory=list)
    note: str | None = None


class DecisionContextReconfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1)
    strategy: Literal["STRICT_BLOCK", "PAUSE_DECISION_LAYER"]
    requested_by: ActorModel
    requested_at_utc: str
    field_codes: list[str] = Field(default_factory=list)


class DecisionPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferences: dict[str, Any] = Field(default_factory=dict)
    cleared_fields: list[str] = Field(default_factory=list)
    confirmation_actor: ActorModel
    confirmed_at_utc: str


class ProfilePerformanceInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str = Field(min_length=1)
    buyer_name: str | None = None
    industry: str | None = None
    region: str = Field(min_length=1)
    contract_amount: float | None = Field(default=None, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    performance_scope: str = Field(min_length=1)
    source_description: str = Field(min_length=1)
    submitted_at_utc: str


class ProfilePerformanceSupplementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buyer_name: str | None = None
    contract_amount: float | None = Field(default=None, ge=0)
    source_description: str = Field(min_length=1)
    submitted_at_utc: str


class ProfilePersonnelInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_count: int | None = Field(default=None, ge=0)
    social_insurance_count: int | None = Field(default=None, ge=0)
    count_scope: str | None = None
    source_description: str = Field(min_length=1)
    submitted_at_utc: str


class ProfileGeneralInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_layer: Literal["fact", "capability"]
    target_code: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_description: str = Field(min_length=1)
    submitted_at_utc: str


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str = Field(min_length=1)
    workflow_mode: Literal["legacy", "automatic", "interactive"] = "legacy"
    provider_mode: Literal["LOCAL_PROFILE"] = "LOCAL_PROFILE"
    max_api_calls: int = Field(default=10, ge=0, le=100)
    max_rounds: int = Field(default=3, ge=1, le=10)


class EvaluationAnswerSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[dict[str, Any]] = Field(min_length=1)


class BidProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    current_profile_version: str = Field(alias="currentProfileVersion", min_length=1)
    provided_fields: dict[str, Any] = Field(alias="providedFields", min_length=1)


class BidProfileSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    requested_profile_version: str | None = Field(default=None, alias="requestedProfileVersion")
    as_of_time: datetime | None = Field(default=None, alias="asOfTime")
    current_task_constraints: dict[str, Any] = Field(
        default_factory=dict, alias="currentTaskConstraints"
    )
