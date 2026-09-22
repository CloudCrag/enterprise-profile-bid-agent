"""Semantic, source-linked, and cryptographic validation for profile gap inventories."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ..errors import InputDataError
from .errors import issue
from .gap_inventory import gap_content_hash, gap_id, gap_inventory_content_hash, gap_inventory_id
from .request import GAP_INVENTORY_SCHEMA_VERSION, normalize_requirements, requirement_sort_key
from .request_validator import assert_valid_gap_analysis_request, validate_gap_analysis_request
from .requirement_evaluator import evaluate_requirement

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "schemas" / "enterprise_profile_gap_inventory.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())

_REQUIREMENT_FIELDS = ("requirement_id", "target_layer", "target_code", "importance")
_GAP_SEMANTIC_FIELDS = (
    "gap_status",
    "reason_code",
    "reason_summary",
    "source_references",
    "resolution_type",
)
_SATISFIED_DETAIL_FIELDS = (
    "satisfaction_status",
    "source_references",
    "limitation_count",
    "unknown_count",
    "limitations",
)


def _expected_summary(gaps: list[dict[str, Any]], satisfied: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_requirement_count": len(gaps) + len(satisfied),
        "satisfied_requirement_count": len(satisfied),
        "gap_count": len(gaps),
        "blocking_gap_count": sum(1 for item in gaps if item.get("importance") == "blocking"),
        "important_gap_count": sum(1 for item in gaps if item.get("importance") == "important"),
        "optional_gap_count": sum(1 for item in gaps if item.get("importance") == "optional"),
        "fact_gap_count": sum(1 for item in gaps if item.get("target_layer") == "fact"),
        "capability_gap_count": sum(1 for item in gaps if item.get("target_layer") == "capability"),
        "decision_gap_count": sum(1 for item in gaps if item.get("target_layer") == "decision"),
    }


def _result_entries(
    gaps: list[dict[str, Any]], satisfied: list[dict[str, Any]]
) -> list[tuple[str, int, dict[str, Any]]]:
    result: list[tuple[str, int, dict[str, Any]]] = []
    result.extend(("gap", index, item) for index, item in enumerate(gaps) if isinstance(item, dict))
    result.extend(("satisfied", index, item) for index, item in enumerate(satisfied) if isinstance(item, dict))
    return result


def _path(container: str, index: int, field: str | None = None) -> str:
    base = f"{'gaps' if container == 'gap' else 'satisfied_requirements'}.{index}"
    return f"{base}.{field}" if field else base


def _requirement_binding_issues(
    entries: list[tuple[str, int, dict[str, Any]]],
    requirement_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for container, index, actual in entries:
        requirement_id = str(actual.get("requirement_id") or "")
        expected = requirement_map.get(requirement_id)
        if expected is None:
            continue
        for field in _REQUIREMENT_FIELDS[1:]:
            if actual.get(field) != expected.get(field):
                issues.append(issue(
                    "gap_result_requirement_mismatch",
                    _path(container, index, field),
                    f"Result {field} must match requirement {requirement_id}: expected {expected.get(field)!r}",
                ))
    return issues


def _fact_reference_issues(
    actual: dict[str, Any],
    *,
    path: str,
    requirement: dict[str, Any],
    actual_outcome: str,
    fact_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    refs = actual.get("source_references") if isinstance(actual.get("source_references"), dict) else {}
    facts_by_id = {
        fact.get("fact_id"): fact
        for fact in fact_profile.get("facts", [])
        if isinstance(fact, dict) and fact.get("fact_id")
    }
    included_ids = set((fact_profile.get("fact_view") or {}).get("included_fact_ids") or [])
    cited_facts: list[dict[str, Any]] = []
    for fact_id_value in refs.get("fact_ids") or []:
        fact = facts_by_id.get(fact_id_value)
        if fact is None:
            issues.append(issue(
                "gap_source_reference_mismatch",
                f"{path}.source_references.fact_ids",
                f"Unknown fact_id: {fact_id_value}",
            ))
            continue
        cited_facts.append(fact)
        if fact.get("fact_type") != requirement.get("target_code"):
            issues.append(issue(
                "gap_fact_reference_type_mismatch",
                f"{path}.source_references.fact_ids",
                f"Fact {fact_id_value} has type {fact.get('fact_type')}, expected {requirement.get('target_code')}",
            ))
        if actual_outcome == "satisfied" and fact_id_value not in included_ids:
            issues.append(issue(
                "gap_fact_reference_view_mismatch",
                f"{path}.source_references.fact_ids",
                f"Satisfied fact reference is outside the current fact view: {fact_id_value}",
            ))
    linked_evidence = {
        str(evidence_id)
        for fact in cited_facts
        for evidence_id in fact.get("evidence_ids", [])
        if evidence_id
    }
    cited_evidence = set(refs.get("evidence_ids") or [])
    if not cited_evidence.issubset(linked_evidence):
        unlinked = sorted(cited_evidence - linked_evidence)
        issues.append(issue(
            "gap_evidence_not_linked_to_source",
            f"{path}.source_references.evidence_ids",
            f"Evidence is not linked to the cited target facts: {unlinked}",
        ))
    if refs.get("capability_ids") or refs.get("decision_profile_id") is not None:
        issues.append(issue(
            "gap_source_reference_mismatch",
            f"{path}.source_references",
            "Fact-layer results may only cite target facts and their evidence",
        ))
    return issues


def _capability_item_index(capability_profile: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    result: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for domain in capability_profile.get("capability_domains", []):
        if not isinstance(domain, dict):
            continue
        domain_code = str(domain.get("capability_type") or "")
        for section in ("capability_claims", "reviewed_semantic_observations"):
            for item in domain.get(section, []) or []:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("capability_id") or item.get("semantic_observation_id")
                if item_id:
                    result[str(item_id)] = (domain_code, section, item)
    return result


def _capability_reference_issues(
    actual: dict[str, Any],
    *,
    path: str,
    requirement: dict[str, Any],
    actual_outcome: str,
    capability_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    refs = actual.get("source_references") if isinstance(actual.get("source_references"), dict) else {}
    index = _capability_item_index(capability_profile)
    cited_items: list[dict[str, Any]] = []
    for capability_id_value in refs.get("capability_ids") or []:
        resolved = index.get(str(capability_id_value))
        if resolved is None:
            issues.append(issue(
                "gap_source_reference_mismatch",
                f"{path}.source_references.capability_ids",
                f"Unknown capability reference: {capability_id_value}",
            ))
            continue
        domain_code, section, item = resolved
        cited_items.append(item)
        if domain_code != requirement.get("target_code"):
            issues.append(issue(
                "gap_capability_reference_domain_mismatch",
                f"{path}.source_references.capability_ids",
                f"Capability {capability_id_value} belongs to {domain_code}, expected {requirement.get('target_code')}",
            ))
        if actual_outcome == "satisfied" and section != "capability_claims":
            issues.append(issue(
                "gap_capability_reference_not_formal_claim",
                f"{path}.source_references.capability_ids",
                f"Satisfied capability requirements may not rely on semantic observations: {capability_id_value}",
            ))
    linked_fact_ids = {
        str(fact_id_value)
        for item in cited_items
        for fact_id_value in item.get("source_fact_ids", [])
        if fact_id_value
    }
    linked_evidence_ids = {
        str(evidence_id)
        for item in cited_items
        for evidence_id in item.get("evidence_ids", [])
        if evidence_id
    }
    cited_fact_ids = set(refs.get("fact_ids") or [])
    cited_evidence_ids = set(refs.get("evidence_ids") or [])
    if not cited_fact_ids.issubset(linked_fact_ids):
        issues.append(issue(
            "gap_capability_reference_mismatch",
            f"{path}.source_references.fact_ids",
            f"Fact references are not linked to the cited capability items: {sorted(cited_fact_ids - linked_fact_ids)}",
        ))
    if not cited_evidence_ids.issubset(linked_evidence_ids):
        issues.append(issue(
            "gap_evidence_not_linked_to_source",
            f"{path}.source_references.evidence_ids",
            f"Evidence is not linked to the cited capability items: {sorted(cited_evidence_ids - linked_evidence_ids)}",
        ))
    if refs.get("decision_profile_id") is not None:
        issues.append(issue(
            "gap_capability_reference_mismatch",
            f"{path}.source_references.decision_profile_id",
            "Capability-layer results may not cite a decision profile",
        ))
    return issues


def _decision_reference_issues(
    actual: dict[str, Any],
    *,
    path: str,
    decision_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    refs = actual.get("source_references") if isinstance(actual.get("source_references"), dict) else {}
    expected_id = decision_profile.get("decision_profile_id") if decision_profile else None
    issues: list[dict[str, Any]] = []
    if refs.get("decision_profile_id") != expected_id:
        issues.append(issue(
            "gap_decision_reference_mismatch",
            f"{path}.source_references.decision_profile_id",
            f"Expected decision profile reference {expected_id!r}",
        ))
    if refs.get("fact_ids") or refs.get("capability_ids") or refs.get("evidence_ids"):
        issues.append(issue(
            "gap_decision_reference_mismatch",
            f"{path}.source_references",
            "Decision-layer results may only cite the current decision profile",
        ))
    return issues


def _semantic_result_issues(
    entries: list[tuple[str, int, dict[str, Any]]],
    *,
    requirement_map: dict[str, dict[str, Any]],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for container, index, actual in entries:
        requirement_id = str(actual.get("requirement_id") or "")
        requirement = requirement_map.get(requirement_id)
        if requirement is None:
            continue
        path = _path(container, index)
        try:
            expected_evaluation = evaluate_requirement(
                requirement,
                fact_profile=fact_profile,
                capability_profile=capability_profile,
                decision_profile=decision_profile,
            )
        except InputDataError as exc:
            issues.append(issue("gap_result_semantic_mismatch", path, str(exc)))
            continue
        expected_outcome = expected_evaluation["outcome"]
        expected = expected_evaluation["result"]
        if container != expected_outcome:
            issues.append(issue(
                "gap_result_outcome_mismatch",
                path,
                f"Requirement {requirement_id} must be in {expected_outcome}, not {container}",
            ))
        # Always validate the actual source semantics, even if it sits in the wrong array.
        layer = requirement.get("target_layer")
        if layer == "fact":
            issues.extend(_fact_reference_issues(
                actual,
                path=path,
                requirement=requirement,
                actual_outcome=container,
                fact_profile=fact_profile,
            ))
        elif layer == "capability":
            issues.extend(_capability_reference_issues(
                actual,
                path=path,
                requirement=requirement,
                actual_outcome=container,
                capability_profile=capability_profile,
            ))
        elif layer == "decision":
            issues.extend(_decision_reference_issues(actual, path=path, decision_profile=decision_profile))

        if container != expected_outcome:
            continue
        for field in _REQUIREMENT_FIELDS:
            if actual.get(field) != expected.get(field):
                # target/importance errors are already reported with the stronger binding code.
                if field != "requirement_id":
                    continue
                issues.append(issue(
                    "gap_result_requirement_mismatch",
                    f"{path}.{field}",
                    f"Expected {expected.get(field)!r}",
                ))
        if container == "gap":
            if actual.get("gap_status") != expected.get("gap_status"):
                issues.append(issue(
                    "gap_result_semantic_mismatch",
                    f"{path}.gap_status",
                    f"Expected gap_status {expected.get('gap_status')!r}",
                ))
            for field in ("reason_code", "reason_summary"):
                if actual.get(field) != expected.get(field):
                    issues.append(issue(
                        "gap_result_semantic_mismatch",
                        f"{path}.{field}",
                        f"Expected {field} {expected.get(field)!r}",
                    ))
            if actual.get("resolution_type") != expected.get("resolution_type"):
                issues.append(issue(
                    "gap_result_semantic_mismatch",
                    f"{path}.resolution_type",
                    f"Expected resolution_type {expected.get('resolution_type')!r}",
                ))
            if actual.get("source_references") != expected.get("source_references"):
                issues.append(issue(
                    "gap_source_reference_mismatch",
                    f"{path}.source_references",
                    "Gap source references differ from the deterministic evaluation result",
                ))
        else:
            mismatched = [field for field in _SATISFIED_DETAIL_FIELDS if actual.get(field) != expected.get(field)]
            for field in mismatched:
                code = "gap_source_reference_mismatch" if field == "source_references" else "gap_satisfaction_detail_mismatch"
                issues.append(issue(
                    code,
                    f"{path}.{field}",
                    f"Satisfied result differs from deterministic evaluation; expected {expected.get(field)!r}",
                ))
    return issues



def _local_integrity_issues(inventory: Any) -> list[dict[str, Any]]:
    """Validate only the inventory's own structure, ordering, IDs and hashes.

    This function deliberately does not prove that the business conclusions were
    calculated from a particular request or profile context.
    """
    if not isinstance(inventory, dict):
        return [issue("gap_inventory_schema_invalid", "$", "Gap inventory must be an object")]

    issues: list[dict[str, Any]] = []

    for error in sorted(_VALIDATOR.iter_errors(inventory), key=lambda item: (list(item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        issues.append(issue("gap_inventory_schema_invalid", path, error.message))
    if inventory.get("gap_inventory_schema_version") != GAP_INVENTORY_SCHEMA_VERSION:
        issues.append(issue("gap_inventory_schema_invalid", "gap_inventory_schema_version", "Unsupported gap inventory version"))

    deps = inventory.get("source_dependencies") if isinstance(inventory.get("source_dependencies"), dict) else {}
    request_id = str(deps.get("gap_analysis_request_id") or "")
    gaps_value = inventory.get("gaps")
    satisfied_value = inventory.get("satisfied_requirements")
    gaps = gaps_value if isinstance(gaps_value, list) else []
    satisfied = satisfied_value if isinstance(satisfied_value, list) else []
    gaps_are_objects = isinstance(gaps_value, list) and all(isinstance(item, dict) for item in gaps)
    satisfied_are_objects = isinstance(satisfied_value, list) and all(isinstance(item, dict) for item in satisfied)
    entries = _result_entries(gaps, satisfied)

    result_requirement_ids = [item.get("requirement_id") for _, _, item in entries]
    duplicate_result_ids = sorted({str(value) for value in result_requirement_ids if result_requirement_ids.count(value) > 1})
    if duplicate_result_ids:
        issues.append(issue("gap_requirement_coverage_mismatch", "gaps", f"Requirements appear more than once: {duplicate_result_ids}"))

    if gaps_are_objects and satisfied_are_objects:
        expected_summary = _expected_summary(gaps, satisfied)
        if inventory.get("gap_summary") != expected_summary:
            issues.append(issue("gap_summary_mismatch", "gap_summary", f"Expected {expected_summary}"))
        if gaps != sorted(gaps, key=requirement_sort_key):
            issues.append(issue("gap_inventory_schema_invalid", "gaps", "Gaps must use the stable requirement order"))
        if satisfied != sorted(satisfied, key=requirement_sort_key):
            issues.append(issue("gap_inventory_schema_invalid", "satisfied_requirements", "Satisfied requirements must use the stable requirement order"))

        gap_ids: list[Any] = []
        for index, gap in enumerate(gaps):
            digest = gap_content_hash(gap)
            if gap.get("gap_content_hash") != digest:
                issues.append(issue("gap_id_mismatch", f"gaps.{index}.gap_content_hash", "Gap content hash mismatch"))
            try:
                expected_id = gap_id(gap, enterprise=inventory.get("enterprise") or {}, gap_analysis_request_id=request_id, digest=digest)
            except InputDataError as exc:
                issues.append(issue("gap_id_mismatch", f"gaps.{index}.gap_id", str(exc)))
            else:
                if gap.get("gap_id") != expected_id:
                    issues.append(issue("gap_id_mismatch", f"gaps.{index}.gap_id", "Gap ID mismatch"))
            gap_ids.append(gap.get("gap_id"))
        duplicates = sorted({str(value) for value in gap_ids if gap_ids.count(value) > 1})
        if duplicates:
            issues.append(issue("gap_id_duplicate", "gaps", f"Duplicate gap_id values: {duplicates}"))

        # Content hashing sorts result entries, so only calculate it after both
        # arrays have been confirmed to contain JSON objects.
        digest = gap_inventory_content_hash(inventory)
        if inventory.get("gap_inventory_content_hash") != digest:
            issues.append(issue("gap_inventory_content_hash_mismatch", "gap_inventory_content_hash", "Gap inventory content hash mismatch"))
        try:
            expected_inventory_id = gap_inventory_id(inventory, digest)
        except InputDataError as exc:
            issues.append(issue("gap_inventory_id_mismatch", "gap_inventory_id", str(exc)))
        else:
            if inventory.get("gap_inventory_id") != expected_inventory_id:
                issues.append(issue("gap_inventory_id_mismatch", "gap_inventory_id", "Gap inventory ID mismatch"))

    return issues


def validate_gap_inventory_structure_only(inventory: Any) -> list[dict[str, Any]]:
    """Check JSON structure and local cryptographic integrity only.

    Passing this validation is not proof that the gap conclusions are correct for
    any request or enterprise profile context.
    """
    return _local_integrity_issues(inventory)


def assert_valid_gap_inventory_structure_only(inventory: Any) -> None:
    issues = validate_gap_inventory_structure_only(inventory)
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))


def _required_context_issues(
    inventory: Any,
    *,
    request: Any,
    fact_profile: Any,
    capability_profile: Any,
    decision_profile: Any,
) -> list[dict[str, Any]]:
    """Report context values that were not supplied at all.

    ``None`` means missing context. A non-None value with the wrong JSON type is
    handled separately by :func:`_context_type_issues`.
    """
    issues: list[dict[str, Any]] = []
    for field_name, value in (
        ("request", request),
        ("fact_profile", fact_profile),
        ("capability_profile", capability_profile),
    ):
        if value is None:
            issues.append(issue(
                "gap_validation_context_required",
                field_name,
                f"Strict gap inventory validation requires {field_name}",
            ))

    deps = inventory.get("source_dependencies") if isinstance(inventory, dict) and isinstance(inventory.get("source_dependencies"), dict) else {}
    decision_dependency_id = deps.get("decision_profile_id")
    if decision_dependency_id is not None and decision_profile is None:
        issues.append(issue(
            "gap_validation_context_required",
            "decision_profile",
            "Strict validation requires decision_profile because the inventory records a decision profile dependency",
        ))
    elif decision_dependency_id is None and decision_profile is not None:
        issues.append(issue(
            "gap_decision_dependency_mismatch",
            "decision_profile",
            "A decision profile was supplied but the inventory records no decision profile dependency",
        ))
    return issues


def _context_type_issues(
    *,
    request: Any,
    fact_profile: Any,
    capability_profile: Any,
    decision_profile: Any,
) -> list[dict[str, Any]]:
    """Report supplied context values that are not JSON objects."""
    issues: list[dict[str, Any]] = []
    if request is not None and not isinstance(request, dict):
        issues.append(issue(
            "gap_analysis_request_schema_invalid",
            "request",
            "Gap analysis request must be an object",
        ))
    if fact_profile is not None and not isinstance(fact_profile, dict):
        issues.append(issue(
            "gap_fact_dependency_mismatch",
            "fact_profile",
            "Fact profile must be an object",
        ))
    if capability_profile is not None and not isinstance(capability_profile, dict):
        issues.append(issue(
            "gap_capability_dependency_mismatch",
            "capability_profile",
            "Capability profile must be an object",
        ))
    if decision_profile is not None and not isinstance(decision_profile, dict):
        issues.append(issue(
            "gap_decision_dependency_mismatch",
            "decision_profile",
            "Decision profile must be an object",
        ))
    return issues


def _context_schema_issues(
    *,
    request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Validate each supplied context object before cross-object calculations."""
    issues: list[dict[str, Any]] = []
    request_issues = validate_gap_analysis_request(request)
    if request_issues:
        issues.extend(request_issues)

    from ..fact_validator import assert_valid_fact_profile
    from ..capability_validator import assert_valid_capability_profile
    from ..decision_profile_validator import assert_valid_decision_profile

    try:
        assert_valid_fact_profile(fact_profile)
    except InputDataError as exc:
        issues.append(issue("gap_fact_dependency_mismatch", "fact_profile", str(exc)))
    try:
        assert_valid_capability_profile(capability_profile)
    except InputDataError as exc:
        issues.append(issue("gap_capability_dependency_mismatch", "capability_profile", str(exc)))
    if decision_profile is not None:
        try:
            assert_valid_decision_profile(decision_profile)
        except InputDataError as exc:
            issues.append(issue("gap_decision_dependency_mismatch", "decision_profile", str(exc)))
    return issues

