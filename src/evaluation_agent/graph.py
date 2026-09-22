"""LangGraph adapter for the deterministic enterprise evaluation workflow.

The project requires the installed upstream ``langgraph`` package. There is no
local compatibility executor or alternate runtime path.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

LANGGRAPH_BACKEND = "UPSTREAM_LANGGRAPH"


class EvaluationWorkflowState(TypedDict, total=False):
    run_id: str
    company_identity: dict[str, Any]
    company_id: str
    provider_id: str
    workflow_mode: str
    evaluation_model_version: str
    active_indicator_codes: list[str]
    core_indicator_codes: list[str]
    excluded_indicator_codes: list[str]
    current_fact_profile_ref: dict[str, Any]
    current_tag_profile_ref: dict[str, Any]
    current_capability_profile_ref: dict[str, Any]
    as_of_date: str
    round_index: int
    max_rounds: int
    max_api_calls: int
    feature_snapshot: dict[str, Any]
    indicator_results: list[dict[str, Any]]
    gap_inventory: dict[str, Any]
    acquisition_plan: dict[str, Any]
    api_call_records: list[dict[str, Any]]
    normalization_results: list[dict[str, Any]]
    fact_candidate_ids: list[str]
    review_task_ids: list[str]
    primary_dimensions: list[dict[str, Any]]
    secondary_dimensions: list[dict[str, Any]]
    evaluation_profile_id: str
    evaluation_card_id: str
    workflow_state: str
    waiting_for: list[str]
    termination_reason: str
    checkpoint_version: int
    current_node: str
    next_node: str | None
    trace: list[dict[str, Any]]
    dependencies: dict[str, Any]
    provider_health: dict[str, Any]
    provider_failure_status: str
    profile: dict[str, Any]
    card: dict[str, Any]
    no_change_count: int
    last_plan_hash: str | None
    last_provider_failure_hash: str | None
    provider_no_change_count: int
    context: dict[str, Any]
    evaluation_profile_version: int
    raw_response_refs: list[str]
    started_at_utc: str


Node = Callable[[EvaluationWorkflowState], EvaluationWorkflowState]
Edge = str | Callable[[EvaluationWorkflowState], str | None] | None
_TERMINAL = {
    "WAITING_REVIEW",
    "WAITING_DATA_PROVIDER",
    "WAITING_USER_INPUT",
    "PROVIDER_NOT_CONFIGURED",
    "MANUAL_INTERVENTION_REQUIRED",
    "FAILED",
    "COMPLETED",
    "COMPLETED_WITH_PARTIAL_COVERAGE",
    "MODEL_POLICY_PENDING",
}


class UpstreamCheckpointedGraph:
    backend = LANGGRAPH_BACKEND

    def __init__(
        self,
        nodes: dict[str, Node],
        edges: dict[str, Edge],
        *,
        entry: str,
        checkpoint_saver: Callable[[EvaluationWorkflowState], None],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.entry = entry
        self.checkpoint_saver = checkpoint_saver

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "entry": self.entry,
            "nodes": list(self.nodes),
            "edges": {
                key: value if isinstance(value, str) else "CONDITIONAL" if callable(value) else "END"
                for key, value in self.edges.items()
            },
            "checkpoint_mode": "UPSTREAM_GRAPH_WITH_REPOSITORY_NODE_CHECKPOINTS",
            "resume_mode": "SAME_RUN_FROM_PERSISTED_NODE",
        }

    def _compile(self, start_node: str):
        graph = StateGraph(EvaluationWorkflowState)
        for name, node in self.nodes.items():
            edge = self.edges.get(name)

            def wrapped(state: EvaluationWorkflowState, *, _name=name, _node=node, _edge=edge):
                working = deepcopy(state)
                working["current_node"] = _name
                working = _node(working)
                working["checkpoint_version"] = int(working.get("checkpoint_version", 0)) + 1
                working["next_node"] = _edge(working) if callable(_edge) else _edge
                self.checkpoint_saver(working)
                return working

            graph.add_node(name, wrapped)

        graph.set_entry_point(start_node)
        destinations = {name: name for name in self.nodes}
        destinations["__END__"] = END
        for name, edge in self.edges.items():
            if callable(edge):

                def router(state: EvaluationWorkflowState, *, _edge=edge):
                    if state.get("workflow_state") in _TERMINAL:
                        return "__END__"
                    return _edge(state) or "__END__"

                graph.add_conditional_edges(name, router, destinations)
            elif edge is None:
                graph.add_edge(name, END)
            else:
                graph.add_edge(name, edge)
        return graph.compile()

    def invoke(
        self,
        state: EvaluationWorkflowState,
        *,
        start_node: str | None = None,
    ) -> EvaluationWorkflowState:
        compiled = self._compile(start_node or state.get("next_node") or self.entry)
        return compiled.invoke(deepcopy(state), config={"recursion_limit": 80})


def build_checkpointed_state_graph(
    nodes: dict[str, Node],
    edges: dict[str, Edge],
    *,
    entry: str,
    checkpoint_saver: Callable[[EvaluationWorkflowState], None],
) -> UpstreamCheckpointedGraph:
    return UpstreamCheckpointedGraph(
        nodes,
        edges,
        entry=entry,
        checkpoint_saver=checkpoint_saver,
    )
