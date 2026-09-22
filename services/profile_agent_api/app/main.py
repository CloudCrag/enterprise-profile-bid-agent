from __future__ import annotations

from src.config.environment_loader import load_project_environment

load_project_environment()

import json
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from src.errors import InputDataError
from src.capability_ai.errors import CapabilityAIError

from .api_models import (
    AgentRunRequest,
    DecisionContextReconfirmationRequest,
    DecisionSelectionRequest,
    FactCandidateSubmissionRequest,
    ResponseResumeRequest,
    ReviewActionRequest,
    ReviewSupplementRequest,
    TaskRequirementsSubmissionRequest,
    EvaluationRunRequest,
    EvaluationAnswerSubmissionRequest,
    BidProfileSnapshotRequest,
    BidProfileUpdateRequest,
    DecisionPreferenceUpdateRequest,
    ProfilePerformanceInputRequest,
    ProfilePerformanceSupplementRequest,
    ProfilePersonnelInputRequest,
    ProfileGeneralInputRequest,
)
from .errors import ServiceError
from .service import ProfileMvpService
from .bid_decision_adapter import (
    build_company_profile_snapshot,
    build_evaluation_snapshot,
    update_profile_snapshot,
)

app = FastAPI(title="Enterprise Profile Agent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
service = ProfileMvpService()


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _trace_id(request: Request | None = None) -> str:
    if request is not None:
        existing = getattr(request.state, "trace_id", None)
        if isinstance(existing, str) and existing:
            return existing
    return uuid4().hex


def fail(code: str, message: str, details: Any = None, *, trace_id: str | None = None) -> dict[str, Any]:
    detail_value = details or {}
    resolved_trace_id = trace_id or _trace_id()
    # Top-level fields are the unified contract. ``error`` is retained as a
    # deprecated compatibility adapter for existing Node/Vue clients.
    return {
        "success": False,
        "data": None,
        "error_code": code,
        "message": message,
        "details": detail_value,
        "trace_id": resolved_trace_id,
        "error": {"code": code, "message": message, "details": detail_value},
    }


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    incoming = request.headers.get("x-trace-id", "").strip()
    request.state.trace_id = incoming[:128] if incoming else uuid4().hex
    response = await call_next(request)
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=fail(exc.code, exc.message, exc.details, trace_id=_trace_id(request)))


@app.exception_handler(InputDataError)
async def input_error_handler(request: Request, exc: InputDataError) -> JSONResponse:
    text = str(exc)
    details: Any = {}
    code = "PROFILE_INPUT_INVALID"
    message = text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            code = parsed.get("code", code)
            message = parsed.get("message", text)
            details = parsed
    except json.JSONDecodeError:
        if ":" in text:
            code = text.split(":", 1)[0]
    return JSONResponse(status_code=400, content=fail(code, message, details, trace_id=_trace_id(request)))


@app.exception_handler(CapabilityAIError)
async def capability_ai_error_handler(request: Request, exc: CapabilityAIError) -> JSONResponse:
    status_code = 502
    if exc.code in {"zhipu_api_key_missing", "zhipu_model_missing"}:
        status_code = 503
    elif exc.code == "zhipu_authentication_failed":
        status_code = 401
    elif exc.code in {"zhipu_permission_denied", "zhipu_insufficient_balance"}:
        status_code = 403
    elif exc.code == "zhipu_rate_limited":
        status_code = 429
    elif exc.code in {"zhipu_timeout", "zhipu_network_error", "zhipu_service_overloaded"}:
        status_code = 503
    return JSONResponse(
        status_code=status_code,
        content=fail(
            exc.code,
            exc.message,
            {"stage": exc.stage, **(exc.details or {})},
            trace_id=_trace_id(request),
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=fail("REQUEST_SCHEMA_INVALID", "请求参数不符合接口Schema。", {"issues": exc.errors()}, trace_id=_trace_id(request)))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=fail("profile_service_internal_error", "企业画像服务内部错误。", {"type": type(exc).__name__}, trace_id=_trace_id(request)))


@app.get("/internal/health")
def health() -> dict[str, Any]:
    return ok(service.health())

@app.get("/internal/ai/capability-provider/status")
def capability_ai_status() -> dict[str, Any]:
    return ok(service.ai_provider_status())


@app.get("/internal/companies")
def companies() -> dict[str, Any]:
    return ok(service.companies())


@app.get("/internal/dashboard")
def dashboard(company_id: str | None = None) -> dict[str, Any]:
    return ok(service.dashboard(company_id))


@app.get("/internal/companies/{company_id}/profile")
def profile(company_id: str) -> dict[str, Any]:
    return ok(service.company_profile(company_id))


