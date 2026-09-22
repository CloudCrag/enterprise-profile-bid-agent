"""Load project-local environment configuration safely and consistently.

The only supported local configuration source is ``<project root>/.env.local``.
Existing process environment variables always win because ``override=False`` is
used.  Parsing is delegated to ``python-dotenv``; this module intentionally does
not contain a fallback parser for secret-bearing files.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"

_LOCK = RLock()
_LOADED_PATHS: set[Path] = set()


@dataclass(frozen=True, slots=True)
class EnvironmentLoadResult:
    """Secret-free metadata about one environment loading attempt."""

    dotenv_path: Path
    file_found: bool
    load_attempted: bool
    already_loaded: bool
    override: bool

    def safe_summary(self) -> dict[str, object]:
        return {
            "source": ".env.local",
            "file_found": self.file_found,
            "load_attempted": self.load_attempted,
            "already_loaded": self.already_loaded,
            "override": self.override,
        }


def load_project_environment(
    *,
    dotenv_path: str | Path | None = None,
    override: bool = False,
    force: bool = False,
) -> EnvironmentLoadResult:
    """Load ``.env.local`` without overriding process environment variables.

    ``dotenv_path`` and ``force`` exist for isolated tests and tooling. Runtime
    callers should use the defaults.  No environment value is returned or
    logged by this function.
    """

    path = Path(dotenv_path).resolve() if dotenv_path is not None else DEFAULT_ENV_LOCAL_PATH
    with _LOCK:
        already_loaded = path in _LOADED_PATHS
        if already_loaded and not force:
            return EnvironmentLoadResult(
                dotenv_path=path,
                file_found=path.is_file(),
                load_attempted=False,
                already_loaded=True,
                override=override,
            )

        found = path.is_file()
        if found:
            # Security requirement: local files never overwrite values already
            # supplied by the operating system or process supervisor.
            load_dotenv(dotenv_path=path, override=override)
        if found:
            _LOADED_PATHS.add(path)
        return EnvironmentLoadResult(
            dotenv_path=path,
            file_found=found,
            load_attempted=found,
            already_loaded=already_loaded,
            override=override,
        )
