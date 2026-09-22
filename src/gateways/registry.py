from __future__ import annotations
from pathlib import Path

from src.gateways.real_project_gateways import build_real_gateways


def build_gateways(
    provider: str = "real",
    *,
    node_base_url: str = "http://127.0.0.1:3000",
    timeout_seconds: float = 15.0,
    database_url: str = "",
    runtime_root: Path = Path("runtime_data"),
    competition_demo_path: Path = Path("runtime_data/competition_demo/competitors.json"),
):
    normalized = provider.strip().lower()
    if normalized != "real":
        raise ValueError(f"Unsupported AGENT_DATA_PROVIDER={provider}; only real is supported")
    return build_real_gateways(
        node_base_url=node_base_url,
        database_url=database_url,
        runtime_root=runtime_root,
        competition_demo_path=competition_demo_path,
        timeout_seconds=timeout_seconds,
    )
