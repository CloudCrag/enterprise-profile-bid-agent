from __future__ import annotations
from langgraph.types import Command

def start_bid_decision(graph, request):
    return graph.invoke({"request":request},config={"configurable":{"thread_id":request.thread_id}})
def resume_bid_decision(graph, thread_id: str, confirmation):
    return graph.invoke(Command(resume=confirmation.model_dump(mode="json")),config={"configurable":{"thread_id":thread_id}})
