from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel

from src.shared.evidence import validate_evidence_ids
from src.shared.schemas import (
    BudgetPreferences,
    DecisionExplanation,
    InterpretedUserGoal,
    RiskPreferences,
)

FORBIDDEN_OUTPUT_KEYS = {
    "eligibility",
    "composite_score",
    "selected_for_portfolio",
    "decision",
    "priority",
    "probability",
    "evidence_id",
}


def _as_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return value
    raise TypeError("LLM structured output must be a Pydantic model or dict")


def ensure_no_forbidden_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_OUTPUT_KEYS:
                raise ValueError(f"forbidden LLM output field at {path}.{key}")
            ensure_no_forbidden_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_no_forbidden_fields(child, path=f"{path}[{index}]")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid budget value: {value!r}") from exc


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    raise ValueError("constraint list value must be a string or list")


def structured_goal_constraints(request, company) -> InterpretedUserGoal:
    constraints = dict(request.resource_constraints)
    budget_obj = constraints.get("budget_preferences", {})
    if not isinstance(budget_obj, dict):
        budget_obj = {}
    min_amount = budget_obj.get("min_amount", constraints.get("budget_min", company.decision_profile.budget_min))
    max_amount = budget_obj.get("max_amount", constraints.get("budget_max", company.decision_profile.budget_max))
    risk_obj = constraints.get("risk_preferences", {})
    if isinstance(risk_obj, str):
        risk_obj = {"level": risk_obj}
    if not isinstance(risk_obj, dict):
        risk_obj = {}
    level = str(risk_obj.get("level", constraints.get("risk_preference", company.decision_profile.risk_preference))).upper()
    if level not in {"LOW", "MEDIUM", "HIGH"}:
        level = company.decision_profile.risk_preference
    specified: list[str] = []
    if "target_industries" in constraints:
        specified.append("industry")
    if "target_regions" in constraints:
        specified.append("region")
    if any(key in constraints for key in ("budget_preferences", "budget_min", "budget_max")):
        specified.append("budget")
    if any(key in constraints for key in ("risk_preferences", "risk_preference", "avoid_contract_risks")):
        specified.append("risk")
    if "explicit_exclusions" in constraints:
        specified.append("exclusion")
    if "available_bid_team_slots" in constraints:
        specified.append("team_slots")
    return InterpretedUserGoal(
        target_industries=_list_value(constraints.get("target_industries", company.decision_profile.strategic_industries)),
        target_regions=_list_value(constraints.get("target_regions", company.decision_profile.strategic_regions)),
        budget_preferences=BudgetPreferences(
            min_amount=_decimal_or_none(min_amount),
            max_amount=_decimal_or_none(max_amount),
        ),
        available_bid_team_slots=max(
            0,
            int(
                constraints.get(
                    "available_bid_team_slots",
                    company.decision_profile.available_bid_team_slots,
                )
            ),
        ),
        risk_preferences=RiskPreferences(
            level=level,
            avoid_conditions=_list_value(
                risk_obj.get("avoid_conditions", constraints.get("avoid_contract_risks", []))
            ),
        ),
        explicit_exclusions=_list_value(
            constraints.get("explicit_exclusions", company.decision_profile.excluded_conditions)
        ),
        specified_preferences=specified,
        current_task_constraints=constraints,
    )


def merge_structured_constraints(interpreted: InterpretedUserGoal, request, company) -> InterpretedUserGoal:
    """Apply deterministic structured constraints after the LLM so they cannot be overwritten."""
    structured = structured_goal_constraints(request, company)
    constraints = request.resource_constraints
    update: dict[str, Any] = {"current_task_constraints": dict(constraints)}
    if "target_industries" in constraints:
        update["target_industries"] = structured.target_industries
    if "target_regions" in constraints:
        update["target_regions"] = structured.target_regions
    if any(key in constraints for key in ("budget_preferences", "budget_min", "budget_max")):
        update["budget_preferences"] = structured.budget_preferences
    if "available_bid_team_slots" in constraints:
        update["available_bid_team_slots"] = structured.available_bid_team_slots
    if "risk_preferences" in constraints or "risk_preference" in constraints or "avoid_contract_risks" in constraints:
        update["risk_preferences"] = structured.risk_preferences
    if "explicit_exclusions" in constraints:
        update["explicit_exclusions"] = structured.explicit_exclusions
    update["specified_preferences"] = sorted(
        set(interpreted.specified_preferences + structured.specified_preferences)
    )
    return interpreted.model_copy(update=update)


def validate_interpreted_goal(raw: Any, request, company) -> InterpretedUserGoal:
    payload = _as_payload(raw)
    ensure_no_forbidden_fields(payload)
    interpreted = InterpretedUserGoal.model_validate(payload)
    inferred = set(interpreted.specified_preferences)
    if interpreted.target_industries:
        inferred.add("industry")
    if interpreted.target_regions:
        inferred.add("region")
    if interpreted.budget_preferences.min_amount is not None or interpreted.budget_preferences.max_amount is not None:
        inferred.add("budget")
    if interpreted.explicit_exclusions:
        inferred.add("exclusion")
    if interpreted.priority_factors:
        inferred.add("priority_factor")
    interpreted = interpreted.model_copy(update={"specified_preferences": sorted(inferred)})
    return merge_structured_constraints(interpreted, request, company)


def collect_decision_evidence_ids(state) -> set[str]:
    available: set[str] = set(state["company_profile"].evidence_ids)
    evaluation = state.get("evaluation")
    if evaluation is not None:
        available.update(evaluation.evidence_ids)
    for project in state.get("projects", []):
        available.update(project.evidence_ids)
    for result in state.get("eligibility_results", []):
        available.update(result.evidence_ids)
        for item in result.item_results:
            available.update(item.evidence_ids)
    for result in state.get("competition_results", []):
        available.update(result.evidence_ids)
        for competitor in result.confirmed_competitors + result.potential_competitors:
            available.update(competitor.evidence_ids)
        for fact in result.confirmed_facts + result.company_advantages + result.company_weaknesses:
            available.update(fact.evidence_ids)
        for inference in result.inferences:
            available.update(inference.based_on_evidence_ids)
    for decision in state.get("project_decisions", []):
        available.update(decision.evidence_ids)
    return available


def validate_decision_explanation(raw: Any, state) -> DecisionExplanation:
    payload = _as_payload(raw)
    # StrictModel forbids unexpected top-level fields. Mapping keys are project IDs.
    explanation = DecisionExplanation.model_validate(payload)
    validate_evidence_ids(explanation.evidence_ids, collect_decision_evidence_ids(state))
    expected_projects = {item.project_id for item in state["project_decisions"]}
    for mapping_name in ("decision_reasons", "risk_explanations", "recommended_actions"):
        mapping = getattr(explanation, mapping_name)
        unknown = set(mapping) - expected_projects
        missing = expected_projects - set(mapping)
        if unknown:
            raise ValueError(f"{mapping_name} references unknown projects: {sorted(unknown)}")
        if missing:
            raise ValueError(f"{mapping_name} is missing projects: {sorted(missing)}")
    return explanation