@app.get("/internal/integration/companies/{company_id}/bid-profile-snapshot")
def bid_profile_snapshot(
    company_id: str,
    requested_profile_version: str | None = None,
    as_of_time: str | None = None,
) -> dict[str, Any]:
    return ok(build_company_profile_snapshot(
        service,
        company_id,
        requested_profile_version=requested_profile_version,
        as_of_time=as_of_time,
    ))


@app.post("/internal/integration/companies/{company_id}/bid-profile-snapshot")
def bid_profile_snapshot_with_context(
    company_id: str,
    payload: BidProfileSnapshotRequest,
) -> dict[str, Any]:
    return ok(build_company_profile_snapshot(
        service,
        company_id,
        requested_profile_version=payload.requested_profile_version,
        as_of_time=payload.as_of_time,
        current_task_constraints=payload.current_task_constraints,
    ))


@app.get("/internal/integration/companies/{company_id}/bid-evaluation-snapshot")
def bid_evaluation_snapshot(company_id: str, profile_version: str) -> dict[str, Any]:
    return ok(build_evaluation_snapshot(service, company_id, profile_version))


@app.post("/internal/integration/companies/{company_id}/profile-updates")
def bid_profile_update(company_id: str, payload: BidProfileUpdateRequest) -> dict[str, Any]:
    return ok(update_profile_snapshot(
        service,
        company_id,
        payload.current_profile_version,
        payload.provided_fields,
    ))


@app.get("/internal/companies/{company_id}/profile/history")
def profile_history(company_id: str) -> dict[str, Any]:
    return ok(service.profile_history(company_id))


@app.get("/internal/companies/{company_id}/profile/diff")
def profile_diff(company_id: str, layer: str, from_version: int, to_version: int) -> dict[str, Any]:
    return ok(service.profile_diff(company_id, layer, from_version, to_version))


@app.get("/internal/companies/{company_id}/gaps")
def gaps(company_id: str) -> dict[str, Any]:
    return ok(service.gaps(company_id))


@app.get("/internal/companies/{company_id}/review-tasks")
def review_tasks(company_id: str) -> dict[str, Any]:
    return ok(service.review_tasks(company_id))


@app.post("/internal/agent/runs")
def start_agent(payload: AgentRunRequest) -> dict[str, Any]:
    return ok(service.start_agent(payload.model_dump()))




