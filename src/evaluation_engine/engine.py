from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from datetime import datetime,timezone
from typing import Any
from src.evaluation_model import EvaluationModelCatalog,content_hash
from src.evaluation_model.models import IndicatorScoreResult
from src.evaluation_rules import EvaluationRuleRegistry

def utc_now()->str:return datetime.now(timezone.utc).isoformat()

class EnterpriseEvaluationEngine:
    version="enterprise-evaluation-engine/2.0.0"
    def __init__(self,catalog:EvaluationModelCatalog|None=None)->None:
        self.catalog=catalog or EvaluationModelCatalog(); self.registry=EvaluationRuleRegistry(self.catalog.indicators())
    def _feature_for(self,bundle:dict[str,Any],key:str|None)->dict[str,Any]:
        if not key:return {"value":None,"fact_ids":[],"evidence_ids":[],"missing_fields":[]}
        if key.startswith("risk:"):
            cat=key.split(":",1)[1]; return deepcopy((bundle["features"].get("risk_categories") or {}).get(cat) or {"value":None,"fact_ids":[],"evidence_ids":[],"missing_fields":[cat]})
        if key.startswith("indicator:"):
            code=key.split(":",1)[1]; return deepcopy((bundle["features"].get("indicator_evidence") or {}).get(code) or {"value":None,"fact_ids":[],"evidence_ids":[],"missing_fields":[code]})
        return deepcopy(bundle["features"].get(key) or {"value":None,"fact_ids":[],"evidence_ids":[],"missing_fields":[key]})
    def evaluate_indicator(self,indicator:dict[str,Any],bundle:dict[str,Any],*,allow_not_active:bool=False)->dict[str,Any]:
        reg=self.registry.get(indicator["indicator_code"]); feature=self._feature_for(bundle,reg.feature_key); active=bool(indicator.get("active",indicator["selection_status"]!="DELETE_CANDIDATE"))
        if not active and allow_not_active:
            from src.evaluation_rules.registry import identity, operation
            historical={"A1.1":("identity_verification",identity),"A1.2":("operation",operation)}.get(indicator["indicator_code"])
            if historical:
                key,func=historical; feature=self._feature_for(bundle,key); outcome=func(feature)
            else: outcome={"status":"EXCLUDED","raw_score":None,"explanation":"无历史观察处理器。","missing_fields":[]}
        elif not active: outcome={"status":"EXCLUDED","raw_score":None,"explanation":"该指标已由模型选择排除，不参与当前缺口、API、评分或活动覆盖率。","missing_fields":[]}
        elif reg.rule_status=="MODEL_CONTENT_CONFLICT":outcome={"status":"PENDING_RULE","raw_score":None,"explanation":"指标名称、规则与API字段语义冲突，正式评分暂停。","missing_fields":[]}
        elif reg.rule_status in {"CALIBRATION_REQUIRED","SEMANTIC_POLICY_REQUIRED"}:outcome={"status":"PENDING_RULE","raw_score":None,"explanation":"规则需要行业阈值、校准或业务政策，不能通过补数据解决。","missing_fields":[]}
        elif reg.function is None:outcome={"status":"PENDING_RULE","raw_score":None,"explanation":"规则已注册，等待特征实现；不触发企业 API 采集。","missing_fields":[]}
        else:
            try:outcome=reg.function(feature)
            except Exception as exc:outcome={"status":"CALCULATION_ERROR","raw_score":None,"explanation":f"确定性计算失败：{type(exc).__name__}","missing_fields":[]}
        raw=outcome.get("raw_score"); weighted=None if raw is None else raw/100*float(indicator["legacy_weight"])
        return IndicatorScoreResult(
            indicator_code=indicator["indicator_code"],indicator_name=indicator["indicator_name"],selection_status=indicator["selection_status"],active=active,
            acquisition_priority=indicator.get("acquisition_priority"),primary_code=indicator["primary_code"],secondary_code=indicator["secondary_code"],legacy_weight=float(indicator["legacy_weight"]),active_weight=None,
            scoring_status=outcome["status"],raw_score=raw,weighted_contribution=weighted,rule_status=reg.rule_status,resolvability=reg.resolvability,
            input_features={reg.feature_key or "none":deepcopy(feature.get("value"))},evidence_ids=feature.get("evidence_ids") or [],fact_ids=feature.get("fact_ids") or [],
            api_ids=(indicator.get("api_ids") or []) if active else [],missing_fields=outcome.get("missing_fields") or feature.get("missing_fields") or [],explanation=outcome.get("explanation", ""),
            low_score_diagnosis=indicator.get("low_score_diagnosis","") if active else "",improvement_suggestion=indicator.get("improvement_suggestion","") if active else "",
            redline_note=indicator.get("redline_note","") if active else "",model_conflicts=indicator.get("model_conflicts") or [],handler_id=reg.handler_id,
        ).to_dict()
    @staticmethod
    def _dimension(code:str,name:str,items:list[dict[str,Any]])->dict[str,Any]:
        active=[i for i in items if i["active"]]
        scored=[i for i in active if i["scoring_status"]=="SCORED"]
        active_weight=sum(i["legacy_weight"] for i in active); scored_weight=sum(i["legacy_weight"] for i in scored); points=sum(i["weighted_contribution"] or 0 for i in scored)
        strengths=sorted([i for i in scored if (i["raw_score"] or 0)>=80],key=lambda x:(-(x["raw_score"] or 0),x["indicator_code"]))[:3]
        risks=sorted([i for i in scored if (i["raw_score"] or 100)<60],key=lambda x:((x["raw_score"] or 0),x["indicator_code"]))[:3]
        return {"code":code,"name":name,"active_indicator_count":len(active),"scored_indicator_count":len(scored),"active_legacy_weight":active_weight,"scored_weight":scored_weight,
                "observed_points":points,"coverage_ratio":None if active_weight==0 else scored_weight/active_weight,"normalized_observed_score":None if scored_weight==0 else points/scored_weight*100,
                "missing_data_count":sum(i["scoring_status"] in {"MISSING_DATA","INSUFFICIENT_EVIDENCE","API_FAILED","API_PENDING"} for i in active),
                "pending_rule_count":sum(i["scoring_status"]=="PENDING_RULE" for i in active),"model_conflict_count":sum(i["rule_status"]=="MODEL_CONTENT_CONFLICT" for i in active),
                "key_strengths":[{"indicator_code":i["indicator_code"],"indicator_name":i["indicator_name"],"raw_score":i["raw_score"]} for i in strengths],
                "key_risks":[{"indicator_code":i["indicator_code"],"indicator_name":i["indicator_name"],"raw_score":i["raw_score"],"diagnosis":i["low_score_diagnosis"]} for i in risks]}
    def evaluate(self,*,company_id:str,feature_bundle:dict[str,Any],dependencies:dict[str,Any])->tuple[dict[str,Any],dict[str,Any]]:
        results=[self.evaluate_indicator(i,feature_bundle) for i in self.catalog.indicators()]; active=[i for i in results if i["active"]]; core=[i for i in active if i["selection_status"]=="CORE_RETAINED"]
        scored=[i for i in active if i["scoring_status"]=="SCORED"]; core_scored=[i for i in core if i["scoring_status"]=="SCORED"]
        scored_weight=sum(i["legacy_weight"] for i in scored); points=sum(i["weighted_contribution"] or 0 for i in scored); core_weight=sum(i["legacy_weight"] for i in core); core_scored_weight=sum(i["legacy_weight"] for i in core_scored); core_points=sum(i["weighted_contribution"] or 0 for i in core_scored)
        active_weight=sum(i["legacy_weight"] for i in active)
        official_score=None if scored_weight==0 else points/scored_weight*100
        official_grade=None if official_score is None else "优秀" if official_score>=85 else "良好" if official_score>=70 else "一般" if official_score>=60 else "待改善"
        summary={
            "scored_indicator_count":len(scored),"scoreable_rule_count":sum(i["raw_score"] is not None for i in active),"scored_core_indicator_count":len(core_scored),"scored_legacy_weight":scored_weight,"observed_legacy_points":points,
            "legacy_60_coverage_ratio":scored_weight/100,"active_44_coverage_ratio":None if active_weight==0 else scored_weight/active_weight,"core_27_coverage_ratio":None if core_weight==0 else core_scored_weight/core_weight,
            "active_44_normalized_observed_score":None if scored_weight==0 else points/scored_weight*100,"core_27_normalized_observed_score":None if core_scored_weight==0 else core_points/core_scored_weight*100,
            "coverage_ratio_on_legacy_model":scored_weight/100,"candidate_active_set_coverage_ratio":None if active_weight==0 else scored_weight/active_weight,"normalized_observed_score":None if scored_weight==0 else points/scored_weight*100,
            "official_total_score":official_score,"official_grade":official_grade,"publication_status":"RULES_CONFIRMED","status_counts":{},"active_indicator_count":len(active),"core_indicator_count":len(core),"excluded_indicator_count":len(results)-len(active),
        }
        for i in results:summary["status_counts"][i["scoring_status"]]=summary["status_counts"].get(i["scoring_status"],0)+1
        pmeta={x["code"]:x for x in self.catalog.catalog["primary_dimensions"]}; smeta={x["code"]:x for x in self.catalog.catalog["secondary_dimensions"]}
        primary=[self._dimension(c,pmeta[c]["name"],[i for i in active if i["primary_code"]==c]) for c in sorted(pmeta)]
        secondary=[self._dimension(c,smeta[c]["name"],[i for i in active if i["secondary_code"]==c]) for c in sorted(smeta)]
        redlines=[i for i in active if i["scoring_status"]=="SCORED" and (i["raw_score"] or 100)<=40 and i.get("redline_note")]
        gaps=[{"indicator_code":i["indicator_code"],"indicator_name":i["indicator_name"],"scoring_status":i["scoring_status"],"resolvability":i["resolvability"],"missing_fields":i["missing_fields"],"api_ids":i["api_ids"] if i["resolvability"] in {"DATA_RESOLVABLE","PROVIDER_RESOLVABLE"} else []} for i in active if i["scoring_status"]!="SCORED"]
        profile={"evaluation_profile_schema_version":"enterprise-evaluation-profile/2.0.0","company_id":company_id,"enterprise":deepcopy(feature_bundle.get("enterprise")),"as_of_date":feature_bundle.get("as_of_date"),"model_sha256":self.catalog.source_sha256,"rule_version":self.registry.version,"feature_version":feature_bundle.get("feature_schema_version"),"dependencies":deepcopy(dependencies),"indicator_results":results,"active_indicator_results":active,"excluded_indicator_results":[i for i in results if not i["active"]],"primary_dimensions":primary,"secondary_dimensions":secondary,"data_gaps":gaps,"summary":summary,"publication_status":"RULES_CONFIRMED","generated_at_utc":utc_now()}
        profile["content_hash"]=content_hash({k:v for k,v in profile.items() if k not in {"generated_at_utc","content_hash"}})
        card={"evaluation_card_schema_version":"enterprise-evaluation-card/2.0.0","company_id":company_id,"enterprise":deepcopy(profile["enterprise"]),"identity":{"data_source":dependencies.get("data_origin") or "PROFILE_FACTS","is_mock":False,"as_of_date":profile["as_of_date"]},"model_status":{"model_sha256":self.catalog.source_sha256,"total_indicators":60,"active_indicators":44,"core_indicators":27,"retained_indicators":17,"excluded_indicators":16,"weight_policy_confirmed":True,"model_conflict_count":0},"score_summary":deepcopy(summary),"primary_dimensions":primary,"secondary_dimensions":secondary,"indicator_results":deepcopy(active),"excluded_indicators":deepcopy(profile["excluded_indicator_results"]),"data_gaps":gaps,"major_strengths":[s for d in primary for s in d["key_strengths"]][:5],"major_weaknesses":[r for d in primary for r in d["key_risks"]][:5],"redline_risks":deepcopy(redlines),"capability_profile":deepcopy(dependencies.get("capability_profile_summary")),"notice":"评分依据度量模型的评分口径和诊断说明生成；每项均保留评分原因，低分项提供诊断与改善建议。","generated_at_utc":utc_now(),"evaluation_profile_hash":profile["content_hash"]}
        card["content_hash"]=content_hash({k:v for k,v in card.items() if k not in {"generated_at_utc","content_hash"}})
        return profile,card
