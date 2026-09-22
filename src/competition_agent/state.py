from __future__ import annotations
from typing import TypedDict
from src.shared.schemas import *

class CompetitionState(TypedDict, total=False):
    request: CompetitionAnalysisRequest
    project: ProjectSnapshot
    company_profile: CompanyProfileSnapshot
    competition_snapshot: CompetitionDataSnapshot
    historical_bidders: list[CompetitorRef]
    buyer_suppliers: list[CompetitorRef]
    confirmed_competitors: list[CompetitorRef]
    potential_competitors: list[CompetitorRef]
    competitor_profiles: dict[str, dict]
    statistics: dict[str, float | int]
    confirmed_facts: list[GroundedStatement]
    inferences: list[InferenceStatement]
    unknowns: list[str]
    company_advantages: list[GroundedStatement]
    company_weaknesses: list[GroundedStatement]
    result: CompetitionAnalysisResult
    current_node: str
    errors: list[AgentError]
    llm_errors: list[str]
