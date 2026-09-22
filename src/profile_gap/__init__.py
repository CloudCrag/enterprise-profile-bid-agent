"""Task-scoped enterprise profile gap analysis."""

from .request import (
    GAP_ANALYSIS_REQUEST_SCHEMA_VERSION,
    finalize_gap_analysis_request,
    gap_analysis_request_content_hash,
    gap_analysis_request_id,
)
from .request_validator import assert_valid_gap_analysis_request, validate_gap_analysis_request
from .gap_analyzer import analyze_profile_gaps
from .gap_validator import (
    assert_valid_gap_inventory,
    assert_valid_gap_inventory_structure_only,
    validate_gap_inventory,
    validate_gap_inventory_structure_only,
)

__all__ = [
    "GAP_ANALYSIS_REQUEST_SCHEMA_VERSION",
    "finalize_gap_analysis_request",
    "gap_analysis_request_content_hash",
    "gap_analysis_request_id",
    "assert_valid_gap_analysis_request",
    "validate_gap_analysis_request",
    "analyze_profile_gaps",
    "assert_valid_gap_inventory",
    "validate_gap_inventory",
    "assert_valid_gap_inventory_structure_only",
    "validate_gap_inventory_structure_only",
]
