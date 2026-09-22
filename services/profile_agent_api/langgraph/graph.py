from __future__ import annotations

from langgraph.graph import StateGraph, START, END

LANGGRAPH_BACKEND = "UPSTREAM_LANGGRAPH"

from .state import ProfileAgentState


def build_profile_agent_graph(nodes: object, *, entry: str = "load_company_context"):
    graph = StateGraph(ProfileAgentState)
    graph.add_node("load_company_context", nodes.load_company_context)
    graph.add_node("analyze_capability_semantics_with_llm", nodes.analyze_capability_semantics_with_llm)
    graph.add_node("analyze_profile_gaps", nodes.analyze_profile_gaps)
    graph.add_node("build_question_plan", nodes.build_question_plan)
    graph.add_node("wait_for_user_response", nodes.wait_for_user_response)
    graph.add_node("record_response", nodes.record_response)
    graph.add_node("route_response_processing", nodes.route_response_processing)
    graph.add_node("apply_decision_candidates", nodes.apply_decision_candidates)
    graph.add_node("create_review_tasks", nodes.create_review_tasks)
    graph.add_node("rescan_gaps", nodes.rescan_gaps)
    graph.add_node("persist_run", nodes.persist_run)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, entry)
    graph.add_conditional_edges(
        "load_company_context",
        lambda s: "llm" if s.get("capability_ai_enabled") and not s.get("llm_review_completed") else "skip",
        {"llm": "analyze_capability_semantics_with_llm", "skip": "analyze_profile_gaps"},
    )
    graph.add_conditional_edges(
        "analyze_capability_semantics_with_llm",
        lambda s: "finish" if s.get("analysis_only") else "continue",
        {"finish": "finalize", "continue": "analyze_profile_gaps"},
    )
    graph.add_conditional_edges(
        "analyze_profile_gaps",
        lambda s: (
            "decision_context"
            if s.get("status") == "DECISION_CONTEXT_RECONFIRMATION_REQUIRED"
            else "no_gaps" if not ((s.get("gap_inventory") or {}).get("gaps")) else "has_gaps"
        ),
        {"decision_context": END, "no_gaps": "rescan_gaps", "has_gaps": "build_question_plan"},
    )
    graph.add_edge("build_question_plan", "wait_for_user_response")
    graph.add_edge("wait_for_user_response", END)
    graph.add_edge("record_response", "route_response_processing")
    graph.add_edge("route_response_processing", "apply_decision_candidates")
    graph.add_conditional_edges(
        "apply_decision_candidates",
        lambda s: "pause" if s.get("waiting_for") == "decision_selection" else "continue",
        {"pause": END, "continue": "create_review_tasks"},
    )
    graph.add_edge("create_review_tasks", "rescan_gaps")
    graph.add_conditional_edges(
        "rescan_gaps",
        lambda s: "next_round" if s.get("next_round_required") else "stop",
        {"next_round": "build_question_plan", "stop": "persist_run"},
    )
    graph.add_edge("persist_run", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
