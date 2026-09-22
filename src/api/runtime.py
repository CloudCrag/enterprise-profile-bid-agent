"""Runtime ASGI entrypoint that loads project-local configuration exactly once."""
from __future__ import annotations

from src.config.environment_loader import load_project_environment

load_project_environment()

from src.api.app import create_app  # noqa: E402

app = create_app()
