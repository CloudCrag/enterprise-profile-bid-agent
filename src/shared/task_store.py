from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Protocol


class AgentTaskStore(Protocol):
    def get(self, task_id: str) -> dict[str, Any] | None: ...
    def create(self, record: dict[str, Any]) -> dict[str, Any]: ...
    def save(self, record: dict[str, Any]) -> dict[str, Any]: ...


def _normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    required = {"task_id", "thread_id", "task_status"}
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"task record missing fields: {missing}")
    clean = deepcopy(record)
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    return clean


class MemoryAgentTaskStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._records.get(task_id)
            return deepcopy(row) if row else None

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        clean = _normalise_record(record)
        with self._lock:
            if clean["task_id"] in self._records:
                raise KeyError(clean["task_id"])
            self._records[clean["task_id"]] = clean
            return deepcopy(clean)

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        clean = _normalise_record(record)
        with self._lock:
            self._records[clean["task_id"]] = clean
            return deepcopy(clean)


class SQLiteAgentTaskStore:
    """Persist API task metadata beside LangGraph checkpoints.

    LangGraph owns its checkpoint tables. This class only owns `agent_api_tasks`,
    so both components can safely share one SQLite database file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_api_tasks (
                    task_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    task_status TEXT NOT NULL,
                    pending_confirmation_json TEXT,
                    result_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row | tuple | None) -> dict[str, Any] | None:
        if row is None:
            return None
        task_id, thread_id, task_status, pending_json, result_json, updated_at = row
        record: dict[str, Any] = {
            "task_id": task_id,
            "thread_id": thread_id,
            "task_status": task_status,
            "updated_at": updated_at,
        }
        if pending_json:
            record["pending_confirmation"] = json.loads(pending_json)
        if result_json:
            record["result"] = json.loads(result_json)
        return record

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT task_id, thread_id, task_status,
                       pending_confirmation_json, result_json, updated_at
                FROM agent_api_tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        return self._row_to_record(row)

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        clean = _normalise_record(record)
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO agent_api_tasks(
                        task_id, thread_id, task_status,
                        pending_confirmation_json, result_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._params(clean),
                )
            except sqlite3.IntegrityError as exc:
                raise KeyError(clean["task_id"]) from exc
        return clean

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        clean = _normalise_record(record)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_api_tasks(
                    task_id, thread_id, task_status,
                    pending_confirmation_json, result_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    task_status = excluded.task_status,
                    pending_confirmation_json = excluded.pending_confirmation_json,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                self._params(clean),
            )
        return clean

    @staticmethod
    def _params(record: dict[str, Any]) -> tuple[Any, ...]:
        pending = record.get("pending_confirmation")
        result = record.get("result")
        return (
            record["task_id"],
            record["thread_id"],
            record["task_status"],
            json.dumps(pending, ensure_ascii=False, separators=(",", ":")) if pending is not None else None,
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result is not None else None,
            record["updated_at"],
        )
