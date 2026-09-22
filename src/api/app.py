from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.types import Command
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.bid_decision_agent.graph import build_bid_decision_graph
from src.api.competition_detail_service import build_competition_detail
from src.competition_agent.graph import build_competition_graph
from src.competition_agent.result_builder import provider_model_version
from src.gateways.registry import build_gateways
from src.llm.provider_registry import PROVIDER_NAMES, build_llm_provider
from src.shared.checkpoints import memory_checkpointer, sqlite_checkpointer
from src.shared.config import Settings
from src.shared.errors import AgentException
from src.shared.model_normalization import ensure_model
from src.shared.safe_diagnostics import safe_diagnostic_value
from src.shared.schemas import (
    AgentError,
    BidDecisionRequest,
    BidDecisionResult,
    CompetitionAnalysisRequest,
    CompetitionAnalysisResult,
    CompetitionDetailResponse,
    HumanConfirmationResponse,
)
from src.shared.task_store import MemoryAgentTaskStore, SQLiteAgentTaskStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Runtime:
    """Composition root for gateways, official LangGraph and API task metadata."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        runtime_root = self.settings.runtime_data_root
        if not runtime_root.is_absolute():
            runtime_root = PROJECT_ROOT / runtime_root
        competition_demo_path = self.settings.competition_demo_path
        if not competition_demo_path.is_absolute():
            competition_demo_path = PROJECT_ROOT / competition_demo_path
        self.gateways = build_gateways(
            self.settings.data_provider,
            node_base_url=self.settings.profile_adapter_node_url,
            timeout_seconds=self.settings.gateway_timeout_seconds,
            database_url=self.settings.real_database_url,
            runtime_root=runtime_root,
            competition_demo_path=competition_demo_path,
        )
        self.llm = build_llm_provider(self.settings)
        self._checkpointer_context = None

        database_path = self.settings.agent_checkpoint_db_path
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        self.checkpoint_db_path = database_path
        self._checkpointer_context = sqlite_checkpointer(database_path)
        self.checkpointer = self._checkpointer_context.__enter__()
        self.task_store = SQLiteAgentTaskStore(database_path)

        self.competition_graph = build_competition_graph(None, self.gateways, self.llm)
        self.bid_graph = build_bid_decision_graph(
            self.checkpointer,
            self.gateways,
            self.llm,
            self.competition_graph,
        )

    def close(self) -> None:
        if self._checkpointer_context is not None:
            context, self._checkpointer_context = self._checkpointer_context, None
            context.__exit__(None, None, None)


def _error_response(error_code: str, message: str, details: dict | None = None, status_code: int = 400):
    payload = AgentError(error_code=error_code, message=message, details=details or {})
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _safe_validation_errors(exc: Exception) -> list[dict[str, Any]]:
    """Return validation errors without echoing request/model input values.

    FastAPI RequestValidationError and Pydantic ValidationError do not expose
    exactly the same errors() signature across locked/runtime versions.
    """
    try:
        errors = exc.errors(include_input=False, include_url=False)
    except TypeError:
        try:
            errors = exc.errors(include_input=False)
        except TypeError:
            errors = exc.errors()
    sanitized: list[dict[str, Any]] = []
    for item in errors:
        clean = dict(item)
        clean.pop("input", None)
        clean.pop("url", None)
        sanitized.append(safe_diagnostic_value(clean))
    return sanitized


def _pending_from_output(output: dict[str, Any]) -> dict[str, Any] | None:
    interruptions = output.get("__interrupt__")
    if not interruptions:
        return None
    value = interruptions[0].value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _record_from_output(task_id: str, thread_id: str, output: dict[str, Any]) -> dict[str, Any]:
    pending = _pending_from_output(output)
    record: dict[str, Any] = {
        "task_id": task_id,
        "thread_id": thread_id,
        "task_status": "WAITING_USER_CONFIRMATION" if pending else "DECIDED",
    }
    if pending:
        record["pending_confirmation"] = pending
    elif "result" in output:
        result = ensure_model(output["result"], BidDecisionResult)
        record["result"] = result.model_dump(mode="json", warnings="error")
    return record


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Keep `status` for first-round clients while persisting canonical task_status."""
    payload = dict(record)
    payload["status"] = record["task_status"]
    return payload


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            runtime.close()

    application = FastAPI(title="Bid Decision Agents", version="0.2.0", lifespan=lifespan)
    application.state.runtime = runtime

    @application.middleware("http")
    async def preserve_trace_id(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response

    @application.exception_handler(AgentException)
    async def agent_error_handler(request: Request, exc: AgentException):
        status_code = (
            404 if exc.error_code in {"agent_task_not_found", "agent_checkpoint_not_found"}
            else 409 if exc.error_code in {"agent_profile_version_mismatch", "agent_stale_result"}
            else 503 if exc.error_code == "provider_not_configured"
            else 400
        )
        return _error_response(exc.error_code, exc.message, safe_diagnostic_value(exc.details), status_code)

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        return _error_response(
            "agent_invalid_request",
            "Request schema validation failed",
            {"errors": _safe_validation_errors(exc)},
            422,
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error_code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return _error_response("agent_invalid_request", str(exc.detail), {}, exc.status_code)

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        return _error_response("agent_tool_failed", "Agent execution failed", {"type": type(exc).__name__}, 500)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/api/agent/status")
    def status():
        return {
            "data_provider": "real",
            "data_source_mode": "REAL_LOCAL_ENTERPRISE_REMOTE_PROJECT",
            "company_profile_data_source": "REAL_LOCAL_ENTERPRISE_JSON",
            "enterprise_evaluation_data_source": "REAL_LOCAL_ENTERPRISE_EVALUATION",
            "project_data_source": "REAL_PROJECT_DATABASE",
            "production_project_data_available": True,
            "llm_provider": runtime.llm.provider_name,
            "llm_model": getattr(runtime.llm, "model", None),
            "llm_model_version": provider_model_version(runtime.llm),
            "llm_api_key_configured": bool(runtime.settings.llm_api_key),
            "llm_live_required": True,
            "provider_registry": list(PROVIDER_NAMES),
            "network_used": runtime.llm.network_used,
            "checkpointer": "sqlite",
            "competition_data_mode": "DEMO",
            "competition_data_warning": "演示数据，不代表真实企业参与情况。",
            "score_rule_version": "TEMP-BID-SCORE-V2",
        }

    @application.get("/api/projects")
    def list_projects(query: str = "", page: int = 1, page_size: int = 5):
        projects, total = runtime.gateways["project"].search_projects(
            query=query, page=page, page_size=page_size
        )
        return {
            "items": [
                {
                    "project_id": item.project_id,
                    "project_name": item.project_name,
                    "region": item.region,
                    "industry": item.industry,
                    "budget": str(item.budget) if item.budget is not None else None,
                    "bid_deadline": item.bid_deadline.isoformat() if item.bid_deadline else None,
                    "bid_open_time": item.bid_open_time.isoformat() if item.bid_open_time else None,
                    "time_field_note": item.time_field_note,
                    "project_status": item.project_status,
                    "buyer_name": item.buyer_name,
                    "data_source": "真实项目数据库",
                }
                for item in projects
            ],
            "total": total,
            "page": max(1, page),
            "page_size": max(1, min(page_size, 100)),
            "total_pages": (total + max(1, min(page_size, 100)) - 1) // max(1, min(page_size, 100)),
        }

    @application.post("/api/competition/analyze", response_model=CompetitionAnalysisResult)
    def analyze(req: CompetitionAnalysisRequest):
        output = runtime.competition_graph.invoke(
            {"request": req},
            config={"configurable": {"thread_id": f"competition:{req.company_id}:{req.project_id}"}},
        )
        return output["result"]

    @application.get("/api/competition/{company_id}/{project_id}", response_model=CompetitionAnalysisResult)
    def get_competition(
        company_id: str,
        project_id: str,
        company_profile_version: str | None = None,
        project_version: str | None = None,
        competition_data_version: str | None = None,
    ):
        result = runtime.gateways["persistence"].get_competition(
            company_id,
            project_id,
            company_profile_version=company_profile_version,
            project_version=project_version,
            competition_data_version=competition_data_version,
        )
        if not result:
            raise AgentException("agent_task_not_found", "Competition result not found", {})
        expected_model = provider_model_version(runtime.llm)
        if result.model_version != expected_model:
            raise AgentException(
                "agent_stale_result",
                "Competition result was produced by a different model and must be recomputed",
                {"reason": "MODEL_VERSION_MISMATCH", "expected_model_version": expected_model},
            )
        if result.valid_until <= datetime.now(timezone.utc):
            raise AgentException(
                "agent_stale_result",
                "Competition result is stale and must be recomputed",
                {"reason": "RESULT_EXPIRED", "valid_until": result.valid_until.isoformat()},
            )
        return result

    @application.get(
        "/api/competition/{company_id}/{project_id}/detail",
        response_model=CompetitionDetailResponse,
    )
    def get_competition_detail(
        company_id: str,
        project_id: str,
        company_profile_version: str | None = None,
        project_version: str | None = None,
        competition_data_version: str | None = None,
        task_id: str | None = None,
        thread_id: str | None = None,
        ensure_analysis: bool = False,
    ):
        task_record = runtime.task_store.get(task_id) if task_id else None

        def analyze_for_detail(request_model: CompetitionAnalysisRequest, analysis_thread_id: str):
            output = runtime.competition_graph.invoke(
                {"request": request_model},
                config={"configurable": {"thread_id": analysis_thread_id}},
            )
            return ensure_model(output["result"], CompetitionAnalysisResult)

        return build_competition_detail(
            gateways=runtime.gateways,
            provider=runtime.settings.data_provider,
            company_id=company_id,
            project_id=project_id,
            company_profile_version=company_profile_version,
            project_version=project_version,
            competition_data_version=competition_data_version,
            task_id=task_id,
            thread_id=thread_id,
            ensure_analysis=ensure_analysis,
            task_record=task_record,
            analyze=analyze_for_detail,
            expected_model_version=provider_model_version(runtime.llm),
        )

    @application.post("/api/bid-decisions")
    def create_bid_decision(req: BidDecisionRequest):
        try:
            runtime.task_store.create(
                {
                    "task_id": req.task_id,
                    "thread_id": req.thread_id,
                    "task_status": "RUNNING",
                }
            )
        except KeyError as exc:
            raise AgentException(
                "agent_invalid_request", "task_id already exists", {"task_id": req.task_id}
            ) from exc

        try:
            output = runtime.bid_graph.invoke(
                {"request": req},
                config={"configurable": {"thread_id": req.thread_id}},
            )
        except Exception:
            runtime.task_store.save(
                {"task_id": req.task_id, "thread_id": req.thread_id, "task_status": "NEEDS_HUMAN_REVIEW"}
            )
            runtime.gateways["audit"].record(
                "bid_decision_execution_failed",
                {"task_id": req.task_id, "thread_id": req.thread_id, "phase": "create"},
            )
            raise
        record = runtime.task_store.save(_record_from_output(req.task_id, req.thread_id, output))
        return _public_record(record)

    @application.get("/api/bid-decisions/{task_id}")
    def get_bid_decision(task_id: str):
        record = runtime.task_store.get(task_id)
        if not record:
            raise AgentException("agent_task_not_found", "Task not found", {"task_id": task_id})
        return _public_record(record)

    def resume_task(task_id: str, confirmation: HumanConfirmationResponse):
        record = runtime.task_store.get(task_id)
        if not record:
            raise AgentException("agent_task_not_found", "Task not found", {"task_id": task_id})
        if confirmation.task_id != task_id or confirmation.thread_id != record["thread_id"]:
            raise AgentException("agent_invalid_request", "task_id/thread_id mismatch", {})
        if record["task_status"] != "WAITING_USER_CONFIRMATION":
            raise AgentException(
                "agent_invalid_request",
                "Task is not waiting for confirmation",
                {"task_status": record["task_status"]},
            )
        pending = record.get("pending_confirmation") or {}
        if confirmation.confirmation_id != pending.get("confirmation_id"):
            raise AgentException("agent_invalid_request", "confirmation_id mismatch", {})

        config = {"configurable": {"thread_id": record["thread_id"]}}
        try:
            snapshot = runtime.bid_graph.get_state(config)
        except Exception as exc:
            raise AgentException(
                "agent_checkpoint_not_found",
                "Checkpoint could not be loaded",
                {"task_id": task_id, "thread_id": record["thread_id"]},
            ) from exc
        if snapshot is None or not getattr(snapshot, "values", None):
            raise AgentException(
                "agent_checkpoint_not_found",
                "Checkpoint not found",
                {"task_id": task_id, "thread_id": record["thread_id"]},
            )

        try:
            output = runtime.bid_graph.invoke(
                Command(resume=confirmation.model_dump(mode="json")),
                config=config,
            )
        except Exception:
            # A failed resumed graph may already have advanced internal checkpoints.
            # Do not leave the old confirmation buttons active, because replaying the
            # same confirmation against a partially advanced checkpoint is unsafe.
            runtime.task_store.save(
                {"task_id": task_id, "thread_id": record["thread_id"], "task_status": "NEEDS_HUMAN_REVIEW"}
            )
            runtime.gateways["audit"].record(
                "bid_decision_resume_failed",
                {
                    "task_id": task_id,
                    "thread_id": record["thread_id"],
                    "confirmation_id": confirmation.confirmation_id,
                    "action": confirmation.action,
                },
            )
            raise
        saved = runtime.task_store.save(_record_from_output(task_id, record["thread_id"], output))
        return _public_record(saved)

    @application.post("/api/bid-decisions/{task_id}/confirmations")
    def confirm(task_id: str, confirmation: HumanConfirmationResponse):
        return resume_task(task_id, confirmation)

    @application.post("/api/bid-decisions/{task_id}/resume")
    def resume(task_id: str, confirmation: HumanConfirmationResponse):
        return resume_task(task_id, confirmation)

    return application


app = create_app()
