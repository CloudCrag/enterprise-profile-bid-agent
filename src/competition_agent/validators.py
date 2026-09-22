from __future__ import annotations

from datetime import timezone

from src.shared.evidence import validate_evidence_ids
from src.shared.schemas import CompetitionAnalysisResult, CompanyProfileSnapshot, ProjectSnapshot, CompetitionDataSnapshot
from src.shared.versions import require_equal
from src.competition_agent.strategy_policy import ILLEGAL_STRATEGY_TERMS


def validate_competition_result(
    result: CompetitionAnalysisResult,
    company: CompanyProfileSnapshot,
    project: ProjectSnapshot,
    snapshot: CompetitionDataSnapshot,
) -> None:
    require_equal("company_profile_version", company.profile_version, result.company_profile_version)
    require_equal("project_version", project.project_version, result.project_version)
    require_equal("competition_data_version", snapshot.competition_data_version, result.competition_data_version)
    if result.company_id != company.company_id or result.project_id != project.project_id:
        raise ValueError("competition result entity mismatch")
    allowed = set(company.evidence_ids + project.evidence_ids + snapshot.evidence_ids)
    validate_evidence_ids(result.evidence_ids, allowed)
    for item in result.confirmed_competitors + result.potential_competitors:
        validate_evidence_ids(item.evidence_ids, allowed)
    for item in result.confirmed_facts + result.company_advantages + result.company_weaknesses:
        validate_evidence_ids(item.evidence_ids, allowed)
    for item in result.inferences:
        validate_evidence_ids(item.based_on_evidence_ids, allowed)
    if any(term in strategy for strategy in result.strategies for term in ILLEGAL_STRATEGY_TERMS):
        raise ValueError("illegal competition strategy")
    if result.generated_at.tzinfo is None or result.generated_at.utcoffset() != timezone.utc.utcoffset(result.generated_at):
        raise ValueError("generated_at must be UTC")
    if result.valid_until.tzinfo is None or result.valid_until.utcoffset() != timezone.utc.utcoffset(result.valid_until):
        raise ValueError("valid_until must be UTC")
    if not result.generated_at <= result.valid_until:
        raise ValueError("competition result time window is invalid")
    if project.project_status != "OPEN":
        raise ValueError("closed project cannot have a normal competition result")
    if project.bid_deadline is not None:
        project_deadline = project.bid_deadline.astimezone(timezone.utc)
        if result.valid_until > project_deadline or project_deadline <= result.generated_at:
            raise ValueError("competition result time window violates confirmed project deadline")
