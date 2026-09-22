"""Validation helpers for opaque external material references.

This module deliberately never opens, downloads, parses, or inspects material bytes.
"""
from __future__ import annotations
import re
from typing import Any
from .errors import issue

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def validate_material_reference(value: Any, *, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [issue("question_response_material_invalid", path, "Material reference must be an object")]
    issues: list[dict[str, Any]] = []
    material_id = value.get("material_id")
    digest = value.get("material_content_sha256")
    filename = value.get("original_filename")
    media_type = value.get("media_type")
    size = value.get("size_bytes")
    if not isinstance(material_id, str) or not material_id.strip():
        issues.append(issue("question_response_material_invalid", f"{path}.material_id", "material_id must be a non-empty string"))
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        issues.append(issue("question_response_material_invalid", f"{path}.material_content_sha256", "material_content_sha256 must be 64 lowercase hexadecimal characters"))
    if not isinstance(filename, str) or not filename.strip():
        issues.append(issue("question_response_material_invalid", f"{path}.original_filename", "original_filename must be a non-empty filename"))
    elif filename.startswith(("/", "\\")) or _DRIVE_RE.match(filename) or "../" in filename or "..\\" in filename or "/" in filename or "\\" in filename:
        issues.append(issue("question_response_material_invalid", f"{path}.original_filename", "original_filename must not contain a path"))
    if not isinstance(media_type, str) or not media_type.strip():
        issues.append(issue("question_response_material_invalid", f"{path}.media_type", "media_type must be a non-empty string"))
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        issues.append(issue("question_response_material_invalid", f"{path}.size_bytes", "size_bytes must be a positive integer"))
    return issues


def material_ids_and_hashes(response_items: list[dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]]]:
    seen: dict[str, str] = {}
    unique: set[str] = set()
    issues: list[dict[str, Any]] = []
    for item_index, item in enumerate(response_items):
        refs = item.get("material_references") if isinstance(item, dict) else []
        if not isinstance(refs, list):
            continue
        local_ids: list[str] = []
        for ref_index, ref in enumerate(refs):
            path = f"response_items.{item_index}.material_references.{ref_index}"
            issues.extend(validate_material_reference(ref, path=path))
            if not isinstance(ref, dict):
                continue
            material_id = ref.get("material_id")
            digest = ref.get("material_content_sha256")
            if isinstance(material_id, str):
                local_ids.append(material_id)
                unique.add(material_id)
                if material_id in seen and seen[material_id] != digest:
                    issues.append(issue("question_response_material_hash_conflict", path, f"material_id {material_id!r} is bound to more than one content hash"))
                elif isinstance(digest, str):
                    seen[material_id] = digest
        duplicates = sorted({value for value in local_ids if local_ids.count(value) > 1})
        if duplicates:
            issues.append(issue("question_response_material_invalid", f"response_items.{item_index}.material_references", f"Duplicate material_id values: {duplicates}"))
    return unique, issues
