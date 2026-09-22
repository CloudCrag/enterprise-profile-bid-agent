from __future__ import annotations

from src.competition_agent.prompts import COMPETITION_SYSTEM_PROMPT
from src.competition_agent.validators import validate_competition_result
from src.competition_agent.result_builder import build_competition_result, provider_model_version
from src.competition_agent.strategy_policy import sanitize_competition_strategies
from src.shared.safe_diagnostics import safe_exception_feedback
from src.shared.schemas import (
    CompetitionSemanticOutput,
    GroundedStatement,
    InferenceStatement,
)


def build_nodes(gateways, llm_provider):
    def load_project(state):
        request = state["request"]
        project = gateways["project"].get_project(request.project_id)
        if project.project_id != request.project_id or project.project_version != request.project_version:
            raise ValueError("competition request/project version mismatch")
        return {"project": project, "current_node": "load_project"}

    def load_company_profile(state):
        request = state["request"]
        company = gateways["company"].get_company_profile(request.company_id)
        if company.company_id != request.company_id or company.profile_version != request.company_profile_version:
            raise ValueError("competition request/company version mismatch")
        return {"company_profile": company, "current_node": "load_company_profile"}

    def load_project_competition_snapshot(state):
        request = state["request"]
        snapshot = gateways["competition_data"].get_snapshot(request.project_id, request.project_version)
        return {"competition_snapshot": snapshot, "current_node": "load_project_competition_snapshot"}

    def load_historical_bidders(state):
        return {
            "historical_bidders": state["competition_snapshot"].historical_bidders,
            "current_node": "load_historical_bidders",
        }

    def load_buyer_suppliers(state):
        return {
            "buyer_suppliers": state["competition_snapshot"].buyer_suppliers,
            "current_node": "load_buyer_suppliers",
        }

    def identify_confirmed_competitors(state):
        snapshot = state["competition_snapshot"]
        seen = {}
        for competitor in snapshot.direct_participants + state["historical_bidders"]:
            seen[competitor.company_id] = competitor
        return {
            "confirmed_competitors": list(seen.values()),
            "current_node": "identify_confirmed_competitors",
        }

    def identify_potential_competitors(state):
        confirmed = {x.company_id for x in state["confirmed_competitors"]}
        seen = {}
        for competitor in state["buyer_suppliers"]:
            if competitor.company_id not in confirmed:
                seen[competitor.company_id] = competitor
        return {
            "potential_competitors": list(seen.values()),
            "current_node": "identify_potential_competitors",
        }

    def load_competitor_profiles(state):
        ids = {x.company_id for x in state["confirmed_competitors"] + state["potential_competitors"]}
        profiles = {key: value for key, value in state["competition_snapshot"].competitor_profiles.items() if key in ids}
        return {"competitor_profiles": profiles, "current_node": "load_competitor_profiles"}

    def calculate_deterministic_statistics(state):
        snapshot = state["competition_snapshot"]
        coverage = sum(1 for value in snapshot.source_coverage.values() if value) / max(len(snapshot.source_coverage), 1)
        return {
            "statistics": {
                "data_coverage": round(coverage, 4),
                "competitor_count": len(state["confirmed_competitors"]) + len(state["potential_competitors"]),
                "confirmed_count": len(state["confirmed_competitors"]),
            },
            "current_node": "calculate_deterministic_statistics",
        }

    def compare_company_and_competitors(state):
        company = state["company_profile"]
        snapshot = state["competition_snapshot"]
        project = state["project"]
        facts = []
        inferences = []
        advantages = []
        weaknesses = []
        if state["confirmed_competitors"]:
            facts.append(
                GroundedStatement(
                    statement=f"发现{len(state['confirmed_competitors'])}家有明确投标或历史同场证据的竞争者",
                    evidence_ids=state["confirmed_competitors"][0].evidence_ids,
                )
            )
        if state["potential_competitors"]:
            inferences.append(
                InferenceStatement(
                    statement=("演示候选企业可能参与本项目（不代表真实企业参与情况）" if snapshot.data_is_demo else "采购人历史供应商可能参与本项目"),
                    based_on_evidence_ids=state["potential_competitors"][0].evidence_ids,
                    confidence="MEDIUM",
                )
            )
        if project.region in company.capability_profile.regional_delivery_capability:
            advantages.append(
                GroundedStatement(
                    statement="我方具备项目所在地区交付经验",
                    evidence_ids=[company.evidence_ids[0]],
                )
            )
        if state["statistics"]["data_coverage"] < 0.6:
            weaknesses.append(
                GroundedStatement(
                    statement="竞争数据覆盖不足，无法确认完整竞争格局",
                    evidence_ids=[snapshot.evidence_ids[0]],
                )
            )
        return {
            "confirmed_facts": facts,
            "inferences": inferences,
            "unknowns": snapshot.unknowns,
            "company_advantages": advantages,
            "company_weaknesses": weaknesses,
            "current_node": "compare_company_and_competitors",
        }

    def generate_competition_summary(state):
        request = state["request"]
        snapshot = state["competition_snapshot"]
        payload = {
            "confirmed_facts": [x.model_dump(mode="json") for x in state["confirmed_facts"]],
            "inferences": [x.model_dump(mode="json") for x in state["inferences"]],
            "unknowns": list(state["unknowns"]),
            "statistics": dict(state["statistics"]),
            "company_advantages": [x.model_dump(mode="json") for x in state["company_advantages"]],
            "company_weaknesses": [x.model_dump(mode="json") for x in state["company_weaknesses"]],
            "allowed_strategy_scope": [
                "可核验差异化优势",
                "标书证据组织",
                "合规风险复核",
                "资源和交付准备",
            ],
        }
        errors: list[dict] = []
        last_error: Exception | None = None
        for _attempt in range(2):
            retry_payload = dict(payload)
            if errors:
                retry_payload["validation_feedback"] = errors[-1]
            try:
                semantic = llm_provider.generate_structured(
                    system_prompt=COMPETITION_SYSTEM_PROMPT,
                    user_payload=retry_payload,
                    output_schema=CompetitionSemanticOutput,
                )
                strategies, filtered_strategies = sanitize_competition_strategies(semantic.strategies)
                if filtered_strategies:
                    gateways["audit"].record(
                        "competition_strategy_policy_filtered",
                        {
                            "company_id": request.company_id,
                            "project_id": request.project_id,
                            "filtered_count": len(filtered_strategies),
                        },
                    )
                semantic = semantic.model_copy(update={"strategies": strategies})
                result = build_competition_result(
                    request=request,
                    project=state["project"],
                    snapshot=snapshot,
                    state=state,
                    semantic=semantic,
                    model_version=provider_model_version(llm_provider),
                )
                validate_competition_result(
                    result,
                    state["company_profile"],
                    state["project"],
                    snapshot,
                )
                return {
                    "result": result,
                    "llm_errors": errors,
                    "current_node": "generate_competition_summary",
                }
            except Exception as exc:
                last_error = exc
                errors.append(safe_exception_feedback(exc))

        gateways["audit"].record(
            "competition_llm_failed",
            {"company_id": request.company_id, "project_id": request.project_id, "errors": errors},
        )
        if last_error is not None:
            raise last_error
        raise RuntimeError("competition LLM failed without diagnostic")
    def validate_result(state):
        validate_competition_result(
            state["result"],
            state["company_profile"],
            state["project"],
            state["competition_snapshot"],
        )
        return {"current_node": "validate_competition_result"}

    def persist_result(state):
        gateways["persistence"].save_competition(state["result"])
        gateways["audit"].record(
            "competition_persisted",
            {"company_id": state["result"].company_id, "project_id": state["result"].project_id},
        )
        return {"current_node": "persist_competition_result"}

    return {
        "load_project": load_project,
        "load_company_profile": load_company_profile,
        "load_project_competition_snapshot": load_project_competition_snapshot,
        "load_historical_bidders": load_historical_bidders,
        "load_buyer_suppliers": load_buyer_suppliers,
        "identify_confirmed_competitors": identify_confirmed_competitors,
        "identify_potential_competitors": identify_potential_competitors,
        "load_competitor_profiles": load_competitor_profiles,
        "calculate_deterministic_statistics": calculate_deterministic_statistics,
        "compare_company_and_competitors": compare_company_and_competitors,
        "generate_competition_summary": generate_competition_summary,
        "validate_competition_result": validate_result,
        "persist_competition_result": persist_result,
    }
