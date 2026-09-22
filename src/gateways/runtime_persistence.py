"""Durable local persistence for bid decisions, competition analyses and audit.

Files contain business results only. Secret-like keys are removed recursively
before persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from src.shared.schemas import BidDecisionResult, CompetitionAnalysisResult, MonitoringPlan

_SECRET_KEYS = {
    "authorization", "api_key", "apikey", "password", "database_password",
    "llm_api_key", "capability_ai_api_key", "token", "secret",
}


def _safe_component(value: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError("storage identifier cannot be empty")
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_sanitize(value), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class RuntimePersistenceGateway:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.competition_root = self.root / "competition_analysis"
        self.decision_root = self.root / "decisions"
        self.monitoring_root = self.root / "monitoring_plans"
        for path in (self.competition_root, self.decision_root, self.monitoring_root):
            path.mkdir(parents=True, exist_ok=True)

    def save_competition(self, result: CompetitionAnalysisResult) -> None:
        name = "__".join(
            _safe_component(value)
            for value in (
                result.company_id,
                result.project_id,
                result.company_profile_version,
                result.project_version,
                result.competition_data_version,
            )
        )
        _atomic_json(self.competition_root / f"{name}.json", result.model_dump(mode="json"))

    def get_competition(
        self,
        company_id: str,
        project_id: str,
        *,
        company_profile_version: str | None = None,
        project_version: str | None = None,
        competition_data_version: str | None = None,
    ) -> CompetitionAnalysisResult | None:
        candidates: list[CompetitionAnalysisResult] = []
        prefix = f"{_safe_component(company_id)}__{_safe_component(project_id)}__"
        for path in self.competition_root.glob(f"{prefix}*.json"):
            try:
                item = CompetitionAnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if company_profile_version is not None and item.company_profile_version != company_profile_version:
                continue
            if project_version is not None and item.project_version != project_version:
                continue
            if competition_data_version is not None and item.competition_data_version != competition_data_version:
                continue
            candidates.append(item)
        return max(candidates, key=lambda item: item.generated_at, default=None)

    def save_decision(self, result: BidDecisionResult) -> None:
        _atomic_json(
            self.decision_root / f"{_safe_component(result.task_id)}.json",
            result.model_dump(mode="json"),
        )

    def get_decision(self, task_id: str) -> BidDecisionResult | None:
        path = self.decision_root / f"{_safe_component(task_id)}.json"
        if not path.is_file():
            return None
        return BidDecisionResult.model_validate_json(path.read_text(encoding="utf-8"))

    def save_monitoring_plan(self, plan: MonitoringPlan) -> None:
        _atomic_json(
            self.monitoring_root / f"{_safe_component(plan.task_id)}.json",
            plan.model_dump(mode="json"),
        )

    def mark_stale(self, *, entity_type: str, entity_id: str, reason: str) -> None:
        RuntimeAuditGateway(self.root).record(
            "entity_marked_stale",
            {"entity_type": entity_type, "entity_id": entity_id, "reason": reason},
        )


class RuntimeAuditGateway:
    def __init__(self, root: Path):
        self.path = Path(root) / "audit" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "event_type": str(event_type),
            "payload": _sanitize(payload),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operator": "LOCAL_OPERATOR",
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
