from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.types import interrupt

from src.bid_decision_agent.comparison import SCORE_RULE_VERSION, compare_project_portfolio, validate_portfolio_team_slots
from src.bid_decision_agent.decision_llm import (
    validate_decision_explanation,
    validate_interpreted_goal,
)
from src.bid_decision_agent.evaluation import load_required_evaluation_snapshot
from src.bid_decision_agent.prompts import (
    DECISION_EXPLANATION_SYSTEM_PROMPT,
    INTERPRET_USER_GOAL_SYSTEM_PROMPT,
)
from src.bid_decision_agent.validators import validate_decisions
from src.shared.model_normalization import ensure_model, ensure_model_list
from src.shared.safe_diagnostics import safe_exception_feedback
from src.shared.schemas import *


def _closed_project_eligibility(base: EligibilityResult, project: ProjectSnapshot, as_of_time: datetime) -> EligibilityResult:
    """Only an authoritative project status can close a project in the current data model."""
    if project.project_status == "OPEN":
        return base
    reason = "PROJECT_NOT_OPEN"
    item = EligibilityItemResult(
        requirement_id="system-project-validity",
        status=EligibilityStatus.FAIL,
        reason_code=reason,
        explanation="项目状态已关闭、终止或授标；bid_open_time不作为投标截止时间使用。",
        missing_fields=[],
        evidence_ids=project.evidence_ids,
        critical=True,
    )
    items = [*base.item_results, item]
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "overall_status": EligibilityStatus.FAIL,
            "pass_count": sum(x.status == EligibilityStatus.PASS for x in items),
            "fail_count": sum(x.status == EligibilityStatus.FAIL for x in items),
            "unknown_count": sum(x.status == EligibilityStatus.UNKNOWN for x in items),
            "item_results": items,
            "evidence_ids": sorted(set(base.evidence_ids + project.evidence_ids)),
            "verification_tasks": sorted(set(base.verification_tasks + ["停止投入投标准备资源"])),
        }
    )
    return EligibilityResult.model_validate(payload)


def _project_valid_until(project: ProjectSnapshot, fallback: datetime) -> datetime:
    """Use a real deadline only when one exists; otherwise use a short analysis validity window."""
    return min(project.bid_deadline, fallback) if project.bid_deadline is not None else fallback


