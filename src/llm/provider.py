from __future__ import annotations
from typing import Protocol, TypeVar
from pydantic import BaseModel
T=TypeVar("T", bound=BaseModel)

class LLMProvider(Protocol):
    provider_name: str
    provider_mode: str
    network_used: bool
    def generate_structured(self, *, system_prompt: str, user_payload: dict, output_schema: type[T]) -> T: ...
