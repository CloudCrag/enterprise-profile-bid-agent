from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        clean = {k: v for k, v in payload.items() if k.lower() not in {"authorization", "api_key", "reasoning" + "_content"}}
        self.events.append(AuditEvent(event_type, clean))
