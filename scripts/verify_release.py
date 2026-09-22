from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    checks: list[str] = []

    if (ROOT / ".env.local").exists():
        fail("交付目录包含 .env.local")
    checks.append("未包含 .env.local")

    launchers = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".ps1", ".bat", ".cmd", ".sh"}
        and "node_modules" not in path.parts
        and ".venv" not in path.parts
        and ".testvenv" not in path.parts
    ]
    if launchers != ["start.ps1"]:
        fail(f"启动入口不唯一: {launchers}")
    checks.append("唯一启动入口为 start.ps1")

    source_root = ROOT / "runtime_data" / "enterprise_source"
    companies = sorted(path for path in source_root.iterdir() if path.is_dir())
    if len(companies) != 117:
        fail(f"真实企业数量应为 117，实际 {len(companies)}")
    company_ids: set[str] = set()
    for company_dir in companies:
        fact_path = company_dir / "current" / "fact_profile.json"
        if not fact_path.exists():
            fail(f"缺少企业事实文件: {company_dir.name}")
        payload = json.loads(fact_path.read_text(encoding="utf-8"))
        enterprise = payload.get("enterprise") or {}
        company_id = str(enterprise.get("unified_social_credit_code") or enterprise.get("company_id") or "").strip()
        if not company_id:
            fail(f"企业唯一标识缺失: {company_dir.name}")
        if company_id in company_ids:
            fail(f"企业唯一标识重复: {company_id}")
        if company_id.upper().startswith(("MOCK", "FIXTURE", "DEMO")):
            fail(f"企业源目录包含虚构企业: {company_id}")
        company_ids.add(company_id)
    checks.append("117 家真实企业 JSON 可解析且唯一")

    integrity = json.loads((ROOT / "config" / "evaluation_rules_integrity.json").read_text(encoding="utf-8"))
    for relative, expected in integrity.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            fail(f"企业评价规则文件被修改: {relative}")
    checks.append("企业评价规则哈希一致")

    demo = json.loads((ROOT / "runtime_data" / "competition_demo" / "competitors.json").read_text(encoding="utf-8"))
    if demo.get("data_is_demo") is not True or "演示数据" not in str(demo.get("warning")):
        fail("竞争演示数据缺少显式标识")
    checks.append("竞争演示数据已显式隔离和标注")

    forbidden_files = [
        "src/llm/fixture_provider.py",
        "src/llm/disabled_provider.py",
        "src/capability_ai/fixture_provider.py",
        "src/capability_ai/deepseek_provider.py",
        "src/gateways/fixture_gateways.py",
        "src/gateways/hybrid_demo_gateways.py",
        "src/gateways/local_json_gateways.py",
        "src/gateways/database_repository_skeleton.py",
        "src/importers/agent_demo.py",
        "services/profile_agent_api/langgraph/offline_graph.py",
    ]
    existing = [path for path in forbidden_files if (ROOT / path).exists()]
    if existing:
        fail(f"仍存在已禁用的虚构/降级实现: {existing}")
    checks.append("已删除 Fixture/Mock/Replay Provider 与业务回退实现")

    for path in ROOT.rglob("*"):
        if not path.is_file() or "competition_demo" in path.parts or path == Path(__file__).resolve():
            continue
        if any(part in {"node_modules", ".venv", ".testvenv", ".git"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".mjs", ".js", ".ts", ".vue", ".json", ".md", ".ps1", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for marker in ("demo-interactive-user", "demo-reviewer", "web-enterprise-user", "fixturellmprovider"):
            if marker in lowered:
                fail(f"仍存在伪用户或 Fixture LLM 引用: {path.relative_to(ROOT)} -> {marker}")
    checks.append("未发现伪用户身份或 Fixture LLM 引用")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("CAPABILITY_AI_API_KEY=", "REAL_DATABASE_URL="):
        line = next((item for item in env_example.splitlines() if item.startswith(name)), None)
        if line is None or line != name:
            fail(f".env.example 中 {name[:-1]} 必须为空")
    checks.append("示例配置不包含密钥或数据库凭据")

    if args.release:
        forbidden_dirs = [ROOT / ".testvenv", ROOT / ".venv", ROOT / "apps" / "web" / "node_modules"]
        existing_dirs = [str(path.relative_to(ROOT)) for path in forbidden_dirs if path.exists()]
        if existing_dirs:
            fail(f"交付目录包含依赖或临时环境: {existing_dirs}")
        transient = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file()
            and (path.suffix in {".pyc", ".log"} or path.name in {".DS_Store", "startup_preflight.json", "startup_error.html"})
        ]
        if transient:
            fail(f"交付目录包含临时产物: {transient[:20]}")
        checks.append("交付目录不包含依赖、缓存、日志或启动状态")

    print(json.dumps({"success": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
