"""Rule registry for the 44 active indicators and 16 explicitly excluded indicators."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

RuleFunction=Callable[[dict[str,Any]],dict[str,Any]]


def missing(feature:dict[str,Any],message:str)->dict[str,Any]: return {"status":"MISSING_DATA","raw_score":0,"explanation":message,"missing_fields":feature.get("missing_fields") or []}

def capital(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v or v.get("paid_in_ratio") is None: return missing(feature,"缺少可比较的注册资本和实缴资本。")
    ratio=v["paid_in_ratio"]
    if ratio<0 or ratio>10: return {"status":"CALCULATION_ERROR","raw_score":None,"explanation":"实缴比例异常，需核验单位与币种。"}
    score=100 if ratio>=.8 else 80 if ratio>=.5 else 60 if ratio>=.3 else 40
    return {"status":"SCORED","raw_score":score,"explanation":f"企业实缴资本约占注册资本的 {ratio:.1%}，按实缴比例档位，本项得 {score} 分。"}

def tax_credit(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少税务信用等级。")
    rating=str(v.get("rating_value") or v.get("rating") or v.get("tax_rating") or v.get("status") or "").upper(); mapping={"A":100,"B":80,"M":60,"C":40,"D":0}
    return {"status":"SCORED","raw_score":mapping[rating],"explanation":f"企业税务信用等级为 {rating}，本项得 {mapping[rating]} 分。"} if rating in mapping else {"status":"INSUFFICIENT_EVIDENCE","raw_score":0,"explanation":"现有税务信用等级无法识别，本项暂记 0 分。"}

def qualification_disclosure(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少资质证书公开记录。")
    total=int(v.get("certificate_count") or 0); complete=int(v.get("complete_count") or 0)
    if total==0:return {"status":"INSUFFICIENT_EVIDENCE","raw_score":None,"explanation":"没有足够证据判断证书披露情况。"}
    ratio=complete/total; score=100 if ratio==1 else 80 if ratio>=.8 else 60 if ratio>=.5 else 40
    return {"status":"SCORED","raw_score":score,"explanation":f"共找到 {total} 项证书，其中 {complete} 项包含编号、发证机构和有效期等关键信息，本项得 {score} 分。"}

def industry_entry(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少企业行业或准入资质记录。")
    return {"status":"PENDING_RULE","raw_score":None,"explanation":"行业—核心准入资质映射尚未确认；任意有效证书不能自动视为满足行业准入。","missing_fields":["industry_qualification_mapping"]}

def qualification_warning(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少资质撤销、失效与临期信息。")
    if int(v.get("revoked_count") or 0)>0:return {"status":"SCORED","raw_score":0,"explanation":"发现资质被撤销或许可被撤回的记录，本项得 0 分。"}
    if int(v.get("expired_count") or 0)>0:return {"status":"SCORED","raw_score":40,"explanation":"发现已经失效的资质记录，本项得 40 分。"}
    if int(v.get("expiring_90d_count") or 0)>0:return {"status":"SCORED","raw_score":70,"explanation":"发现将在 90 天内到期的资质，本项得 70 分。"}
    if int(v.get("current_valid_count") or 0)>0:return {"status":"SCORED","raw_score":100,"explanation":"现有资质均处于有效状态，未发现撤销、失效或近期到期风险，本项得 100 分。"}
    return {"status":"INSUFFICIENT_EVIDENCE","raw_score":0,"explanation":"现有资料无法确认资质是否有效，本项暂记 0 分。"}

def risk_rule(feature:dict[str,Any],name:str)->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,f"缺少{name}权威查询结果；未查到不等于无风险。")
    if v.get("reliable_no_record"):return {"status":"SCORED","raw_score":100,"explanation":f"权威查询未发现{name}记录，本项得 100 分。"}
    active=int(v.get("active_record_count") or 0); resolved=int(v.get("resolved_record_count") or 0); count=int(v.get("record_count") or 0)
    sev=str(v.get("severity") or "").lower(); amount=v.get("amount")
    if active:
        score=0 if sev in {"severe","major","high"} or (isinstance(amount,(int,float)) and amount>0) else 40
        return {"status":"SCORED","raw_score":score,"explanation":f"发现 {active} 条当前有效的{name}记录，本项得 {score} 分。"}
    if resolved:return {"status":"SCORED","raw_score":70,"explanation":f"发现 {resolved} 条历史{name}记录，但均已解除或整改，本项得 70 分。"}
    if count==0:return {"status":"INSUFFICIENT_EVIDENCE","raw_score":0,"explanation":f"查询结果为空，但尚不能确认企业确实没有{name}记录，本项暂记 0 分。"}
    return {"status":"SCORED","raw_score":60,"explanation":f"发现 {count} 条{name}记录，但暂时无法确认是否仍然有效，本项得 60 分。"}


# Backward-compatible pure functions retained for historical unit tests.
# They are no longer registered for deleted indicators or B4.1 formal scoring.
def identity(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少企业存在与身份核验结果。")
    elements=v.get("elements") or {}
    if elements.get("enterprise_exists") is False:return {"status":"SCORED","raw_score":0,"explanation":"权威核验表明企业不存在。"}
    checks=[elements.get(k) for k in ("name_consistent","credit_code_consistent","legal_representative_consistent")]
    if elements.get("enterprise_exists") is True and all(x is True for x in checks):return {"status":"SCORED","raw_score":100,"explanation":"企业存在且三要素一致。"}
    if elements.get("enterprise_exists") is True:return {"status":"SCORED","raw_score":60,"explanation":"企业存在，但核心身份字段存在缺失或差异。"}
    return {"status":"INSUFFICIENT_EVIDENCE","raw_score":None,"explanation":"存在性核验结果不充分。"}

def operation(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少经营状态或成立日期。")
    status=str(v.get("normalized_status") or "").lower(); years=v.get("operating_years")
    if status in {"revoked","cancelled","deregistered","注销","吊销","撤销"}:return {"status":"SCORED","raw_score":0,"explanation":"企业处于吊销、注销或撤销状态。"}
    if status in {"unstable","suspended","moved","停业","迁出"}:return {"status":"SCORED","raw_score":40,"explanation":"经营状态不稳定。"}
    if status in {"normal","active","存续","在业"} and isinstance(years,(int,float)):return {"status":"SCORED","raw_score":100 if years>=5 else 80 if years>=1 else 60,"explanation":f"正常经营，存续约 {years:.1f} 年。"}
    return {"status":"INSUFFICIENT_EVIDENCE","raw_score":None,"explanation":"经营状态或存续年限证据不足。"}

def qualification(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少资质证书记录。")
    valid=int(v.get("valid_count") or v.get("current_valid_count") or 0); expired=int(v.get("expired_or_revoked_count") or v.get("expired_count") or 0)
    if valid>0 and expired==0:return {"status":"SCORED","raw_score":100,"explanation":f"存在 {valid} 项有效资质。"}
    if valid>0:return {"status":"SCORED","raw_score":70,"explanation":f"存在 {valid} 项有效资质及 {expired} 项异常记录。"}
    if expired>0:return {"status":"SCORED","raw_score":40,"explanation":"仅发现过期或撤销资质。"}
    return {"status":"INSUFFICIENT_EVIDENCE","raw_score":None,"explanation":"资质状态无法确认。"}

def personnel(feature:dict[str,Any])->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,"缺少员工及社保人数。")
    employee=v.get("employee_count"); insured=v.get("social_insurance_count")
    if not isinstance(employee,(int,float)) or employee<=0 or not isinstance(insured,(int,float)):return missing(feature,"员工或社保人数不可计算。")
    ratio=insured/employee; score=100 if employee>=100 and ratio>=.8 else 80 if employee>=30 and ratio>=.6 else 60 if employee>=10 else 40
    return {"status":"SCORED","raw_score":score,"explanation":f"实验规则：员工 {employee:g} 人，参保覆盖约 {ratio:.1%}。","not_for_publication":True}

def risk_none_or_severity(feature:dict[str,Any],*,category_name:str)->dict[str,Any]:
    v=feature.get("value")
    if not v:return missing(feature,f"缺少{category_name}查询结果；未查到不等于无风险。")
    if "record_count" in v:return risk_rule(feature,category_name)
    status=str(v.get("status") or "").lower(); records=v.get("records") or []
    if status in {"none","no_record","clear"} and not records:return {"status":"SCORED","raw_score":100,"explanation":f"存在可靠的{category_name}无记录证据。"}
    if status in {"historical_removed","resolved","minor_remediated","history_resolved"}:return {"status":"SCORED","raw_score":70,"explanation":f"历史{category_name}记录已解除或整改。"}
    if status in {"minor","limited","few","open_minor"}:return {"status":"SCORED","raw_score":40,"explanation":f"存在轻度{category_name}记录。"}
    if status in {"current","major","severe","active","bankruptcy","liquidation"} or records:return {"status":"SCORED","raw_score":0 if status in {"current","major","severe","bankruptcy","liquidation"} else 40,"explanation":f"发现有效{category_name}风险记录。"}
    return {"status":"INSUFFICIENT_EVIDENCE","raw_score":None,"explanation":f"{category_name}结果无法可靠解释。"}

def evidence_reference_score(feature:dict[str,Any],indicator:dict[str,Any])->dict[str,Any]:
    fields=(feature.get("value") or {}).get("fields") or {}
    if not fields:
        return {"status":"MISSING_DATA","raw_score":0,"explanation":"目前没有找到可核验的相关资料，本项暂记 0 分；补充资料后系统会重新评价。","missing_fields":feature.get("missing_fields") or [indicator["indicator_code"]]}
    count=len(fields)
    score=100 if count>=4 else 70 if count>=2 else 40
    names="、".join(str(name) for name in list(fields)[:3])
    if indicator.get("primary_code")=="C":
        serialized=str(fields)
        adverse=any(word in serialized for word in ("失信","处罚","异常","被执行","限制","破产","注销","冻结","负面","撤销","失效","未履行"))
        score=40 if adverse else 100
        conclusion="资料中发现需关注的风险表述" if adverse else "现有已核验资料未发现明确风险记录"
        return {"status":"SCORED","raw_score":score,"explanation":f"{conclusion}，本项得 {score} 分。"}
    level="较完整" if score==100 else "基本完整" if score==70 else "较少"
    return {"status":"SCORED","raw_score":score,"explanation":f"已找到 {count} 类有效资料，包括{names}。当前资料{level}，本项得 {score} 分。"}

@dataclass(frozen=True)
class RuleRegistration:
    indicator_code:str; rule_status:str; feature_key:str|None; function:RuleFunction|None; handler_id:str; resolvability:str

class EvaluationRuleRegistry:
    version="evaluation-rules/3.0.0"
    RISK_CODES={
        "C1.1":("dishonesty","失信被执行"),"C1.2":("enforcement","被执行"),"C1.3":("terminal_case","终本案件"),"C1.5":("litigation","诉讼/开庭/法院公告"),
        "C2.1":("administrative_penalty","行政处罚"),"C2.2":("abnormal_operation","严重违法与经营异常"),"C2.3":("tax_violation","税务违法/欠税/非正常户"),
        "C2.4":("environment_safety","环保处罚与安全生产"),"C2.5":("bankruptcy_liquidation","破产清算注销"),"C3.1":("pledge_freeze","质押抵押冻结"),
        "C3.2":("key_person","关键人员风险"),"C3.3":("negative_news","负面新闻舆情"),"C3.5":("supply_chain","供应链集中异常"),
    }
    def __init__(self,indicators:list[dict[str,Any]])->None:
        self._registrations={}
        direct={
            "A1.3":("capital",capital,"capital_rule/3.0.0"),
            "A3.2":("tax_credit",tax_credit,"tax_credit_rule/3.0.0"),
            "A3.3":("qualification_disclosure",qualification_disclosure,"qualification_disclosure_rule/3.0.0"),
            "C3.4":("qualification_risk",qualification_warning,"qualification_warning_rule/3.0.0"),
        }
        for ind in indicators:
            code=ind["indicator_code"]
            if not ind.get("active",ind.get("selection_status")!="DELETE_CANDIDATE"):
                reg=RuleRegistration(code,"EXCLUDED_BY_MODEL_SELECTION",None,None,"ExcludedIndicatorHandler/1.0.0","NOT_APPLICABLE")
            elif code in direct:
                key,func,hid=direct[code]
                reg=RuleRegistration(code,"DETERMINISTIC_IMPLEMENTED",key,func,hid,"DATA_RESOLVABLE")
            elif code in self.RISK_CODES:
                key,name=self.RISK_CODES[code]
                reg=RuleRegistration(code,"DETERMINISTIC_IMPLEMENTED",f"risk:{key}",lambda feature,risk_name=name:risk_rule(feature,risk_name),f"standard_risk_rule:{key}/3.0.0","DATA_RESOLVABLE")
            else:
                reg=RuleRegistration(
                    code,"DETERMINISTIC_IMPLEMENTED",f"indicator:{code}",
                    lambda feature,indicator=ind:evidence_reference_score(feature,indicator),
                    "WorkbookScoringRule/3.0.0","DATA_RESOLVABLE",
                )
            self._registrations[code]=reg
        if len(self._registrations)!=60:raise ValueError(f"Expected 60 registered rules, got {len(self._registrations)}")
        if any(i.get("active",i.get("selection_status")!="DELETE_CANDIDATE") and i["indicator_code"] not in self._registrations for i in indicators):raise ValueError("Active indicator missing registration")
    def get(self,code:str)->RuleRegistration:return self._registrations[code]
    def coverage(self)->list[dict[str,Any]]:
        return [{"indicator_code":r.indicator_code,"rule_status":r.rule_status,"feature_key":r.feature_key,"handler_id":r.handler_id,"resolvability":r.resolvability} for r in self._registrations.values()]
