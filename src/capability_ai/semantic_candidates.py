"""Versioned output for validated semantic capability candidates."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.enterprise_capability_profile import (
    CAPABILITY_PROFILE_SCHEMA_VERSION,
    canonical_json,
    stable_enterprise_id,
)

from .errors import CapabilityAIError

SEMANTIC_CANDIDATES_SCHEMA_VERSION = "enterprise-capability-semantic-candidates/1.1.0"
_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_capability_semantic_candidates.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())

_VOLATILE_AUDIT_KEYS = {"started_at_utc", "finished_at_utc", "duration_ms"}


def semantic_candidate_content_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key not in {"candidate_id", "candidate_content_hash"}
    }


def semantic_candidate_content_hash(candidate: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(semantic_candidate_content_payload(candidate))).hexdigest()


def semantic_candidate_id(
    candidate: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    semantic_analysis_run_id: str,
) -> str:
    """Return the stable identity of a candidate within an enterprise analysis run.

    ``candidate_content_hash`` intentionally identifies only the normalized candidate
    body.  ``candidate_id`` additionally binds that body to the enterprise and the
    semantic analysis run so identical candidate text cannot collide across contexts.
    """
    enterprise_id = stable_enterprise_id(enterprise)
    if not isinstance(semantic_analysis_run_id, str) or not semantic_analysis_run_id.strip():
        raise CapabilityAIError(
            code="semantic_candidate_run_id_required",
            stage="candidate_identity",
            message="semantic_analysis_run_id is required to generate candidate_id",
        )
    capability_type = candidate.get("capability_type")
    if not isinstance(capability_type, str) or not capability_type.strip():
        raise CapabilityAIError(
            code="semantic_candidate_capability_type_required",
            stage="candidate_identity",
            message="capability_type is required to generate candidate_id",
        )
    basis = {
        "enterprise_id": enterprise_id,
        "semantic_analysis_run_id": semantic_analysis_run_id.strip(),
        "candidate_content_hash": semantic_candidate_content_hash(candidate),
        "capability_type": capability_type,
    }
    digest = hashlib.sha256(canonical_json(basis)).hexdigest()
    return f"semantic-candidate:{capability_type}:{digest[:24]}"


def finalize_semantic_candidate(
    candidate: dict[str, Any],
    *,
    enterprise: dict[str, Any],
    semantic_analysis_run_id: str,
) -> dict[str, Any]:
    result = deepcopy(candidate)
    result["candidate_content_hash"] = semantic_candidate_content_hash(result)
    result["candidate_id"] = semantic_candidate_id(
        result,
        enterprise=enterprise,
        semantic_analysis_run_id=semantic_analysis_run_id,
    )
    return {
        "candidate_id": result["candidate_id"],
        "candidate_content_hash": result["candidate_content_hash"],
        **{key: value for key, value in result.items() if key not in {"candidate_id", "candidate_content_hash"}},
    }


def _stable_audit(audit: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in audit.items() if key not in _VOLATILE_AUDIT_KEYS}


def semantic_content_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "semantic_candidates_schema_version": artifact.get("semantic_candidates_schema_version"),
        "enterprise": artifact.get("enterprise"),
        "as_of_date": artifact.get("as_of_date"),
        "source_dependencies": artifact.get("source_dependencies"),
        "provider": artifact.get("provider"),
        "run_status": artifact.get("run_status"),
        "capability_candidates": artifact.get("capability_candidates"),
        "rejected_or_unsupported_candidates": artifact.get("rejected_or_unsupported_candidates"),
        "global_unknowns": artifact.get("global_unknowns"),
        "review_status": artifact.get("review_status"),
        "audit_summary": _stable_audit(artifact.get("audit_summary") or {}),
        "evidence_index": artifact.get("evidence_index"),
    }


def semantic_analysis_content_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(semantic_content_payload(artifact))).hexdigest()


def semantic_analysis_run_id(
    *, request_content_hash: str, prompt_content_hash: str, provider_name: str,
    model_name: str | None, response_content_hash: str,
) -> str:
    basis = {
        "request_content_hash": request_content_hash,
        "prompt_content_hash": prompt_content_hash,
        "provider_name": provider_name,
        "model_name": model_name,
        "response_content_hash": response_content_hash,
    }
    return f"semantic-run:{hashlib.sha256(canonical_json(basis)).hexdigest()[:24]}"


def validate_semantic_candidates(artifact: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(artifact), key=lambda item: (list(item.absolute_path), item.message)):
        issues.append({
            "code": "semantic_candidates_schema_invalid",
            "field_path": ".".join(str(item) for item in error.absolute_path) or "$",
            "message": error.message,
        })
    if not isinstance(artifact, dict):
        return issues
    if artifact.get("semantic_analysis_content_hash") != semantic_analysis_content_hash(artifact):
        issues.append({
            "code": "semantic_candidates_hash_mismatch",
            "field_path": "semantic_analysis_content_hash",
            "message": "semantic_analysis_content_hash does not match normalized candidate content",
        })
    deps = artifact.get("source_dependencies") or {}
    audit = artifact.get("audit_summary") or {}
    expected_run_id = semantic_analysis_run_id(
        request_content_hash=deps.get("analysis_request_content_hash", ""),
        prompt_content_hash=deps.get("prompt_content_hash", ""),
        provider_name=(artifact.get("provider") or {}).get("provider_name", ""),
        model_name=(artifact.get("provider") or {}).get("model_name"),
        response_content_hash=audit.get("response_content_hash", ""),
    )
    if artifact.get("semantic_analysis_run_id") != expected_run_id:
        issues.append({
            "code": "semantic_candidates_run_id_mismatch",
            "field_path": "semantic_analysis_run_id",
            "message": "semantic_analysis_run_id does not match request, prompt, provider and response",
        })
    candidates = artifact.get("capability_candidates", []) if isinstance(artifact.get("capability_candidates"), list) else []
    candidate_ids = [
        candidate.get("candidate_id")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
    ]
    duplicate_ids = sorted({candidate_id for candidate_id in candidate_ids if candidate_ids.count(candidate_id) > 1})
    if duplicate_ids:
        issues.append({
            "code": "semantic_candidate_id_duplicate",
            "field_path": "capability_candidates",
            "message": f"Duplicate candidate_id values are not allowed: {', '.join(duplicate_ids)}",
        })
    enterprise = artifact.get("enterprise") if isinstance(artifact.get("enterprise"), dict) else {}
    run_id = artifact.get("semantic_analysis_run_id")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        expected_candidate_hash = semantic_candidate_content_hash(candidate)
        if candidate.get("candidate_content_hash") != expected_candidate_hash:
            issues.append({
                "code": "semantic_candidate_hash_mismatch",
                "field_path": f"capability_candidates.{index}.candidate_content_hash",
                "message": "candidate_content_hash does not match normalized candidate content",
            })
        try:
            expected_candidate_id = semantic_candidate_id(
                candidate,
                enterprise=enterprise,
                semantic_analysis_run_id=run_id,
            )
        except CapabilityAIError as exc:
            issues.append({
                "code": exc.code,
                "field_path": f"capability_candidates.{index}.candidate_id",
                "message": exc.message,
            })
            continue
        if candidate.get("candidate_id") != expected_candidate_id:
            issues.append({
                "code": "semantic_candidate_id_mismatch",
                "field_path": f"capability_candidates.{index}.candidate_id",
                "message": "candidate_id does not match enterprise, semantic analysis run and candidate content",
            })
    referenced_evidence = {
        evidence_id
        for candidate in artifact.get("capability_candidates", []) if isinstance(candidate, dict)
        for evidence_id in candidate.get("evidence_ids", [])
    }
    evidence_index = artifact.get("evidence_index") if isinstance(artifact.get("evidence_index"), dict) else {}
    if set(evidence_index) != referenced_evidence:
        issues.append({
            "code": "semantic_candidates_evidence_index_mismatch",
            "field_path": "evidence_index",
            "message": "evidence_index must contain exactly candidate-referenced evidence",
        })
    return issues


def assert_valid_semantic_candidates(artifact: Any) -> None:
    issues = validate_semantic_candidates(artifact)
    if issues:
        first = issues[0]
        raise CapabilityAIError(first["code"], "candidate_validation", first["message"], {"field_path": first["field_path"]})


def build_unique_candidate_map(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate a semantic candidate artifact before building an ID lookup map."""
    assert_valid_semantic_candidates(artifact)
    result: dict[str, dict[str, Any]] = {}
    for candidate in artifact.get("capability_candidates", []):
        candidate_id = candidate["candidate_id"]
        if candidate_id in result:
            # Defensive guard even though assert_valid_semantic_candidates checks this.
            raise CapabilityAIError(
                code="semantic_candidate_id_duplicate",
                stage="candidate_validation",
                message=f"Duplicate candidate_id values are not allowed: {candidate_id}",
                details={"field_path": "capability_candidates"},
            )
        result[candidate_id] = candidate
    return result


