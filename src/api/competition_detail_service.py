from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from src.shared.model_normalization import ensure_model
from src.shared.schemas import (
    BidDecisionResult,
    CompetitionAnalysisRequest,
    CompetitionAnalysisResult,
    CompetitionDetailResponse,
)


AnalysisCallback = Callable[[CompetitionAnalysisRequest, str], CompetitionAnalysisResult]


def public_data_provider(provider: str) -> str:
    """Expose the single real runtime chain and its temporary demo competition source."""
    if provider.strip().lower() != "real":
        raise ValueError("only the real data provider is supported")
    return "REAL_PROJECT_WITH_DEMO_COMPETITION"


def build_competition_detail(
    *,
    gateways: dict[str, Any],
    provider: str,
    company_id: str,
    project_id: str,
    company_profile_version: str | None = None,
    project_version: str | None = None,
    competition_data_version: str | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    ensure_analysis: bool = False,
    task_record: dict[str, Any] | None = None,
    analyze: AnalysisCallback | None = None,
    expected_model_version: str | None = None,
    now: datetime | None = None,
) -> CompetitionDetailResponse:
    """Read or create one version-exact company/project competition detail.

    This function intentionally contains no FastAPI or LangGraph imports.  The
    HTTP layer supplies an optional analysis callback, while version checks,
    stale handling and display DTO assembly remain deterministic and unit-testable.
    """
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    profile = gateways["company"].get_company_profile(
        company_id,
        requested_profile_version=None,
        as_of_time=current_time,
        current_task_constraints={},
    )
    project = gateways["project"].get_project(project_id)
    competition_snapshot = gateways["competition_data"].get_snapshot(
        project_id, project.project_version
    )

    stale_reason: str | None = None
    if company_profile_version and company_profile_version != profile.profile_version:
        stale_reason = "PROFILE_VERSION_MISMATCH"
    elif project_version and project_version != project.project_version:
        stale_reason = "PROJECT_VERSION_MISMATCH"
    elif (
        competition_data_version
        and competition_data_version != competition_snapshot.competition_data_version
    ):
        stale_reason = "COMPETITION_DATA_VERSION_MISMATCH"

    analysis = gateways["persistence"].get_competition(
        company_id,
        project_id,
        company_profile_version=profile.profile_version,
        project_version=project.project_version,
        competition_data_version=competition_snapshot.competition_data_version,
    )
    if analysis and expected_model_version and analysis.model_version != expected_model_version:
        stale_reason = stale_reason or "MODEL_VERSION_MISMATCH"
        analysis = None
    if analysis and analysis.valid_until <= current_time:
        stale_reason = stale_reason or "RESULT_EXPIRED"
        analysis = None

    recomputable_reason = stale_reason in {None, "MODEL_VERSION_MISMATCH", "RESULT_EXPIRED"}
    if recomputable_reason and analysis is None and ensure_analysis and analyze is not None:
        request_model = CompetitionAnalysisRequest(
            company_id=company_id,
            project_id=project_id,
            company_profile_version=profile.profile_version,
            project_version=project.project_version,
            as_of_time=current_time,
        )
        analysis = ensure_model(
            analyze(
                request_model,
                thread_id or f"competition:{company_id}:{project_id}",
            ),
            CompetitionAnalysisResult,
        )
        if expected_model_version and analysis.model_version != expected_model_version:
            raise ValueError("competition analysis returned an unexpected model version")
        stale_reason = None

    eligibility_status = None
    opportunity_level = None
    if task_record and isinstance(task_record.get("result"), (dict, str, bytes, bytearray)):
        decision_result = ensure_model(task_record["result"], BidDecisionResult)
        for item in decision_result.project_decisions:
            if item.project_id == project_id:
                eligibility_status = item.eligibility
                opportunity_level = (
                    item.win_opportunity.opportunity_level
                    if item.win_opportunity is not None
                    else None
                )
                break

    status_value = "STALE" if stale_reason else (
        analysis.status if analysis is not None else "PENDING"
    )
    return CompetitionDetailResponse(
        status=status_value,
        stale_reason=stale_reason,
        recompute_required=bool(stale_reason or analysis is None),
        task_id=task_id,
        thread_id=thread_id,
        data_provider=public_data_provider(provider),
        project={
            "project_id": project.project_id,
            "project_version": project.project_version,
            "project_name": project.project_name,
            "buyer_name": project.buyer_name,
            "region": project.region,
            "industry": project.industry,
            "budget": project.budget,
            "bid_deadline": project.bid_deadline,
            "bid_open_time": project.bid_open_time,
            "time_field_note": project.time_field_note,
            "project_status": project.project_status,
        },
        company={
            "company_id": profile.company_id,
            "company_name": profile.company_name
            or (profile.fact_profile.get("company_name") if isinstance(profile.fact_profile, dict) else None),
            "company_profile_version": profile.profile_version,
            "qualification_status": eligibility_status,
            "core_capabilities": (
                profile.capability_profile.industry_capability
                + profile.capability_profile.technical_capability
            )[:12],
            "win_opportunity_level": opportunity_level,
        },
        analysis=analysis,
        generated_at=current_time,
    )
