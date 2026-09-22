"""Formal 60-tag generation from an enterprise fact profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import TagCatalogError
from .fact_to_tag_mapper import CORE_TAG_CODES, map_fact_profile_to_tags
from .fact_validator import assert_valid_fact_profile
from .io_utils import load_json

TAG_SET_SCHEMA_VERSION = "enterprise-profile-tags/1.1.0"
TAG_CATALOG_SCHEMA_VERSION = "enterprise-profile-tag-catalog/1.0.0"


def load_tag_catalog(path: str | Path) -> dict[str, Any]:
    catalog = load_json(path)
    if not isinstance(catalog, dict):
        raise TagCatalogError("Tag catalog JSON root must be an object")
    if catalog.get("schema_version") != TAG_CATALOG_SCHEMA_VERSION:
        raise TagCatalogError("Unsupported or missing tag catalog schema version")
    primary = catalog.get("primary_dimensions")
    secondary = catalog.get("secondary_dimensions")
    tags = catalog.get("tags")
    if not isinstance(primary, list) or len(primary) != 3:
        raise TagCatalogError("Tag catalog must contain exactly 3 primary dimensions")
    if not isinstance(secondary, list) or len(secondary) != 11:
        raise TagCatalogError("Tag catalog must contain exactly 11 secondary dimensions")
    if not isinstance(tags, list) or len(tags) != 60:
        raise TagCatalogError("Tag catalog must contain exactly 60 tags")
    codes = [item.get("tag_code") for item in tags if isinstance(item, dict)]
    if len(codes) != 60 or any(not isinstance(code, str) or not code for code in codes):
        raise TagCatalogError("Every tag must have a non-empty tag_code")
    if len(set(codes)) != 60:
        raise TagCatalogError("Tag codes must be unique")
    core_codes = {item["tag_code"] for item in tags if item.get("is_core") is True}
    if core_codes != CORE_TAG_CODES:
        raise TagCatalogError("The catalog core-tag selection is inconsistent")
    return catalog


def generate_tag_profile(fact_profile: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    assert_valid_fact_profile(fact_profile)
    return map_fact_profile_to_tags(fact_profile, catalog)
