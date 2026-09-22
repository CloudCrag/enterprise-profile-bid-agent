from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.bid_decision_agent.comparison import SCORE_RULE_VERSION, compare_project_portfolio
from src.shared.schemas import CompetitiveIntensity, EligibilityStatus, InterpretedUserGoal


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


def make_state(*, eligibility: EligibilityStatus, competition_demo: bool = False, target_region: str | None = None):
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    project = ns(
        project_id="project-1",
        project_version="v1",
        project_name="真实项目",
        project_type="软件项目",
        industry="信息技术",
        region="北京市",
        budget=Decimal("5000000"),
        maximum_price=None,
        bid_deadline=None,
        bid_open_time=now,
        technical_scope=["数据平台", "软件开发"],
        performance_requirements=[],
        qualification_requirements=[],
        personnel_requirements=[{"role": "项目经理", "count": 1}],
        contract_risks=[],
        contract_risk_data_available=False,
    )
    capability = ns(
        industry_capability=["信息技术"],
        technical_capability=["数据平台", "软件开发"],
        similar_performance_capability=["信息化建设项目"],
        regional_delivery_capability=["北京市"],
        amount_experience_capability={"max_amount": Decimal("8000000")},
        personnel_resource_capability={"项目经理": 2},
    )
    decision = ns(max_concurrent_bids=2, available_bid_team_slots=2)
    company = ns(capability_profile=capability, decision_profile=decision)
    request = ns(
        as_of_time=now,
        resource_constraints={
            "available_bid_team_slots": 2,
            "max_concurrent_bids": 2,
            "project_team_requirements": {"project-1": 1},
            "project_personnel_requirements": {"project-1": {"项目经理": 1}},
        },
    )
    candidate = ns(
        project_id="project-1",
        rank=1,
        rank_score=999.0,
        score_breakdown={"旧推荐分": 999.0},
    )
    eligibility_result = ns(project_id="project-1", overall_status=eligibility)
    competition = ns(
        project_id="project-1",
        competitive_intensity=CompetitiveIntensity.HIGH,
        data_coverage=1.0,
        data_is_demo=competition_demo,
    )
    goal = InterpretedUserGoal(
        target_regions=[target_region] if target_region else [],
        available_bid_team_slots=2,
    )
    return {
        "request": request,
        "company_profile": company,
        "projects": [project],
        "candidates": [candidate],
        "eligibility_results": [eligibility_result],
        "competition_results": [competition],
        "win_results": [],
        "critical_unknown_project_ids": [],
        "interpreted_user_goal": goal,
    }


def test_full_match_pass_scores_100_under_v2() -> None:
    output = compare_project_portfolio(make_state(eligibility=EligibilityStatus.PASS))
    item = output["project_comparisons"][0]
    assert item.composite_score == 100.0
    assert item.temporary_score_breakdown == {
        "资格可投性": 40.0,
        "企业能力匹配": 40.0,
        "资源可执行性": 20.0,
    }
    assert item.score_rule_version == SCORE_RULE_VERSION == "TEMP-BID-SCORE-V2"
    assert item.preparation_time_score == 0.0
    assert item.contract_risk_score == 0.0
    assert item.selected_for_portfolio is True


def test_unknown_never_becomes_pass_and_uses_20_qualification_points() -> None:
    state = make_state(eligibility=EligibilityStatus.UNKNOWN)
    state["critical_unknown_project_ids"] = ["project-1"]
    output = compare_project_portfolio(state)
    item = output["project_comparisons"][0]
    assert item.eligibility == EligibilityStatus.UNKNOWN
    assert item.temporary_score_breakdown["资格可投性"] == 20.0
    assert item.composite_score == 80.0
    assert item.selected_for_portfolio is False


def test_demo_competition_old_rank_and_bid_open_time_do_not_change_objective_score() -> None:
    real_like = compare_project_portfolio(make_state(eligibility=EligibilityStatus.PASS, competition_demo=False))["project_comparisons"][0]
    demo = compare_project_portfolio(make_state(eligibility=EligibilityStatus.PASS, competition_demo=True))["project_comparisons"][0]
    assert real_like.composite_score == demo.composite_score == 100.0
    assert demo.competition_score == 0.0
    assert demo.win_opportunity_score == 0.0
    assert demo.preparation_time_score == 0.0
    assert any("不参与客观基础分" in reason for reason in demo.reasons)


def test_current_preference_is_separate_from_score_but_changes_match_result() -> None:
    matched = compare_project_portfolio(make_state(eligibility=EligibilityStatus.PASS, target_region="北京市"))["project_comparisons"][0]
    unmatched = compare_project_portfolio(make_state(eligibility=EligibilityStatus.PASS, target_region="山西省"))["project_comparisons"][0]
    assert matched.composite_score == unmatched.composite_score == 100.0
    assert matched.preference_match_level == "HIGH"
    assert unmatched.preference_match_level == "LOW"
    assert matched.preference_match_score > unmatched.preference_match_score


def test_preference_changes_portfolio_order_without_changing_objective_scores() -> None:
    state = make_state(eligibility=EligibilityStatus.PASS, target_region="山西省")
    # Both projects are objectively deliverable; the current-task preference alone
    # should decide which one is selected when only one slot is available.
    state["company_profile"].capability_profile.regional_delivery_capability.append("山西省")
    first = state["projects"][0]
    second = SimpleNamespace(**first.__dict__)
    second.project_id = "project-2"
    second.project_version = "v2"
    second.project_name = "山西项目"
    second.region = "山西省"
    state["projects"] = [first, second]
    state["candidates"] = [
        ns(project_id="project-1", rank=1, rank_score=999.0, score_breakdown={}),
        ns(project_id="project-2", rank=2, rank_score=1.0, score_breakdown={}),
    ]
    state["eligibility_results"] = [
        ns(project_id="project-1", overall_status=EligibilityStatus.PASS),
        ns(project_id="project-2", overall_status=EligibilityStatus.PASS),
    ]
    state["competition_results"] = []
    state["request"].resource_constraints["project_team_requirements"] = {
        "project-1": 1,
        "project-2": 1,
    }
    state["request"].resource_constraints["project_personnel_requirements"] = {
        "project-1": {"项目经理": 1},
        "project-2": {"项目经理": 1},
    }
    state["request"].resource_constraints["available_bid_team_slots"] = 1
    state["request"].resource_constraints["max_concurrent_bids"] = 1
    state["interpreted_user_goal"].available_bid_team_slots = 1

    output = compare_project_portfolio(state)
    by_id = {item.project_id: item for item in output["project_comparisons"]}
    assert by_id["project-1"].composite_score == by_id["project-2"].composite_score
    assert output["portfolio_project_ids"] == ["project-2"]
    assert by_id["project-2"].preference_match_score > by_id["project-1"].preference_match_score
