from pathlib import Path
import sys
from types import SimpleNamespace

sys.modules.setdefault("pymysql", SimpleNamespace(cursors=SimpleNamespace(DictCursor=object), connect=lambda **_: None))

from src.gateways.real_project_gateways import DemoCompetitionDataGateway, row_to_project


def test_demo_competitor_evidence_is_registered_in_snapshot() -> None:
    gateway = DemoCompetitionDataGateway(Path("runtime_data/competition_demo/competitors.json"))
    snapshot = gateway.get_snapshot("db-project-1", "v1")
    referenced = {
        evidence_id
        for competitor in snapshot.buyer_suppliers
        for evidence_id in competitor.evidence_ids
    }
    assert referenced
    assert referenced.issubset(set(snapshot.evidence_ids))


def test_bid_open_time_is_not_mapped_to_bid_deadline_or_status_expiry() -> None:
    project = row_to_project(
        {
            "id": 1,
            "project_name": "项目A",
            "owner_company_name": "采购人",
            "province": "山西省",
            "city": "太原市",
            "industry": "电力",
            "project_type": "工程",
            "current_status": "TENDER",
            "bid_open_time": "2020-01-01 09:00:00",
            "updated_at": "2026-07-31 00:00:00",
            "structured_data": {},
        }
    )
    assert project.bid_open_time is not None
    assert project.bid_deadline is None
    assert project.project_status == "OPEN"

from src.gateways.real_project_gateways import RealEligibilityGateway
from src.shared.schemas import EligibilityStatus


class _StaticProjectGateway:
    def __init__(self, project):
        self.project = project

    def get_project(self, project_id: str):
        assert project_id == self.project.project_id
        return self.project


class _StaticCompanyGateway:
    def __init__(self, company):
        self.company = company

    def get_company_profile(self, company_id: str):
        assert company_id == "company-1"
        return self.company


def _project_with_requirements():
    return row_to_project(
        {
            "id": 2,
            "project_name": "资格测试项目",
            "owner_company_name": "采购人",
            "province": "山西省",
            "city": "太原市",
            "industry": "电力",
            "project_type": "工程",
            "current_status": "TENDER",
            "bid_open_time": None,
            "updated_at": "2026-07-31 00:00:00",
            "structured_data": {
                "资格要求": "具有电力工程施工总承包一级资质",
                "人员要求": "项目经理须持有一级建造师证书",
            },
        }
    )


def test_qualification_supplements_bind_to_one_project_and_one_requirement() -> None:
    project = _project_with_requirements()
    first = project.qualification_requirements[0]
    first_key = f"{project.project_id}::{first['requirement_id']}"
    company = SimpleNamespace(
        current_task_constraints={
            "bid_decision_user_supplied_materials": {
                first_key: {
                    "certificate_name": "电力工程施工总承包资质",
                    "certificate_level": "一级",
                    "certificate_number": "TEST-001",
                    "certificate_valid_until": "2099-12-31",
                    "evidence_description": "本地流程测试证明材料",
                }
            }
        },
        fact_profile={},
    )
    gateway = RealEligibilityGateway(_StaticProjectGateway(project), _StaticCompanyGateway(company))
    result = gateway.evaluate("company-1", project.project_id, "company-v1", project.project_version)

    assert result.overall_status == EligibilityStatus.UNKNOWN
    assert len(result.item_results) == 2
    supplied = next(item for item in result.item_results if item.supplement_key == first_key)
    untouched = next(item for item in result.item_results if item.supplement_key != first_key)
    assert supplied.status == EligibilityStatus.PASS
    assert supplied.reason_code == "USER_STRUCTURED_MATERIAL_RULE_PASS"
    assert supplied.missing_fields == []
    assert untouched.status == EligibilityStatus.UNKNOWN
    assert untouched.reason_code == "REAL_NOTICE_REQUIRES_VERIFICATION"
    assert untouched.required_materials


