"""Small JSON I/O helpers with explicit, readable error messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import InputDataError


def load_json(path: str | Path) -> Any:
    """Load JSON while distinguishing missing files from invalid JSON syntax."""
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {json_path}")
    if not json_path.is_file():
        raise InputDataError(f"JSON path is not a file: {json_path}")

    try:
        with json_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as exc:
        raise InputDataError(
            f"Invalid JSON in {json_path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def write_json(path: str | Path, data: Any) -> None:
    """Write UTF-8 JSON and create the parent directory when necessary."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
