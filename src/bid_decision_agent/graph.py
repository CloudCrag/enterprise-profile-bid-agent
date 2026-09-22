from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.bid_decision_agent.nodes import build_nodes
from src.bid_decision_agent.routes import route_after_eligibility, route_after_unknown_resolution
from src.bid_decision_agent.state import BidDecisionState

NODE_ORDER = [
    "understand_task",
    "load_company_profile",
    "interpret_user_goal",
    "load_required_evaluation",
    "get_ranked_candidates",
    "load_project_snapshots",
    "verify_eligibility",
    "route_eligibility_results",
    "resolve_critical_unknowns",
    "analyze_competition",
    "estimate_win_opportunity",
    "compare_projects",
    "build_project_decisions",
    "validate_decision",
    "generate_decision_explanation",
    "request_user_confirmation",
    "finalize_decision",
    "persist_decision",
    "create_monitoring_plan",
]


def build_bid_decision_graph(checkpointer, gateways, llm_provider, competition_graph):
    nodes = build_nodes(gateways, llm_provider, competition_graph)
    graph = StateGraph(BidDecisionState)
    for name in NODE_ORDER:
        graph.add_node(name, nodes[name])

    graph.add_edge(START, "understand_task")
    graph.add_edge("understand_task", "load_company_profile")
    graph.add_edge("load_company_profile", "interpret_user_goal")
    graph.add_edge("interpret_user_goal", "load_required_evaluation")
    graph.add_edge("load_required_evaluation", "get_ranked_candidates")
    graph.add_edge("get_ranked_candidates", "load_project_snapshots")
    graph.add_edge("load_project_snapshots", "verify_eligibility")
    graph.add_edge("verify_eligibility", "route_eligibility_results")
    graph.add_conditional_edges(
        "route_eligibility_results",
        route_after_eligibility,
        {
            "resolve_critical_unknowns": "resolve_critical_unknowns",
            "analyze_competition": "analyze_competition",
        },
    )
    graph.add_conditional_edges(
        "resolve_critical_unknowns",
        route_after_unknown_resolution,
        {
            "resolve_critical_unknowns": "resolve_critical_unknowns",
            "analyze_competition": "analyze_competition",
        },
    )
    graph.add_edge("analyze_competition", "estimate_win_opportunity")
    graph.add_edge("estimate_win_opportunity", "compare_projects")
    graph.add_edge("compare_projects", "build_project_decisions")
    graph.add_edge("build_project_decisions", "validate_decision")
    graph.add_edge("validate_decision", "generate_decision_explanation")
    graph.add_edge("generate_decision_explanation", "request_user_confirmation")
    graph.add_edge("request_user_confirmation", "finalize_decision")
    graph.add_edge("finalize_decision", "persist_decision")
    graph.add_edge("persist_decision", "create_monitoring_plan")
    graph.add_edge("create_monitoring_plan", END)
    return graph.compile(checkpointer=checkpointer)
