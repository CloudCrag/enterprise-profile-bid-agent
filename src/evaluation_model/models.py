"""Independent enterprise-evaluation domain models and deterministic hashing."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from hashlib import sha256
import json
from typing import Any

def canonical_json(value:Any)->bytes:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
def content_hash(value:Any)->str:return sha256(canonical_json(value)).hexdigest()

@dataclass(frozen=True)
class IndicatorScoreResult:
    indicator_code:str; indicator_name:str; selection_status:str; active:bool; acquisition_priority:int|None
    primary_code:str; secondary_code:str; legacy_weight:float; active_weight:float|None; scoring_status:str
    raw_score:float|None=None; weighted_contribution:float|None=None; rule_status:str="PENDING_RULE"; resolvability:str="FEATURE_RESOLVABLE"
    input_features:dict[str,Any]=field(default_factory=dict); evidence_ids:list[str]=field(default_factory=list); fact_ids:list[str]=field(default_factory=list)
    api_ids:list[str]=field(default_factory=list); missing_fields:list[str]=field(default_factory=list); explanation:str=""; low_score_diagnosis:str=""
    improvement_suggestion:str=""; redline_note:str=""; model_conflicts:list[dict[str,Any]]=field(default_factory=list); handler_id:str=""
    def to_dict(self)->dict[str,Any]:return asdict(self)

@dataclass(frozen=True)
class EnterpriseEvaluationRun:
    run_id:str; company_id:str; status:str; provider_id:str; started_at_utc:str; completed_at_utc:str|None; model_sha256:str
    fact_profile_version:int|None; capability_profile_version:int|None; decision_profile_version:int|None; trace:list[dict[str,Any]]
    def to_dict(self)->dict[str,Any]:return asdict(self)