def build_semantic_candidates(
    *,
    request: dict[str, Any],
    prompt: dict[str, Any],
    response: dict[str, Any],
    provider_name: str,
    provider_mode: str,
    model_name: str | None,
    audit_summary: dict[str, Any],
    generated_at_utc: str,
) -> dict[str, Any]:
    allowed = {("zhipu", "external")}
    if (provider_name, provider_mode) not in allowed:
        raise CapabilityAIError(
            code="semantic_candidates_provider_not_allowed",
            stage="candidate_building",
            message="Provider is not allowed to create semantic candidates",
        )
    run_id = semantic_analysis_run_id(
        request_content_hash=request["request_content_hash"],
        prompt_content_hash=prompt["prompt_content_hash"],
        provider_name=provider_name,
        model_name=model_name,
        response_content_hash=audit_summary["response_content_hash"],
    )
    finalized_candidates = [
        finalize_semantic_candidate(
            item,
            enterprise=request["enterprise"],
            semantic_analysis_run_id=run_id,
        )
        for item in response["capability_candidates"]
    ]
    artifact = {
        "semantic_candidates_schema_version": SEMANTIC_CANDIDATES_SCHEMA_VERSION,
        "semantic_analysis_run_id": run_id,
        "enterprise": deepcopy(request["enterprise"]),
        "as_of_date": request.get("as_of_date"),
        "source_dependencies": {
            "analysis_request_schema_version": request["request_schema_version"],
            "analysis_request_content_hash": request["request_content_hash"],
            "capability_profile_schema_version": CAPABILITY_PROFILE_SCHEMA_VERSION,
            "capability_profile_id": request["capability_profile_id"],
            "capability_content_hash": request["capability_content_hash"],
            "prompt_schema_version": prompt["prompt_schema_version"],
            "prompt_content_hash": prompt["prompt_content_hash"],
            "evidence_policy_version": request["evidence_policy_version"],
        },
        "provider": {
            "provider_name": provider_name,
            "provider_mode": provider_mode,
            "model_name": model_name,
        },
        "run_status": "succeeded",
        "capability_candidates": finalized_candidates,
        "rejected_or_unsupported_candidates": deepcopy(response["rejected_or_unsupported_candidates"]),
        "global_unknowns": deepcopy(response["global_unknowns"]),
        "review_status": "pending_review",
        "audit_summary": deepcopy(audit_summary),
        "evidence_index": {
            evidence_id: deepcopy(request["evidence_index"][evidence_id])
            for evidence_id in sorted({
                evidence_id
                for candidate in response["capability_candidates"]
                for evidence_id in candidate.get("evidence_ids", [])
            })
        },
        "generated_at_utc": generated_at_utc,
    }
    artifact["semantic_analysis_content_hash"] = semantic_analysis_content_hash(artifact)
    ordered = {
        "semantic_candidates_schema_version": artifact["semantic_candidates_schema_version"],
        "semantic_analysis_run_id": artifact["semantic_analysis_run_id"],
        "semantic_analysis_content_hash": artifact["semantic_analysis_content_hash"],
        **{key: value for key, value in artifact.items() if key not in {"semantic_candidates_schema_version", "semantic_analysis_run_id", "semantic_analysis_content_hash"}},
    }
    assert_valid_semantic_candidates(ordered)
    return ordered
