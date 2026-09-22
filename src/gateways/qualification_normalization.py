"""Deterministic qualification requirement atomization and supplement validation.

The project database often stores one long notice paragraph.  This module turns
that paragraph into independently verifiable requirements and describes the
exact fields a user must provide for each requirement.  It deliberately does
not treat arbitrary non-empty text as proof.
"""
from __future__ import annotations

from datetime import date
import hashlib
import re
from typing import Any


_CATEGORY_LABELS = {
    "BUSINESS_REGISTRATION": "企业主体与营业执照",
    "QUALIFICATION_CERTIFICATE": "企业资质或行政许可",
    "PERFORMANCE": "同类项目业绩",
    "PERSONNEL": "项目负责人及人员证书",
    "RELATIONSHIP": "负责人、控股及管理关系",
    "CREDIT": "信用与失信记录",
    "CONSORTIUM": "联合体要求",
    "OTHER": "其他资格要求",
}

_NUMBER_WORDS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, "其他资格要求")


def _field(
    key: str,
    label: str,
    input_type: str = "text",
    *,
    required: bool = True,
    placeholder: str = "",
    help_text: str = "",
    options: list[dict[str, Any]] | None = None,
    min_value: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": key,
        "label": label,
        "input_type": input_type,
        "required": required,
        "placeholder": placeholder,
        "help_text": help_text,
        "options": options or [],
    }
    if min_value is not None:
        payload["min_value"] = min_value
    return payload


def _yes_no_options() -> list[dict[str, Any]]:
    return [{"label": "是", "value": True}, {"label": "否", "value": False}]


def _input_schema(category: str, text: str) -> list[dict[str, Any]]:
    if category == "BUSINESS_REGISTRATION":
        return [
            _field("enterprise_name", "营业执照企业名称", placeholder="与营业执照完全一致"),
            _field("unified_social_credit_code", "统一社会信用代码", placeholder="18位统一社会信用代码"),
            _field(
                "registration_status", "登记状态", "select",
                options=[
                    {"label": "存续/在业/正常", "value": "ACTIVE"},
                    {"label": "注销", "value": "CANCELLED"},
                    {"label": "吊销", "value": "REVOKED"},
                    {"label": "其他或不确定", "value": "UNKNOWN"},
                ],
            ),
            _field("license_valid_until", "营业期限或有效期", "date", required=False, help_text="长期有效时可不填，并在证明说明中注明。"),
            _field("evidence_description", "营业执照证明说明", "textarea", placeholder="说明营业执照文件名称、页码或存放位置"),
        ]
    if category == "QUALIFICATION_CERTIFICATE":
        return [
            _field("certificate_name", "资质或许可证名称", placeholder="例如：测绘资质证书、劳务派遣经营许可证"),
            _field("certificate_level", "资质等级", placeholder="例如：乙级、一级；无等级许可证填写“不分等级”"),
            _field("certificate_number", "证书编号", placeholder="填写真实证书编号"),
            _field("certificate_valid_until", "证书有效期", "date"),
            _field("evidence_description", "资质证明材料", "textarea", placeholder="说明证书扫描件、电子证照或其他证明材料"),
        ]
    if category == "PERFORMANCE":
        minimum = _extract_min_count(text) or 1
        return [
            _field("completed_project_count", "符合要求的业绩数量", "number", min_value=0, help_text=f"公告要求至少 {minimum} 项。"),
            _field("project_names", "业绩项目名称", "textarea", placeholder="逐行填写符合条件的项目名称"),
            _field("performance_period", "业绩完成时间范围", "text", placeholder="例如：2023-01-01至投标截止时间前"),
            _field("scope_description", "项目类型及服务范围", "textarea", placeholder="说明每项业绩为何属于公告要求的同类项目"),
            _field("evidence_description", "业绩证明材料", "textarea", placeholder="说明合同、发票、中标通知书、批复文件等材料"),
        ]
    if category == "PERSONNEL":
        return [
            _field("person_name", "人员姓名"),
            _field("profession", "专业", placeholder="例如：国土空间规划、土地资源管理、测绘工程"),
            _field("title_level", "职称或证书等级", placeholder="例如：中级工程师、一级建造师"),
            _field("certificate_number", "职称或执业证书编号"),
            _field("social_security_months", "近期开具社保覆盖月数", "number", min_value=0, help_text="公告要求近半年时应至少填写6。"),
            _field("availability_status", "本项目可用状态", "select", options=[
                {"label": "可投入本项目", "value": "AVAILABLE"},
                {"label": "不可投入本项目", "value": "UNAVAILABLE"},
                {"label": "尚不确定", "value": "UNKNOWN"},
            ]),
            _field("evidence_description", "人员证明材料", "textarea", placeholder="说明证书、劳动关系及社保证明材料"),
        ]
    if category == "RELATIONSHIP":
        return [
            _field("no_prohibited_relationship", "是否确认不存在公告禁止的负责人相同、控股或管理关系", "boolean", options=_yes_no_options()),
            _field("evidence_description", "关联关系声明", "textarea", placeholder="说明核查范围及声明材料"),
        ]
    if category == "CREDIT":
        return [
            _field("not_dishonest_enforcement", "是否未被列入失信被执行人名单", "boolean", options=_yes_no_options()),
            _field("not_serious_illegal_enterprise", "是否未被列入严重违法失信企业名单", "boolean", options=_yes_no_options()),
            _field("query_date", "信用查询日期", "date"),
            _field("evidence_description", "信用查询证明", "textarea", placeholder="说明查询网站、查询结果及截图或记录"),
        ]
    if category == "CONSORTIUM":
        return [
            _field("is_consortium_bid", "本次是否以联合体形式投标", "boolean", options=_yes_no_options()),
            _field("evidence_description", "投标形式声明", "textarea", placeholder="说明独立投标或联合体安排"),
        ]
    return []


