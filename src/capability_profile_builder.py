"""Build the versioned deterministic enterprise capability baseline (1.1.0)."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from .capability_validator import assert_valid_capability_profile, validate_fact_tag_pair
from .deterministic_capability_engine import build_deterministic_domains
from .enterprise_capability_profile import CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,DETERMINISTIC_ENGINE_VERSION,finalize_capability_profile,load_capability_domain_catalog,tag_profile_content_hash
from .enterprise_fact_profile import fact_profile_content_hash

def build_capability_profile(fact_profile:dict[str,Any],tag_profile:dict[str,Any],*,generated_at_utc:str|None=None)->dict[str,Any]:
    validate_fact_tag_pair(fact_profile,tag_profile); load_capability_domain_catalog()
    domains=build_deterministic_domains(fact_profile,tag_profile)
    counts=Counter(d["support_status"] for d in domains)
    claims=[c for d in domains for c in d["capability_claims"]]
    evidence_ids=sorted({e for d in domains for e in d["evidence_ids"]})
    source_index=fact_profile.get("evidence_index") or {}
    included=set((fact_profile.get("fact_view") or {}).get("included_fact_ids",[]))
    referenced={f for d in domains for f in d["source_fact_ids"]}
    profile={
      "capability_profile_schema_version":CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,
      "enterprise":deepcopy(fact_profile["enterprise"]),"as_of_date":fact_profile.get("as_of_date"),"fact_view_mode":(fact_profile.get("fact_view") or {}).get("view_mode"),
      "source_dependencies":{"fact_profile_schema_version":fact_profile.get("fact_profile_schema_version"),"fact_profile_content_hash":fact_profile_content_hash(fact_profile),"tag_profile_schema_version":tag_profile.get("tag_set_schema_version"),"tag_profile_content_hash":tag_profile_content_hash(tag_profile)},
      "generation":{"generation_mode":"deterministic_baseline","deterministic_engine_version":DETERMINISTIC_ENGINE_VERSION,"semantic_analysis_status":"not_run"},
      "capability_domains":domains,
      "capability_summary":{"total_domain_count":8,"supported_domain_count":counts["supported"],"partially_supported_domain_count":counts["partially_supported"],"ambiguous_domain_count":counts["ambiguous"],"insufficient_data_domain_count":counts["insufficient_data"],"deterministic_claim_count":len(claims),"semantic_candidate_count":0},
      "evidence_index":{e:deepcopy(source_index[e]) for e in evidence_ids if e in source_index},
      "warnings":deepcopy(fact_profile.get("warnings",[]))+deepcopy(tag_profile.get("warnings",[])),
      "data_quality_summary":{"all_fact_references_resolved":referenced.issubset(included),"referenced_fact_count":len(referenced),"all_tag_references_resolved":all(c in {t["tag_code"] for t in tag_profile["tags"]} for d in domains for c in d["source_tag_codes"]),"all_evidence_references_resolved":all(e in source_index for e in evidence_ids),"referenced_evidence_count":len(evidence_ids),"fact_view_included_fact_count":len(included),"contains_mock_data":bool((fact_profile.get("data_quality_summary") or {}).get("contains_mock_data")),"contains_anonymized_data":bool((fact_profile.get("data_quality_summary") or {}).get("contains_anonymized_data")),"threshold_free_deterministic_baseline":True},
    }
    result=finalize_capability_profile(profile,generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat())
    assert_valid_capability_profile(result,fact_profile=fact_profile,tag_profile=tag_profile)
    return result
