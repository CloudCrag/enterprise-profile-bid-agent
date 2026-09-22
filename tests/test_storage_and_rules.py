from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from services.profile_agent_api.app.local_store import LocalJsonStore

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_enterprise_storage_has_117_unique_companies() -> None:
    store = LocalJsonStore(ROOT / "runtime_data")
    store.ensure_initialized()
    companies = store.list_companies()
    ids = [item["company_id"] for item in companies]
    assert len(ids) == 117
    assert len(set(ids)) == 117
    assert not any("MOCK" in value.upper() for value in ids)
    assert all(item.get("data_origin") == "PROFILE_AGENT_VERIFIED_JSON" for item in companies)


def test_frozen_evaluation_rules_match_integrity_manifest() -> None:
    manifest = json.loads((ROOT / "config" / "evaluation_rules_integrity.json").read_text(encoding="utf-8"))
    for relative_path, expected in manifest.items():
        assert sha256(ROOT / relative_path) == expected


def test_writing_runtime_profile_does_not_change_source(tmp_path: Path) -> None:
    source_root = ROOT / "runtime_data"
    company_id = sorted(path.name for path in (source_root / "enterprise_source").iterdir() if path.is_dir())[0]
    target = tmp_path / "runtime_data"
    shutil.copytree(source_root / "enterprise_source" / company_id, target / "enterprise_source" / company_id)
    shutil.copytree(source_root / "enterprise_profiles" / company_id, target / "enterprise_profiles" / company_id)

    source_files = sorted((target / "enterprise_source" / company_id).rglob("*.json"))
    before = {path.relative_to(target): sha256(path) for path in source_files}
    store = LocalJsonStore(target)
    store.ensure_initialized()
    fact = store.read_profile(company_id, "fact")
    assert fact is not None
    store.write_profile(
        company_id,
        "fact",
        fact,
        update_source="TEST_VERIFIED_UPDATE",
        actor={"actor_type": "system", "actor_id": "LOCAL_OPERATOR"},
    )
    after = {path.relative_to(target): sha256(path) for path in source_files}
    assert after == before
