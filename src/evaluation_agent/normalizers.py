"""Approved provider-response normalizers.

A provider response is evidence input, not a scoring feature.  Every normalizer
maps an API-specific response into one of the existing enterprise fact candidate
schemas.  Unknown APIs and unsupported payloads fail closed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from src.evaluation_model import content_hash


class ApiResponseNormalizer(Protocol):
    api_id: str
    supported_fact_types: tuple[str, ...]

    def normalize(
        self,
        *,
        company_id: str,
        enterprise: dict[str, Any],
        response: dict[str, Any],
        source_context: dict[str, Any],
    ) -> list[dict[str, Any]]: ...


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop only absent values; false/zero remain meaningful."""
    return {key: value for key, value in payload.items() if value is not None}


def _amount(value: Any, *, currency: Any = None, unit: Any = None) -> Any:
    """Map a provider amount to the existing strict amount shape."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        raw_value = value.get("raw_value", value.get("value"))
        mapped = {
            "value": value.get("value"),
            "raw_value": raw_value,
            "value_parse_status": value.get("value_parse_status"),
            "unit_raw": value.get("unit_raw", value.get("unit", unit)),
            "unit_normalized": value.get("unit_normalized"),
            "currency_raw": value.get("currency_raw", value.get("currency", currency)),
            "semantic_status": value.get("semantic_status"),
        }
        return _compact(mapped)
    return _compact(
        {
            "value": value,
            "raw_value": value,
            "unit_raw": unit,
            "currency_raw": currency,
        }
    )




def _strict_amount(value: Any, *, currency: Any = None, unit: Any = None) -> dict[str, Any] | None:
    """Map to the canonical amount object used by award/performance facts."""
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("raw_value", value.get("value"))
        parsed_value = value.get("value")
        source_unit = value.get("unit_raw", value.get("unit", unit))
        source_currency = value.get("currency_raw", value.get("currency", currency))
    else:
        raw = value
        parsed_value = value
        source_unit = unit
        source_currency = currency
    numeric = parsed_value if isinstance(parsed_value, (int, float)) else None
    if numeric is not None and source_unit and source_currency:
        normalization_status = "normalized"
    elif numeric is not None and not source_unit:
        normalization_status = "missing_unit"
    elif numeric is not None and not source_currency:
        normalization_status = "missing_currency"
    else:
        normalization_status = "not_normalized"
    return {
        "raw_value": raw,
        "value": parsed_value,
        "unit_raw": source_unit,
        "currency_raw": source_currency,
        "normalized_value": numeric if normalization_status == "normalized" else None,
        "normalized_unit": str(source_unit) if normalization_status == "normalized" else None,
        "normalized_currency": str(source_currency) if normalization_status == "normalized" else None,
        "value_parse_status": "parsed" if numeric is not None else "unknown",
        "semantic_status": "confirmed" if normalization_status == "normalized" else "ambiguous",
        "normalization_status": normalization_status,
    }


def _schema_date(value: Any) -> Any:
    """Avoid the schema format overlap where a bare date also matches iso-date-time."""
    if isinstance(value, str) and len(value) == 10 and value[4:5] == "-" and value[7:8] == "-":
        return f"{value}T00:00:00+00:00"
    return value


def _submission(
    *,
    company_id: str,
    fact_type: str,
    payload: dict[str, Any],
    source_context: dict[str, Any],
    record_key: str,
) -> dict[str, Any]:
    provider_id = str(source_context["provider_id"])
    api_id = str(source_context["api_id"])
    source_record_id = str(source_context.get("source_record_id") or record_key)
    is_mock = False
    return {
        "submission_id": f"api-fact-submission:{content_hash([company_id, provider_id, api_id, source_record_id, fact_type, payload])[:24]}",
        "company_id": company_id,
        "source_type": "external_data",
        "source_system": source_context.get("source_system") or provider_id,
        "source_record_id": source_record_id,
        "source_url": source_context.get("source_url"),
        "collected_at": source_context["collected_at"],
        "fact_type": fact_type,
        "payload": deepcopy(payload),
        "material_refs": [],
        "submitted_by": {
            "actor_type": "system",
            "actor_id": "enterprise-api-normalizer",
            "display_name": "企业 API 标准化器",
        },
        "idempotency_key": content_hash(
            [company_id, provider_id, api_id, source_record_id, fact_type, payload]
        ),
        "provider_id": provider_id,
        "api_id": api_id,
        "api_call_id": source_context.get("api_call_id"),
        "raw_response_ref": source_context.get("raw_response_ref"),
        "is_mock": is_mock,
        "normalizer_id": source_context.get("normalizer_id"),
    }


@dataclass
class BaseNormalizer:
    api_id: str
    supported_fact_types: tuple[str, ...]
    normalizer_id: str


class BusinessRegistrationNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        item = data.get("enterprise") or data
        payload = _compact(
            {
                "enterprise_name": item.get("enterprise_name") or item.get("company_name") or enterprise.get("name"),
                "unified_social_credit_code": item.get("unified_social_credit_code") or enterprise.get("unified_social_credit_code"),
                "legal_representative": item.get("legal_representative"),
                "verification_result": item.get("verification_result"),
                "verification_elements": item.get("verification_elements"),
                "operation_status_raw": item.get("operation_status_raw") or item.get("operating_status"),
                "normalized_operation_status": item.get("normalized_operation_status"),
                "established_date": _schema_date(item.get("established_date")),
                "registered_industry": item.get("registered_industry") or item.get("industry"),
                "business_scope": item.get("business_scope"),
                "enterprise_size_classification": item.get("enterprise_size_classification"),
            }
        )
        if not payload.get("enterprise_name") and not payload.get("unified_social_credit_code"):
            return []
        return [
            _submission(
                company_id=company_id,
                fact_type="business_registration",
                payload=payload,
                source_context={**source_context, "normalizer_id": self.normalizer_id},
                record_key="registration",
            )
        ]


class CapitalNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        item = data.get("enterprise") or data
        registered = item.get("registered_capital")
        paid = item.get("paid_in_capital")
        if registered is None and paid is None:
            return []
        payload = _compact(
            {
                "enterprise_name": item.get("enterprise_name") or item.get("company_name") or enterprise.get("name"),
                "unified_social_credit_code": item.get("unified_social_credit_code") or enterprise.get("unified_social_credit_code"),
                "registered_capital": _amount(
                    registered,
                    currency=item.get("capital_currency", "CNY"),
                    unit=item.get("capital_unit"),
                ),
                "paid_in_capital": _amount(
                    paid,
                    currency=item.get("capital_currency", "CNY"),
                    unit=item.get("capital_unit"),
                ),
            }
        )
        if not payload.get("enterprise_name") and not payload.get("unified_social_credit_code"):
            return []
        return [
            _submission(
                company_id=company_id,
                fact_type="business_registration",
                payload=payload,
                source_context={**source_context, "normalizer_id": self.normalizer_id},
                record_key="capital",
            )
        ]


class QualificationNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        items = data.get("qualifications") or (data.get("enterprise") or {}).get("qualifications") or []
        results: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("qualification_name")
            if not name:
                continue
            payload = _compact(
                {
                    "name": name,
                    "qualification_level": item.get("qualification_level") or item.get("level"),
                    "certificate_number": item.get("certificate_number") or item.get("code"),
                    "status": item.get("status"),
                    "issue_date": _schema_date(item.get("issue_date") or item.get("valid_from")),
                    "valid_until": _schema_date(item.get("valid_until")),
                    "issuing_authority": item.get("issuing_authority") or item.get("issuer"),
                }
            )
            results.append(
                _submission(
                    company_id=company_id,
                    fact_type="qualification",
                    payload=payload,
                    source_context={**source_context, "normalizer_id": self.normalizer_id},
                    record_key=f"qualification:{index}",
                )
            )
        return results


class TaxCreditNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        item = data.get("tax_credit") or (data.get("enterprise") or {}).get("tax_credit")
        if not isinstance(item, dict):
            return []
        rating = item.get("rating_value") or item.get("rating") or item.get("grade") or item.get("level")
        payload = _compact(
            {
                "category": "tax_credit",
                "status": item.get("status"),
                "records": item.get("records"),
                "severity_raw": item.get("severity_raw") or item.get("severity"),
                "remediation_status": item.get("remediation_status"),
                "rating_type": item.get("rating_type") or "tax_credit_rating",
                "rating_value": rating,
                "rating_outlook": item.get("rating_outlook") or item.get("outlook"),
            }
        )
        if not any(key in payload for key in ("status", "records", "rating_value")):
            return []
        return [
            _submission(
                company_id=company_id,
                fact_type="risk_penalty_credit",
                payload=payload,
                source_context={**source_context, "normalizer_id": self.normalizer_id},
                record_key="tax-credit",
            )
        ]


class IntellectualPropertyNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        items = data.get("intellectual_property") or []
        results: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not item:
                continue
            payload = {"subtype": "intellectual_property", "details": deepcopy(item)}
            results.append(
                _submission(
                    company_id=company_id,
                    fact_type="other_enterprise_fact",
                    payload=payload,
                    source_context={**source_context, "normalizer_id": self.normalizer_id},
                    record_key=f"ip:{index}",
                )
            )
        return results


class PersonnelNormalizer(BaseNormalizer):
    _ALLOWED = (
        "employee_count",
        "social_insurance_count",
        "count_scope",
        "person_id",
        "name",
        "role",
        "availability_status",
    )

    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        raw = data.get("personnel") or (data.get("enterprise") or {}).get("personnel")
        if not isinstance(raw, dict):
            return []
        payload = _compact({key: raw.get(key) for key in self._ALLOWED})
        if not any(payload.get(key) is not None for key in ("employee_count", "social_insurance_count", "person_id", "name")):
            return []
        return [
            _submission(
                company_id=company_id,
                fact_type="personnel",
                payload=payload,
                source_context={**source_context, "normalizer_id": self.normalizer_id},
                record_key="personnel",
            )
        ]


class BranchNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        branches = data.get("branches") or []
        if not isinstance(branches, list) or not branches:
            return []
        payload = {"subtype": "organization_network", "branches": deepcopy(branches)}
        return [
            _submission(
                company_id=company_id,
                fact_type="other_enterprise_fact",
                payload=payload,
                source_context={**source_context, "normalizer_id": self.normalizer_id},
                record_key="branches",
            )
        ]


class RiskNormalizer(BaseNormalizer):
    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        risks = data.get("risks") or []
        if isinstance(risks, dict):
            risks = [risks]
        results: list[dict[str, Any]] = []
        for index, item in enumerate(risks):
            if not isinstance(item, dict) or not item.get("category"):
                continue
            records = item.get("records")
            if records is None and item.get("record") is not None:
                records = [item.get("record")]
            payload = _compact(
                {
                    "category": str(item["category"]),
                    "status": item.get("status") or item.get("current_status"),
                    "records": records,
                    "severity_raw": item.get("severity_raw") or item.get("severity"),
                    "remediation_status": item.get("remediation_status") or item.get("resolution_status"),
                    "rating_type": item.get("rating_type"),
                    "rating_value": item.get("rating_value"),
                    "rating_outlook": item.get("rating_outlook"),
                }
            )
            if not any(key in payload for key in ("status", "records", "rating_value")):
                continue
            results.append(
                _submission(
                    company_id=company_id,
                    fact_type="risk_penalty_credit",
                    payload=payload,
                    source_context={**source_context, "normalizer_id": self.normalizer_id},
                    record_key=f"risk:{index}",
                )
            )
        return results


class BidParticipationNormalizer(BaseNormalizer):
    @staticmethod
    def _role(raw: Any) -> str:
        text = str(raw or "")
        if "候选" in text:
            return "candidate"
        if "投标" in text:
            return "bidder"
        return "participant_unknown"

    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        results: list[dict[str, Any]] = []
        for event in data.get("events") or []:
            if not isinstance(event, dict) or event.get("fact_candidate_policy") != "BID_PARTICIPATION_ONLY":
                continue
            project = event.get("project") or {}
            record_id = str(event.get("source_record_id") or "")
            if not record_id:
                continue
            payload = _compact(
                {
                    "project_name": project.get("project_name"),
                    "project_number": project.get("project_number") or project.get("project_code"),
                    "announcement_unique_id": record_id,
                    "project_classification": project.get("project_classification"),
                    "project_industry": project.get("industry"),
                    "project_region": project.get("region") or project.get("city"),
                    "procurement_method": project.get("procurement_method"),
                    "project_budget": _amount(project.get("budget_amount_cny")) if project.get("budget_amount_cny") is not None else None,
                    "role": {
                        "status": self._role(event.get("enterprise_role")),
                        "raw_value": event.get("enterprise_role"),
                        "reason": "来源仅证明参与角色，不证明最终中标。",
                    },
                    "record_status": event.get("role_verification_status") or "SOURCE_DECLARED",
                    "buyer_and_agency_raw": (event.get("mixed_parties") or {}).get("buyer_and_agent_raw"),
                }
            )
            results.append(
                _submission(
                    company_id=company_id,
                    fact_type="bid_participation",
                    payload=payload,
                    source_context={
                        **source_context,
                        "source_record_id": record_id,
                        "source_url": event.get("source_url") or source_context.get("source_url"),
                        "normalizer_id": self.normalizer_id,
                    },
                    record_key=record_id,
                )
            )
        return results


class BidAwardNormalizer(BaseNormalizer):
    _WINNER_ROLES = {"winner", "final_winner", "中标人", "成交供应商"}

    def normalize(self, *, company_id: str, enterprise: dict[str, Any], response: dict[str, Any], source_context: dict[str, Any]) -> list[dict[str, Any]]:
        data = response.get("data") or {}
        awards = data.get("awards") or []
        results: list[dict[str, Any]] = []
        credit_code = enterprise.get("unified_social_credit_code")
        for index, item in enumerate(awards):
            if not isinstance(item, dict):
                continue
            role = item.get("award_role") or item.get("role")
            if role not in self._WINNER_ROLES:
                continue
            if credit_code and item.get("unified_social_credit_code") not in {None, credit_code}:
                continue
            payload = _compact(
                {
                    "project_name": item.get("project_name"),
                    "project_number": item.get("project_number") or item.get("project_code"),
                    "announcement_unique_id": item.get("announcement_unique_id") or item.get("source_record_id"),
                    "award_role": str(role),
                    "award_amount": _strict_amount(
                        item.get("award_amount") if item.get("award_amount") is not None else item.get("winning_amount"),
                        currency=item.get("currency"),
                        unit=item.get("unit"),
                    ),
                    "award_date": _schema_date(item.get("award_date") or item.get("announcement_date")),
                    "buyer_name": item.get("buyer_name") or item.get("purchaser_name"),
                }
            )
            if not any(payload.get(key) for key in ("announcement_unique_id", "project_number", "project_name")):
                continue
            results.append(
                _submission(
                    company_id=company_id,
                    fact_type="bid_award",
                    payload=payload,
                    source_context={**source_context, "normalizer_id": self.normalizer_id},
                    record_key=f"award:{index}",
                )
            )
        return results


class ApiNormalizerRegistry:
    """Route an API id to an approved normalizer; unknown APIs fail closed."""

    def __init__(self, api_catalog: dict[str, Any] | None = None) -> None:
        self._normalizers: dict[str, ApiResponseNormalizer] = {}
        for item in (api_catalog or {}).get("apis", []):
            api_id = str(item.get("api_id"))
            text = " ".join(
                str(item.get(key) or "")
                for key in (
                    "enterprise_data_category",
                    "data_dimension",
                    "api_name",
                    "description",
                    "output_fields",
                )
            )
            normalizer_class: type[BaseNormalizer] | None = None
            fact_types: tuple[str, ...] = ()
            if any(keyword in text for keyword in ("注册资本", "实缴资本")):
                normalizer_class, fact_types = CapitalNormalizer, ("business_registration",)
            elif any(keyword in text for keyword in ("工商", "主体", "企业基本", "企业信息")):
                normalizer_class, fact_types = BusinessRegistrationNormalizer, ("business_registration",)
            elif any(keyword in text for keyword in ("资质", "许可", "证书")):
                normalizer_class, fact_types = QualificationNormalizer, ("qualification",)
            elif "税务信用" in text:
                normalizer_class, fact_types = TaxCreditNormalizer, ("risk_penalty_credit",)
            elif any(keyword in text for keyword in ("专利", "著作权", "商标")):
                normalizer_class, fact_types = IntellectualPropertyNormalizer, ("other_enterprise_fact",)
            elif any(keyword in text for keyword in ("员工", "社保", "人员")):
                normalizer_class, fact_types = PersonnelNormalizer, ("personnel",)
            elif "分支" in text:
                normalizer_class, fact_types = BranchNormalizer, ("other_enterprise_fact",)
            elif any(keyword in text for keyword in ("失信", "被执行", "处罚", "异常", "欠税", "环保", "破产", "冻结", "负面")):
                normalizer_class, fact_types = RiskNormalizer, ("risk_penalty_credit",)
            elif any(keyword in text for keyword in ("中标", "成交")):
                normalizer_class, fact_types = BidAwardNormalizer, ("bid_award",)
            elif any(keyword in text for keyword in ("投标", "招投标")):
                normalizer_class, fact_types = BidParticipationNormalizer, ("bid_participation",)
            if normalizer_class:
                self._normalizers[api_id] = normalizer_class(
                    api_id=api_id,
                    supported_fact_types=fact_types,
                    normalizer_id=f"{normalizer_class.__name__}/2.0.0",
                )

    def register(self, normalizer: ApiResponseNormalizer) -> None:
        self._normalizers[str(normalizer.api_id)] = normalizer

    def get(self, api_id: str, response: dict[str, Any] | None = None) -> ApiResponseNormalizer:
        key = str(api_id)
        data = (response or {}).get("data") or {}
        if any(
            isinstance(event, dict)
            and event.get("fact_candidate_policy") == "BID_PARTICIPATION_ONLY"
            for event in data.get("events") or []
        ):
            return BidParticipationNormalizer(
                api_id=key,
                supported_fact_types=("bid_participation",),
                normalizer_id="BidParticipationNormalizer/2.0.0",
            )
        if key in self._normalizers:
            return self._normalizers[key]
        raise KeyError(f"NORMALIZER_NOT_IMPLEMENTED:{key}")

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "api_id": key,
                "normalizer_id": normalizer.normalizer_id,
                "supported_fact_types": list(normalizer.supported_fact_types),
            }
            for key, normalizer in sorted(self._normalizers.items())
        ]
