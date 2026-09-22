from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from src.competition_agent.state import CompetitionState
from src.competition_agent.nodes import build_nodes

NODE_ORDER=["load_project","load_company_profile","load_project_competition_snapshot","load_historical_bidders","load_buyer_suppliers","identify_confirmed_competitors","identify_potential_competitors","load_competitor_profiles","calculate_deterministic_statistics","compare_company_and_competitors","generate_competition_summary","validate_competition_result","persist_competition_result"]

def build_competition_graph(checkpointer, gateways, llm_provider):
    n=build_nodes(gateways,llm_provider); g=StateGraph(CompetitionState)
    mapping={
      "load_project":n["load_project"],"load_company_profile":n["load_company_profile"],"load_project_competition_snapshot":n["load_project_competition_snapshot"],
      "load_historical_bidders":n["load_historical_bidders"],"load_buyer_suppliers":n["load_buyer_suppliers"],"identify_confirmed_competitors":n["identify_confirmed_competitors"],
      "identify_potential_competitors":n["identify_potential_competitors"],"load_competitor_profiles":n["load_competitor_profiles"],"calculate_deterministic_statistics":n["calculate_deterministic_statistics"],
      "compare_company_and_competitors":n["compare_company_and_competitors"],"generate_competition_summary":n["generate_competition_summary"],
      "validate_competition_result":n["validate_competition_result"],"persist_competition_result":n["persist_competition_result"]}
    for name,fn in mapping.items(): g.add_node(name,fn)
    g.add_edge(START,NODE_ORDER[0])
    for a,b in zip(NODE_ORDER,NODE_ORDER[1:]): g.add_edge(a,b)
    g.add_edge(NODE_ORDER[-1],END)
    return g.compile(checkpointer=checkpointer)
