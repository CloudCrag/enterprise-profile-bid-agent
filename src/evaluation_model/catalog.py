from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class EvaluationModelCatalog:
    def __init__(self, config_dir: Path | str = ROOT / "config") -> None:
        config_dir = Path(config_dir)
        self.catalog = json.loads((config_dir / "evaluation_indicator_catalog.json").read_text(encoding="utf-8"))
        self.selection = json.loads((config_dir / "evaluation_indicator_selection.json").read_text(encoding="utf-8"))
        self.rules = json.loads((config_dir / "evaluation_scoring_rules.json").read_text(encoding="utf-8"))
        self.api_catalog = json.loads((config_dir / "enterprise_api_catalog.json").read_text(encoding="utf-8"))
        self.api_requirements = json.loads((config_dir / "indicator_api_requirements.json").read_text(encoding="utf-8"))
        self.notes = json.loads((config_dir / "evaluation_model_notes.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((config_dir / "evaluation_model_manifest.json").read_text(encoding="utf-8"))
        self._indicators = {item["indicator_code"]: item for item in self.catalog["indicators"]}

    @property
    def source_sha256(self) -> str:
        return self.manifest["source_sha256"]

    def indicator(self, code: str) -> dict[str, Any]:
        return deepcopy(self._indicators[code])

    def indicators(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.catalog["indicators"]]

    def active_indicators(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.catalog["indicators"] if item.get("active", item.get("selection_status") != "DELETE_CANDIDATE")]

    def core_indicators(self) -> list[dict[str, Any]]:
        return [item for item in self.active_indicators() if item["selection_status"] == "CORE_RETAINED"]

    def excluded_indicators(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.catalog["indicators"] if not item.get("active", item.get("selection_status") != "DELETE_CANDIDATE")]

    def public_model(self) -> dict[str, Any]:
        manifest = deepcopy(self.manifest)
        manifest["publication_policy"] = "RULES_CONFIRMED"
        manifest["official_total_score_available"] = True
        return {
            "manifest": manifest,
            "primary_dimensions": deepcopy(self.catalog["primary_dimensions"]),
            "secondary_dimensions": deepcopy(self.catalog["secondary_dimensions"]),
            "selection": deepcopy(self.selection),
            "notes": deepcopy(self.notes["notes"]),
        }
