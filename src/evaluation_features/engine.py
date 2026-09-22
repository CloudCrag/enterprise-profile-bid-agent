"""Deterministic evaluation features derived only from verified current facts."""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from typing import Any

ACCEPTED_VERIFICATION = {"verified", "partially_verified"}


def _date(value: Any) -> date | None:
    if not value: return None
    try: return date.fromisoformat(str(value)[:10])
    except ValueError: return None


def _feature(value: Any, facts: list[dict[str, Any]], *, as_of_date: str, missing_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "value": deepcopy(value),
        "fact_ids": [fact["fact_id"] for fact in facts if fact],
        "evidence_ids": sorted({e for fact in facts if fact for e in fact.get("evidence_ids") or []}),
        "as_of_date": as_of_date,
        "missing_fields": missing_fields or [],
    }


def _risk_feature(category: str, facts: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    records=[]
    for fact in facts:
        payload=deepcopy(fact.get("payload") or {})
        source=fact.get("source") or {}; temporal=fact.get("temporal") or {}
        raw_records=payload.get("records") if isinstance(payload.get("records"),list) else [payload]
        for item in raw_records:
            if not isinstance(item,dict): continue
            records.append({
                **deepcopy(item),
                "current_effective": item.get("current_effective", payload.get("current_effective", str(payload.get("status") or "").lower() in {"active","current","major","severe","bankruptcy","liquidation"})),
                "resolved": item.get("resolved", str(item.get("remediation_status") or payload.get("remediation_status") or "").lower() in {"completed","resolved","removed","closed"}),
                "occurrence_date": item.get("occurrence_date") or item.get("date") or temporal.get("published_at"),
                "resolved_date": item.get("resolved_date") or payload.get("resolved_date"),
                "amount": item.get("amount") or payload.get("amount"),
                "severity": item.get("severity") or payload.get("severity") or payload.get("severity_raw"),
                "source_authority": item.get("source_authority") or source.get("source_platform"),
                "source_record_id": source.get("source_record_id"),
            })
    active=[r for r in records if r.get("current_effective") and not r.get("resolved")]
    resolved=[r for r in records if r.get("resolved")]
    dates=[r.get("occurrence_date") for r in records if r.get("occurrence_date")]
    amounts=[r.get("amount") for r in records if isinstance(r.get("amount"),(int,float))]
    severities=[str(r.get("severity") or "").lower() for r in records]
    value={
        "category":category,
        "record_count":len(records),
        "active_record_count":len(active),
        "resolved_record_count":len(resolved),
        "occurrence_dates":dates,
        "resolved_dates":[r.get("resolved_date") for r in resolved if r.get("resolved_date")],
        "amount":sum(amounts) if amounts else None,
        "severity":"severe" if any(s in {"severe","major","high"} for s in severities) else "minor" if any(severities) else None,
        "repeat_count":max(0,len(records)-1),
        "current_effective":bool(active),
        "source_authority":sorted({str(r.get("source_authority")) for r in records if r.get("source_authority")}),
        "time_window":{"as_of_date":as_of,"earliest":min(dates) if dates else None,"latest":max(dates) if dates else None},
        "records":records,
        "reliable_no_record": any(str((f.get("payload") or {}).get("status") or "").lower() in {"none","no_record","clear"} for f in facts) and not records,
    }
    return _feature(value if facts else None, facts, as_of_date=as_of, missing_fields=[] if facts else [category])


class EvaluationFeatureEngine:
    version = "evaluation-features/2.0.0"
    RISK_ALIASES={
        "dishonesty":{"dishonesty","失信被执行"}, "enforcement":{"enforcement","被执行人"},
        "terminal_case":{"terminal_case","终本案件"}, "litigation":{"litigation","诉讼","开庭","法院公告"},
        "administrative_penalty":{"administrative_penalty","行政处罚"},
        "abnormal_operation":{"abnormal_operation","经营异常","严重违法"},
        "tax_violation":{"tax_violation","tax","税务违法","欠税","非正常户"},
        "environment_safety":{"environment_safety","环保处罚","安全生产"},
        "bankruptcy_liquidation":{"bankruptcy_liquidation","破产","清算","注销"},
        "pledge_freeze":{"pledge_freeze","股权质押","动产抵押","司法冻结"},
        "key_person":{"key_person","关键人员风险"}, "negative_news":{"negative_news","负面新闻","舆情"},
        "qualification_warning":{"qualification_warning","资质撤销","许可失效"},
        "supply_chain":{"supply_chain","供应链风险","客户集中","供应商集中"},
    }

    def derive(self, fact_profile: dict[str, Any]) -> dict[str, Any]:
        as_of=str(fact_profile.get("as_of_date") or date.today().isoformat())
        included=set((fact_profile.get("fact_view") or {}).get("included_fact_ids") or [])
        facts=[f for f in fact_profile.get("facts") or [] if f.get("verification_status") in ACCEPTED_VERIFICATION and (not included or f.get("fact_id") in included or f.get("fact_type")=="qualification") and f.get("fact_status") not in {"invalid","ambiguous"}]
        by_type:dict[str,list[dict[str,Any]]]=defaultdict(list)
        for fact in facts: by_type[str(fact.get("fact_type"))].append(fact)
        registration=(by_type.get("business_registration") or [None])[0]
        rp=(registration or {}).get("payload") or {}
        qualifications=by_type.get("qualification") or []
        personnel=(by_type.get("personnel") or [None])[0]
        risk_facts=by_type.get("risk_penalty_credit") or []
        today=_date(as_of) or date.today(); established=_date(rp.get("established_date"))
        operating_years=None if established is None else max(0.0,(today-established).days/365.2425)
        registered=(rp.get("registered_capital") or {}).get("value") if isinstance(rp.get("registered_capital"),dict) else rp.get("registered_capital")
        paid=(rp.get("paid_in_capital") or {}).get("value") if isinstance(rp.get("paid_in_capital"),dict) else rp.get("paid_in_capital")
        ratio=paid/registered if isinstance(registered,(int,float)) and registered>0 and isinstance(paid,(int,float)) else None
        qitems=[]
        for fact in qualifications:
            p=deepcopy(fact.get("payload") or {})
            valid_until=_date(p.get("valid_until")); status=str(p.get("status") or "unknown").lower()
            current_valid=status=="valid" and (valid_until is None or valid_until>=today) and fact.get("fact_status")=="active"
            days=None if valid_until is None else (valid_until-today).days
            qitems.append({**p,"current_valid":current_valid,"days_to_expiry":days,"public_disclosure_complete":bool((p.get("qualification_name") or p.get("name")) and p.get("certificate_number") and (p.get("issuer") or p.get("issuing_authority")) and p.get("valid_until")),"revoked":status=="revoked" or str(p.get("revocation_status") or "").lower() in {"revoked","withdrawn"}})
        valid=[q for q in qitems if q["current_valid"]]; revoked=[q for q in qitems if q["revoked"]]; expiring=[q for q in qitems if isinstance(q.get("days_to_expiry"),int) and 0<=q["days_to_expiry"]<=90]
        disclosure={"certificate_count":len(qitems),"complete_count":sum(1 for q in qitems if q["public_disclosure_complete"]),"items":qitems}
        industry_entry={"enterprise_industry":rp.get("industry"),"items":qitems,"mapping_status":"CALIBRATION_REQUIRED","matching_core_qualification_count":None}
        qualification_risk={"items":qitems,"revoked_count":len(revoked),"expired_count":sum(1 for q in qitems if q.get("days_to_expiry") is not None and q["days_to_expiry"]<0),"expiring_90d_count":len(expiring),"current_valid_count":len(valid)}
        category_facts:dict[str,list[dict[str,Any]]]=defaultdict(list)
        for fact in risk_facts:
            raw=str((fact.get("payload") or {}).get("category") or "")
            matched=False
            for normalized,aliases in self.RISK_ALIASES.items():
                chinese_partial_match = any(
                    alias and any(ord(char) > 127 for char in alias) and alias in raw
                    for alias in aliases
                )
                if raw in aliases or chinese_partial_match:
                    category_facts[normalized].append(fact); matched=True
            if not matched and raw: category_facts[raw].append(fact)
        tax_candidates=category_facts.get("tax_credit") or [f for f in risk_facts if str((f.get("payload") or {}).get("category")) in {"tax_credit","tax"}]
        tax=tax_candidates[0] if tax_candidates else None
        indicator_evidence={}
        for fact in by_type.get("other_enterprise_fact") or []:
            payload=fact.get("payload") or {}
            details=payload.get("details") or {}
            if payload.get("subtype")=="evaluation_indicator_evidence" and details.get("indicator_code"):
                indicator_evidence[str(details["indicator_code"])]=_feature({"fields":deepcopy(details.get("fields") or {})},[fact],as_of_date=as_of)
        features={
            "identity_verification":_feature({"verification_result":rp.get("verification_result"),"elements":rp.get("verification_elements")} if registration else None,[registration] if registration else [],as_of_date=as_of,missing_fields=[] if registration else ["business_registration"]),
            "operation":_feature({"normalized_status":rp.get("normalized_operation_status"),"status_raw":rp.get("operation_status_raw"),"established_date":rp.get("established_date"),"operating_years":operating_years} if registration else None,[registration] if registration else [],as_of_date=as_of,missing_fields=[] if registration else ["business_registration"]),
            "capital":_feature({"registered_capital":registered,"paid_in_capital":paid,"paid_in_ratio":ratio} if registration else None,[registration] if registration else [],as_of_date=as_of,missing_fields=[n for n,v in (("registered_capital",registered),("paid_in_capital",paid)) if v is None]),
            "tax_credit":_feature((tax or {}).get("payload") if tax else None,[tax] if tax else [],as_of_date=as_of,missing_fields=[] if tax else ["tax_credit_rating"]),
            "qualification_disclosure":_feature(disclosure if qualifications else None,qualifications,as_of_date=as_of,missing_fields=[] if qualifications else ["qualification_records"]),
            "industry_entry_qualification":_feature(industry_entry if qualifications else None,qualifications,as_of_date=as_of,missing_fields=[] if qualifications else ["industry_qualification_mapping","qualification_records"]),
            "qualification_risk":_feature(qualification_risk if qualifications else None,qualifications,as_of_date=as_of,missing_fields=[] if qualifications else ["qualification_records"]),
            "personnel":_feature(deepcopy((personnel or {}).get("payload")) if personnel else None,[personnel] if personnel else [],as_of_date=as_of,missing_fields=[] if personnel else ["employee_count","social_insurance_count"]),
            "risk_categories":{category:_risk_feature(category,items,as_of) for category,items in category_facts.items()},
            "indicator_evidence":indicator_evidence,
        }
        return {"feature_schema_version":self.version,"enterprise":deepcopy(fact_profile.get("enterprise")),"as_of_date":as_of,"features":features,"source_fact_profile_hash":(fact_profile.get("fact_summary") or {}).get("fact_profile_content_hash")}
