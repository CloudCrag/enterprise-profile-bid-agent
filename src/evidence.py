"""Shared evidence-lineage helpers for enterprise-profile facts.

Evidence identifiers are deterministic and dataset-scoped.  The helpers never
infer business meaning, collection time, or source authority that upstream data
did not provide.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

EVIDENCE_SCHEMA_VERSION = "enterprise-profile-evidence/1.1.0"
_DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_source_dataset_id(value: Any) -> str:
    """Validate a stable logical dataset identifier.

    Dataset IDs are deliberately independent of absolute paths and downloaded
    filenames such as ``(1)`` or ``副本`` variants.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_dataset_id must be a non-empty string")
    dataset_id = value.strip()
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError(
            "source_dataset_id may contain only letters, digits, dot, low line and hyphen"
        )
    return dataset_id


def legacy_company_excel_evidence_id(source_sheet: str, source_row_number: int) -> str:
    """Return the phase-four row identifier retained for compatibility."""
    sheet = str(source_sheet or "").strip()
    if not sheet:
        raise ValueError("source_sheet must be non-empty")
    if not isinstance(source_row_number, int) or isinstance(source_row_number, bool) or source_row_number <= 0:
        raise ValueError("source_row_number must be a positive integer")
    return f"company_excel:{sheet}:row:{source_row_number}"


def deterministic_evidence_id(
    source_type: str,
    source_dataset_id: str,
    source_sheet: str,
    source_row_number: int,
) -> str:
    """Return a stable, dataset-scoped evidence identifier."""
    source = str(source_type or "").strip()
    sheet = str(source_sheet or "").strip()
    dataset_id = validate_source_dataset_id(source_dataset_id)
    if not source:
        raise ValueError("source_type must be non-empty")
    if not sheet:
        raise ValueError("source_sheet must be non-empty")
    if not isinstance(source_row_number, int) or isinstance(source_row_number, bool) or source_row_number <= 0:
        raise ValueError("source_row_number must be a positive integer")
    return f"{source}:{dataset_id}:{sheet}:row:{source_row_number}"


def company_excel_evidence_id(
    source_dataset_id: str,
    source_sheet: str,
    source_row_number: int,
) -> str:
    """Return a globally unique deterministic row ID for company Excel data."""
    return deterministic_evidence_id(
        "company_excel", source_dataset_id, source_sheet, source_row_number
    )


def build_company_excel_evidence(
    *,
    source_dataset_id: str,
    source_file: str,
    source_sheet: str,
    source_row_number: int,
    announcement_unique_id: Any,
    project_number: Any,
    source_url: Any,
    published_at: Any,
    tender_end_at: Any,
    opening_at: Any,
    record_status: str,
    quality_flags: Iterable[Any] | None,
    anonymized: bool,
) -> dict[str, Any]:
    """Build one deterministic evidence object for a company Excel row."""
    dataset_id = validate_source_dataset_id(source_dataset_id)
    evidence_id = company_excel_evidence_id(dataset_id, source_sheet, source_row_number)
    return {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "legacy_evidence_id": legacy_company_excel_evidence_id(
            source_sheet, source_row_number
        ),
        "source_type": "company_excel",
        "source_dataset_id": dataset_id,
        "source_file": str(source_file),
        "source_sheet": str(source_sheet),
        "source_row_number": source_row_number,
        "announcement_unique_id": announcement_unique_id or None,
        "project_number": project_number or None,
        "source_url": source_url or None,
        "record_time": {
            "published_at": published_at or None,
            "tender_end_at": tender_end_at or None,
            "opening_at": opening_at or None,
        },
        "collected_at": None,
        "collected_at_status": "not_provided",
        "is_mock": bool(anonymized),
        "anonymized": bool(anonymized),
        "record_status": record_status,
        "quality_flags": sorted({str(flag) for flag in (quality_flags or []) if flag}),
    }


def register_evidence(
    evidence_index: dict[str, dict[str, Any]], evidence: dict[str, Any]
) -> tuple[bool, str | None]:
    """Register one object; refuse an ID that maps to a different source row."""
    evidence_id = evidence.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        return False, "evidence_id_missing"
    existing = evidence_index.get(evidence_id)
    if existing is None:
        evidence_index[evidence_id] = deepcopy(evidence)
        return True, None
    comparable = (
        "source_type",
        "source_dataset_id",
        "source_file",
        "source_sheet",
        "source_row_number",
    )
    if all(existing.get(key) == evidence.get(key) for key in comparable):
        return True, None
    return False, "evidence_id_source_conflict"


