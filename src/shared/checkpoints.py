from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
import sqlite3
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.shared import schemas
from src.shared.checkpoint_schema_codec import CheckpointSchemaCodec

# Exact allowlist of custom schema types that can be reachable from the two graph states.
# Built-in safe types (datetime, Decimal, UUID, etc.) remain handled by LangGraph itself.
_CHECKPOINT_SCHEMA_TYPES = (
    schemas.EligibilityStatus,
    schemas.CompetitiveIntensity,
    schemas.BidDecision,
    schemas.Priority,
    schemas.DataQuality,
    schemas.CapabilityProfile,
    schemas.DecisionProfile,
    schemas.BudgetPreferences,
    schemas.RiskPreferences,
    schemas.InterpretedUserGoal,
    schemas.DecisionExplanation,
    schemas.CompanyProfileSnapshot,
    schemas.EnterpriseEvaluationSnapshot,
    schemas.ProjectSnapshot,
    schemas.RankedProjectCandidate,
    schemas.EligibilityItemResult,
    schemas.EligibilityResult,
    schemas.CompetitorRef,
    schemas.GroundedStatement,
    schemas.InferenceStatement,
    schemas.CompetitionAnalysisRequest,
    schemas.CompetitionAnalysisResult,
    schemas.WinOpportunityResult,
    schemas.CompetitionAssessment,
    schemas.ProjectComparisonResult,
    schemas.ProjectDecisionResult,
    schemas.BidDecisionRequest,
    schemas.HumanConfirmationResponse,
    schemas.BidDecisionResult,
    schemas.AgentError,
    schemas.CompetitionDataSnapshot,
    schemas.MonitoringPlan,
)

ALLOWED_MSGPACK_MODULES = tuple(
    (schema_type.__module__, schema_type.__name__) for schema_type in _CHECKPOINT_SCHEMA_TYPES
)


class SchemaAwareJsonPlusSerializer(JsonPlusSerializer):
    """Strict JsonPlus serializer with a safe project-schema envelope layer.

    The base serializer still handles LangGraph's built-in safe values. Project
    Pydantic models are converted to plain data before msgpack encoding and are
    reconstructed only from the explicit `_CHECKPOINT_SCHEMA_TYPES` registry.
    This avoids the strict-mode Pydantic encoding failure seen with
    langgraph-checkpoint 4.1.1 while preserving typed state after resume.
    """

    def __init__(self) -> None:
        serializer_parameters = inspect.signature(JsonPlusSerializer).parameters
        serializer_options: dict[str, Any] = {"pickle_fallback": False}
        if "allowed_msgpack_modules" in serializer_parameters:
            serializer_options["allowed_msgpack_modules"] = ALLOWED_MSGPACK_MODULES
        super().__init__(**serializer_options)
        self._schema_codec = CheckpointSchemaCodec(_CHECKPOINT_SCHEMA_TYPES)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return super().dumps_typed(self._schema_codec.encode(obj))

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        return self._schema_codec.decode(super().loads_typed(data))


def checkpoint_serializer() -> SchemaAwareJsonPlusSerializer:
    return SchemaAwareJsonPlusSerializer()


def memory_checkpointer() -> MemorySaver:
    return MemorySaver(serde=checkpoint_serializer())


@contextmanager
def sqlite_checkpointer(path: Path):
    """Create an official SqliteSaver with the same strict serializer as memory mode.

    The connection remains alive for the complete runtime and is closed only when
    this context manager exits, preserving restart and same-thread resume semantics.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    saver = SqliteSaver(connection, serde=checkpoint_serializer())
    try:
        yield saver
    finally:
        connection.close()
