from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float_env(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True, slots=True)
class Settings:
    data_provider: str = field(default_factory=lambda: os.getenv("AGENT_DATA_PROVIDER", "real"))
    agent_checkpointer: str = "sqlite"
    agent_checkpoint_db_path: Path = field(
        default_factory=lambda: Path(os.getenv("AGENT_CHECKPOINT_DB_PATH", "runtime_data/checkpoints/agent.sqlite"))
    )
    llm_provider: str = "zhipu"
    llm_base_url: str = field(default_factory=lambda: os.getenv("CAPABILITY_AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"))
    llm_model: str = field(default_factory=lambda: os.getenv("CAPABILITY_AI_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("CAPABILITY_AI_API_KEY", ""))
    llm_timeout_seconds: int = field(default_factory=lambda: _int_env("CAPABILITY_AI_TIMEOUT_SECONDS", 180))
    llm_max_retries: int = field(default_factory=lambda: _int_env("CAPABILITY_AI_MAX_RETRIES", 2))
    llm_max_response_bytes: int = field(default_factory=lambda: _int_env("CAPABILITY_AI_MAX_RESPONSE_BYTES", 1_048_576))
    llm_temperature: float = field(default_factory=lambda: _float_env("CAPABILITY_AI_TEMPERATURE", 0))
    llm_max_tokens: int = field(default_factory=lambda: _int_env("CAPABILITY_AI_MAX_TOKENS", 4096))
    profile_adapter_node_url: str = field(default_factory=lambda: os.getenv("PROFILE_ADAPTER_NODE_URL", "http://127.0.0.1:3000"))
    gateway_timeout_seconds: float = field(default_factory=lambda: _float_env("AGENT_GATEWAY_TIMEOUT_SECONDS", 15.0))
    real_database_url: str = field(default_factory=lambda: os.getenv("REAL_DATABASE_URL", ""))
    runtime_data_root: Path = field(default_factory=lambda: Path(os.getenv("RUNTIME_DATA_ROOT", "runtime_data")))
    competition_demo_path: Path = field(default_factory=lambda: Path(os.getenv("COMPETITION_DEMO_PATH", "runtime_data/competition_demo/competitors.json")))

    @property
    def checkpoint_provider(self) -> str:
        return self.agent_checkpointer

    @property
    def checkpoint_sqlite_path(self) -> Path:
        return self.agent_checkpoint_db_path
