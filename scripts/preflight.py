from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.environment_loader import load_project_environment
from src.gateways.real_project_gateways import RealProjectStore
from src.llm.zhipu_provider import ZhipuGLMProvider
from services.profile_agent_api.app.local_store import LocalJsonStore


class ProbeResponse(BaseModel):
    ok: Literal[True]
    message: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必需配置：{name}")
    return value


def write_report(payload: dict) -> None:
    target = PROJECT_ROOT / "runtime_data" / "startup_preflight.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    load_project_environment()
    report: dict = {
        "checked_at_utc": utc_now(),
        "success": False,
        "checks": [],
    }
    try:
        if os.getenv("AGENT_DATA_PROVIDER", "real").strip().lower() != "real":
            raise RuntimeError("AGENT_DATA_PROVIDER 必须为 real")
        if os.getenv("CAPABILITY_AI_PROVIDER", "zhipu").strip().lower() != "zhipu":
            raise RuntimeError("CAPABILITY_AI_PROVIDER 必须为 zhipu")

        model = env_required("CAPABILITY_AI_MODEL")
        api_key = env_required("CAPABILITY_AI_API_KEY")
        database_url = env_required("REAL_DATABASE_URL")
        report["checks"].append({"name": "配置文件", "success": True, "message": "必需配置已提供"})

        runtime_root = Path(os.getenv("RUNTIME_DATA_ROOT", "runtime_data"))
        if not runtime_root.is_absolute():
            runtime_root = PROJECT_ROOT / runtime_root
        store = LocalJsonStore(runtime_root)
        store.ensure_initialized()
        companies = store.list_companies()
        if not companies:
            raise RuntimeError("没有可用的真实企业数据")
        report["checks"].append({
            "name": "真实企业 JSON",
            "success": True,
            "message": f"已校验 {len(companies)} 家企业，原始目录可解析且企业唯一标识有效",
        })

        project_probe = RealProjectStore(
            database_url,
            timeout_seconds=float(os.getenv("AGENT_GATEWAY_TIMEOUT_SECONDS", "15")),
        ).probe()
        if not project_probe.get("select_one") or not project_probe.get("project_query_ok"):
            raise RuntimeError("远程项目数据库只读探测未通过")
        if int(project_probe.get("sample_count") or 0) != 1:
            raise RuntimeError("真实项目查询未读取到一条状态为 TENDER/PLAN 的项目")
        report["checks"].append({
            "name": "远程项目数据库",
            "success": True,
            "message": "SSH 转发端口、数据库登录、SELECT 1 与真实项目查询路径均通过",
            "current_project_total": int(project_probe.get("current_project_total") or 0),
        })

        provider = ZhipuGLMProvider(
            base_url=os.getenv("CAPABILITY_AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            model=model,
            api_key=api_key,
            timeout_seconds=int(os.getenv("CAPABILITY_AI_TIMEOUT_SECONDS", "180")),
            max_retries=int(os.getenv("CAPABILITY_AI_MAX_RETRIES", "2")),
            max_response_bytes=int(os.getenv("CAPABILITY_AI_MAX_RESPONSE_BYTES", "1048576")),
            temperature=float(os.getenv("CAPABILITY_AI_TEMPERATURE", "0")),
            max_tokens=min(int(os.getenv("CAPABILITY_AI_MAX_TOKENS", "4096")), 256),
        )
        response = provider.generate_structured(
            system_prompt=(
                "你正在执行系统启动健康检查。只能返回符合给定 JSON Schema 的 JSON；"
                "ok 必须为 true，message 使用简短中文，不得输出其他内容。"
            ),
            user_payload={"task": "startup_health_check", "expected": {"ok": True}},
            output_schema=ProbeResponse,
        )
        if response.ok is not True:
            raise RuntimeError("智谱 GLM 返回了无效健康检查结果")
        report["checks"].append({
            "name": "智谱 GLM",
            "success": True,
            "message": "真实网络调用、鉴权、模型权限与结构化响应校验均通过",
            "model": model,
        })

        report["success"] = True
        report["message"] = "全部核心依赖检查通过"
        write_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        report["message"] = "系统启动检查失败"
        write_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
