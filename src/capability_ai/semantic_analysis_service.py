"""Orchestrate strict semantic capability analysis with the official Zhipu provider."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from src.ability_analysis_request import assert_valid_analysis_request
from src.ability_analysis_response_validator import validate_capability_analysis_response
from src.capability_evidence_policy import load_capability_evidence_policy

from .audit import build_audit_summary, response_content_hash, utc_now
from .errors import CapabilityAIError
from .prompt_builder import PromptBuilder
from .provider_config import CapabilityAIConfig
from .provider_registry import get_provider
from .response_parser import ResponseParser
from .semantic_candidates import build_semantic_candidates


def _normalize_known_text_item(item: Any) -> Any:
    """Convert one known structured text item into a deterministic string.

    Only the exact known shape is accepted:
    ``code`` / ``reason`` / ``required_data``.
    Unknown keys or invalid value types are deliberately left unchanged so
    strict schema validation can still reject them.
    """
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return item

    allowed_keys = {"code", "reason", "required_data"}
    if not set(item).issubset(allowed_keys):
        return item

    code = item.get("code")
    reason = item.get("reason")
    required_data = item.get("required_data")

    if code is not None and not isinstance(code, str):
        return item
    if reason is not None and not isinstance(reason, str):
        return item

    if required_data is None:
        required_items: list[str] = []
    elif isinstance(required_data, str):
        required_items = [required_data]
    elif isinstance(required_data, list) and all(
        isinstance(value, str) for value in required_data
    ):
        required_items = required_data
    else:
        return item

    parts: list[str] = []
    if isinstance(code, str) and code.strip():
        parts.append(f"[{code.strip()}]")
    if isinstance(reason, str) and reason.strip():
        parts.append(reason.strip())

    cleaned_required = [value.strip() for value in required_items if value.strip()]
    if cleaned_required:
        parts.append("需要补充：" + "；".join(cleaned_required))

    normalized = " ".join(parts).strip()
    return normalized if normalized else item


def _normalize_known_text_array(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [_normalize_known_text_item(item) for item in value]




_SUPPORT_STATUS_ALIASES = {"insufficient_data": "ambiguous"}
_KNOWN_GENERIC_KIND_ALIASES = {"industry_scope_candidate"}

_FACT_KIND_RULES: dict[tuple[str, str, str | None], str] = {
    ("industry_capability", "business_registration", None): "registered_industry_context",
    ("industry_capability", "bid_participation", None): "market_participation_industry_candidate",
    ("industry_capability", "performance", None): "delivered_industry_candidate",
    ("industry_capability", "fulfillment", None): "delivered_industry_candidate",
    ("technical_capability", "qualification", None): "qualification_capability_candidate",
    ("technical_capability", "personnel_certificate", None): "personnel_certificate_candidate",
    ("technical_capability", "performance", None): "technical_experience_candidate",
    ("technical_capability", "fulfillment", None): "technical_experience_candidate",
    ("technical_capability", "other_enterprise_fact", "product_business"): "product_business_candidate",
    ("similar_performance_capability", "performance", None): "similar_performance_candidate",
    ("similar_performance_capability", "fulfillment", None): "similar_performance_candidate",
    ("regional_delivery_capability", "other_enterprise_fact", "organization_network"): "service_presence_region_candidate",
    ("regional_delivery_capability", "bid_participation", None): "market_participation_region_candidate",
    ("regional_delivery_capability", "performance", None): "historical_delivery_region_candidate",
    ("regional_delivery_capability", "fulfillment", None): "historical_delivery_region_candidate",
    ("amount_experience_capability", "performance", None): "normalized_amount_experience_candidate",
    ("amount_experience_capability", "fulfillment", None): "normalized_amount_experience_candidate",
    ("amount_experience_capability", "bid_award", None): "normalized_amount_experience_candidate",
    ("personnel_resource_capability", "personnel", None): "personnel_resource_candidate",
    ("personnel_resource_capability", "personnel_certificate", None): "personnel_certificate_candidate",
    ("personnel_resource_capability", "other_enterprise_fact", "organization_network"): "organization_resource_candidate",
    ("buyer_relationship_capability", "buyer_relationship", None): "buyer_relationship_candidate",
    ("buyer_relationship_capability", "performance", None): "buyer_relationship_candidate",
    ("buyer_relationship_capability", "fulfillment", None): "buyer_relationship_candidate",
    ("buyer_relationship_capability", "bid_award", None): "buyer_relationship_candidate",
    ("tender_performance_capability", "bid_participation", None): "tender_participation_candidate",
    ("tender_performance_capability", "bid_award", None): "confirmed_award_history_candidate",
    ("tender_performance_capability", "fulfillment", None): "confirmed_fulfillment_history_candidate",
}

_VALUE_KIND_HINTS: dict[str, dict[str, str]] = {
    "industry_capability": {
        "registered_industry": "registered_industry_context",
        "registered_industries": "registered_industry_context",
        "registered_industry_context": "registered_industry_context",
        "market_participation_industries": "market_participation_industry_candidate",
        "delivered_project_industries": "delivered_industry_candidate",
        "historical_delivered_project_industries": "delivered_industry_candidate",
    },
}


def _normalize_candidate_kind(
    candidate: dict[str, Any],
    *,
    request: dict[str, Any],
) -> None:
    capability_type = candidate.get("capability_type")
    if not isinstance(capability_type, str):
        return
    policy = load_capability_evidence_policy()
    domain = (policy.get("domains") or {}).get(capability_type)
    if not isinstance(domain, dict):
        return
    allowed = set(domain.get("allowed_candidate_kinds") or [])
    current = candidate.get("candidate_kind")
    if current in allowed:
        return
    if current not in _KNOWN_GENERIC_KIND_ALIASES:
        return

    # Candidate kind is a controlled taxonomy label. Derive it only when the
    # cited evidence makes one policy kind unambiguous; otherwise strict
    # validation still rejects the model output.
    fact_index = {
        fact.get("fact_id"): fact
        for fact in request.get("allowed_facts", [])
        if isinstance(fact, dict) and fact.get("fact_id")
    }
    possible: set[str] = set()
    for fact_id in candidate.get("source_fact_ids") or []:
        fact = fact_index.get(fact_id)
        if not isinstance(fact, dict):
            continue
        fact_type = fact.get("fact_type")
        subtype = (fact.get("payload") or {}).get("subtype")
        kind = _FACT_KIND_RULES.get((capability_type, fact_type, subtype))
        if kind is None:
            kind = _FACT_KIND_RULES.get((capability_type, fact_type, None))
        if kind in allowed:
            possible.add(kind)

    hinted_kinds: set[str] = set()
    value = candidate.get("candidate_value")
    if isinstance(value, dict):
        hints = _VALUE_KIND_HINTS.get(capability_type, {})
        for key in value:
            hinted = hints.get(key)
            if hinted in allowed:
                hinted_kinds.add(hinted)

    if len(hinted_kinds) == 1:
        candidate["candidate_kind"] = next(iter(hinted_kinds))
    elif len(possible) == 1:
        candidate["candidate_kind"] = next(iter(possible))


def _normalize_rejected_reason(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    if "reason" in normalized:
        normalized["reason"] = _normalize_known_text_item(normalized["reason"])
    elif isinstance(normalized.get("capability_type"), str) and isinstance(
        normalized.get("candidate_subject"), str
    ):
        # Some GLM responses correctly identify an unsupported candidate but
        # omit the explanatory text.  Filling this neutral, non-factual reason
        # preserves the rejection while keeping strict validation for every
        # other missing or unknown field.
        normalized["reason"] = "现有证据不足，未形成可供确认的能力建议。"
    return normalized

def _normalize_capability_analysis_response(
    response: Any,
    *,
    request: dict[str, Any],
) -> Any:
    """Apply narrowly scoped response normalization before strict validation."""
    if not isinstance(response, dict):
        return response

    normalized = dict(response)

    # request_content_hash is deterministic and system-owned. Only fill it when
    # absent. A model-supplied but incorrect value must still fail validation.
    if "request_content_hash" not in normalized:
        normalized["request_content_hash"] = request["request_content_hash"]

    # These are known input echoes and are not part of the response schema.
    normalized.pop("enterprise", None)
    normalized.pop("as_of_date", None)

    # Some providers occasionally append a top-level bid-decision estimate even
    # though the capability prompt explicitly forbids it. It is outside this
    # agent's responsibility and must never enter the capability profile. Drop
    # only these known top-level decision fields; unknown fields and forbidden
    # content inside candidates still fail strict validation.
    for field_name in (
        "winning_probability",
        "win_probability",
        "competition_analysis",
        "recommendation",
        "project_recommendation",
    ):
        normalized.pop(field_name, None)

    if "global_unknowns" in normalized:
        normalized["global_unknowns"] = _normalize_known_text_array(
            normalized["global_unknowns"]
        )

    candidates = normalized.get("capability_candidates")
    if isinstance(candidates, list):
        normalized_candidates: list[Any] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                normalized_candidates.append(candidate)
                continue

            normalized_candidate = dict(candidate)
            status = normalized_candidate.get("support_status")
            if status in _SUPPORT_STATUS_ALIASES:
                normalized_candidate["support_status"] = _SUPPORT_STATUS_ALIASES[status]
            # GLM may return a plain list for a single list-valued capability.
            # The contract deliberately keeps candidate_value as an object so
            # downstream domains can evolve without changing the outer shape.
            # Preserve the model values under a neutral key instead of failing
            # the whole analysis batch solely because the wrapper was omitted.
            candidate_value = normalized_candidate.get("candidate_value")
            if isinstance(candidate_value, list):
                normalized_candidate["candidate_value"] = {
                    "items": candidate_value
                }
            for field_name in ("limitations", "unknowns"):
                if field_name in normalized_candidate:
                    normalized_candidate[field_name] = _normalize_known_text_array(
                        normalized_candidate[field_name]
                    )
            _normalize_candidate_kind(normalized_candidate, request=request)
            normalized_candidates.append(normalized_candidate)
        normalized["capability_candidates"] = normalized_candidates

    rejected = normalized.get("rejected_or_unsupported_candidates")
    if isinstance(rejected, list):
        normalized["rejected_or_unsupported_candidates"] = [
            _normalize_rejected_reason(item) for item in rejected
        ]

    return normalized


@dataclass(slots=True)
class SemanticAnalysisRunResult:
    candidates: dict[str, Any]
    audit: dict[str, Any]
    prompt: dict[str, Any]
    response: dict[str, Any]
    provider_call_audit: dict[str, Any] | None = None


class SemanticCapabilityAnalysisService:
    def __init__(self, config: CapabilityAIConfig) -> None:
        self.config = config.validate()
        self.last_audit = None
        self.last_prompt = None
        self.last_failure_record = None
        self.last_provider_call_audit = None

    def run(
        self,
        request: dict[str, Any],
        *,
        invocation_context: dict[str, Any] | None = None,
        generated_at_utc: str | None = None,
    ) -> SemanticAnalysisRunResult:
        ctx = dict(invocation_context or {})
        started = utc_now()
        start_ns = time.monotonic_ns()
        provider_name = self.config.provider
        provider_mode = "external"
        prompt = None
        provider = None
        attempt_count = 0
        response_hash = None

        try:
            try:
                assert_valid_analysis_request(request)
            except Exception as exc:
                raise CapabilityAIError(
                    "analysis_request_invalid",
                    "request_validation",
                    str(exc),
                ) from exc

            prompt = PromptBuilder(
                prompt_version=self.config.prompt_version
            ).build(request)
            self.last_prompt = prompt
            provider = get_provider(provider_name, self.config)
            provider_mode = provider.provider_mode
            repair_used = False

            while True:
                raw = provider.analyze(
                    request=request,
                    prompt=prompt,
                    invocation_context=ctx,
                )
                call_audit = dict(
                    getattr(provider, "last_call_audit", {}) or {}
                )
                attempt_count += int(call_audit.get("attempt_count") or 1)
                self.last_provider_call_audit = call_audit

                try:
                    response = ResponseParser(
                        max_response_bytes=self.config.max_response_bytes
                    ).parse(raw)
                    response = _normalize_capability_analysis_response(
                        response,
                        request=request,
                    )
                    issues = validate_capability_analysis_response(
                        response,
                        request,
                    )
                    if issues:
                        first = issues[0]
                        raise CapabilityAIError(
                            first["code"],
                            "response_validation",
                            first["message"],
                            {"field_path": first.get("field_path", "$")},
                        )
                    break
                except CapabilityAIError as exc:
                    retryable = (
                        not repair_used
                        and exc.stage in {"response_parsing", "response_validation"}
                        and exc.code != "provider_response_too_large"
                    )
                    if not retryable:
                        if exc.code.startswith(
                            "provider_response_"
                        ):
                            exc = CapabilityAIError(
                                f"{provider_name}_response_json_invalid",
                                exc.stage,
                                exc.message,
                                exc.details,
                            )
                        elif exc.stage == "response_validation":
                            exc = CapabilityAIError(
                                f"{provider_name}_response_schema_invalid",
                                exc.stage,
                                exc.message,
                                exc.details,
                            )
                        raise exc

                    repair_used = True
                    ctx = {
                        **ctx,
                        "repair_error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details or {},
                        },
                    }

            response_hash = response_content_hash(response)
            finished = utc_now()
            duration = max(
                0,
                (time.monotonic_ns() - start_ns) // 1_000_000,
            )
            audit = build_audit_summary(
                provider_name=provider.provider_name,
                provider_mode=provider.provider_mode,
                model_name=self.config.model_name,
                prompt_version=prompt["prompt_schema_version"],
                request_content_hash=request["request_content_hash"],
                prompt_content_hash=prompt["prompt_content_hash"],
                response_hash=response_hash,
                started_at_utc=started,
                finished_at_utc=finished,
                duration_ms=duration,
                attempt_count=attempt_count,
                network_used=provider.network_used,
                validation_result="passed",
                config_source=self.config.provider_source,
            )
            if self.last_provider_call_audit is not None:
                self.last_provider_call_audit.update(
                    {
                        "validation_result": "passed",
                        "prompt_hash": prompt["prompt_content_hash"],
                        "response_hash": response_hash,
                        "finished_at_utc": finished,
                    }
                )

            candidates = build_semantic_candidates(
                request=request,
                prompt=prompt,
                response=response,
                provider_name=provider.provider_name,
                provider_mode=provider.provider_mode,
                model_name=self.config.model_name,
                audit_summary=audit,
                generated_at_utc=generated_at_utc or finished,
            )
            self.last_audit = audit
            self.last_failure_record = None
            return SemanticAnalysisRunResult(
                candidates,
                audit,
                prompt,
                response,
                self.last_provider_call_audit,
            )
        except CapabilityAIError as exc:
            finished = utc_now()
            duration = max(
                0,
                (time.monotonic_ns() - start_ns) // 1_000_000,
            )
            network = bool(getattr(provider, "network_used", False))
            ph = (
                prompt.get("prompt_content_hash")
                if isinstance(prompt, dict)
                else None
            )
            rh = (
                request.get("request_content_hash")
                if isinstance(request, dict)
                else None
            )
            audit = build_audit_summary(
                provider_name=provider_name,
                provider_mode=provider_mode,
                model_name=self.config.model_name,
                prompt_version=self.config.prompt_version,
                request_content_hash=rh or "unknown",
                prompt_content_hash=ph or "unknown",
                response_hash=response_hash,
                started_at_utc=started,
                finished_at_utc=finished,
                duration_ms=duration,
                attempt_count=attempt_count,
                network_used=network,
                validation_result="failed",
                config_source=self.config.provider_source,
                error_code=exc.code,
            )
            self.last_audit = audit
            self.last_provider_call_audit = (
                dict(getattr(provider, "last_call_audit", {}) or {})
                if provider
                else None
            )
            if self.last_provider_call_audit is not None:
                self.last_provider_call_audit.update(
                    {
                        "prompt_hash": ph,
                        "response_hash": response_hash,
                        "finished_at_utc": finished,
                    }
                )
            self.last_failure_record = {
                "run_status": "failed",
                "provider": {
                    "provider_name": provider_name,
                    "provider_mode": provider_mode,
                    "model_name": self.config.model_name,
                },
                "request_content_hash": rh,
                "prompt_content_hash": ph,
                "error": exc.as_dict(),
                "audit_summary": audit,
                "provider_call_audit": self.last_provider_call_audit,
            }
            raise
