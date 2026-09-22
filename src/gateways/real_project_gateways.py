"""Read-only real-project gateways for the unified bid-decision workflow.

Enterprise profile/evaluation data remains owned by the host profile product.
This adapter reads project and notice facts from the team MySQL database and
never writes to that database. Empty relationship tables are represented as
unknown competition data instead of invented competitors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pymysql

from src.gateways.profile_service_gateways import (
    NodeEnvelopeClient,
    ProfileServiceCompanyGateway,
    ProfileServiceEvaluationGateway,
    ProfileServiceUpdateGateway,
)
from src.gateways.runtime_persistence import RuntimeAuditGateway, RuntimePersistenceGateway
from src.shared.errors import GatewayError
from src.gateways.qualification_normalization import (
    atomize_requirement_blocks,
    required_material_labels,
    validate_structured_supplement,
)
from src.shared.schemas import (
    CompetitionDataSnapshot,
    CompetitorRef,
    EligibilityItemResult,
    EligibilityResult,
    EligibilityStatus,
    ProjectSnapshot,
    RankedProjectCandidate,
    WinOpportunityResult,
)


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class RealProjectStore:
    def __init__(self, database_url: str, *, timeout_seconds: float = 15.0):
        if not database_url:
            raise GatewayError(
                "provider_not_configured",
                "未配置真实项目数据库连接。",
                {"required_configuration": "REAL_DATABASE_URL"},
            )
        parsed = urlparse(database_url)
        self.connection_options = {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 13306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": parsed.path.lstrip("/") or "project_recommendation",
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": max(2, int(timeout_seconds)),
            "read_timeout": max(5, int(timeout_seconds)),
        }
        self.project_cache: dict[int, dict[str, Any]] = {}

    def _connection(self):
        try:
            return pymysql.connect(**self.connection_options)
        except Exception as exc:
            raise GatewayError(
                "provider_not_configured",
                "真实项目数据库暂时不可用，请确认 SSH 隧道保持连接。",
                {"provider": "REAL_PROJECT_DATABASE", "type": type(exc).__name__},
            ) from exc

    def search_projects(
        self,
        *,
        query: str = "",
        page: int = 1,
        page_size: int = 20,
        open_only: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        where = []
        params: list[Any] = []
        if query.strip():
            token = f"%{query.strip()}%"
            where.append(
                "(p.project_name LIKE %s OR p.industry LIKE %s OR p.province LIKE %s "
                "OR p.city LIKE %s OR p.owner_company_name LIKE %s)"
            )
            params.extend([token] * 5)
        if open_only:
            # bid_open_time is not treated as a bid deadline. Until a semantically
            # confirmed deadline field is available, current projects are selected
            # only by the authoritative project status.
            where.append("p.current_status IN ('TENDER', 'PLAN')")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        count_sql = f"SELECT COUNT(*) AS total FROM project p {clause}"
        offset = (safe_page - 1) * safe_page_size
        query_params = [*params, safe_page_size, offset]
        sql = f"""
            SELECT p.*, pn.id AS notice_id, pn.structured_data,
                   pn.title AS notice_title, pn.publish_date AS notice_publish_date
            FROM project p
            LEFT JOIN project_notice pn ON pn.id = (
                SELECT pn2.id FROM project_notice pn2
                WHERE pn2.project_id = p.id
                ORDER BY pn2.publish_date DESC, pn2.id DESC LIMIT 1
            )
            {clause}
            ORDER BY
                CASE WHEN p.current_status = 'TENDER' THEN 0 ELSE 1 END,
                p.updated_at DESC, p.id DESC
            LIMIT %s OFFSET %s
        """
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(count_sql, params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(sql, query_params)
            rows = list(cursor.fetchall())
        for row in rows:
            self.project_cache[int(row["id"])] = row
        return rows, total

    def probe(self) -> dict[str, Any]:
        """Minimal read-only startup probe required by the Windows launcher."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            row = cursor.fetchone() or {}
        projects, total = self.search_projects(page=1, page_size=1, open_only=True)
        return {"select_one": int(row.get("ok") or 0) == 1, "project_query_ok": True, "current_project_total": total, "sample_count": len(projects)}

    def read_projects(self, project_ids: list[str]) -> list[dict[str, Any]]:
        numeric_ids = []
        for value in project_ids:
            normalized = str(value).removeprefix("db-project-")
            if normalized.isdigit():
                numeric_ids.append(int(normalized))
        if not numeric_ids:
            return []
        cached = {item: self.project_cache[item] for item in numeric_ids if item in self.project_cache}
        missing_ids = [item for item in numeric_ids if item not in cached]
        if not missing_ids:
            return [cached[item] for item in numeric_ids]
        placeholders = ",".join(["%s"] * len(missing_ids))
        sql = f"""
            SELECT p.*, pn.id AS notice_id, pn.structured_data,
                   pn.title AS notice_title, pn.publish_date AS notice_publish_date
            FROM project p
            LEFT JOIN project_notice pn ON pn.id = (
                SELECT pn2.id FROM project_notice pn2
                WHERE pn2.project_id = p.id
                ORDER BY pn2.publish_date DESC, pn2.id DESC LIMIT 1
            )
            WHERE p.id IN ({placeholders})
        """
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, missing_ids)
            rows = {int(row["id"]): row for row in cursor.fetchall()}
        self.project_cache.update(rows)
        cached.update(rows)
        return [cached[item] for item in numeric_ids if item in cached]

    def relation_rows(self, project_id: str) -> list[dict[str, Any]]:
        numeric = str(project_id).removeprefix("db-project-")
        if not numeric.isdigit():
            return []
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT company_id, company_name, relation_type, ranking, is_winner
                    FROM project_company_relation
                    WHERE project_id = %s
                    ORDER BY ranking IS NULL, ranking, id
                    """,
                    (int(numeric),),
                )
                return list(cursor.fetchall())
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                "real_competition_query_failed",
                "竞争关系查询失败，不能按零条数据处理。",
                {"project_id": project_id, "type": type(exc).__name__},
            ) from exc


def _extract_requirements(
    structured: dict[str, Any],
    terms: tuple[str, ...],
    *,
    category: str,
) -> list[dict[str, Any]]:
    return atomize_requirement_blocks(
        structured,
        terms,
        default_category=category,
    )


def _project_status(row: dict[str, Any]) -> str:
    status = _text(row.get("current_status")).upper()
    if status in {"TERMINATED", "CANCELLED"}:
        return "TERMINATED"
    if status == "AWARD":
        return "AWARDED"
    if status in {"TENDER", "PLAN"}:
        return "OPEN"
    return "CLOSED"


def row_to_project(row: dict[str, Any]) -> ProjectSnapshot:
    structured = _json_object(row.get("structured_data"))
    qualification = _extract_requirements(
        structured, ("资格要求", "资质要求", "供应商要求"), category="QUALIFICATION"
    )
    personnel = _extract_requirements(
        structured, ("人员要求", "项目负责人", "项目经理"), category="PERSONNEL"
    )
    performance = _extract_requirements(
        structured, ("业绩要求", "类似项目", "合同业绩"), category="PERFORMANCE"
    )
    technical = [
        item["text"]
        for item in _extract_requirements(
            structured, ("招标内容", "采购内容", "项目范围", "技术要求"), category="TECHNICAL"
        )
    ]
    contract_risk_items = _extract_requirements(
        structured,
        ("合同风险", "付款条件", "支付条件", "违约责任", "履约风险", "保证金", "垫资", "工期要求"),
        category="CONTRACT",
    )
    contract_risks = [item["text"] for item in contract_risk_items]
    bid_open_time = _utc(row.get("bid_open_time")) if row.get("bid_open_time") else None
    project_id = f"db-project-{row['id']}"
    notice_id = row.get("notice_id")
    evidence = [f"mysql:project:{row['id']}"]
    if notice_id:
        evidence.append(f"mysql:project_notice:{notice_id}")
    amount = row.get("tender_amount") or row.get("estimated_amount")
    return ProjectSnapshot(
        project_id=project_id,
        project_version=f"mysql-project-{row['id']}-{_utc(row.get('updated_at')).isoformat()}",
        project_name=_text(row.get("project_name")) or f"项目{row['id']}",
        buyer_id=f"buyer:{_text(row.get('owner_company_id')) or _text(row.get('owner_company_name'))}",
        buyer_name=_text(row.get("owner_company_name")) or "采购人信息待补充",
        region="·".join(filter(None, [_text(row.get("province")), _text(row.get("city"))])) or "地区待补充",
        industry=_text(row.get("industry")) or "行业待补充",
        project_type=_text(row.get("project_type")) or "项目类型待补充",
        budget=Decimal(str(amount)) if amount is not None else None,
        maximum_price=Decimal(str(row.get("tender_amount"))) if row.get("tender_amount") is not None else None,
        bid_deadline=None,
        bid_open_time=bid_open_time,
        time_field_note="数据库仅提供bid_open_time，当前按开标时间展示；尚未确认投标截止时间。",
        project_status=_project_status(row),
        qualification_requirements=qualification,
        personnel_requirements=personnel,
        performance_requirements=performance,
        technical_scope=technical,
        contract_risks=contract_risks,
        contract_risk_data_available=bool(contract_risk_items),
        evidence_ids=evidence,
        as_of_time=_utc(row.get("updated_at")),
    )


class RealProjectGateway:
    def __init__(self, store: RealProjectStore):
        self.store = store

    def get_project(self, project_id: str) -> ProjectSnapshot:
        rows = self.store.read_projects([project_id])
        if not rows:
            raise GatewayError("agent_invalid_request", "未找到所选项目。", {"project_id": project_id})
        return row_to_project(rows[0])

    def list_projects(self, project_ids: list[str]) -> list[ProjectSnapshot]:
        return [row_to_project(row) for row in self.store.read_projects(project_ids)]

    def search_projects(self, query: str = "", page: int = 1, page_size: int = 20) -> tuple[list[ProjectSnapshot], int]:
        rows, total = self.store.search_projects(query=query, page=page, page_size=page_size)
        return [row_to_project(row) for row in rows], total


class OverlayCompanyGateway:
    def __init__(self, base_gateway):
        self.base_gateway = base_gateway
        self.snapshots: dict[str, Any] = {}
        self.supplied_fields: dict[str, dict[str, Any]] = {}

    def get_company_profile(self, company_id: str, **kwargs):
        return self.snapshots.get(company_id) or self.base_gateway.get_company_profile(company_id, **kwargs)


class RealProfileUpdateGateway:
    def __init__(self, company_gateway: OverlayCompanyGateway):
        self.company_gateway = company_gateway

    def update_profile(
        self,
        company_id: str,
        current_profile_version: str,
        provided_fields: dict[str, Any],
    ):
        if not provided_fields:
            raise GatewayError("agent_invalid_request", "补充材料不能为空。", {})
        current = self.company_gateway.get_company_profile(company_id)
        if current.profile_version != current_profile_version:
            raise GatewayError(
                "agent_profile_version_mismatch",
                "企业画像版本已变化，请重新发起分析。",
                {"company_id": company_id},
            )
        accumulated = dict(self.company_gateway.supplied_fields.get(company_id, {}))
        accumulated.update(provided_fields)
        self.company_gateway.supplied_fields[company_id] = accumulated
        facts = dict(current.fact_profile)
        facts["bid_decision_user_supplied_materials"] = accumulated
        updated = current.model_copy(
            update={
                "profile_version": f"{current.profile_version}-bid-{len(accumulated)}",
                "fact_profile": facts,
                "current_task_constraints": {
                    **current.current_task_constraints,
                    "bid_decision_user_supplied_materials": accumulated,
                },
                "data_sources": sorted(set(current.data_sources + ["用户补充的投标证明材料"])),
                "data_updated_at": datetime.now(timezone.utc),
            }
        )
        self.company_gateway.snapshots[company_id] = updated
        return updated


def _normalized_terms(values: list[str]) -> list[str]:
    return [_text(item).lower() for item in values if _text(item)]


class RealRecommendationGateway:
    def __init__(self, project_gateway: RealProjectGateway, company_gateway):
        self.project_gateway = project_gateway
        self.company_gateway = company_gateway

    def get_ranked_candidates(self, company_id: str, project_ids: list[str]) -> list[RankedProjectCandidate]:
        company = self.company_gateway.get_company_profile(company_id)
        projects = (
            self.project_gateway.list_projects(project_ids)
            if project_ids
            else self.project_gateway.search_projects(page=1, page_size=30)[0]
        )
        industries = _normalized_terms(
            company.capability_profile.industry_capability
            + company.decision_profile.strategic_industries
        )
        regions = _normalized_terms(
            company.capability_profile.regional_delivery_capability
            + company.decision_profile.strategic_regions
        )
        ranked: list[tuple[float, ProjectSnapshot, dict[str, float], list[str]]] = []
        for project in projects:
            industry_match = 100.0 if any(term in project.industry.lower() or project.industry.lower() in term for term in industries) else 45.0
            region_match = 100.0 if any(term in project.region.lower() or project.region.lower() in term for term in regions) else 50.0
            completeness = sum(
                [
                    bool(project.industry and "待补充" not in project.industry),
                    bool(project.region and "待补充" not in project.region),
                    bool(project.buyer_name and "待补充" not in project.buyer_name),
                    project.budget is not None,
                    bool(project.qualification_requirements),
                ]
            ) / 5 * 100
            score = round(industry_match * 0.5 + region_match * 0.3 + completeness * 0.2, 2)
            channels = ["真实项目库"]
            if industry_match == 100:
                channels.append("行业匹配")
            if region_match == 100:
                channels.append("地区匹配")
            ranked.append(
                (
                    score,
                    project,
                    {
                        "行业匹配": industry_match,
                        "地区匹配": region_match,
                        "项目资料完整度": round(completeness, 2),
                    },
                    channels,
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1].as_of_time, item[1].project_id))
        return [
            RankedProjectCandidate(
                company_id=company_id,
                project_id=project.project_id,
                company_profile_version=company.profile_version,
                project_version=project.project_version,
                rank=index,
                rank_score=score,
                recall_channels=channels,
                score_breakdown=breakdown,
                evidence_ids=project.evidence_ids + company.evidence_ids[:3],
            )
            for index, (score, project, breakdown, channels) in enumerate(ranked, 1)
        ]


class RealEligibilityGateway:
    def __init__(self, project_gateway: RealProjectGateway, company_gateway):
        self.project_gateway = project_gateway
        self.company_gateway = company_gateway

    @staticmethod
    def _supplied_materials(company) -> tuple[dict[str, Any], dict[str, list[str]]]:
        values: dict[str, Any] = {}
        evidence_by_key: dict[str, list[str]] = {}
        direct = company.current_task_constraints.get("bid_decision_user_supplied_materials")
        if isinstance(direct, dict):
            values.update(direct)
        for fact in company.fact_profile.get("facts", []) if isinstance(company.fact_profile, dict) else []:
            payload = fact.get("payload") if isinstance(fact, dict) else None
            if not isinstance(payload, dict) or payload.get("material_type") != "bid_decision_profile_supplement":
                continue
            declared = payload.get("declared_fields")
            if not isinstance(declared, dict):
                continue
            evidence_ids = [str(item) for item in fact.get("evidence_ids", []) if item]
            for key, value in declared.items():
                values[str(key)] = value
                if evidence_ids:
                    evidence_by_key[str(key)] = evidence_ids
        return values, evidence_by_key

    def evaluate(self, company_id: str, project_id: str, profile_version: str, project_version: str) -> EligibilityResult:
        project = self.project_gateway.get_project(project_id)
        company = self.company_gateway.get_company_profile(company_id)
        requirements = (
            project.qualification_requirements
            + project.personnel_requirements
            + project.performance_requirements
        )
        supplied, supplied_evidence = self._supplied_materials(company)
        items: list[EligibilityItemResult] = []
        for index, requirement in enumerate(requirements):
            requirement_id = _text(requirement.get("requirement_id")) or f"notice-requirement-{index + 1}"
            category = _text(requirement.get("category")) or "OTHER"
            requirement_text = _text(requirement.get("text")) or _text(requirement.get("source_field"))
            supplement_key = f"{project_id}::{requirement_id}"
            required_materials = required_material_labels(requirement)
            matching_value = supplied.get(supplement_key)
            if matching_value in (None, "", [], {}):
                status = EligibilityStatus.UNKNOWN
                reason_code = "REAL_NOTICE_REQUIRES_VERIFICATION"
                explanation = "公告提出了该项要求，但企业画像中没有足够的结构化证据完成逐项核验。"
                missing_fields = required_materials
            else:
                validation_status, explanation, missing_fields = validate_structured_supplement(
                    requirement, matching_value
                )
                status = EligibilityStatus(validation_status)
                reason_code = {
                    EligibilityStatus.PASS: "USER_STRUCTURED_MATERIAL_RULE_PASS",
                    EligibilityStatus.FAIL: "USER_STRUCTURED_MATERIAL_RULE_FAIL",
                    EligibilityStatus.UNKNOWN: "USER_STRUCTURED_MATERIAL_INCOMPLETE",
                }[status]
            items.append(
                EligibilityItemResult(
                    requirement_id=requirement_id,
                    status=status,
                    reason_code=reason_code,
                    explanation=explanation,
                    missing_fields=missing_fields,
                    evidence_ids=sorted(set(project.evidence_ids + supplied_evidence.get(supplement_key, []))),
                    critical=True,
                    requirement_text=requirement_text,
                    requirement_category=category,
                    supplement_key=supplement_key,
                    required_materials=required_materials,
                    supplement_allowed=bool(requirement.get("input_fields")),
                    input_fields=list(requirement.get("input_fields") or []),
                    validation_rule=dict(requirement.get("validation_rule") or {}),
                )
            )
        if not items:
            items.append(
                EligibilityItemResult(
                    requirement_id="notice-requirement-missing",
                    status=EligibilityStatus.UNKNOWN,
                    reason_code="REAL_NOTICE_REQUIREMENT_NOT_STRUCTURED",
                    explanation="数据库尚未形成可直接核验的资格条件，系统无法明确要求用户补充哪类材料。",
                    missing_fields=[],
                    evidence_ids=project.evidence_ids,
                    critical=True,
                    requirement_text="当前项目资格要求尚未完成结构化提取，请查看公告原文或转人工核验。",
                    requirement_category="UNSTRUCTURED",
                    supplement_key=None,
                    required_materials=[],
                    supplement_allowed=False,
                    input_fields=[],
                    validation_rule={},
                )
            )
        pass_count = sum(item.status == EligibilityStatus.PASS for item in items)
        fail_count = sum(item.status == EligibilityStatus.FAIL for item in items)
        unknown_count = sum(item.status == EligibilityStatus.UNKNOWN for item in items)
        overall_status = (
            EligibilityStatus.FAIL if fail_count
            else EligibilityStatus.UNKNOWN if unknown_count
            else EligibilityStatus.PASS
        )
        missing = sorted({field for item in items for field in item.missing_fields})
        result_evidence = sorted({evidence for item in items for evidence in item.evidence_ids})
        return EligibilityResult(
            company_id=company_id, project_id=project_id,
            company_profile_version=profile_version, project_version=project_version,
            overall_status=overall_status, pass_count=pass_count, fail_count=fail_count,
            unknown_count=unknown_count, item_results=items, missing_fields=missing,
            verification_tasks=["继续补充仍为UNKNOWN的资格项"] if unknown_count else [],
            evidence_ids=result_evidence, rule_version="real-notice-structured-supplement-v4",
        )


class DemoCompetitionDataGateway:
    """Temporary, explicitly labelled demo competition data.

    It never reads or writes the remote relationship table and therefore cannot
    be mistaken for confirmed real participation data.
    """
    def __init__(self, demo_path: Path):
        self.demo_path = Path(demo_path)

    def get_snapshot(self, project_id: str, project_version: str) -> CompetitionDataSnapshot:
        try:
            payload = json.loads(self.demo_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GatewayError(
                "competition_demo_data_invalid",
                "竞争对手演示数据不可用。",
                {"path": str(self.demo_path), "type": type(exc).__name__},
            ) from exc
        rows = payload.get("competitors") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise GatewayError("competition_demo_data_invalid", "竞争对手演示数据为空。", {"path": str(self.demo_path)})
        competitors = [
            CompetitorRef(
                company_id=_text(row.get("company_id")) or f"DEMO-COMPETITOR-{index:03d}",
                company_name=_text(row.get("company_name")) or f"演示竞争企业{index}",
                basis=_text(row.get("basis")) or "演示候选，仅用于页面与流程验证",
                evidence_ids=[f"demo:competition:{index}"],
            )
            for index, row in enumerate(rows, 1) if isinstance(row, dict)
        ]
        return CompetitionDataSnapshot(
            project_id=project_id, project_version=project_version,
            competition_data_version="competition-demo-v1",
            direct_participants=[], historical_bidders=[], buyer_suppliers=competitors,
            competitor_profiles={},
            source_coverage={"项目公告": True, "演示竞争候选": True, "真实竞争关系": False},
            unknowns=["演示数据，不代表真实企业参与情况。真实竞争关系表完善后需替换此临时数据源。"],
            evidence_ids=[
                f"mysql:project:{project_id.removeprefix('db-project-')}",
                "demo:competition:warning",
                *[evidence_id for competitor in competitors for evidence_id in competitor.evidence_ids],
            ],
            as_of_time=datetime.now(timezone.utc),
            data_is_demo=True,
            data_warning="演示数据，不代表真实企业参与情况",
        )


class RealWinOpportunityGateway:
    def estimate(
        self,
        company_id: str,
        project_id: str,
        profile_version: str,
        project_version: str,
        competition_coverage: float,
        eligibility: str,
    ) -> WinOpportunityResult:
        return WinOpportunityResult(
            company_id=company_id,
            project_id=project_id,
            company_profile_version=profile_version,
            project_version=project_version,
            status="INSUFFICIENT_DATA",
            opportunity_level="UNKNOWN",
            probability=None,
            probability_range=None,
            confidence="LOW",
            data_coverage=competition_coverage,
            positive_factors=[],
            negative_factors=["企业—项目关系和历史竞争数据尚未入库"],
            reason_codes=["REAL_COMPETITION_RELATION_MISSING"],
            model_version="real-project-conservative-v1",
        )


def build_real_gateways(
    *,
    node_base_url: str,
    database_url: str,
    runtime_root: Path,
    competition_demo_path: Path,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    client = NodeEnvelopeClient(node_base_url, timeout_seconds)
    company = ProfileServiceCompanyGateway(client)
    profile_update = ProfileServiceUpdateGateway(client)
    store = RealProjectStore(database_url, timeout_seconds=timeout_seconds)
    project = RealProjectGateway(store)
    persistence = RuntimePersistenceGateway(runtime_root)
    return {
        "company": company,
        "evaluation": ProfileServiceEvaluationGateway(client),
        "profile_update": profile_update,
        "project": project,
        "recommendation": RealRecommendationGateway(project, company),
        "eligibility": RealEligibilityGateway(project, company),
        "competition_data": DemoCompetitionDataGateway(competition_demo_path),
        "win_opportunity": RealWinOpportunityGateway(),
        "persistence": persistence,
        "audit": RuntimeAuditGateway(runtime_root),
        "real_project_store": store,
    }
