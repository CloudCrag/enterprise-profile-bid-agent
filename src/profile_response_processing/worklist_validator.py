"""Structure-only and strict validation for response-processing worklists."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from ..profile_responses.receipt_validator import validate_question_response_receipt
from .decision_candidate import (
    DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION,
    decision_candidate_content_hash,
    decision_candidate_id,
)
from .errors import issue
from .processing_item import (
    PROCESSING_STATUSES,
    REQUIRED_ACTION_CATALOG,
    normalize_required_actions,
    processing_item_content_hash,
    processing_item_id,
    processing_item_sort_key,
)
from .worklist import (
    RESPONSE_PROCESSING_WORKLIST_SCHEMA_VERSION,
    processing_summary,
    response_processing_worklist_content_hash,
    response_processing_worklist_id,
)
from .worklist_builder import expected_processing_items, response_processing_source_dependencies

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_response_processing_worklist.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _local_integrity_issues(worklist: Any) -> list[dict[str, Any]]:
    if not isinstance(worklist, dict):
        return [issue("response_processing_worklist_schema_invalid", "$", "Response processing worklist must be an object")]
    issues: list[dict[str, Any]] = []
    for error in sorted(_VALIDATOR.iter_errors(worklist), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("response_processing_worklist_schema_invalid", path, error.message))
    if worklist.get("response_processing_worklist_schema_version") != RESPONSE_PROCESSING_WORKLIST_SCHEMA_VERSION:
        issues.append(issue("response_processing_worklist_schema_invalid", "response_processing_worklist_schema_version", "Unsupported worklist schema version"))

    items_value = worklist.get("processing_items")
    items = items_value if isinstance(items_value, list) else []
    all_dict = isinstance(items_value, list) and all(isinstance(item, dict) for item in items)
    enterprise = worklist.get("enterprise") if isinstance(worklist.get("enterprise"), dict) else {}
    deps = worklist.get("source_dependencies") if isinstance(worklist.get("source_dependencies"), dict) else {}
    receipt_id = str(deps.get("question_response_receipt_id") or "")
    if all_dict:
        if items != sorted(items, key=processing_item_sort_key):
            issues.append(issue("response_processing_worklist_schema_invalid", "processing_items", "Processing items must use stable source order"))
        processing_ids: list[Any] = []
        response_ids: list[Any] = []
        for index, item in enumerate(items):
            path = f"processing_items.{index}"
            actions = item.get("required_actions") if isinstance(item.get("required_actions"), list) else []
            if len(actions) != len(set(actions)) or any(action not in REQUIRED_ACTION_CATALOG for action in actions):
                issues.append(issue("response_processing_actions_mismatch", f"{path}.required_actions", "Required actions must be unique catalog values"))
            if actions != normalize_required_actions(actions):
                issues.append(issue("response_processing_actions_mismatch", f"{path}.required_actions", "Required actions must use stable catalog order"))
            status = item.get("processing_status")
            candidate = item.get("decision_update_candidate")
            if status not in PROCESSING_STATUSES:
                issues.append(issue("response_processing_status_mismatch", f"{path}.processing_status", "Unknown processing status"))
            if status == "decision_update_candidate_ready" and not isinstance(candidate, dict):
                issues.append(issue("response_processing_candidate_required", f"{path}.decision_update_candidate", "Decision-ready item requires a candidate"))
            if status != "decision_update_candidate_ready" and candidate is not None:
                issues.append(issue("response_processing_candidate_not_allowed", f"{path}.decision_update_candidate", "Only decision confirmation may contain a decision candidate"))
            if status == "unavailable_recorded" and candidate is not None:
                issues.append(issue("response_processing_unavailable_update_forbidden", f"{path}.decision_update_candidate", "Unavailable declaration cannot create an update candidate"))
            if isinstance(candidate, dict):
                if candidate.get("candidate_schema_version") != DECISION_UPDATE_CANDIDATE_SCHEMA_VERSION:
                    issues.append(issue("response_processing_candidate_mismatch", f"{path}.decision_update_candidate.candidate_schema_version", "Unsupported candidate schema version"))
                try:
                    digest = decision_candidate_content_hash(candidate, enterprise=enterprise, response_item=item, question_item=item)
                    expected_id = decision_candidate_id(candidate, enterprise=enterprise, digest=digest)
                except (InputDataError, TypeError, AttributeError) as exc:
                    issues.append(issue("response_processing_candidate_hash_mismatch", f"{path}.decision_update_candidate.candidate_content_hash", str(exc)))
                else:
                    if candidate.get("candidate_content_hash") != digest:
                        issues.append(issue("response_processing_candidate_hash_mismatch", f"{path}.decision_update_candidate.candidate_content_hash", "Decision candidate content hash mismatch"))
                    if candidate.get("candidate_id") != expected_id:
                        issues.append(issue("response_processing_candidate_id_mismatch", f"{path}.decision_update_candidate.candidate_id", "Decision candidate ID mismatch"))
                if candidate.get("target_field") != item.get("target_code"):
                    issues.append(issue("response_processing_candidate_mismatch", f"{path}.decision_update_candidate.target_field", "Candidate target field must match processing target"))
            try:
                digest = processing_item_content_hash(item)
                expected_id = processing_item_id(item, enterprise=enterprise, receipt_id=receipt_id, digest=digest)
            except (InputDataError, TypeError, AttributeError) as exc:
                issues.append(issue("response_processing_worklist_hash_mismatch", f"{path}.processing_item_content_hash", str(exc)))
            else:
                if item.get("processing_item_content_hash") != digest:
                    issues.append(issue("response_processing_worklist_hash_mismatch", f"{path}.processing_item_content_hash", "Processing item content hash mismatch"))
                if item.get("processing_item_id") != expected_id:
                    issues.append(issue("response_processing_worklist_id_mismatch", f"{path}.processing_item_id", "Processing item ID mismatch"))
            processing_ids.append(item.get("processing_item_id"))
            response_ids.append(item.get("response_item_id"))
        if len(processing_ids) != len(set(processing_ids)) or len(response_ids) != len(set(response_ids)):
            issues.append(issue("response_processing_duplicate_item", "processing_items", "Processing and source response item IDs must be unique"))

        expected_summary = processing_summary(items, recorded_response_item_count=len(items))
        if worklist.get("processing_summary") != expected_summary:
            issues.append(issue("response_processing_summary_mismatch", "processing_summary", f"Expected {expected_summary}"))

    try:
        digest = response_processing_worklist_content_hash(worklist)
        expected_id = response_processing_worklist_id(worklist, digest)
    except (InputDataError, TypeError, AttributeError) as exc:
        issues.append(issue("response_processing_worklist_hash_mismatch", "response_processing_worklist_content_hash", str(exc)))
    else:
        if worklist.get("response_processing_worklist_content_hash") != digest:
            issues.append(issue("response_processing_worklist_hash_mismatch", "response_processing_worklist_content_hash", "Worklist content hash mismatch"))
        if worklist.get("response_processing_worklist_id") != expected_id:
            issues.append(issue("response_processing_worklist_id_mismatch", "response_processing_worklist_id", "Worklist ID mismatch"))
    return issues


def validate_response_processing_worklist_structure_only(worklist: Any) -> list[dict[str, Any]]:
    return _local_integrity_issues(worklist)


def assert_valid_response_processing_worklist_structure_only(worklist: Any) -> None:
    issues = validate_response_processing_worklist_structure_only(worklist)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _context_issues(
    worklist: dict[str, Any], receipt: Any, submission: Any, question_plan: Any, question_plan_request: Any,
    gap_inventory: Any, gap_analysis_request: Any, fact_profile: Any, capability_profile: Any, decision_profile: Any,
) -> list[dict[str, Any]]:
    required = (
        ("receipt", receipt, "response_processing_receipt_mismatch"),
        ("submission", submission, "response_processing_submission_mismatch"),
        ("question_plan", question_plan, "response_processing_dependency_mismatch"),
        ("question_plan_request", question_plan_request, "response_processing_dependency_mismatch"),
        ("gap_inventory", gap_inventory, "response_processing_dependency_mismatch"),
        ("gap_analysis_request", gap_analysis_request, "response_processing_dependency_mismatch"),
        ("fact_profile", fact_profile, "response_processing_dependency_mismatch"),
        ("capability_profile", capability_profile, "response_processing_dependency_mismatch"),
    )
    missing = [issue("response_processing_validation_context_required", name, f"Strict worklist validation requires {name}") for name, value, _ in required if value is None]
    deps = worklist.get("source_dependencies") if isinstance(worklist.get("source_dependencies"), dict) else {}
    if deps.get("decision_profile_id") is not None and decision_profile is None:
        missing.append(issue("response_processing_validation_context_required", "decision_profile", "Worklist records a decision profile dependency"))
    if missing:
        return missing
    invalid = [issue(code, name, f"{name} must be an object") for name, value, code in (*required, ("decision_profile", decision_profile, "response_processing_dependency_mismatch")) if value is not None and not isinstance(value, dict)]
    return invalid


def validate_response_processing_worklist(
    worklist: Any,
    *,
    receipt: Any = None,
    submission: Any = None,
    question_plan: Any = None,
    question_plan_request: Any = None,
    gap_inventory: Any = None,
    gap_analysis_request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> list[dict[str, Any]]:
    local = _local_integrity_issues(worklist)
    if not isinstance(worklist, dict):
        return local
    context_issues = _context_issues(worklist, receipt, submission, question_plan, question_plan_request, gap_inventory, gap_analysis_request, fact_profile, capability_profile, decision_profile)
    if context_issues:
        return local + context_issues
    receipt_issues = validate_question_response_receipt(
        receipt,
        submission=submission,
        question_plan=question_plan,
        question_plan_request=question_plan_request,
        gap_inventory=gap_inventory,
        gap_analysis_request=gap_analysis_request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if receipt_issues:
        return local + receipt_issues

    issues = list(local)
    if worklist.get("enterprise") != receipt.get("enterprise"):
        issues.append(issue("response_processing_enterprise_mismatch", "enterprise", "Worklist and receipt enterprises differ"))
    expected_deps = response_processing_source_dependencies(receipt)
    if worklist.get("source_dependencies") != expected_deps:
        issues.append(issue("response_processing_dependency_mismatch", "source_dependencies", "Worklist dependencies differ from strictly validated source chain"))

    try:
        expected_items = expected_processing_items(receipt, question_plan, decision_profile=decision_profile)
    except InputDataError as exc:
        return issues + [issue("response_processing_status_mismatch", "processing_items", str(exc))]
    actual_items = worklist.get("processing_items") if isinstance(worklist.get("processing_items"), list) else []
    if len(actual_items) != len(expected_items) or len(actual_items) != len(receipt.get("recorded_response_items") or []):
        issues.append(issue("response_processing_item_count_mismatch", "processing_items", f"Expected {len(expected_items)} processing items"))

    expected_map = {item.get("response_item_id"): item for item in expected_items}
    actual_map = {item.get("response_item_id"): item for item in actual_items if isinstance(item, dict)}
    for response_id, expected in expected_map.items():
        actual = actual_map.get(response_id)
        if not isinstance(actual, dict):
            issues.append(issue("response_processing_item_not_found", "processing_items", f"Missing processing item for response {response_id}"))
            continue
        source_keys = (
            "response_item_id", "response_item_content_hash", "question_item_id", "question_item_content_hash",
            "gap_id", "gap_content_hash", "requirement_id", "target_layer", "target_code", "importance",
            "response_outcome", "information_request_type",
        )
        for key in source_keys:
            if actual.get(key) != expected.get(key):
                issues.append(issue("response_processing_item_source_mismatch", f"processing_items.{key}", f"Expected {expected.get(key)!r}"))
        if actual.get("processing_status") != expected.get("processing_status"):
            issues.append(issue("response_processing_status_mismatch", "processing_items.processing_status", f"Expected {expected.get('processing_status')}"))
        if actual.get("required_actions") != expected.get("required_actions"):
            issues.append(issue("response_processing_actions_mismatch", "processing_items.required_actions", f"Expected {expected.get('required_actions')}"))
        if actual.get("material_references") != expected.get("material_references"):
            issues.append(issue("response_processing_material_mismatch", "processing_items.material_references", "Material references differ from recorded response"))
        expected_candidate = expected.get("decision_update_candidate")
        actual_candidate = actual.get("decision_update_candidate")
        if expected_candidate is None and actual_candidate is not None:
            issues.append(issue("response_processing_candidate_not_allowed", "processing_items.decision_update_candidate", "This response route cannot create a decision candidate"))
        elif expected_candidate is not None and actual_candidate is None:
            issues.append(issue("response_processing_candidate_required", "processing_items.decision_update_candidate", "Decision confirmation requires a candidate"))
        elif actual_candidate != expected_candidate:
            issues.append(issue("response_processing_candidate_mismatch", "processing_items.decision_update_candidate", "Decision candidate differs from the validated confirmation response"))
        if actual != expected:
            # Granular issues above remain authoritative; this catches unexpected extra semantic drift.
            issues.append(issue("response_processing_item_source_mismatch", "processing_items", f"Processing item differs from deterministic route for {response_id}"))

    for response_id in sorted(set(actual_map) - set(expected_map)):
        issues.append(issue("response_processing_item_not_found", "processing_items", f"Unexpected processing item for response {response_id}"))

    expected_summary = processing_summary(expected_items, recorded_response_item_count=len(receipt.get("recorded_response_items") or []))
    if worklist.get("processing_summary") != expected_summary:
        issues.append(issue("response_processing_summary_mismatch", "processing_summary", f"Expected {expected_summary}"))
    return issues


def assert_valid_response_processing_worklist(worklist: Any, **kwargs: Any) -> None:
    issues = validate_response_processing_worklist(worklist, **kwargs)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
