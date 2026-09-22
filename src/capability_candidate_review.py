"""Build and validate immutable batch review records."""
from __future__ import annotations
import hashlib,json
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker
from .capability_ai.semantic_candidates import build_unique_candidate_map
from .capability_candidate_review_input import assert_valid_candidate_review_input
from .capability_validator import assert_valid_capability_profile,validate_fact_tag_pair
from .enterprise_capability_profile import canonical_json,tag_profile_content_hash,EVIDENCE_POLICY_VERSION,CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION
from .enterprise_fact_profile import fact_profile_content_hash
from .errors import InputDataError

REVIEW_SCHEMA_VERSION='enterprise-capability-candidate-review/1.0.0'
_ROOT=Path(__file__).resolve().parents[1]
_SCHEMA=json.loads((_ROOT/'schemas'/'enterprise_capability_candidate_review.schema.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(_SCHEMA); _VALIDATOR=Draft202012Validator(_SCHEMA,format_checker=FormatChecker())

def _normalized_decisions(items:list[dict[str,Any]])->list[dict[str,Any]]:
    return sorted((deepcopy(x) for x in items),key=lambda x:(x.get('candidate_id',''),x.get('decision',''),x.get('reason_code',''),x.get('reason_summary','')))

def review_content_payload(review:dict[str,Any])->dict[str,Any]:
    return {k:(_normalized_decisions(v) if k=='decisions' else deepcopy(v)) for k,v in review.items() if k not in {'review_batch_id','review_content_hash','generated_at_utc'}}

def review_content_hash(review:dict[str,Any])->str: return hashlib.sha256(canonical_json(review_content_payload(review))).hexdigest()

def stable_review_batch_id(review:dict[str,Any])->str: return f"capability-review-batch:{review_content_hash(review)[:24]}"

def _summary(total:int,decisions:list[dict[str,Any]])->dict[str,int]:
    counts={k:sum(1 for d in decisions if d.get('decision')==k) for k in ('approved','rejected','needs_more_evidence')}
    return {'total_candidate_count':total,'reviewed_candidate_count':len(decisions),'approved_candidate_count':counts['approved'],'rejected_candidate_count':counts['rejected'],'needs_more_evidence_count':counts['needs_more_evidence'],'pending_candidate_count':total-len(decisions)}

def validate_candidate_review(review:Any,*,fact_profile:dict[str,Any]|None=None,tag_profile:dict[str,Any]|None=None,capability_profile:dict[str,Any]|None=None,semantic_candidates:dict[str,Any]|None=None)->list[dict[str,Any]]:
    issues=[]
    for e in sorted(_VALIDATOR.iter_errors(review),key=lambda x:(list(x.absolute_path),x.message)):
        issues.append({'code':'candidate_review_schema_invalid','field_path':'.'.join(str(i) for i in e.absolute_path) or '$','message':e.message})
    if not isinstance(review,dict): return issues
    if review.get('review_content_hash')!=review_content_hash(review): issues.append({'code':'candidate_review_hash_mismatch','field_path':'review_content_hash','message':'Review content hash mismatch'})
    if review.get('review_batch_id')!=stable_review_batch_id(review): issues.append({'code':'candidate_review_batch_id_mismatch','field_path':'review_batch_id','message':'Review batch ID mismatch'})
    actor=review.get('review_actor')
    if not isinstance(actor,dict): issues.append({'code':'review_actor_required','field_path':'review_actor','message':'review_actor is required'})
    else:
        if actor.get('actor_type')!='human': issues.append({'code':'review_actor_type_not_supported','field_path':'review_actor.actor_type','message':'Only human actor is supported'})
        if not isinstance(actor.get('actor_id'),str) or not actor.get('actor_id').strip(): issues.append({'code':'review_actor_id_invalid','field_path':'review_actor.actor_id','message':'actor_id must be non-empty'})
    decisions=review.get('decisions') if isinstance(review.get('decisions'),list) else []
    if len({d.get('candidate_id') for d in decisions if isinstance(d,dict)})!=len(decisions): issues.append({'code':'candidate_review_duplicate_decision','field_path':'decisions','message':'Duplicate candidate decision'})
    if semantic_candidates is not None:
        try: cmap=build_unique_candidate_map(semantic_candidates)
        except Exception as exc: issues.append({'code':'candidate_review_source_artifact_invalid','field_path':'$','message':str(exc)}); return issues
        for i,d in enumerate(decisions):
            c=cmap.get(d.get('candidate_id')) if isinstance(d,dict) else None
            if c is None: issues.append({'code':'candidate_review_candidate_not_found','field_path':f'decisions.{i}.candidate_id','message':str(d.get('candidate_id'))})
            elif d.get('candidate_content_hash')!=c.get('candidate_content_hash'): issues.append({'code':'candidate_review_candidate_hash_mismatch','field_path':f'decisions.{i}.candidate_content_hash','message':'Candidate hash mismatch'})
        if review.get('review_scope')=='full' and set(cmap)!={d.get('candidate_id') for d in decisions if isinstance(d,dict)}: issues.append({'code':'candidate_review_full_scope_incomplete','field_path':'decisions','message':'Full review must cover all candidates'})
        expected=_summary(len(cmap),decisions)
        if review.get('review_summary')!=expected: issues.append({'code':'candidate_review_summary_mismatch','field_path':'review_summary','message':f'Expected {expected}'})
        deps=review.get('source_dependencies') or {}
        checks={'semantic_candidates_schema_version':semantic_candidates.get('semantic_candidates_schema_version'),'semantic_analysis_run_id':semantic_candidates.get('semantic_analysis_run_id'),'semantic_analysis_content_hash':semantic_candidates.get('semantic_analysis_content_hash'),'evidence_policy_version':(semantic_candidates.get('source_dependencies') or {}).get('evidence_policy_version')}
        for k,v in checks.items():
            if deps.get(k)!=v: issues.append({'code':'candidate_review_dependency_mismatch','field_path':f'source_dependencies.{k}','message':f'{k} mismatch'})
        if review.get('enterprise')!=semantic_candidates.get('enterprise') or review.get('as_of_date')!=semantic_candidates.get('as_of_date'): issues.append({'code':'candidate_review_context_mismatch','field_path':'enterprise','message':'Review context differs from semantic candidates'})
    if fact_profile is not None and tag_profile is not None:
        try: validate_fact_tag_pair(fact_profile,tag_profile)
        except InputDataError as exc: issues.append({'code':'candidate_review_source_pair_invalid','field_path':'source_dependencies','message':str(exc)})
        deps=review.get('source_dependencies') or {}
        for k,v in {'fact_profile_schema_version':fact_profile.get('fact_profile_schema_version'),'fact_profile_content_hash':fact_profile_content_hash(fact_profile),'tag_profile_schema_version':tag_profile.get('tag_set_schema_version'),'tag_profile_content_hash':tag_profile_content_hash(tag_profile)}.items():
            if deps.get(k)!=v: issues.append({'code':'candidate_review_dependency_mismatch','field_path':f'source_dependencies.{k}','message':f'{k} mismatch'})
    if capability_profile is not None:
        try: assert_valid_capability_profile(capability_profile,fact_profile=fact_profile,tag_profile=tag_profile)
        except Exception as exc: issues.append({'code':'candidate_review_baseline_invalid','field_path':'source_dependencies','message':str(exc)})
        if capability_profile.get('capability_profile_schema_version') not in {CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION}: issues.append({'code':'candidate_review_baseline_version_invalid','field_path':'source_dependencies.capability_profile_schema_version','message':'Review source must be a supported capability profile version'})
        deps=review.get('source_dependencies') or {}
        for k,v in {'capability_profile_schema_version':capability_profile.get('capability_profile_schema_version'),'capability_profile_id':capability_profile.get('capability_profile_id'),'capability_content_hash':capability_profile.get('capability_content_hash')}.items():
            if deps.get(k)!=v: issues.append({'code':'candidate_review_dependency_mismatch','field_path':f'source_dependencies.{k}','message':f'{k} mismatch'})
    return issues

def assert_valid_candidate_review(review:Any,**kwargs)->None:
    issues=validate_candidate_review(review,**kwargs)
    if issues: raise InputDataError(json.dumps(issues[0],ensure_ascii=False,sort_keys=True))

def build_candidate_review_batch(fact_profile:dict[str,Any],tag_profile:dict[str,Any],capability_profile:dict[str,Any],semantic_candidates:dict[str,Any],review_input:dict[str,Any],*,generated_at_utc:str|None=None)->dict[str,Any]:
    validate_fact_tag_pair(fact_profile,tag_profile)
    assert_valid_capability_profile(capability_profile,fact_profile=fact_profile,tag_profile=tag_profile)
    build_unique_candidate_map(semantic_candidates)
    assert_valid_candidate_review_input(review_input,semantic_candidates)
    deps_sc=semantic_candidates.get('source_dependencies') or {}
    if deps_sc.get('capability_profile_id')!=capability_profile.get('capability_profile_id') or deps_sc.get('capability_content_hash')!=capability_profile.get('capability_content_hash'): raise InputDataError('candidate_source_profile_dependency_mismatch')
    decisions=_normalized_decisions(review_input['decisions'])
    review={'candidate_review_schema_version':REVIEW_SCHEMA_VERSION,'enterprise':deepcopy(capability_profile['enterprise']),'as_of_date':capability_profile.get('as_of_date'),'source_dependencies':{'semantic_candidates_schema_version':semantic_candidates['semantic_candidates_schema_version'],'semantic_analysis_run_id':semantic_candidates['semantic_analysis_run_id'],'semantic_analysis_content_hash':semantic_candidates['semantic_analysis_content_hash'],'capability_profile_schema_version':capability_profile['capability_profile_schema_version'],'capability_profile_id':capability_profile['capability_profile_id'],'capability_content_hash':capability_profile['capability_content_hash'],'fact_profile_schema_version':fact_profile['fact_profile_schema_version'],'fact_profile_content_hash':fact_profile_content_hash(fact_profile),'tag_profile_schema_version':tag_profile['tag_set_schema_version'],'tag_profile_content_hash':tag_profile_content_hash(tag_profile),'evidence_policy_version':EVIDENCE_POLICY_VERSION},'review_actor':deepcopy(review_input['review_actor']),'review_scope':review_input['review_scope'],'decisions':decisions,'review_summary':_summary(len(semantic_candidates.get('capability_candidates',[])),decisions),'generated_at_utc':generated_at_utc or datetime.now(timezone.utc).isoformat()}
    review['review_content_hash']=review_content_hash(review);review['review_batch_id']=stable_review_batch_id(review)
    ordered={'candidate_review_schema_version':review['candidate_review_schema_version'],'review_batch_id':review['review_batch_id'],'review_content_hash':review['review_content_hash'],**{k:v for k,v in review.items() if k not in {'candidate_review_schema_version','review_batch_id','review_content_hash'}}}
    assert_valid_candidate_review(ordered,fact_profile=fact_profile,tag_profile=tag_profile,capability_profile=capability_profile,semantic_candidates=semantic_candidates)
    return ordered