@app.get("/internal/agent/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return ok(service.get_run(run_id))


@app.get("/internal/companies/{company_id}/agent-runs/latest")
def latest_agent_run(company_id: str) -> dict[str, Any]:
    return ok(service.latest_run(company_id))


@app.post("/internal/agent/runs/{run_id}/responses")
def submit_responses(run_id: str, payload: ResponseResumeRequest) -> dict[str, Any]:
    return ok(service.submit_responses(run_id, payload.submission))


@app.post("/internal/agent/runs/{run_id}/decision-selection")
def decision_selection(run_id: str, payload: DecisionSelectionRequest) -> dict[str, Any]:
    return ok(service.select_decisions(run_id, payload.selected_candidate_ids))


@app.post("/internal/review-tasks/{review_task_id}/approve")
def approve_review(review_task_id: str, payload: ReviewActionRequest) -> dict[str, Any]:
    return ok(service.review_action(review_task_id, "approve", payload.model_dump()))


@app.post("/internal/review-tasks/{review_task_id}/reject")
def reject_review(review_task_id: str, payload: ReviewActionRequest) -> dict[str, Any]:
    return ok(service.review_action(review_task_id, "reject", payload.model_dump()))


@app.post("/internal/review-tasks/{review_task_id}/needs-more-information")
def needs_more_review(review_task_id: str, payload: ReviewActionRequest) -> dict[str, Any]:
    return ok(service.review_action(review_task_id, "needs-more-information", payload.model_dump()))


@app.post("/internal/integration/fact-candidates")
def submit_fact_candidate(payload: FactCandidateSubmissionRequest) -> dict[str, Any]:
    return ok(service.submit_fact_candidate(payload.model_dump(mode="json")))


@app.get("/internal/integration/fact-candidates")
def list_fact_candidates(company_id: str | None = None) -> dict[str, Any]:
    return ok(service.fact_candidates(company_id))


@app.post("/internal/integration/task-requirements")
def submit_task_requirements(payload: TaskRequirementsSubmissionRequest) -> dict[str, Any]:
    return ok(service.submit_task_requirements(payload.model_dump(mode="json")))


@app.post("/internal/review-tasks/{review_task_id}/supplements")
def submit_review_supplement(review_task_id: str, payload: ReviewSupplementRequest) -> dict[str, Any]:
    return ok(service.submit_review_supplement(review_task_id, payload.model_dump(mode="json")))


@app.post("/internal/agent/runs/{run_id}/decision-reconfirmation")
def request_decision_reconfirmation(run_id: str, payload: DecisionContextReconfirmationRequest) -> dict[str, Any]:
    if payload.run_id != run_id:
        raise ServiceError("DECISION_RECONFIRMATION_RUN_MISMATCH", "run_id does not match route")
    return ok(service.request_decision_reconfirmation(run_id, payload.model_dump(mode="json")))


@app.post("/internal/companies/{company_id}/decision-preferences")
def update_decision_preferences(company_id: str, payload: DecisionPreferenceUpdateRequest) -> dict[str, Any]:
    return ok(service.update_decision_preferences(company_id, payload.model_dump(mode="json")))


@app.post("/internal/companies/{company_id}/profile-inputs/performance")
def submit_profile_performance(company_id: str, payload: ProfilePerformanceInputRequest) -> dict[str, Any]:
    return ok(service.submit_profile_performance(company_id, payload.model_dump(mode="json")))


@app.post("/internal/companies/{company_id}/profile-inputs/performance-supplement")
def supplement_profile_performance(company_id: str, payload: ProfilePerformanceSupplementRequest) -> dict[str, Any]:
    return ok(service.supplement_profile_performance(company_id, payload.model_dump(mode="json")))


@app.post("/internal/companies/{company_id}/profile-inputs/personnel")
def submit_profile_personnel(company_id: str, payload: ProfilePersonnelInputRequest) -> dict[str, Any]:
    return ok(service.submit_profile_personnel(company_id, payload.model_dump(mode="json")))

@app.post("/internal/companies/{company_id}/profile-inputs/general")
def submit_profile_general(company_id: str, payload: ProfileGeneralInputRequest) -> dict[str, Any]:
    return ok(service.submit_profile_general(company_id, payload.model_dump(mode="json")))



@app.get("/internal/evaluation/model")
def evaluation_model() -> dict[str, Any]:
    return ok(service.evaluation_model())


@app.get("/internal/evaluation/model/indicators")
def evaluation_indicators() -> dict[str, Any]:
    return ok(service.evaluation_indicators())


@app.get("/internal/evaluation/model/notes")
def evaluation_notes() -> dict[str, Any]:
    return ok(service.evaluation_notes())


@app.get("/internal/evaluation/model/api-catalog")
def evaluation_api_catalog() -> dict[str, Any]:
    return ok(service.evaluation_api_catalog())


@app.post("/internal/evaluation/runs")
def start_evaluation(payload: EvaluationRunRequest) -> dict[str, Any]:
    return ok(service.start_evaluation(payload.model_dump()))


@app.get("/internal/evaluation/runs/{run_id}")
def get_evaluation_run(run_id: str) -> dict[str, Any]:
    return ok(service.get_evaluation_run(run_id))


@app.post("/internal/evaluation/runs/{run_id}/resume")
def resume_evaluation_run(run_id: str) -> dict[str, Any]:
    return ok(service.resume_evaluation_run(run_id))


@app.post("/internal/evaluation/runs/{run_id}/answers")
def submit_evaluation_answers(run_id: str, payload: EvaluationAnswerSubmissionRequest) -> dict[str, Any]:
    return ok(service.submit_evaluation_answers(run_id, payload.model_dump()))


@app.get("/internal/companies/{company_id}/evaluation/latest")
def latest_evaluation(company_id: str) -> dict[str, Any]:
    return ok(service.latest_evaluation(company_id))


@app.get("/internal/companies/{company_id}/evaluation/history")
def evaluation_history(company_id: str) -> dict[str, Any]:
    return ok(service.evaluation_history(company_id))


@app.get("/internal/companies/{company_id}/evaluation-card/latest")
def latest_evaluation_card(company_id: str) -> dict[str, Any]:
    return ok(service.latest_evaluation_card(company_id))


@app.get("/internal/companies/{company_id}/evaluation-data-gaps")
def evaluation_data_gaps(company_id: str) -> dict[str, Any]:
    return ok(service.evaluation_data_gaps(company_id))


@app.get("/internal/companies/{company_id}/evaluation-api-plan")
def evaluation_api_plan(company_id: str) -> dict[str, Any]:
    return ok(service.evaluation_api_plan(company_id))



@app.get("/internal/enterprise-data/providers")
def enterprise_data_providers() -> dict[str, Any]:
    return ok(service.evaluation_providers())

@app.get("/internal/evaluation/graph")
def evaluation_graph() -> dict[str, Any]:
    return ok(service.evaluation_graph())

@app.get("/internal/enterprise-data/normalizers")
def enterprise_data_normalizers() -> dict[str, Any]:
    return ok(service.evaluation_normalizers())


