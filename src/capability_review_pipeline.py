"""Merge a validated semantic-candidate review batch into capability profile 1.2.0."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
from typing import Any
from .capability_ai.semantic_candidates import assert_valid_semantic_candidates,build_unique_candidate_map
from .capability_candidate_review import assert_valid_candidate_review,REVIEW_SCHEMA_VERSION
from .capability_validator import assert_valid_capability_profile,validate_fact_tag_pair
from .capability_evidence_policy import load_capability_evidence_policy,fact_allowed_for_domain
from .enterprise_capability_profile import CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION,DETERMINISTIC_ENGINE_VERSION,EVIDENCE_POLICY_VERSION,finalize_capability_profile,stable_capability_item_id,canonical_json
from .errors import InputDataError

_RANK={'insufficient_data':0,'ambiguous':1,'partially_supported':2,'supported':3}
_DET={'deterministic_aggregation','deterministic_rule'}

def _decision_map(r): return {d['candidate_id']:d for d in r.get('decisions',[]) if isinstance(d,dict)}
def _claim_key(c): return (c.get('capability_type'),c.get('capability_subject'),canonical_json(c.get('capability_value')),tuple(sorted(c.get('source_fact_ids',[]))),tuple(sorted(c.get('source_tag_codes',[]))))
def _subject_key(c): return (c.get('capability_type'),c.get('capability_subject'))
def _candidate_claim_key(c): return (c.get('capability_type'),c.get('candidate_subject'),canonical_json(c.get('candidate_value')),tuple(sorted(c.get('source_fact_ids',[]))),tuple(sorted(c.get('source_tag_codes',[]))))
def _candidate_subject_key(c): return (c.get('capability_type'),c.get('candidate_subject'))

def _lineage(cand,candidates,review):
 return {'source_candidate_id':cand['candidate_id'],'source_candidate_content_hash':cand['candidate_content_hash'],'semantic_analysis_run_id':candidates['semantic_analysis_run_id'],'semantic_analysis_content_hash':candidates['semantic_analysis_content_hash'],'review_batch_id':review['review_batch_id'],'review_content_hash':review['review_content_hash']}

def _semantic_claim(enterprise,as_of,cand,candidates,review):
 lin=_lineage(cand,candidates,review)
 cid=stable_capability_item_id(enterprise=enterprise,capability_type=cand['capability_type'],capability_subject=cand['candidate_subject'],capability_value=cand['candidate_value'],source_fact_ids=cand['source_fact_ids'],source_tag_codes=cand['source_tag_codes'],derivation_method='semantic_inference',**lin)
 return {'capability_id':cid,'capability_type':cand['capability_type'],'capability_subject':cand['candidate_subject'],'capability_value':deepcopy(cand['candidate_value']),'support_status':'partially_supported','derivation_method':'semantic_inference','basis_summary':cand['inference_summary'],'source_fact_ids':deepcopy(cand['source_fact_ids']),'source_tag_codes':deepcopy(cand['source_tag_codes']),'evidence_ids':deepcopy(cand['evidence_ids']),'limitations':deepcopy(cand['limitations']),'time_scope':{'as_of_date':as_of},**lin}

def _semantic_observation(enterprise,cand,candidates,review):
 lin=_lineage(cand,candidates,review)
 oid=stable_capability_item_id(enterprise=enterprise,capability_type=cand['capability_type'],capability_subject=cand['candidate_subject'],capability_value=cand['candidate_value'],source_fact_ids=cand['source_fact_ids'],source_tag_codes=cand['source_tag_codes'],derivation_method='semantic_inference',prefix='semantic-observation',**lin)
 return {'semantic_observation_id':oid,'capability_type':cand['capability_type'],'candidate_kind':cand['candidate_kind'],'observation_subject':cand['candidate_subject'],'observation_value':deepcopy(cand['candidate_value']),'inference_summary':cand['inference_summary'],'support_status':'ambiguous','source_fact_ids':deepcopy(cand['source_fact_ids']),'source_tag_codes':deepcopy(cand['source_tag_codes']),'evidence_ids':deepcopy(cand['evidence_ids']),'limitations':deepcopy(cand['limitations']),'unknowns':deepcopy(cand['unknowns']),**lin}

def _recompute_refs(domain):
 items=[]
 for sec in ('observations','capability_claims','reviewed_semantic_observations'): items.extend(x for x in domain.get(sec,[]) if isinstance(x,dict))
 domain['source_fact_ids']=list(dict.fromkeys(fid for x in items for fid in x.get('source_fact_ids',[])))
 domain['source_tag_codes']=list(dict.fromkeys(code for x in items for code in x.get('source_tag_codes',[])))
 domain['evidence_ids']=list(dict.fromkeys(eid for x in items for eid in x.get('evidence_ids',[])))

def _revalidate_candidate(cand,fact_profile,tag_profile):
 included=set((fact_profile.get('fact_view') or {}).get('included_fact_ids',[]));fmap={f.get('fact_id'):f for f in fact_profile.get('facts',[]) if isinstance(f,dict)};tmap={t.get('tag_code'):t for t in tag_profile.get('tags',[]) if isinstance(t,dict)};eidx=fact_profile.get('evidence_index') or {};policy=load_capability_evidence_policy();ctype=cand['capability_type'];linked=set()
 if not cand.get('source_fact_ids'): raise InputDataError('semantic_candidate_fact_reference_required')
 for fid in cand.get('source_fact_ids',[]):
  f=fmap.get(fid)
  if f is None: raise InputDataError('semantic_candidate_fact_reference_not_found')
  if fid not in included: raise InputDataError('semantic_candidate_fact_outside_fact_view')
  allowed,code=fact_allowed_for_domain(f,ctype,policy)
  if not allowed: raise InputDataError(code or 'capability_analysis_fact_type_not_allowed')
  linked.update(f.get('evidence_ids',[]))
 for code in cand.get('source_tag_codes',[]):
  t=tmap.get(code)
  if t is None: raise InputDataError('semantic_candidate_tag_reference_not_found')
  if code not in policy['domains'][ctype]['allowed_tag_codes']: raise InputDataError('capability_analysis_tag_not_allowed_for_domain')
  linked.update(t.get('evidence_ids',[]))
 for eid in cand.get('evidence_ids',[]):
  if eid not in eidx: raise InputDataError('semantic_candidate_evidence_reference_not_found')
  if eid not in linked: raise InputDataError('capability_analysis_evidence_not_linked_to_source')

def build_reviewed_capability_profile(fact_profile:dict[str,Any],tag_profile:dict[str,Any],previous_profile:dict[str,Any],semantic_candidates:dict[str,Any],review:dict[str,Any],*,generated_at_utc:str|None=None)->dict[str,Any]:
 validate_fact_tag_pair(fact_profile,tag_profile)
 assert_valid_capability_profile(previous_profile,fact_profile=fact_profile,tag_profile=tag_profile)
 if previous_profile.get('capability_profile_schema_version') not in {CAPABILITY_PROFILE_BASELINE_SCHEMA_VERSION,CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION}: raise InputDataError('previous_capability_profile_version_not_supported')
 assert_valid_semantic_candidates(semantic_candidates)
 assert_valid_candidate_review(review,fact_profile=fact_profile,tag_profile=tag_profile,capability_profile=previous_profile,semantic_candidates=semantic_candidates)
 deps=semantic_candidates.get('source_dependencies') or {}
 if deps.get('capability_profile_id')!=previous_profile.get('capability_profile_id') or deps.get('capability_content_hash')!=previous_profile.get('capability_content_hash'): raise InputDataError('candidate_source_profile_dependency_mismatch')
 cmap=build_unique_candidate_map(semantic_candidates);dmap=_decision_map(review)
 result=deepcopy(previous_profile);result['capability_profile_schema_version']=CAPABILITY_PROFILE_REVIEWED_SCHEMA_VERSION
 for d in result['capability_domains']: d.setdefault('reviewed_semantic_observations',[])
 outcomes=[];approved_claims=0;approved_obs=0;gaps=[]
 existing_claims=[c for d in result['capability_domains'] for c in d.get('capability_claims',[])];det_keys={_claim_key(c) for c in existing_claims if c.get('derivation_method') in _DET};all_claim_keys={_claim_key(c) for c in existing_claims};subject_values={_subject_key(c):canonical_json(c.get('capability_value')) for c in existing_claims}
 for cid,decision in sorted(dmap.items()):
  cand=cmap[cid];_revalidate_candidate(cand,fact_profile,tag_profile)
  action=decision['decision']
  if action=='rejected': outcomes.append({'candidate_id':cid,'outcome_code':'rejected'});continue
  if action=='needs_more_evidence':
   outcomes.append({'candidate_id':cid,'outcome_code':'needs_more_evidence'});gaps.append({'candidate_id':cid,'capability_type':cand['capability_type'],'reason_code':decision['reason_code'],'reason_summary':decision['reason_summary']});continue
  domain=next((d for d in result['capability_domains'] if d['capability_type']==cand['capability_type']),None)
  if domain is None: raise InputDataError('semantic_candidate_domain_not_found')
  if cand['support_status']=='ambiguous':
   obs=_semantic_observation(result['enterprise'],cand,semantic_candidates,review)
   if any(o.get('semantic_observation_id')==obs['semantic_observation_id'] for o in domain['reviewed_semantic_observations']): outcomes.append({'candidate_id':cid,'outcome_code':'duplicate_semantic_observation_skipped'})
   else: domain['reviewed_semantic_observations'].append(obs);approved_obs+=1;outcomes.append({'candidate_id':cid,'outcome_code':'approved_semantic_observation'});domain['support_status']='ambiguous' if _RANK[domain['support_status']]<_RANK['ambiguous'] else domain['support_status']
   continue
  key=_candidate_claim_key(cand);skey=_candidate_subject_key(cand)
  if key in det_keys: outcomes.append({'candidate_id':cid,'outcome_code':'semantic_duplicate_of_deterministic_claim'});continue
  if key in all_claim_keys: outcomes.append({'candidate_id':cid,'outcome_code':'duplicate_semantic_claim_skipped'});continue
  if skey in subject_values and subject_values[skey]!=canonical_json(cand.get('candidate_value')): raise InputDataError('semantic_candidate_conflicts_with_existing_claim')
  claim=_semantic_claim(result['enterprise'],result.get('as_of_date'),cand,semantic_candidates,review)
  domain['capability_claims'].append(claim);all_claim_keys.add(key);subject_values[skey]=canonical_json(cand.get('candidate_value'));approved_claims+=1;outcomes.append({'candidate_id':cid,'outcome_code':'approved_semantic_claim'});domain['support_status']='partially_supported' if _RANK[domain['support_status']]<_RANK['partially_supported'] else domain['support_status']
 for d in result['capability_domains']:_recompute_refs(d)
 rr=review['review_summary'];status='reviewed' if rr['pending_candidate_count']==0 else 'partially_reviewed'
 result['source_dependencies']={'fact_profile_schema_version':fact_profile['fact_profile_schema_version'],'fact_profile_content_hash':previous_profile['source_dependencies']['fact_profile_content_hash'],'tag_profile_schema_version':tag_profile['tag_set_schema_version'],'tag_profile_content_hash':previous_profile['source_dependencies']['tag_profile_content_hash'],'previous_capability_profile_id':previous_profile['capability_profile_id'],'previous_capability_content_hash':previous_profile['capability_content_hash'],'semantic_candidates_schema_version':semantic_candidates['semantic_candidates_schema_version'],'semantic_analysis_run_id':semantic_candidates['semantic_analysis_run_id'],'semantic_analysis_content_hash':semantic_candidates['semantic_analysis_content_hash'],'candidate_review_schema_version':REVIEW_SCHEMA_VERSION,'candidate_review_content_hash':review['review_content_hash'],'evidence_policy_version':EVIDENCE_POLICY_VERSION}
 result['generation']={'generation_mode':'reviewed_capability_update','deterministic_engine_version':DETERMINISTIC_ENGINE_VERSION,'semantic_analysis_status':status}
 result['semantic_review_summary']={'semantic_analysis_run_id':semantic_candidates['semantic_analysis_run_id'],'semantic_analysis_content_hash':semantic_candidates['semantic_analysis_content_hash'],'review_batch_id':review['review_batch_id'],'review_content_hash':review['review_content_hash'],'review_scope':review['review_scope'],'total_candidate_count':rr['total_candidate_count'],'approved_candidate_count':rr['approved_candidate_count'],'approved_claim_count':approved_claims,'approved_observation_count':approved_obs,'rejected_candidate_count':rr['rejected_candidate_count'],'needs_more_evidence_count':rr['needs_more_evidence_count'],'pending_candidate_count':rr['pending_candidate_count'],'merge_outcomes':outcomes}
 result['capability_gap_summary']={'needs_more_evidence_count':len(gaps),'items':gaps}
 domains=result['capability_domains'];counts=Counter(d['support_status'] for d in domains);claims=[c for d in domains for c in d['capability_claims']];det=[c for c in claims if c.get('derivation_method') in _DET];sem=[c for c in claims if c.get('derivation_method')=='semantic_inference'];obs=[o for d in domains for o in d['reviewed_semantic_observations']]
 result['capability_summary']={'total_domain_count':8,'supported_domain_count':counts['supported'],'partially_supported_domain_count':counts['partially_supported'],'ambiguous_domain_count':counts['ambiguous'],'insufficient_data_domain_count':counts['insufficient_data'],'deterministic_claim_count':len(det),'reviewed_semantic_claim_count':len(sem),'reviewed_semantic_observation_count':len(obs),'semantic_candidate_count':0,'total_formal_claim_count':len(det)+len(sem)}
 referenced={e for d in domains for sec in ('observations','capability_claims','reviewed_semantic_observations') for x in d.get(sec,[]) for e in x.get('evidence_ids',[])};source=fact_profile.get('evidence_index') or {};result['evidence_index']={e:deepcopy(source[e]) for e in sorted(referenced) if e in source}
 result['data_quality_summary']={**deepcopy(previous_profile.get('data_quality_summary') or {}),'semantic_review_integrity_validated':True,'deterministic_content_preserved':True,'review_batch_actor_validated':True,'reviewed_semantic_claim_count':len(sem),'reviewed_semantic_observation_count':len(obs)}
 for k in ('capability_profile_id','capability_content_hash','generated_at_utc'): result.pop(k,None)
 final=finalize_capability_profile(result,generated_at_utc=generated_at_utc or datetime.now(timezone.utc).isoformat())
 assert_valid_capability_profile(final,fact_profile=fact_profile,tag_profile=tag_profile,previous_capability_profile=previous_profile,semantic_candidates=semantic_candidates,candidate_review=review)
 return final
