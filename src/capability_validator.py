"""Runtime validation and integrity checks for capability profile versions 1.1 and 1.2."""
from __future__ import annotations
import json
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker
from .enterprise_capability_profile import CAPABILITY_DOMAINS,CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION,DETERMINISTIC_ENGINE_VERSION,EVIDENCE_POLICY_VERSION,capability_content_hash,stable_enterprise_id,tag_profile_content_hash
from .enterprise_fact_profile import fact_profile_content_hash
from .errors import InputDataError
from .fact_validator import assert_valid_fact_profile
from .tag_profile_generator import TAG_SET_SCHEMA_VERSION
from .capability_evidence_policy import load_capability_evidence_policy,fact_allowed_for_domain

_ROOT=Path(__file__).resolve().parents[1]
def _load(name):
 s=json.loads((_ROOT/'schemas'/name).read_text(encoding='utf-8'));Draft202012Validator.check_schema(s);return Draft202012Validator(s,format_checker=FormatChecker())
_VALIDATORS={CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION:_load('enterprise_capability_profile_v1_1.schema.json'),CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION:_load('enterprise_capability_profile_v1_2.schema.json')}
_VERIFIED={'verified','partially_verified'};_BLOCKED={'expired','revoked','superseded','conflicted','ambiguous'};_CURRENT={'qualification','personnel_certificate'};_DET={'deterministic_aggregation','deterministic_rule'}

def _issue(code,path,message): return {'code':code,'field_path':path,'message':message}

def _usable(fact,as_of):
 if fact.get('verification_status') not in _VERIFIED or fact.get('fact_status') in _BLOCKED:return False
 payload=fact.get('payload') or {}
 if str(payload.get('status') or '').lower() in {'expired','revoked','invalid'}:return False
 if fact.get('fact_type') in _CURRENT and as_of:
  vu=payload.get('valid_until') or (fact.get('temporal') or {}).get('valid_until')
  if isinstance(vu,str):
   try:
    if date.fromisoformat(vu[:10])<date.fromisoformat(as_of):return False
   except ValueError:return False
 return True

def validate_fact_tag_pair(fact_profile,tag_profile):
 assert_valid_fact_profile(fact_profile)
 if not isinstance(tag_profile,dict) or tag_profile.get('tag_set_schema_version')!=TAG_SET_SCHEMA_VERSION: raise InputDataError(f'Tag input must be an {TAG_SET_SCHEMA_VERSION} object')
 if len(tag_profile.get('tags',[]))!=60: raise InputDataError('Tag profile must contain exactly 60 formal tags')
 if fact_profile.get('enterprise')!=tag_profile.get('enterprise'): raise InputDataError('Fact profile and tag profile enterprise identifiers do not match')
 if fact_profile.get('as_of_date')!=tag_profile.get('as_of_date'): raise InputDataError('Fact profile and tag profile as_of_date values do not match')
 if tag_profile.get('source_fact_profile_content_hash')!=fact_profile_content_hash(fact_profile): raise InputDataError('Tag profile was not generated from the supplied fact profile content')

def _all_items(domains):
 for di,d in enumerate(domains):
  for section in ('observations','capability_claims','reviewed_semantic_observations'):
   for ii,item in enumerate(d.get(section,[]) if isinstance(d,dict) else []):
    if isinstance(item,dict): yield di,section,ii,item

