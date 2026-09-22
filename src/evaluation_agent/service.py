from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Any

from src.evaluation_engine import EnterpriseEvaluationEngine
from src.evaluation_features import EvaluationFeatureEngine
from src.evaluation_model import EvaluationModelCatalog, content_hash
from .graph import EvaluationWorkflowState, build_checkpointed_state_graph, LANGGRAPH_BACKEND
from .normalizers import ApiNormalizerRegistry
from .providers import ProviderRegistry

ROOT=Path(__file__).resolve().parents[2]

def utc_now()->str:return datetime.now(timezone.utc).isoformat()
def stable_id(prefix:str,value:Any)->str:return f"{prefix}:{content_hash(value)[:24]}"

DATA_PLAN_RESOLVABILITY={"DATA_RESOLVABLE","PROVIDER_RESOLVABLE"}
WAITING_STATUSES={"WAITING_REVIEW","WAITING_DATA_PROVIDER","WAITING_USER_INPUT","PROVIDER_NOT_CONFIGURED"}

class EnterpriseEvaluationAgentService:
    """Checkpointed PROFILE_EVALUATION StateGraph with pluggable providers."""
    def __init__(self,repository:Any,*,fact_candidate_submitter:Any|None=None,provider_registry:ProviderRegistry|None=None)->None:
        self.repository=repository; self.fact_candidate_submitter=fact_candidate_submitter
        self.catalog=EvaluationModelCatalog(); self.feature_engine=EvaluationFeatureEngine(); self.engine=EnterpriseEvaluationEngine(self.catalog)
        self.providers=provider_registry or ProviderRegistry(repository); self.normalizers=ApiNormalizerRegistry(self.catalog.api_catalog)
        self.ttl_policy=json.loads((ROOT/"config"/"provider_api_ttl_policy.json").read_text(encoding="utf-8"))
        self.trust_policy=json.loads((ROOT/"config"/"source_trust_policy.json").read_text(encoding="utf-8"))
        self.graph=self._build_graph()

    def model(self)->dict[str,Any]:return self.catalog.public_model()
    def indicators(self)->list[dict[str,Any]]:
        coverage={i["indicator_code"]:i for i in self.engine.registry.coverage()}
        return [{**i,**coverage[i["indicator_code"]]} for i in self.catalog.indicators()]
    def notes(self)->dict[str,Any]:return deepcopy(self.catalog.notes)
    def api_catalog(self)->dict[str,Any]:return deepcopy(self.catalog.api_catalog)
    def provider_descriptors(self)->list[dict[str,Any]]:return self.providers.list()
    def normalizer_descriptors(self)->list[dict[str,Any]]:return self.normalizers.describe()
    def graph_description(self)->dict[str,Any]:return {**self.graph.describe(),"langgraph_import_status":LANGGRAPH_BACKEND}

    @staticmethod
    def _append_trace(state:EvaluationWorkflowState,node:str,details:dict[str,Any]|None=None)->None:
        state.setdefault("trace",[]).append({"node":node,"occurred_at_utc":utc_now(),"details":details or {}})

    def _checkpoint(self,state:EvaluationWorkflowState)->None:
        self.repository.save_evaluation_checkpoint(state["run_id"],state)
        run=self._run_from_state(state)
        self.repository.save_evaluation_run(run)

    def _run_from_state(self,state:EvaluationWorkflowState)->dict[str,Any]:
        status=state.get("workflow_state","RUNNING")
        run={
            "evaluation_run_schema_version":"enterprise-evaluation-run/2.0.0","run_id":state["run_id"],"company_id":state["company_id"],"agent_mode":"PROFILE_EVALUATION","workflow_mode":state.get("workflow_mode","legacy"),
            "status":status,"workflow_state":status,"current_node":state.get("current_node"),"next_node":state.get("next_node"),"provider_id":state.get("provider_id"),
            "provider_mode":state.get("provider_id"),"provider_health":deepcopy(state.get("provider_health") or {}),"model_sha256":self.catalog.source_sha256,
            "active_indicator_codes":deepcopy(state.get("active_indicator_codes") or []),"core_indicator_codes":deepcopy(state.get("core_indicator_codes") or []),"excluded_indicator_codes":deepcopy(state.get("excluded_indicator_codes") or []),
            "dependencies":deepcopy(state.get("dependencies") or {}),"acquisition_plan_id":(state.get("acquisition_plan") or {}).get("plan_id"),
            "api_call_ids":[r.get("api_call_id") for r in state.get("api_call_records") or []],"review_task_ids":deepcopy(state.get("review_task_ids") or []),
            "evaluation_profile_hash":(state.get("profile") or {}).get("content_hash"),"evaluation_card_hash":(state.get("card") or {}).get("content_hash"),
            "evaluation_profile_version":state.get("evaluation_profile_version"),"started_at_utc":state.get("started_at_utc"),
            "completed_at_utc":utc_now() if status in {"COMPLETED","COMPLETED_WITH_PARTIAL_COVERAGE","MODEL_POLICY_PENDING","PROVIDER_NOT_CONFIGURED"} else None,
            "trace":deepcopy(state.get("trace") or []),"termination_reason":state.get("termination_reason"),"checkpoint_version":state.get("checkpoint_version",0),
            "graph_backend":self.graph.backend,"round_index":state.get("round_index",0),"max_rounds":state.get("max_rounds",3),"waiting_for":deepcopy(state.get("waiting_for") or []),"no_change_count":state.get("no_change_count",0),"provider_no_change_count":state.get("provider_no_change_count",0),
        }
        run["content_hash"]=content_hash({k:v for k,v in run.items() if k not in {"started_at_utc","completed_at_utc","trace","content_hash"}})
        return run

    def _build_graph(self):
        nodes={name:getattr(self,f"_node_{name}") for name in [
            "load_company_identity","load_current_profiles","load_evaluation_model","resolve_active_indicator_set","derive_features","evaluate_active_indicators",
            "classify_evaluation_gaps","build_data_acquisition_plan","call_enterprise_data_provider","normalize_provider_results","ingest_facts_or_create_reviews",
            "wait_for_review","refresh_profiles_after_review","rederive_features","recalculate_active_indicators","aggregate_secondary_dimensions","aggregate_primary_dimensions",
            "build_evaluation_profile","build_evaluation_card","finalize"]}
        def after_provider(s:EvaluationWorkflowState)->str:
            if (s.get("provider_health") or {}).get("status") != "UP" or not (s.get("acquisition_plan") or {}).get("planned_calls"):
                return "aggregate_secondary_dimensions"
            records = s.get("api_call_records") or []
            if records and not any(item.get("status") == "OK" for item in records):
                s["provider_failure_status"] = "WAITING_DATA_PROVIDER"
                return "aggregate_secondary_dimensions"
            return "normalize_provider_results"
        def after_ingest(s:EvaluationWorkflowState)->str:return "wait_for_review" if s.get("review_task_ids") else "refresh_profiles_after_review"
        edges={
            "load_company_identity":"load_current_profiles","load_current_profiles":"load_evaluation_model","load_evaluation_model":"resolve_active_indicator_set","resolve_active_indicator_set":"derive_features",
            "derive_features":"evaluate_active_indicators","evaluate_active_indicators":"classify_evaluation_gaps","classify_evaluation_gaps":"build_data_acquisition_plan","build_data_acquisition_plan":"call_enterprise_data_provider",
            "call_enterprise_data_provider":after_provider,"normalize_provider_results":"ingest_facts_or_create_reviews","ingest_facts_or_create_reviews":after_ingest,"wait_for_review":None,
            "refresh_profiles_after_review":"rederive_features","rederive_features":"recalculate_active_indicators","recalculate_active_indicators":"aggregate_secondary_dimensions",
            "aggregate_secondary_dimensions":"aggregate_primary_dimensions","aggregate_primary_dimensions":"build_evaluation_profile","build_evaluation_profile":"build_evaluation_card","build_evaluation_card":"finalize","finalize":None,
        }
        return build_checkpointed_state_graph(nodes,edges,entry="load_company_identity",checkpoint_saver=self._checkpoint)

    def _node_load_company_identity(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        context=self.repository.read_company_context(s["company_id"]); e=context["fact_profile"]["enterprise"]
        identity={"company_id":s["company_id"],"external_company_id":s["company_id"],"company_name":e.get("name"),"unified_social_credit_code":e.get("unified_social_credit_code"),"provider_id":s["provider_id"],"identity_status":"LOCAL_CONTEXT"}
        s["company_identity"]=identity; self._append_trace(s,"load_company_identity",{"provider_id":s["provider_id"]}); return s
    def _node_load_current_profiles(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        c=self.repository.read_company_context(s["company_id"]); versions=deepcopy(c["metadata"].get("current_versions") or {})
        s["context"]=c; s["current_fact_profile_ref"]={"version":versions.get("fact"),"content_hash":(c["fact_profile"].get("fact_summary") or {}).get("fact_profile_content_hash")}; s["current_tag_profile_ref"]={"version":versions.get("tag")}; s["current_capability_profile_ref"]={"version":versions.get("capability"),"content_hash":c["capability_profile"].get("capability_content_hash")}
        s["dependencies"]={"fact_profile_version":versions.get("fact"),"capability_profile_version":versions.get("capability"),"decision_profile_version":versions.get("decision"),"fact_profile_hash":s["current_fact_profile_ref"]["content_hash"],"capability_profile_hash":s["current_capability_profile_ref"]["content_hash"],"decision_profile_hash":(c.get("decision_profile") or {}).get("decision_profile_content_hash"),"capability_profile_summary":deepcopy(c["capability_profile"].get("capability_domains")),"data_origin":c["metadata"].get("data_origin")}
        self._append_trace(s,"load_current_profiles",{"versions":versions}); return s
    def _node_load_evaluation_model(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["evaluation_model_version"]=self.catalog.source_sha256; self._append_trace(s,"load_evaluation_model",{"model_sha256":self.catalog.source_sha256}); return s
    def _node_resolve_active_indicator_set(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        s["active_indicator_codes"]=[i["indicator_code"] for i in self.catalog.active_indicators()]; s["core_indicator_codes"]=[i["indicator_code"] for i in self.catalog.core_indicators()]; s["excluded_indicator_codes"]=[i["indicator_code"] for i in self.catalog.excluded_indicators()]
        self._append_trace(s,"resolve_active_indicator_set",{"active":len(s["active_indicator_codes"]),"core":len(s["core_indicator_codes"]),"excluded":len(s["excluded_indicator_codes"])}); return s
    def _node_derive_features(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["feature_snapshot"]=self.feature_engine.derive(s["context"]["fact_profile"]); self._append_trace(s,"derive_features",{"version":self.feature_engine.version}); return s
    def _node_evaluate_active_indicators(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        profile,card=self.engine.evaluate(company_id=s["company_id"],feature_bundle=s["feature_snapshot"],dependencies=s["dependencies"]); s["profile"]=profile; s["card"]=card; s["indicator_results"]=profile["indicator_results"]
        self._append_trace(s,"evaluate_active_indicators",{"scored":profile["summary"]["scored_indicator_count"]}); return s
    def _node_classify_evaluation_gaps(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        groups:dict[str,list[dict[str,Any]]]={}
        for item in s["profile"]["data_gaps"]:groups.setdefault(item["resolvability"],[]).append(item)
        s["gap_inventory"]={"company_id":s["company_id"],"active_indicator_count":44,"groups":groups,"item_count":sum(len(v) for v in groups.values())}
        self._append_trace(s,"classify_evaluation_gaps",{"groups":{k:len(v) for k,v in groups.items()}}); return s
    def _build_plan(self,s:EvaluationWorkflowState)->dict[str,Any]:
        by_api={}
        for item in s["profile"]["data_gaps"]:
            if item["resolvability"] not in DATA_PLAN_RESOLVABILITY:continue
            full=next(r for r in s["indicator_results"] if r["indicator_code"]==item["indicator_code"])
            if full["rule_status"] in {"CALIBRATION_REQUIRED","MODEL_CONTENT_CONFLICT","EXCLUDED_BY_MODEL_SELECTION","DETERMINISTIC_NEEDS_FEATURE"}:continue
            for api_id in item.get("api_ids") or []:
                call=by_api.setdefault(api_id,{"api_id":api_id,"indicator_codes":[],"missing_fields":[],"priority":full.get("acquisition_priority") or 2})
                call["indicator_codes"].append(item["indicator_code"]); call["missing_fields"].extend(item.get("missing_fields") or []); call["priority"]=min(call["priority"],full.get("acquisition_priority") or 2)
        calls=[]
        for api_id,item in sorted(by_api.items(),key=lambda p:(p[1]["priority"],-len(set(p[1]["indicator_codes"])),str(p[0])))[:int(s.get("max_api_calls",10))]:
            calls.append({**item,"indicator_codes":sorted(set(item["indicator_codes"])),"missing_fields":sorted(set(item["missing_fields"])),"call_status":"PLANNED"})
        return {"acquisition_plan_schema_version":"enterprise-data-acquisition-plan/2.0.0","plan_id":stable_id("evaluation-acquisition-plan",{"run_id":s["run_id"],"calls":calls}),"run_id":s["run_id"],"company_id":s["company_id"],"provider_id":s["provider_id"],"deduplication_key":content_hash(calls),"max_api_calls":s.get("max_api_calls",10),"planned_calls":calls,"created_at_utc":utc_now()}
    def _node_build_data_acquisition_plan(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        plan=self._build_plan(s); old=s.get("last_plan_hash"); s["acquisition_plan"]=plan; s["no_change_count"]=int(s.get("no_change_count",0))+1 if old==plan["deduplication_key"] else 0; s["last_plan_hash"]=plan["deduplication_key"]; self.repository.save_acquisition_plan(plan)
        self._append_trace(s,"build_data_acquisition_plan",{"calls":len(plan["planned_calls"]),"no_change_count":s["no_change_count"]}); return s
    def _cache_key(self,s:EvaluationWorkflowState,api_id:str,params:dict[str,Any])->str:
        provider=self.providers.get(s["provider_id"]); identity={"external_company_id":s["company_identity"].get("external_company_id"),"unified_social_credit_code":s["company_identity"].get("unified_social_credit_code")}
        return content_hash([provider.provider_id,provider.provider_version,identity,str(api_id),params])
    def _valid_until(self,provider_id:str,called_at:str)->str|None:
        ttl=((self.ttl_policy.get("policies") or {}).get(provider_id) or {}).get("default_ttl_seconds")
        if not isinstance(ttl,(int,float)):return None
        return (datetime.fromisoformat(called_at)+timedelta(seconds=ttl)).isoformat()
    @staticmethod
    def _provider_exception_code(exc: Exception) -> str:
        if isinstance(exc, TimeoutError):
            return "TIMEOUT"
        name = type(exc).__name__.upper()
        if "AUTH" in name:
            return "AUTHENTICATION_FAILED"
        if "RATE" in name and "LIMIT" in name:
            return "RATE_LIMITED"
        return "HTTP_ERROR"

    def _node_call_enterprise_data_provider(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        provider=self.providers.get(s["provider_id"]); health=provider.health(); s["provider_health"]=health; records=[]; raw_refs=[]
        previous_records=deepcopy(s.get("api_call_records") or [])
        if health.get("status")!="UP":self._append_trace(s,"call_enterprise_data_provider",{"status":health.get("status")}); return s
        for call in (s.get("acquisition_plan") or {}).get("planned_calls") or []:
            params={}; cache_key=self._cache_key(s,call["api_id"],params); cached=self.repository.find_valid_api_cache(cache_key,at_utc=utc_now())
            if cached:
                records.append({**cached,"cache_hit":True}); raw_refs.append(cached.get("raw_response_ref")); continue
            prior_retry=max([int(item.get("retry_count",0)) for item in previous_records if str(item.get("api_id"))==str(call["api_id"])] or [-1])
            called=utc_now(); call_id=stable_id("evaluation-api-call",[s["run_id"],call["api_id"],cache_key])
            try:
                response=provider.fetch_api(api_id=call["api_id"],enterprise={**s["company_identity"],"internal_enterprise_id":s["company_id"]},query_params=params)
                if not isinstance(response,dict):
                    response={"status":"INVALID_RESPONSE","api_id":call["api_id"],"data":None}
            except Exception as exc:
                error_code=self._provider_exception_code(exc)
                response={"status":error_code,"api_id":call["api_id"],"data":None,"error_type":type(exc).__name__,"error_message":str(exc)}
            raw_ref=stable_id("raw-api-response",[call_id,response]); response_hash=content_hash(response)
            self.repository.save_raw_api_response({"raw_response_ref":raw_ref,"api_call_id":call_id,"provider_id":s["provider_id"],"api_id":call["api_id"],"company_id":s["company_id"],"collected_at":called,"response_hash":response_hash,"response":response})
            valid_until=self._valid_until(provider.provider_id,called) if response.get("status")=="OK" else None
            record={"api_call_record_schema_version":"enterprise-api-call-record/2.0.0","api_call_id":call_id,"run_id":s["run_id"],"company_id":s["company_id"],"provider_id":provider.provider_id,"provider_version":provider.provider_version,"api_id":call["api_id"],"cache_key":cache_key,"called_at":called,"valid_until":valid_until,"ttl_policy_status":"CONFIGURED" if valid_until else "TTL_POLICY_NOT_CONFIGURED","status":response.get("status"),"raw_response_ref":raw_ref,"response_hash":response_hash,"retry_count":prior_retry+1,"error_code":None if response.get("status")=="OK" else response.get("status"),"cache_hit":False}
            self.repository.save_api_call_record(record); records.append(record); raw_refs.append(raw_ref)
        s["api_call_records"]=records; s["raw_response_refs"]=[r for r in raw_refs if r]
        failures=[(str(r.get("api_id")),r.get("status"),r.get("response_hash")) for r in records if r.get("status")!="OK"]
        if failures and not any(r.get("status")=="OK" for r in records):
            failure_hash=content_hash(failures)
            s["provider_no_change_count"]=int(s.get("provider_no_change_count",0))+1 if s.get("last_provider_failure_hash")==failure_hash else 0
            s["last_provider_failure_hash"]=failure_hash
        else:
            s["provider_no_change_count"]=0; s["last_provider_failure_hash"]=None
        self._append_trace(s,"call_enterprise_data_provider",{"calls":len(records),"cache_hits":sum(bool(r.get("cache_hit")) for r in records),"provider_no_change_count":s.get("provider_no_change_count",0)}); return s
    def _node_normalize_provider_results(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        normalized=[]
        for record in s.get("api_call_records") or []:
            if record.get("status")!="OK":continue
            raw=self.repository.get_raw_api_response(record["raw_response_ref"]); response=raw["response"]
            try:normalizer=self.normalizers.get(record["api_id"],response)
            except KeyError as exc:normalized.append({"api_id":record["api_id"],"status":"NORMALIZER_NOT_IMPLEMENTED","error":str(exc),"submissions":[]}); continue
            ctx={"provider_id":record["provider_id"],"api_id":record["api_id"],"api_call_id":record["api_call_id"],"raw_response_ref":record["raw_response_ref"],"collected_at":record["called_at"],"is_mock":False,"normalizer_id":normalizer.normalizer_id}
            submissions=normalizer.normalize(company_id=s["company_id"],enterprise=s["company_identity"],response=response,source_context=ctx)
            normalized.append({"api_id":record["api_id"],"status":"NORMALIZED" if submissions else "EMPTY_RESULT","normalizer_id":normalizer.normalizer_id,"submissions":submissions})
        s["normalization_results"]=normalized; self._append_trace(s,"normalize_provider_results",{"groups":len(normalized),"candidate_count":sum(len(n["submissions"]) for n in normalized)}); return s
    def _node_ingest_facts_or_create_reviews(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        task_ids=[]; candidate_ids=[]
        if self.fact_candidate_submitter:
            seen=set()
            for group in s.get("normalization_results") or []:
                for submission in group.get("submissions") or []:
                    if submission["idempotency_key"] in seen:continue
                    seen.add(submission["idempotency_key"]); submission["trust_policy"]=self.trust_policy.get("default_policy","REVIEW_REQUIRED")
                    result=self.fact_candidate_submitter(submission); candidate=(result.get("candidate") or {}); cid=candidate.get("candidate_id")
                    if cid:candidate_ids.append(cid)
                    tid=result.get("review_task_id")
                    if tid:
                        task=self.repository.read_review_task(tid); task["run_id"]=s["run_id"]; task["evaluation_provider_context"]={"agent_mode":"PROFILE_EVALUATION","provider_id":s["provider_id"],"api_id":submission.get("api_id"),"api_call_id":submission.get("api_call_id")}; self.repository.save_review_task(task)
                        if task.get("status") in {"PENDING","NEEDS_MORE_INFORMATION"}:task_ids.append(tid)
        s["fact_candidate_ids"]=sorted(set(candidate_ids)); s["review_task_ids"]=sorted(set(task_ids)); self._append_trace(s,"ingest_facts_or_create_reviews",{"reviews":len(s["review_task_ids"])}); return s
    def _node_wait_for_review(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["workflow_state"]="WAITING_REVIEW"; s["waiting_for"]=deepcopy(s.get("review_task_ids") or []); s["termination_reason"]="WAITING_FACT_CANDIDATE_REVIEW"; self._append_trace(s,"wait_for_review",{"tasks":len(s["waiting_for"])}); return s
    def _node_refresh_profiles_after_review(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        c=self.repository.read_company_context(s["company_id"]); s["context"]=c; versions=c["metadata"].get("current_versions") or {}; s["dependencies"].update({"fact_profile_version":versions.get("fact"),"capability_profile_version":versions.get("capability"),"fact_profile_hash":(c["fact_profile"].get("fact_summary") or {}).get("fact_profile_content_hash"),"capability_profile_hash":c["capability_profile"].get("capability_content_hash"),"capability_profile_summary":deepcopy(c["capability_profile"].get("capability_domains"))}); self._append_trace(s,"refresh_profiles_after_review",{"versions":versions}); return s
    def _node_rederive_features(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["feature_snapshot"]=self.feature_engine.derive(s["context"]["fact_profile"]); self._append_trace(s,"rederive_features"); return s
    def _node_recalculate_active_indicators(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        profile,card=self.engine.evaluate(company_id=s["company_id"],feature_bundle=s["feature_snapshot"],dependencies=s["dependencies"]); s["profile"]=profile;s["card"]=card;s["indicator_results"]=profile["indicator_results"];self._append_trace(s,"recalculate_active_indicators",{"scored":profile["summary"]["scored_indicator_count"]});return s
    def _node_aggregate_secondary_dimensions(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["secondary_dimensions"]=deepcopy(s["profile"]["secondary_dimensions"]);self._append_trace(s,"aggregate_secondary_dimensions",{"count":len(s["secondary_dimensions"])});return s
    def _node_aggregate_primary_dimensions(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:s["primary_dimensions"]=deepcopy(s["profile"]["primary_dimensions"]);self._append_trace(s,"aggregate_primary_dimensions",{"count":len(s["primary_dimensions"])});return s
    def _node_build_evaluation_profile(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        saved=self.repository.save_evaluation_profile(s["company_id"],s["profile"],run_id=s["run_id"]);s["evaluation_profile_version"]=saved["version"];s["evaluation_profile_id"]=f"evaluation-profile:{s['company_id']}:v{saved['version']}";self._append_trace(s,"build_evaluation_profile",{"version":saved["version"]});return s
    def _node_build_evaluation_card(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        s["card"]["agent_trace"]=deepcopy(s.get("trace") or []);s["card"]["api_call_records"]=deepcopy(s.get("api_call_records") or []);s["card"]["waiting_review_count"]=len(s.get("review_task_ids") or []);self.repository.save_evaluation_card(s["company_id"],s["card"],run_id=s["run_id"]);s["evaluation_card_id"]=f"evaluation-card:{s['run_id']}";self._append_trace(s,"build_evaluation_card");return s
    def _node_finalize(self,s:EvaluationWorkflowState)->EvaluationWorkflowState:
        scored = s["profile"]["summary"]["scored_indicator_count"]
        provider_status = (s.get("provider_health") or {}).get("status")
        if s.get("provider_failure_status") == "WAITING_DATA_PROVIDER" and int(s.get("provider_no_change_count",0)) >= 1:
            s["workflow_state"] = "MANUAL_INTERVENTION_REQUIRED"
            s["termination_reason"] = "REPEATED_PROVIDER_FAILURE_NO_CHANGE"
            s["waiting_for"] = ["HUMAN_REVIEW"]
        elif s.get("provider_failure_status") == "WAITING_DATA_PROVIDER":
            s["workflow_state"] = "WAITING_DATA_PROVIDER"
            s["termination_reason"] = "PROVIDER_CALLS_FAILED_RETRYABLE"
            s["waiting_for"] = ["DATA_PROVIDER"]
        elif provider_status == "PROVIDER_NOT_CONFIGURED" and (s.get("acquisition_plan") or {}).get("planned_calls"):
            s["workflow_state"] = "PROVIDER_NOT_CONFIGURED"
            s["termination_reason"] = "PROVIDER_NOT_CONFIGURED_PARTIAL_EVALUATION_AVAILABLE"
            s["waiting_for"] = [s.get("provider_id") or "DATA_PROVIDER"]
        else:
            s["workflow_state"] = "COMPLETED_WITH_PARTIAL_COVERAGE" if scored < 44 else "COMPLETED"
            s["termination_reason"] = "ACTIVE_RULES_COMPLETED_REMAINING_GAPS_CLASSIFIED"
            s["waiting_for"] = []
        self._append_trace(s,"finalize",{"status":s["workflow_state"]})
        return s

    def start(self,*,company_id:str,provider_id:str="LOCAL_PROFILE",provider_mode:str|None=None,workflow_mode:str="legacy",max_api_calls:int=10,max_rounds:int=3)->dict[str,Any]:
        provider_id=provider_mode or provider_id; self.providers.get(provider_id)
        started=utc_now();run_id=stable_id("evaluation-run",[company_id,started,provider_id])
        state:EvaluationWorkflowState={"run_id":run_id,"company_id":company_id,"provider_id":provider_id,"workflow_mode":workflow_mode,"round_index":0,"max_rounds":max_rounds,"max_api_calls":max_api_calls,"workflow_state":"RUNNING","waiting_for":[],"termination_reason":"","checkpoint_version":0,"trace":[],"started_at_utc":started,"no_change_count":0,"last_plan_hash":None,"provider_no_change_count":0,"last_provider_failure_hash":None,"api_call_records":[],"normalization_results":[],"fact_candidate_ids":[],"review_task_ids":[]}
        final=self.graph.invoke(state)
        return {"run":self._run_from_state(final),"profile":final.get("profile"),"card":final.get("card"),"acquisition_plan":final.get("acquisition_plan"),"api_calls":final.get("api_call_records") or [],"checkpoint":self.repository.get_evaluation_checkpoint(run_id)}
    def get_run(self,run_id:str)->dict[str,Any]:return self.repository.get_evaluation_run(run_id)
    def resume(self,run_id:str)->dict[str,Any]:
        checkpoint=self.repository.get_evaluation_checkpoint(run_id); state=checkpoint["state"]; previous=self.get_run(run_id)
        if previous.get("status") not in {"WAITING_REVIEW","WAITING_DATA_PROVIDER","PROVIDER_NOT_CONFIGURED","COMPLETED_WITH_PARTIAL_COVERAGE"}:
            raise ValueError("EVALUATION_RUN_NOT_RESUMABLE")
        remaining=[t for t in self.repository.list_review_tasks(company_id=state["company_id"]) if t.get("run_id")==run_id and t.get("status") in {"PENDING","NEEDS_MORE_INFORMATION"}]
        if remaining:raise ValueError("EVALUATION_REVIEW_PENDING")
        state["workflow_state"]="RUNNING";state["waiting_for"]=[];state["round_index"]=int(state.get("round_index",0))+1
        if state["round_index"] > int(state.get("max_rounds",3)):
            state["workflow_state"]="MANUAL_INTERVENTION_REQUIRED"; state["termination_reason"]="MAX_ROUNDS_REACHED"; self._checkpoint(state); final=state
        else:
            start_node = "refresh_profiles_after_review" if previous.get("status") in {"WAITING_REVIEW","COMPLETED_WITH_PARTIAL_COVERAGE"} else "call_enterprise_data_provider"
            state.pop("provider_failure_status", None)
            final=self.graph.invoke(state,start_node=start_node)
        return {"run":self._run_from_state(final),"profile":final.get("profile"),"card":final.get("card"),"acquisition_plan":final.get("acquisition_plan"),"api_calls":final.get("api_call_records") or [],"checkpoint":self.repository.get_evaluation_checkpoint(run_id)}
    def latest_profile(self,company_id:str)->dict[str,Any]|None:return self.repository.get_current_evaluation_profile(company_id)
    def history(self,company_id:str)->list[dict[str,Any]]:return self.repository.list_evaluation_profile_versions(company_id)
    def latest_card(self,company_id:str)->dict[str,Any]|None:return self.repository.get_current_evaluation_card(company_id)
    def data_gaps(self,company_id:str)->dict[str,Any]:
        p=self.latest_profile(company_id)
        if not p:return {"company_id":company_id,"items":[],"groups":{},"status":"NOT_EVALUATED"}
        groups={}
        for i in p.get("data_gaps") or []:groups.setdefault(i["resolvability"],[]).append(i)
        return {"company_id":company_id,"item_count":sum(len(v) for v in groups.values()),"groups":groups,"items":[i for v in groups.values() for i in v]}
    def latest_plan(self,company_id:str)->dict[str,Any]|None:
        runs=self.repository.list_evaluation_runs(company_id=company_id,limit=1)
        if not runs or not runs[0].get("acquisition_plan_id"):return None
        return self.repository.get_acquisition_plan(runs[0]["acquisition_plan_id"])
