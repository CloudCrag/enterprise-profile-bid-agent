from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol
from src.shared.schemas import (
    BidDecisionResult, CompanyProfileSnapshot, CompetitionAnalysisResult,
    CompetitionDataSnapshot, EligibilityResult, EnterpriseEvaluationSnapshot,
    MonitoringPlan, ProjectSnapshot, RankedProjectCandidate, WinOpportunityResult,
)

class CompanyProfileGateway(Protocol):
    def get_company_profile(
        self,
        company_id: str,
        *,
        requested_profile_version: str | None = None,
        as_of_time: datetime | None = None,
        current_task_constraints: dict[str, Any] | None = None,
    ) -> CompanyProfileSnapshot: ...

class ProfileUpdateGateway(Protocol):
    def update_profile(self, company_id: str, current_profile_version: str, provided_fields: dict) -> CompanyProfileSnapshot: ...

class EnterpriseEvaluationGateway(Protocol):
    def get_enterprise_evaluation(self, company_id: str, profile_version: str) -> EnterpriseEvaluationSnapshot: ...

class ProjectGateway(Protocol):
    def get_project(self, project_id: str) -> ProjectSnapshot: ...
    def list_projects(self, project_ids: list[str]) -> list[ProjectSnapshot]: ...

class RecommendationGateway(Protocol):
    def get_ranked_candidates(self, company_id: str, project_ids: list[str]) -> list[RankedProjectCandidate]: ...

class EligibilityGateway(Protocol):
    def evaluate(self, company_id: str, project_id: str, profile_version: str, project_version: str) -> EligibilityResult: ...

class CompetitionDataGateway(Protocol):
    def get_snapshot(self, project_id: str, project_version: str) -> CompetitionDataSnapshot: ...

class WinOpportunityGateway(Protocol):
    def estimate(self, company_id: str, project_id: str, profile_version: str, project_version: str, competition_coverage: float, eligibility: str) -> WinOpportunityResult: ...

class DecisionPersistenceGateway(Protocol):
    def save_competition(self, result: CompetitionAnalysisResult) -> None: ...
    def get_competition(
        self,
        company_id: str,
        project_id: str,
        *,
        company_profile_version: str | None = None,
        project_version: str | None = None,
        competition_data_version: str | None = None,
    ) -> CompetitionAnalysisResult | None: ...
    def save_decision(self, result: BidDecisionResult) -> None: ...
    def get_decision(self, task_id: str) -> BidDecisionResult | None: ...
    def save_monitoring_plan(self, plan: MonitoringPlan) -> None: ...
    def mark_stale(self, *, entity_type: str, entity_id: str, reason: str) -> None: ...

class AuditGateway(Protocol):
    def record(self, event_type: str, payload: dict) -> None: ...