def build_nodes(gateways, llm_provider, competition_graph):
    def understand_task(state):
        request = state["request"]
        if not request.user_goal.strip():
            raise ValueError("user_goal must not be empty")
        return {"current_node": "understand_task"}

    def load_company_profile(state):
        request = state["request"]
        profile = gateways["company"].get_company_profile(
            request.company_id,
            as_of_time=request.as_of_time,
            current_task_constraints=request.resource_constraints,
        )
        if profile.company_id != request.company_id:
            raise ValueError("company gateway returned mismatched company_id")
        return {"company_profile": profile, "current_node": "load_company_profile"}

    def interpret_user_goal(state):
        request = state["request"]
        company = state["company_profile"]
        payload = {
            "user_goal": request.user_goal,
            "resource_constraints": request.resource_constraints,
            "company_decision_profile": company.decision_profile.model_dump(mode="json"),
        }
        failures: list[dict[str, Any]] = []
        interpreted = None
        last_error: Exception | None = None
        for _attempt in range(2):
            retry_payload = dict(payload)
            if failures:
                retry_payload["validation_feedback"] = failures[-1]
            try:
                raw = llm_provider.generate_structured(
                    system_prompt=INTERPRET_USER_GOAL_SYSTEM_PROMPT,
                    user_payload=retry_payload,
                    output_schema=InterpretedUserGoal,
                )
                interpreted = validate_interpreted_goal(raw, request, company)
                break
            except Exception as exc:
                last_error = exc
                failures.append(safe_exception_feedback(exc))
        if interpreted is None:
            gateways["audit"].record(
                "bid_goal_interpretation_failed",
                {"task_id": request.task_id, "errors": failures},
            )
            if last_error is not None:
                raise last_error
            raise RuntimeError("bid goal interpretation failed without diagnostic")
        return {"interpreted_user_goal": interpreted, "current_node": "interpret_user_goal"}

    def load_required_evaluation(state):
        evaluation = load_required_evaluation_snapshot(state["company_profile"], gateways)
        return {"evaluation": evaluation, "current_node": "load_required_evaluation"}

    def get_ranked_candidates(state):
        request = state["request"]
        company = state["company_profile"]
        candidates = gateways["recommendation"].get_ranked_candidates(company.company_id, request.requested_project_ids)
        for candidate in candidates:
            if candidate.company_id != company.company_id or candidate.company_profile_version != company.profile_version:
                raise ValueError("candidate company/profile version mismatch")
        return {"candidates": candidates, "current_node": "get_ranked_candidates"}

    def load_project_snapshots(state):
        candidates = state["candidates"]
        project_ids = [candidate.project_id for candidate in candidates]
        projects = gateways["project"].list_projects(project_ids)
        project_map = {project.project_id: project for project in projects}
        if set(project_map) != set(project_ids):
            raise ValueError("project gateway did not return the complete candidate set")
        for candidate in candidates:
            if project_map[candidate.project_id].project_version != candidate.project_version:
                raise ValueError("candidate/project version mismatch")
        return {"projects": projects, "current_node": "load_project_snapshots"}

    def _evaluate_all_projects(company: CompanyProfileSnapshot, projects: list[ProjectSnapshot], request: BidDecisionRequest):
        results = []
        for project in projects:
            result = gateways["eligibility"].evaluate(
                company.company_id,
                project.project_id,
                company.profile_version,
                project.project_version,
            )
            if result.company_id != company.company_id or result.company_profile_version != company.profile_version:
                raise ValueError("eligibility gateway returned mismatched company/profile version")
            results.append(_closed_project_eligibility(result, project, request.as_of_time))
        return results

    def verify_eligibility(state):
        results = _evaluate_all_projects(state["company_profile"], state["projects"], state["request"])
        return {"eligibility_results": results, "current_node": "verify_eligibility"}

    def _classify_eligibility(results: list[EligibilityResult]) -> dict[str, list[str]]:
        return {
            "failed_project_ids": [x.project_id for x in results if x.overall_status == EligibilityStatus.FAIL],
            "unknown_project_ids": [x.project_id for x in results if x.overall_status == EligibilityStatus.UNKNOWN],
            "critical_unknown_project_ids": [
                x.project_id
                for x in results
                if any(item.status == EligibilityStatus.UNKNOWN and item.critical for item in x.item_results)
            ],
        }

    def route_eligibility_results(state):
        classified = _classify_eligibility(state["eligibility_results"])
        return {
            **classified,
            "unknown_resolution_round": int(state.get("unknown_resolution_round", 0)),
            "unknown_resolution_exhausted": False,
            "unknown_resolution_status": "ACTIVE" if classified["critical_unknown_project_ids"] else "RESOLVED",
            "current_node": "route_eligibility_results",
        }

    def resolve_critical_unknowns(state):
        critical = state.get("critical_unknown_project_ids", [])
        request = state["request"]
        current_round = int(state.get("unknown_resolution_round", 0))
        exhausted = bool(critical) and current_round >= 2
        project_map = {project.project_id: project for project in state["projects"]}
        qualification_gaps = [
            QualificationGap(
                project_id=result.project_id,
                project_name=project_map[result.project_id].project_name,
                requirement_id=item.requirement_id,
                requirement_text=item.requirement_text or item.explanation,
                requirement_category=item.requirement_category,
                status=item.status,
                reason=item.explanation,
                required_materials=item.required_materials,
                supplement_key=item.supplement_key,
                supplement_allowed=item.supplement_allowed,
                evidence_ids=item.evidence_ids,
                input_fields=item.input_fields,
                validation_rule=item.validation_rule,
            )
            for result in state["eligibility_results"]
            if result.project_id in critical
            for item in result.item_results
            if item.status == EligibilityStatus.UNKNOWN and item.critical
        ]
        required_fields = sorted(
            {
                gap.supplement_key
                for gap in qualification_gaps
                if gap.supplement_allowed and gap.supplement_key
            }
        )
        allowed_actions = ["ACCEPT_CONDITIONS", "REJECT"]
        if required_fields and not exhausted:
            allowed_actions.insert(0, "SUPPLY_AND_CONTINUE")
        if exhausted:
            message = "经过两轮补充后仍有关键资格无法确认。可以保持UNKNOWN并附条件继续，或结束这些项目的分析。"
        elif required_fields:
            message = "请按项目、按原子资格要求填写结构化核验字段。系统只重算对应资格项；必填字段不完整仍为UNKNOWN，明确冲突则为FAIL。"
        else:
            message = "当前数据库没有形成可操作的结构化资格要求，系统无法明确要求补充什么。请保持UNKNOWN继续分析，或结束这些项目的分析。"
        payload = HumanConfirmationRequest(
            confirmation_id=f"confirm-{request.task_id}-critical-unknown-r{current_round + 1}",
            task_id=request.task_id,
            thread_id=request.thread_id,
            confirmation_type="CRITICAL_UNKNOWN",
            message=message,
            affected_project_ids=critical,
            required_fields=required_fields,
            qualification_gaps=qualification_gaps,
            interpreted_user_goal=state.get("interpreted_user_goal"),
            allowed_actions=allowed_actions,
        )
        raw = interrupt(payload.model_dump(mode="json"))
        response = HumanConfirmationResponse.model_validate(raw)
        if response.task_id != request.task_id or response.thread_id != request.thread_id:
            raise ValueError("confirmation task/thread mismatch")
        if response.confirmation_id != payload.confirmation_id:
            raise ValueError("confirmation_id mismatch")
        if response.action not in payload.allowed_actions:
            raise ValueError("confirmation action is not allowed for this interrupt")

        updates: dict[str, Any] = {
            "unknown_resolution_confirmation": response,
            "unknown_resolution_exhausted": exhausted,
            "unknown_resolution_status": "INSUFFICIENT_DATA" if exhausted else "RESOLVED",
            "current_node": "resolve_critical_unknowns",
        }
        if response.action == "SUPPLY_AND_CONTINUE":
            next_round = current_round + 1
            updated_profile = gateways["profile_update"].update_profile(
                state["company_profile"].company_id,
                state["company_profile"].profile_version,
                response.provided_fields,
            )
            if updated_profile.company_id != state["company_profile"].company_id:
                raise ValueError("profile update gateway returned mismatched company_id")
            if updated_profile.profile_version == state["company_profile"].profile_version:
                raise ValueError("profile update gateway must return a new profile version")
            refreshed = _evaluate_all_projects(updated_profile, state["projects"], request)
            classified = _classify_eligibility(refreshed)
            still_critical = bool(classified["critical_unknown_project_ids"])
            limit_reached = still_critical and next_round >= 2
            refreshed_evaluation = load_required_evaluation_snapshot(updated_profile, gateways)
            updates.update(
                {
                    "company_profile": updated_profile,
                    "evaluation": refreshed_evaluation,
                    "eligibility_results": refreshed,
                    **classified,
                    "unknown_resolution_round": next_round,
                    "unknown_resolution_exhausted": limit_reached,
                    "unknown_resolution_status": (
                        "INSUFFICIENT_DATA" if limit_reached else ("ACTIVE" if still_critical else "RESOLVED")
                    ),
                }
            )
            gateways["audit"].record(
                "profile_updated_and_eligibility_rechecked",
                {
                    "company_id": updated_profile.company_id,
                    "new_profile_version": updated_profile.profile_version,
                    "unknown_resolution_round": next_round,
                    "critical_unknown_project_ids": classified["critical_unknown_project_ids"],
                    "unknown_resolution_status": updates["unknown_resolution_status"],
                    "affected_project_ids": [x.project_id for x in refreshed],
                },
            )
        return updates

    def _rejected_critical_project(state, project_id: str) -> bool:
        confirmation = state.get("unknown_resolution_confirmation")
        return bool(
            confirmation
            and confirmation.action == "REJECT"
            and project_id in state.get("critical_unknown_project_ids", [])
        )

    def analyze_competition(state):
        company = state["company_profile"]
        eligibility_map = {x.project_id: x for x in state["eligibility_results"]}
        results = []
        for project in state["projects"]:
            if eligibility_map[project.project_id].overall_status == EligibilityStatus.FAIL:
                continue
            if _rejected_critical_project(state, project.project_id):
                continue
            request = CompetitionAnalysisRequest(
                company_id=company.company_id,
                project_id=project.project_id,
                company_profile_version=company.profile_version,
                project_version=project.project_version,
                as_of_time=state["request"].as_of_time,
            )
            sub_result = competition_graph.invoke(
                {"request": request},
                config={"configurable": {"thread_id": f"{state['request'].thread_id}:competition:{project.project_id}"}},
            )
            results.append(sub_result["result"])
        return {"competition_results": results, "current_node": "analyze_competition"}

    def estimate_win_opportunity(state):
        company = state["company_profile"]
        eligibility_map = {x.project_id: x for x in state["eligibility_results"]}
        competition_map = {x.project_id: x for x in state["competition_results"]}
        results = []
        for project in state["projects"]:
            eligibility = eligibility_map[project.project_id]
            if eligibility.overall_status == EligibilityStatus.FAIL:
                continue
            if _rejected_critical_project(state, project.project_id):
                continue
            coverage = competition_map[project.project_id].data_coverage
            results.append(
                gateways["win_opportunity"].estimate(
                    company.company_id,
                    project.project_id,
                    company.profile_version,
                    project.project_version,
                    coverage,
                    eligibility.overall_status.value,
                )
            )
        return {"win_results": results, "current_node": "estimate_win_opportunity"}

    def compare_projects(state):
        return compare_project_portfolio(state)

    def _priority_for(comparison: ProjectComparisonResult) -> Priority:
        if comparison.eligibility == EligibilityStatus.FAIL:
            return Priority.NOT_APPLICABLE
        if comparison.preference_excluded:
            return Priority.LOW
        if comparison.eligibility == EligibilityStatus.UNKNOWN:
            return Priority.MEDIUM if comparison.composite_score >= 65 and comparison.preference_match_level != "LOW" else Priority.LOW
        base = Priority.HIGH if comparison.composite_score >= 80 else (Priority.MEDIUM if comparison.composite_score >= 65 else Priority.LOW)
        if comparison.preference_match_level == "LOW":
            return Priority.MEDIUM if base == Priority.HIGH else Priority.LOW
        if comparison.preference_match_level == "HIGH" and base == Priority.LOW and comparison.composite_score >= 55:
            return Priority.MEDIUM
        return base

    def build_project_decisions(state):
        now = datetime.now(timezone.utc)
        eligibility_results = ensure_model_list(state["eligibility_results"], EligibilityResult)
        competition_results = ensure_model_list(state["competition_results"], CompetitionAnalysisResult)
        win_results = ensure_model_list(state["win_results"], WinOpportunityResult)
        project_comparisons = ensure_model_list(state["project_comparisons"], ProjectComparisonResult)
        eligibility_map = {x.project_id: x for x in eligibility_results}
        competition_map = {x.project_id: x for x in competition_results}
        win_map = {x.project_id: x for x in win_results}
        comparison_map = {x.project_id: x for x in project_comparisons}
        unknown_action = getattr(state.get("unknown_resolution_confirmation"), "action", None)
        critical_ids = set(state.get("critical_unknown_project_ids", []))
        decisions = []

        for project in state["projects"]:
            eligibility = eligibility_map[project.project_id]
            comparison = comparison_map[project.project_id]
            evidence = sorted(set(project.evidence_ids + eligibility.evidence_ids + state["company_profile"].evidence_ids))
            if eligibility.overall_status == EligibilityStatus.FAIL:
                decisions.append(
                    ProjectDecisionResult(
                        project_id=project.project_id,
                        project_name=project.project_name,
                        project_version=project.project_version,
                        decision=BidDecision.NO_GO,
                        priority=Priority.NOT_APPLICABLE,
                        eligibility=EligibilityStatus.FAIL,
                        strengths=[],
                        risks=["存在明确强制资格不满足或项目已失效"],
                        unknowns=[],
                        conditions=[],
                        competition_assessment=None,
                        win_opportunity=None,
                        recommended_actions=eligibility.verification_tasks or ["不投入正常投标准备资源"],
                        evidence_ids=evidence,
                        valid_until=_project_valid_until(project, now + timedelta(days=7)),
                    )
                )
                continue

            if _rejected_critical_project(state, project.project_id):
                decisions.append(
                    ProjectDecisionResult(
                        project_id=project.project_id,
                        project_name=project.project_name,
                        project_version=project.project_version,
                        decision=BidDecision.NO_GO,
                        priority=Priority.LOW,
                        eligibility=EligibilityStatus.UNKNOWN,
                        strengths=[],
                        risks=["用户拒绝在关键资格未知条件下继续投入"],
                        unknowns=eligibility.missing_fields,
                        conditions=eligibility.verification_tasks,
                        competition_assessment=None,
                        win_opportunity=None,
                        recommended_actions=["补齐材料后重新发起资格核验"],
                        evidence_ids=evidence,
                        valid_until=_project_valid_until(project, now + timedelta(days=3)),
                    )
                )
                continue

            competition = competition_map[project.project_id]
            win = win_map[project.project_id]
            assessment = CompetitionAssessment(
                intensity=competition.competitive_intensity,
                data_coverage=competition.data_coverage,
                confirmed_competitor_count=len(competition.confirmed_competitors),
                potential_competitor_count=len(competition.potential_competitors),
                summary=(
                    ("演示数据，不代表真实企业参与情况。" if competition.data_is_demo else "")
                    + ("；".join([x.statement for x in competition.confirmed_facts + competition.inferences]) or "竞争信息不足")
                ),
                data_is_demo=competition.data_is_demo,
                data_warning=competition.data_warning,
            )
            if comparison.preference_excluded:
                decision = BidDecision.NO_GO
            elif eligibility.overall_status == EligibilityStatus.UNKNOWN:
                accepted = unknown_action == "ACCEPT_CONDITIONS" or project.project_id not in critical_ids
                decision = BidDecision.CONDITIONAL_GO if accepted else BidDecision.INSUFFICIENT_DATA
            else:
                decision = BidDecision.GO

            conditions = list(eligibility.verification_tasks) if eligibility.overall_status == EligibilityStatus.UNKNOWN else []
            risks = project.contract_risks + [x.statement for x in competition.company_weaknesses]
            actions = list(competition.strategies)
            if comparison.preference_excluded:
                risks.append("命中本次目标中的明确排除条件")
                actions.insert(0, "如需重新考虑，请修改本次目标中的排除条件后重新分析")
            elif comparison.preference_match_level == "LOW":
                risks.append("项目与本次填写的行业、地区或预算偏好匹配较低")
            elif comparison.preference_match_level == "HIGH":
                actions.insert(0, "项目与本次偏好高度匹配，可优先核验关键资格和资源")
            if not comparison.selected_for_portfolio and decision in {BidDecision.GO, BidDecision.CONDITIONAL_GO}:
                conditions.append("当前可用投标团队槽位未将本项目纳入最终组合")
                risks.append("与更高综合得分项目存在团队或资源投入冲突")
                actions.append("释放团队槽位或调整项目组合后再推进")

            decisions.append(
                ProjectDecisionResult(
                    project_id=project.project_id,
                    project_name=project.project_name,
                    project_version=project.project_version,
                    decision=decision,
                    priority=_priority_for(comparison),
                    eligibility=eligibility.overall_status,
                    strengths=[x.statement for x in competition.company_advantages] + comparison.reasons[:3] + comparison.preference_reasons,
                    risks=risks,
                    unknowns=sorted(set(eligibility.missing_fields + competition.unknowns + comparison.data_gaps)),
                    conditions=conditions,
                    competition_assessment=assessment,
                    win_opportunity=win,
                    recommended_actions=actions,
                    evidence_ids=evidence,
                    valid_until=_project_valid_until(project, competition.valid_until),
                )
            )
        return {"project_decisions": decisions, "current_node": "build_project_decisions"}

    def validate_decision(state):
        decisions = ensure_model_list(state["project_decisions"], ProjectDecisionResult)
        company = ensure_model(state["company_profile"], CompanyProfileSnapshot)
        projects = ensure_model_list(state["projects"], ProjectSnapshot)
        eligibility = ensure_model_list(state["eligibility_results"], EligibilityResult)
        validate_decisions(decisions, company, projects, eligibility)
        normalized_state = dict(state)
        normalized_state.update(
            {
                "project_decisions": decisions,
                "company_profile": company,
                "projects": projects,
                "eligibility_results": eligibility,
                "project_comparisons": ensure_model_list(
                    state["project_comparisons"], ProjectComparisonResult
                ),
            }
        )
        validate_portfolio_team_slots(normalized_state)
        return {"project_decisions": decisions, "current_node": "validate_decision"}

    def generate_decision_explanation(state):
        request = state["request"]
        payload = {
            "user_goal": request.user_goal,
            "interpreted_user_goal": state["interpreted_user_goal"].model_dump(mode="json"),
            "preference_policy": "本次偏好影响解释、优先级和组合，不改变客观100分或资格三态。",
            "eligibility_results": [item.model_dump(mode="json") for item in state["eligibility_results"]],
            "project_comparisons": [item.model_dump(mode="json") for item in state["project_comparisons"]],
            "competition_results": [item.model_dump(mode="json") for item in state["competition_results"]],
            "win_opportunity_results": [item.model_dump(mode="json") for item in state["win_results"]],
            "project_decisions": [item.model_dump(mode="json") for item in state["project_decisions"]],
            "evidence_ids": sorted(
                {
                    evidence_id
                    for item in state["project_decisions"]
                    for evidence_id in item.evidence_ids
                }
            ),
        }
        failures: list[dict[str, Any]] = []
        explanation = None
        last_error: Exception | None = None
        for _attempt in range(2):
            retry_payload = dict(payload)
            if failures:
                retry_payload["validation_feedback"] = failures[-1]
            try:
                raw = llm_provider.generate_structured(
                    system_prompt=DECISION_EXPLANATION_SYSTEM_PROMPT,
                    user_payload=retry_payload,
                    output_schema=DecisionExplanation,
                )
                explanation = validate_decision_explanation(raw, state)
                break
            except Exception as exc:
                last_error = exc
                failures.append(safe_exception_feedback(exc))
        if explanation is None:
            gateways["audit"].record(
                "bid_decision_explanation_failed",
                {"task_id": request.task_id, "errors": failures},
            )
            if last_error is not None:
                raise last_error
            raise RuntimeError("bid decision explanation failed without diagnostic")
        return {"decision_explanation": explanation, "current_node": "generate_decision_explanation"}

    def request_user_confirmation(state):
        request = state["request"]
        payload = HumanConfirmationRequest(
            confirmation_id=f"confirm-{request.task_id}-final-decision",
            task_id=request.task_id,
            thread_id=request.thread_id,
            confirmation_type="FINAL_DECISION",
            message="请确认是否采用当前多项目比较后生成的最终投标组合。",
            affected_project_ids=state.get("portfolio_project_ids", []),
            required_fields=[],
            qualification_gaps=[],
            interpreted_user_goal=state.get("interpreted_user_goal"),
            allowed_actions=["APPROVE_FINAL", "REJECT"],
        )
        raw = interrupt(payload.model_dump(mode="json"))
        response = HumanConfirmationResponse.model_validate(raw)
        if response.task_id != request.task_id or response.thread_id != request.thread_id:
            raise ValueError("confirmation task/thread mismatch")
        if response.confirmation_id != payload.confirmation_id:
            raise ValueError("confirmation_id mismatch")
        if response.action not in payload.allowed_actions:
            raise ValueError("confirmation action is not allowed for this interrupt")
        return {
            "final_decision_confirmation": response,
            "final_confirmation_rejected": response.action == "REJECT",
            "current_node": "request_user_confirmation",
        }

    def finalize_decision(state):
        request = state["request"]
        company = state["company_profile"]
        conflicts = list(state.get("resource_conflicts", []))
        selected_ids = list(state.get("portfolio_project_ids", []))
        comparison_map = {x.project_id: x for x in state["project_comparisons"]}
        if state.get("final_confirmation_rejected"):
            selected_ids = []
            conflicts.append("用户拒绝采用当前投标组合建议；保留项目分析但不保存推进组合")
        # Keep portfolio_recommendation as pure project IDs. Human-readable
        # descriptions belong in decision_explanation, not in an identifier list.
        portfolio = list(selected_ids)
        result = BidDecisionResult(
            task_id=request.task_id,
            thread_id=request.thread_id,
            company_id=company.company_id,
            company_profile_version=company.profile_version,
            user_goal=request.user_goal,
            interpreted_user_goal=ensure_model(state["interpreted_user_goal"], InterpretedUserGoal),
            project_comparisons=ensure_model_list(
                state["project_comparisons"], ProjectComparisonResult
            ),
            project_decisions=ensure_model_list(
                state["project_decisions"], ProjectDecisionResult
            ),
            decision_explanation=ensure_model(state["decision_explanation"], DecisionExplanation),
            portfolio_recommendation=portfolio,
            resource_conflicts=conflicts,
            pending_confirmation=None,
            status="DECIDED",
            generated_at=datetime.now(timezone.utc),
            fact_profile_version=company.fact_profile_version,
            capability_profile_version=company.capability_profile_version,
            decision_profile_version=company.decision_profile_version,
            evaluation_version=(state.get("evaluation").evaluation_version if state.get("evaluation") else None),
            project_versions={item.project_id: item.project_version for item in state["projects"]},
            competition_data_versions={
                item.project_id: item.competition_data_version
                for item in state.get("competition_results", [])
            },
            model_version=getattr(llm_provider, "model_version", None) or getattr(llm_provider, "model", None) or llm_provider.provider_name,
            prompt_version="bid-decision-prompts-v1",
            valid_until=min((item.valid_until for item in state["project_decisions"]), default=None),
            data_provider=company.data_provider,
            score_rule_version=SCORE_RULE_VERSION,
            score_is_temporary=True,
        )
        return {"result": result, "current_node": "finalize_decision"}

    def persist_decision(state):
        gateways["persistence"].save_decision(state["result"])
        gateways["audit"].record(
            "bid_decision_persisted",
            {"task_id": state["result"].task_id, "thread_id": state["result"].thread_id},
        )
        return {"current_node": "persist_decision"}

    def create_monitoring_plan(state):
        selected = set(state.get("portfolio_project_ids", []))
        if state.get("final_confirmation_rejected"):
            selected = set()
        plan = MonitoringPlan(
            task_id=state["result"].task_id,
            project_ids=[x.project_id for x in state["result"].project_decisions if x.project_id in selected],
            triggers=["PROJECT_VERSION_CHANGED", "COMPETITION_DATA_CHANGED", "COMPANY_PROFILE_CHANGED"],
            created_at=datetime.now(timezone.utc),
        )
        gateways["persistence"].save_monitoring_plan(plan)
        return {"monitoring_plan": plan, "current_node": "create_monitoring_plan"}

    return {
        "understand_task": understand_task,
        "load_company_profile": load_company_profile,
        "interpret_user_goal": interpret_user_goal,
        "load_required_evaluation": load_required_evaluation,
        "get_ranked_candidates": get_ranked_candidates,
        "load_project_snapshots": load_project_snapshots,
        "verify_eligibility": verify_eligibility,
        "route_eligibility_results": route_eligibility_results,
        "resolve_critical_unknowns": resolve_critical_unknowns,
        "analyze_competition": analyze_competition,
        "estimate_win_opportunity": estimate_win_opportunity,
        "compare_projects": compare_projects,
        "build_project_decisions": build_project_decisions,
        "validate_decision": validate_decision,
        "generate_decision_explanation": generate_decision_explanation,
        "request_user_confirmation": request_user_confirmation,
        "finalize_decision": finalize_decision,
        "persist_decision": persist_decision,
        "create_monitoring_plan": create_monitoring_plan,
    }
