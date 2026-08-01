"""CLI entrypoint for paper real-AI experiment planning and gated execution."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter_ns

from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibDeepSeekTransport,
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.experiments.paper_budget import (
    EXP1_PILOT_EXPERIMENT_ID,
    Exp1PilotProfile,
    PaperBudgetApprovalError,
    build_exp5_v3_token_ceiling_mapping,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
    load_paper_catalogs,
)
from tokenshare.experiments.paper_catalog_execution_view import (
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_exp5_smoke_evidence import (
    EXP5_SMOKE_SUITE_ID,
    Exp5SmokeEvidenceError,
    load_exp5_smoke_evidence_bundle,
    write_exp5_smoke_evidence_bundle,
)
from tokenshare.experiments.paper_models import (
    PAPER_FORMAL_AI_TIMEOUT_SECONDS,
    PaperBudgetResult,
    PaperStatus,
    PaperSuiteResult,
    digest_json,
)
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
    PaperInfrastructureBlockedError,
    execute_paper_formal_suite,
    replay_paper_formal_suite,
    write_paper_formal_replay_report,
)
from tokenshare.experiments.paper_exp1 import EXP1_FORMAL_REQUEST_CONTROLS
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_formal_metrics import (
    recompute_paper_formal_metrics,
)
from tokenshare.experiments.paper_formal_report import (
    generate_paper_formal_report,
)
from tokenshare.experiments.paper_runner import (
    build_gate_c_dispatch_plans,
    build_lean_3x3_matrix_plan,
    execute_gate_c_pilot_case,
    execute_exp1_pilot,
    expand_plan_conditions,
    normalize_experiment_ids,
    validate_experiment_dependency_order,
    validate_gate_c_pilot_output_root,
    validate_exp1_selector_output_root,
)
from tokenshare.experiments.paper_smoke import (
    execute_paper_smoke_suite,
    load_paper_smoke_profile,
    replay_paper_smoke_suite,
    resolve_paper_smoke_execution_plan,
)
from tokenshare.experiments.paper_suite_scale import (
    load_paper_suite_scale_profile,
)
from tokenshare.runtime_paths import default_data_root, resolve_experiment_output_root


DEFAULT_FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v2.jsonl")
DEFAULT_EXP1_PILOT_FACTOR_CATALOG = Path(
    "benchmarks/paper/factorization_catalog.v1.jsonl"
)
DEFAULT_LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
DEFAULT_LEAN_LEMMA_GRAPH_CATALOG = Path(
    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)
DEFAULT_EXP1_PILOT_PROFILE = Path(
    "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
)
DEFAULT_PAPER_SUITE_SCALE_PROFILE = Path(
    "benchmarks/paper/paper_suite_scale_profile.v1.json"
)
EXP1_EXP4_ONLY_EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
EXP3_EXP4_ONLY_EXPERIMENT_IDS = (
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG = Path(
    "benchmarks/paper/exp1_baseline_provider_config.v3.json"
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_DIGEST = (
    "sha256:4bdf0d330c8d01af9760f17b333d75a68f0b8bfad214e807072562e057ef3923"
)
EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID = "exp1_baseline_deepseek"
EXP1_EXP4_REQUIRED_ENTRY_ID = "deepseek_v4_pro_exp1_baseline"
FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT = 304_096
FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT = 0.05
EXP5_V3_EXECUTION_SCHEDULE_PATH = Path(
    "audit/exp5_v3_execution_schedule.json"
)


class _ProviderFamilyTransportRouter:
    """为 mixed smoke 按已验证的 entry 绑定选择既有真实 transport。"""

    def __init__(
        self,
        *,
        provider_family_by_entry_id: Mapping[str, str],
    ) -> None:
        self._provider_family_by_entry_id = dict(provider_family_by_entry_id)
        self._transports = {
            "siliconflow": UrlLibSiliconFlowTransport(),
            "openai": UrlLibOpenAITransport(),
            "deepseek": UrlLibDeepSeekTransport(),
        }

    def tokenshare_transport_for_provider(self, provider_family: str):
        try:
            return self._transports[provider_family]
        except KeyError as exc:
            raise ValueError(
                f"unsupported routed provider_family: {provider_family}"
            ) from exc

def _provider_family_bindings_from_execution_configs(
    ai_api_configs: Mapping[str, object],
) -> dict[str, str]:
    """从本次已加载配置建立 router 所需的完整 entry/provider 映射。"""

    bindings: dict[str, str] = {}
    for config_id, config in ai_api_configs.items():
        if config_id == APPROVED_ENDPOINT_BINDINGS_KEY:
            continue
        provider_family = getattr(config, "provider_family", None)
        entries = getattr(config, "entries", None)
        if (
            not isinstance(provider_family, str)
            or not provider_family
            or not isinstance(entries, Sequence)
            or isinstance(entries, (str, bytes))
        ):
            raise ValueError("routed AI API execution config is invalid")
        for entry in entries:
            entry_id = getattr(entry, "entry_id", None)
            if not isinstance(entry_id, str) or not entry_id:
                raise ValueError("routed AI API entry identity is missing")
            previous = bindings.get(entry_id)
            if previous is not None and previous != provider_family:
                raise ValueError(
                    "routed AI API entry identity maps to multiple provider families"
                )
            bindings[entry_id] = provider_family
    if not bindings:
        raise ValueError("routed AI API execution config inventory is empty")
    return bindings


class _Exp5V3SharedProviderTransport:
    """四个 v3 entry 共用一个 provider-config namespace semaphore。"""

    def __init__(
        self,
        *,
        delegate: object,
        member_id_by_entry_id: Mapping[str, str],
        member_id_by_model: Mapping[str, str] | None = None,
        schedule_binding: Mapping[str, object],
        prior_evidence: Mapping[str, object] | None = None,
    ) -> None:
        normalized_binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
        max_in_flight_global = int(normalized_binding["max_in_flight_global"])
        if type(max_in_flight_global) is not int or max_in_flight_global != 3:
            raise ValueError(
                "Exp5 v3 shared provider limit must be exact integer 3"
            )
        if not member_id_by_entry_id or len(
            set(member_id_by_entry_id.values())
        ) != len(member_id_by_entry_id):
            raise ValueError("Exp5 v3 entry/member namespace mapping is invalid")
        self.tokenshare_underlying_transport = delegate
        self.tokenshare_offline_capturing_transport = bool(
            getattr(
                delegate,
                "tokenshare_offline_capturing_transport",
                False,
            )
        )
        self._delegate = delegate
        self._member_id_by_entry_id = dict(member_id_by_entry_id)
        self._member_id_by_model = dict(member_id_by_model or {})
        self._schedule_binding = normalized_binding
        self._max_in_flight_global = max_in_flight_global
        self._prior_evidence = (
            _validate_exp5_v3_execution_schedule(
                prior_evidence,
                schedule_binding=normalized_binding,
            )
            if prior_evidence is not None
            else None
        )
        self._semaphore = BoundedSemaphore(max_in_flight_global)
        self._lock = Lock()
        self._active = 0
        self._active_by_member: dict[str, int] = {}
        self._observed_peak = 0
        self._provider_calls_made = 0
        self._observed_member_order: list[str] = []
        self._arm_windows: dict[str, dict[str, object]] = {}

    @property
    def new_provider_calls_made(self) -> int:
        with self._lock:
            return self._provider_calls_made

    @property
    def prior_evidence(self) -> dict[str, object] | None:
        if self._prior_evidence is None:
            return None
        return json.loads(json.dumps(self._prior_evidence))

    def tokenshare_transport_for_provider(self, provider_family: str):
        if provider_family == "siliconflow":
            return self
        resolver = getattr(self._delegate, "tokenshare_transport_for_provider", None)
        if callable(resolver):
            return resolver(provider_family)
        return self._delegate

    def post_chat_completion(
        self,
        *,
        api_key: str,
        body_bytes: bytes,
        normalized_absolute_endpoint: str,
        content_type: str,
        timeout_seconds: int,
    ):
        body = json.loads(body_bytes.decode("utf-8"))
        explicit_member = body.get("member_id") if isinstance(body, dict) else None
        member_id = (
            str(explicit_member)
            if explicit_member in self._member_id_by_entry_id.values()
            else self._member_id_by_model.get(str(body.get("model", "")))
            if isinstance(body, dict)
            else None
        )
        delegate = self._delegate
        resolver = getattr(delegate, "tokenshare_transport_for_provider", None)
        if callable(resolver):
            delegate = resolver("siliconflow")
        if member_id is None:
            return delegate.post_chat_completion(
                api_key=api_key,
                body_bytes=body_bytes,
                normalized_absolute_endpoint=normalized_absolute_endpoint,
                content_type=content_type,
                timeout_seconds=timeout_seconds,
            )
        with self._semaphore:
            self._record_call_started(member_id)
            try:
                return delegate.post_chat_completion(
                    api_key=api_key,
                    body_bytes=body_bytes,
                    normalized_absolute_endpoint=normalized_absolute_endpoint,
                    content_type=content_type,
                    timeout_seconds=timeout_seconds,
                )
            finally:
                self._record_call_ended(member_id)

    def execution_evidence(self) -> dict[str, object]:
        with self._lock:
            if self._active != 0 or any(self._active_by_member.values()):
                raise ValueError(
                    "Exp5 v3 execution schedule has provider calls still in flight"
                )
            prior_segments = (
                list(self._prior_evidence["execution_segments"])
                if self._prior_evidence is not None
                else []
            )
            segments = json.loads(json.dumps(prior_segments))
            if self._provider_calls_made:
                windows = [
                    dict(self._arm_windows[member_id])
                    for member_id in self._observed_member_order
                ]
                segment_body: dict[str, object] = {
                    "segment_index": len(segments),
                    "started_at": windows[0]["started_at"],
                    "ended_at": windows[-1]["ended_at"],
                    "observed_member_order": list(self._observed_member_order),
                    "observed_peak": self._observed_peak,
                    "provider_calls_made": self._provider_calls_made,
                    "arm_windows": windows,
                }
                segments.append(
                    {
                        **segment_body,
                        "segment_digest": digest_json(segment_body),
                    }
                )
        return _build_exp5_v3_schedule_evidence(
            schedule_binding=self._schedule_binding,
            execution_segments=segments,
        )

    def _record_call_started(self, member_id: str) -> None:
        recorded_at = _utc_now_string()
        tick = perf_counter_ns()
        with self._lock:
            self._active += 1
            self._provider_calls_made += 1
            self._observed_peak = max(self._observed_peak, self._active)
            member_active = self._active_by_member.get(member_id, 0) + 1
            self._active_by_member[member_id] = member_active
            if (
                not self._observed_member_order
                or self._observed_member_order[-1] != member_id
            ):
                self._observed_member_order.append(member_id)
            window = self._arm_windows.setdefault(
                member_id,
                {
                    "cohort_member_id": member_id,
                    "started_at": recorded_at,
                    "ended_at": recorded_at,
                    "started_tick_ns": tick,
                    "ended_tick_ns": tick,
                    "provider_calls_made": 0,
                    "observed_peak": 0,
                },
            )
            window["provider_calls_made"] = int(
                window["provider_calls_made"]
            ) + 1
            window["observed_peak"] = max(
                int(window["observed_peak"]),
                member_active,
            )

    def _record_call_ended(self, member_id: str) -> None:
        recorded_at = _utc_now_string()
        tick = perf_counter_ns()
        with self._lock:
            self._active -= 1
            self._active_by_member[member_id] -= 1
            window = self._arm_windows[member_id]
            window["ended_at"] = recorded_at
            window["ended_tick_ns"] = max(
                int(window["ended_tick_ns"]),
                tick,
            )


def _normalize_exp5_v3_schedule_binding(
    schedule_binding: Mapping[str, object],
) -> dict[str, object]:
    required = {
        "profile_digest",
        "execution_plan_digest",
        "sequence_plan_digest",
        "provider_namespace",
        "max_in_flight_global",
        "expected_member_order",
        "expected_condition_ids",
    }
    if set(schedule_binding) != required:
        raise ValueError("Exp5 v3 execution schedule binding fields are invalid")
    normalized = json.loads(json.dumps(dict(schedule_binding)))
    for name in (
        "profile_digest",
        "execution_plan_digest",
        "sequence_plan_digest",
        "provider_namespace",
    ):
        if not isinstance(normalized[name], str) or not normalized[name]:
            raise ValueError(f"Exp5 v3 execution schedule {name} is invalid")
    if type(normalized["max_in_flight_global"]) is not int or normalized[
        "max_in_flight_global"
    ] != 3:
        raise ValueError("Exp5 v3 execution schedule provider limit is invalid")
    expected_member_order = normalized["expected_member_order"]
    if (
        not isinstance(expected_member_order, list)
        or len(expected_member_order) != 4
        or any(not isinstance(item, str) or not item for item in expected_member_order)
        or len(set(expected_member_order)) != 4
    ):
        raise ValueError("Exp5 v3 execution schedule member order is invalid")
    expected_condition_ids = normalized["expected_condition_ids"]
    if (
        not isinstance(expected_condition_ids, list)
        or len(expected_condition_ids) != 8
        or any(not isinstance(item, str) or not item for item in expected_condition_ids)
        or len(set(expected_condition_ids)) != 8
    ):
        raise ValueError("Exp5 v3 execution schedule condition binding is invalid")
    return normalized


def _validate_exp5_v3_arm_window(window: object) -> dict[str, object]:
    required = {
        "cohort_member_id",
        "started_at",
        "ended_at",
        "started_tick_ns",
        "ended_tick_ns",
        "provider_calls_made",
        "observed_peak",
    }
    if not isinstance(window, Mapping) or set(window) != required:
        raise ValueError("Exp5 v3 execution schedule arm window fields are invalid")
    normalized = json.loads(json.dumps(dict(window)))
    member_id = normalized["cohort_member_id"]
    if not isinstance(member_id, str) or not member_id:
        raise ValueError("Exp5 v3 execution schedule arm identity is invalid")
    for name in ("started_at", "ended_at"):
        value = normalized[name]
        if not isinstance(value, str) or not value:
            raise ValueError("Exp5 v3 execution schedule UTC window is invalid")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "Exp5 v3 execution schedule UTC window is invalid"
            ) from exc
    if normalized["started_at"] > normalized["ended_at"]:
        raise ValueError("Exp5 v3 execution schedule UTC window is reversed")
    for name in ("started_tick_ns", "ended_tick_ns"):
        if type(normalized[name]) is not int:
            raise ValueError("Exp5 v3 execution schedule ticks must be exact integers")
    if normalized["started_tick_ns"] >= normalized["ended_tick_ns"]:
        raise ValueError("Exp5 v3 execution schedule monotonic window is invalid")
    for name in ("provider_calls_made", "observed_peak"):
        if type(normalized[name]) is not int or normalized[name] < 1:
            raise ValueError("Exp5 v3 execution schedule arm counters are invalid")
    return normalized


def _validate_exp5_v3_execution_segment(
    segment: object,
    *,
    segment_index: int,
) -> dict[str, object]:
    required = {
        "segment_index",
        "started_at",
        "ended_at",
        "observed_member_order",
        "observed_peak",
        "provider_calls_made",
        "arm_windows",
        "segment_digest",
    }
    if not isinstance(segment, Mapping) or set(segment) != required:
        raise ValueError("Exp5 v3 execution schedule segment fields are invalid")
    if type(segment.get("segment_index")) is not int or segment[
        "segment_index"
    ] != segment_index:
        raise ValueError("Exp5 v3 execution schedule segment index is invalid")
    windows_value = segment.get("arm_windows")
    if not isinstance(windows_value, list) or not windows_value:
        raise ValueError("Exp5 v3 execution schedule segment windows are invalid")
    windows = [_validate_exp5_v3_arm_window(item) for item in windows_value]
    member_order = [str(item["cohort_member_id"]) for item in windows]
    provider_calls_made = sum(int(item["provider_calls_made"]) for item in windows)
    observed_peak = max(int(item["observed_peak"]) for item in windows)
    body: dict[str, object] = {
        "segment_index": segment_index,
        "started_at": windows[0]["started_at"],
        "ended_at": windows[-1]["ended_at"],
        "observed_member_order": member_order,
        "observed_peak": observed_peak,
        "provider_calls_made": provider_calls_made,
        "arm_windows": windows,
    }
    if segment.get("segment_digest") != digest_json(body) or json.loads(
        json.dumps(dict(segment))
    ) != {**body, "segment_digest": digest_json(body)}:
        raise ValueError("Exp5 v3 execution schedule segment evidence drift")
    return {**body, "segment_digest": digest_json(body)}


def _build_exp5_v3_schedule_evidence(
    *,
    schedule_binding: Mapping[str, object],
    execution_segments: Sequence[object],
) -> dict[str, object]:
    binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
    segments = [
        _validate_exp5_v3_execution_segment(item, segment_index=index)
        for index, item in enumerate(execution_segments)
    ]
    merged_windows: list[dict[str, object]] = []
    overlap_violation = False
    previous_window: dict[str, object] | None = None
    for segment in segments:
        for raw_window in segment["arm_windows"]:
            window = dict(raw_window)
            if previous_window is not None and int(window["started_tick_ns"]) < int(
                previous_window["ended_tick_ns"]
            ):
                overlap_violation = True
            previous_window = window
            if (
                merged_windows
                and merged_windows[-1]["cohort_member_id"]
                == window["cohort_member_id"]
            ):
                merged = merged_windows[-1]
                merged["ended_at"] = window["ended_at"]
                merged["ended_tick_ns"] = window["ended_tick_ns"]
                merged["provider_calls_made"] = int(
                    merged["provider_calls_made"]
                ) + int(window["provider_calls_made"])
                merged["observed_peak"] = max(
                    int(merged["observed_peak"]),
                    int(window["observed_peak"]),
                )
            else:
                merged_windows.append(window)
    observed_member_order = [
        str(window["cohort_member_id"]) for window in merged_windows
    ]
    expected_member_order = list(binding["expected_member_order"])
    prefix_valid = observed_member_order == expected_member_order[
        : len(observed_member_order)
    ]
    provider_calls_made = sum(
        int(segment["provider_calls_made"]) for segment in segments
    )
    observed_peak = max(
        (int(segment["observed_peak"]) for segment in segments),
        default=0,
    )
    limit_violation = observed_peak > int(binding["max_in_flight_global"])
    sequence_complete = observed_member_order == expected_member_order
    body: dict[str, object] = {
        "schema_version": "tokenshare.paper_exp5_v3_execution_schedule.v2",
        **binding,
        "observed_peak": observed_peak,
        "provider_calls_made": provider_calls_made,
        "observed_member_order": observed_member_order,
        "sequence_complete": sequence_complete,
        "sequence_matches": sequence_complete,
        "prefix_valid": prefix_valid,
        "arm_windows": merged_windows,
        "execution_segments": segments,
        "overlap_violation": overlap_violation,
        "limit_violation": limit_violation,
    }
    return {**body, "evidence_digest": digest_json(body)}


def _validate_exp5_v3_execution_schedule(
    evidence: object,
    *,
    schedule_binding: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(evidence, Mapping):
        raise ValueError("Exp5 v3 execution schedule must be a JSON object")
    expected_schema = "tokenshare.paper_exp5_v3_execution_schedule.v2"
    if evidence.get("schema_version") != expected_schema:
        raise ValueError("Exp5 v3 execution schedule schema is unsupported")
    binding = _normalize_exp5_v3_schedule_binding(schedule_binding)
    for key, expected_value in binding.items():
        if evidence.get(key) != expected_value:
            raise ValueError(f"Exp5 v3 execution schedule binding drift: {key}")
    segments = evidence.get("execution_segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Exp5 v3 execution schedule segments are missing")
    canonical = _build_exp5_v3_schedule_evidence(
        schedule_binding=binding,
        execution_segments=segments,
    )
    if json.loads(json.dumps(dict(evidence))) != canonical:
        raise ValueError("Exp5 v3 execution schedule derived evidence drift")
    if (
        canonical["prefix_valid"] is not True
        or canonical["overlap_violation"] is True
        or canonical["limit_violation"] is True
    ):
        raise ValueError("Exp5 v3 execution schedule is not a valid prefix")
    return canonical


def _utc_now_string() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _paper_output_root(explicit_output_root: str | Path | None) -> Path:
    return resolve_experiment_output_root(explicit_output_root, "paper_v1")


def _configured_paper_output_boundary() -> Path:
    """返回 smoke 必须避开的正式 paper 容器；非法 override 不阻断显式路径。"""

    try:
        return _paper_output_root(None).resolve(strict=False)
    except ValueError:
        return (
            default_data_root() / "outputs" / "experiments" / "paper_v1"
        ).resolve(strict=False)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or run TokenShare paper real-AI experiments.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
    )
    parser.add_argument("--experiments", default="exp1")
    parser.add_argument("--worker-levels", default="10")
    parser.add_argument("--optional-worker-levels", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed-family", default="1")
    parser.add_argument("--real-transport", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--baseline-entry-id", default=None)
    parser.add_argument("--provider-attempt-limit", type=int, default=None)
    parser.add_argument("--token-limit", type=int, default=None)
    parser.add_argument("--cost-limit", type=float, default=None)
    parser.add_argument("--max-total-provider-attempts", type=int, default=None)
    parser.add_argument("--max-total-tokens", type=int, default=None)
    parser.add_argument("--max-cost-estimate", type=float, default=None)
    parser.add_argument("--stop-after-current-task", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--approve-budget-digest", default=None)
    parser.add_argument("--require-budget-approval", action="store_true")
    parser.add_argument("--unlimited-budget", action="store_true")
    parser.add_argument("--smoke-profile", default=None)
    parser.add_argument("--smoke-identity-only", action="store_true")
    parser.add_argument("--expect-smoke-execution-plan-digest", default=None)
    parser.add_argument("--expect-smoke-budget-digest", default=None)
    parser.add_argument("--exp1-pilot-profile", default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--ai-unit-id", default=None)
    parser.add_argument("--condition-id", default=None)
    parser.add_argument("--pilot-output-root", default=None)
    parser.add_argument(
        "--ai-api-config",
        default="benchmarks/paper/exp1_baseline_provider_config.v3.json",
    )
    parser.add_argument("--local-ai-api-config", default=None)
    parser.add_argument("--model-cohort-file", default=None)
    parser.add_argument("--model-entry-map", default=None)
    parser.add_argument("--exp5-smoke-evidence-bundle", default=None)
    parser.add_argument("--provider-config", action="append", default=[])
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    gate_c_transport=None,
    gate_c_ai_api_configs: dict | None = None,
) -> int:
    parser = _build_argument_parser()
    command_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(command_argv)
    args._command_argv = command_argv

    experiment_ids = normalize_experiment_ids(tuple(args.experiments.split(",")))
    try:
        validate_experiment_dependency_order(experiment_ids)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "invalid_experiment_dependency_order",
                    "message": str(error),
                    "experiment_ids": list(experiment_ids),
                    "provider_calls_made": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    if args.output_root is None:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "missing_output_root",
                    "message": (
                        "paper experiment commands require an explicit new "
                        "--output-root"
                    ),
                    "provider_calls_made": 0,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    output_base = _paper_output_root(args.output_root)
    output_root = output_base
    pilot_blocked_output_root: Path | None = None
    unlimited_conflicts = (
        args.require_budget_approval
        or args.approve_budget_digest is not None
        or args.max_total_provider_attempts is not None
        or args.max_total_tokens is not None
        or args.max_cost_estimate is not None
    )
    if args.unlimited_budget and unlimited_conflicts:
        message = (
            "--unlimited-budget is mutually exclusive with budget approval "
            "and all --max-total-* limits"
        )
        if args.smoke_profile is not None:
            try:
                smoke_profile = load_paper_smoke_profile(Path(args.smoke_profile))
            except (OSError, ValueError, json.JSONDecodeError):
                _write_blocked_suite(
                    output_root=output_root,
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_budget_mode",
                    message=message,
                )
            else:
                _write_smoke_blocked_suite(
                    output_root=output_root,
                    profile=smoke_profile,
                    failure_kind="invalid_budget_mode",
                    message=message,
                )
        else:
            _write_blocked_suite(
                output_root=output_root,
                experiment_ids=experiment_ids,
                failure_kind="invalid_budget_mode",
                message=message,
            )
        return 3
    if args.smoke_profile is not None:
        return _run_smoke_cli(
            args=args,
            output_root=output_root,
            transport=gate_c_transport,
            supplied_ai_api_configs=gate_c_ai_api_configs,
        )
    selector_requested = any(
        value is not None
        for value in (args.condition_id, args.case_id, args.ai_unit_id)
    )
    profile_pilot_requested = args.exp1_pilot_profile is not None
    invalid_profile_selector = profile_pilot_requested and (
        (args.case_id is None) != (args.ai_unit_id is None)
        or args.condition_id is not None
    )
    invalid_general_selector = not profile_pilot_requested and selector_requested and (
        args.condition_id is None or args.case_id is None
    )
    if (
        invalid_profile_selector
        or invalid_general_selector
        or (selector_requested and not args.pilot)
        or (selector_requested and args.pilot_output_root is None)
        or (args.pilot_output_root is not None and not args.pilot)
    ):
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_selector",
            message=(
                "profile pilots require paired --case-id/--ai-unit-id; general "
                "Gate C pilots require --condition-id and --case-id with optional "
                "--ai-unit-id. Selectors require --pilot and an independent "
                "--pilot-output-root. --ai-unit-id selects the frozen runtime "
                "diagnostic scope inside the system coordinator"
            ),
        )
        return 3
    pilot_profile = _load_cli_pilot_profile(
        profile_path=args.exp1_pilot_profile,
        output_root=output_base,
        experiment_ids=experiment_ids,
    )
    if args.exp1_pilot_profile is not None and pilot_profile is None:
        return 3
    if pilot_profile is not None:
        if args.pilot:
            output_root = output_base / str(pilot_profile.body["suite_id"])
            pilot_blocked_output_root = output_base / (
                f"{pilot_profile.body['suite_id']}_blocked"
            )
        pilot_cli_error = _pilot_cli_profile_error(
            pilot_profile=pilot_profile,
            experiment_ids=experiment_ids,
            worker_levels=_parse_int_tuple(args.worker_levels),
            optional_worker_levels=_parse_int_tuple(args.optional_worker_levels),
            repeats=args.repeats,
            seed_family=_parse_int_tuple(args.seed_family),
        )
        if pilot_cli_error is not None:
            _write_blocked_suite(
                output_root=pilot_blocked_output_root or output_root,
                experiment_ids=experiment_ids,
                failure_kind="pilot_profile_mismatch",
                message=pilot_cli_error,
                suite_id=f"{pilot_profile.body['suite_id']}_blocked",
            )
            return 3
        if selector_requested:
            try:
                validate_exp1_selector_output_root(
                    output_base=output_base,
                    suite_id=str(pilot_profile.body["suite_id"]),
                    execution_output_root=Path(str(args.pilot_output_root)),
                )
            except ValueError as exc:
                _write_blocked_suite(
                    output_root=pilot_blocked_output_root or output_root,
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message=str(exc),
                    suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                )
                return 3
    if args.pilot and pilot_profile is None and not selector_requested:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_pilot_profile",
            message=(
                "general --pilot requires --condition-id, --case-id, and an "
                "independent --pilot-output-root"
            ),
        )
        return 3
    if args.pilot and args.plan_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_mode",
            message="--pilot cannot be combined with --plan-only",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if pilot_profile is not None and not args.plan_only and not args.pilot:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_mode",
            message="--exp1-pilot-profile execution requires --pilot",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (args.resume or args.replay_only) and args.plan_only:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only cannot be combined with --plan-only",
        )
        return 3
    if args.resume and args.replay_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only are mutually exclusive",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if args.pilot and pilot_profile is not None and args.baseline_entry_id is None:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_baseline_entry_id",
            message="--baseline-entry-id is required for Exp1 pilot execution",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (
        args.pilot
        and pilot_profile is not None
        and args.baseline_entry_id
        != pilot_profile.model_endpoint_identity.selected_entry_id
    ):
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="baseline_entry_mismatch",
            message=(
                "--baseline-entry-id does not match the approved Exp1 pilot entry"
            ),
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if args.replay_only and not args.pilot:
        try:
            execution_result = replay_paper_formal_suite(output_root=output_root)
            write_paper_formal_replay_report(output_root=output_root)
            metrics = recompute_paper_formal_metrics(output_root)
            report_result = generate_paper_formal_report(
                output_root=output_root,
                metrics=metrics,
                secret_values=(),
            )
            FormalEvidenceStore(output_root)._refresh_evidence_manifest()
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "formal_replay_failed",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _formal_execution_exit_code(execution_result, report_result)
    model_endpoint_cohort_preflight, model_cohort = (
        _model_endpoint_cohort_preflight_for_suite(
            experiment_ids=experiment_ids,
            model_cohort_file=args.model_cohort_file,
            model_entry_map=args.model_entry_map,
            provider_config_args=tuple(args.provider_config),
            smoke_evidence_bundle=args.exp5_smoke_evidence_bundle,
        )
    )

    if not args.plan_only and not args.real_transport and not args.replay_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_real_transport",
            message="formal paper runs require --real-transport",
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 1
    catalog_manifest = (
        _load_exp1_pilot_paper_catalogs()
        if pilot_profile is not None
        else _load_default_paper_catalogs()
    )
    lean_3x3_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=catalog_manifest,
    )
    dispatch_plans = ()
    frozen_selection_commitments = None
    if pilot_profile is None:
        planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
        try:
            suite_scale_profile = load_paper_suite_scale_profile(
                DEFAULT_PAPER_SUITE_SCALE_PROFILE
            )
            dispatch_plans = build_gate_c_dispatch_plans(
                catalog_manifest=catalog_manifest,
                lean_3x3_matrix=lean_3x3_matrix,
                experiment_ids=experiment_ids,
                baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
                paper_suite_scale_profile=suite_scale_profile,
                output_root=output_root,
            )
        except ValueError as exc:
            _write_blocked_suite(
                output_root=output_root,
                experiment_ids=experiment_ids,
                failure_kind="gate_c_plan_blocked",
                message=str(exc),
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            )
            return 3
        conditions = tuple(
            condition
            for dispatch_plan in dispatch_plans
            for condition in dispatch_plan.conditions
        )
        frozen_selection_commitments = [
            {
                **selection.to_dict(),
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
            }
            for dispatch_plan in dispatch_plans
            for condition, selection in dispatch_plan.bound_items()
        ]
    else:
        conditions = ()
    del model_cohort
    endpoint_token_ceilings = build_exp5_v3_token_ceiling_mapping(
        model_endpoint_cohort_preflight
    )
    model_policy_preflight = None
    try:
        budget = (
            plan_exp1_pilot(
                catalog_manifest=catalog_manifest,
                pilot_profile=pilot_profile,
                plan_only=args.plan_only,
                approve_budget_digest=args.approve_budget_digest,
                budget_approval_required=args.require_budget_approval,
                budget_mode="unlimited" if args.unlimited_budget else None,
            )
            if pilot_profile is not None
            else plan_paper_suite(
                catalog_manifest=catalog_manifest,
                conditions=conditions,
                max_provider_attempts_per_ai_unit=1,
                token_upper_bound_per_provider_attempt=(
                    FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
                ),
                token_upper_bound_by_endpoint_identity_digest=(
                    endpoint_token_ceilings
                ),
                cost_upper_bound_per_provider_attempt=(
                    FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
                ),
                plan_only=args.plan_only,
                lean_3x3_matrix=lean_3x3_matrix,
                model_policy_preflight=model_policy_preflight,
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
                frozen_selections=frozen_selection_commitments,
                endpoint_identity={
                    "baseline": _baseline_endpoint_binding(
                        load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
                    ),
                    "model_endpoint_cohort_preflight": (
                        model_endpoint_cohort_preflight
                    ),
                },
                request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
                suite_identity={
                    "suite_version": "paper_v1",
                    "execution_scope": "formal_matrix",
                    "experiment_ids": list(experiment_ids),
                    "paper_suite_scale_profile_source_path": (
                        suite_scale_profile.source_path
                    ),
                    "paper_suite_scale_profile_digest": (
                        suite_scale_profile.profile_digest
                    ),
                    "exp5_selection_path": (
                        suite_scale_profile.exp5_source_selection_path
                    ),
                    "exp5_selection_digest": (
                        suite_scale_profile.exp5_source_selection_digest
                    ),
                },
                output_identity={
                    "output_root": output_root.resolve(strict=False).as_posix(),
                    "per_experiment_roots": [
                        (output_root / experiment_id).resolve(
                            strict=False
                        ).as_posix()
                        for experiment_id in experiment_ids
                    ],
                    **_shared_exp1_reference_output_identity(experiment_ids),
                },
                approve_budget_digest=args.approve_budget_digest,
                budget_approval_required=args.require_budget_approval,
                hard_limits={} if args.unlimited_budget else None,
                budget_mode="unlimited" if args.unlimited_budget else None,
            )
        )
    except PaperBudgetApprovalError as exc:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_budget_approval"
            if args.approve_budget_digest is None
            else "budget_digest_mismatch",
            message=str(exc),
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 2

    effective_budget_digest = (
        args.approve_budget_digest or budget.budget_digest
    )

    if args.pilot:
        if pilot_profile is not None:
            if not (args.resume or args.replay_only):
                try:
                    _inject_exp1_pilot_api_key(
                        pilot_profile=pilot_profile,
                        local_config_path=Path(args.ai_api_config),
                    )
                except (OSError, ValueError) as exc:
                    _write_blocked_suite(
                        output_root=pilot_blocked_output_root or output_root,
                        experiment_ids=experiment_ids,
                        failure_kind="missing_real_api_key",
                        message=str(exc),
                        suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                    )
                    return 1
            try:
                execution_result = execute_exp1_pilot(
                    catalog_manifest=catalog_manifest,
                    pilot_profile=pilot_profile,
                    budget=budget,
                    approved_budget_digest=effective_budget_digest,
                    baseline_entry_id=str(args.baseline_entry_id),
                    output_base=output_base,
                    execution_output_root=(
                        Path(args.pilot_output_root)
                        if args.pilot_output_root is not None
                        else None
                    ),
                    real_transport=args.real_transport,
                    provider_attempt_limit=args.provider_attempt_limit,
                    token_limit=args.token_limit,
                    cost_limit=args.cost_limit,
                    resume=args.resume,
                    replay_only=args.replay_only,
                    case_id=args.case_id,
                    ai_unit_id=args.ai_unit_id,
                )
            except ValueError as exc:
                if not selector_requested:
                    raise
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message=str(exc),
                    suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                )
                return 3
        else:
            if len(experiment_ids) != 1:
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_selector",
                    message="general Gate C pilot requires exactly one experiment",
                )
                return 3
            experiment_id = experiment_ids[0]
            try:
                validate_gate_c_pilot_output_root(
                    output_base=output_base,
                    experiment_id=experiment_id,
                    execution_output_root=Path(str(args.pilot_output_root)),
                )
                if gate_c_ai_api_configs is not None:
                    execution_configs = dict(gate_c_ai_api_configs)
                elif args.resume or args.replay_only:
                    execution_configs = {}
                elif experiment_id == "exp5_real_ai_model_endpoint_comparison":
                    execution_configs = load_provider_config_map(
                        _parse_provider_config_args(tuple(args.provider_config))
                    )
                else:
                    _inject_exp1_pilot_api_key(
                        pilot_profile=planning_profile,
                        local_config_path=Path(args.ai_api_config),
                    )
                    execution_configs = {
                        planning_profile.model_endpoint_identity.provider_config_id: (
                            planning_profile.source_provider_config
                        )
                    }
                execution_result = execute_gate_c_pilot_case(
                    catalog_manifest=catalog_manifest,
                    lean_3x3_matrix=lean_3x3_matrix,
                    experiment_id=experiment_id,
                    baseline_endpoint_binding=_baseline_endpoint_binding(
                        planning_profile
                    ),
                    model_endpoint_cohort_preflight=(
                        model_endpoint_cohort_preflight
                        if experiment_id
                        == "exp5_real_ai_model_endpoint_comparison"
                        else None
                    ),
                    budget=budget,
                    approved_budget_digest=effective_budget_digest,
                    condition_id=str(args.condition_id),
                    case_id=str(args.case_id),
                    ai_unit_id=args.ai_unit_id,
                    execution_output_root=Path(str(args.pilot_output_root)),
                    ai_api_configs=execution_configs,
                    transport=gate_c_transport,
                    real_transport=args.real_transport,
                    resume=args.resume,
                    replay_only=args.replay_only,
                )
            except (OSError, ValueError) as exc:
                _write_blocked_suite(
                    output_root=Path(str(args.pilot_output_root)),
                    experiment_ids=experiment_ids,
                    failure_kind="invalid_pilot_execution",
                    message=str(exc),
                )
                return 3
        output_root = Path(str(execution_result.to_dict()["output_root"]))
        _write_plan_artifacts(
            output_root=output_root,
            budget=budget.to_dict(),
            pilot_profile=(
                pilot_profile.to_dict() if pilot_profile is not None else None
            ),
            lean_3x3_matrix=lean_3x3_matrix,
        )
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _execution_result_exit_code(execution_result)

    if not args.plan_only:
        if gate_c_ai_api_configs is not None:
            execution_configs = dict(gate_c_ai_api_configs)
        elif args.replay_only:
            execution_configs = {}
        else:
            execution_configs = {}
            if any(
                experiment_id
                != "exp5_real_ai_model_endpoint_comparison"
                for experiment_id in experiment_ids
            ):
                _inject_exp1_pilot_api_key(
                    pilot_profile=planning_profile,
                    local_config_path=Path(args.ai_api_config),
                )
                execution_configs[
                    planning_profile.model_endpoint_identity.provider_config_id
                ] = planning_profile.source_provider_config
            if "exp5_real_ai_model_endpoint_comparison" in experiment_ids:
                execution_configs.update(
                    load_provider_config_map(
                        _parse_provider_config_args(tuple(args.provider_config))
                    )
                )
                execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY] = {
                    "exp5_real_ai_model_endpoint_comparison": (
                        model_endpoint_cohort_preflight
                    )
                }
        hard_limits = {
            key: value
            for key, value in (
                (
                    "max_total_provider_attempts",
                    args.max_total_provider_attempts,
                ),
                ("max_total_tokens", args.max_total_tokens),
                ("max_cost_estimate", args.max_cost_estimate),
                (
                    "stop_after_current_task",
                    True if args.stop_after_current_task else None,
                ),
            )
            if value is not None
        }
        budget_approval = dict(
            budget.quota_preflight.get(
                "budget_approval",
                {
                    "approval_mode": (
                        "digest_approved"
                        if args.require_budget_approval
                        else "user_bypassed"
                    ),
                    "budget_digest": budget.budget_digest,
                },
            )
        )
        budget_approval.setdefault("budget_digest", budget.budget_digest)
        try:
            execution_result = execute_paper_formal_suite(
                dispatch_plans=dispatch_plans,
                catalog_manifest=catalog_manifest,
                budget=budget,
                budget_approval=budget_approval,
                output_root=output_root,
                ai_api_configs=execution_configs,
                transport=gate_c_transport,
                real_transport=args.real_transport,
                hard_limits=hard_limits,
                resume=args.resume,
                replay_only=args.replay_only,
            )
        except PaperInfrastructureBlockedError as error:
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        **error.to_summary(),
                        "message": str(error),
                        "provider_calls_made": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 3
        budget_path = output_root / "run_budget.json"
        if not budget_path.is_file():
            budget_path.write_text(
                json.dumps(
                    budget.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        report_result = None
        suite_manifest_path = output_root / "suite_manifest.json"
        if not suite_manifest_path.is_file():
            _write_suite(output_root, execution_result)
        else:
            suite_manifest = json.loads(
                suite_manifest_path.read_text(encoding="utf-8")
            )
            if suite_manifest.get("formal") is True:
                write_paper_formal_replay_report(output_root=output_root)
                metrics = recompute_paper_formal_metrics(output_root)
                report_result = generate_paper_formal_report(
                    output_root=output_root,
                    metrics=metrics,
                    secret_values=_configured_secret_values(execution_configs),
                )
                FormalEvidenceStore(output_root)._refresh_evidence_manifest()
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return _formal_execution_exit_code(execution_result, report_result)

    _write_plan_artifacts(
        output_root=output_root,
        budget=budget.to_dict(),
        pilot_profile=(
            pilot_profile.to_dict() if pilot_profile is not None else None
        ),
        lean_3x3_matrix=lean_3x3_matrix,
    )
    if dispatch_plans:
        (output_root / "paper_dispatch_plans.json").write_text(
            json.dumps(
                {
                    "schema_version": "tokenshare.paper_dispatch_plan_bundle.v1",
                    "provider_calls_made": 0,
                    "plans": [plan.to_dict() for plan in dispatch_plans],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if model_policy_preflight is not None:
        (output_root / "model_policy_plan.json").write_text(
            json.dumps(
                model_policy_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if model_endpoint_cohort_preflight is not None:
        (output_root / "model_endpoint_cohort_plan.json").write_text(
            json.dumps(
                model_endpoint_cohort_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    has_planned_dispatch = any(
        plan.status == "planned" and bool(plan.conditions)
        for plan in dispatch_plans
    )
    suite_status = (
        PaperStatus.BLOCKED
        if (
            model_endpoint_cohort_preflight is not None
            and model_endpoint_cohort_preflight.get("status") == "blocked"
            and not has_planned_dispatch
        )
        else PaperStatus.PLANNED
    )
    suite = PaperSuiteResult(
        suite_id=(
            str(pilot_profile.body["suite_id"])
            if pilot_profile is not None
            else "paper_v1_plan"
        ),
        status=suite_status,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=budget.planned_conditions,
        run_count=budget.planned_root_runs,
        task_count=budget.planned_root_runs,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref={"path": "audit/paper_eligibility_report.json"},
        budget_ref=budget.to_dict(),
        metrics_refs=[],
        audit_refs=[
            {"path": "lean_3x3_matrix.json"},
            *(
                [{"path": "exp1_pilot_profile.json"}]
                if pilot_profile is not None
                else []
            ),
            *(
                [{"path": "model_policy_plan.json"}]
                if model_policy_preflight is not None
                else []
            ),
            *(
                [{"path": "model_endpoint_cohort_plan.json"}]
                if model_endpoint_cohort_preflight is not None
                else []
            ),
        ],
        error_summary=[],
        model_policy_preflight=model_policy_preflight,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _baseline_endpoint_binding(profile: Exp1PilotProfile) -> dict:
    identity = profile.model_endpoint_identity.to_dict()
    request_controls = dict(EXP1_FORMAL_REQUEST_CONTROLS)
    return {
        **identity,
        "model_entry_id": identity["selected_entry_id"],
        "request_controls": request_controls,
    }


def _is_exp1_exp4_only_smoke(profile: object) -> bool:
    return tuple(profile.experiment_ids) in {
        EXP1_EXP4_ONLY_EXPERIMENT_IDS,
        EXP3_EXP4_ONLY_EXPERIMENT_IDS,
    }


def _explicit_ai_api_config_path(command_argv: Sequence[str]) -> Path | None:
    """返回 CLI 显式给出的 provider config；缺省值不能充当启动授权。"""

    for index, argument in enumerate(command_argv):
        if argument == "--ai-api-config":
            if index + 1 >= len(command_argv):
                return None
            return Path(command_argv[index + 1])
        if argument.startswith("--ai-api-config="):
            return Path(argument.split("=", 1)[1])
    return None


def _validate_exp1_exp4_v3_config_path(args: argparse.Namespace) -> None:
    selected_path = _explicit_ai_api_config_path(args._command_argv)
    if selected_path is None:
        raise ValueError(
            "Exp1-4 smoke execution requires explicit --ai-api-config "
            "benchmarks/paper/exp1_baseline_provider_config.v3.json"
        )
    required_path = EXP1_EXP4_REQUIRED_PROVIDER_CONFIG.resolve(strict=False)
    if selected_path.resolve(strict=False) != required_path:
        raise ValueError(
            "Exp1-4 smoke execution requires the registered v3 provider config path"
        )


def _validate_exp1_exp4_v3_execution_config(config: object) -> None:
    """在 provider dispatch 前冻结 Exp1-4 DeepSeek v3 的完整语义。"""

    if config is None or not hasattr(config, "config_digest"):
        raise ValueError("Exp1-4 v3 baseline provider config is missing")
    if config.config_digest != EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_DIGEST:
        raise ValueError("Exp1-4 v3 baseline provider config digest mismatch")
    if config.provider_family != "deepseek":
        raise ValueError("Exp1-4 v3 baseline provider must be official DeepSeek")
    expected_defaults = {
        "timeout_seconds": 600,
        "max_tokens": 300_000,
        "stream": False,
        "max_provider_attempts": 1,
    }
    if config.defaults != expected_defaults:
        raise ValueError("Exp1-4 v3 baseline request defaults mismatch")
    enabled_entries = [entry for entry in config.entries if entry.enabled]
    if len(config.entries) != 1 or len(enabled_entries) != 1:
        raise ValueError("Exp1-4 v3 baseline must contain one enabled entry")
    entry = enabled_entries[0]
    expected_entry_fields = {
        "entry_id": EXP1_EXP4_REQUIRED_ENTRY_ID,
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-pro",
        "endpoint": "/chat/completions",
        "supports_json_mode": True,
        "supports_streaming": False,
        "request_overrides": {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
    }
    actual_entry_fields = {
        field_name: getattr(entry, field_name)
        for field_name in expected_entry_fields
    }
    if actual_entry_fields != expected_entry_fields:
        raise ValueError("Exp1-4 v3 baseline endpoint or reasoning controls mismatch")
    if config.local_concurrency != {"max_in_flight_global": 50}:
        raise ValueError("Exp1-4 v3 baseline provider inflight limit mismatch")


def _exp5_v3_smoke_execution_transport(
    *,
    profile: object,
    execution_plan: object,
    model_endpoint_cohort_preflight: Mapping[str, object] | None,
    transport: object | None,
    ai_api_configs: Mapping[str, object] | None = None,
    prior_evidence: Mapping[str, object] | None = None,
) -> tuple[object | None, _Exp5V3SharedProviderTransport | None]:
    contract = getattr(profile, "exp5_v3_contract", None)
    if not isinstance(contract, Mapping):
        return transport, None
    if (
        not isinstance(model_endpoint_cohort_preflight, Mapping)
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        raise ValueError(
            "Exp5 v3 shared provider limiter requires a planned preflight"
        )
    repeat_member_order = contract.get("repeat_member_order")
    if not isinstance(repeat_member_order, Mapping):
        raise ValueError("Exp5 v3 smoke repeat member order is missing")
    expected_member_order = repeat_member_order.get("0")
    if not isinstance(expected_member_order, list) or len(
        expected_member_order
    ) != 4:
        raise ValueError("Exp5 v3 smoke repeat-0 member order is invalid")
    member_plans = model_endpoint_cohort_preflight.get("member_plans")
    if not isinstance(member_plans, Mapping) or set(member_plans) != set(
        expected_member_order
    ):
        raise ValueError("Exp5 v3 shared provider member inventory drift")
    provider_namespace = str(contract["provider_config_id"])
    member_id_by_entry_id: dict[str, str] = {}
    member_id_by_model: dict[str, str] = {}
    exp5_provider_family_by_entry_id: dict[str, str] = {}
    member_provider_config_ids: set[str] = set()
    for member_id in expected_member_order:
        member_plan = member_plans[member_id]
        member_provider_config_id = (
            member_plan.get("provider_config_id")
            if isinstance(member_plan, Mapping)
            else None
        )
        if (
            not isinstance(member_plan, Mapping)
            or member_plan.get("status") != "planned"
            or member_plan.get("provider_family") != "siliconflow"
            or not isinstance(member_provider_config_id, str)
            or not member_provider_config_id
        ):
            raise ValueError(
                "Exp5 v3 shared provider member plan is not executable"
            )
        member_provider_config_ids.add(member_provider_config_id)
        entry_id = member_plan.get("selected_entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("Exp5 v3 shared provider entry identity is missing")
        if entry_id in member_id_by_entry_id:
            raise ValueError("Exp5 v3 shared provider entry identities must be unique")
        member_id_by_entry_id[entry_id] = str(member_id)
        provider_model_id = member_plan.get("provider_model_id")
        if not isinstance(provider_model_id, str) or not provider_model_id:
            raise ValueError("Exp5 v3 shared provider model identity is missing")
        member_id_by_model[provider_model_id] = str(member_id)
        exp5_provider_family_by_entry_id[entry_id] = str(
            member_plan["provider_family"]
        )
    if len(member_provider_config_ids) != 1:
        raise ValueError("Exp5 v3 member provider config namespace drift")

    if transport is None:
        if not isinstance(ai_api_configs, Mapping):
            raise ValueError(
                "default provider router requires approved execution configs"
            )
        provider_family_by_entry_id = (
            _provider_family_bindings_from_execution_configs(ai_api_configs)
        )
        if any(
            provider_family_by_entry_id.get(entry_id) != provider_family
            for entry_id, provider_family in exp5_provider_family_by_entry_id.items()
        ):
            raise ValueError("Exp5 v3 routed provider binding drift")
        delegate = _ProviderFamilyTransportRouter(
            provider_family_by_entry_id=provider_family_by_entry_id
        )
    else:
        delegate = transport
    schedule_binding = _exp5_v3_execution_schedule_binding(
        profile=profile,
        execution_plan=execution_plan,
    )
    limiter = _Exp5V3SharedProviderTransport(
        delegate=delegate,
        member_id_by_entry_id=member_id_by_entry_id,
        member_id_by_model=member_id_by_model,
        schedule_binding=schedule_binding,
        prior_evidence=prior_evidence,
    )
    return limiter, limiter


def _exp5_v3_execution_schedule_binding(
    *,
    profile: object,
    execution_plan: object,
) -> dict[str, object]:
    contract = getattr(profile, "exp5_v3_contract", None)
    if not isinstance(contract, Mapping):
        raise ValueError("Exp5 v3 execution schedule contract is missing")
    repeat_member_order = contract.get("repeat_member_order")
    expected_member_order = (
        repeat_member_order.get("0")
        if isinstance(repeat_member_order, Mapping)
        else None
    )
    if not isinstance(expected_member_order, list):
        raise ValueError("Exp5 v3 execution schedule member order is missing")
    plans = [
        plan
        for plan in execution_plan.dispatch_plans
        if plan.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    ]
    if len(plans) != 1:
        raise ValueError("Exp5 v3 execution schedule dispatch plan is ambiguous")
    conditions = tuple(plans[0].conditions)
    expected_condition_members = [
        str(member_id)
        for member_id in expected_member_order
        for _repeat_index in range(2)
    ]
    actual_condition_members = [
        str(condition.cohort_member_id) for condition in conditions
    ]
    if actual_condition_members != expected_condition_members:
        raise ValueError("Exp5 v3 execution schedule condition order drift")
    return _normalize_exp5_v3_schedule_binding(
        {
            "profile_digest": str(profile.profile_digest),
            "execution_plan_digest": str(execution_plan.execution_plan_digest),
            "sequence_plan_digest": str(contract["sequence_plan_digest"]),
            "provider_namespace": str(contract["provider_config_id"]),
            "max_in_flight_global": contract["max_in_flight_global"],
            "expected_member_order": [
                str(member_id) for member_id in expected_member_order
            ],
            "expected_condition_ids": [
                str(condition.condition_id) for condition in conditions
            ],
        }
    )


def _load_exp5_v3_execution_schedule(
    *,
    output_root: Path,
    schedule_binding: Mapping[str, object],
) -> dict[str, object] | None:
    path = output_root / EXP5_V3_EXECUTION_SCHEDULE_PATH
    if not path.is_file():
        return None
    evidence = json.loads(path.read_text(encoding="utf-8"))
    return _validate_exp5_v3_execution_schedule(
        evidence,
        schedule_binding=schedule_binding,
    )


def _write_exp5_v3_execution_schedule(
    *,
    output_root: Path,
    limiter: _Exp5V3SharedProviderTransport,
) -> dict[str, object]:
    if limiter.new_provider_calls_made == 0:
        prior_evidence = limiter.prior_evidence
        if prior_evidence is None:
            raise ValueError("Exp5 v3 execution schedule has no provider calls")
        return prior_evidence
    evidence = limiter.execution_evidence()
    evidence = _validate_exp5_v3_execution_schedule(
        evidence,
        schedule_binding=limiter._schedule_binding,
    )
    path = output_root / EXP5_V3_EXECUTION_SCHEDULE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    FormalEvidenceStore(output_root)._refresh_evidence_manifest()
    return evidence


def _run_smoke_cli(
    *,
    args: argparse.Namespace,
    output_root: Path,
    transport: object,
    supplied_ai_api_configs: dict | None,
) -> int:
    """独立 smoke CLI；正式矩阵参数不参与 smoke selector。"""

    formal_output_root = _configured_paper_output_boundary()
    resolved_output_root = output_root.resolve(strict=False)
    if (
        resolved_output_root == formal_output_root
        or formal_output_root in resolved_output_root.parents
        or resolved_output_root in formal_output_root.parents
    ):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "failure_kind": "invalid_smoke_output_root",
                    "message": (
                        "smoke output root must be isolated from formal paper output"
                    ),
                    "provider_calls_made": 0,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    try:
        profile = load_paper_smoke_profile(Path(args.smoke_profile))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=(),
            failure_kind="invalid_smoke_profile",
            message=str(exc),
            suite_id="paper_smoke_blocked",
        )
        return 3
    if args.pilot or args.exp1_pilot_profile is not None or any(
        value is not None
        for value in (args.condition_id, args.case_id, args.ai_unit_id)
    ):
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="invalid_smoke_mode",
            message="--smoke-profile cannot be combined with pilot selectors",
        )
        return 3
    if args.replay_only:
        try:
            result = replay_paper_smoke_suite(
                output_root=output_root,
                expected_profile_digest=profile.profile_digest,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_replay_failed",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    experiment_ids = profile.experiment_ids
    if not (args.plan_only or args.smoke_identity_only) and not args.real_transport:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="missing_real_transport",
            message="paper smoke execution requires --real-transport",
        )
        return 1
    planning_profile: Exp1PilotProfile | None = None
    if not args.plan_only and _is_exp1_exp4_only_smoke(profile):
        try:
            _validate_exp1_exp4_v3_config_path(args)
            planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
            _validate_exp1_exp4_v3_execution_config(
                planning_profile.source_provider_config
            )
            if supplied_ai_api_configs is not None:
                _validate_exp1_exp4_v3_execution_config(
                    supplied_ai_api_configs.get(
                        EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _write_smoke_blocked_suite(
                output_root=output_root,
                profile=profile,
                failure_kind="exp1_exp4_v3_config_blocked",
                message=str(exc),
            )
            return 3
    requires_exp5_cohort = (
        "exp5_real_ai_model_endpoint_comparison" in experiment_ids
    )
    if requires_exp5_cohort and args.local_ai_api_config is not None:
        try:
            _inject_exp5_v3_api_key(
                local_config_path=Path(args.local_ai_api_config),
                provider_config_args=tuple(args.provider_config),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _write_smoke_blocked_suite(
                output_root=output_root,
                profile=profile,
                failure_kind="exp5_local_api_config_blocked",
                message=str(exc),
            )
            return 3
    model_endpoint_cohort_preflight, _model_cohort = (
        _model_endpoint_cohort_preflight_for_suite(
            experiment_ids=experiment_ids,
            model_cohort_file=args.model_cohort_file,
            model_entry_map=args.model_entry_map,
            provider_config_args=tuple(args.provider_config),
            require_smoke_evidence=False,
        )
    )
    if requires_exp5_cohort and (
        model_endpoint_cohort_preflight is None
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="incomplete_model_cohort",
            message=str(
                (model_endpoint_cohort_preflight or {}).get(
                    "message", "Experiment 5 cohort preflight is incomplete"
                )
            ),
            blocked_members=_smoke_exp5_blocked_members(
                profile=profile,
                preflight=model_endpoint_cohort_preflight or {},
            ),
        )
        return 3
    try:
        catalog_manifest = _load_default_paper_catalogs()
        lean_3x3_matrix = build_lean_3x3_matrix_plan(
            catalog_manifest=catalog_manifest
        )
        if planning_profile is None:
            planning_profile = load_exp1_pilot_profile(DEFAULT_EXP1_PILOT_PROFILE)
        suite_scale_profile = load_paper_suite_scale_profile(
            DEFAULT_PAPER_SUITE_SCALE_PROFILE
        )
        canonical_plans = build_gate_c_dispatch_plans(
            catalog_manifest=catalog_manifest,
            lean_3x3_matrix=lean_3x3_matrix,
            experiment_ids=experiment_ids,
            baseline_endpoint_binding=_baseline_endpoint_binding(planning_profile),
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            paper_suite_scale_profile=suite_scale_profile,
            output_root=output_root,
        )
        execution_plan = resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=canonical_plans,
            catalog_id=catalog_manifest.catalog_id,
            catalog_version=catalog_manifest.catalog_version,
            catalog_digest=catalog_manifest.catalog_digest,
            output_root=output_root,
        )
    except (OSError, ValueError) as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_plan_blocked",
            message=str(exc),
        )
        return 3
    if args.resume:
        try:
            execution_plan = _restore_smoke_resume_dispatch_views(
                execution_plan=execution_plan,
                catalog_manifest=catalog_manifest,
                output_root=output_root,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_resume_dispatch_blocked",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    expected_ai_units_by_case = {
        item.case_id: estimated_ai_units_for_case(cases_by_id[item.case_id])
        for item in execution_plan.items
    }
    frozen_selections = execution_plan.budget_selection_commitments(
        expected_ai_units_by_case=expected_ai_units_by_case
    )
    endpoint_token_ceilings = build_exp5_v3_token_ceiling_mapping(
        model_endpoint_cohort_preflight
    )
    hard_limits = {
        key: value
        for key, value in (
            ("max_total_provider_attempts", args.max_total_provider_attempts),
            ("max_total_tokens", args.max_total_tokens),
            ("max_cost_estimate", args.max_cost_estimate),
            (
                "stop_after_current_task",
                True if args.stop_after_current_task else None,
            ),
        )
        if value is not None
    }
    try:
        budget = plan_paper_suite(
            catalog_manifest=catalog_manifest,
            conditions=tuple(
                condition
                for plan in execution_plan.dispatch_plans
                for condition in plan.conditions
            ),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=(
                FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            token_upper_bound_by_endpoint_identity_digest=(
                endpoint_token_ceilings
            ),
            cost_upper_bound_per_provider_attempt=(
                FORMAL_COST_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            plan_only=args.plan_only,
            lean_3x3_matrix=lean_3x3_matrix,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            frozen_selections=frozen_selections,
            endpoint_identity={
                "baseline": _baseline_endpoint_binding(planning_profile),
                "model_endpoint_cohort_preflight": model_endpoint_cohort_preflight,
            },
            request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
            hard_limits={} if args.unlimited_budget else hard_limits,
            suite_identity={
                "suite_version": "paper_smoke_v1",
                "execution_scope": "smoke_suite",
                "profile_digest": profile.profile_digest,
                "execution_plan_digest": execution_plan.execution_plan_digest,
                "experiment_ids": list(experiment_ids),
                "paper_suite_scale_profile_source_path": (
                    suite_scale_profile.source_path
                ),
                "paper_suite_scale_profile_digest": (
                    suite_scale_profile.profile_digest
                ),
                "exp5_selection_path": (
                    suite_scale_profile.exp5_source_selection_path
                ),
                "exp5_selection_digest": (
                    suite_scale_profile.exp5_source_selection_digest
                ),
            },
            output_identity={
                "output_root": output_root.resolve(strict=False).as_posix(),
                "formal_output_root_allowed": False,
                **_shared_exp1_reference_output_identity(
                    experiment_ids,
                    baseline_policy=profile.baseline_policy,
                ),
            },
            approve_budget_digest=args.approve_budget_digest,
            budget_approval_required=args.require_budget_approval,
            budget_mode="unlimited" if args.unlimited_budget else None,
        )
    except (PaperBudgetApprovalError, ValueError) as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_budget_blocked",
            message=str(exc),
        )
        return 2
    if args.smoke_identity_only:
        identity = _build_smoke_prelaunch_identity(
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=catalog_manifest,
            budget=budget,
            planning_profile=planning_profile,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        )
        print(json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.resume:
        try:
            budget = _restore_smoke_resume_budget(
                budget=budget,
                output_root=output_root,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failure_kind": "smoke_resume_budget_blocked",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 3
    try:
        _validate_expected_smoke_run_instance_identity(
            args=args,
            execution_plan=execution_plan,
            budget=budget,
        )
    except ValueError as exc:
        _write_smoke_blocked_suite(
            output_root=output_root,
            profile=profile,
            failure_kind="smoke_run_instance_identity_blocked",
            message=str(exc),
        )
        return 3
    if args.plan_only:
        _write_smoke_plan_only(
            output_root=output_root,
            profile=profile,
            execution_plan=execution_plan,
            budget=budget.to_dict(),
            lean_3x3_matrix=lean_3x3_matrix,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        )
        return 0
    try:
        if supplied_ai_api_configs is not None:
            execution_configs = dict(supplied_ai_api_configs)
        else:
            if any(
                experiment_id != "exp5_real_ai_model_endpoint_comparison"
                for experiment_id in experiment_ids
            ):
                _inject_exp1_pilot_api_key(
                    pilot_profile=planning_profile,
                    local_config_path=Path(args.ai_api_config),
                )
            execution_configs = {
                planning_profile.model_endpoint_identity.provider_config_id: (
                    planning_profile.source_provider_config
                )
            }
            execution_configs.update(
                load_provider_config_map(
                    _parse_provider_config_args(tuple(args.provider_config))
                )
            )
        if model_endpoint_cohort_preflight is not None:
            execution_configs.setdefault(APPROVED_ENDPOINT_BINDINGS_KEY, {})
            execution_configs[APPROVED_ENDPOINT_BINDINGS_KEY][
                "exp5_real_ai_model_endpoint_comparison"
            ] = model_endpoint_cohort_preflight
        if _is_exp1_exp4_only_smoke(profile):
            _validate_exp1_exp4_v3_execution_config(
                execution_configs.get(EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID)
            )
        current_launch_manifest = _build_smoke_launch_manifest(
            args=args,
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=catalog_manifest,
            budget=budget,
            planning_profile=planning_profile,
            execution_configs=execution_configs,
        )
        launch_manifest, recovery_manifest = _resolve_smoke_invocation_manifests(
            resume=args.resume,
            output_root=output_root,
            current_manifest=current_launch_manifest,
        )
        exp5_v3_prior_evidence = None
        if isinstance(getattr(profile, "exp5_v3_contract", None), Mapping):
            exp5_v3_schedule_binding = _exp5_v3_execution_schedule_binding(
                profile=profile,
                execution_plan=execution_plan,
            )
            if args.resume:
                exp5_v3_prior_evidence = _load_exp5_v3_execution_schedule(
                    output_root=output_root,
                    schedule_binding=exp5_v3_schedule_binding,
                )
        execution_transport, exp5_v3_limiter = (
            _exp5_v3_smoke_execution_transport(
                profile=profile,
                execution_plan=execution_plan,
                model_endpoint_cohort_preflight=(
                    model_endpoint_cohort_preflight
                ),
                transport=transport,
                ai_api_configs=execution_configs,
                prior_evidence=exp5_v3_prior_evidence,
            )
        )
        exp5_v3_execution_evidence = None
        try:
            result = execute_paper_smoke_suite(
                profile=profile,
                execution_plan=execution_plan,
                catalog_manifest=catalog_manifest,
                budget=budget,
                ai_api_configs=execution_configs,
                transport=execution_transport,
                real_transport=args.real_transport,
                hard_limits=hard_limits,
                resume=args.resume,
                secret_values=_configured_secret_values(execution_configs),
                launch_manifest=launch_manifest,
                recovery_manifest=recovery_manifest,
            )
        finally:
            if exp5_v3_limiter is not None:
                if exp5_v3_limiter.new_provider_calls_made:
                    exp5_v3_execution_evidence = _write_exp5_v3_execution_schedule(
                        output_root=output_root,
                        limiter=exp5_v3_limiter,
                    )
                else:
                    exp5_v3_execution_evidence = exp5_v3_limiter.prior_evidence
        result_completed = result.to_dict()["status"] == PaperStatus.COMPLETED.value
        if exp5_v3_limiter is not None and result_completed and (
            exp5_v3_execution_evidence is None
            or exp5_v3_execution_evidence["sequence_complete"] is not True
        ):
            raise ValueError(
                "Exp5 v3 execution schedule evidence failed closed"
            )
        _write_completed_exp5_smoke_evidence(
            profile=profile,
            output_root=output_root,
            result=result,
            entry_map_path=(
                Path(args.model_entry_map)
                if args.model_entry_map is not None
                else None
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        error_payload = {
            "status": "failed",
            "failure_kind": "smoke_execution_failed",
            "message": str(exc),
        }
        if not (output_root / "suite_manifest.json").exists():
            try:
                _write_smoke_blocked_suite(
                    output_root=output_root,
                    profile=profile,
                    failure_kind="smoke_execution_failed",
                    message=str(exc),
                )
            except (OSError, RuntimeError, ValueError) as persistence_exc:
                error_payload["evidence_persistence_error"] = str(
                    persistence_exc
                )
        print(
            json.dumps(
                error_payload,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return _execution_result_exit_code(result)


def _execution_result_exit_code(result: object) -> int:
    """把持久化 suite 终态传播给 CLI/监督进程。"""

    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        return 3
    status = to_dict().get("status")
    if status in {
        PaperStatus.COMPLETED.value,
        PaperStatus.COMPLETED_WITH_FAILURES.value,
    }:
        return 0
    return 3


def _formal_execution_exit_code(
    execution_result: object,
    report_result: object | None,
) -> int:
    """正式 suite 声称可写论文时，renderer 失败必须传播到监督进程。"""

    execution_exit_code = _execution_result_exit_code(execution_result)
    if execution_exit_code != 0:
        return execution_exit_code
    to_dict = getattr(execution_result, "to_dict", None)
    if not callable(to_dict) or to_dict().get("paper_eligible") is not True:
        return execution_exit_code
    if (
        getattr(report_result, "paper_eligible", None) is not True
        or getattr(report_result, "formal_paper_table_generated", None) is not True
    ):
        return 3
    return execution_exit_code


def _write_smoke_blocked_suite(
    *,
    output_root: Path,
    profile: object,
    failure_kind: str,
    message: str,
    blocked_members: tuple[dict, ...] = (),
) -> None:
    profile_body = profile.to_dict()
    body = {
        "schema_version": "tokenshare.paper_smoke_suite.v1",
        "suite_id": profile.suite_id,
        "status": "blocked",
        "experiment_ids": list(profile.experiment_ids),
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        "provider_attempt_count": 0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "error_summary": [
            {"failure_kind": failure_kind, "message": message}
        ],
        "blocked_members": [dict(member) for member in blocked_members],
    }
    documents = {
        "smoke_profile.json": profile_body,
        "suite_manifest.json": body,
    }
    if blocked_members:
        documents["audit/smoke_endpoint_preflight.json"] = {
            "schema_version": "tokenshare.paper_smoke_endpoint_preflight.v1",
            "suite_id": profile.suite_id,
            "status": "blocked",
            "provider_calls_made": 0,
            "members": [dict(member) for member in blocked_members],
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        }
    _write_smoke_documents_fail_closed(
        output_root=output_root,
        documents=documents,
    )


def _smoke_exp5_blocked_members(*, profile: object, preflight: dict) -> tuple[dict, ...]:
    """把 atomic cohort preflight 的阻塞显式投影到每个 smoke endpoint。"""

    member_plans = preflight.get("member_plans")
    if not isinstance(member_plans, dict):
        member_plans = {}
    member_ids = sorted(
        {
            str(item.condition_selector["cohort_member_id"])
            for item in profile.items
            if item.experiment_id == "exp5_real_ai_model_endpoint_comparison"
            and "cohort_member_id" in item.condition_selector
        }
    )
    records = []
    for member_id in member_ids:
        member_plan = member_plans.get(member_id)
        reasons = (
            member_plan.get("blocked_reasons")
            if isinstance(member_plan, dict)
            else None
        )
        if not isinstance(reasons, list) or not reasons:
            reasons = ["incomplete_model_cohort"]
        records.append(
            {
                "cohort_member_id": member_id,
                "status": "blocked",
                "blocked_reasons": list(dict.fromkeys(str(reason) for reason in reasons)),
                "provider_attempt_count": 0,
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "provider_actual_billing": None,
                "provider_actual_billing_available": False,
                "paper_eligible": False,
            }
        )
    return tuple(records)


def _write_smoke_plan_only(
    *,
    output_root: Path,
    profile: object,
    execution_plan: object,
    budget: dict,
    lean_3x3_matrix: dict,
    model_endpoint_cohort_preflight: dict | None,
) -> None:
    documents = {
        "smoke_profile.json": profile.to_dict(),
        "smoke_execution_plan.json": execution_plan.to_dict(),
        "run_budget.json": budget,
        "lean_3x3_matrix.json": lean_3x3_matrix,
        "suite_manifest.json": {
            "schema_version": "tokenshare.paper_smoke_suite.v1",
            "suite_id": profile.suite_id,
            "status": "planned",
            "experiment_ids": list(profile.experiment_ids),
            "condition_count": len(execution_plan.items),
            "run_count": execution_plan.direct_root_run_count,
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
            "provider_attempt_count": 0,
            "total_tokens": 0,
            "total_cost_estimate": 0.0,
        },
    }
    if model_endpoint_cohort_preflight is not None:
        documents["model_endpoint_cohort_plan.json"] = (
            model_endpoint_cohort_preflight
        )
    _write_smoke_documents_fail_closed(
        output_root=output_root,
        documents=documents,
    )
    print(
        json.dumps(
            documents["suite_manifest.json"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _write_smoke_documents_fail_closed(
    *,
    output_root: Path,
    documents: dict[str, dict],
) -> None:
    """先校验全部已有 identity，再写缺失文件，避免半写入覆盖。"""

    output_root.mkdir(parents=True, exist_ok=True)
    for name, body in documents.items():
        path = output_root / name
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != body:
                raise ValueError(f"smoke output identity mismatch: {name}")
    for name, body in documents.items():
        path = output_root / name
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


@lru_cache(maxsize=1)
def _load_default_paper_catalogs() -> PaperInputCatalogManifest:
    return load_paper_catalogs(
        factorization_path=DEFAULT_FACTOR_CATALOG,
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG
            if DEFAULT_LEAN_LEMMA_GRAPH_CATALOG.exists()
            else None
        ),
    )


def _load_exp1_pilot_paper_catalogs() -> PaperInputCatalogManifest:
    return load_paper_catalogs(
        factorization_path=DEFAULT_EXP1_PILOT_FACTOR_CATALOG,
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG
            if DEFAULT_LEAN_LEMMA_GRAPH_CATALOG.exists()
            else None
        ),
    )


def _inject_exp1_pilot_api_key(
    *,
    pilot_profile: Exp1PilotProfile,
    local_config_path: Path,
) -> None:
    """只把匹配 provider/model 的本地 secret 注入批准 entry 的 env。"""

    selected_entry_id = pilot_profile.model_endpoint_identity.selected_entry_id
    approved_entries = [
        entry
        for entry in pilot_profile.source_provider_config.entries
        if entry.entry_id == selected_entry_id and entry.enabled
    ]
    if len(approved_entries) != 1:
        raise ValueError("approved Exp1 pilot entry is missing or disabled")
    approved_entry = approved_entries[0]
    if os.environ.get(approved_entry.api_key_env):
        return
    if not local_config_path.is_file():
        raise ValueError(
            f"Exp1 pilot local AI API config is missing: {local_config_path}"
        )

    local_config = load_local_ai_api_config(local_config_path)
    candidates = sorted(
        (
            entry
            for entry in local_config.entries
            if entry.enabled
            and local_config.provider_family
            == pilot_profile.model_endpoint_identity.provider_family
            and entry.model
            == pilot_profile.model_endpoint_identity.provider_model_id
            and entry.base_url == approved_entry.base_url
        ),
        key=lambda entry: entry.entry_id,
    )
    if not candidates:
        raise ValueError(
            "Exp1 pilot local config has no enabled key for the approved provider/model"
        )
    os.environ[approved_entry.api_key_env] = candidates[0].resolve_api_key()


def _inject_exp5_v3_api_key(
    *,
    local_config_path: Path,
    provider_config_args: tuple[str, ...],
) -> None:
    """把 gitignored 本地 SiliconFlow secret 注入 v3 共享 env。"""

    provider_configs = load_provider_config_map(
        _parse_provider_config_args(provider_config_args)
    )
    target_config = provider_configs.get("siliconflow")
    if target_config is None:
        raise ValueError("Exp5 v3 SiliconFlow provider config is missing")
    target_entries = tuple(
        entry for entry in target_config.entries if entry.enabled
    )
    target_envs = {entry.api_key_env for entry in target_entries}
    if not target_entries or target_envs != {"SILICONFLOW_API_KEY"}:
        raise ValueError("Exp5 v3 SiliconFlow API key env binding drift")
    if os.environ.get("SILICONFLOW_API_KEY"):
        return
    if not local_config_path.is_file():
        raise ValueError(
            f"Exp5 v3 local AI API config is missing: {local_config_path}"
        )

    local_config = load_local_ai_api_config(local_config_path)
    if local_config.provider_family != target_config.provider_family:
        raise ValueError("Exp5 v3 local provider family does not match")
    target_base_urls = {entry.base_url for entry in target_entries}
    candidate_secrets = {
        secret
        for entry in local_config.entries
        if entry.enabled and entry.base_url in target_base_urls
        for secret in (os.environ.get(entry.api_key_env, ""),)
        if secret
    }
    if len(candidate_secrets) != 1:
        raise ValueError(
            "Exp5 v3 local config must resolve exactly one SiliconFlow secret"
        )
    os.environ["SILICONFLOW_API_KEY"] = next(iter(candidate_secrets))


def _configured_secret_values(configs: dict) -> tuple[str, ...]:
    """向报告 secret scan 提供本次实际配置引用的环境变量值。"""

    values: list[str] = []
    for config in configs.values():
        entries = getattr(config, "entries", ())
        for entry in entries:
            api_key_env = getattr(entry, "api_key_env", None)
            value = os.environ.get(api_key_env) if isinstance(api_key_env, str) else None
            if value:
                values.append(value)
    return tuple(sorted(set(values)))


def _build_smoke_launch_manifest(
    *,
    args: argparse.Namespace,
    profile: object,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    budget: object,
    planning_profile: Exp1PilotProfile,
    execution_configs: dict,
) -> dict:
    """冻结首次 provider 调用前的 smoke 授权与执行身份。"""

    endpoint_identity = planning_profile.model_endpoint_identity
    provider_config_id = endpoint_identity.provider_config_id
    config = execution_configs.get(provider_config_id)
    if config is None or not hasattr(config, "config_digest"):
        raise ValueError("smoke baseline provider config is missing")
    if config.config_digest != planning_profile.source_provider_config.config_digest:
        raise ValueError("smoke baseline provider config digest mismatch")
    entries = [
        entry
        for entry in config.entries
        if entry.enabled
        and entry.entry_id == endpoint_identity.selected_entry_id
    ]
    if len(entries) != 1:
        raise ValueError("smoke baseline provider entry identity mismatch")
    entry = entries[0]
    request_limits = _baseline_endpoint_binding(planning_profile)[
        "request_controls"
    ]
    direct_by_experiment = {
        experiment_id: sum(
            item.experiment_id == experiment_id for item in profile.items
        )
        for experiment_id in profile.experiment_ids
    }
    scope = (
        "exp1_exp4_only_smoke"
        if tuple(profile.experiment_ids) == EXP1_EXP4_ONLY_EXPERIMENT_IDS
        else "paper_smoke"
    )
    selection_inventory = _smoke_selection_inventory(execution_plan)
    identity_seed = {
        "suite_id": profile.suite_id,
        "profile_digest": profile.profile_digest,
        "execution_plan_digest": execution_plan.execution_plan_digest,
        "catalog_digest": catalog_manifest.catalog_digest,
        "budget_digest": budget.budget_digest,
        "output_root": Path(execution_plan.output_root)
        .resolve(strict=False)
        .as_posix(),
    }
    run_identity_digest = digest_json(identity_seed)
    run_identity_suffix = run_identity_digest.removeprefix("sha256:")[:16]
    launch_recorded_at = datetime.now(timezone.utc)
    body = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "recorded_at": launch_recorded_at.isoformat().replace("+00:00", "Z"),
        "recorded_at_local": launch_recorded_at.astimezone().isoformat(),
        "authorization_scope": scope,
        "execution_identity": {
            "suite_id": profile.suite_id,
            "run_id": f"smoke_run_{run_identity_suffix}",
            "generation_id": f"smoke_generation_{run_identity_suffix}",
            "checkpoint_identities_at_launch": [],
            "identity_digest": run_identity_digest,
        },
        "command": {
            "executable": sys.executable,
            "module": "tokenshare.experiments.run_paper_experiments",
            "argv": list(args._command_argv),
        },
        "suite_id": profile.suite_id,
        "experiment_ids": list(profile.experiment_ids),
        "profile_path": Path(args.smoke_profile).as_posix(),
        "profile_digest": profile.profile_digest,
        "execution_plan_digest": execution_plan.execution_plan_digest,
        "catalog": {
            "catalog_id": catalog_manifest.catalog_id,
            "catalog_version": catalog_manifest.catalog_version,
            "catalog_digest": catalog_manifest.catalog_digest,
        },
        "budget": {
            "budget_mode": budget.budget_mode,
            "budget_digest": budget.budget_digest,
            "direct_root_runs": execution_plan.direct_root_run_count,
            "actual_scheduled_root_runs": budget.planned_root_runs,
            "planned_ai_units": budget.planned_ai_units,
            "max_provider_attempts": budget.max_provider_attempts,
        },
        "direct_root_runs_by_experiment": direct_by_experiment,
        "model_endpoint": {
            "provider_config_id": provider_config_id,
            "provider_config_path": Path(args.ai_api_config).as_posix(),
            "source_provider_config_digest": config.config_digest,
            "selected_entry_id": entry.entry_id,
            "provider_family": endpoint_identity.provider_family,
            "provider_model_id": endpoint_identity.provider_model_id,
            "reasoning_profile_id": endpoint_identity.reasoning_profile_id,
            "base_url": entry.base_url,
            "endpoint": entry.endpoint,
            "provider_inflight_limit": config.local_concurrency[
                "max_in_flight_global"
            ],
        },
        "request_limits": dict(request_limits),
        "worker_counts": sorted(
            {
                int(item.condition_selector["worker_count"])
                for item in profile.items
                if "worker_count" in item.condition_selector
            }
        ),
        "repeat_ids": sorted({int(item.repeat_id) for item in profile.items}),
        "output_root": Path(execution_plan.output_root)
        .resolve(strict=False)
        .as_posix(),
        "selection_bundle_digest": digest_json(selection_inventory),
        "selection_inventory": selection_inventory,
        "source_control": _git_source_snapshot(),
        "explicit_exclusions": [
            "exp5",
            "pilot",
            "formal_experiments",
            "full_experiment_matrix",
            "full_lean",
            "lean_audit",
            "force_all_lean_audit",
        ],
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
    }
    return {**body, "launch_manifest_digest": digest_json(body)}


def _smoke_selection_inventory(execution_plan: object) -> list[dict]:
    """返回不含 output root 的跨 run 稳定 smoke selection inventory。"""

    return [
        {
            "item_id": item.item_id,
            "experiment_id": item.experiment_id,
            "condition_id": item.condition_id,
            "condition_digest": item.condition_digest,
            "selection_id": item.selection_id,
            "selection_digest": item.selection_digest,
            "case_id": item.case_id,
            "repeat_id": item.repeat_id,
        }
        for item in execution_plan.items
    ]


def _shared_exp1_reference_output_identity(
    experiment_ids: Sequence[str],
    *,
    baseline_policy: str = "shared_exp1_reference",
) -> dict[str, object]:
    """只允许正式的 Exp1→Exp3 shared-reference evidence 流向。"""

    try:
        validate_experiment_dependency_order(experiment_ids)
    except ValueError:
        dependency_order_valid = False
    else:
        dependency_order_valid = True
    allowed = (
        "exp1_real_ai_feasibility" in experiment_ids
        and "exp3_real_ai_fault_recovery" in experiment_ids
        and dependency_order_valid
        and baseline_policy != "omitted_for_smoke_regression"
    )
    return {
        "cross_experiment_evidence_allowed": allowed,
        "cross_experiment_evidence_policy": {
            "policy_id": "exp1_to_exp3_shared_reference_v1",
            "allowed": allowed,
            "dependency_order_valid": dependency_order_valid,
            "source_experiment_id": (
                "exp1_real_ai_feasibility" if allowed else None
            ),
            "target_experiment_id": (
                "exp3_real_ai_fault_recovery" if allowed else None
            ),
            "purpose": "shared_exp1_reference" if allowed else None,
        },
    }


def _build_smoke_prelaunch_identity(
    *,
    profile: object,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    budget: object,
    planning_profile: Exp1PilotProfile,
    model_endpoint_cohort_preflight: Mapping[str, object] | None = None,
) -> dict:
    """分离跨 run 预注册语义与绑定 output root 的运行实例 identity。"""

    experiment_ids = tuple(profile.experiment_ids)
    includes_exp5 = "exp5_real_ai_model_endpoint_comparison" in experiment_ids
    includes_baseline_experiment = any(
        experiment_id != "exp5_real_ai_model_endpoint_comparison"
        for experiment_id in experiment_ids
    )
    endpoint_semantics: dict[str, object] = {}
    if includes_baseline_experiment:
        config = planning_profile.source_provider_config
        _validate_exp1_exp4_v3_execution_config(config)
        endpoint_identity = planning_profile.model_endpoint_identity
        entries = [
            entry
            for entry in config.entries
            if entry.enabled
            and entry.entry_id == endpoint_identity.selected_entry_id
        ]
        if len(entries) != 1:
            raise ValueError("smoke baseline provider entry identity mismatch")
        entry = entries[0]
        endpoint_semantics.update(
            {
                "provider_config_source_digest": config.config_digest,
                "model_endpoint": {
                    "provider_config_id": endpoint_identity.provider_config_id,
                    "selected_entry_id": entry.entry_id,
                    "provider_family": endpoint_identity.provider_family,
                    "provider_model_id": endpoint_identity.provider_model_id,
                    "reasoning_profile_id": endpoint_identity.reasoning_profile_id,
                    "base_url": entry.base_url,
                    "endpoint": entry.endpoint,
                    "provider_inflight_limit": config.local_concurrency[
                        "max_in_flight_global"
                    ],
                },
                "request_limits": dict(
                    _baseline_endpoint_binding(planning_profile)[
                        "request_controls"
                    ]
                ),
            }
        )
    if includes_exp5:
        preflight = model_endpoint_cohort_preflight
        if not isinstance(preflight, Mapping) or preflight.get("status") != "planned":
            raise ValueError("smoke Exp5 cohort identity is not planned")
        expected_member_ids = preflight.get("expected_member_ids")
        member_plans = preflight.get("member_plans")
        if not isinstance(expected_member_ids, list) or not isinstance(
            member_plans, Mapping
        ):
            raise ValueError("smoke Exp5 cohort identity is incomplete")
        member_endpoints: list[dict[str, object]] = []
        for member_id in expected_member_ids:
            plan = member_plans.get(member_id)
            if not isinstance(member_id, str) or not isinstance(plan, Mapping):
                raise ValueError("smoke Exp5 member identity is incomplete")
            if plan.get("status") != "planned":
                raise ValueError("smoke Exp5 member identity is not planned")
            endpoint_identity = plan.get("endpoint_identity")
            request_controls = plan.get("request_controls")
            if not isinstance(endpoint_identity, Mapping) or not isinstance(
                request_controls, Mapping
            ):
                raise ValueError("smoke Exp5 endpoint identity is incomplete")
            member_endpoints.append(
                {
                    "cohort_member_id": member_id,
                    "provider_config_id": plan.get("provider_config_id"),
                    "selected_entry_id": plan.get("selected_entry_id"),
                    "provider_family": plan.get("provider_family"),
                    "provider_model_id": plan.get("provider_model_id"),
                    "reasoning_profile_id": plan.get("reasoning_profile_id"),
                    "effective_reasoning_controls": endpoint_identity.get(
                        "effective_reasoning_controls"
                    ),
                    "source_provider_config_digest": plan.get(
                        "source_provider_config_digest"
                    ),
                    "model_endpoint_identity_digest": plan.get(
                        "model_endpoint_identity_digest"
                    ),
                    "request_controls": dict(request_controls),
                    "request_controls_digest": plan.get(
                        "request_controls_digest"
                    ),
                }
            )
        endpoint_semantics["model_endpoint_cohort"] = {
            "cohort_id": preflight.get("cohort_id"),
            "model_cohort_digest": preflight.get("model_cohort_digest"),
            "request_controls_snapshot": preflight.get(
                "request_controls_snapshot"
            ),
            "request_controls_snapshot_digest": preflight.get(
                "request_controls_snapshot_digest"
            ),
            "member_endpoints": member_endpoints,
        }
    selection_inventory = _smoke_selection_inventory(execution_plan)
    direct_root_runs = execution_plan.direct_root_run_count
    actual_scheduled_root_runs = budget.planned_root_runs
    supporting_baselines = actual_scheduled_root_runs - direct_root_runs
    if supporting_baselines < 0:
        raise ValueError("smoke supporting baseline count is invalid")
    commitments = budget.quota_preflight.get("budget_commitments", {})
    experiment_budget_identity = (
        commitments.get("experiment_budget_identity", {})
        if isinstance(commitments, Mapping)
        else {}
    )
    supporting_root_map = experiment_budget_identity.get(
        "supporting_baseline_root_runs_by_experiment",
        {},
    )
    supporting_ai_unit_map = experiment_budget_identity.get(
        "supporting_baseline_ai_units_by_experiment",
        {},
    )
    if not isinstance(supporting_root_map, Mapping) or not isinstance(
        supporting_ai_unit_map,
        Mapping,
    ):
        raise ValueError("smoke supporting baseline budget identity is invalid")
    supporting_baseline_root_runs = sum(
        int(value) for value in supporting_root_map.values()
    )
    supporting_baseline_ai_units = sum(
        int(value) for value in supporting_ai_unit_map.values()
    )
    if supporting_baseline_root_runs != supporting_baselines:
        raise ValueError("smoke supporting baseline root identity mismatch")
    baseline_omitted = (
        profile.baseline_policy == "omitted_for_smoke_regression"
    )
    shared_reference_identity = _shared_exp1_reference_output_identity(
        profile.experiment_ids,
        baseline_policy=profile.baseline_policy,
    )
    return {
        "schema_version": "tokenshare.paper_smoke_prelaunch_identity.v1",
        "preregistered_semantics": {
            "suite_id": profile.suite_id,
            "profile_digest": profile.profile_digest,
            "catalog_digest": catalog_manifest.catalog_digest,
            "selection_bundle_digest": digest_json(selection_inventory),
            "experiment_ids": list(profile.experiment_ids),
            "direct_root_runs": direct_root_runs,
            "supporting_worker_death_baselines": supporting_baselines,
            "supporting_baseline_root_runs": supporting_baseline_root_runs,
            "supporting_baseline_ai_units": supporting_baseline_ai_units,
            "supporting_provider_attempt_upper_bound": (
                supporting_baseline_ai_units
                * int(
                    experiment_budget_identity.get(
                        "provider_attempt_multiplier",
                        1,
                    )
                )
            ),
            "actual_scheduled_root_runs": actual_scheduled_root_runs,
            "planned_first_attempt_ai_units": budget.planned_ai_units,
            "budget_provider_attempt_upper_bound": budget.max_provider_attempts,
            "provider_retry_limit": 0,
            "worker_counts": sorted(
                {
                    int(item.condition_selector["worker_count"])
                    for item in profile.items
                    if "worker_count" in item.condition_selector
                }
            ),
            "repeat_ids": sorted({int(item.repeat_id) for item in profile.items}),
            "baseline_policy": profile.baseline_policy,
            "baseline": None if baseline_omitted else "required_by_formal_plan",
            "baseline_comparison_eligible": not baseline_omitted,
            "baseline_unavailable_reason": (
                "smoke_baseline_not_requested" if baseline_omitted else None
            ),
            **shared_reference_identity,
            **endpoint_semantics,
            "paper_eligible": False,
        },
        "run_instance_identity": {
            "output_root": Path(execution_plan.output_root)
            .resolve(strict=False)
            .as_posix(),
            "execution_plan_digest": execution_plan.execution_plan_digest,
            "budget_digest": budget.budget_digest,
        },
        "identity_policy": {
            "cross_run_stable": "preregistered_semantics",
            "per_output_root": [
                "run_instance_identity.output_root",
                "run_instance_identity.execution_plan_digest",
                "run_instance_identity.budget_digest",
            ],
            "same_output_root_identity_drift": "fail_closed",
        },
    }


def _validate_expected_smoke_run_instance_identity(
    *,
    args: argparse.Namespace,
    execution_plan: object,
    budget: object,
) -> None:
    """若 launcher 冻结了本 root identity，则在 secret/provider 前精确复核。"""

    expected_plan = args.expect_smoke_execution_plan_digest
    expected_budget = args.expect_smoke_budget_digest
    if (expected_plan is None) != (expected_budget is None):
        raise ValueError("smoke run-instance digest expectations must be paired")
    if expected_plan is None:
        return
    if execution_plan.execution_plan_digest != expected_plan:
        raise ValueError("same output root smoke execution-plan identity drift")
    if budget.budget_digest != expected_budget:
        raise ValueError("same output root smoke budget identity drift")


def _git_source_snapshot() -> dict:
    """记录 commit 与不含 diff 内容/secret 的 commit-less 工作树摘要。"""

    def run_git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise ValueError(
                f"unable to persist smoke Git identity: git {' '.join(arguments)}"
            )
        return result.stdout.strip()

    commit = run_git("rev-parse", "HEAD")
    status_lines = tuple(
        line for line in run_git("status", "--short").splitlines() if line
    )
    diff_stat = tuple(
        line for line in run_git("diff", "--stat", "HEAD").splitlines() if line
    )
    staged_stat = tuple(
        line for line in run_git("diff", "--cached", "--stat").splitlines() if line
    )
    return {
        "git_commit": commit,
        "commit_less_diff": {
            "dirty": bool(status_lines),
            "status_line_count": len(status_lines),
            "status_digest": digest_json({"status_lines": list(status_lines)}),
            "status_lines": list(status_lines),
            "worktree_diff_stat": list(diff_stat),
            "staged_diff_stat": list(staged_stat),
        },
    }


def _resolve_smoke_invocation_manifests(
    *,
    resume: bool,
    output_root: Path,
    current_manifest: dict,
) -> tuple[dict, dict | None]:
    """恢复时复用初始授权身份，并追加本次恢复调用与 checkpoint 血缘。"""

    _validate_smoke_manifest_digest(current_manifest)
    if not resume:
        return dict(current_manifest), None
    launch_path = output_root / "smoke_launch_manifest.json"
    if not launch_path.is_file():
        raise ValueError("smoke resume requires persisted launch manifest")
    existing = json.loads(launch_path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("persisted smoke launch manifest must be an object")
    _validate_smoke_manifest_digest(existing)
    volatile_fields = {
        "recorded_at",
        "recorded_at_local",
        "command",
        "launch_manifest_digest",
    }
    stable_existing = {
        key: value for key, value in existing.items() if key not in volatile_fields
    }
    stable_current = {
        key: value
        for key, value in current_manifest.items()
        if key not in volatile_fields
    }
    if stable_existing != stable_current:
        raise ValueError("smoke resume authorization identity drift")

    checkpoints = []
    checkpoint_root = output_root / "experiments"
    if checkpoint_root.is_dir():
        for path in sorted(checkpoint_root.rglob("CURRENT.json")):
            pointer = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(pointer, dict):
                raise ValueError("smoke resume checkpoint pointer must be an object")
            generation_id = pointer.get("generation_id")
            generation_digest = pointer.get("generation_manifest_digest")
            if (
                not isinstance(generation_id, str)
                or not generation_id
                or not isinstance(generation_digest, str)
                or not generation_digest.startswith("sha256:")
            ):
                raise ValueError("smoke resume checkpoint identity is invalid")
            checkpoints.append(
                {
                    "checkpoint_path": path.relative_to(output_root).as_posix(),
                    "generation_id": generation_id,
                    "generation_manifest_digest": generation_digest,
                }
            )
    if not checkpoints:
        raise ValueError("smoke resume requires a committed checkpoint")

    source_dir = Path(__file__).resolve().parent
    source_snapshot = {
        name: "sha256:" + sha256((source_dir / name).read_bytes()).hexdigest()
        for name in (
            "lean_paper_adapter.py",
            "paper_formal_runner.py",
            "paper_smoke.py",
            "run_paper_experiments.py",
        )
    }
    recovery_base = {
        "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
        "recorded_at": current_manifest["recorded_at"],
        "recovery_reason": "facility_code_repair",
        "parent_launch_manifest_digest": existing["launch_manifest_digest"],
        "resume_invocation_digest": current_manifest["launch_manifest_digest"],
        "command": current_manifest["command"],
        "output_root": current_manifest["output_root"],
        "profile_digest": current_manifest["profile_digest"],
        "execution_plan_digest": current_manifest["execution_plan_digest"],
        "catalog_digest": current_manifest["catalog"]["catalog_digest"],
        "budget_digest": current_manifest["budget"]["budget_digest"],
        "source_checkpoints": checkpoints,
        "facility_source_snapshot": source_snapshot,
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
    }
    provisional_digest = digest_json(recovery_base)
    recovery_id = "smoke_recovery_" + provisional_digest.removeprefix(
        "sha256:"
    )[:16]
    recovery_body = {**recovery_base, "recovery_id": recovery_id}
    recovery = {
        **recovery_body,
        "recovery_manifest_digest": digest_json(recovery_body),
    }
    return existing, recovery


def _restore_smoke_resume_dispatch_views(
    *,
    execution_plan: object,
    catalog_manifest: PaperInputCatalogManifest,
    output_root: Path,
) -> object:
    """从初始 suite identity 恢复非确定性 readiness execution view。"""

    suite_path = output_root / "suite_manifest.json"
    if not suite_path.is_file():
        raise ValueError("smoke resume requires persisted suite manifest")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    frozen_identity = suite.get("suite_identity")
    frozen_dispatch = (
        frozen_identity.get("dispatch")
        if isinstance(frozen_identity, dict)
        else None
    )
    dispatch_body = (
        frozen_dispatch.get("body")
        if isinstance(frozen_dispatch, dict)
        else None
    )
    stored_plans = (
        dispatch_body.get("plans")
        if isinstance(dispatch_body, dict)
        else None
    )
    if not isinstance(stored_plans, list):
        raise ValueError("smoke resume frozen dispatch plans are missing")
    stored_by_experiment = {
        str(body.get("experiment_id")): body
        for body in stored_plans
        if isinstance(body, dict) and body.get("experiment_id")
    }
    current_plans = tuple(execution_plan.dispatch_plans)
    if set(stored_by_experiment) != {
        plan.experiment_id for plan in current_plans
    }:
        raise ValueError("smoke resume frozen dispatch experiment coverage drift")

    restored_plans = []
    for plan in current_plans:
        stored = stored_by_experiment[plan.experiment_id]
        current = plan.to_dict()
        stored_without_view = {
            key: value
            for key, value in stored.items()
            if key != "catalog_execution_view"
        }
        current_without_view = {
            key: value
            for key, value in current.items()
            if key != "catalog_execution_view"
        }
        if stored_without_view != current_without_view:
            raise ValueError(
                "smoke resume dispatch identity drift outside catalog execution view"
            )
        frozen_view = stored.get("catalog_execution_view")
        if frozen_view is None:
            if current.get("catalog_execution_view") is not None:
                raise ValueError("smoke resume catalog execution view coverage drift")
            restored_plans.append(plan)
            continue
        if not isinstance(frozen_view, dict):
            raise ValueError("smoke resume frozen catalog execution view is invalid")
        restore_catalog_execution_view(
            frozen_view,
            catalog_manifest=catalog_manifest,
        )
        restored_plans.append(
            replace(plan, catalog_execution_view=dict(frozen_view))
        )
    return replace(execution_plan, dispatch_plans=tuple(restored_plans))


def _restore_smoke_resume_budget(
    *,
    budget: PaperBudgetResult,
    output_root: Path,
) -> PaperBudgetResult:
    """恢复初始 budget 中会随 readiness 预检重建的 golden evidence 哈希。"""

    suite_path = output_root / "suite_manifest.json"
    if not suite_path.is_file():
        raise ValueError("smoke resume requires persisted suite manifest")
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    frozen_identity = suite.get("suite_identity")
    frozen_budget = (
        frozen_identity.get("budget")
        if isinstance(frozen_identity, dict)
        else None
    )
    stored_body = (
        frozen_budget.get("body")
        if isinstance(frozen_budget, dict)
        else None
    )
    if not isinstance(stored_body, dict):
        raise ValueError("smoke resume frozen budget body is missing")

    current_body = budget.to_dict()
    stored_comparable = _without_lean_golden_evidence_content_hashes(stored_body)
    current_comparable = _without_lean_golden_evidence_content_hashes(current_body)
    if stored_comparable != current_comparable:
        raise ValueError(
            "smoke resume budget identity drift outside Lean golden evidence hashes"
        )
    stored_quota = stored_body.get("quota_preflight")
    if not isinstance(stored_quota, dict):
        raise ValueError("smoke resume frozen budget quota preflight is invalid")
    restored = replace(
        budget,
        quota_preflight=json.loads(
            json.dumps(stored_quota, ensure_ascii=False, sort_keys=True)
        ),
    )
    if restored.to_dict() != stored_body:
        raise ValueError("smoke resume frozen budget restoration is incomplete")
    return restored


def _without_lean_golden_evidence_content_hashes(body: dict) -> dict:
    comparable = json.loads(json.dumps(body, ensure_ascii=False, sort_keys=True))
    quota = comparable.get("quota_preflight")
    matrix = quota.get("lean_3x3_matrix") if isinstance(quota, dict) else None
    cells = matrix.get("cells") if isinstance(matrix, dict) else None
    if not isinstance(cells, list):
        raise ValueError("smoke resume budget Lean matrix is missing")
    for cell in cells:
        golden_by_case = (
            cell.get("golden_evidence_by_case_id")
            if isinstance(cell, dict)
            else None
        )
        if not isinstance(golden_by_case, dict):
            raise ValueError("smoke resume Lean golden evidence map is invalid")
        for evidence in golden_by_case.values():
            _remove_content_hashes(evidence)
    return comparable


def _remove_content_hashes(value: object) -> None:
    if isinstance(value, dict):
        value.pop("content_hash", None)
        for child in value.values():
            _remove_content_hashes(child)
    elif isinstance(value, list):
        for child in value:
            _remove_content_hashes(child)


def _validate_smoke_manifest_digest(manifest: dict) -> None:
    claimed = manifest.get("launch_manifest_digest")
    body = {
        key: value
        for key, value in manifest.items()
        if key != "launch_manifest_digest"
    }
    if not isinstance(claimed, str) or claimed != digest_json(body):
        raise ValueError("smoke launch manifest digest mismatch")


def _load_cli_pilot_profile(
    *,
    profile_path: str | None,
    output_root: Path,
    experiment_ids: tuple[str, ...],
) -> Exp1PilotProfile | None:
    if profile_path is None:
        return None
    try:
        return load_exp1_pilot_profile(Path(profile_path))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_profile",
            message=str(exc),
            suite_id="paper_exp1_minimal_pilot_invalid_profile",
        )
        return None


def _pilot_cli_profile_error(
    *,
    pilot_profile: Exp1PilotProfile,
    experiment_ids: tuple[str, ...],
    worker_levels: tuple[int, ...],
    optional_worker_levels: tuple[int, ...],
    repeats: int,
    seed_family: tuple[int, ...],
) -> str | None:
    body = pilot_profile.body
    if experiment_ids != (EXP1_PILOT_EXPERIMENT_ID,):
        return "Exp1 pilot profile can only plan Experiment 1"
    if worker_levels != (int(body["worker_count"]),):
        return "CLI worker levels do not match the frozen pilot profile"
    if optional_worker_levels:
        return "Exp1 minimal pilot does not allow optional worker levels"
    if repeats != int(body["repeat_count"]):
        return "CLI repeats do not match the frozen pilot profile"
    if seed_family != tuple(int(seed) for seed in body["seed_family"]):
        return "CLI seed family does not match the frozen pilot profile"
    return None


def _write_blocked_suite(
    *,
    output_root: Path,
    experiment_ids: tuple[str, ...],
    failure_kind: str,
    message: str,
    suite_id: str = "paper_v1_blocked",
    model_endpoint_cohort_preflight: Mapping[str, object] | None = None,
) -> None:
    suite = PaperSuiteResult(
        suite_id=suite_id,
        status=PaperStatus.BLOCKED,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=0,
        run_count=0,
        task_count=0,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=[],
        audit_refs=[],
        error_summary=[{"failure_kind": failure_kind, "message": message}],
        model_endpoint_cohort_preflight=(
            dict(model_endpoint_cohort_preflight)
            if model_endpoint_cohort_preflight is not None
            else None
        ),
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


def _write_suite(output_root: Path, suite: PaperSuiteResult) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "suite_manifest.json").write_text(
        json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_plan_artifacts(
    *,
    output_root: Path,
    budget: dict,
    pilot_profile: dict | None,
    lean_3x3_matrix: dict,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "run_budget.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if pilot_profile is not None:
        (output_root / "exp1_pilot_profile.json").write_text(
            json.dumps(
                pilot_profile,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    (output_root / "lean_3x3_matrix.json").write_text(
        json.dumps(
            lean_3x3_matrix,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _parse_provider_config_args(values: tuple[str, ...]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--provider-config must use provider=path")
        provider_config_id, path = value.split("=", 1)
        provider_config_id = provider_config_id.strip()
        path = path.strip()
        if not provider_config_id or not path:
            raise ValueError("--provider-config must use provider=path")
        result[provider_config_id] = Path(path)
    return result


def _model_endpoint_cohort_preflight_for_suite(
    *,
    experiment_ids: tuple[str, ...],
    model_cohort_file: str | None,
    model_entry_map: str | None,
    provider_config_args: tuple[str, ...],
    require_smoke_evidence: bool = True,
    smoke_evidence_bundle: str | None = None,
) -> tuple[dict | None, dict | None]:
    if "exp5_real_ai_model_endpoint_comparison" not in experiment_ids:
        return None, None
    if model_cohort_file is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-cohort-file is required for Experiment 5",
            ),
            None,
        )
    try:
        cohort = load_model_endpoint_cohort(Path(model_cohort_file))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return _blocked_model_endpoint_cohort_preflight(message=str(exc)), None
    if model_entry_map is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-entry-map is required for Experiment 5",
                cohort=cohort,
            ),
            cohort,
        )
    try:
        entry_map = load_model_entry_map(Path(model_entry_map))
        provider_configs = load_provider_config_map(
            _parse_provider_config_args(provider_config_args)
        )
        smoke_bundle = (
            load_exp5_smoke_evidence_bundle(Path(smoke_evidence_bundle))
            if require_smoke_evidence and smoke_evidence_bundle is not None
            else None
        )
        preflight = build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=provider_configs,
            require_smoke_evidence=require_smoke_evidence,
            smoke_evidence_bundle=smoke_bundle,
        )
        return preflight, cohort
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        reasons = (
            [exc.reason]
            if isinstance(exc, Exp5SmokeEvidenceError)
            else ["incomplete_model_cohort"]
        )
        return (
            _blocked_model_endpoint_cohort_preflight(
                message=str(exc),
                cohort=cohort,
                ineligibility_reasons=reasons,
            ),
            cohort,
        )


def _blocked_model_endpoint_cohort_preflight(
    *,
    message: str,
    cohort: dict | None = None,
    ineligibility_reasons: Sequence[str] = ("incomplete_model_cohort",),
) -> dict:
    members = cohort.get("members") if isinstance(cohort, dict) else None
    cohort_member_ids = (
        [
            str(member["cohort_member_id"])
            for member in members
            if isinstance(member, Mapping)
            and isinstance(member.get("cohort_member_id"), str)
            and member["cohort_member_id"]
        ]
        if isinstance(members, Sequence)
        and not isinstance(members, (str, bytes))
        else []
    )
    expected_member_ids = (
        cohort_member_ids
        if cohort_member_ids
        else list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    )
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "blocked",
        "paper_eligible_possible": False,
        "blocked_reason": "incomplete_model_cohort",
        "ineligibility_reasons": list(dict.fromkeys(ineligibility_reasons)),
        "message": message,
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": cohort.get("cohort_id") if isinstance(cohort, dict) else None,
        "model_cohort_digest": (
            cohort.get("model_cohort_digest") if isinstance(cohort, dict) else None
        ),
        "expected_member_ids": expected_member_ids,
        "missing_members": [],
        "missing_provider_configs": [],
        "missing_entry_ids": [],
        "ineligible_members": [],
        "member_plans": {},
    }


def _write_completed_exp5_smoke_evidence(
    *,
    profile: object,
    output_root: Path,
    result: object,
    entry_map_path: Path | None,
) -> dict | None:
    """仅为成功的独立 Exp5 bootstrap smoke 派生 formal 门禁证据。"""

    if getattr(profile, "suite_id", None) != EXP5_SMOKE_SUITE_ID:
        return None
    to_dict = getattr(result, "to_dict", None)
    terminal_statuses = {
        PaperStatus.COMPLETED.value,
        PaperStatus.COMPLETED_WITH_FAILURES.value,
    }
    if not callable(to_dict) or to_dict().get("status") not in terminal_statuses:
        return None
    if entry_map_path is None:
        raise ValueError("completed Exp5 smoke requires --model-entry-map")
    bundle = write_exp5_smoke_evidence_bundle(
        source_suite_root=output_root,
        entry_map=load_model_entry_map(entry_map_path),
        output_path=output_root / "audit" / "exp5_endpoint_smoke_evidence.json",
    )
    FormalEvidenceStore(output_root)._refresh_evidence_manifest()
    return bundle

if __name__ == "__main__":
    raise SystemExit(main())
