from __future__ import annotations

from src.shared.schemas import CompanyProfileSnapshot, EnterpriseEvaluationSnapshot


def load_required_evaluation_snapshot(
    company: CompanyProfileSnapshot,
    gateways,
) -> EnterpriseEvaluationSnapshot:
    """Load the authoritative enterprise evaluation or fail the decision task.

    The current runtime has one real data chain. Transport, schema, version and
    service errors are not converted into an empty or rules-only snapshot.
    """
    evaluation = gateways["evaluation"].get_enterprise_evaluation(
        company.company_id,
        company.profile_version,
    )
    if evaluation.company_id != company.company_id:
        raise ValueError("enterprise evaluation company mismatch")
    if evaluation.profile_version != company.profile_version:
        raise ValueError("enterprise evaluation version mismatch")
    return evaluation
