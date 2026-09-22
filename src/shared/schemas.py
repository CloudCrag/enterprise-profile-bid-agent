from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, populate_by_name=True,
        alias_generator=to_camel, validate_assignment=True,
    )


class EligibilityStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class CompetitiveIntensity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class BidDecision(str, Enum):
    GO = "GO"
    CONDITIONAL_GO = "CONDITIONAL_GO"
    NO_GO = "NO_GO"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceReference(StrictModel):
    evidence_id: str = Field(min_length=1)
    source_type: str
    source_uri: str | None = None
    description: str
    captured_at: datetime


class DataQuality(StrictModel):
    completeness: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)


class CapabilityProfile(StrictModel):
    industry_capability: list[str]
    technical_capability: list[str]
    similar_performance_capability: list[str]
    regional_delivery_capability: list[str]
    amount_experience_capability: dict[str, Decimal | None]
    personnel_resource_capability: dict[str, int]
    buyer_relationship_capability: list[str]
    tender_performance_capability: dict[str, int | float]
    pending_items: list[dict[str, Any]] = Field(default_factory=list)


class DecisionProfile(StrictModel):
    strategic_industries: list[str]
    strategic_regions: list[str]
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    max_concurrent_bids: int = Field(default=2, ge=1)
    available_bid_team_slots: int = Field(default=1, ge=0)
    risk_preference: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    excluded_conditions: list[str] = Field(default_factory=list)




class BudgetPreferences(StrictModel):
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None

    @field_validator("min_amount", "max_amount", mode="before")
    @classmethod
    def parse_decimal(cls, value):
        if value is None or isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @model_validator(mode="after")
    def validate_range(self):
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError("budget min_amount cannot exceed max_amount")
        return self


class RiskPreferences(StrictModel):
    level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    avoid_conditions: list[str] = Field(default_factory=list)


class InterpretedUserGoal(StrictModel):
    target_industries: list[str] = Field(default_factory=list)
    target_regions: list[str] = Field(default_factory=list)
    budget_preferences: BudgetPreferences = Field(default_factory=BudgetPreferences)
    available_bid_team_slots: int = Field(default=1, ge=0)
    risk_preferences: RiskPreferences = Field(default_factory=RiskPreferences)
    explicit_exclusions: list[str] = Field(default_factory=list)
    priority_factors: list[str] = Field(default_factory=list)
    specified_preferences: list[str] = Field(default_factory=list)
    current_task_constraints: dict[str, Any] = Field(default_factory=dict)


class DecisionExplanation(StrictModel):
    comparison_summary: str
    decision_reasons: dict[str, list[str]]
    risk_explanations: dict[str, list[str]]
    recommended_actions: dict[str, list[str]]
    portfolio_explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class CompanyProfileSnapshot(StrictModel):
    company_id: str
    company_name: str | None = None
    profile_version: str
    fact_profile_version: str | None = None
    capability_profile_version: str | None = None
    decision_profile_version: str | None = None
    fact_profile: dict[str, Any]
    capability_profile: CapabilityProfile
    decision_profile: DecisionProfile
    evidence_ids: list[str]
    data_quality: DataQuality
    conflicts: list[str]
    as_of_time: datetime
    generated_at: datetime | None = None
    current_task_constraints: dict[str, Any] = Field(default_factory=dict)
    data_sources: list[str] = Field(default_factory=list)
    data_updated_at: datetime | None = None
    data_provider: str = "UNKNOWN"
    unknown_items: list[dict[str, Any]] = Field(default_factory=list)


class EnterpriseEvaluationSnapshot(StrictModel):
    company_id: str
    profile_version: str
    evaluation_version: str | None = None
    status: Literal["AVAILABLE", "NOT_AVAILABLE", "PARTIAL"]
    coverage: float = Field(ge=0, le=1)
    dimension_results: dict[str, Any] = Field(default_factory=dict)
    risk_indicators: list[str] = Field(default_factory=list)
    official_total_score: float | None = None
    official_grade: str | None = None
    publication_status: Literal["PUBLISHED", "DRAFT", "POLICY_NOT_CONFIRMED"]
    evidence_ids: list[str] = Field(default_factory=list)


class ProjectSnapshot(StrictModel):
    project_id: str
    project_version: str
    project_name: str
    buyer_id: str
    buyer_name: str
    region: str
    industry: str
    project_type: str
    budget: Decimal | None
    maximum_price: Decimal | None
    bid_deadline: datetime | None = None
    bid_open_time: datetime | None = None
    time_field_note: str = "投标截止时间尚未确认；bid_open_time仅按数据库字段原义展示为开标时间。"
    project_status: Literal["OPEN", "CLOSED", "TERMINATED", "AWARDED"]
    qualification_requirements: list[dict[str, Any]]
    personnel_requirements: list[dict[str, Any]]
    performance_requirements: list[dict[str, Any]]
    technical_scope: list[str]
    contract_risks: list[str]
    contract_risk_data_available: bool = False
    evidence_ids: list[str]
    as_of_time: datetime


