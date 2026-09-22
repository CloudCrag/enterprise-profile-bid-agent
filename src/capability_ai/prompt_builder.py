"""Build a stable, versioned prompt envelope from an approved analysis request."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.ability_analysis_request import assert_valid_analysis_request
from src.enterprise_capability_profile import CAPABILITY_TYPES, canonical_json

from .errors import CapabilityAIError
from .provider_config import DEFAULT_PROMPT_VERSION

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_PATH = _ROOT / "config" / "prompts" / "enterprise_capability_semantic_prompt_v1.json"
_SCHEMA_PATH = _ROOT / "schemas" / "enterprise_capability_semantic_prompt.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def prompt_content_hash(prompt: dict[str, Any]) -> str:
    payload = {key: value for key, value in prompt.items() if key != "prompt_content_hash"}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def validate_prompt(prompt: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(prompt), key=lambda item: (list(item.absolute_path), item.message)):
        issues.append({
            "code": "capability_semantic_prompt_invalid",
            "field_path": ".".join(str(item) for item in error.absolute_path) or "$",
            "message": error.message,
        })
    if isinstance(prompt, dict) and prompt.get("prompt_content_hash") != prompt_content_hash(prompt):
        issues.append({
            "code": "capability_semantic_prompt_hash_mismatch",
            "field_path": "prompt_content_hash",
            "message": "prompt_content_hash does not match normalized prompt content",
        })
    return issues


def assert_valid_prompt(prompt: Any) -> None:
    issues = validate_prompt(prompt)
    if issues:
        first = issues[0]
        raise CapabilityAIError(first["code"], "prompt_validation", first["message"], {"field_path": first["field_path"]})


class PromptBuilder:
    def __init__(self, *, prompt_version: str = DEFAULT_PROMPT_VERSION) -> None:
        if prompt_version != DEFAULT_PROMPT_VERSION:
            raise CapabilityAIError(
                code="prompt_version_not_supported",
                stage="prompt_building",
                message=f"Unsupported prompt version: {prompt_version}",
            )
        self.prompt_version = prompt_version

    def build(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            assert_valid_analysis_request(request)
        except Exception as exc:
            raise CapabilityAIError(
                code="analysis_request_invalid",
                stage="prompt_building",
                message=str(exc),
            ) from exc
        requested = request.get("requested_capability_types", [])
        if any(item not in CAPABILITY_TYPES for item in requested):
            raise CapabilityAIError(
                code="prompt_capability_type_invalid",
                stage="prompt_building",
                message="Prompt request contains an unsupported capability domain",
            )
        template = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
        if template.get("prompt_schema_version") != self.prompt_version:
            raise CapabilityAIError(
                code="prompt_template_version_mismatch",
                stage="prompt_building",
                message="Prompt template version does not match configured version",
            )
        prompt = {
            **template,
            "request_content_hash": request["request_content_hash"],
            "evidence_policy_version": request["evidence_policy_version"],
            "analysis_input": {
                "enterprise": deepcopy(request["enterprise"]),
                "as_of_date": request.get("as_of_date"),
                "requested_capability_types": deepcopy(requested),
                "allowed_facts": deepcopy(request["allowed_facts"]),
                "allowed_tags": deepcopy(request["allowed_tags"]),
                "evidence_index": deepcopy(request["evidence_index"]),
                "deterministic_baseline": deepcopy(request["deterministic_baseline"]),
            },
        }
        prompt["prompt_content_hash"] = prompt_content_hash(prompt)
        ordered = {
            "prompt_schema_version": prompt["prompt_schema_version"],
            "prompt_content_hash": prompt["prompt_content_hash"],
            **{key: value for key, value in prompt.items() if key not in {"prompt_schema_version", "prompt_content_hash"}},
        }
        assert_valid_prompt(ordered)
        return ordered
