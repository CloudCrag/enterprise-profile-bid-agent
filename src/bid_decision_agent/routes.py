from __future__ import annotations


def route_after_eligibility(state) -> str:
    """Only critical UNKNOWN items need the human-resolution node."""
    return "resolve_critical_unknowns" if state.get("critical_unknown_project_ids") else "analyze_competition"


def route_after_unknown_resolution(state) -> str:
    """A supply action must be re-routed using the freshly recalculated eligibility state."""
    confirmation = state.get("unknown_resolution_confirmation")
    if (
        confirmation is not None
        and confirmation.action == "SUPPLY_AND_CONTINUE"
        and state.get("critical_unknown_project_ids")
    ):
        return "resolve_critical_unknowns"
    return "analyze_competition"