def record_evidence_ids(record: Any) -> list[str]:
    """Return stable evidence references from a record without inventing IDs."""
    if not isinstance(record, dict):
        return []
    ids: list[str] = []
    raw_ids = record.get("evidence_ids")
    if isinstance(raw_ids, list):
        ids.extend(str(item) for item in raw_ids if isinstance(item, str) and item)
    raw_id = record.get("evidence_id")
    if isinstance(raw_id, str) and raw_id:
        ids.append(raw_id)
    return list(dict.fromkeys(ids))


def validate_evidence_index(evidence_index: Any) -> list[dict[str, Any]]:
    """Return validation warnings for malformed evidence objects."""
    warnings: list[dict[str, Any]] = []
    if evidence_index is None:
        return warnings
    if not isinstance(evidence_index, dict):
        return [{"code": "evidence_index_not_object", "message": "evidence_index必须是JSON对象。"}]

    source_rows: dict[tuple[Any, Any, Any, Any], str] = {}
    for key, evidence in evidence_index.items():
        if not isinstance(key, str) or not key:
            warnings.append({"code": "invalid_evidence_index_key", "evidence_id": key})
            continue
        if not isinstance(evidence, dict):
            warnings.append({"code": "evidence_object_not_object", "evidence_id": key})
            continue
        if evidence.get("evidence_id") != key:
            warnings.append({
                "code": "evidence_id_key_mismatch",
                "evidence_id": key,
                "object_evidence_id": evidence.get("evidence_id"),
            })
        dataset_id = evidence.get("source_dataset_id")
        try:
            validate_source_dataset_id(dataset_id)
        except ValueError:
            warnings.append({"code": "source_dataset_id_invalid", "evidence_id": key})
        row_number = evidence.get("source_row_number")
        if not isinstance(row_number, int) or isinstance(row_number, bool) or row_number <= 0:
            warnings.append({"code": "invalid_source_row_number", "evidence_id": key})
        sheet = evidence.get("source_sheet")
        if not isinstance(sheet, str) or not sheet.strip():
            warnings.append({"code": "source_sheet_missing", "evidence_id": key})
        if evidence.get("collected_at_status") == "not_provided" and evidence.get("collected_at") is not None:
            warnings.append({"code": "collection_time_status_conflict", "evidence_id": key})
        published_at = None
        if isinstance(evidence.get("record_time"), dict):
            published_at = evidence["record_time"].get("published_at")
        if evidence.get("collected_at") is not None and evidence.get("collected_at") == published_at:
            warnings.append({"code": "published_at_copied_to_collected_at", "evidence_id": key})

        source_locator = (
            evidence.get("source_type"),
            evidence.get("source_dataset_id"),
            evidence.get("source_sheet"),
            evidence.get("source_row_number"),
        )
        other_id = source_rows.get(source_locator)
        if other_id is not None and other_id != key:
            warnings.append({
                "code": "source_row_mapped_to_multiple_evidence_ids",
                "evidence_id": key,
                "other_evidence_id": other_id,
            })
        else:
            source_rows[source_locator] = key
    return warnings


def validate_evidence_references(
    evidence_index: dict[str, dict[str, Any]],
    references: Iterable[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return resolved IDs and explicit warnings for missing references."""
    resolved: list[str] = []
    warnings: list[dict[str, Any]] = []
    for evidence_id in dict.fromkeys(str(item) for item in references if item):
        if evidence_id in evidence_index:
            resolved.append(evidence_id)
        else:
            warnings.append({
                "code": "unresolved_evidence_reference",
                "evidence_id": evidence_id,
                "message": "证据引用未在evidence_index中找到。",
            })
    return resolved, warnings


def evidence_source_types(
    evidence_index: dict[str, dict[str, Any]], evidence_ids: Iterable[str]
) -> list[str]:
    return sorted({
        str(evidence_index[evidence_id].get("source_type"))
        for evidence_id in evidence_ids
        if evidence_id in evidence_index and evidence_index[evidence_id].get("source_type")
    })
