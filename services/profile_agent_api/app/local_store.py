"""Atomic local JSON store for the Section 6 enterprise-profile MVP."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from src.enterprise_fact_profile import fact_profile_content_hash
from src.fact_profile_builder import build_fact_profile
from src.tag_profile_generator import generate_tag_profile, load_tag_catalog
from src.capability_profile_builder import build_capability_profile

from .storage_paths import safe_storage_component


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STORE_ROOT = ROOT / "runtime_data"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary filename short. Batch item identifiers are already
    # descriptive and repeating them in the temp prefix can exceed the classic
    # Windows path-length limit under nested test or deployment directories.
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Files copied from an archive or another Windows machine can retain the
        # read-only attribute. Runtime profiles are writable working copies, so
        # clear that attribute before atomically replacing an existing file.
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class LocalJsonStore:
    def __init__(self, root: Path | str = DEFAULT_STORE_ROOT) -> None:
        self.root = Path(root)
        self.source_root = self.root / "enterprise_source"
        self.companies_root = self.root / "enterprise_profiles"
        self.enterprise_updates_root = self.root / "enterprise_updates"
        self.runs_root = self.root / "agent_runs"
        self.review_root = self.root / "review_tasks"
        self.fact_candidate_root = self.root / "fact_candidates"
        self.evaluation_runs_root = self.root / "evaluation_runs"
        self.evaluation_profiles_root = self.root / "evaluation_profiles"
        self.evaluation_cards_root = self.root / "evaluation_cards"
        self.evaluation_api_calls_root = self.root / "evaluation_api_calls"
        self.evaluation_plans_root = self.root / "evaluation_acquisition_plans"
        self.evaluation_checkpoints_root = self.root / "evaluation_checkpoints"
        self.raw_api_responses_root = self.root / "raw_api_responses"
        self.audit_path = self.root / "audit" / "events.jsonl"

    def ensure_initialized(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        required_roots = (
            self.source_root, self.companies_root, self.enterprise_updates_root,
            self.runs_root, self.review_root, self.fact_candidate_root,
            self.evaluation_runs_root, self.evaluation_profiles_root,
            self.evaluation_cards_root, self.evaluation_api_calls_root,
            self.evaluation_plans_root, self.evaluation_checkpoints_root,
            self.raw_api_responses_root,
        )
        for path in required_roots:
            path.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        source_companies = [item for item in self.source_root.iterdir() if item.is_dir()]
        if not source_companies:
            raise RuntimeError("企业原始数据目录为空：runtime_data/enterprise_source")
        seen: set[str] = set()
        for source_company in source_companies:
            metadata_path = source_company / "metadata.json"
            current_dir = source_company / "current"
            if not metadata_path.exists() or not current_dir.exists():
                raise RuntimeError(f"企业原始数据结构不完整：{source_company.name}")
            metadata = _read_json(metadata_path)
            company_id = str(metadata.get("company_id") or "").strip()
            if not company_id or company_id in seen:
                raise RuntimeError(f"企业唯一标识缺失或重复：{source_company.name}")
            seen.add(company_id)
            for json_path in current_dir.glob("*.json"):
                _read_json(json_path)
            target = self.company_root(company_id)
            if not target.exists():
                raise RuntimeError(f"企业运行画像缺失：{company_id}")


    def company_root(self, company_id: str) -> Path:
        return self.companies_root / safe_storage_component(company_id)

    def current_path(self, company_id: str, profile_type: str) -> Path:
        filenames = {
            "fact": "fact_profile.json",
            "tag": "tag_profile.json",
            "capability": "capability_profile.json",
            "decision": "decision_profile.json",
            "task": "task_requirements.json",
            "gap": "gap_inventory.json",
        }
        return self.company_root(company_id) / "current" / filenames[profile_type]

    def metadata_path(self, company_id: str) -> Path:
        return self.company_root(company_id) / "metadata.json"

    def list_companies(self) -> list[dict[str, Any]]:
        self.ensure_initialized()
        values: list[dict[str, Any]] = []
        for child in sorted(self.companies_root.iterdir()):
            if not child.is_dir() or not (child / "metadata.json").exists():
                continue
            metadata = _read_json(child / "metadata.json")
            company_id = metadata.get("company_id")
            if not isinstance(company_id, str) or not company_id:
                continue
            fact = self.read_profile(company_id, "fact")
            values.append({
                "company_id": company_id,
                "enterprise": deepcopy(fact.get("enterprise")),
                "data_origin": metadata.get("data_origin", "PROFILE_AGENT_VERIFIED_JSON"),
                "versions": deepcopy(metadata.get("current_versions") or {}),
            })
        return values

    def read_profile(self, company_id: str, profile_type: str) -> dict[str, Any] | None:
        path = self.current_path(company_id, profile_type)
        if not path.exists():
            return None
        value = _read_json(path)
        return value if isinstance(value, dict) else None

    def read_company_context(self, company_id: str) -> dict[str, Any]:
        self.ensure_initialized()
        metadata = _read_json(self.metadata_path(company_id))
        return {
            "company_id": company_id,
            "fact_profile": self.read_profile(company_id, "fact"),
            "tag_profile": self.read_profile(company_id, "tag"),
            "capability_profile": self.read_profile(company_id, "capability"),
            "decision_profile": self.read_profile(company_id, "decision"),
            "task_requirements": self.read_profile(company_id, "task"),
            "metadata": metadata,
        }

    def _profile_identity(self, profile_type: str, profile: dict[str, Any]) -> dict[str, Any]:
        if profile_type == "fact":
            return {
                "profile_id": f"fact-profile:{(profile.get('enterprise') or {}).get('internal_enterprise_id') or (profile.get('enterprise') or {}).get('unified_social_credit_code') or 'unknown'}:{fact_profile_content_hash(profile)[:16]}",
                "content_hash": fact_profile_content_hash(profile),
                "schema_version": profile.get("fact_profile_schema_version"),
                "generated_at_utc": profile.get("generated_at_utc"),
            }
        if profile_type == "capability":
            return {
                "profile_id": profile.get("capability_profile_id"),
                "content_hash": profile.get("capability_content_hash"),
                "schema_version": profile.get("capability_profile_schema_version"),
                "generated_at_utc": profile.get("generated_at_utc"),
            }
        if profile_type == "tag":
            return {
                "profile_id": profile.get("tag_profile_id"),
                "content_hash": profile.get("tag_profile_content_hash") or profile.get("content_hash"),
                "schema_version": profile.get("tag_profile_schema_version"),
                "generated_at_utc": profile.get("generated_at_utc"),
            }
        return {
            "profile_id": profile.get("decision_profile_id"),
            "content_hash": profile.get("decision_profile_content_hash"),
            "schema_version": profile.get("decision_profile_schema_version"),
            "generated_at_utc": profile.get("generated_at_utc"),
            "profile_version": profile.get("profile_version"),
            "previous_profile_id": profile.get("previous_decision_profile_id"),
        }

    def _snapshot_current(
        self,
        company_id: str,
        profile_type: str,
        *,
        version: int,
        update_source: str,
        updated_fields: list[str] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> None:
        profile = self.read_profile(company_id, profile_type)
        if profile is None:
            return
        entry = {
            "version": version,
            "profile_type": profile_type,
            **self._profile_identity(profile_type, profile),
            "update_source": update_source,
            "updated_fields": updated_fields or [],
            "actor": deepcopy(actor),
            "recorded_at_utc": utc_now(),
            "profile": deepcopy(profile),
        }
        history_dir = self.company_root(company_id) / "history" / profile_type
        history_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(history_dir / f"v{version:04d}.json", entry)

    def write_profile(
        self,
        company_id: str,
        profile_type: str,
        profile: dict[str, Any],
        *,
        update_source: str,
        updated_fields: list[str] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = _read_json(self.metadata_path(company_id))
        versions = metadata.setdefault("current_versions", {})
        version = int(versions.get(profile_type, 0)) + 1
        _atomic_json(self.current_path(company_id, profile_type), profile)
        versions[profile_type] = version
        metadata["updated_at_utc"] = utc_now()
        _atomic_json(self.metadata_path(company_id), metadata)
        self._snapshot_current(
            company_id,
            profile_type,
            version=version,
            update_source=update_source,
            updated_fields=updated_fields,
            actor=actor,
        )
        update_record = {
            "company_id": company_id,
            "profile_type": profile_type,
            "version": version,
            "update_source": update_source,
            "updated_fields": updated_fields or [],
            "actor": deepcopy(actor),
            "recorded_at_utc": utc_now(),
            "profile_identity": self._profile_identity(profile_type, profile),
        }
        update_dir = self.enterprise_updates_root / safe_storage_component(company_id)
        _atomic_json(update_dir / f"{profile_type}-v{version:04d}.json", update_record)
        self.audit("profile_updated", {
            "company_id": company_id,
            "profile_type": profile_type,
            "version": version,
            "update_source": update_source,
            **self._profile_identity(profile_type, profile),
        })
        return {"version": version, **self._profile_identity(profile_type, profile)}

    def history(self, company_id: str) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for layer in ("fact", "tag", "capability", "decision"):
            entries = []
            for path in sorted((self.company_root(company_id) / "history" / layer).glob("v*.json")):
                item = _read_json(path)
                if isinstance(item, dict):
                    item = {key: value for key, value in item.items() if key != "profile"}
                    entries.append(item)
            result[layer] = entries
        return result

    def history_entry(self, company_id: str, layer: str, version: int) -> dict[str, Any]:
        return _read_json(self.company_root(company_id) / "history" / layer / f"v{version:04d}.json")

    def _business_json_path(self, root: Path, business_id: str) -> Path:
        return root / f"{safe_storage_component(business_id)}.json"

    def save_run(self, run: dict[str, Any]) -> None:
        _atomic_json(self._business_json_path(self.runs_root, run["run_id"]), run)

    def read_run(self, run_id: str) -> dict[str, Any]:
        path = self._business_json_path(self.runs_root, run_id)
        if not path.exists():
            raise FileNotFoundError(run_id)
        return _read_json(path)

    def list_runs(self, *, company_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.runs_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            item = _read_json(path)
            if company_id and item.get("company_id") != company_id:
                continue
            values.append(item)
            if len(values) >= limit:
                break
        return values

    def save_review_task(self, task: dict[str, Any]) -> None:
        _atomic_json(self._business_json_path(self.review_root, task["review_task_id"]), task)

    def read_review_task(self, task_id: str) -> dict[str, Any]:
        path = self._business_json_path(self.review_root, task_id)
        if not path.exists():
            raise FileNotFoundError(task_id)
        return _read_json(path)

    def list_review_tasks(self, *, company_id: str | None = None) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.review_root.glob("*.json")):
            item = _read_json(path)
            if company_id and item.get("company_id") != company_id:
                continue
            values.append(item)
        return values


    def read_task_requirements(self, company_id: str) -> dict[str, Any] | None:
        return self.read_profile(company_id, "task")

    def save_task_requirements(self, company_id: str, value: dict[str, Any]) -> None:
        _atomic_json(self.current_path(company_id, "task"), value)
        self.audit("task_requirements_saved", {
            "company_id": company_id,
            "requirement_set_id": value.get("requirement_set_id"),
        })

    def read_gap_inventory(self, company_id: str) -> dict[str, Any] | None:
        return self.read_profile(company_id, "gap")

    def save_gap_inventory(self, company_id: str, value: dict[str, Any]) -> None:
        _atomic_json(self.current_path(company_id, "gap"), value)

    def save_fact_candidate(self, candidate: dict[str, Any]) -> None:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("submission_id"))
        _atomic_json(self._business_json_path(self.fact_candidate_root, candidate_id), candidate)

    def list_fact_candidates(self, *, company_id: str | None = None) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for path in sorted(self.fact_candidate_root.glob("*.json")):
            item = _read_json(path)
            if company_id and item.get("company_id") != company_id:
                continue
            values.append(item)
        return values


    # Independent enterprise-evaluation persistence. These methods deliberately
    # do not route through fact/capability/decision profile writers.
    def save_evaluation_run(self, run: dict[str, Any]) -> None:
        _atomic_json(self._business_json_path(self.evaluation_runs_root, run["run_id"]), run)

    def get_evaluation_run(self, run_id: str) -> dict[str, Any]:
        path = self._business_json_path(self.evaluation_runs_root, run_id)
        if not path.exists():
            raise FileNotFoundError(run_id)
        return _read_json(path)

    def list_evaluation_runs(self, *, company_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for path in sorted(self.evaluation_runs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            value = _read_json(path)
            if company_id and value.get("company_id") != company_id:
                continue
            values.append(value)
            if len(values) >= limit:
                break
        return values

    def save_evaluation_profile(self, company_id: str, profile: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        metadata = _read_json(self.metadata_path(company_id))
        versions = metadata.setdefault("current_versions", {})
        previous_version = int(versions.get("evaluation", 0)) or None
        version = int(versions.get("evaluation", 0)) + 1
        stored_profile = deepcopy(profile)
        stored_profile["evaluation_version"] = version
        stored_profile["previous_evaluation_version"] = previous_version
        stored_profile["evaluation_run_id"] = run_id
        current = self.company_root(company_id) / "current" / "evaluation_profile.json"
        _atomic_json(current, stored_profile)
        versions["evaluation"] = version
        metadata["updated_at_utc"] = utc_now()
        _atomic_json(self.metadata_path(company_id), metadata)
        entry = {
            "version": version, "company_id": company_id, "run_id": run_id,
            "content_hash": profile.get("content_hash"), "model_sha256": profile.get("model_sha256"),
            "publication_status": profile.get("publication_status"), "recorded_at_utc": utc_now(),
            "profile": deepcopy(stored_profile),
        }
        history = self.company_root(company_id) / "history" / "evaluation" / f"v{version:04d}.json"
        _atomic_json(history, entry)
        _atomic_json(self._business_json_path(self.evaluation_profiles_root, f"{company_id}-v{version:04d}"), entry)
        return {key: entry[key] for key in ("version", "content_hash", "model_sha256", "publication_status")}

    def get_current_evaluation_profile(self, company_id: str) -> dict[str, Any] | None:
        path = self.company_root(company_id) / "current" / "evaluation_profile.json"
        return _read_json(path) if path.exists() else None

    def get_evaluation_profile_version(self, company_id: str, version: int) -> dict[str, Any]:
        return _read_json(self.company_root(company_id) / "history" / "evaluation" / f"v{version:04d}.json")

    def list_evaluation_profile_versions(self, company_id: str) -> list[dict[str, Any]]:
        result = []
        for path in sorted((self.company_root(company_id) / "history" / "evaluation").glob("v*.json")):
            item = _read_json(path)
            result.append({key: value for key, value in item.items() if key != "profile"})
        return result

    def save_evaluation_card(self, company_id: str, card: dict[str, Any], *, run_id: str) -> None:
        _atomic_json(self.company_root(company_id) / "current" / "evaluation_card.json", card)
        _atomic_json(self._business_json_path(self.evaluation_cards_root, run_id), {"run_id": run_id, "company_id": company_id, "card": card})

    def get_current_evaluation_card(self, company_id: str) -> dict[str, Any] | None:
        path = self.company_root(company_id) / "current" / "evaluation_card.json"
        return _read_json(path) if path.exists() else None

    def save_api_call_record(self, record: dict[str, Any]) -> None:
        _atomic_json(self._business_json_path(self.evaluation_api_calls_root, record["api_call_id"]), record)

    def list_api_call_records(self, *, run_id: str | None = None, company_id: str | None = None) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.evaluation_api_calls_root.glob("*.json")):
            item = _read_json(path)
            if run_id and item.get("run_id") != run_id:
                continue
            if company_id and item.get("company_id") != company_id:
                continue
            result.append(item)
        return result

    def save_acquisition_plan(self, plan: dict[str, Any]) -> None:
        _atomic_json(self._business_json_path(self.evaluation_plans_root, plan["plan_id"]), plan)

    def get_acquisition_plan(self, plan_id: str) -> dict[str, Any]:
        path = self._business_json_path(self.evaluation_plans_root, plan_id)
        if not path.exists():
            raise FileNotFoundError(plan_id)
        return _read_json(path)

    def current_bundle(self, company_id: str) -> dict[str, Any]:
        metadata = _read_json(self.metadata_path(company_id))
        return {
            "metadata": metadata,
            "profiles": {
                key: self.read_profile(company_id, key)
                for key in ("fact", "tag", "capability", "decision", "task", "gap")
            },
        }

    def restore_current_bundle(self, company_id: str, bundle: dict[str, Any]) -> None:
        for key, value in (bundle.get("profiles") or {}).items():
            path = self.current_path(company_id, key)
            if value is None:
                if path.exists():
                    path.unlink()
            else:
                _atomic_json(path, value)
        _atomic_json(self.metadata_path(company_id), bundle["metadata"])
        self.audit("profile_change_rolled_back", {"company_id": company_id})

    def save_raw_api_response(self, record: dict[str, Any]) -> dict[str, Any]:
        raw_ref=str(record.get("raw_response_ref") or f"raw-api-response:{safe_storage_component(record.get('api_call_id','unknown'))}")
        stored={**deepcopy(record),"raw_response_ref":raw_ref}
        _atomic_json(self._business_json_path(self.raw_api_responses_root,raw_ref),stored)
        return {"raw_response_ref":raw_ref,"response_hash":record.get("response_hash")}

    def get_raw_api_response(self, raw_response_ref: str) -> dict[str, Any]:
        path=self._business_json_path(self.raw_api_responses_root,raw_response_ref)
        if not path.exists(): raise FileNotFoundError(raw_response_ref)
        return _read_json(path)

    def find_valid_api_cache(self, cache_key: str, *, at_utc: str) -> dict[str, Any] | None:
        for item in reversed(self.list_api_call_records()):
            if item.get("cache_key")!=cache_key or item.get("status")!="OK": continue
            valid=item.get("valid_until")
            if valid is None:
                continue
            if str(valid)>=str(at_utc): return item
        return None

    def save_evaluation_checkpoint(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        stored={"run_id":run_id,"checkpoint_version":state.get("checkpoint_version",0),"saved_at_utc":utc_now(),"state":deepcopy(state)}
        _atomic_json(self._business_json_path(self.evaluation_checkpoints_root,run_id),stored)
        return {k:stored[k] for k in ("run_id","checkpoint_version","saved_at_utc")}

    def get_evaluation_checkpoint(self, run_id: str) -> dict[str, Any]:
        path=self._business_json_path(self.evaluation_checkpoints_root,run_id)
        if not path.exists(): raise FileNotFoundError(run_id)
        return _read_json(path)

    def audit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"event_type": event_type, "occurred_at_utc": utc_now(), **deepcopy(payload)}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
