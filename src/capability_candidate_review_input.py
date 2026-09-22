"""Validate independent batch review inputs for semantic capability candidates."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
from .capability_ai.semantic_candidates import build_unique_candidate_map
from .errors import InputDataError

REVIEW_INPUT_SCHEMA_VERSION="enterprise-capability-candidate-review-input/1.0.0"
_ROOT=Path(__file__).resolve().parents[1]
_SCHEMA=json.loads((_ROOT/'schemas'/'enterprise_capability_candidate_review_input.schema.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR=Draft202012Validator(_SCHEMA,format_checker=FormatChecker())

def _issue(code,path,message): return {'code':code,'field_path':path,'message':message}

def validate_candidate_review_input(value:Any, semantic_candidates:dict[str,Any]|None=None)->list[dict[str,Any]]:
    issues=[]
    for e in sorted(_VALIDATOR.iter_errors(value),key=lambda x:(list(x.absolute_path),x.message)):
        path='.'.join(str(i) for i in e.absolute_path) or '$'
        code='candidate_review_input_schema_invalid'
        if path=='review_actor' or path.startswith('review_actor.'):
            if 'actor_type' in path or 'human' in e.message: code='review_actor_type_not_supported'
            elif 'actor_id' in path: code='review_actor_id_invalid'
            else: code='review_actor_required'
        issues.append(_issue(code,path,e.message))
    if not isinstance(value,dict): return issues
    actor=value.get('review_actor')
    if not isinstance(actor,dict): issues.append(_issue('review_actor_required','review_actor','review_actor is required'))
    else:
        if actor.get('actor_type')!='human': issues.append(_issue('review_actor_type_not_supported','review_actor.actor_type','Only human review actors are supported'))
        if not isinstance(actor.get('actor_id'),str) or not actor.get('actor_id').strip(): issues.append(_issue('review_actor_id_invalid','review_actor.actor_id','actor_id must be a non-empty string'))
    decisions=value.get('decisions') if isinstance(value.get('decisions'),list) else []
    ids=[d.get('candidate_id') for d in decisions if isinstance(d,dict)]
    if len(ids)!=len(set(ids)): issues.append(_issue('candidate_review_duplicate_decision','decisions','The same candidate_id cannot appear twice'))
    if semantic_candidates is not None:
        try: cmap=build_unique_candidate_map(semantic_candidates)
        except Exception as exc:
            issues.append(_issue('candidate_review_source_artifact_invalid','$',str(exc))); return issues
        if value.get('semantic_analysis_run_id')!=semantic_candidates.get('semantic_analysis_run_id'):
            issues.append(_issue('candidate_review_run_id_mismatch','semantic_analysis_run_id','Review input does not belong to the supplied candidate run'))
        if value.get('semantic_analysis_content_hash')!=semantic_candidates.get('semantic_analysis_content_hash'):
            issues.append(_issue('candidate_review_candidate_artifact_hash_mismatch','semantic_analysis_content_hash','Review input candidate artifact hash mismatch'))
        for i,d in enumerate(decisions):
            if not isinstance(d,dict): continue
            c=cmap.get(d.get('candidate_id'))
            if c is None: issues.append(_issue('candidate_review_candidate_not_found',f'decisions.{i}.candidate_id',str(d.get('candidate_id'))))
            elif d.get('candidate_content_hash')!=c.get('candidate_content_hash'):
                issues.append(_issue('candidate_review_candidate_hash_mismatch',f'decisions.{i}.candidate_content_hash','candidate_content_hash differs from source candidate'))
        if value.get('review_scope')=='full' and set(ids)!=set(cmap):
            issues.append(_issue('candidate_review_full_scope_incomplete','decisions','Full review must cover every candidate exactly once'))
    return issues

def assert_valid_candidate_review_input(value:Any, semantic_candidates:dict[str,Any]|None=None)->None:
    issues=validate_candidate_review_input(value,semantic_candidates)
    if issues: raise InputDataError(json.dumps(issues[0],ensure_ascii=False,sort_keys=True))
