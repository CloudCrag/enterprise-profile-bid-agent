from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
import types

import pytest

if "pymysql" not in sys.modules:
    sys.modules["pymysql"] = types.SimpleNamespace(
        connect=lambda **kwargs: None,
        cursors=types.SimpleNamespace(DictCursor=object),
    )

from src.capability_ai.provider_config import CapabilityAIConfig
from src.gateways.real_project_gateways import RealProjectStore
from src.shared.errors import GatewayError


class FailingCursor:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, *args, **kwargs): raise RuntimeError("relation query broken")


class FailingConnection:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return FailingCursor()


def test_competition_query_failure_is_not_converted_to_empty_list() -> None:
    store = RealProjectStore.__new__(RealProjectStore)
    store._connection = lambda: FailingConnection()
    with pytest.raises(GatewayError) as exc:
        store.relation_rows("db-project-1")
    assert exc.value.error_code == "real_competition_query_failed"


def test_only_zhipu_provider_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPABILITY_AI_PROVIDER", "other")
    with pytest.raises(Exception):
        CapabilityAIConfig.from_env()


def test_zhipu_requires_model_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPABILITY_AI_PROVIDER", "zhipu")
    monkeypatch.delenv("CAPABILITY_AI_MODEL", raising=False)
    monkeypatch.delenv("CAPABILITY_AI_API_KEY", raising=False)
    with pytest.raises(Exception):
        CapabilityAIConfig.from_env()


def test_resume_failure_disables_old_confirmation_buttons() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "api" / "app.py").read_text(encoding="utf-8")
    assert '"bid_decision_resume_failed"' in source
    assert '"task_status": "NEEDS_HUMAN_REVIEW"' in source
    assert "partially advanced checkpoint" in source

from src.shared.task_store import SQLiteAgentTaskStore


def test_failed_resume_state_clears_pending_confirmation(tmp_path: Path) -> None:
    store = SQLiteAgentTaskStore(tmp_path / "tasks.sqlite3")
    store.create(
        {
            "task_id": "task-1",
            "thread_id": "thread-1",
            "task_status": "WAITING_USER_CONFIRMATION",
            "pending_confirmation": {"confirmation_id": "confirm-1"},
        }
    )
    store.save(
        {
            "task_id": "task-1",
            "thread_id": "thread-1",
            "task_status": "NEEDS_HUMAN_REVIEW",
        }
    )
    record = store.get("task-1")
    assert record is not None
    assert record["task_status"] == "NEEDS_HUMAN_REVIEW"
    assert "pending_confirmation" not in record