def _semantic_integrity_issues(
    inventory: dict[str, Any],
    *,
    request: dict[str, Any],
    fact_profile: dict[str, Any],
    capability_profile: dict[str, Any],
    decision_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Recompute business semantics after all inputs passed object/schema checks."""
    issues: list[dict[str, Any]] = []
    deps = inventory.get("source_dependencies") if isinstance(inventory.get("source_dependencies"), dict) else {}
    gaps = inventory.get("gaps") if isinstance(inventory.get("gaps"), list) else []
    satisfied = inventory.get("satisfied_requirements") if isinstance(inventory.get("satisfied_requirements"), list) else []
    entries = _result_entries(gaps, satisfied)

    requirements = normalize_requirements((request.get("task_context") or {}).get("requirements") or [])
    requirement_map = {str(item.get("requirement_id")): item for item in requirements}
    result_requirement_ids = [item.get("requirement_id") for _, _, item in entries]
    expected_requirement_ids = [item.get("requirement_id") for item in requirements]
    if Counter(result_requirement_ids) != Counter(expected_requirement_ids):
        issues.append(issue("gap_requirement_coverage_mismatch", "gaps", "Every requested requirement must appear exactly once as gap or satisfied"))

    expected_request_dependencies = {
        "gap_analysis_request_schema_version": request.get("gap_analysis_request_schema_version"),
        "gap_analysis_request_id": request.get("request_id"),
        "gap_analysis_request_content_hash": request.get("request_content_hash"),
    }
    for key, expected in expected_request_dependencies.items():
        if deps.get(key) != expected:
            issues.append(issue("gap_analysis_request_hash_mismatch", f"source_dependencies.{key}", f"Expected {expected}"))
    if inventory.get("enterprise") != request.get("enterprise"):
        issues.append(issue("gap_context_enterprise_mismatch", "enterprise", "Inventory enterprise differs from request"))
    expected_task = {
        "task_context_id": (request.get("task_context") or {}).get("task_context_id"),
        "task_goal": (request.get("task_context") or {}).get("task_goal"),
    }
    if inventory.get("task_context") != expected_task:
        issues.append(issue("gap_requirement_coverage_mismatch", "task_context", "Inventory task context differs from request"))
    issues.extend(_requirement_binding_issues(entries, requirement_map))

    from .gap_analyzer import _source_dependencies, _validate_context
    try:
        _validate_context(request, fact_profile, capability_profile, decision_profile)
    except InputDataError as exc:
        text = str(exc)
        code = text.split(":", 1)[0] if text.startswith("gap_") else "gap_capability_dependency_mismatch"
        issues.append(issue(code, "source_dependencies", text))
        return issues

    expected_deps = _source_dependencies(request, fact_profile, capability_profile, decision_profile)
    for key, expected in expected_deps.items():
        if deps.get(key) != expected:
            if key.startswith("fact_"):
                code = "gap_fact_dependency_mismatch"
            elif key.startswith("capability_"):
                code = "gap_capability_dependency_mismatch"
            elif key.startswith("decision_"):
                code = "gap_decision_dependency_mismatch"
            else:
                code = "gap_analysis_request_hash_mismatch"
            issues.append(issue(code, f"source_dependencies.{key}", f"Expected {expected}"))

    issues.extend(_semantic_result_issues(
        entries,
        requirement_map=requirement_map,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    ))
    return issues

def validate_gap_inventory(
    inventory: Any,
    *,
    request: Any = None,
    fact_profile: Any = None,
    capability_profile: Any = None,
    decision_profile: Any = None,
) -> list[dict[str, Any]]:
    """Strict business validation of a gap inventory.

    Ordinary malformed external JSON is reported as structured validation issues.
    ``None`` means a required context was not supplied; a non-None value of the
    wrong type is reported as a schema/dependency format error. This function
    never downgrades to structure-only validation.
    """
    local_issues = _local_integrity_issues(inventory)
    if not isinstance(inventory, dict):
        return local_issues

    missing_issues = _required_context_issues(
        inventory,
        request=request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if missing_issues:
        return local_issues + missing_issues

    type_issues = _context_type_issues(
        request=request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if type_issues:
        return local_issues + type_issues

    # The None/type checks above make these casts true without relying on Python
    # ``assert`` statements for external input validation.
    request_obj: dict[str, Any] = request
    fact_obj: dict[str, Any] = fact_profile
    capability_obj: dict[str, Any] = capability_profile
    decision_obj: dict[str, Any] | None = decision_profile

    schema_issues = _context_schema_issues(
        request=request_obj,
        fact_profile=fact_obj,
        capability_profile=capability_obj,
        decision_profile=decision_obj,
    )
    if schema_issues:
        return local_issues + schema_issues

    return local_issues + _semantic_integrity_issues(
        inventory,
        request=request_obj,
        fact_profile=fact_obj,
        capability_profile=capability_obj,
        decision_profile=decision_obj,
    )

def assert_valid_gap_inventory(
    inventory: Any,
    *,
    request: dict[str, Any] | None = None,
    fact_profile: dict[str, Any] | None = None,
    capability_profile: dict[str, Any] | None = None,
    decision_profile: dict[str, Any] | None = None,
) -> None:
    issues = validate_gap_inventory(
        inventory,
        request=request,
        fact_profile=fact_profile,
        capability_profile=capability_profile,
        decision_profile=decision_profile,
    )
    if issues:
        raise InputDataError(json.dumps(issues[0], ensure_ascii=False, sort_keys=True))
