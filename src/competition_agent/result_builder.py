from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.shared.errors import AgentException
from src.shared.schemas import (
    CompetitionAnalysisRequest,
    CompetitionAnalysisResult,
    CompetitionDataSnapshot,
    CompetitionSemanticOutput,
    GroundedStatement,
    ProjectSnapshot,
)

COMPETITION_RESULT_VALIDITY_DAYS = 7
COMPETITION_PROMPT_VERSION = "competition-prompt-v2"


def as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def competition_time_window(
    request: CompetitionAnalysisRequest,
    project: ProjectSnapshot,
) -> tuple[datetime, datetime]:
    generated_at = as_utc(request.as_of_time, field_name="request.as_of_time")
    if project.project_status != "OPEN":
        raise AgentException(
            "agent_invalid_request",
            "Competition analysis is unavailable for a project whose authoritative status is not open",
            {
                "project_id": project.project_id,
                "project_status": project.project_status,
                "generated_at": generated_at.isoformat(),
            },
        )
    validity_limit = generated_at + timedelta(days=COMPETITION_RESULT_VALIDITY_DAYS)
    if project.bid_deadline is not None:
        deadline = as_utc(project.bid_deadline, field_name="project.bid_deadline")
        if deadline <= generated_at:
            raise AgentException(
                "agent_invalid_request",
                "Competition analysis is unavailable after the confirmed bid deadline",
                {"project_id": project.project_id, "bid_deadline": deadline.isoformat()},
            )
        validity_limit = min(validity_limit, deadline)
    return generated_at, validity_limit


def provider_model_version(provider) -> str:
    provider_name = str(getattr(provider, "provider_name", "unknown"))
    model = getattr(provider, "model", None)
    if model:
        return f"{provider_name}:{model}"
    return f"{provider_name}:unknown-model"


def _ground_semantic_statements(
    statements: Iterable[str],
    deterministic_sources: list[GroundedStatement],
    fallback_evidence_ids: list[str],
) -> list[GroundedStatement]:
    clean = [item.strip() for item in statements if isinstance(item, str) and item.strip()]
    if not clean:
        return deterministic_sources
    source_evidence = sorted(
        {
            evidence_id
            for source in deterministic_sources
            for evidence_id in source.evidence_ids
        }
    ) or fallback_evidence_ids
    return [GroundedStatement(statement=item, evidence_ids=source_evidence) for item in clean]


def build_competition_result(
    *,
    request: CompetitionAnalysisRequest,
    project: ProjectSnapshot,
    snapshot: CompetitionDataSnapshot,
    state: dict,
    semantic: CompetitionSemanticOutput,
    model_version: str,
    status: str = "COMPLETED",
) -> CompetitionAnalysisResult:
    generated_at, valid_until = competition_time_window(request, project)
    evidence_ids = sorted(
        set(
            state["company_profile"].evidence_ids
            + project.evidence_ids
            + snapshot.evidence_ids
        )
    )
    return CompetitionAnalysisResult(
        company_id=request.company_id,
        project_id=request.project_id,
        company_profile_version=request.company_profile_version,
        project_version=request.project_version,
        competition_data_version=snapshot.competition_data_version,
        confirmed_competitors=state["confirmed_competitors"],
        potential_competitors=state["potential_competitors"],
        confirmed_facts=state["confirmed_facts"],
        inferences=state["inferences"],
        unknowns=state["unknowns"],
        data_coverage=float(state["statistics"]["data_coverage"]),
        competitive_intensity=semantic.competitive_intensity,
        company_advantages=_ground_semantic_statements(
            semantic.company_advantages,
            state["company_advantages"],
            evidence_ids,
        ),
        company_weaknesses=_ground_semantic_statements(
            semantic.company_weaknesses,
            state["company_weaknesses"],
            evidence_ids,
        ),
        strategies=semantic.strategies,
        evidence_ids=evidence_ids,
        model_version=model_version,
        prompt_version=COMPETITION_PROMPT_VERSION,
        generated_at=generated_at,
        valid_until=valid_until,
        status=status,
        data_is_demo=snapshot.data_is_demo,
        data_warning=snapshot.data_warning,
    )
