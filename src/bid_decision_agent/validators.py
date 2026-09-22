from __future__ import annotations

from src.shared.evidence import validate_evidence_ids
from src.shared.schemas import EligibilityStatus


def validate_decisions(decisions, company, projects, eligibility):
    project_map = {project.project_id: project for project in projects}
    eligibility_map = {result.project_id: result for result in eligibility}
    available = set(company.evidence_ids)
    for project in projects:
        available.update(project.evidence_ids)
    for result in eligibility:
        available.update(result.evidence_ids)
        for item in result.item_results:
            available.update(item.evidence_ids)

    for decision in decisions:
        if decision.project_id not in project_map or decision.project_id not in eligibility_map:
            raise ValueError("decision references unknown project")
        if decision.project_version != project_map[decision.project_id].project_version:
            raise ValueError("project version mismatch")
        if decision.eligibility != eligibility_map[decision.project_id].overall_status:
            raise ValueError("eligibility status overwritten")
        validate_evidence_ids(decision.evidence_ids, available)
        if decision.eligibility == EligibilityStatus.FAIL and (
            decision.competition_assessment is not None or decision.win_opportunity is not None
        ):
            raise ValueError("FAIL project entered competition flow")
