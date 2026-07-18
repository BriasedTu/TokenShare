"""论文实验 fixed-entry model endpoint 身份契约。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from tokenshare.executors.ai_api_artifacts import read_raw_model_identity_evidence
from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
)
from tokenshare.experiments.paper_models import (
    FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID,
    PaperExperimentCondition,
    PaperModelExecutionRecord,
)


REASONING_IDENTITY_SCHEMA_VERSION = "tokenshare.paper_reasoning_identity.v1"
MODEL_ENDPOINT_IDENTITY_SCHEMA_VERSION = "tokenshare.paper_model_endpoint_identity.v1"
VALIDATED_ENDPOINT_BINDING_SCHEMA_VERSION = (
    "tokenshare.paper_validated_model_endpoint_binding.v1"
)
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PaperModelIdentityMismatch(ValueError):
    """表示计划身份与配置/entry 身份不一致。"""

    def __init__(self, reasons: str | Iterable[str]) -> None:
        values = (reasons,) if isinstance(reasons, str) else tuple(reasons)
        stable_reasons = tuple(
            dict.fromkeys(str(reason) for reason in values if reason)
        )
        if not stable_reasons:
            stable_reasons = ("model_identity_mismatch",)
        self.reasons = stable_reasons
        super().__init__(f"paper model identity mismatch: {', '.join(stable_reasons)}")


@dataclass(frozen=True, kw_only=True)
class NormalizedReasoningIdentity:
    """Provider-specific reasoning 配置的规范化逻辑身份。"""

    reasoning_profile_id: str
    effective_controls: Mapping[str, Any]
    schema_version: str = REASONING_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("reasoning_profile_id", self.reasoning_profile_id)
        object.__setattr__(
            self,
            "effective_controls",
            MappingProxyType(dict(self.effective_controls)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "reasoning_profile_id": self.reasoning_profile_id,
            "effective_controls": dict(self.effective_controls),
        }


@dataclass(frozen=True, kw_only=True)
class PaperModelEndpointIdentity:
    """从 frozen cohort 到 provider entry 的稳定语义身份。"""

    model_cohort_id: str
    model_cohort_digest: str
    cohort_member_id: str
    provider_config_id: str
    selected_entry_id: str
    provider_family: str
    provider_model_id: str
    reasoning_profile_id: str
    effective_reasoning_controls: Mapping[str, Any]
    source_provider_config_digest: str
    schema_version: str = MODEL_ENDPOINT_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "model_cohort_id",
            "cohort_member_id",
            "provider_config_id",
            "selected_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_digest("model_cohort_digest", self.model_cohort_digest)
        _require_digest(
            "source_provider_config_digest",
            self.source_provider_config_digest,
        )
        object.__setattr__(
            self,
            "effective_reasoning_controls",
            MappingProxyType(dict(self.effective_reasoning_controls)),
        )

    @property
    def model_endpoint_identity_digest(self) -> str:
        return _sha256_json(self._semantic_body())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._semantic_body(),
            "source_provider_config_digest": self.source_provider_config_digest,
            "model_endpoint_identity_digest": self.model_endpoint_identity_digest,
        }

    def _semantic_body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_cohort_id": self.model_cohort_id,
            "model_cohort_digest": self.model_cohort_digest,
            "cohort_member_id": self.cohort_member_id,
            "provider_config_id": self.provider_config_id,
            "selected_entry_id": self.selected_entry_id,
            "provider_family": self.provider_family,
            "provider_model_id": self.provider_model_id,
            "reasoning_profile_id": self.reasoning_profile_id,
            "effective_reasoning_controls": dict(
                self.effective_reasoning_controls
            ),
        }


@dataclass(frozen=True, kw_only=True)
class ValidatedModelEndpointBinding:
    """已经与调用方 source config 对照通过的单 entry binding。"""

    identity: PaperModelEndpointIdentity
    provider_config_id: str
    source_config: AIAPIExecutorConfig
    selected_entry: AIAPIProviderEntry
    normalized_reasoning: NormalizedReasoningIdentity
    schema_version: str = VALIDATED_ENDPOINT_BINDING_SCHEMA_VERSION

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "provider_config_id": self.provider_config_id,
            "source_provider_config_digest": self.source_config.config_digest,
            "selected_entry": self.selected_entry.to_safe_dict(),
            "normalized_reasoning": self.normalized_reasoning.to_dict(),
        }


def normalize_reasoning_identity(
    *,
    provider_family: str,
    request_overrides: Mapping[str, Any],
) -> NormalizedReasoningIdentity:
    """将 provider-specific reasoning overrides 转为稳定逻辑身份。"""

    family = str(provider_family).strip().lower()
    if family == "openai":
        if request_overrides.get("enable_thinking") is not None:
            raise PaperModelIdentityMismatch("unsupported_reasoning_control")
        reasoning_effort = request_overrides.get("reasoning_effort")
        if reasoning_effort is None:
            return NormalizedReasoningIdentity(
                reasoning_profile_id="default",
                effective_controls={},
            )
        if not isinstance(reasoning_effort, str) or not reasoning_effort.strip():
            raise PaperModelIdentityMismatch("invalid_reasoning_profile")
        normalized_effort = reasoning_effort.strip().lower()
        return NormalizedReasoningIdentity(
            reasoning_profile_id=normalized_effort,
            effective_controls={"reasoning_effort": normalized_effort},
        )

    if family == "siliconflow":
        if request_overrides.get("reasoning_effort") is not None:
            raise PaperModelIdentityMismatch("unsupported_reasoning_control")
        enable_thinking = request_overrides.get("enable_thinking")
        if enable_thinking is None:
            controls: dict[str, Any] = {}
        elif isinstance(enable_thinking, bool):
            controls = {"enable_thinking": enable_thinking}
        else:
            raise PaperModelIdentityMismatch("invalid_reasoning_control")
        return NormalizedReasoningIdentity(
            reasoning_profile_id="default",
            effective_controls=controls,
        )

    raise PaperModelIdentityMismatch("unsupported_provider_family")


def build_model_endpoint_identity(
    *,
    model_cohort_id: str,
    model_cohort_digest: str,
    cohort_member_id: str,
    provider_config_id: str,
    selected_entry_id: str,
    expected_provider_family: str,
    expected_provider_model_id: str,
    expected_reasoning_profile_id: str,
    source_config: AIAPIExecutorConfig,
) -> PaperModelEndpointIdentity:
    """从批准的 cohort member 与 source config 构造并验证 endpoint identity。"""

    selected_entry = _entry_by_id(source_config, selected_entry_id)
    reasons: list[str] = []
    if selected_entry is None:
        reasons.append("selected_entry_not_found")
    elif not selected_entry.enabled:
        reasons.append("selected_entry_disabled")
    if reasons:
        raise PaperModelIdentityMismatch(reasons)

    assert selected_entry is not None
    normalized_reasoning = normalize_reasoning_identity(
        provider_family=source_config.provider_family,
        request_overrides=selected_entry.request_overrides,
    )
    expected_profile = _normalized_profile_label(expected_reasoning_profile_id)
    identity = PaperModelEndpointIdentity(
        model_cohort_id=model_cohort_id,
        model_cohort_digest=model_cohort_digest,
        cohort_member_id=cohort_member_id,
        provider_config_id=provider_config_id,
        selected_entry_id=selected_entry_id,
        provider_family=expected_provider_family,
        provider_model_id=expected_provider_model_id,
        reasoning_profile_id=expected_profile,
        effective_reasoning_controls=normalized_reasoning.effective_controls,
        source_provider_config_digest=source_config.config_digest,
    )
    validate_fixed_entry_config_identity(
        expected_identity=identity,
        provider_config_id=provider_config_id,
        source_config=source_config,
    )
    return identity


def validate_fixed_entry_config_identity(
    *,
    expected_identity: PaperModelEndpointIdentity,
    provider_config_id: str,
    source_config: AIAPIExecutorConfig,
) -> ValidatedModelEndpointBinding:
    """在任何 provider call 前验证 source config 与批准 endpoint 完全一致。"""

    reasons: list[str] = []
    if provider_config_id != expected_identity.provider_config_id:
        reasons.append("provider_config_id_mismatch")
    if source_config.provider_family != expected_identity.provider_family:
        reasons.append("provider_family_mismatch")
    if source_config.config_digest != expected_identity.source_provider_config_digest:
        reasons.append("source_provider_config_digest_mismatch")

    selected_entry = _entry_by_id(source_config, expected_identity.selected_entry_id)
    normalized_reasoning: NormalizedReasoningIdentity | None = None
    if selected_entry is None:
        reasons.append("selected_entry_not_found")
    else:
        if not selected_entry.enabled:
            reasons.append("selected_entry_disabled")
        if selected_entry.model != expected_identity.provider_model_id:
            reasons.append("provider_model_id_mismatch")
        try:
            normalized_reasoning = normalize_reasoning_identity(
                provider_family=source_config.provider_family,
                request_overrides=selected_entry.request_overrides,
            )
        except PaperModelIdentityMismatch as exc:
            reasons.extend(exc.reasons)
        if normalized_reasoning is not None:
            if (
                normalized_reasoning.reasoning_profile_id
                != expected_identity.reasoning_profile_id
            ):
                reasons.append("reasoning_profile_id_mismatch")
            if dict(normalized_reasoning.effective_controls) != dict(
                expected_identity.effective_reasoning_controls
            ):
                reasons.append("effective_reasoning_controls_mismatch")
            actual_endpoint_digest = _endpoint_digest_for_actual_binding(
                expected_identity=expected_identity,
                provider_config_id=provider_config_id,
                provider_family=source_config.provider_family,
                provider_model_id=selected_entry.model,
                normalized_reasoning=normalized_reasoning,
            )
            if (
                actual_endpoint_digest
                != expected_identity.model_endpoint_identity_digest
            ):
                reasons.append("model_endpoint_identity_digest_mismatch")

    if reasons:
        raise PaperModelIdentityMismatch(reasons)

    assert selected_entry is not None
    assert normalized_reasoning is not None
    return ValidatedModelEndpointBinding(
        identity=expected_identity,
        provider_config_id=provider_config_id,
        source_config=source_config,
        selected_entry=selected_entry,
        normalized_reasoning=normalized_reasoning,
    )


def validate_condition_fixed_entry_identity(
    *,
    condition: PaperExperimentCondition,
    source_config: AIAPIExecutorConfig,
) -> ValidatedModelEndpointBinding | None:
    """将正式 condition 身份绑定到本次 adapter 收到的 source config。

    Experiment 1--4 的历史 condition 允许只携带 entry ID；只有正式
    Experiment 5 或完整携带 endpoint identity 的 condition 才启用严格校验。
    """

    field_names = (
        "model_cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
        "provider_config_id",
        "model_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    )
    values = {name: getattr(condition, name) for name in field_names}
    has_complete_identity = all(value is not None for value in values.values())
    if (
        condition.experiment_id != FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
        and not has_complete_identity
    ):
        return None
    if not has_complete_identity:
        raise PaperModelIdentityMismatch("incomplete_condition_model_identity")

    selected_entry_id = str(values["model_entry_id"])
    selected_entry = _entry_by_id(source_config, selected_entry_id)
    if selected_entry is None:
        raise PaperModelIdentityMismatch("selected_entry_not_found")
    normalized_reasoning = normalize_reasoning_identity(
        provider_family=source_config.provider_family,
        request_overrides=selected_entry.request_overrides,
    )
    expected_identity = PaperModelEndpointIdentity(
        model_cohort_id=str(values["model_cohort_id"]),
        model_cohort_digest=str(values["model_cohort_digest"]),
        cohort_member_id=str(values["cohort_member_id"]),
        provider_config_id=str(values["provider_config_id"]),
        selected_entry_id=selected_entry_id,
        provider_family=str(values["provider_family"]),
        provider_model_id=str(values["provider_model_id"]),
        reasoning_profile_id=str(values["reasoning_profile_id"]),
        effective_reasoning_controls=normalized_reasoning.effective_controls,
        source_provider_config_digest=str(
            values["source_provider_config_digest"]
        ),
    )

    reasons: list[str] = []
    if (
        expected_identity.model_endpoint_identity_digest
        != values["model_endpoint_identity_digest"]
    ):
        reasons.append("model_endpoint_identity_digest_mismatch")
    binding: ValidatedModelEndpointBinding | None = None
    try:
        binding = validate_fixed_entry_config_identity(
            expected_identity=expected_identity,
            provider_config_id=str(values["provider_config_id"]),
            source_config=source_config,
        )
    except PaperModelIdentityMismatch as exc:
        reasons.extend(exc.reasons)
    if reasons:
        raise PaperModelIdentityMismatch(reasons)
    assert binding is not None
    return binding


def validate_fixed_entry_submission_identity(
    *,
    expected_identity: PaperModelEndpointIdentity,
    prepared_execution_config_digest: str,
    condition_id: str,
    repeat_id: int,
    run_id: str,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    request: Mapping[str, Any],
    request_ref: Mapping[str, Any],
    provenance: Mapping[str, Any],
    provenance_ref: Mapping[str, Any],
    raw_output: Mapping[str, Any] | None,
    raw_output_ref: Mapping[str, Any] | None,
    usage_ref: Mapping[str, Any],
    created_at: str,
) -> PaperModelExecutionRecord:
    """核对一次 provider submission，并返回可持久化的身份审计记录。"""

    reasons: list[str] = []
    expected_provider = expected_identity.provider_family
    expected_entry = expected_identity.selected_entry_id
    expected_model = expected_identity.provider_model_id

    if provenance.get("schema_version") not in {
        "phase7.ai_provider_call_provenance.v1",
        "phase7.ai_provider_call_provenance.v2",
    }:
        reasons.append("provider_provenance_schema_mismatch")

    hard_requirements = request.get("hard_requirements")
    capability_snapshot = request.get("capability_snapshot")
    if not isinstance(hard_requirements, Mapping) or (
        hard_requirements.get("provider_family") != expected_provider
    ):
        reasons.append("request_provider_family_mismatch")
    if not isinstance(capability_snapshot, Mapping) or (
        capability_snapshot.get("provider_family") != expected_provider
    ):
        reasons.append("request_capability_provider_family_mismatch")

    if provenance.get("provider_family") != expected_provider:
        reasons.append("provenance_provider_family_mismatch")
    if provenance.get("config_digest") != prepared_execution_config_digest:
        reasons.append("prepared_execution_config_digest_mismatch")

    selection = provenance.get("selection_record")
    if not isinstance(selection, Mapping):
        reasons.append("missing_provider_selection_record")
    else:
        for field_name in ("eligible_entry_ids", "attempt_entry_ids"):
            entry_ids = selection.get(field_name)
            if not isinstance(entry_ids, list) or any(
                entry_id != expected_entry for entry_id in entry_ids
            ):
                reasons.append("selection_entry_id_mismatch")
        selected_entry_id = selection.get("selected_entry_id")
        if selected_entry_id is not None and selected_entry_id != expected_entry:
            reasons.append("selection_entry_id_mismatch")

    final_entry_id = provenance.get("final_entry_id")
    if final_entry_id is not None and final_entry_id != expected_entry:
        reasons.append("final_entry_id_mismatch")

    attempts_value = provenance.get("attempts")
    provider_attempts: list[dict[str, Any]] = []
    request_identities: list[dict[str, Any]] = []
    requested_model: str | None = None
    if not isinstance(attempts_value, list):
        reasons.append("invalid_provider_attempts")
        attempts_value = []
    for attempt in attempts_value:
        if not isinstance(attempt, Mapping):
            reasons.append("invalid_provider_attempt")
            continue
        provider_attempts.append(
            {
                "provider_family": attempt.get("provider_family"),
                "entry_id": attempt.get("entry_id"),
                "configured_model": (
                    attempt.get("configured_model")
                    if "configured_model" in attempt
                    else attempt.get("model")
                ),
                "result_kind": attempt.get("result_kind"),
            }
        )
        if attempt.get("provider_family") != expected_provider:
            reasons.append("attempt_provider_family_mismatch")
        if attempt.get("entry_id") != expected_entry:
            reasons.append("attempt_entry_id_mismatch")
        attempt_configured_model = (
            attempt.get("configured_model")
            if "configured_model" in attempt
            else attempt.get("model")
        )
        if attempt_configured_model != expected_model:
            reasons.append("attempt_model_mismatch")

        request_identity = attempt.get("provider_request_identity")
        if not isinstance(request_identity, Mapping):
            reasons.append("missing_provider_request_identity")
            continue
        request_identity_body = dict(request_identity)
        request_identities.append(request_identity_body)
        request_identity_schema = request_identity.get("schema_version")
        if request_identity_schema not in {
            "phase7.provider_request_identity.v1",
            "phase7.provider_request_identity.v2",
        }:
            reasons.append("provider_request_identity_schema_mismatch")
        if request_identity.get("provider_family") != expected_provider:
            reasons.append("request_identity_provider_family_mismatch")
        if request_identity.get("entry_id") != expected_entry:
            reasons.append("request_identity_entry_id_mismatch")
        if request_identity_schema == "phase7.provider_request_identity.v2":
            configured_model = request_identity.get("configured_model")
            actual_requested_model = request_identity.get("requested_model")
        else:
            configured_model = request_identity.get("model")
            actual_requested_model = request_identity.get("model")
        if configured_model != expected_model:
            reasons.append("request_identity_configured_model_mismatch")
        if isinstance(actual_requested_model, str) and actual_requested_model.strip():
            requested_model = actual_requested_model
        if actual_requested_model != expected_model:
            reasons.append("request_identity_model_mismatch")
        controls_digest = request_identity.get("effective_request_controls_digest")
        if not isinstance(controls_digest, str) or _SHA256_PATTERN.fullmatch(
            controls_digest
        ) is None:
            reasons.append("invalid_effective_request_controls_digest")
        reasoning_controls = request_identity.get("reasoning_controls")
        if not isinstance(reasoning_controls, Mapping):
            reasons.append("invalid_request_reasoning_controls")
            continue
        try:
            actual_reasoning = normalize_reasoning_identity(
                provider_family=expected_provider,
                request_overrides=reasoning_controls,
            )
        except PaperModelIdentityMismatch as exc:
            reasons.extend(exc.reasons)
            continue
        if (
            actual_reasoning.reasoning_profile_id
            != expected_identity.reasoning_profile_id
        ):
            reasons.append("request_reasoning_profile_id_mismatch")
        expected_controls = dict(expected_identity.effective_reasoning_controls)
        actual_controls = dict(actual_reasoning.effective_controls)
        allows_implicit_json_thinking_disable = (
            expected_provider == "siliconflow"
            and expected_controls == {}
            and actual_controls == {"enable_thinking": False}
        )
        if actual_controls != expected_controls and not (
            allows_implicit_json_thinking_disable
        ):
            reasons.append("request_reasoning_controls_mismatch")

    resolved_model: str | None = None
    response_model_status = "unavailable"
    if raw_output is not None:
        if raw_output.get("provider_family") != expected_provider:
            reasons.append("raw_provider_family_mismatch")
        if raw_output.get("entry_id") != expected_entry:
            reasons.append("raw_entry_id_mismatch")
        try:
            raw_identity = read_raw_model_identity_evidence(raw_output)
        except ValueError:
            reasons.append("invalid_raw_model_identity_evidence")
        else:
            resolved_model = raw_identity.resolved_model
            response_model_status = raw_identity.response_model_status
            if (
                raw_identity.configured_model is not None
                and raw_identity.configured_model != expected_model
            ):
                reasons.append("raw_configured_model_mismatch")
            if (
                raw_identity.requested_model is not None
                and raw_identity.requested_model != expected_model
            ):
                reasons.append("raw_requested_model_mismatch")
            if resolved_model is not None and resolved_model != expected_model:
                reasons.append("resolved_model_mismatch")
        if resolved_model is None:
            reasons.append("missing_resolved_model")
    elif provenance.get("final_result_kind") in {"succeeded", "parse_failed"}:
        reasons.append("missing_raw_output")
        reasons.append("missing_resolved_model")

    stable_reasons = tuple(dict.fromkeys(reasons))
    if stable_reasons:
        identity_status = "model_identity_mismatch"
    elif raw_output is None:
        identity_status = "not_observed"
    else:
        identity_status = "matched"
    return PaperModelExecutionRecord(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=run_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        expected_identity=expected_identity.to_dict(),
        source_provider_config_digest=(
            expected_identity.source_provider_config_digest
        ),
        prepared_execution_config_digest=prepared_execution_config_digest,
        request_ref=dict(request_ref),
        provenance_ref=dict(provenance_ref),
        raw_output_ref=dict(raw_output_ref) if raw_output_ref is not None else None,
        usage_ref=dict(usage_ref),
        actual_request_identities=request_identities,
        actual_provider_attempts=provider_attempts,
        requested_model=requested_model,
        resolved_model=resolved_model,
        response_model_status=response_model_status,
        identity_status=identity_status,
        mismatch_reasons=stable_reasons,
        paper_eligible=identity_status == "matched" and raw_output is not None,
        created_at=created_at,
    )


def _endpoint_digest_for_actual_binding(
    *,
    expected_identity: PaperModelEndpointIdentity,
    provider_config_id: str,
    provider_family: str,
    provider_model_id: str,
    normalized_reasoning: NormalizedReasoningIdentity,
) -> str:
    body = {
        "schema_version": expected_identity.schema_version,
        "model_cohort_id": expected_identity.model_cohort_id,
        "model_cohort_digest": expected_identity.model_cohort_digest,
        "cohort_member_id": expected_identity.cohort_member_id,
        "provider_config_id": provider_config_id,
        "selected_entry_id": expected_identity.selected_entry_id,
        "provider_family": provider_family,
        "provider_model_id": provider_model_id,
        "reasoning_profile_id": normalized_reasoning.reasoning_profile_id,
        "effective_reasoning_controls": dict(
            normalized_reasoning.effective_controls
        ),
    }
    return _sha256_json(body)


def _entry_by_id(
    config: AIAPIExecutorConfig,
    entry_id: str,
) -> AIAPIProviderEntry | None:
    return next((entry for entry in config.entries if entry.entry_id == entry_id), None)


def _normalized_profile_label(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperModelIdentityMismatch("invalid_reasoning_profile")
    return value.strip().lower()


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PaperModelIdentityMismatch(f"invalid_{field_name}")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PaperModelIdentityMismatch(f"invalid_{field_name}")


def _sha256_json(body: dict[str, Any]) -> str:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
