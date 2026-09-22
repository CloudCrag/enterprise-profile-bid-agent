"""Convert existing temporary inputs into normalized enterprise facts."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Iterable

from .enterprise_fact_profile import build_fact
from .evidence import record_evidence_ids


def _value(node: Any) -> Any:
    return node.get("value") if isinstance(node, dict) and "value" in node else node


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _metadata(nodes: Iterable[Any]) -> tuple[list[str], str | None, str | None, str | None, bool]:
    evidence_ids: list[str] = []
    raw_sources: list[str] = []
    observed_times: list[str] = []
    urls: list[str] = []
    is_mock = False
    for item in nodes:
        for node in _walk(item):
            evidence_ids.extend(record_evidence_ids(node))
            source_type = node.get("source_type")
            if isinstance(source_type, str) and source_type:
                raw_sources.append(source_type)
            observed_at = node.get("available_at") or node.get("updated_at")
            if isinstance(observed_at, str) and observed_at:
                observed_times.append(observed_at)
            source_url = node.get("source_url") or node.get("original_announcement_url")
            if isinstance(source_url, str) and source_url:
                urls.append(source_url)
            is_mock = is_mock or bool(node.get("is_mock"))
    return (
        list(dict.fromkeys(evidence_ids)),
        raw_sources[0] if raw_sources else None,
        max(observed_times) if observed_times else None,
        urls[0] if urls else None,
        is_mock,
    )


def _map_source_type(raw: str | None, *, has_excel_evidence: bool = False) -> str:
    if raw in {"official_api", "business_api"}:
        return "official_api"
    if raw in {"crawler", "crawler_json"}:
        return "crawler"
    if raw == "user_upload":
        return "user_upload"
    if raw in {"manual_review", "manual_input"}:
        return "manual_review"
    if raw in {"company_excel", "legacy_excel"} or has_excel_evidence:
        return "legacy_excel"
    return "external_data"


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _all_input_evidence(input_data: dict[str, Any]) -> list[str]:
    records = input_data.get("source_data", {}).get("tender_records", [])
    if not isinstance(records, list):
        return []
    return list(dict.fromkeys(
        evidence_id
        for record in records if isinstance(record, dict)
        for evidence_id in record_evidence_ids(record)
    ))


def _source(
    *,
    raw_source: str | None,
    source_data_category: str,
    dataset_id: str | None,
    source_record_id: str,
    source_url: str | None,
    evidence_ids: list[str],
) -> dict[str, Any]:
    has_excel = any(eid.startswith("company_excel:") for eid in evidence_ids)
    return {
        "source_type": _map_source_type(raw_source, has_excel_evidence=has_excel),
        "source_data_category": source_data_category,
        "source_platform": raw_source or ("company_excel" if has_excel else None),
        "source_record_id": source_record_id,
        "source_url": source_url,
        "dataset_id": dataset_id,
    }


def _availability_temporal(
    *,
    published_at: str | None = None,
    collected_at: str | None = None,
    observed_at: str | None = None,
    verified_at: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    source_type: str | None = None,
    explicit_available_at: str | None = None,
) -> dict[str, Any]:
    available_at = None
    basis = None
    if explicit_available_at:
        available_at, basis = explicit_available_at, "upstream_provided"
    elif source_type in {"manual_review", "manual_input"} and verified_at:
        available_at, basis = verified_at, "manual_verified_at"
    elif source_type == "user_upload":
        # Only an explicit upstream available/received time is acceptable for uploads.
        available_at, basis = None, None
    elif published_at:
        available_at, basis = published_at, "source_published_at"
    elif collected_at:
        available_at, basis = collected_at, "source_collected_at"
    elif observed_at:
        available_at, basis = observed_at, "source_observed_at"
    return {
        "published_at": published_at,
        "collected_at": collected_at,
        "verified_at": verified_at,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "observed_at": observed_at,
        "available_at": available_at,
        "availability_basis": basis,
        "availability_status": "known" if available_at else "unknown",
    }


def _record_temporal(record: dict[str, Any]) -> dict[str, Any]:
    record_time = record.get("record_time") if isinstance(record.get("record_time"), dict) else {}
    times = record.get("times") if isinstance(record.get("times"), dict) else {}
    published = record_time.get("published_at") or times.get("announcement_published_at") or times.get("published_at")
    return _availability_temporal(
        published_at=published,
        collected_at=record.get("collected_at"),
        observed_at=record.get("updated_at"),
        valid_until=record_time.get("tender_end_at") or times.get("bid_end"),
        source_type=record.get("source_type"),
        explicit_available_at=record.get("available_at"),
    )


def _observed_temporal(observed_at: str | None, raw_source: str | None, *, valid_from: str | None = None, valid_until: str | None = None) -> dict[str, Any]:
    return _availability_temporal(
        observed_at=observed_at,
        valid_from=valid_from,
        valid_until=valid_until,
        source_type=raw_source,
    )


def convert_legacy_input_to_facts(input_data: dict[str, Any], as_of_date: str | None) -> list[dict[str, Any]]:
    enterprise = input_data["enterprise"]
    meta = input_data.get("_meta") if isinstance(input_data.get("_meta"), dict) else {}
    dataset_id = meta.get("source_dataset_id") or "legacy_enterprise_input"
    anonymized = bool(meta.get("anonymized"))
    global_mock = bool(meta.get("mock_data") or anonymized)
    core = input_data.get("core_input") if isinstance(input_data.get("core_input"), dict) else {}
    facts: list[dict[str, Any]] = []

    identity = core.get("identity") if isinstance(core.get("identity"), dict) else {}
    operation = core.get("operation") if isinstance(core.get("operation"), dict) else {}
    business = core.get("business") if isinstance(core.get("business"), dict) else {}
    scale = core.get("scale") if isinstance(core.get("scale"), dict) else {}
    registration_nodes = [identity, operation, business, scale]
    ids, raw_source, observed_at, url, node_mock = _metadata(registration_nodes)
    registration_payload = {
        "enterprise_name": _value(identity.get("enterprise_name")) or enterprise.get("name"),
        "unified_social_credit_code": _value(identity.get("unified_social_credit_code")) or enterprise.get("unified_social_credit_code"),
        "legal_representative": _value(identity.get("legal_representative")),
        "verification_result": _value(identity.get("verification_result") or identity.get("verification_category")),
        "verification_elements": _value(identity.get("verification_elements")),
        "operation_status_raw": _value(operation.get("status_raw")),
        "normalized_operation_status": _value(operation.get("normalized_status") or operation.get("status_category")),
        "established_date": _value(operation.get("established_date")),
        "registered_industry": _value(business.get("industry")),
        "business_scope": _value(business.get("business_scope")),
        "registered_capital": _value(scale.get("registered_capital")),
        "paid_in_capital": _value(scale.get("paid_in_capital")),
        "enterprise_size_classification": _value(scale.get("enterprise_size_classification")),
    }
    explicit_registration_values = [
        _value(identity.get("enterprise_name")), _value(identity.get("unified_social_credit_code")),
        _value(identity.get("legal_representative")), _value(identity.get("verification_result") or identity.get("verification_category")),
        _value(identity.get("verification_elements")), _value(operation.get("status_raw")),
        _value(operation.get("normalized_status") or operation.get("status_category")), _value(operation.get("established_date")),
        _value(business.get("industry")), _value(business.get("business_scope")),
        _value(scale.get("registered_capital")), _value(scale.get("paid_in_capital")),
        _value(scale.get("enterprise_size_classification")),
    ]
    if any(_present(value) for value in explicit_registration_values):
        quality = [] if registration_payload["verification_result"] else ["authority_verification_not_provided"]
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="business_registration",
            payload=registration_payload,
            source=_source(
                raw_source=raw_source,
                source_data_category="business_registration",
                dataset_id=dataset_id,
                source_record_id=ids[0] if ids else f"enterprise-registration:{enterprise.get('unified_social_credit_code') or enterprise.get('name')}",
                source_url=url,
                evidence_ids=ids,
            ),
            temporal=_observed_temporal(observed_at, raw_source),
            verification_status="partially_verified" if registration_payload["verification_result"] else "unverified",
            evidence_ids=ids,
            quality_flags=quality + ([] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"]),
            is_mock=global_mock or node_mock,
        ))
    else:
        observation_ids = _all_input_evidence(input_data)
        tender_records = input_data.get("source_data", {}).get("tender_records", [])
        first_record = next((item for item in tender_records if isinstance(item, dict)), None) if isinstance(tender_records, list) else None
        if observation_ids and (enterprise.get("name") or enterprise.get("unified_social_credit_code")):
            observation_temporal = _record_temporal(first_record or {})
            facts.append(build_fact(
                enterprise=enterprise,
                fact_type="other_enterprise_fact",
                payload={
                    "subtype": "enterprise_identity_observation",
                    "enterprise_name": enterprise.get("name"),
                    "unified_social_credit_code": enterprise.get("unified_social_credit_code"),
                    "observation_context": "tender_history_record",
                },
                source=_source(
                    raw_source=(first_record or {}).get("source_type") or "company_excel",
                    source_data_category="tender_notice",
                    dataset_id=(first_record or {}).get("source_dataset_id") or dataset_id,
                    source_record_id=observation_ids[0],
                    source_url=(first_record or {}).get("source_url") or (first_record or {}).get("original_announcement_url"),
                    evidence_ids=observation_ids,
                ),
                temporal=observation_temporal,
                verification_status="unverified",
                evidence_ids=observation_ids,
                quality_flags=[
                    "identity_observed_in_tender_dataset",
                    "business_registration_not_verified",
                    *( ["availability_time_not_provided"] if observation_temporal["availability_status"] != "known" else [] ),
                ],
                is_mock=global_mock,
            ))

    qualifications = core.get("qualifications") if isinstance(core.get("qualifications"), list) else []
    for item in qualifications:
        if not isinstance(item, dict) or not _present(item.get("name")):
            continue
        ids, raw_source, observed_at, url, item_mock = _metadata([item])
        payload = {
            "name": item.get("name"),
            "qualification_level": item.get("qualification_level", item.get("level")),
            "certificate_number": item.get("certificate_number"),
            "status": item.get("status"),
            "issue_date": item.get("issue_date"),
            "valid_until": item.get("expiry_date") or item.get("valid_until"),
            "issuing_authority": item.get("issuing_authority"),
        }
        lifecycle = "active"
        if item.get("status") in {"expired", "invalid"}:
            lifecycle = "expired"
        elif item.get("status") in {"revoked", "cancelled"}:
            lifecycle = "revoked"
        else:
            expiry, reference = _parse_date(payload["valid_until"]), _parse_date(as_of_date)
            if expiry and reference and expiry < reference:
                lifecycle = "expired"
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="qualification",
            payload=payload,
            source=_source(
                raw_source=raw_source,
                source_data_category="qualification_registry",
                dataset_id=dataset_id,
                source_record_id=ids[0] if ids else f"qualification:{item.get('name')}:{payload.get('certificate_number') or payload.get('issue_date') or payload.get('valid_until')}",
                source_url=url,
                evidence_ids=ids,
            ),
            temporal=_observed_temporal(observed_at, raw_source, valid_from=item.get("issue_date"), valid_until=payload.get("valid_until")),
            fact_status=lifecycle,
            verification_status="partially_verified" if ids else "unverified",
            evidence_ids=ids,
            quality_flags=[] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"],
            is_mock=global_mock or item_mock,
        ))

    organization = core.get("organization") if isinstance(core.get("organization"), dict) else {}
    personnel_nodes = [organization.get("employee_count"), organization.get("social_insurance_count")]
    if any(_present(_value(node)) for node in personnel_nodes):
        ids, raw_source, observed_at, url, item_mock = _metadata(personnel_nodes)
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="personnel",
            payload={
                "employee_count": _value(personnel_nodes[0]),
                "social_insurance_count": _value(personnel_nodes[1]),
                "count_scope": "enterprise_aggregate",
            },
            source=_source(raw_source=raw_source, source_data_category="personnel_registry", dataset_id=dataset_id, source_record_id="personnel:aggregate", source_url=url, evidence_ids=ids),
            temporal=_observed_temporal(observed_at, raw_source),
            verification_status="partially_verified" if ids else "unverified",
            evidence_ids=ids,
            quality_flags=[] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"],
            is_mock=global_mock or item_mock,
        ))

    product_nodes = [business.get("business_description"), business.get("products"), business.get("product_business_tags")]
    if any(_present(_value(node)) for node in product_nodes):
        ids, raw_source, observed_at, url, item_mock = _metadata(product_nodes)
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="other_enterprise_fact",
            payload={
                "subtype": "product_business",
                "business_description": _value(product_nodes[0]),
                "products": _value(product_nodes[1]) or [],
                "product_business_tags": _value(product_nodes[2]) or [],
            },
            source=_source(raw_source=raw_source, source_data_category="other", dataset_id=dataset_id, source_record_id="other:product_business", source_url=url, evidence_ids=ids),
            temporal=_observed_temporal(observed_at, raw_source),
            verification_status="unverified",
            evidence_ids=ids,
            quality_flags=[] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"],
            is_mock=global_mock or item_mock,
        ))

    branches = organization.get("branches") if isinstance(organization.get("branches"), list) else []
    service_regions = organization.get("service_regions")
    if branches or _present(_value(service_regions)):
        ids, raw_source, observed_at, url, item_mock = _metadata([branches, service_regions])
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="other_enterprise_fact",
            payload={"subtype": "organization_network", "branches": deepcopy(branches), "service_regions": _value(service_regions) or []},
            source=_source(raw_source=raw_source, source_data_category="other", dataset_id=dataset_id, source_record_id="other:organization_network", source_url=url, evidence_ids=ids),
            temporal=_observed_temporal(observed_at, raw_source),
            verification_status="unverified",
            evidence_ids=ids,
            quality_flags=[] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"],
            is_mock=global_mock or item_mock,
        ))

    records = input_data.get("source_data", {}).get("tender_records", [])
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            ids = record_evidence_ids(record)
            raw_source = record.get("source_type")
            source_record_id = record.get("announcement_unique_id") or record.get("project_number") or (ids[0] if ids else f"tender-row:{record.get('source_row_number')}")
            quality = list(record.get("quality_flags") or [])
            role = record.get("role") if isinstance(record.get("role"), dict) else {}
            if role.get("status") != "confirmed":
                quality.append("role_evidence_insufficient")
            if record.get("collected_at") is None:
                quality.append("collection_time_not_provided")
            duplicate = record.get("duplicate_flags") if isinstance(record.get("duplicate_flags"), dict) else {}
            if duplicate.get("exact_duplicate") or int(duplicate.get("exact_duplicate_occurrence") or 1) > 1:
                quality.append("exact_duplicate_marked")
            record_status = record.get("record_status") or "valid"
            lifecycle = "ambiguous" if record_status == "invalid_record" else "active"
            amounts = record.get("amounts") if isinstance(record.get("amounts"), dict) else {}
            temporal = _record_temporal(record)
            if temporal["availability_status"] != "known":
                quality.append("availability_time_not_provided")
            facts.append(build_fact(
                enterprise=enterprise,
                fact_type="bid_participation",
                payload={
                    "project_name": record.get("project_name"),
                    "project_number": record.get("project_number"),
                    "announcement_unique_id": record.get("announcement_unique_id"),
                    "project_classification": record.get("project_classification"),
                    "project_industry": record.get("industry_classification"),
                    "project_region": deepcopy(record.get("region")),
                    "procurement_method": (record.get("raw_fields") or {}).get("采购方式") if isinstance(record.get("raw_fields"), dict) else record.get("procurement_method"),
                    "project_budget": deepcopy(amounts.get("project_budget")),
                    "unconfirmed_winning_amount_observation": deepcopy(amounts.get("winning_total_raw")),
                    "role": deepcopy(role),
                    "record_status": record_status,
                    "duplicate_flags": deepcopy(duplicate),
                    "buyer_and_agency_raw": record.get("buyer_and_agency_raw"),
                },
                source=_source(
                    raw_source=raw_source,
                    source_data_category="tender_notice",
                    dataset_id=record.get("source_dataset_id") or dataset_id,
                    source_record_id=str(source_record_id),
                    source_url=record.get("source_url") or record.get("original_announcement_url"),
                    evidence_ids=ids,
                ),
                temporal=temporal,
                fact_status=lifecycle,
                verification_status="unverified",
                evidence_ids=ids,
                quality_flags=quality,
                is_mock=global_mock or bool(record.get("is_mock")),
            ))

    explicit_awards = input_data.get("source_data", {}).get("bid_award_records", [])
    if isinstance(explicit_awards, list):
        for award in explicit_awards:
            if not isinstance(award, dict) or award.get("role_evidence_status") != "confirmed":
                continue
            ids = record_evidence_ids(award)
            raw_temporal = deepcopy(award.get("temporal") or {})
            temporal = _availability_temporal(
                published_at=raw_temporal.get("published_at"),
                collected_at=raw_temporal.get("collected_at"),
                observed_at=raw_temporal.get("observed_at"),
                verified_at=raw_temporal.get("verified_at"),
                valid_from=raw_temporal.get("valid_from"),
                valid_until=raw_temporal.get("valid_until"),
                source_type=award.get("source_type"),
                explicit_available_at=raw_temporal.get("available_at"),
            )
            facts.append(build_fact(
                enterprise=enterprise,
                fact_type="bid_award",
                payload=deepcopy(award.get("payload") or {}),
                source=_source(
                    raw_source=award.get("source_type"), source_data_category="award_notice",
                    dataset_id=award.get("source_dataset_id") or dataset_id,
                    source_record_id=award.get("source_record_id") or (ids[0] if ids else "explicit-award"),
                    source_url=award.get("source_url"), evidence_ids=ids,
                ),
                temporal=temporal,
                verification_status="verified",
                evidence_ids=ids,
                quality_flags=[] if temporal["availability_status"] == "known" else ["availability_time_not_provided"],
                is_mock=global_mock or bool(award.get("is_mock")),
            ))

    risks = core.get("risks") if isinstance(core.get("risks"), dict) else {}
    risk_specs = [
        ("dishonesty", risks.get("dishonesty_status"), risks.get("dishonesty_records")),
        ("administrative_penalty", risks.get("penalty_status"), risks.get("administrative_penalties")),
        ("abnormal_operation", risks.get("abnormal_operation_status"), risks.get("abnormal_operation_records")),
        ("bankruptcy_liquidation", risks.get("bankruptcy_status"), risks.get("bankruptcy_records") or risks.get("bankruptcy_liquidation_records")),
    ]
    for category, status_node, records_node in risk_specs:
        status_value, records_value = _value(status_node), _value(records_node) or []
        if not _present(status_value) and not records_value:
            continue
        extra_nodes = [status_node, records_node]
        if category == "administrative_penalty":
            extra_nodes.extend([risks.get("penalty_severity"), risks.get("penalty_remediation_status")])
        ids, raw_source, observed_at, url, item_mock = _metadata(extra_nodes)
        payload = {"category": category, "status": status_value, "records": deepcopy(records_value)}
        if category == "administrative_penalty":
            payload["severity_raw"] = _value(risks.get("penalty_severity"))
            payload["remediation_status"] = _value(risks.get("penalty_remediation_status"))
        facts.append(build_fact(
            enterprise=enterprise,
            fact_type="risk_penalty_credit",
            payload=payload,
            source=_source(raw_source=raw_source, source_data_category="risk_credit", dataset_id=dataset_id, source_record_id=f"risk:{category}", source_url=url, evidence_ids=ids),
            temporal=_observed_temporal(observed_at, raw_source),
            verification_status="partially_verified" if ids else "unverified",
            evidence_ids=ids,
            quality_flags=[] if observed_at else ["collection_or_observation_time_not_provided", "availability_time_not_provided"],
            is_mock=global_mock or item_mock,
        ))

    return facts