class RankedProjectCandidate(StrictModel):
    company_id: str
    project_id: str
    company_profile_version: str
    project_version: str
    rank: int = Field(ge=1)
    rank_score: float
    recall_channels: list[str]
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str]




class QualificationInputOption(StrictModel):
    label: str
    value: str | bool | int | float


class QualificationInputField(StrictModel):
    key: str
    label: str
    input_type: Literal["text", "textarea", "date", "number", "select", "boolean"]
    required: bool = True
    placeholder: str = ""
    help_text: str = ""
    options: list[QualificationInputOption] = Field(default_factory=list)
    min_value: float | None = None


class EligibilityItemResult(StrictModel):
    requirement_id: str
    status: EligibilityStatus
    reason_code: str
    explanation: str
    missing_fields: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    critical: bool = False
    requirement_text: str | None = None
    requirement_category: str = "OTHER"
    supplement_key: str | None = None
    required_materials: list[str] = Field(default_factory=list)
    supplement_allowed: bool = False
    input_fields: list[QualificationInputField] = Field(default_factory=list)
    validation_rule: dict[str, Any] = Field(default_factory=dict)


class EligibilityResult(StrictModel):
    company_id: str
    project_id: str
    company_profile_version: str
    project_version: str
    overall_status: EligibilityStatus
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    item_results: list[EligibilityItemResult]
    missing_fields: list[str]
    verification_tasks: list[str]
    evidence_ids: list[str]
    rule_version: str

    @model_validator(mode="after")
    def enforce_three_state_counts(self):
        counts = {s: 0 for s in EligibilityStatus}
        for item in self.item_results:
            counts[item.status] += 1
        if (self.pass_count, self.fail_count, self.unknown_count) != (
            counts[EligibilityStatus.PASS], counts[EligibilityStatus.FAIL], counts[EligibilityStatus.UNKNOWN]
        ):
            raise ValueError("eligibility counts do not match item results")
        expected = EligibilityStatus.FAIL if self.fail_count else (
            EligibilityStatus.UNKNOWN if self.unknown_count else EligibilityStatus.PASS
        )
        if self.overall_status != expected:
            raise ValueError("overall_status violates deterministic three-state logic")
        return self


class CompetitorRef(StrictModel):
    company_id: str
    company_name: str
    basis: str
    evidence_ids: list[str]


class GroundedStatement(StrictModel):
    statement: str
    evidence_ids: list[str]


class InferenceStatement(StrictModel):
    statement: str
    based_on_evidence_ids: list[str]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]


class CompetitionAnalysisRequest(StrictModel):
    company_id: str
    project_id: str
    company_profile_version: str
    project_version: str
    as_of_time: datetime

    @field_validator("as_of_time", mode="before")
    @classmethod
    def parse_as_of_time(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class CompetitionSemanticOutput(StrictModel):
    """Narrow schema for LLM-only competition semantics.

    Identity, evidence, versions, statistics and timestamps are deliberately
    excluded and are assembled by deterministic program code.
    """

    competitive_intensity: CompetitiveIntensity
    company_advantages: list[str] = Field(default_factory=list)
    company_weaknesses: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list)


