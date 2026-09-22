"""Shared constants and stable identifiers for enterprise capability profiles."""
from __future__ import annotations
import hashlib, json, re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable
from .errors import InputDataError

CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION = "enterprise-capability-profile/1.1.0"
CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION = "enterprise-capability-profile/1.2.0"
CAPABILITY_PROFILE_SCHEMA_VERSION = CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION
CAPABILITY_DOMAIN_CATALOG_VERSION = "enterprise-capability-domain-catalog/1.0.0"
DETERMINISTIC_ENGINE_VERSION = "capability-deterministic-v1.1"
ANALYSIS_REQUEST_SCHEMA_VERSION = "enterprise-capability-analysis-request/1.1.0"
ANALYSIS_RESPONSE_SCHEMA_VERSION = "enterprise-capability-analysis-response/1.1.0"
EVIDENCE_POLICY_VERSION = "enterprise-capability-evidence-policy/1.0.0"
CAPABILITY_DOMAINS = (
    ("industry_capability", "行业能力"),
    ("technical_capability", "技术能力"),
    ("similar_performance_capability", "同类业绩能力"),
    ("regional_delivery_capability", "地区交付能力"),
    ("amount_experience_capability", "金额承接能力"),
    ("personnel_resource_capability", "人员和资源能力"),
    ("buyer_relationship_capability", "采购人关系"),
    ("tender_performance_capability", "历史投标表现"),
)
CAPABILITY_TYPES = {x[0] for x in CAPABILITY_DOMAINS}
SUPPORT_STATUSES = {"supported","partially_supported","ambiguous","insufficient_data"}
DERIVATION_METHODS = {"deterministic_aggregation","deterministic_rule","semantic_inference"}
VERIFIED_FACT_STATUSES = {"verified","partially_verified"}
CURRENT_FACT_STATUSES = {"active","unknown"}

def load_capability_domain_catalog(path: str|Path|None=None)->dict[str,Any]:
    p=Path(path) if path else Path(__file__).resolve().parents[1]/"config"/"enterprise_capability_domains.json"
    try: data=json.loads(p.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise InputDataError(f"Cannot load capability domain catalog: {p}") from exc
    expected=[{"capability_type":k,"capability_name":n} for k,n in CAPABILITY_DOMAINS]
    if data.get("schema_version")!=CAPABILITY_DOMAIN_CATALOG_VERSION or data.get("domains")!=expected:
        raise InputDataError("Capability domain catalog must contain exactly the fixed eight V2 domains")
    return data

def canonical_json(value:Any)->bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()

def stable_enterprise_id(enterprise:dict[str,Any])->str:
    raw=enterprise.get("unified_social_credit_code") or enterprise.get("name")
    if not isinstance(raw,str) or not raw.strip(): raise InputDataError("Enterprise name or unified social credit code is required for a stable capability ID")
    return re.sub(r"[^0-9A-Za-z._-]+","-",raw.strip()).strip("-") or "enterprise"

def tag_profile_content_hash(tag_profile:dict[str,Any])->str:
    return hashlib.sha256(canonical_json(tag_profile)).hexdigest()

def stable_capability_item_id(*,enterprise:dict[str,Any],capability_type:str,capability_subject:str,capability_value:Any,source_fact_ids:Iterable[str],source_tag_codes:Iterable[str],derivation_method:str,prefix:str="capability",source_candidate_id:str|None=None,source_candidate_content_hash:str|None=None,semantic_analysis_run_id:str|None=None,semantic_analysis_content_hash:str|None=None,review_batch_id:str|None=None,review_content_hash:str|None=None)->str:
    basis={"enterprise":enterprise.get("unified_social_credit_code") or enterprise.get("name"),"capability_type":capability_type,"capability_subject":capability_subject,"capability_value":capability_value,"source_fact_ids":sorted(set(source_fact_ids)),"source_tag_codes":sorted(set(source_tag_codes)),"derivation_method":derivation_method}
    for k,v in {"source_candidate_id":source_candidate_id,"source_candidate_content_hash":source_candidate_content_hash,"semantic_analysis_run_id":semantic_analysis_run_id,"semantic_analysis_content_hash":semantic_analysis_content_hash,"review_batch_id":review_batch_id,"review_content_hash":review_content_hash}.items():
        if v is not None: basis[k]=v
    return f"{prefix}:{capability_type}:{hashlib.sha256(canonical_json(basis)).hexdigest()[:24]}"

def capability_content_payload(profile:dict[str,Any])->dict[str,Any]:
    keys=["capability_profile_schema_version","enterprise","as_of_date","fact_view_mode","source_dependencies","generation","capability_domains","capability_summary","semantic_review_summary","capability_gap_summary","evidence_index","warnings","data_quality_summary"]
    return {k:deepcopy(profile.get(k)) for k in keys if k in profile}

def capability_content_hash(profile:dict[str,Any])->str:
    return hashlib.sha256(canonical_json(capability_content_payload(profile))).hexdigest()

def finalize_capability_profile(profile:dict[str,Any],*,generated_at_utc:str)->dict[str,Any]:
    result=deepcopy(profile); digest=capability_content_hash(result)
    result["capability_content_hash"]=digest
    result["capability_profile_id"]=f"capability-profile:{stable_enterprise_id(result['enterprise'])}:{digest[:16]}"
    result["generated_at_utc"]=generated_at_utc
    return result
