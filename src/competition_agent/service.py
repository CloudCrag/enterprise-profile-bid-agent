from __future__ import annotations
from src.competition_agent.state import CompetitionState

def analyze_competition(graph, request):
    return graph.invoke(CompetitionState(request=request),config={"configurable":{"thread_id":f"competition:{request.company_id}:{request.project_id}"}})["result"]
