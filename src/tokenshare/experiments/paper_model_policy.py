"""Experiment-layer model policy planning for paper suites."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from tokenshare.experiments.paper_models import JsonObject, digest_json


POLICY_STATUS_PLANNED = "planned"
POLICY_STATUS_BLOCKED = "blocked"
LEGACY_MODEL_POLICIES = ("strong_only", "weak_only", "mixed")
FIXED_ENTRY_MODEL_POLICY = "fixed_entry"
PAPER_MODEL_ENDPOINT_COHORT_ID = "tokenshare.paper.model_endpoint_cohort.v1"
MODEL_ENDPOINT_COHORT_SCHEMA_VERSION = "tokenshare.paper_model_endpoint_cohort.v1"
MODEL_ENTRY_MAP_SCHEMA_VERSION = "tokenshare.paper_model_entry_map.v1"
MODEL_ENDPOINT_COHORT_PRELIGHT_SCHEMA_VERSION = (
    "tokenshare.paper_model_endpoint_cohort_preflight.v1"
)
SMOKE_EVIDENCE_SCHEMA_VERSION = "tokenshare.paper_model_endpoint_smoke_evidence.v1"
PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_6_27b_siliconflow",
    "gpt_5_6_sol_high_openai",
)
PAPER_MODEL_ENDPOINT_COHORT_MEMBERS = {
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


def load_model_endpoint_cohort(path: str | Path) -> JsonObject:
    body = _load_json_file(path)
    if body.get("schema_version") != MODEL_ENDPOINT_COHORT_SCHEMA_VERSION:
        raise ValueError("unsupported model endpoint cohort schema")
    cohort_id = body.get("cohort_id")
    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValueError("model endpoint cohort requires cohort_id")
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
) -> JsonObject:
    cohort_id = str(cohort.get("cohort_id") or "")
    cohort_digest = str(cohort.get("model_cohort_digest") or digest_json(cohort))
    members_by_id = _cohort_members_by_id(cohort)
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

    expected_member_ids = set(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS)
    actual_member_ids = set(members_by_id)
    entry_map_member_ids = {str(member_id) for member_id in entry_specs}
    if cohort_id != PAPER_MODEL_ENDPOINT_COHORT_ID:
        cohort_level_reasons.append("cohort_id_mismatch")
    if entry_map.get("cohort_id") != cohort_id or cohort_id != PAPER_MODEL_ENDPOINT_COHORT_ID:
        cohort_level_reasons.append("entry_map_cohort_id_mismatch")
    if actual_member_ids != expected_member_ids:
        cohort_level_reasons.append("cohort_member_set_mismatch")
        unexpected_members = sorted(actual_member_ids - expected_member_ids)
    unexpected_entry_map_members = sorted(entry_map_member_ids - expected_member_ids)
    if unexpected_entry_map_members:
        cohort_level_reasons.append("entry_map_member_set_mismatch")

    for member_id in PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS:
        expected_member = PAPER_MODEL_ENDPOINT_COHORT_MEMBERS[member_id]
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
        blocked_reasons: list[str] = []
        if str(member.get("provider_family") or "") != provider_family:
            blocked_reasons.append("cohort_provider_family_mismatch")
        if str(member.get("provider_model_id") or "") != provider_model_id:
            blocked_reasons.append("cohort_model_mismatch")
        if str(member.get("reasoning_profile_id") or "") != reasoning_profile_id:
            blocked_reasons.append("cohort_reasoning_profile_mismatch")
        if config is None:
            missing_provider_configs.append(provider_config_id)
            blocked_reasons.append("missing_provider_config")
        else:
            try:
                endpoint_identity = build_model_endpoint_identity(
                    model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_ID,
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
            elif selected_entry.enabled:
                if not os.environ.get(selected_entry.api_key_env, ""):
                    blocked_reasons.append("missing_api_key_env")
                smoke_reasons = _smoke_evidence_blocked_reasons(
                    smoke_evidence=spec.get("smoke_evidence_ref"),
                    member_id=member_id,
                    entry_id=entry_id,
                    provider_family=provider_family,
                    provider_model_id=provider_model_id,
                    reasoning_profile_id=reasoning_profile_id,
                )
                blocked_reasons.extend(smoke_reasons)

        blocked_reasons = list(dict.fromkeys(blocked_reasons))
        plan = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_ID,
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
        "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS),
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
    member_id: str,
    entry_id: str,
    provider_family: str,
    provider_model_id: str,
    reasoning_profile_id: str,
) -> list[str]:
    if not isinstance(smoke_evidence, dict):
        return ["missing_smoke_evidence"]
    reasons: list[str] = []
    expected = {
        "schema_version": SMOKE_EVIDENCE_SCHEMA_VERSION,
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
