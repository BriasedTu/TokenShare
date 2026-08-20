"""Experiment-layer model policy planning for paper suites."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
    load_ai_api_config,
)
from tokenshare.experiments.paper_model_identity import (
    PaperModelEndpointIdentity,
    PaperModelIdentityMismatch,
    build_model_endpoint_identity,
    validate_fixed_entry_config_identity,
)
from tokenshare.experiments.paper_exp5_smoke_evidence import (
    validate_exp5_smoke_evidence_bundle,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    digest_json,
)


POLICY_STATUS_PLANNED = "planned"
POLICY_STATUS_BLOCKED = "blocked"
LEGACY_MODEL_POLICIES = ("strong_only", "weak_only", "mixed")
FIXED_ENTRY_MODEL_POLICY = "fixed_entry"
LEGACY_PAPER_MODEL_ENDPOINT_COHORT_ID = "tokenshare.paper.model_endpoint_cohort.v1"
LEGACY_MODEL_ENDPOINT_COHORT_SCHEMA_VERSION = (
    "tokenshare.paper_model_endpoint_cohort.v1"
)
PAPER_MODEL_ENDPOINT_COHORT_ID = "tokenshare.paper.model_endpoint_cohort.v2"
MODEL_ENDPOINT_COHORT_SCHEMA_VERSION = "tokenshare.paper_model_endpoint_cohort.v2"
PAPER_MODEL_ENDPOINT_COHORT_V3_ID = "tokenshare.paper.model_endpoint_cohort.v3"
MODEL_ENDPOINT_COHORT_V3_SCHEMA_VERSION = (
    "tokenshare.paper_model_endpoint_cohort.v3"
)
MODEL_ENTRY_MAP_SCHEMA_VERSION = "tokenshare.paper_model_entry_map.v1"
MODEL_ENDPOINT_COHORT_PRELIGHT_SCHEMA_VERSION = (
    "tokenshare.paper_model_endpoint_cohort_preflight.v1"
)
SMOKE_EVIDENCE_SCHEMA_VERSION = "tokenshare.paper_model_endpoint_smoke_evidence.v1"
V3_SMOKE_EVIDENCE_SCHEMA_VERSION = (
    "tokenshare.paper_model_endpoint_smoke_evidence.v2"
)
PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS = (
    "glm_5_2_siliconflow",
    "deepseek_v4_pro_deepseek",
    "gpt_5_6_sol_high_openai",
)
PAPER_MODEL_ENDPOINT_COHORT_MEMBERS = {
    "glm_5_2_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "thinking",
        "request_overrides": {"enable_thinking": True},
        "max_tokens": 8192,
    },
    "deepseek_v4_pro_deepseek": {
        "provider_family": "deepseek",
        "provider_model_id": "deepseek-v4-pro",
        "reasoning_profile_id": "high",
        "request_overrides": {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        "max_tokens": 8192,
    },
    "gpt_5_6_sol_high_openai": {
        "provider_family": "openai",
        "provider_model_id": "gpt-5.6-sol",
        "reasoning_profile_id": "high",
        "request_overrides": {"reasoning_effort": "high"},
        "max_tokens": 8192,
    },
}
PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_14b_siliconflow",
    "minimax_m2_5_siliconflow",
    "deepseek_v3_pro_siliconflow",
)
_V3_PRICING_SOURCE_URL = "https://siliconflow.cn/pricing"
_V3_PRICING_ACCESSED_AT = "2026-08-15"
EXP5_PRICING_FRESHNESS_AS_OF = "2026-08-15"
EXP5_PRICING_MAX_AGE_DAYS = 7
EXP5_PRICING_FRESHNESS_AUTHORITY_SCHEMA_VERSION = (
    "tokenshare.exp5_pricing_freshness_authority.v2"
)
PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS = {
    "glm_5_2_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "thinking",
        "request_overrides": {
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        "max_tokens": 32768,
        "api_key_env": "SILICONFLOW_API_KEY",
        "pricing": {
            "currency": "CNY",
            "cached_input_per_million_tokens": 2.0,
            "uncached_input_per_million_tokens": 8.0,
            "output_per_million_tokens": 28.0,
            "source_url": _V3_PRICING_SOURCE_URL,
            "accessed_at": _V3_PRICING_ACCESSED_AT,
        },
    },
    "qwen3_14b_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "Qwen/Qwen3-14B",
        "reasoning_profile_id": "thinking",
        "request_overrides": {
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        "max_tokens": 32768,
        "api_key_env": "SILICONFLOW_API_KEY",
        "pricing": {
            "currency": "CNY",
            "input_per_million_tokens": 0.5,
            "output_per_million_tokens": 2.0,
            "source_url": _V3_PRICING_SOURCE_URL,
            "accessed_at": _V3_PRICING_ACCESSED_AT,
        },
    },
    "minimax_m2_5_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "MiniMaxAI/MiniMax-M2.5",
        "reasoning_profile_id": "thinking",
        "request_overrides": {
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        "max_tokens": 32768,
        "api_key_env": "SILICONFLOW_API_KEY",
        "pricing": {
            "currency": "CNY",
            "cached_input_per_million_tokens": 0.21,
            "uncached_input_per_million_tokens": 2.1,
            "output_per_million_tokens": 8.4,
            "source_url": _V3_PRICING_SOURCE_URL,
            "accessed_at": _V3_PRICING_ACCESSED_AT,
        },
    },
    "deepseek_v3_pro_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "Pro/deepseek-ai/DeepSeek-V3",
        "reasoning_profile_id": "default",
        "request_overrides": {"enable_thinking": False},
        "max_tokens": 32768,
        "api_key_env": "SILICONFLOW_API_KEY",
        "pricing": {
            "currency": "CNY",
            "cached_input_per_million_tokens": 0.2,
            "uncached_input_per_million_tokens": 2.0,
            "output_per_million_tokens": 8.0,
            "source_url": _V3_PRICING_SOURCE_URL,
            "accessed_at": _V3_PRICING_ACCESSED_AT,
        },
    },
}
EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS = (
    "stream",
    "timeout_seconds",
    "max_tokens",
    "max_provider_attempts",
)
EXP5_PROVIDER_SPECIFIC_REASONING_CONTROL_FIELDS = (
    "enable_thinking",
    "thinking_budget",
    "thinking",
    "reasoning_effort",
)


def exp5_provider_specific_reasoning_controls(
    request_controls: Mapping[str, object],
) -> JsonObject:
    """按同一预注册字段集投影 Exp5 provider-specific reasoning 参数。"""
    return {
        field_name: request_controls[field_name]
        for field_name in EXP5_PROVIDER_SPECIFIC_REASONING_CONTROL_FIELDS
        if field_name in request_controls
    }


_LEGACY_COHORT_MEMBER_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_6_27b_siliconflow",
    "gpt_5_6_sol_high_openai",
)
_LEGACY_COHORT_MEMBERS = {
    "glm_5_2_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "default",
    },
    "qwen3_6_27b_siliconflow": {
        "provider_family": "siliconflow",
        "provider_model_id": "Qwen/Qwen3.6-27B",
        "reasoning_profile_id": "default",
    },
    "gpt_5_6_sol_high_openai": {
        "provider_family": "openai",
        "provider_model_id": "gpt-5.6-sol",
        "reasoning_profile_id": "high",
    },
}
_LEGACY_COMPARABLE_REQUEST_CONTROL_FIELDS = (
    "temperature",
    "top_p",
    "stream",
    "timeout_seconds",
    "max_tokens",
    "max_provider_attempts",
)
_COHORT_DEFINITIONS = {
    LEGACY_PAPER_MODEL_ENDPOINT_COHORT_ID: {
        "schema_version": LEGACY_MODEL_ENDPOINT_COHORT_SCHEMA_VERSION,
        "member_ids": _LEGACY_COHORT_MEMBER_IDS,
        "members": _LEGACY_COHORT_MEMBERS,
        "comparable_fields": _LEGACY_COMPARABLE_REQUEST_CONTROL_FIELDS,
        "required_common_controls": {
            "stream": False,
            "timeout_seconds": 100,
            "max_tokens": 8192,
            "max_provider_attempts": 1,
        },
        "smoke_evidence_schema_version": SMOKE_EVIDENCE_SCHEMA_VERSION,
    },
    PAPER_MODEL_ENDPOINT_COHORT_ID: {
        "schema_version": MODEL_ENDPOINT_COHORT_SCHEMA_VERSION,
        "member_ids": PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
        "members": PAPER_MODEL_ENDPOINT_COHORT_MEMBERS,
        "comparable_fields": EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
        "required_common_controls": {
            "stream": False,
            "timeout_seconds": 100,
            "max_tokens": 8192,
            "max_provider_attempts": 1,
        },
        "smoke_evidence_schema_version": SMOKE_EVIDENCE_SCHEMA_VERSION,
    },
    PAPER_MODEL_ENDPOINT_COHORT_V3_ID: {
        "schema_version": MODEL_ENDPOINT_COHORT_V3_SCHEMA_VERSION,
        "member_ids": PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
        "members": PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS,
        "comparable_fields": (
            "temperature",
            "top_p",
            "stream",
            "timeout_seconds",
            "max_tokens",
            "max_provider_attempts",
        ),
        "required_common_controls": {
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
            "timeout_seconds": 600,
            "max_tokens": 32768,
            "max_provider_attempts": 1,
        },
        "smoke_evidence_schema_version": V3_SMOKE_EVIDENCE_SCHEMA_VERSION,
    },
}
EXP5_DOMAIN_EXECUTION_CONTRACTS = {
    "factorization": {
        "prompt_profile": "factorization.bounded_range_prompt.v1",
        "parser_id": "factorization.range_result.parser.v1",
        "plugin_version": "0.1.0",
    },
    "lean_proof": {
        "prompt_profile": "lean_proof.proof_candidate_prompt.v1",
        "parser_id": "lean_proof.proof_candidate.parser.v1",
        "plugin_version": "0.1.0",
    },
}


def load_model_endpoint_cohort(path: str | Path) -> JsonObject:
    body = _load_json_file(path)
    cohort_id = body.get("cohort_id")
    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValueError("model endpoint cohort requires cohort_id")
    definition = _COHORT_DEFINITIONS.get(cohort_id)
    if definition is None or body.get("schema_version") != definition["schema_version"]:
        raise ValueError("unsupported model endpoint cohort schema")
    members = body.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("model endpoint cohort requires members")
    seen: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("model endpoint cohort members must be objects")
        member_id = _required_str(member, "cohort_member_id")
        if member_id in seen:
            raise ValueError(f"duplicate cohort member: {member_id}")
        seen.add(member_id)
        _required_str(member, "provider_family")
        _required_str(member, "provider_model_id")
        _required_str(member, "reasoning_profile_id")
        if cohort_id in {
            PAPER_MODEL_ENDPOINT_COHORT_ID,
            PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        }:
            if not isinstance(member.get("request_overrides"), dict):
                raise ValueError("model endpoint cohort requires request_overrides")
            max_tokens = member.get("max_tokens")
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
                raise ValueError("model endpoint cohort requires integer max_tokens")
    result = dict(body)
    result["model_cohort_digest"] = digest_json(body)
    return result


def load_model_entry_map(path: str | Path) -> JsonObject:
    body = _load_json_file(path)
    if body.get("schema_version") != MODEL_ENTRY_MAP_SCHEMA_VERSION:
        raise ValueError("unsupported model entry map schema")
    if not isinstance(body.get("cohort_id"), str) or not body["cohort_id"]:
        raise ValueError("model entry map requires cohort_id")
    members = body.get("members")
    if not isinstance(members, dict):
        raise ValueError("model entry map requires members object")
    for member_id, spec in members.items():
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("model entry map member ids must be non-empty strings")
        if not isinstance(spec, dict):
            raise ValueError("model entry map member specs must be objects")
        _required_str(spec, "provider_config_id")
        _required_str(spec, "entry_id")
    return dict(body)


def load_provider_config_map(
    provider_config_paths: dict[str, str | Path],
) -> dict[str, AIAPIExecutorConfig]:
    configs: dict[str, AIAPIExecutorConfig] = {}
    for provider_config_id, path in sorted(provider_config_paths.items()):
        if not provider_config_id:
            raise ValueError("provider config id must be non-empty")
        config = load_ai_api_config(_load_json_file(path))
        if config.provider_family != provider_config_id:
            raise ValueError(
                "provider config id must match provider_family: "
                f"{provider_config_id} != {config.provider_family}"
            )
        configs[provider_config_id] = config
    return configs


def build_model_endpoint_cohort_preflight(
    *,
    cohort: JsonObject,
    entry_map: JsonObject,
    provider_configs: dict[str, AIAPIExecutorConfig],
    require_smoke_evidence: bool = True,
    smoke_evidence_bundle: Mapping[str, Any] | None = None,
    api_key_presence_resolver: Callable[[str], bool] | None = None,
    pricing_freshness_as_of: str | None = None,
) -> JsonObject:
    if api_key_presence_resolver is not None and not callable(
        api_key_presence_resolver
    ):
        raise TypeError("api_key_presence_resolver must be callable")
    key_is_available = api_key_presence_resolver or (
        lambda name: bool(os.environ.get(name, ""))
    )
    cohort_id = str(cohort.get("cohort_id") or "")
    cohort_digest = str(cohort.get("model_cohort_digest") or digest_json(cohort))
    members_by_id = _cohort_members_by_id(cohort)
    definition = _COHORT_DEFINITIONS.get(cohort_id)
    if definition is None:
        raise ValueError("unsupported model endpoint cohort id")
    expected_member_ids_ordered = tuple(definition["member_ids"])
    expected_members = dict(definition["members"])
    comparable_fields = tuple(definition["comparable_fields"])
    required_common_controls = dict(definition["required_common_controls"])
    entry_specs = entry_map.get("members")
    if not isinstance(entry_specs, dict):
        entry_specs = {}

    missing_members: list[str] = []
    unexpected_members: list[str] = []
    unexpected_entry_map_members: list[str] = []
    missing_provider_configs: list[str] = []
    missing_entry_ids: list[str] = []
    ineligible_members: list[JsonObject] = []
    member_plans: dict[str, JsonObject] = {}
    cohort_level_reasons: list[str] = []
    smoke_bundle_validation: JsonObject | None = None
    if (
        require_smoke_evidence
        and cohort_id == PAPER_MODEL_ENDPOINT_COHORT_V3_ID
    ):
        smoke_bundle_validation = validate_exp5_smoke_evidence_bundle(
            smoke_evidence_bundle,
            entry_map=entry_map,
            model_cohort_id=cohort_id,
            model_cohort_digest=cohort_digest,
            expected_member_ids=expected_member_ids_ordered,
        )

    expected_member_ids = set(expected_member_ids_ordered)
    actual_member_ids = set(members_by_id)
    entry_map_member_ids = {str(member_id) for member_id in entry_specs}
    if entry_map.get("cohort_id") != cohort_id:
        cohort_level_reasons.append("entry_map_cohort_id_mismatch")
    if actual_member_ids != expected_member_ids:
        cohort_level_reasons.append("cohort_member_set_mismatch")
        unexpected_members = sorted(actual_member_ids - expected_member_ids)
    unexpected_entry_map_members = sorted(entry_map_member_ids - expected_member_ids)
    if unexpected_entry_map_members:
        cohort_level_reasons.append("entry_map_member_set_mismatch")

    for member_id in expected_member_ids_ordered:
        expected_member = expected_members[member_id]
        member = members_by_id.get(member_id)
        spec = entry_specs.get(member_id)
        if member is None or not isinstance(spec, dict):
            missing_members.append(member_id)
            continue

        provider_config_id = str(spec.get("provider_config_id") or "")
        entry_id = str(spec.get("entry_id") or "")
        provider_family = str(expected_member["provider_family"])
        provider_model_id = str(expected_member["provider_model_id"])
        reasoning_profile_id = str(expected_member["reasoning_profile_id"])
        config = provider_configs.get(provider_config_id)
        selected_entry: AIAPIProviderEntry | None = None
        endpoint_identity: PaperModelEndpointIdentity | None = None
        pricing_snapshot: JsonObject | None = None
        pricing_freshness: JsonObject | None = None
        blocked_reasons: list[str] = []
        if str(member.get("provider_family") or "") != provider_family:
            blocked_reasons.append("cohort_provider_family_mismatch")
        if str(member.get("provider_model_id") or "") != provider_model_id:
            blocked_reasons.append("cohort_model_mismatch")
        if str(member.get("reasoning_profile_id") or "") != reasoning_profile_id:
            blocked_reasons.append("cohort_reasoning_profile_mismatch")
        expected_overrides = expected_member.get("request_overrides")
        if expected_overrides is not None and not _strict_json_equal(
            member.get("request_overrides"),
            expected_overrides,
        ):
            blocked_reasons.append("cohort_request_overrides_mismatch")
        expected_max_tokens = expected_member.get("max_tokens")
        if expected_max_tokens is not None and member.get("max_tokens") != expected_max_tokens:
            blocked_reasons.append("cohort_max_tokens_mismatch")
        if config is None:
            missing_provider_configs.append(provider_config_id)
            blocked_reasons.append("missing_provider_config")
        else:
            try:
                endpoint_identity = build_model_endpoint_identity(
                    model_cohort_id=cohort_id,
                    model_cohort_digest=cohort_digest,
                    cohort_member_id=member_id,
                    provider_config_id=provider_config_id,
                    selected_entry_id=entry_id,
                    expected_provider_family=provider_family,
                    expected_provider_model_id=provider_model_id,
                    expected_reasoning_profile_id=reasoning_profile_id,
                    source_config=config,
                )
                binding = validate_fixed_entry_config_identity(
                    expected_identity=endpoint_identity,
                    provider_config_id=provider_config_id,
                    source_config=config,
                )
                selected_entry = binding.selected_entry
            except PaperModelIdentityMismatch as exc:
                blocked_reasons.extend(exc.reasons)
                selected_entry = _entry_by_id(config, entry_id)
            if selected_entry is None:
                missing_entry_ids.append(entry_id)
                if "selected_entry_not_found" not in blocked_reasons:
                    blocked_reasons.append("selected_entry_not_found")
            else:
                if not selected_entry.enabled:
                    blocked_reasons.append("selected_entry_disabled")
                expected_api_key_env = expected_member.get("api_key_env")
                if (
                    expected_api_key_env is not None
                    and selected_entry.api_key_env != expected_api_key_env
                ):
                    blocked_reasons.append("api_key_env_mismatch")
                if not key_is_available(selected_entry.api_key_env):
                    blocked_reasons.append("missing_api_key_env")
                if (
                    expected_overrides is not None
                    and endpoint_identity is not None
                    and dict(endpoint_identity.effective_reasoning_controls)
                    != expected_overrides
                ):
                    blocked_reasons.append("entry_reasoning_controls_mismatch")
                pricing_snapshot = dict(selected_entry.pricing)
                expected_pricing = expected_member.get("pricing")
                if isinstance(expected_pricing, dict):
                    blocked_reasons.extend(
                        _pricing_snapshot_blocked_reasons(
                            actual=pricing_snapshot,
                            expected=expected_pricing,
                        )
                    )
        if cohort_id == PAPER_MODEL_ENDPOINT_COHORT_V3_ID:
            pricing_freshness, freshness_reasons = (
                _exp5_pricing_freshness_evidence(
                    pricing_snapshot=pricing_snapshot,
                    pricing_freshness_as_of=pricing_freshness_as_of,
                )
            )
            blocked_reasons.extend(freshness_reasons)

        request_controls: JsonObject | None = None
        if (
            config is not None
            and selected_entry is not None
            and endpoint_identity is not None
        ):
            try:
                request_controls = _normalized_exp5_request_controls(
                config=config,
                selected_entry=selected_entry,
                endpoint_identity=endpoint_identity,
                comparable_fields=comparable_fields,
            )
            except ValueError:
                blocked_reasons.append("missing_comparable_request_controls")
        if request_controls is None:
            if "missing_comparable_request_controls" not in blocked_reasons:
                blocked_reasons.append("missing_comparable_request_controls")
        else:
            blocked_reasons.extend(
                _required_control_blocked_reasons(
                    actual=request_controls["comparable"],
                    required=required_common_controls,
                )
            )
        request_controls_digest = _request_controls_binding_digest(
            request_controls
        )
        pricing_snapshot_digest = (
            digest_json(pricing_snapshot)
            if pricing_snapshot is not None
            else None
        )
        if require_smoke_evidence:
            smoke_evidence = spec.get("smoke_evidence_ref")
            validate_member_smoke = True
            if cohort_id == PAPER_MODEL_ENDPOINT_COHORT_V3_ID:
                bundle_members = (
                    smoke_evidence_bundle.get("members")
                    if isinstance(smoke_evidence_bundle, Mapping)
                    else None
                )
                if not isinstance(bundle_members, Mapping):
                    blocked_reasons.append("missing_smoke_evidence_bundle")
                    smoke_evidence = None
                    validate_member_smoke = False
                else:
                    smoke_evidence = bundle_members.get(member_id)
                if smoke_bundle_validation is not None:
                    blocked_reasons.extend(
                        smoke_bundle_validation.get("global_reasons", [])
                    )
                    member_bundle_reasons = smoke_bundle_validation.get(
                        "member_reasons"
                    )
                    if isinstance(member_bundle_reasons, Mapping):
                        blocked_reasons.extend(
                            member_bundle_reasons.get(member_id, [])
                        )
            if validate_member_smoke:
                blocked_reasons.extend(
                    _smoke_evidence_blocked_reasons(
                        smoke_evidence=smoke_evidence,
                        smoke_schema_version=str(
                            definition["smoke_evidence_schema_version"]
                        ),
                        member_id=member_id,
                        entry_id=entry_id,
                        provider_family=provider_family,
                        provider_model_id=provider_model_id,
                        reasoning_profile_id=reasoning_profile_id,
                        model_cohort_id=cohort_id,
                        model_cohort_digest=cohort_digest,
                        source_provider_config_digest=(
                            config.config_digest if config is not None else None
                        ),
                        model_endpoint_identity_digest=(
                            endpoint_identity.model_endpoint_identity_digest
                            if endpoint_identity is not None
                            else None
                        ),
                        request_controls_digest=request_controls_digest,
                        pricing_snapshot_digest=pricing_snapshot_digest,
                    )
                )
        blocked_reasons = list(dict.fromkeys(blocked_reasons))
        plan = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "cohort_id": cohort_id,
            "model_cohort_digest": cohort_digest,
            "cohort_member_id": member_id,
            "provider_config_id": provider_config_id or None,
            "requested_entry_id": entry_id or None,
            "selected_entry_id": selected_entry.entry_id if selected_entry is not None else None,
            "provider_family": provider_family,
            "provider_model_id": provider_model_id,
            "reasoning_profile_id": reasoning_profile_id,
            "model": selected_entry.model if selected_entry is not None else None,
            "api_key_env": selected_entry.api_key_env if selected_entry is not None else None,
            "source_provider_config_digest": (
                config.config_digest if config is not None else None
            ),
            "model_endpoint_identity_digest": (
                endpoint_identity.model_endpoint_identity_digest
                if endpoint_identity is not None
                else None
            ),
            "endpoint_identity": (
                endpoint_identity.to_dict() if endpoint_identity is not None else None
            ),
            "request_controls": request_controls,
            "request_controls_digest": request_controls_digest,
            "pricing_snapshot": pricing_snapshot,
            "pricing_snapshot_digest": pricing_snapshot_digest,
            "pricing_freshness": pricing_freshness,
            "smoke_evidence_ref": spec.get("smoke_evidence_ref"),
            "external_benchmark": {
                "source": member.get("external_benchmark_source"),
                "index_version": member.get("index_version"),
                "index_score": member.get("index_score"),
                "benchmark_variant": member.get("benchmark_variant"),
                "benchmark_match_status": member.get("benchmark_match_status"),
                "source_url": member.get("source_url"),
                "observed_at": member.get("observed_at"),
            },
            "status": POLICY_STATUS_BLOCKED if blocked_reasons else POLICY_STATUS_PLANNED,
            "blocked_reasons": blocked_reasons,
        }
        member_plans[member_id] = plan
        if blocked_reasons:
            ineligible_members.append(
                {
                    "cohort_member_id": member_id,
                    "blocked_reasons": blocked_reasons,
                }
            )

    comparable_snapshots = [
        plan["request_controls"]["comparable"]
        for plan in member_plans.values()
        if isinstance(plan.get("request_controls"), dict)
        and isinstance(plan["request_controls"].get("comparable"), dict)
    ]
    request_controls_snapshot = (
        comparable_snapshots[0]
        if len(comparable_snapshots) == len(expected_member_ids_ordered)
        else None
    )
    if request_controls_snapshot is not None and len(
        {digest_json(snapshot) for snapshot in comparable_snapshots}
    ) != 1:
        cohort_level_reasons.append(
            "cross_member_request_controls_mismatch"
        )
        request_controls_snapshot = None

    pricing_authority = None
    if (
        cohort_id == PAPER_MODEL_ENDPOINT_COHORT_V3_ID
        and _strict_iso_calendar_date(pricing_freshness_as_of) is not None
        and set(member_plans) == set(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
        and all(
            isinstance(plan.get("pricing_snapshot"), Mapping)
            and isinstance(plan.get("source_provider_config_digest"), str)
            and not any(
                str(reason).startswith("pricing_")
                for reason in plan.get("blocked_reasons", ())
            )
            for plan in member_plans.values()
        )
    ):
        pricing_authority = build_exp5_pricing_freshness_authority(
            member_plans=member_plans,
            pricing_freshness_as_of=pricing_freshness_as_of,
            provider_configs=provider_configs,
        )

    blocked = bool(
        cohort_level_reasons
        or missing_members
        or unexpected_members
        or unexpected_entry_map_members
        or missing_provider_configs
        or missing_entry_ids
        or ineligible_members
    )
    return {
        "schema_version": MODEL_ENDPOINT_COHORT_PRELIGHT_SCHEMA_VERSION,
        "status": POLICY_STATUS_BLOCKED if blocked else POLICY_STATUS_PLANNED,
        "paper_eligible_possible": not blocked,
        "blocked_reason": "incomplete_model_cohort" if blocked else None,
        "ineligibility_reasons": ["incomplete_model_cohort"] if blocked else [],
        "provider_calls_made": 0,
        "model_policy": FIXED_ENTRY_MODEL_POLICY,
        "cohort_id": cohort_id,
        "model_cohort_digest": cohort_digest,
        "expected_member_ids": list(expected_member_ids_ordered),
        "comparable_request_control_fields": list(comparable_fields),
        "cohort_level_reasons": cohort_level_reasons,
        "missing_members": sorted(set(missing_members)),
        "unexpected_members": unexpected_members,
        "unexpected_entry_map_members": unexpected_entry_map_members,
        "missing_provider_configs": sorted(
            {item for item in missing_provider_configs if item}
        ),
        "missing_entry_ids": sorted({item for item in missing_entry_ids if item}),
        "ineligible_members": ineligible_members,
        "member_plans": member_plans,
        "request_controls_snapshot": request_controls_snapshot,
        "request_controls_snapshot_digest": (
            digest_json(request_controls_snapshot)
            if request_controls_snapshot is not None
            else None
        ),
        "required_common_controls": required_common_controls,
        "smoke_evidence_bundle_digest": (
            smoke_evidence_bundle.get("bundle_digest")
            if isinstance(smoke_evidence_bundle, Mapping)
            else None
        ),
        "pricing_freshness_authority": pricing_authority,
    }


def _required_control_blocked_reasons(
    *,
    actual: JsonObject,
    required: JsonObject,
) -> list[str]:
    reasons: list[str] = []
    reason_by_field = {
        "timeout_seconds": "formal_ai_timeout_seconds_mismatch",
        "max_tokens": "formal_ai_max_tokens_mismatch",
    }
    for field_name, expected_value in required.items():
        actual_value = actual.get(field_name)
        if type(actual_value) is type(expected_value) and actual_value == expected_value:
            continue
        reasons.append(
            reason_by_field.get(field_name, f"formal_ai_{field_name}_mismatch")
        )
    return reasons


def _strict_json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def _request_controls_binding_digest(
    request_controls: JsonObject | None,
) -> str | None:
    if not isinstance(request_controls, dict):
        return None
    comparable = request_controls.get("comparable")
    reasoning = request_controls.get("provider_specific_reasoning")
    if not isinstance(comparable, dict) or not isinstance(reasoning, dict):
        return None
    return digest_json(
        {
            "comparable": dict(comparable),
            "provider_specific_reasoning": dict(reasoning),
        }
    )


def _pricing_snapshot_blocked_reasons(
    *,
    actual: JsonObject,
    expected: JsonObject,
) -> list[str]:
    reasons: list[str] = []
    for field_name, reason in (
        ("currency", "pricing_snapshot_currency_mismatch"),
        ("source_url", "pricing_snapshot_source_url_mismatch"),
        ("accessed_at", "pricing_snapshot_accessed_at_mismatch"),
    ):
        if actual.get(field_name) != expected.get(field_name):
            reasons.append(reason)
    rate_fields = {
        "input_per_million_tokens",
        "cached_input_per_million_tokens",
        "uncached_input_per_million_tokens",
        "output_per_million_tokens",
    }
    expected_rates = {field for field in rate_fields if field in expected}
    actual_rates = {field for field in rate_fields if field in actual}
    if actual_rates != expected_rates or any(
        isinstance(actual.get(field), bool)
        or not isinstance(actual.get(field), (int, float))
        or float(actual[field]) != float(expected[field])
        for field in expected_rates
    ):
        reasons.append("pricing_snapshot_rate_mismatch")
    return list(dict.fromkeys(reasons))


def _exp5_pricing_freshness_evidence(
    *,
    pricing_snapshot: JsonObject | None,
    pricing_freshness_as_of: str | None,
) -> tuple[JsonObject, list[str]]:
    accessed_at = (
        pricing_snapshot.get("accessed_at")
        if isinstance(pricing_snapshot, Mapping)
        else None
    )
    evidence: JsonObject = {
        "accessed_at": accessed_at,
        "pricing_freshness_as_of": pricing_freshness_as_of,
        "age_days": None,
        "max_age_days": EXP5_PRICING_MAX_AGE_DAYS,
        "status": "blocked",
    }
    if pricing_freshness_as_of is None:
        return evidence, ["pricing_freshness_as_of_missing"]
    as_of = _strict_iso_calendar_date(pricing_freshness_as_of)
    if as_of is None:
        return evidence, ["pricing_freshness_as_of_invalid"]
    if accessed_at is None:
        return evidence, ["pricing_snapshot_accessed_at_missing"]
    accessed = _strict_iso_calendar_date(accessed_at)
    if accessed is None:
        return evidence, ["pricing_snapshot_accessed_at_invalid"]
    age_days = (as_of - accessed).days
    evidence["age_days"] = age_days
    if age_days < 0:
        return evidence, ["pricing_snapshot_accessed_at_future"]
    if age_days > EXP5_PRICING_MAX_AGE_DAYS:
        return evidence, ["pricing_snapshot_stale"]
    evidence["status"] = "fresh"
    return evidence, []


def _strict_iso_calendar_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def validate_exp5_pricing_freshness_evidence(
    value: Mapping[str, Any],
    *,
    provider_configs: Mapping[str, AIAPIExecutorConfig] | None = None,
) -> JsonObject:
    """验证签发时冻结的 Exp5 pricing authority 及其当前配置绑定。"""

    if not isinstance(value, Mapping):
        raise ValueError("Experiment 5 pricing freshness preflight is missing")
    authority = value.get("pricing_freshness_authority")
    if (
        value.get("cohort_id") != PAPER_MODEL_ENDPOINT_COHORT_V3_ID
        or not isinstance(authority, Mapping)
    ):
        raise ValueError("Experiment 5 pricing freshness authority drift")
    member_plans = value.get("member_plans")
    if not isinstance(member_plans, Mapping) or set(member_plans) != set(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    ):
        raise ValueError("Experiment 5 pricing freshness member set drift")
    expected_authority = build_exp5_pricing_freshness_authority(
        member_plans=member_plans,
        pricing_freshness_as_of=authority.get("pricing_freshness_as_of"),
        provider_configs=provider_configs,
    )
    if dict(authority) != expected_authority:
        raise ValueError("Experiment 5 pricing freshness authority drift")
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS:
        plan = member_plans[member_id]
        expected_freshness, freshness_reasons = _exp5_pricing_freshness_evidence(
            pricing_snapshot=dict(plan["pricing_snapshot"]),
            pricing_freshness_as_of=authority["pricing_freshness_as_of"],
        )
        if freshness_reasons or plan.get("pricing_freshness") != expected_freshness:
            raise ValueError("Experiment 5 pricing freshness evidence drift")
    return dict(value)


def build_exp5_pricing_freshness_authority(
    *,
    member_plans: Mapping[str, Mapping[str, Any]],
    pricing_freshness_as_of: object,
    provider_configs: Mapping[str, AIAPIExecutorConfig] | None = None,
) -> JsonObject:
    """从本次 approved member plans 构造可持久化的 unit-price authority。"""

    as_of = _strict_iso_calendar_date(pricing_freshness_as_of)
    if as_of is None or set(member_plans) != set(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    ):
        raise ValueError("Experiment 5 pricing freshness authority is invalid")
    config_digests: dict[str, str] = {}
    pricing_digests: dict[str, str] = {}
    configs = tuple(provider_configs.values()) if provider_configs is not None else ()
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS:
        plan = member_plans.get(member_id)
        if not isinstance(plan, Mapping):
            raise ValueError("Experiment 5 pricing freshness member plan drift")
        pricing_snapshot = plan.get("pricing_snapshot")
        source_config_digest = plan.get("source_provider_config_digest")
        selected_entry_id = plan.get("selected_entry_id")
        if (
            not isinstance(pricing_snapshot, Mapping)
            or not isinstance(source_config_digest, str)
            or not isinstance(selected_entry_id, str)
        ):
            raise ValueError("Experiment 5 pricing freshness snapshot is missing")
        pricing_body = dict(pricing_snapshot)
        expected_pricing = dict(
            PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS[member_id]["pricing"]
        )
        if _pricing_snapshot_blocked_reasons(
            actual=pricing_body,
            expected=expected_pricing,
        ):
            raise ValueError("Experiment 5 pricing freshness snapshot drift")
        pricing_digest = digest_json(pricing_body)
        if plan.get("pricing_snapshot_digest") != pricing_digest:
            raise ValueError("Experiment 5 pricing freshness snapshot digest drift")
        if provider_configs is not None:
            config_matches = tuple(
                config
                for config in configs
                if getattr(config, "config_digest", None) == source_config_digest
            )
            if len(config_matches) != 1:
                raise ValueError("Experiment 5 pricing source config drift")
            entry_matches = tuple(
                entry
                for entry in config_matches[0].entries
                if entry.entry_id == selected_entry_id
            )
            if len(entry_matches) != 1 or dict(entry_matches[0].pricing) != pricing_body:
                raise ValueError("Experiment 5 pricing current config drift")
        config_digests[member_id] = source_config_digest
        pricing_digests[member_id] = pricing_digest
    body: JsonObject = {
        "schema_version": EXP5_PRICING_FRESHNESS_AUTHORITY_SCHEMA_VERSION,
        "pricing_freshness_as_of": as_of.isoformat(),
        "max_age_days": EXP5_PRICING_MAX_AGE_DAYS,
        "source_provider_config_digest_by_member": config_digests,
        "pricing_snapshot_digest_by_member": pricing_digests,
    }
    return {**body, "authority_digest": digest_json(body)}


def _normalized_exp5_request_controls(
    *,
    config: AIAPIExecutorConfig,
    selected_entry: AIAPIProviderEntry,
    endpoint_identity: PaperModelEndpointIdentity,
    comparable_fields: tuple[str, ...],
) -> JsonObject:
    effective = {
        **dict(config.defaults),
        **dict(selected_entry.request_overrides),
    }
    comparable: JsonObject = {}
    for field_name in comparable_fields:
        if field_name not in effective:
            raise ValueError(
                f"missing Experiment 5 request control: {field_name}"
            )
        comparable[field_name] = effective[field_name]
    comparable["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
    }
    provider_specific_reasoning = exp5_provider_specific_reasoning_controls(
        endpoint_identity.effective_reasoning_controls
    )
    return {
        "schema_version": "tokenshare.paper_exp5_request_controls.v1",
        "comparable": comparable,
        "comparable_digest": digest_json(comparable),
        "provider_specific_reasoning": provider_specific_reasoning,
        "provider_specific_reasoning_digest": digest_json(
            provider_specific_reasoning
        ),
    }


@dataclass(frozen=True, kw_only=True)
class PaperModelPolicySelection:
    unit_key: str
    allocation_index: int
    allocation_role: str
    entry_id: str
    provider: str
    model: str
    tags: tuple[str, ...]
    config_digest: str
    schema_version: str = "tokenshare.paper_model_policy_selection.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "unit_key": self.unit_key,
            "allocation_index": self.allocation_index,
            "allocation_role": self.allocation_role,
            "entry_id": self.entry_id,
            "provider": self.provider,
            "model": self.model,
            "tags": list(self.tags),
            "config_digest": self.config_digest,
        }


@dataclass(frozen=True, kw_only=True)
class PaperModelPolicyPlan:
    model_policy: str
    status: str
    strong_entry_id: str | None
    weak_entry_id: str | None
    provider_family: str
    config_digest: str
    allocations: tuple[PaperModelPolicySelection, ...]
    blocked_reason: str | None = None
    missing_entry_ids: tuple[str, ...] = ()
    available_entry_ids: tuple[str, ...] = ()
    schema_version: str = "tokenshare.paper_model_policy_plan.v1"

    @property
    def paper_eligible_possible(self) -> bool:
        return self.status == POLICY_STATUS_PLANNED

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "model_policy": self.model_policy,
            "status": self.status,
            "paper_eligible_possible": self.paper_eligible_possible,
            "strong_entry_id": self.strong_entry_id,
            "weak_entry_id": self.weak_entry_id,
            "provider_family": self.provider_family,
            "config_digest": self.config_digest,
            "blocked_reason": self.blocked_reason,
            "missing_entry_ids": list(self.missing_entry_ids),
            "available_entry_ids": list(self.available_entry_ids),
            "allocations": [selection.to_dict() for selection in self.allocations],
        }

    def selection_for_unit(self, unit_key: str) -> PaperModelPolicySelection:
        for selection in self.allocations:
            if selection.unit_key == unit_key:
                return selection
        raise ValueError(f"model policy allocation missing unit: {unit_key}")

    def filtered_config_for_unit(
        self,
        config: AIAPIExecutorConfig,
        unit_key: str,
    ) -> AIAPIExecutorConfig:
        if self.status != POLICY_STATUS_PLANNED:
            raise ValueError("blocked model policy plan has no filtered config")
        return filtered_config_for_entry(
            config=config,
            entry_id=self.selection_for_unit(unit_key).entry_id,
            model_policy=self.model_policy,
        )


def resolve_paper_model_policy(
    *,
    config: AIAPIExecutorConfig,
    model_policy: str,
    strong_entry_id: str | None,
    weak_entry_id: str | None,
    unit_keys: tuple[str, ...] | list[str],
) -> PaperModelPolicyPlan:
    _validate_model_policy(model_policy)
    normalized_unit_keys = _normalize_unit_keys(unit_keys)
    entries_by_id = _enabled_entries_by_id(config)
    required_entry_ids = _required_entry_ids(
        model_policy=model_policy,
        strong_entry_id=strong_entry_id,
        weak_entry_id=weak_entry_id,
    )
    missing_entry_ids = tuple(
        entry_id for entry_id in required_entry_ids if entry_id not in entries_by_id
    )
    if missing_entry_ids:
        return PaperModelPolicyPlan(
            model_policy=model_policy,
            status=POLICY_STATUS_BLOCKED,
            strong_entry_id=strong_entry_id,
            weak_entry_id=weak_entry_id,
            provider_family=config.provider_family,
            config_digest=config.config_digest,
            allocations=(),
            blocked_reason="missing_model_entry",
            missing_entry_ids=missing_entry_ids,
            available_entry_ids=tuple(sorted(entries_by_id)),
        )

    allocations: list[PaperModelPolicySelection] = []
    for index, unit_key in enumerate(normalized_unit_keys):
        allocation_role, entry = _entry_for_unit(
            model_policy=model_policy,
            index=index,
            entries_by_id=entries_by_id,
            strong_entry_id=strong_entry_id,
            weak_entry_id=weak_entry_id,
        )
        allocations.append(
            PaperModelPolicySelection(
                unit_key=unit_key,
                allocation_index=index,
                allocation_role=allocation_role,
                entry_id=entry.entry_id,
                provider=config.provider_family,
                model=entry.model,
                tags=tuple(entry.tags),
                config_digest=config.config_digest,
            )
        )
    return PaperModelPolicyPlan(
        model_policy=model_policy,
        status=POLICY_STATUS_PLANNED,
        strong_entry_id=strong_entry_id,
        weak_entry_id=weak_entry_id,
        provider_family=config.provider_family,
        config_digest=config.config_digest,
        allocations=tuple(allocations),
        available_entry_ids=tuple(sorted(entries_by_id)),
    )


def build_model_policy_preflight(
    *,
    config: AIAPIExecutorConfig,
    model_policies: tuple[str, ...] | list[str],
    strong_entry_id: str | None,
    weak_entry_id: str | None,
    unit_keys_by_policy: dict[str, tuple[str, ...]],
) -> JsonObject:
    policy_plans: dict[str, JsonObject] = {}
    blocked_reasons: list[str] = []
    for model_policy in model_policies:
        unit_keys = unit_keys_by_policy.get(model_policy) or (
            f"exp5:{model_policy}:unit0",
        )
        plan = resolve_paper_model_policy(
            config=config,
            model_policy=model_policy,
            strong_entry_id=strong_entry_id,
            weak_entry_id=weak_entry_id,
            unit_keys=unit_keys,
        )
        body = plan.to_dict()
        policy_plans[model_policy] = body
        if body["status"] == POLICY_STATUS_BLOCKED:
            blocked_reason = str(body.get("blocked_reason") or "unknown")
            if blocked_reason not in blocked_reasons:
                blocked_reasons.append(blocked_reason)
    status = POLICY_STATUS_BLOCKED if blocked_reasons else POLICY_STATUS_PLANNED
    return {
        "schema_version": "tokenshare.paper_model_policy_preflight.v1",
        "status": status,
        "paper_eligible_possible": status == POLICY_STATUS_PLANNED,
        "blocked_reason": blocked_reasons[0] if blocked_reasons else None,
        "ineligibility_reasons": blocked_reasons,
        "provider_calls_made": 0,
        "config_digest": config.config_digest,
        "strong_entry_id": strong_entry_id,
        "weak_entry_id": weak_entry_id,
        "model_policies": list(model_policies),
        "policy_plans": policy_plans,
    }


def blocked_model_policy_preflight(
    *,
    reason: str,
    message: str,
    model_policies: tuple[str, ...] | list[str],
    strong_entry_id: str | None,
    weak_entry_id: str | None,
) -> JsonObject:
    return {
        "schema_version": "tokenshare.paper_model_policy_preflight.v1",
        "status": POLICY_STATUS_BLOCKED,
        "paper_eligible_possible": False,
        "blocked_reason": reason,
        "ineligibility_reasons": [reason],
        "message": message,
        "provider_calls_made": 0,
        "config_digest": None,
        "strong_entry_id": strong_entry_id,
        "weak_entry_id": weak_entry_id,
        "model_policies": list(model_policies),
        "policy_plans": {},
    }


def filtered_config_for_entry(
    *,
    config: AIAPIExecutorConfig,
    entry_id: str,
    model_policy: str,
) -> AIAPIExecutorConfig:
    _validate_model_policy(model_policy)
    entries = [entry for entry in config.entries if entry.entry_id == entry_id]
    if not entries:
        raise ValueError(f"missing ai api entry id: {entry_id}")
    return AIAPIExecutorConfig(
        schema_version=config.schema_version,
        executor_id=config.executor_id,
        provider_family=config.provider_family,
        selection_policy=dict(config.selection_policy),
        defaults=dict(config.defaults),
        entries=entries,
        local_concurrency=dict(config.local_concurrency),
        metadata={
            **dict(config.metadata),
            "paper_model_policy": model_policy,
            "paper_selected_entry_id": entry_id,
        },
    )


def _validate_model_policy(value: str) -> None:
    if value not in LEGACY_MODEL_POLICIES:
        raise ValueError("model_policy must be strong_only, weak_only, or mixed")


def _smoke_evidence_blocked_reasons(
    *,
    smoke_evidence: Any,
    smoke_schema_version: str,
    member_id: str,
    entry_id: str,
    provider_family: str,
    provider_model_id: str,
    reasoning_profile_id: str,
    model_cohort_id: str,
    model_cohort_digest: str,
    source_provider_config_digest: str | None,
    model_endpoint_identity_digest: str | None,
    request_controls_digest: str | None,
    pricing_snapshot_digest: str | None,
) -> list[str]:
    if not isinstance(smoke_evidence, dict):
        return ["missing_smoke_evidence"]
    reasons: list[str] = []
    expected = {
        "schema_version": smoke_schema_version,
        "status": "passed",
        "cohort_member_id": member_id,
        "entry_id": entry_id,
        "provider_family": provider_family,
        "provider_model_id": provider_model_id,
        "reasoning_profile_id": reasoning_profile_id,
    }
    for field_name, expected_value in expected.items():
        if smoke_evidence.get(field_name) != expected_value:
            reasons.append(f"smoke_evidence_{field_name}_mismatch")
    if smoke_schema_version == V3_SMOKE_EVIDENCE_SCHEMA_VERSION:
        v3_bindings = {
            "model_cohort_id": model_cohort_id,
            "model_cohort_digest": model_cohort_digest,
            "source_provider_config_digest": source_provider_config_digest,
            "model_endpoint_identity_digest": model_endpoint_identity_digest,
            "request_controls_digest": request_controls_digest,
            "pricing_snapshot_digest": pricing_snapshot_digest,
        }
        for field_name, expected_value in v3_bindings.items():
            if expected_value is None or smoke_evidence.get(field_name) != expected_value:
                reasons.append(f"smoke_evidence_{field_name}_mismatch")
        capability_fields = {
            "resolved_model_match",
            "request_controls_match",
            "usage_schema_verified",
            "thinking_breakdown_verified",
            "timeout_limit_verified",
        }
        capability_checks = smoke_evidence.get("capability_checks")
        if (
            not isinstance(capability_checks, dict)
            or set(capability_checks) != capability_fields
            or any(capability_checks[field] is not True for field in capability_fields)
        ):
            reasons.append("smoke_evidence_capability_checks_mismatch")
    if not _positive_int(smoke_evidence.get("provider_attempt_count")):
        reasons.append("smoke_evidence_missing_provider_attempt")
    for field_name in ("raw_output_ref", "provenance_ref", "usage_ref"):
        if not _has_minimal_artifact_ref(smoke_evidence.get(field_name)):
            reasons.append(f"smoke_evidence_missing_{field_name}")
    return reasons


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _has_minimal_artifact_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("artifact_id"), str)
        and bool(value["artifact_id"])
        and isinstance(value.get("content_hash"), str)
        and value["content_hash"].startswith("sha256:")
    )


def _enabled_entries_by_id(config: AIAPIExecutorConfig) -> dict[str, AIAPIProviderEntry]:
    return {entry.entry_id: entry for entry in config.entries if entry.enabled}


def _required_entry_ids(
    *,
    model_policy: str,
    strong_entry_id: str | None,
    weak_entry_id: str | None,
) -> tuple[str, ...]:
    if model_policy == "strong_only":
        return (_require_entry_id("strong_entry_id", strong_entry_id),)
    if model_policy == "weak_only":
        return (_require_entry_id("weak_entry_id", weak_entry_id),)
    return (
        _require_entry_id("strong_entry_id", strong_entry_id),
        _require_entry_id("weak_entry_id", weak_entry_id),
    )


def _require_entry_id(field_name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be provided for model policy planning")
    return value


def _entry_for_unit(
    *,
    model_policy: str,
    index: int,
    entries_by_id: dict[str, AIAPIProviderEntry],
    strong_entry_id: str | None,
    weak_entry_id: str | None,
) -> tuple[str, AIAPIProviderEntry]:
    if model_policy == "strong_only":
        return "strong", entries_by_id[_require_entry_id("strong_entry_id", strong_entry_id)]
    if model_policy == "weak_only":
        return "weak", entries_by_id[_require_entry_id("weak_entry_id", weak_entry_id)]
    if index % 2 == 0:
        return "strong", entries_by_id[_require_entry_id("strong_entry_id", strong_entry_id)]
    return "weak", entries_by_id[_require_entry_id("weak_entry_id", weak_entry_id)]


def _normalize_unit_keys(unit_keys: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = tuple(str(unit_key) for unit_key in unit_keys if str(unit_key))
    if not normalized:
        raise ValueError("unit_keys must contain at least one AI unit key")
    if len(set(normalized)) != len(normalized):
        raise ValueError("unit_keys must be unique")
    return normalized


def _load_json_file(path: str | Path) -> JsonObject:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return body


def _required_str(body: JsonObject, field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _cohort_members_by_id(cohort: JsonObject) -> dict[str, JsonObject]:
    result: dict[str, JsonObject] = {}
    members = cohort.get("members")
    if not isinstance(members, list):
        return result
    for member in members:
        if not isinstance(member, dict):
            continue
        member_id = member.get("cohort_member_id")
        if isinstance(member_id, str) and member_id:
            result[member_id] = dict(member)
    return result


def _entry_by_id(
    config: AIAPIExecutorConfig,
    entry_id: str,
) -> AIAPIProviderEntry | None:
    for entry in config.entries:
        if entry.entry_id == entry_id:
            return entry
    return None
