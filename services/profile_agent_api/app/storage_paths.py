"""Deterministic, cross-platform storage path helpers.

Business identifiers remain unchanged inside JSON documents.  Only local disk
path components are normalized.  A short digest is appended whenever a value
needs normalization so different unsafe identifiers cannot silently collide.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_RE = re.compile(r"[ .]+$")


def safe_storage_component(business_id: str) -> str:
    """Return one safe deterministic file/directory component.

    The function never returns ``.`` or ``..`` and never retains path
    separators.  It intentionally does not alter the identifier stored in the
    JSON payload.
    """
    if not isinstance(business_id, str) or not business_id.strip():
        raise ValueError("business_id must be a non-empty string")
    original = unicodedata.normalize("NFC", business_id)
    normalized = _INVALID_RE.sub("_", original)
    # Eliminate traversal-like dot segments even after separators were replaced.
    while ".." in normalized:
        normalized = normalized.replace("..", "__")
    normalized = _TRAILING_RE.sub("", normalized)
    normalized = normalized.strip()
    if normalized in {"", ".", ".."}:
        normalized = "_id"
    stem = normalized.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        normalized = f"_{normalized}"
    # Keep the common safe case readable; add identity protection if changed.
    if normalized != original:
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized}--{digest}"
    # Defensive final assertion: no path syntax may remain.
    if _INVALID_RE.search(normalized) or normalized in {".", ".."}:
        raise ValueError("unable to create a safe storage component")
    return normalized