class CompetitionAnalysisResult(StrictModel):
    company_id: str
    project_id: str
    company_profile_version: str
    project_version: str
    competition_data_version: str
    confirmed_competitors: list[CompetitorRef]
    potential_competitors: list[CompetitorRef]
    confirmed_facts: list[GroundedStatement]
    inferences: list[InferenceStatement]
    unknowns: list[str]
    data_coverage: float = Field(ge=0, le=1)
    competitive_intensity: CompetitiveIntensity
    company_advantages: list[GroundedStatement]
    company_weaknesses: list[GroundedStatement]
    strategies: list[str]
    evidence_ids: list[str]
    model_version: str
    prompt_version: str
    generated_at: datetime
    valid_until: datetime
    status: Literal["COMPLETED", "NEEDS_HUMAN_REVIEW"] = "COMPLETED"
    data_is_demo: bool = False
    data_warning: str | None = None

    @field_validator("generated_at", "valid_until")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("competition timestamps must be timezone-aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("competition timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def coverage_guard(self):
        if self.generated_at > self.valid_until:
            raise ValueError("generated_at cannot be later than valid_until")
        if self.data_coverage < 0.5 and self.competitive_intensity == CompetitiveIntensity.LOW:
            raise ValueError("low coverage cannot produce LOW competitive intensity")
        if not self.confirmed_competitors and not self.potential_competitors and self.competitive_intensity == CompetitiveIntensity.LOW:
            raise ValueError("no competitor data cannot be interpreted as LOW")
        confirmed = {c.company_id for c in self.confirmed_competitors}
        potential = {c.company_id for c in self.potential_competitors}
        if confirmed & potential:
            raise ValueError("competitor cannot be both confirmed and potential")
        return self


class CompetitionDetailProjectSummary(StrictModel):
    project_id: str
    project_version: str
    project_name: str
    buyer_name: str
    region: str
    industry: str
    budget: Decimal | None = None
    bid_deadline: datetime | None = None
    bid_open_time: datetime | None = None
    time_field_note: str | None = None
    project_status: str


class CompetitionDetailCompanySummary(StrictModel):
    company_id: str
    company_name: str | None = None
    company_profile_version: str
    qualification_status: EligibilityStatus | None = None
    core_capabilities: list[str] = Field(default_factory=list)
    win_opportunity_level: str | None = None


class CompetitionDetailResponse(StrictModel):
    status: Literal[
        "PENDING", "RUNNING", "COMPLETED", "NEEDS_HUMAN_REVIEW", "FAILED", "STALE"
    ]
    stale_reason: str | None = None
    recompute_required: bool = False
    task_id: str | None = None
    thread_id: str | None = None
    data_provider: str
    project: CompetitionDetailProjectSummary
    company: CompetitionDetailCompanySummary
    analysis: CompetitionAnalysisResult | None = None
    generated_at: datetime


class WinOpportunityResult(StrictModel):
    company_id: str
    project_id: str
    company_profile_version: str
    project_version: str
    status: Literal["AVAILABLE", "INSUFFICIENT_DATA", "NOT_APPLICABLE"]
    opportunity_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN", "NOT_APPLICABLE"]
    probability: float | None = Field(default=None, ge=0, le=1)
    probability_range: tuple[float, float] | None = None
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    data_coverage: float = Field(ge=0, le=1)
    positive_factors: list[str]
    negative_factors: list[str]
    reason_codes: list[str]
    model_version: str

    @model_validator(mode="after")
    def probability_guard(self):
        if self.status != "AVAILABLE" and (self.probability is not None or self.probability_range is not None):
            raise ValueError("probability forbidden when status is not AVAILABLE")
        return self


class CompetitionAssessment(StrictModel):
    intensity: CompetitiveIntensity
    data_coverage: float = Field(ge=0, le=1)
    confirmed_competitor_count: int = Field(ge=0)
    potential_competitor_count: int = Field(ge=0)
    summary: str
    data_is_demo: bool = False
    data_warning: str | None = None


class ProjectComparisonResult(StrictModel):
    project_id: str
    project_version: str
    eligibility: EligibilityStatus
    original_rank: int
    original_rank_score: float
    recommendation_score_breakdown: dict[str, float]
    capability_match_score: float = Field(ge=0, le=100)
    competitive_intensity: CompetitiveIntensity
    competition_score: float = Field(ge=0, le=100)
    competition_data_coverage: float = Field(ge=0, le=1)
    win_opportunity_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN", "NOT_APPLICABLE"]
    win_opportunity_score: float = Field(ge=0, le=100)
    remaining_preparation_days: float = Field(ge=0)
    preparation_time_score: float = Field(ge=0, le=100)
    contract_risk_count: int = Field(ge=0)
    contract_risk_score: float = Field(ge=0, le=100)
    strategic_alignment_score: float = Field(ge=0, le=100)
    resource_fit_score: float = Field(ge=0, le=100)
    team_slots_required: int = Field(ge=1)
    composite_score: float = Field(ge=0, le=100)
    portfolio_rank: int | None = Field(default=None, ge=1)
    selected_for_portfolio: bool
    conflict_project_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    score_rule_version: str = "TEMP-BID-SCORE-V2"
    score_is_temporary: bool = True
    temporary_score_breakdown: dict[str, float] = Field(default_factory=dict)
    score_details: list[ScoreDimensionDetail] = Field(default_factory=list)
    preference_match_score: float = Field(default=0, ge=0, le=100)
    preference_match_level: Literal["HIGH", "MEDIUM", "LOW", "NOT_SPECIFIED"] = "NOT_SPECIFIED"
    preference_reasons: list[str] = Field(default_factory=list)
    preference_excluded: bool = False
    data_gaps: list[str] = Field(default_factory=list)


class ProjectDecisionResult(StrictModel):
    project_id: str
    project_name: str | None = None
    project_version: str
    decision: BidDecision
    priority: Priority
    eligibility: EligibilityStatus
    strengths: list[str]
    risks: list[str]
    unknowns: list[str]
    conditions: list[str]
    competition_assessment: CompetitionAssessment | None
    win_opportunity: WinOpportunityResult | None
    recommended_actions: list[str]
    evidence_ids: list[str]
    valid_until: datetime

    @model_validator(mode="after")
    def hard_fail_guard(self):
        if self.eligibility == EligibilityStatus.FAIL:
            if self.decision != BidDecision.NO_GO or self.priority != Priority.NOT_APPLICABLE:
                raise ValueError("FAIL project must be NO_GO/NOT_APPLICABLE")
            if self.competition_assessment is not None or self.win_opportunity is not None:
                raise ValueError("FAIL project must not enter normal competition/opportunity flow")
        if self.eligibility == EligibilityStatus.UNKNOWN and self.decision == BidDecision.GO:
            raise ValueError("UNKNOWN eligibility cannot become GO")
        return self


class BidDecisionRequest(StrictModel):
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:12]}")
    thread_id: str = Field(default_factory=lambda: f"thread-{uuid4().hex[:12]}")
    company_id: str
    user_goal: str
    requested_project_ids: list[str] = Field(default_factory=list)
    resource_constraints: dict[str, Any] = Field(default_factory=dict)
    as_of_time: datetime

    @field_validator("as_of_time", mode="before")
    @classmethod
    def parse_as_of_time(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class QualificationGap(StrictModel):
    project_id: str
    project_name: str
    requirement_id: str
    requirement_text: str
    requirement_category: str
    status: EligibilityStatus = EligibilityStatus.UNKNOWN
    reason: str
    required_materials: list[str] = Field(default_factory=list)
    supplement_key: str | None = None
    supplement_allowed: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    input_fields: list[QualificationInputField] = Field(default_factory=list)
    validation_rule: dict[str, Any] = Field(default_factory=dict)


class ScoreItemDetail(StrictModel):
    name: str
    score: float
    max_score: float
    status: Literal["PASS", "UNKNOWN", "FAIL", "MATCH", "PARTIAL", "NO_MATCH", "INSUFFICIENT_DATA"]
    rule: str
    reason: str
    data_sources: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class ScoreDimensionDetail(StrictModel):
    name: str
    score: float
    max_score: float
    items: list[ScoreItemDetail] = Field(default_factory=list)


class HumanConfirmationRequest(StrictModel):
    confirmation_id: str = Field(default_factory=lambda: f"confirm-{uuid4().hex[:10]}")
    task_id: str
    thread_id: str
    confirmation_type: Literal["CRITICAL_UNKNOWN", "FINAL_DECISION"]
    message: str
    affected_project_ids: list[str]
    required_fields: list[str]
    qualification_gaps: list[QualificationGap] = Field(default_factory=list)
    interpreted_user_goal: InterpretedUserGoal | None = None
    allowed_actions: list[str]


class HumanConfirmationResponse(StrictModel):
    confirmation_id: str
    task_id: str
    thread_id: str
    action: Literal["SUPPLY_AND_CONTINUE", "ACCEPT_CONDITIONS", "REJECT", "APPROVE_FINAL"]
    provided_fields: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None


class BidDecisionResult(StrictModel):
    task_id: str
    thread_id: str
    company_id: str
    company_profile_version: str
    user_goal: str
    interpreted_user_goal: InterpretedUserGoal
    project_comparisons: list[ProjectComparisonResult] = Field(default_factory=list)
    project_decisions: list[ProjectDecisionResult]
    decision_explanation: DecisionExplanation
    portfolio_recommendation: list[str]
    resource_conflicts: list[str]
    pending_confirmation: HumanConfirmationRequest | None
    status: Literal["WAITING_USER_CONFIRMATION", "DECIDED", "NEEDS_HUMAN_REVIEW", "INSUFFICIENT_DATA"]
    generated_at: datetime
    fact_profile_version: str | None = None
    capability_profile_version: str | None = None
    decision_profile_version: str | None = None
    evaluation_version: str | None = None
    project_versions: dict[str, str] = Field(default_factory=dict)
    competition_data_versions: dict[str, str] = Field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    valid_until: datetime | None = None
    data_provider: str = "UNKNOWN"
    score_rule_version: str = "TEMP-BID-SCORE-V2"
    score_is_temporary: bool = True


class AgentError(StrictModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)


class CompetitionDataSnapshot(StrictModel):
    project_id: str
    project_version: str
    competition_data_version: str
    direct_participants: list[CompetitorRef]
    historical_bidders: list[CompetitorRef]
    buyer_suppliers: list[CompetitorRef]
    competitor_profiles: dict[str, dict[str, Any]]
    source_coverage: dict[str, bool]
    unknowns: list[str]
    evidence_ids: list[str]
    as_of_time: datetime
    data_is_demo: bool = False
    data_warning: str | None = None


class MonitoringPlan(StrictModel):
    task_id: str
    project_ids: list[str]
    triggers: list[str]
    created_at: datetime