def test_unstructured_qualification_does_not_show_meaningless_input() -> None:
    project = row_to_project(
        {
            "id": 3,
            "project_name": "未结构化资格项目",
            "owner_company_name": "采购人",
            "province": "山西省",
            "city": "太原市",
            "industry": "电力",
            "project_type": "工程",
            "current_status": "PLAN",
            "bid_open_time": None,
            "updated_at": "2026-07-31 00:00:00",
            "structured_data": {},
        }
    )
    company = SimpleNamespace(current_task_constraints={}, fact_profile={})
    gateway = RealEligibilityGateway(_StaticProjectGateway(project), _StaticCompanyGateway(company))
    result = gateway.evaluate("company-1", project.project_id, "company-v1", project.project_version)
    item = result.item_results[0]
    assert item.status == EligibilityStatus.UNKNOWN
    assert item.supplement_allowed is False
    assert item.supplement_key is None
    assert item.required_materials == []


def test_legacy_free_text_supplement_cannot_become_pass() -> None:
    project = _project_with_requirements()
    first = project.qualification_requirements[0]
    first_key = f"{project.project_id}::{first['requirement_id']}"
    company = SimpleNamespace(
        current_task_constraints={
            "bid_decision_user_supplied_materials": {first_key: "符合要求，证书有效"}
        },
        fact_profile={},
    )
    gateway = RealEligibilityGateway(_StaticProjectGateway(project), _StaticCompanyGateway(company))
    result = gateway.evaluate("company-1", project.project_id, "company-v1", project.project_version)
    supplied = next(item for item in result.item_results if item.supplement_key == first_key)
    assert supplied.status == EligibilityStatus.UNKNOWN
    assert supplied.reason_code == "USER_STRUCTURED_MATERIAL_INCOMPLETE"
    assert supplied.missing_fields


def test_long_notice_is_atomized_into_distinct_qualification_forms() -> None:
    project = row_to_project(
        {
            "id": 4,
            "project_name": "原子资格测试项目",
            "owner_company_name": "采购人",
            "province": "山西省",
            "city": "太原市",
            "industry": "能源",
            "project_type": "服务",
            "current_status": "TENDER",
            "bid_open_time": None,
            "updated_at": "2026-07-31 00:00:00",
            "structured_data": {
                "资格要求": (
                    "投标人资格能力要求：3.1在中华人民共和国境内注册，具有有效的营业执照，具有乙级及以上测绘资质。"
                    "3.2近三年独立完成至少三项同类项目业绩。"
                    "3.3项目负责人须具有相关专业中级及以上职称，并提供近半年社保。"
                    "3.4存在控股、管理关系的不同单位不得同时投标。"
                    "3.5未被列入失信被执行人及严重违法失信企业名单。"
                    "3.6本项目不允许联合体投标。"
                )
            },
        }
    )
    categories = {item["category"] for item in project.qualification_requirements}
    assert categories == {
        "BUSINESS_REGISTRATION",
        "QUALIFICATION_CERTIFICATE",
        "PERFORMANCE",
        "PERSONNEL",
        "RELATIONSHIP",
        "CREDIT",
        "CONSORTIUM",
    }
    for item in project.qualification_requirements:
        assert item["input_fields"]
        assert all(field["label"] for field in item["input_fields"])


def test_structured_supplement_can_fail_on_explicit_rule_conflict() -> None:
    project = row_to_project(
        {
            "id": 5,
            "project_name": "联合体限制项目",
            "owner_company_name": "采购人",
            "province": "山西省",
            "city": "太原市",
            "industry": "能源",
            "project_type": "服务",
            "current_status": "TENDER",
            "bid_open_time": None,
            "updated_at": "2026-07-31 00:00:00",
            "structured_data": {"资格要求": "本项目不允许联合体投标。"},
        }
    )
    requirement = project.qualification_requirements[0]
    key = f"{project.project_id}::{requirement['requirement_id']}"
    company = SimpleNamespace(
        current_task_constraints={
            "bid_decision_user_supplied_materials": {
                key: {"is_consortium_bid": True, "evidence_description": "拟采用联合体"}
            }
        },
        fact_profile={},
    )
    gateway = RealEligibilityGateway(_StaticProjectGateway(project), _StaticCompanyGateway(company))
    result = gateway.evaluate("company-1", project.project_id, "company-v1", project.project_version)
    assert result.overall_status == EligibilityStatus.FAIL
    assert result.item_results[0].status == EligibilityStatus.FAIL