def validate_capability_profile(profile:Any,*,fact_profile=None,tag_profile=None,previous_capability_profile=None,semantic_candidates=None,candidate_review=None)->list[dict[str,Any]]:
 issues=[]
 if not isinstance(profile,dict): return [_issue('capability_profile_schema_invalid','$','Profile must be object')]
 ver=profile.get('capability_profile_schema_version');validator=_VALIDATORS.get(ver)
 if validator is None: issues.append(_issue('capability_profile_schema_version_unsupported','capability_profile_schema_version',str(ver)))
 else:
  for e in sorted(validator.iter_errors(profile),key=lambda x:(list(x.absolute_path),x.message)): issues.append(_issue('capability_profile_schema_invalid','.'.join(str(i) for i in e.absolute_path) or '$',e.message))
 domains=profile.get('capability_domains') if isinstance(profile.get('capability_domains'),list) else []
 expected=[x[0] for x in CAPABILITY_DOMAINS]
 if [d.get('capability_type') for d in domains if isinstance(d,dict)]!=expected: issues.append(_issue('capability_domain_catalog_mismatch','capability_domains','Capability domains must contain the fixed eight domains in order'))
 h=capability_content_hash(profile)
 if profile.get('capability_content_hash')!=h: issues.append(_issue('capability_content_hash_mismatch','capability_content_hash','Content hash mismatch'))
 try: pid=f"capability-profile:{stable_enterprise_id(profile.get('enterprise') or {})}:{h[:16]}"
 except InputDataError as exc: issues.append(_issue('capability_profile_id_mismatch','capability_profile_id',str(exc)))
 else:
  if profile.get('capability_profile_id')!=pid: issues.append(_issue('capability_profile_id_mismatch','capability_profile_id','Profile ID mismatch'))
 gen=profile.get('generation') or {};deps=profile.get('source_dependencies') or {}
 if gen.get('deterministic_engine_version')!=DETERMINISTIC_ENGINE_VERSION: issues.append(_issue('capability_engine_version_unsupported','generation.deterministic_engine_version','Unsupported engine'))
 if ver==CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION:
  if gen.get('generation_mode')!='deterministic_baseline' or gen.get('semantic_analysis_status')!='not_run': issues.append(_issue('capability_baseline_generation_mismatch','generation','1.1 baseline must be deterministic and not_run'))
  if 'semantic_review_summary' in profile or 'capability_gap_summary' in profile: issues.append(_issue('capability_baseline_review_summary_not_allowed','$','1.1 baseline cannot contain review fields'))
 elif ver==CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION:
  if gen.get('generation_mode')!='reviewed_capability_update' or gen.get('semantic_analysis_status') not in {'partially_reviewed','reviewed'}: issues.append(_issue('capability_review_generation_mismatch','generation','1.2 reviewed profile status invalid'))
 if any(d.get('semantic_capability_candidates') for d in domains if isinstance(d,dict)): issues.append(_issue('semantic_candidates_not_allowed','capability_domains','Formal profile cannot embed pending candidates'))
 # summary
 counts=Counter(d.get('support_status') for d in domains if isinstance(d,dict));claims=[i for _,s,_,i in _all_items(domains) if s=='capability_claims'];det=[c for c in claims if c.get('derivation_method') in _DET];sem=[c for c in claims if c.get('derivation_method')=='semantic_inference'];semobs=[i for _,s,_,i in _all_items(domains) if s=='reviewed_semantic_observations']
 summary=profile.get('capability_summary') or {}
 common={'total_domain_count':len(domains),'supported_domain_count':counts['supported'],'partially_supported_domain_count':counts['partially_supported'],'ambiguous_domain_count':counts['ambiguous'],'insufficient_data_domain_count':counts['insufficient_data'],'deterministic_claim_count':len(det),'semantic_candidate_count':0}
 expected_summary=common if ver==CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION else {**common,'reviewed_semantic_claim_count':len(sem),'reviewed_semantic_observation_count':len(semobs),'total_formal_claim_count':len(det)+len(sem)}
 if any(summary.get(k)!=v for k,v in expected_summary.items()):
  issues.append(_issue('capability_summary_mismatch','capability_summary',f'Expected {expected_summary}'))
  if summary.get('deterministic_claim_count')!=len(det): issues.append(_issue('capability_claim_count_mismatch','capability_summary.deterministic_claim_count','Deterministic claim count mismatch'))
 # lineage methods
 lineage=('source_candidate_id','source_candidate_content_hash','semantic_analysis_run_id','semantic_analysis_content_hash','review_batch_id','review_content_hash')
 for di,s,ii,item in _all_items(domains):
  base=f'capability_domains.{di}.{s}.{ii}'
  method=item.get('derivation_method')
  if s=='capability_claims' and method=='semantic_inference':
   if item.get('support_status')!='partially_supported': issues.append(_issue('semantic_claim_support_status_invalid',f'{base}.support_status','Semantic claims must be partially_supported'))
   for k in lineage:
    if not item.get(k): issues.append(_issue('semantic_claim_lineage_missing',f'{base}.{k}',f'{k} required'))
  elif s=='capability_claims' and method in _DET:
   if any(k in item for k in lineage): issues.append(_issue('deterministic_claim_lineage_not_allowed',base,'Deterministic claims cannot contain semantic review lineage'))
  if s=='reviewed_semantic_observations' and item.get('support_status')!='ambiguous': issues.append(_issue('semantic_observation_support_status_invalid',f'{base}.support_status','Reviewed semantic observations must be ambiguous'))
 # exact refs/index
 referenced={e for _,_,_,i in _all_items(domains) for e in i.get('evidence_ids',[])};idx=profile.get('evidence_index') if isinstance(profile.get('evidence_index'),dict) else {}
 if set(idx)!=referenced: issues.append(_issue('capability_evidence_index_mismatch','evidence_index','Evidence index must exactly match references'))
 if fact_profile is not None and tag_profile is not None:
  try: validate_fact_tag_pair(fact_profile,tag_profile)
  except InputDataError as exc: issues.append(_issue('capability_source_pair_invalid','source_dependencies',str(exc)));return issues
  fh=fact_profile_content_hash(fact_profile);th=tag_profile_content_hash(tag_profile)
  if deps.get('fact_profile_schema_version')!=fact_profile.get('fact_profile_schema_version'): issues.append(_issue('capability_fact_dependency_schema_mismatch','source_dependencies.fact_profile_schema_version','Fact schema mismatch'))
  if deps.get('fact_profile_content_hash')!=fh: issues.append(_issue('capability_fact_dependency_hash_mismatch','source_dependencies.fact_profile_content_hash','Fact hash mismatch'))
  if deps.get('tag_profile_schema_version')!=tag_profile.get('tag_set_schema_version'): issues.append(_issue('capability_tag_dependency_schema_mismatch','source_dependencies.tag_profile_schema_version','Tag schema mismatch'))
  if deps.get('tag_profile_content_hash')!=th: issues.append(_issue('capability_tag_dependency_hash_mismatch','source_dependencies.tag_profile_content_hash','Tag hash mismatch'))
  included=set((fact_profile.get('fact_view') or {}).get('included_fact_ids',[]));fmap={f.get('fact_id'):f for f in fact_profile.get('facts',[]) if isinstance(f,dict)};tmap={t.get('tag_code'):t for t in tag_profile.get('tags',[]) if isinstance(t,dict)};source_e=fact_profile.get('evidence_index') or {};policy=load_capability_evidence_policy()
  for di,s,ii,item in _all_items(domains):
   base=f'capability_domains.{di}.{s}.{ii}';ctype=domains[di].get('capability_type');linked=set()
   for fid in item.get('source_fact_ids',[]):
    f=fmap.get(fid)
    if f is None: issues.append(_issue('capability_fact_reference_unresolved',f'{base}.source_fact_ids',str(fid)));continue
    if fid not in included: issues.append(_issue('capability_fact_outside_active_view',f'{base}.source_fact_ids',str(fid)))
    linked.update(f.get('evidence_ids',[]))
    if s=='capability_claims' and item.get('derivation_method') in _DET and not _usable(f,profile.get('as_of_date')): issues.append(_issue('capability_claim_fact_not_verified',f'{base}.source_fact_ids',str(fid)))
    if s in {'capability_claims','reviewed_semantic_observations'} and item.get('derivation_method')=='semantic_inference' or s=='reviewed_semantic_observations':
     allowed,code=fact_allowed_for_domain(f,ctype,policy)
     if not allowed: issues.append(_issue(code or 'capability_analysis_fact_type_not_allowed',f'{base}.source_fact_ids',str(fid)))
   for code in item.get('source_tag_codes',[]):
    t=tmap.get(code)
    if t is None: issues.append(_issue('capability_tag_reference_unresolved',f'{base}.source_tag_codes',str(code)))
    else:
     linked.update(t.get('evidence_ids',[]))
     if s in {'capability_claims','reviewed_semantic_observations'} and (item.get('derivation_method')=='semantic_inference' or s=='reviewed_semantic_observations') and code not in policy['domains'][ctype]['allowed_tag_codes']: issues.append(_issue('capability_analysis_tag_not_allowed_for_domain',f'{base}.source_tag_codes',str(code)))
   for eid in item.get('evidence_ids',[]):
    if eid not in source_e: issues.append(_issue('capability_evidence_reference_unresolved',f'{base}.evidence_ids',str(eid)))
    if s in {'capability_claims','reviewed_semantic_observations'} and (item.get('derivation_method')=='semantic_inference' or s=='reviewed_semantic_observations') and eid not in linked: issues.append(_issue('capability_analysis_evidence_not_linked_to_source',f'{base}.evidence_ids',str(eid)))
 # reviewed context
 if any(x is not None for x in (previous_capability_profile,semantic_candidates,candidate_review)):
  if not all(x is not None for x in (fact_profile,tag_profile,previous_capability_profile,semantic_candidates,candidate_review)): issues.append(_issue('capability_review_validation_inputs_incomplete','$','All five source artifacts are required for reviewed profile validation'))
  else:
   from .capability_ai.semantic_candidates import validate_semantic_candidates
   from .capability_candidate_review import validate_candidate_review
   for x in validate_semantic_candidates(semantic_candidates): issues.append(_issue(x['code'],f"semantic_candidates.{x.get('field_path','$')}",x['message']))
   for x in validate_candidate_review(candidate_review,fact_profile=fact_profile,tag_profile=tag_profile,capability_profile=previous_capability_profile,semantic_candidates=semantic_candidates): issues.append(_issue(x['code'],f"candidate_review.{x.get('field_path','$')}",x['message']))
   if previous_capability_profile.get('capability_profile_schema_version') not in {CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION}: issues.append(_issue('capability_previous_profile_version_invalid','source_dependencies.previous_capability_profile_id','Previous profile version is not supported'))
   if profile.get('enterprise')!=previous_capability_profile.get('enterprise') or profile.get('as_of_date')!=previous_capability_profile.get('as_of_date'): issues.append(_issue('capability_review_previous_profile_context_mismatch','$','Context changed'))
   expected_deps={'previous_capability_profile_id':previous_capability_profile.get('capability_profile_id'),'previous_capability_content_hash':previous_capability_profile.get('capability_content_hash'),'semantic_candidates_schema_version':semantic_candidates.get('semantic_candidates_schema_version'),'semantic_analysis_run_id':semantic_candidates.get('semantic_analysis_run_id'),'semantic_analysis_content_hash':semantic_candidates.get('semantic_analysis_content_hash'),'candidate_review_schema_version':candidate_review.get('candidate_review_schema_version'),'candidate_review_content_hash':candidate_review.get('review_content_hash'),'evidence_policy_version':EVIDENCE_POLICY_VERSION}
   for k,v in expected_deps.items():
    if deps.get(k)!=v: issues.append(_issue('capability_review_dependency_mismatch',f'source_dependencies.{k}',f'{k} mismatch'))
   # deterministic preservation
   old={d['capability_type']:d for d in previous_capability_profile.get('capability_domains',[])}
   for d in domains:
    prev=old.get(d.get('capability_type'))
    if not prev: continue
    if d.get('observations')!=prev.get('observations') or d.get('unknowns')!=prev.get('unknowns') or d.get('limitations')!=prev.get('limitations'): issues.append(_issue('capability_deterministic_content_modified',f"capability_domains.{d.get('capability_type')}",'Deterministic observations/unknowns/limitations changed'))
    oldclaims=[c for c in prev.get('capability_claims',[]) if c.get('derivation_method') in _DET];newclaims=[c for c in d.get('capability_claims',[]) if c.get('derivation_method') in _DET]
    if oldclaims!=newclaims: issues.append(_issue('capability_deterministic_claim_modified',f"capability_domains.{d.get('capability_type')}.capability_claims",'Deterministic claims changed'))
   rs=profile.get('semantic_review_summary') or {};rr=candidate_review.get('review_summary') or {}
   expected_status='reviewed' if rr.get('pending_candidate_count')==0 else 'partially_reviewed'
   if gen.get('semantic_analysis_status')!=expected_status: issues.append(_issue('capability_review_status_mismatch','generation.semantic_analysis_status',f'Expected {expected_status}'))
   for k,v in {'semantic_analysis_run_id':semantic_candidates.get('semantic_analysis_run_id'),'semantic_analysis_content_hash':semantic_candidates.get('semantic_analysis_content_hash'),'review_batch_id':candidate_review.get('review_batch_id'),'review_content_hash':candidate_review.get('review_content_hash'),'review_scope':candidate_review.get('review_scope'),'total_candidate_count':rr.get('total_candidate_count'),'approved_candidate_count':rr.get('approved_candidate_count'),'rejected_candidate_count':rr.get('rejected_candidate_count'),'needs_more_evidence_count':rr.get('needs_more_evidence_count'),'pending_candidate_count':rr.get('pending_candidate_count')}.items():
    if rs.get(k)!=v: issues.append(_issue('capability_review_summary_mismatch',f'semantic_review_summary.{k}',f'Expected {v}'))
 return issues

def assert_valid_capability_profile(profile:Any,**kwargs)->None:
 issues=validate_capability_profile(profile,**kwargs)
 if issues: raise InputDataError(json.dumps(issues[0],ensure_ascii=False,sort_keys=True))