def _strip_heading_prefix(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    # Keep the actual clauses after a generic heading such as “资格能力要求：”.
    if "：" in value:
        prefix, suffix = value.split("：", 1)
        if len(prefix) < 80 and "要求" in prefix and suffix.strip():
            return suffix.strip()
    return value


def _segments(text: str) -> list[str]:
    value = _strip_heading_prefix(text)
    if not value:
        return []
    marker = re.compile(r"(?<![\d.])(?:\d+(?:\.\d+)+|[一二三四五六七八九十]+[、.]|（\d+）|\(\d+\))")
    matches = list(marker.finditer(value))
    if matches:
        parts: list[str] = []
        if matches[0].start() > 0:
            prefix = value[:matches[0].start()].strip(" ，。；;:")
            if prefix and not (len(prefix) < 50 and "要求" in prefix):
                parts.append(prefix)
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
            part = value[match.start():end].strip(" ，。；;:")
            if part:
                parts.append(part)
        return parts
    # A semicolon commonly separates independent requirements when no numbering exists.
    semicolon_parts = [part.strip(" ，。；;:") for part in re.split(r"[；;]", value) if part.strip(" ，。；;:")]
    return semicolon_parts if len(semicolon_parts) > 1 else [value]


def _categories(text: str, default_category: str) -> list[str]:
    value = text.lower()
    found: list[str] = []
    if re.search(r"营业执照|境内注册|中华人民共和国境内注册|独立法人|法人证书|独立承担民事责任", text):
        found.append("BUSINESS_REGISTRATION")
    if re.search(r"资质|许可证|行政许可", text):
        found.append("QUALIFICATION_CERTIFICATE")
    if re.search(r"业绩|合同业绩|类似项目|同类项目|合同封面|发票|批复文件|中标通知书", text):
        found.append("PERFORMANCE")
    if re.search(r"项目负责人|项目经理|人员|职称|社保|建造师|工程师", text):
        found.append("PERSONNEL")
    if re.search(r"单位负责人为同一人|控股[、,，及和或]?管理关系|关联关系|管理关系的不同单位", text):
        found.append("RELATIONSHIP")
    if re.search(r"失信|信用信息|执行信息公开网|严重违法|黑名单", text):
        found.append("CREDIT")
    if "联合体" in text:
        found.append("CONSORTIUM")
    if found:
        return list(dict.fromkeys(found))
    mapped = {
        "QUALIFICATION": "QUALIFICATION_CERTIFICATE",
        "PERSONNEL": "PERSONNEL",
        "PERFORMANCE": "PERFORMANCE",
    }.get(default_category, "OTHER")
    return [mapped]


def _extract_min_count(text: str) -> int | None:
    match = re.search(r"至少\s*(\d+)\s*项", text)
    if match:
        return int(match.group(1))
    match = re.search(r"至少\s*([一二三四五六七八九十])\s*项", text)
    if match:
        return _NUMBER_WORDS.get(match.group(1))
    return None


def _extract_min_social_months(text: str) -> int | None:
    match = re.search(r"近\s*(\d+)\s*个?月", text)
    if match:
        return int(match.group(1))
    if "近半年" in text or "近六个月" in text:
        return 6
    return None


def _stable_requirement_id(source_field: str, category: str, text: str) -> str:
    digest = hashlib.sha256(f"{source_field}|{category}|{text}".encode("utf-8")).hexdigest()[:14]
    return f"notice-{category.lower()}-{digest}"


def atomize_requirement_blocks(
    structured: dict[str, Any],
    terms: tuple[str, ...],
    *,
    default_category: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, raw_value in structured.items():
        label = str(key or "").strip()
        content = str(raw_value or "").strip()
        if not content or not any(term in label for term in terms):
            continue
        for segment in _segments(content):
            for category in _categories(segment, default_category):
                normalized = re.sub(r"\s+", " ", segment).strip()
                signature = (category, normalized)
                if signature in seen:
                    continue
                seen.add(signature)
                fields = _input_schema(category, normalized)
                results.append(
                    {
                        "requirement_id": _stable_requirement_id(label, category, normalized),
                        "source_field": label,
                        "text": normalized,
                        "category": category,
                        "category_label": category_label(category),
                        "mandatory": True,
                        "input_fields": fields,
                        "validation_rule": {
                            "min_count": _extract_min_count(normalized),
                            "min_social_security_months": _extract_min_social_months(normalized),
                        },
                    }
                )
    return results


def required_material_labels(requirement: dict[str, Any]) -> list[str]:
    return [
        str(field.get("label"))
        for field in requirement.get("input_fields", [])
        if field.get("required") and field.get("label")
    ]


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _certificate_rank(value: str) -> int | None:
    text = str(value or "").strip()
    rankings = [
        (r"特级", 6), (r"甲级", 5), (r"一级", 5),
        (r"乙级", 4), (r"二级", 4), (r"丙级", 3),
        (r"三级", 3), (r"丁级", 2), (r"四级", 2),
        (r"不分等级", 1),
    ]
    for pattern, rank in rankings:
        if re.search(pattern, text):
            return rank
    return None


def _required_certificate_rank(text: str) -> int | None:
    patterns = ["特级", "甲级", "一级", "乙级", "二级", "丙级", "三级", "丁级", "四级"]
    for value in patterns:
        if value in text:
            return _certificate_rank(value)
    return None


def _required_title_rank(text: str) -> int | None:
    if "正高级" in text:
        return 4
    if "高级" in text:
        return 3
    if "中级" in text:
        return 2
    if "初级" in text:
        return 1
    return None


def _submitted_title_rank(value: str) -> int | None:
    text = str(value or "")
    if "正高级" in text:
        return 4
    if "高级" in text:
        return 3
    if "中级" in text:
        return 2
    if "初级" in text:
        return 1
    return None


def validate_structured_supplement(
    requirement: dict[str, Any],
    supplied: Any,
) -> tuple[str, str, list[str]]:
    """Return status, explanation and missing field labels.

    Status is one of PASS/FAIL/UNKNOWN.  Legacy free text is intentionally not
    accepted as PASS because it cannot be checked against individual fields.
    """
    fields = requirement.get("input_fields") or []
    if not fields:
        return "UNKNOWN", "该要求尚未形成可自动核验的结构化补充表单。", []
    if not isinstance(supplied, dict):
        return "UNKNOWN", "补充内容不是结构化字段，不能据此判定资格符合。", required_material_labels(requirement)

    missing = [
        str(field.get("label"))
        for field in fields
        if field.get("required") and _blank(supplied.get(str(field.get("key"))))
    ]
    if missing:
        return "UNKNOWN", "仍有必填核验字段未补充完整。", missing

    category = str(requirement.get("category") or "OTHER")
    text = str(requirement.get("text") or "")

    if category == "BUSINESS_REGISTRATION":
        code = re.sub(r"\s+", "", str(supplied.get("unified_social_credit_code") or "").upper())
        if not re.fullmatch(r"[0-9A-Z]{18}", code):
            return "UNKNOWN", "统一社会信用代码格式不完整，应为18位数字或大写字母。", ["统一社会信用代码"]
        status = supplied.get("registration_status")
        if status in {"CANCELLED", "REVOKED"}:
            return "FAIL", "企业登记状态为注销或吊销，不满足有效主体要求。", []
        if status == "UNKNOWN":
            return "UNKNOWN", "企业登记状态仍不明确。", ["登记状态"]

    elif category == "QUALIFICATION_CERTIFICATE":
        valid_until = _parse_date(supplied.get("certificate_valid_until"))
        if valid_until is None:
            return "UNKNOWN", "证书有效期格式无效。", ["证书有效期"]
        if valid_until < date.today():
            return "FAIL", "所填资质证书已经超过有效期。", []
        required_rank = _required_certificate_rank(text)
        if required_rank is not None:
            actual_rank = _certificate_rank(str(supplied.get("certificate_level") or ""))
            if actual_rank is None:
                return "UNKNOWN", "无法识别所填资质等级，不能与公告最低等级比较。", ["资质等级"]
            if actual_rank < required_rank:
                return "FAIL", "所填资质等级低于公告最低要求。", []

    elif category == "PERFORMANCE":
        try:
            count = int(supplied.get("completed_project_count"))
        except (TypeError, ValueError):
            return "UNKNOWN", "业绩数量必须填写整数。", ["符合要求的业绩数量"]
        minimum = (requirement.get("validation_rule") or {}).get("min_count") or 1
        if count < int(minimum):
            return "FAIL", f"所填符合条件业绩数量为{count}项，低于公告至少{minimum}项的要求。", []

    elif category == "PERSONNEL":
        availability = supplied.get("availability_status")
        if availability == "UNAVAILABLE":
            return "FAIL", "所填人员当前不可投入本项目。", []
        if availability == "UNKNOWN":
            return "UNKNOWN", "人员能否投入本项目仍不明确。", ["本项目可用状态"]
        required_months = (requirement.get("validation_rule") or {}).get("min_social_security_months")
        if required_months:
            try:
                months = int(supplied.get("social_security_months"))
            except (TypeError, ValueError):
                return "UNKNOWN", "社保覆盖月数必须填写整数。", ["近期开具社保覆盖月数"]
            if months < int(required_months):
                return "FAIL", f"社保覆盖月数为{months}个月，低于公告要求的{required_months}个月。", []
        required_title = _required_title_rank(text)
        if required_title is not None:
            actual_title = _submitted_title_rank(str(supplied.get("title_level") or ""))
            if actual_title is None:
                return "UNKNOWN", "无法识别所填职称等级。", ["职称或证书等级"]
            if actual_title < required_title:
                return "FAIL", "所填人员职称等级低于公告要求。", []

    elif category == "RELATIONSHIP":
        if supplied.get("no_prohibited_relationship") is False:
            return "FAIL", "用户声明存在公告禁止的负责人相同、控股或管理关系。", []

    elif category == "CREDIT":
        if supplied.get("not_dishonest_enforcement") is False:
            return "FAIL", "用户声明企业被列入失信被执行人名单。", []
        if supplied.get("not_serious_illegal_enterprise") is False:
            return "FAIL", "用户声明企业被列入严重违法失信企业名单。", []
        if _parse_date(supplied.get("query_date")) is None:
            return "UNKNOWN", "信用查询日期格式无效。", ["信用查询日期"]

    elif category == "CONSORTIUM":
        prohibited = bool(re.search(r"不\s*(?:允许|接受).*联合体|不允许联合体|不接受联合体", text))
        if prohibited and supplied.get("is_consortium_bid") is True:
            return "FAIL", "公告不接受联合体，但用户选择以联合体形式投标。", []

    else:
        return "UNKNOWN", "该类要求暂不支持自动核验。", required_material_labels(requirement)

    return "PASS", "结构化必填字段已完整，且本次声明未发现与公告规则冲突；结果未经过外部真实性核验。", []
