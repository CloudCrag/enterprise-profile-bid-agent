from __future__ import annotations
from typing import TypedDict
from src.shared.schemas import *


class BidDecisionState(TypedDict, total=False):
    request: BidDecisionRequest
    company_profile: CompanyProfileSnapshot
    interpreted_user_goal: InterpretedUserGoal
    evaluation: EnterpriseEvaluationSnapshot
    candidates: list[RankedProjectCandidate]
    projects: list[ProjectSnapshot]
    eligibility_results: list[EligibilityResult]
    failed_project_ids: list[str]
    unknown_project_ids: list[str]
    critical_unknown_project_ids: list[str]
    unknown_resolution_confirmation: HumanConfirmationResponse
    unknown_resolution_round: int
    unknown_resolution_exhausted: bool
    unknown_resolution_status: str
    final_decision_confirmation: HumanConfirmationResponse
    final_confirmation_rejected: bool
    competition_results: list[CompetitionAnalysisResult]
    win_results: list[WinOpportunityResult]
    project_comparisons: list[ProjectComparisonResult]
    portfolio_project_ids: list[str]
    resource_conflicts: list[str]
    project_decisions: list[ProjectDecisionResult]
    decision_explanation: DecisionExplanation
    result: BidDecisionResult
    monitoring_plan: MonitoringPlan
    current_node: str
