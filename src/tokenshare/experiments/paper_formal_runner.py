"""正式论文实验套件的最小通用调度层。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import pickle
from pathlib import Path
import shutil
import tempfile
from threading import Lock
from typing import Any
import uuid

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.experiments.paper_catalog import estimated_ai_units_for_case
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_catalog_execution_view import (
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_budget import (
    _condition_replacement_policy,
    _paper_disk_estimate,
)
from tokenshare.experiments.paper_dispatcher import (
    PaperExperimentDispatchPlan,
    dispatch_paper_case,
    dispatch_paper_condition,
)
from tokenshare.experiments.paper_experiment_contracts import PaperExecutionContext
from tokenshare.experiments.paper_formal_evidence import (
    FormalEvidenceStore,
    SharedEvidenceError,
    _is_protocol_ledger_event,
)
from tokenshare.experiments.paper_formal_callbacks import (
    exp5_identity_fail_stop_required,
    finalize_exp5_identity_evidence,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_faults import PaperFaultRuntimeHooks
from tokenshare.experiments.paper_exp2_scalability import EXP2_EXPERIMENT_ID
from tokenshare.experiments.paper_exp3_metrics import (
    Exp3PersistedObservation,
    Exp3OnlineRecoveryInput,
    Exp3TraceConditionInput,
)
from tokenshare.experiments.paper_exp1_metrics import (
    Exp1ActualProviderAttemptFacts,
    Exp1HydratedDirectRow,
)
from tokenshare.experiments.paper_exp2_metrics import (
    Exp2AIUnitFacts,
    Exp2OnlineFirstAttemptFacts,
    Exp2OnlineHydratedRoot,
    Exp2TraceConsumptionFacts,
    Exp2TraceHydratedRoot,
)
from tokenshare.experiments.paper_exp4_metrics import (
    Exp4DirectRootFacts,
    Exp4ModeInput,
    Exp4PersistedObservation,
)
from tokenshare.experiments.paper_exp5_metrics import (
    Exp5FirstProviderAttemptFacts,
    Exp5ModelRepeatFacts,
    Exp5PlannedAIUnitFacts,
    Exp5PreregisteredRootFacts,
)
from tokenshare.experiments.paper_model_policy import (
    EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
    EXP5_DOMAIN_EXECUTION_CONTRACTS,
    exp5_provider_specific_reasoning_controls,
)
from tokenshare.experiments.paper_models import (
    ExternalBankObjectLocator,
    PaperEvidenceEligibilityFacts,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    PaperSuiteResult,
    PaperDirectRootInventoryRow,
    PreregisteredRootInventoryManifest,
    digest_json,
    evaluate_versioned_paper_evidence,
)
from tokenshare.experiments.paper_direct_results import (
    CanonicalDirectRootEvidence,
    _DIRECT_RESULT_FACTORY_TOKEN,
    build_canonical_direct_evidence,
    project_paper_direct_results,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_ai_unit_binding_from_request,
)
from tokenshare.experiments.paper_runner import validate_experiment_dependency_order
from tokenshare.experiments.paper_terminal_outcomes import (
    PaperEvidenceIntegrity,
    PaperOutcomeStatus,
    PaperTerminalOutcome,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger
from tokenshare.local_runtime.projection import project_protocol_run
from tokenshare.local_runtime import (
    ExperimentAblationGateAppliedPayloadV1,
    NoOpRuntimeHooks,
    ParsedCandidateContext,
    ParsedCandidateDirective,
    RawOutputContext,
    RuntimeHookObservationKind,
    RuntimeHookObservationV1,
    WorkerTerminationPolicy,
)
from tokenshare.local_runtime.contracts import PreparedTraceDelivery
from tokenshare.experiments.paper_response_bank import (
    FormalTraceInventoryPreflightResult,
    PaperFormalTraceContext,
    preflight_formal_trace_inventory,
)
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_VERSION as FACTORIZATION_PLUGIN_VERSION,
    RANGE_RESULT_SCHEMA_VERSION,
)
from tokenshare.plugins.lean_proof.schemas import (
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    PLUGIN_VERSION as LEAN_PLUGIN_VERSION,
    PROOF_CANDIDATE_PARSER_ID,
)


EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
APPROVED_ENDPOINT_BINDINGS_KEY = "__approved_endpoint_bindings__"
_FORMAL_FINALIZATION_PENDING = "PENDING.json"
_FORMAL_FINALIZATION_SCHEMA = "tokenshare.paper_experiment_finalization_pending.v1"
_FORMAL_SUITE_FINALIZATION_SCHEMA = "tokenshare.paper_suite_finalization_pending.v1"


class PaperInfrastructureBlockedError(Exception):
    """基础设施/身份依赖失败；必须停止新的 provider dispatch。"""

    def __init__(
        self,
        message: str,
        *,
        evidence_integrity: PaperEvidenceIntegrity,
        failure_stage: str,
        failure_kind: str,
        condition_id: str | None = None,
        task_id: str | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.terminal_outcome = PaperTerminalOutcome(
            outcome_status=PaperOutcomeStatus.BLOCKED_DEPENDENCY,
            evidence_integrity=evidence_integrity,
            failure_stage=failure_stage,
            failure_kind=failure_kind,
        )
        self.condition_id = condition_id
        self.task_id = task_id
        self.diagnostics = dict(diagnostics) if diagnostics is not None else None

    def to_summary(self) -> dict[str, Any]:
        body: dict[str, Any] = self.terminal_outcome.to_dict()
        if self.condition_id is not None:
            body["condition_id"] = self.condition_id
        if self.task_id is not None:
            body["task_id"] = self.task_id
        if self.diagnostics is not None:
            body["resource_diagnostics"] = dict(self.diagnostics)
        return body


@dataclass
class _RollingDiskForecast:
    """用 O(1) 状态保存尚未消费的 suite 空间 forecast。"""

    remaining_root_runs: int
    remaining_ai_units: int
    remaining_provider_attempts: int
    fixed_forecast_bytes: int
    max_condition_compaction_bytes: int
    policy: Mapping[str, Any]
    in_flight_forecast_bytes: int = 0
    lock: Lock = field(default_factory=Lock, compare=False, repr=False)

    def _remaining_forecast_bytes(self) -> int:
        return (
            self.remaining_root_runs * int(self.policy["p95_root_bytes"])
            + self.remaining_ai_units * int(self.policy["p95_ai_unit_bytes"])
            + self.remaining_provider_attempts
            * int(self.policy["p95_attempt_envelope_bytes"])
            + self.remaining_provider_attempts
            * int(self.policy["p95_provider_attempt_payload_bytes"])
            + self.remaining_provider_attempts
            * int(self.policy["p95_model_execution_record_bytes"])
            * int(self.policy["model_execution_record_duplicate_multiplier"])
            + self.fixed_forecast_bytes
            + self.in_flight_forecast_bytes
        )

    def root_guard_details(
        self,
        *,
        output_root: str | Path,
        condition_id: str,
        task_id: str,
        ai_unit_count: int,
        provider_attempt_count: int,
        max_tokens: int,
    ) -> dict[str, Any]:
        root_forecast_bytes = (
            int(self.policy["p95_root_bytes"])
            + ai_unit_count * int(self.policy["p95_ai_unit_bytes"])
            + provider_attempt_count
            * int(self.policy["p95_attempt_envelope_bytes"])
            + provider_attempt_count
            * int(self.policy["p95_provider_attempt_payload_bytes"])
            + provider_attempt_count
            * int(self.policy["p95_model_execution_record_bytes"])
            * int(self.policy["model_execution_record_duplicate_multiplier"])
        )
        root_forecast_payload_bytes = provider_attempt_count * int(
            self.policy["p95_provider_attempt_payload_bytes"]
        )
        theoretical_response_bytes = (
            provider_attempt_count
            * max_tokens
            * int(self.policy["utf8_bytes_per_token"])
        )
        theoretical_over_forecast_bytes = max(
            0,
            theoretical_response_bytes - root_forecast_payload_bytes,
        )
        with self.lock:
            remaining_forecast_bytes = self._remaining_forecast_bytes()
            headroom_bytes = max(
                (remaining_forecast_bytes + 3) // 4,
                2 * 1024**3,
            )
            required_bytes = (
                remaining_forecast_bytes
                + theoretical_over_forecast_bytes
                + headroom_bytes
                + self.max_condition_compaction_bytes
            )
            root = Path(output_root).resolve(strict=False)
            volume_path = _nearest_existing_disk_path(root)
            available_bytes = int(shutil.disk_usage(volume_path).free)
            reservation_applied = available_bytes >= required_bytes
            root_reservation_bytes = (
                root_forecast_bytes + theoretical_over_forecast_bytes
            )
            if reservation_applied:
                self.remaining_root_runs = max(0, self.remaining_root_runs - 1)
                self.remaining_ai_units = max(
                    0, self.remaining_ai_units - ai_unit_count
                )
                self.remaining_provider_attempts = max(
                    0,
                    self.remaining_provider_attempts - provider_attempt_count,
                )
                self.in_flight_forecast_bytes += root_reservation_bytes
        return {
            "volume_path": volume_path.as_posix(),
            "condition_id": condition_id,
            "task_id": task_id,
            "ai_unit_count": ai_unit_count,
            "provider_attempt_count": provider_attempt_count,
            "max_tokens": max_tokens,
            "remaining_forecast_bytes": remaining_forecast_bytes,
            "root_forecast_bytes": root_forecast_bytes,
            "root_reservation_bytes": root_reservation_bytes,
            "root_forecast_payload_bytes": root_forecast_payload_bytes,
            "theoretical_response_bytes": theoretical_response_bytes,
            "theoretical_over_forecast_bytes": theoretical_over_forecast_bytes,
            "headroom_bytes": headroom_bytes,
            "condition_compaction_bytes": self.max_condition_compaction_bytes,
            "required_bytes": required_bytes,
            "available_bytes": available_bytes,
            "reservation_applied": reservation_applied,
        }

    def consume_root_forecast(
        self,
        *,
        root_reservation_bytes: int,
    ) -> None:
        with self.lock:
            self.in_flight_forecast_bytes = max(
                0,
                self.in_flight_forecast_bytes - root_reservation_bytes,
            )


def _compose_official_runtime_hooks(
    online_hook: Any | None,
    existing_hook: Any | None,
) -> Any | None:
    if online_hook is None:
        return existing_hook
    if existing_hook is None:
        return online_hook
    return _CompositeOfficialRuntimeHooks(online_hook, existing_hook)


class _CompositeOfficialRuntimeHooks:
    """先保存 online official evidence，再让既有 runtime hook 决定 mutation。"""

    def __init__(self, online_hook: Any, existing_hook: Any) -> None:
        self._online_hook = online_hook
        self._existing_hook = existing_hook

    def __call__(self, **context: Any) -> Any:
        return self._delegate("__call__", **context)

    def after_prepared_dispatch(self, **context: Any) -> Any:
        return self._delegate("after_prepared_dispatch", **context)

    def before_provider_dispatch(self, **context: Any) -> Any:
        return self._delegate("before_provider_dispatch", **context)

    def after_pre_transport_abort(self, **context: Any) -> Any:
        return self._delegate("after_pre_transport_abort", **context)

    def after_provider_failure_persisted(self, **context: Any) -> Any:
        return self._delegate("after_provider_failure_persisted", **context)

    def after_raw_output_persisted(self, context: Any) -> Any:
        return self._delegate("after_raw_output_persisted", context)

    def after_parsed_candidate_persisted(self, context: Any) -> Any:
        return self._delegate("after_parsed_candidate_persisted", context)

    def before_parser(self, context: Any) -> Any:
        return self._delegate("before_parser", context)

    def before_verification(self, context: Any) -> Any:
        return self._delegate("before_verification", context)

    def before_requeue(self, context: Any) -> Any:
        return self._delegate("before_requeue", context)

    def before_merge(self, context: Any) -> Any:
        return self._delegate("before_merge", context)

    def on_unit_progress(self, context: Any) -> Any:
        return self._delegate("on_unit_progress", context)

    def _delegate(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        values: list[Any] = []
        for hook in (self._online_hook, self._existing_hook):
            method = hook if method_name == "__call__" else getattr(hook, method_name, None)
            values.append(
                method(*args, **kwargs) if callable(method) else None
            )
        non_null = tuple(value for value in values if value is not None)
        if len(non_null) > 1 and non_null[0] != non_null[1]:
            raise ValueError(f"conflicting composite runtime hook result: {method_name}")
        return non_null[-1] if non_null else None


class _Exp3RuntimeHookBridge(NoOpRuntimeHooks):
    """同一 Exp3 hook 同时跨接 executor raw 点与 coordinator parsed 点。"""

    def __init__(
        self,
        *,
        condition: Any,
        case_id: str,
        fault_type: str,
        selected_unit_ids: Sequence[str],
        reserve_unit_ids: Sequence[str],
        runtime_records: list[dict[str, Any]],
    ) -> None:
        self._condition = condition
        self._case_id = case_id
        self._fault_type = fault_type
        self._selected_unit_ids = tuple(selected_unit_ids)
        self._reserve_unit_ids = tuple(reserve_unit_ids)
        self._runtime_records = runtime_records
        self._runtime_hook: PaperFaultRuntimeHooks | None = None
        self._lock = Lock()

    def __call__(self, **context: Any) -> Mapping[str, Any] | None:
        with self._lock:
            return self._after_raw_output(**context)

    def _after_raw_output(self, **context: Any) -> Mapping[str, Any] | None:
        request = context["request"]
        artifact_store = context["artifact_store"]
        runtime_hook = self._ensure_runtime_hook(artifact_store)
        submitted_at = str(context["submitted_at"])
        lease_deadline_at = request.soft_hints.get("lease_deadline_at")
        if self._fault_type in {"no_return", "late_submission"} and (
            not isinstance(lease_deadline_at, str)
            or not lease_deadline_at
        ):
            raise ValueError(f"{self._fault_type} requires protocol lease deadline")
        usage = dict(context["usage_summary"])
        before_count = len(runtime_hook.records)
        directive = runtime_hook.after_raw_output_persisted(
            RawOutputContext(
                run_id=f"{self._condition.condition_id}_{self._case_id}",
                task_id=request.task_id,
                unit_id=request.unit_id,
                attempt_id=request.attempt_id,
                worker_id=str(
                    request.allocation_decision.get(
                        "worker_id",
                        request.allocation_decision.get(
                            "client_id",
                            "formal-worker",
                        ),
                    )
                ),
                raw_output_ref=context["raw_output_ref"],
                provenance_ref=context["provenance_ref"],
                usage_ref=context["usage_ref"],
                content_text=str(context["content_text"]),
                provider=str(context["provider_family"]),
                model=str(context["model"]),
                entry_id=str(context["entry_id"]),
                usage_summary={
                    **usage,
                    "prompt_tokens": usage.get(
                        "prompt_tokens",
                        usage.get("input_tokens", 0),
                    ),
                    "completion_tokens": usage.get(
                        "completion_tokens",
                        usage.get("output_tokens", 0),
                    ),
                },
                submitted_at=submitted_at,
                experiment_unit_id=self._experiment_unit_id(request),
                lease_deadline_at=lease_deadline_at,
            )
        )
        self._capture_new_records(runtime_hook, before_count)
        if directive is None:
            return None
        return {
            key: value
            for key, value in {
                "content_text": directive.content_text,
                "result_kind": directive.result_kind,
            }.items()
            if value is not None
        }

    def after_parsed_candidate_persisted(
        self,
        context: ParsedCandidateContext,
    ) -> ParsedCandidateDirective | None:
        with self._lock:
            experiment_unit_id = (
                f"{self._case_id}:{context.experiment_unit_id}"
                if context.experiment_unit_id
                else None
            )
            target_id = experiment_unit_id or context.unit_id
            if target_id not in {
                *self._selected_unit_ids,
                *self._reserve_unit_ids,
            }:
                return None
            if self._runtime_hook is None:
                raise ValueError(
                    "Exp3 parsed-candidate hook requires prior raw observation"
                )
            before_count = len(self._runtime_hook.records)
            directive = self._runtime_hook.after_parsed_candidate_persisted(
                replace(
                    context,
                    experiment_unit_id=experiment_unit_id,
                )
            )
            self._capture_new_records(self._runtime_hook, before_count)
            return directive

    def _ensure_runtime_hook(
        self,
        artifact_store: ArtifactStore,
    ) -> PaperFaultRuntimeHooks:
        if not isinstance(artifact_store, ArtifactStore):
            raise ValueError("Exp3 post-raw hook requires ArtifactStore")
        if self._runtime_hook is None:
            self._runtime_hook = PaperFaultRuntimeHooks(
                artifact_store=artifact_store,
                condition_id=self._condition.condition_id,
                repeat_id=self._condition.repeat_id,
                fault_type=self._fault_type,
                seed=int(self._condition.seed),
                selected_unit_ids=self._selected_unit_ids,
                reserve_unit_ids=self._reserve_unit_ids,
            )
        return self._runtime_hook

    def _experiment_unit_id(self, request: Any) -> str | None:
        planned_ai_unit_id = request.soft_hints.get("planned_ai_unit_id")
        return (
            f"{self._case_id}:{planned_ai_unit_id}"
            if isinstance(planned_ai_unit_id, str) and planned_ai_unit_id
            else None
        )

    def _capture_new_records(
        self,
        runtime_hook: PaperFaultRuntimeHooks,
        before_count: int,
    ) -> None:
        self._runtime_records.extend(
            dict(record) for record in runtime_hook.records[before_count:]
        )


def replay_paper_formal_suite(*, output_root: str | Path) -> PaperSuiteResult:
    """只从 frozen identity 与 canonical checkpoint evidence 独立重放。"""

    suite_root = Path(output_root)
    persisted, _recomputed, _comparison = _verified_replay_summary(suite_root)
    return persisted


def write_paper_formal_replay_report(
    *,
    output_root: str | Path,
) -> dict[str, Any]:
    """执行一次只读 replay，并持久化其输入引用与重放结果。"""

    suite_root = Path(output_root)
    source_refs = {
        "formal_runner_result": _suite_file_ref(
            suite_root,
            "formal_runner_result.json",
        ),
        "evidence_manifest": {
            "path": "evidence_manifest.json",
            "validation_status": "verified_before_replay",
        },
    }
    replayed_result, recomputed, comparison = _verified_replay_summary(suite_root)
    replayed = replayed_result.to_dict()
    replayed_digest = _sha256_bytes(
        json.dumps(
            replayed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    report = {
        "schema_version": "tokenshare.paper_replay_report.v1",
        "status": "replay_verified",
        "replay_mode": "stored_evidence_only",
        "provider_calls_made": 0,
        "cell_traceability_replay": "separate_l4_entrypoint",
        "source_refs": source_refs,
        "persisted_result_ref": dict(source_refs["formal_runner_result"]),
        "independently_recomputed_summary": recomputed,
        "independently_recomputed_summary_digest": _digest_replay_summary(
            recomputed
        ),
        "comparison": comparison,
        "replayed_result": replayed,
        "replayed_result_digest": replayed_digest,
    }
    relative_path = "audit/replay_report.json"
    path = suite_root / relative_path
    _write_json(path, report)
    report_ref = {
        "path": relative_path,
        "content_hash": _sha256_bytes(path.read_bytes()),
    }
    # replay report 是 suite evidence，但 report 不嵌入 evidence_manifest hash，
    # 因此可在写后刷新索引而不形成自引用 digest 循环。
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    return report_ref


def recompute_paper_traceability_replay(
    *,
    output_root: str | Path,
    replay_input_root: Any,
    registry: Any = None,
    contract: Any = None,
) -> Any:
    """Task24 L4 入口；与 suite summary replay 分离且不触发 dispatch。"""

    from tokenshare.experiments.paper_traceability import (
        recompute_paper_traceability,
    )

    result = recompute_paper_traceability(
        output_root=output_root,
        replay_input_root=replay_input_root,
        registry=registry,
        contract=contract,
    )
    if result.provider_calls != 0 or result.source_write_count != 0:
        raise RuntimeError("traceability replay violated read-only boundary")
    return result


def persist_paper_traceability_replay_input_root(
    *,
    replay_input_root: str | Path,
    canonical_direct_rows: Mapping[str, object],
    global_infrastructure_valid: bool = True,
    canonical_runtime_evidence: Sequence[Any] = (),
    requested_lineage_root_ids: Sequence[str] | None = None,
    current_trace_wrappers_by_root: Mapping[str, Sequence[Any]] | None = None,
    trace_source_bindings_by_root: Mapping[str, Sequence[Any]] | None = None,
    eligibility_facts_by_root: Mapping[str, Any] | None = None,
    source_resolvers: Mapping[str, Any] | None = None,
    current_provider_object_files: Mapping[str, str | Path] | None = None,
    current_evidence_root: str | Path | None = None,
) -> Any:
    """由 runner 冻结完整 L4 replay input closure。"""

    from tokenshare.experiments.paper_traceability import (
        _persist_protected_replay_input_root,
    )

    return _persist_protected_replay_input_root(
        replay_input_root=replay_input_root,
        canonical_direct_rows=canonical_direct_rows,
        global_infrastructure_valid=global_infrastructure_valid,
        canonical_runtime_evidence=canonical_runtime_evidence,
        requested_lineage_root_ids=requested_lineage_root_ids,
        current_trace_wrappers_by_root=current_trace_wrappers_by_root,
        trace_source_bindings_by_root=trace_source_bindings_by_root,
        eligibility_facts_by_root=eligibility_facts_by_root,
        source_resolvers=source_resolvers,
        current_provider_object_files=current_provider_object_files,
        current_evidence_root=current_evidence_root,
    )


def _paper_trace_object_role(role: str) -> str:
    return {
        "raw_output": "raw_output_or_provider_failure",
        "provider_failure": "raw_output_or_provider_failure",
        "usage": "usage_status",
    }.get(role, role)


@dataclass(frozen=True)
class _PaperTraceSourceResolver:
    """把 response-bank 原生 role 适配到 paper locator ABI。"""

    resolver: Any

    @property
    def root_path(self) -> Path:
        return self.resolver.root_path

    @property
    def index(self) -> Any:
        return self.resolver.index

    def entry(self, entry_id: str) -> Any:
        return self.resolver.entry(entry_id)

    def read_verified(self, locator: ExternalBankObjectLocator) -> bytes:
        entry = self.resolver.entry(locator.entry_id)
        matches = tuple(
            value
            for value in entry.object_locators
            if value.object_digest == locator.object_digest
            and _paper_trace_object_role(value.object_role) == locator.object_role
        )
        if len(matches) != 1:
            raise ValueError("paper trace source locator is ambiguous")
        return self.resolver.read_verified(matches[0])


@dataclass(frozen=True, kw_only=True)
class _RunnerExp3TraceMetricInput(Exp3TraceConditionInput):
    """保留 projector typed ABI，同时让 direct rows 对 lineage 可达。"""

    direct_results: tuple[Any, ...]


@dataclass(frozen=True, kw_only=True)
class _RunnerExp3OnlineMetricInput(Exp3OnlineRecoveryInput):
    direct_results: tuple[Any, ...]


@dataclass(frozen=True, kw_only=True)
class _RunnerExp2TraceMetricInput(Exp2TraceHydratedRoot):
    """Authoritative direct id 在持久化层保持 frozen；metric view 另行生成。"""

    def __post_init__(self) -> None:
        direct = self.direct_result
        if (
            direct.experiment_id == "exp2_real_ai_scalability"
            and direct.evidence_class == "real_model_trace_protocol_run"
        ):
            validated = Exp2TraceHydratedRoot(
                direct_result=replace(
                    direct,
                    experiment_id="experiment_2_trace",
                    _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
                ),
                persisted_logical_makespan_ms=self.persisted_logical_makespan_ms,
                trace_consumptions=self.trace_consumptions,
                ai_units=self.ai_units,
                in_flight_at_witness=self.in_flight_at_witness,
                observed_peak_concurrency=self.observed_peak_concurrency,
            )
            del validated
            return
        Exp2TraceHydratedRoot.__post_init__(self)


@dataclass(frozen=True, kw_only=True)
class _RunnerExp2OnlineMetricInput(Exp2OnlineHydratedRoot):
    def __post_init__(self) -> None:
        direct = self.direct_result
        if (
            direct.experiment_id == "exp2_real_ai_scalability"
            and direct.evidence_class == "online_real_provider"
        ):
            validated = Exp2OnlineHydratedRoot(
                direct_result=replace(
                    direct,
                    experiment_id="experiment_2_online",
                    _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
                ),
                protocol_first_dispatch_at_ms=self.protocol_first_dispatch_at_ms,
                root_terminal_at_ms=self.root_terminal_at_ms,
                first_provider_attempts=self.first_provider_attempts,
            )
            del validated
            return
        Exp2OnlineHydratedRoot.__post_init__(self)


@dataclass
class _CanonicalDirectCollector:
    """保存由 adapter 原生 ledger/store 验证过的 direct evidence。"""

    inventory: PreregisteredRootInventoryManifest
    condition_manifests: tuple[Mapping[str, Any], ...]
    catalog_manifests: tuple[Mapping[str, Any], ...]
    rows_by_key: Mapping[tuple[str, str], PaperDirectRootInventoryRow]
    evidence: list[CanonicalDirectRootEvidence] = field(default_factory=list)
    current_provider_object_files: dict[str, Path] = field(default_factory=dict)
    current_trace_wrappers_by_root: dict[str, tuple[CurrentTraceWrapper, ...]] = field(
        default_factory=dict
    )
    trace_source_bindings_by_root: dict[str, tuple[Any, ...]] = field(
        default_factory=dict
    )
    eligibility_facts_by_root: dict[str, PaperEvidenceEligibilityFacts] = field(
        default_factory=dict
    )
    source_resolvers: dict[str, Any] = field(default_factory=dict)
    producer_facts_by_root: dict[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    lock: Lock = field(default_factory=Lock, compare=False, repr=False)


def load_paper_traceability_replay_input_root(
    output_root: str | Path,
) -> Any:
    """加载 runner factory 生成的 handle，并用官方 loader 完整验证 closure。"""

    from tokenshare.experiments.paper_traceability import (
        ProtectedReplayInputRoot,
        _load_protected_replay_inputs,
    )

    suite_root = Path(output_root)
    manifest = _required_json_object(
        suite_root / "suite_manifest.json",
        label="suite manifest",
    )
    ref = manifest.get("traceability_replay_input_root_ref")
    if not isinstance(ref, Mapping):
        raise ValueError("suite manifest has no traceability replay input root")
    handle_path = suite_root / str(ref.get("handle_path", ""))
    if not handle_path.is_file():
        raise ValueError("traceability replay input handle is missing")
    content = handle_path.read_bytes()
    if _sha256_bytes(content) != ref.get("handle_digest"):
        raise ValueError("traceability replay input handle digest mismatch")
    value = pickle.loads(content)
    if not isinstance(value, ProtectedReplayInputRoot):
        raise ValueError("traceability replay input handle type mismatch")
    if value.descriptor_digest != ref.get("descriptor_digest"):
        raise ValueError("traceability replay input descriptor digest mismatch")
    _load_protected_replay_inputs(value)
    return value


def load_paper_traceability_replay_inputs(output_root: str | Path) -> Any:
    """返回已由官方 protected-root loader 验证的 typed replay inputs。"""

    from tokenshare.experiments.paper_traceability import (
        _load_protected_replay_inputs,
    )

    return _load_protected_replay_inputs(
        load_paper_traceability_replay_input_root(output_root)
    )


def recompute_paper_formal_metrics_from_runner_inputs(
    output_root: str | Path,
) -> Any:
    """从 runner 的 protected closure 重算，并只发布 derived metric files。"""

    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
        recompute_paper_formal_metrics,
    )

    publication_root = Path(output_root)
    loaded = load_paper_traceability_replay_inputs(publication_root)
    with tempfile.TemporaryDirectory(
        prefix="tokenshare-formal-metrics-",
        dir=publication_root.parent,
    ) as staging_directory:
        staging_root = Path(staging_directory)
        resolved_staging_root = staging_root.resolve(strict=False)
        for relative_name, content in loaded.current_evidence_files:
            relative = Path(relative_name)
            target = (staging_root / relative).resolve(strict=False)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or (
                    target != resolved_staging_root
                    and resolved_staging_root not in target.parents
                )
            ):
                raise ValueError("runner metric evidence path escapes staging root")
            _atomic_write_bytes(target, content)
        current = loaded.current
        source = loaded.source
        metrics = recompute_paper_formal_metrics(
            staging_root,
            loaded.direct,
            global_infrastructure_valid=bool(
                current["global_infrastructure_valid"]
            ),
            canonical_runtime_evidence=current["canonical_runtime_evidence"],
            requested_lineage_root_ids=current["requested_lineage_root_ids"],
            current_trace_wrappers_by_root=current[
                "current_trace_wrappers_by_root"
            ],
            trace_source_bindings_by_root=source[
                "trace_source_bindings_by_root"
            ],
            eligibility_facts_by_root=current["eligibility_facts_by_root"],
            metric_projection_rows=derive_paper_metric_projection_rows(
                loaded.direct
            ),
        )
        for ref in metrics.output_refs:
            relative = Path(str(ref.get("path", "")))
            source_path = (staging_root / relative).resolve(strict=False)
            target = (publication_root / relative).resolve(strict=False)
            resolved_publication_root = publication_root.resolve(strict=False)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not source_path.is_file()
                or (
                    target != resolved_publication_root
                    and resolved_publication_root not in target.parents
                )
            ):
                raise ValueError("runner metric output path is invalid")
            _atomic_write_bytes(target, source_path.read_bytes())
        return metrics


def _suite_file_ref(suite_root: Path, relative_path: str) -> dict[str, Any]:
    path = suite_root / relative_path
    if not path.is_file():
        raise ValueError(f"replay source file is missing: {relative_path}")
    return {
        "path": relative_path,
        "content_hash": _sha256_bytes(path.read_bytes()),
    }


def _direct_evidence_class(
    *,
    real_transport: bool,
    transport: Any,
    trace_context: PaperFormalTraceContext | None,
) -> str:
    if _is_offline_capturing_transport(transport):
        return "regression_only"
    if trace_context is not None:
        return "real_model_trace_protocol_run"
    if real_transport:
        return "online_real_provider"
    return "regression_only"


def _build_canonical_direct_collector(
    *,
    bound_plans: Sequence[
        tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]
    ],
    catalog_manifest: Any,
    normalized_root_filter: Mapping[str, tuple[str, ...]],
    evidence_class: str,
) -> _CanonicalDirectCollector:
    cases = _catalog_cases_by_id(catalog_manifest)
    inventory_id = "paper-formal-direct:" + digest_json(
        [
            {
                "experiment_id": plan.experiment_id,
                "conditions": [condition.condition_id for condition, _ in items],
            }
            for plan, items in bound_plans
        ]
    )
    condition_records: list[dict[str, Any]] = []
    condition_refs: dict[str, dict[str, Any]] = {}
    case_records: dict[str, dict[str, Any]] = {}
    rows: list[PaperDirectRootInventoryRow] = []
    row_inputs: list[tuple[Any, str, dict[str, Any], dict[str, Any]]] = []
    for plan, items in bound_plans:
        for condition, selection in items:
            fault_type = str(condition.fault_type)
            axes = {
                "domain": str(condition.domain),
                "difficulty": str(condition.paper_difficulty or condition.difficulty),
                "topic_family": condition.topic_family,
                "worker_count": int(condition.worker_count),
                "sample_slot_index": int(condition.repeat_id),
                "fault_condition": (
                    fault_type
                    if fault_type in {
                        "false_positive",
                        "false_negative",
                        "no_return",
                        "late_submission",
                        "executor_error",
                    }
                    else None
                ),
                "death_condition": (
                    "worker_death" if fault_type == "worker_death" else None
                ),
                "ablation_mode": (
                    "FULL"
                    if str(condition.ablation_mode) in {"FULL", "full_protocol"}
                    else str(condition.ablation_mode)
                    if str(condition.ablation_mode).startswith("NO_")
                    else None
                ),
                "model_endpoint_id": (
                    condition.model_entry_id or condition.provider_model_id
                ),
            }
            record_body = {
                "schema_version": "tokenshare.preregistered_condition_record.v1",
                "condition_id": condition.condition_id,
                "condition_axes": axes,
                "condition_axes_digest": digest_json(axes),
            }
            record = {
                **record_body,
                "condition_record_digest": digest_json(record_body),
            }
            condition_records.append(record)
            selected_case_ids = normalized_root_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            for case_id in selected_case_ids:
                case = cases.get(case_id)
                if not isinstance(case, Mapping):
                    raise ValueError("direct inventory case is absent from catalog")
                quantile = case.get("factor_position_quantile", "not_applicable")
                stratum = case.get("position_stratum") or str(quantile)
                case_axes = {
                    "factor_position_quantile": quantile,
                    "position_stratum": stratum,
                }
                case_body = {
                    "schema_version": "tokenshare.preregistered_case_record.v1",
                    "case_id": case_id,
                    "domain": str(condition.domain),
                    "difficulty": str(
                        condition.paper_difficulty or condition.difficulty
                    ),
                    "case_axes_digest": digest_json(case_axes),
                    **case_axes,
                }
                case_record = {
                    **case_body,
                    "case_record_digest": digest_json(case_body),
                }
                prior = case_records.setdefault(case_id, case_record)
                if prior != case_record:
                    raise ValueError("direct inventory case axes are inconsistent")
                row_inputs.append((condition, case_id, axes, case_record))
    condition_manifest_body = {
        "schema_version": "tokenshare.preregistered_condition_manifest.v1",
        "records": condition_records,
    }
    condition_manifest = {
        **condition_manifest_body,
        "condition_manifest_digest": digest_json(condition_manifest_body),
    }
    for record in condition_records:
        condition_refs[str(record["condition_id"])] = {
            "schema_version": "tokenshare.preregistered_condition_ref.v1",
            "condition_manifest_digest": condition_manifest[
                "condition_manifest_digest"
            ],
            "condition_record_digest": record["condition_record_digest"],
            "condition_axes_digest": record["condition_axes_digest"],
        }
    catalog_body = {
        "schema_version": "tokenshare.preregistered_case_catalog_manifest.v1",
        "records": list(case_records.values()),
    }
    canonical_catalog = {
        **catalog_body,
        "catalog_digest": digest_json(catalog_body),
    }
    for condition, case_id, axes, case_record in row_inputs:
        root_body = {
            "experiment_id": condition.experiment_id,
            "condition_id": condition.condition_id,
            "case_id": case_id,
            "repeat_id": int(condition.repeat_id),
        }
        root_id = "paper-direct-root:" + digest_json(root_body)
        values = {
            "inventory_id": inventory_id,
            "preregistered_root_run_id": root_id,
            **root_body,
            "preregistered_condition_ref": condition_refs[
                condition.condition_id
            ],
            "condition_axes": axes,
            "preregistered_case_ref": {
                "schema_version": "tokenshare.preregistered_case_ref.v1",
                "catalog_digest": canonical_catalog["catalog_digest"],
                "case_record_digest": case_record["case_record_digest"],
                "case_axes_digest": case_record["case_axes_digest"],
                "factor_position_quantile": case_record[
                    "factor_position_quantile"
                ],
                "position_stratum": case_record["position_stratum"],
            },
            "evidence_class": evidence_class,
        }
        rows.append(
            PaperDirectRootInventoryRow(
                **values,
                inventory_row_digest=digest_json(
                    {
                        "schema_version": (
                            "tokenshare.paper_direct_root_inventory_row.v2"
                        ),
                        **values,
                    }
                ),
            )
        )
    inventory_body = {
        "schema_version": "tokenshare.preregistered_root_inventory_manifest.v1",
        "inventory_id": inventory_id,
        "root_count": len(rows),
        "rows": [row.to_dict() for row in rows],
    }
    inventory = PreregisteredRootInventoryManifest(
        inventory_id=inventory_id,
        rows=tuple(rows),
        root_count=len(rows),
        inventory_digest=digest_json(inventory_body),
    )
    return _CanonicalDirectCollector(
        inventory=inventory,
        condition_manifests=(condition_manifest,),
        catalog_manifests=(canonical_catalog,),
        rows_by_key={(row.condition_id, row.case_id): row for row in rows},
    )


def execute_paper_formal_suite(
    *,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    output_root: str | Path,
    ai_api_configs: Mapping[str, Any],
    transport: Any,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    resume: bool = False,
    replay_only: bool = False,
    root_case_filter: Mapping[str, Sequence[str]] | None = None,
    execution_classification: Mapping[str, Any] | None = None,
    suite_id: str = "paper_formal_suite",
    pre_execution_documents: Mapping[str, Any] | None = None,
    recovery_documents: Mapping[str, Any] | None = None,
    trace_context: PaperFormalTraceContext | None = None,
    online_root_callback_factory: Callable[..., Any] | None = None,
    enforce_publication_closure: bool = False,
) -> PaperSuiteResult:
    """校验冻结计划并通过注册 dispatcher 顺序执行 planned conditions。"""

    plans = tuple(dispatch_plans)
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if replay_only:
        return replay_paper_formal_suite(output_root=output_root)
    normalized_root_filter = _normalize_root_case_filter(
        plans=plans,
        root_case_filter=root_case_filter,
    )
    classification = _normalize_execution_classification(
        execution_classification
    )
    frozen_pre_execution_documents = _normalize_pre_execution_documents(
        pre_execution_documents
    )
    frozen_recovery_documents = _normalize_recovery_documents(
        recovery_documents
    )
    if frozen_recovery_documents and not resume:
        raise ValueError("recovery documents require resume mode")
    bound_plans = _validate_suite_inputs(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        budget_approval=budget_approval,
        output_root=output_root,
        ai_api_configs=ai_api_configs,
        hard_limits=hard_limits,
        resume=resume,
        replay_only=replay_only,
        root_case_filter=normalized_root_filter,
    )
    if trace_context is not None:
        if real_transport:
            raise ValueError("formal trace runtime cannot use current real transport")
        trace_preflight = preflight_formal_trace_inventory(
            required_inventory_entry_ids=tuple(
                row.inventory_entry_id for row in trace_context.inventory_plan.rows
            ),
            available_inventory_entry_ids=(
                trace_context.available_inventory_entry_ids
            ),
        )
        if trace_preflight.status == "blocked":
            _persist_trace_preflight_block(
                output_root=output_root,
                result=trace_preflight,
            )
            ended_at = _utc_now()
            return PaperSuiteResult(
                suite_id=suite_id,
                status=PaperStatus.BLOCKED,
                output_root=Path(output_root).as_posix(),
                started_at=ended_at,
                ended_at=ended_at,
                experiment_ids=tuple(plan.experiment_id for plan in plans),
                condition_count=sum(len(items) for _plan, items in bound_plans),
                run_count=0,
                task_count=0,
                provider_attempt_count=0,
                total_tokens=0,
                total_cost_estimate=0.0,
                cost_estimate_by_currency=None,
                total_cost_estimate_status="not_applicable",
                paper_eligible=False,
                eligibility_report_ref=None,
                budget_ref={"preflight_status": "blocked"},
                metrics_refs=(),
                audit_refs=(),
                error_summary=("response_bank_incomplete",),
            )
    disk_preflight = _preflight_formal_disk_capacity(
        output_root=output_root,
        budget=budget,
        resume=resume,
    )
    suite_root = Path(output_root)
    started_at = _utc_now()
    bodies = _evidence_bodies(
        plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        hard_limits=hard_limits,
        ai_api_configs=ai_api_configs,
        root_case_filter=normalized_root_filter,
        execution_classification=classification,
        suite_id=suite_id,
        pre_execution_documents=frozen_pre_execution_documents,
    )
    if resume:
        evidence_store = FormalEvidenceStore(suite_root)
        _repair_interrupted_formal_finalization(suite_root)
        loaded = FormalEvidenceStore.load(
            output_root=suite_root,
            **{f"expected_{name}": body for name, body in bodies.items()},
        )
        _cleanup_checkpointed_adapter_trees(
            suite_root=suite_root,
            bound_plans=bound_plans,
            normalized_root_filter=normalized_root_filter,
            terminal_task_keys=set(loaded.completed_task_keys),
        )
        _persist_pre_execution_documents(
            suite_root=suite_root,
            documents=frozen_recovery_documents,
        )
        evidence_store._refresh_evidence_manifest()
        completed_task_keys = set(loaded.completed_task_keys)
        all_selected_roots_terminal = _all_selected_roots_completed(
            bound_plans,
            completed_task_keys,
            root_case_filter=normalized_root_filter,
        )
        usage = _usage_from_evidence(suite_root)
        if (
            all_selected_roots_terminal or _hard_limit_reached(usage, hard_limits)
        ) and _formal_suite_closure_complete(
            suite_root=suite_root,
            plans=plans,
        ):
            return _suite_result_from_evidence(suite_root)
    else:
        evidence_store = FormalEvidenceStore.initialize(
            output_root=suite_root,
            **bodies,
            capturing=(not real_transport or _is_offline_capturing_transport(transport)),
        )
        completed_task_keys = set()
        usage = _UsageTotals()
    _persist_pre_execution_documents(
        suite_root=suite_root,
        documents=frozen_pre_execution_documents,
    )
    evidence_store._refresh_evidence_manifest()
    rolling_disk_forecast = _rolling_disk_forecast_from_plan(
        budget=budget,
        bound_plans=bound_plans,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        normalized_root_filter=normalized_root_filter,
        completed_task_keys=completed_task_keys,
        max_condition_compaction_bytes=int(
            disk_preflight["condition_compaction_bytes"]
        ),
    )
    direct_evidence_class = _direct_evidence_class(
        real_transport=real_transport,
        transport=transport,
        trace_context=trace_context,
    )
    direct_collector = (
        _build_canonical_direct_collector(
            bound_plans=bound_plans,
            catalog_manifest=catalog_manifest,
            normalized_root_filter=normalized_root_filter,
            evidence_class=direct_evidence_class,
        )
        if enforce_publication_closure
        and (real_transport or trace_context is not None)
        and classification is None
        and direct_evidence_class != "regression_only"
        and any(bound_items for _plan, bound_items in bound_plans)
        else None
    )
    if resume and direct_collector is not None:
        _restore_canonical_direct_checkpoints(
            suite_root=suite_root,
            collector=direct_collector,
        )
    results: list[PaperConditionResult] = []
    try:
        _dispatch_formal_conditions(
            suite_root=suite_root,
            bound_plans=bound_plans,
            catalog_manifest=catalog_manifest,
            ai_api_configs=ai_api_configs,
            transport=transport,
            real_transport=real_transport,
            hard_limits=hard_limits,
            evidence_store=evidence_store,
            completed_task_keys=completed_task_keys,
            usage=usage,
            budget=budget,
            rolling_disk_forecast=rolling_disk_forecast,
            normalized_root_filter=normalized_root_filter,
            classification=classification,
            results=results,
            trace_context=trace_context,
            direct_collector=direct_collector,
            online_root_callback_factory=online_root_callback_factory,
        )
    except Exception as error:
        blocked_error = (
            error
            if isinstance(error, PaperInfrastructureBlockedError)
            else PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="runner_internal",
                failure_kind=type(error).__name__,
                diagnostics={"message": str(error)},
            )
        )
        return _close_blocked_formal_suite(
            suite_root=suite_root,
            suite_id=suite_id,
            started_at=started_at,
            plans=plans,
            bound_plans=bound_plans,
            condition_results=results,
            blocked_error=blocked_error,
            evidence_store=evidence_store,
            root_case_filter=normalized_root_filter,
            usage=usage,
            budget=budget,
            budget_approval=budget_approval,
            execution_classification=classification,
            completed_task_keys=completed_task_keys,
        )

    suite_result = PaperSuiteResult(
        suite_id=suite_id,
        status=_classified_suite_status(
            _suite_status(plans=plans, results=results),
            classification=classification,
        ),
        output_root=suite_root.as_posix(),
        started_at=started_at,
        ended_at=_utc_now(),
        experiment_ids=tuple(plan.experiment_id for plan in plans),
        condition_count=sum(len(items) for _plan, items in bound_plans),
        run_count=sum(result.repeat_count for result in results),
        task_count=sum(result.task_count for result in results),
        provider_attempt_count=max(
            usage.provider_attempt_count,
            sum(result.provider_attempt_count for result in results),
        ),
        total_tokens=usage.total_tokens,
        total_cost_estimate=usage.reportable_total_cost_estimate(),
        cost_estimate_by_currency=(
            dict(sorted(usage.cost_estimate_by_currency.items()))
            if usage.cost_estimate_by_currency
            else None
        ),
        total_cost_estimate_status=usage.cost_estimate_status(),
        paper_eligible=_suite_paper_eligible(
            plans=plans,
            results=results,
            transport=transport,
            real_transport=real_transport,
            execution_classification=classification,
            trace_evidence=trace_context is not None,
        ),
        eligibility_report_ref=None,
        budget_ref={
            "budget_digest": budget.budget_digest,
            "approval_mode": budget_approval["approval_mode"],
        },
        metrics_refs=(),
        audit_refs=(),
        error_summary=(),
    )
    if direct_collector is not None:
        _finalize_canonical_direct_closure(
            suite_root=suite_root,
            collector=direct_collector,
        )
    _finalize_formal_manifests(
        suite_root=suite_root,
        suite_result=suite_result,
        plans=plans,
        condition_results=results,
        execution_classification=classification,
    )
    # runner 结果同样属于可 replay 的 suite evidence，写入后刷新索引。
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    return suite_result


def _persist_trace_preflight_block(
    *,
    output_root: str | Path,
    result: FormalTraceInventoryPreflightResult,
) -> Path:
    """在 FormalEvidenceStore/协议 ledger 外持久化 trace completeness 阻断。"""

    if result.status != "blocked" or not result.blocked_records:
        raise ValueError("trace preflight block marker requires a blocked result")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "paper_preflight_blocked.v1.json"
    body = {
        "schema_version": "tokenshare.paper_preflight_blocked.v1",
        "status": "blocked",
        "protocol_engine_event_count": result.protocol_engine_event_count,
        "provider_call_count": result.provider_call_count,
        "blocked_records": [asdict(item) for item in result.blocked_records],
    }
    marker.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return marker


def _dispatch_formal_conditions(
    *,
    suite_root: Path,
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    catalog_manifest: Any,
    ai_api_configs: Mapping[str, Any],
    transport: Any,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    evidence_store: FormalEvidenceStore,
    completed_task_keys: set[tuple[str, str, str, str]],
    usage: "_UsageTotals",
    budget: PaperBudgetResult,
    rolling_disk_forecast: _RollingDiskForecast,
    normalized_root_filter: Mapping[str, tuple[str, ...]],
    classification: Mapping[str, Any] | None,
    results: list[PaperConditionResult],
    trace_context: PaperFormalTraceContext | None,
    direct_collector: _CanonicalDirectCollector | None,
    online_root_callback_factory: Callable[..., Any] | None,
) -> None:
    for plan, bound_items in bound_plans:
        if plan.status == "blocked":
            _finalize_formal_experiment(
                suite_root=suite_root,
                plan=plan,
                condition_results=(),
                execution_classification=classification,
            )
            continue
        plan_root = Path(plan.output_root)
        plan_root.mkdir(parents=True, exist_ok=True)
        plan_results: list[PaperConditionResult] = []
        for condition, _selection in bound_items:
            endpoint_binding, request_limits, config = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            context_catalog = _execution_catalog_for_plan(
                plan=plan,
                catalog_manifest=catalog_manifest,
            )
            callback = _FormalConditionExecutionCallback(
                catalog_manifest=catalog_manifest,
                config=config,
                transport=transport,
                real_transport=real_transport,
                output_root=plan_root,
                request_limits=request_limits,
                evidence_store=evidence_store,
                completed_task_keys=completed_task_keys,
                usage=usage,
                budget=budget,
                rolling_disk_forecast=rolling_disk_forecast,
                hard_limits=hard_limits,
                root_case_ids=normalized_root_filter.get(
                    condition.condition_id
                ),
                execution_classification=classification,
                direct_collector=direct_collector,
                trace_context=trace_context,
                online_root_callback_factory=online_root_callback_factory,
            )
            context = PaperExecutionContext(
                context_id=(
                    f"paper_formal_{plan.experiment_id}_{condition.condition_id}"
                ),
                catalog=context_catalog,
                approved_endpoint_binding=endpoint_binding,
                request_limits=request_limits,
                hard_limits=dict(hard_limits),
                output_root=plan_root.as_posix(),
                artifact_store=object(),
                event_store=object(),
                execution_callback=callback,
            )
            result = dispatch_paper_condition(
                context=context,
                plan=plan,
                condition_id=condition.condition_id,
            )
            if not isinstance(result, PaperConditionResult):
                raise ValueError("dispatcher must return PaperConditionResult")
            if result.condition_id != condition.condition_id:
                raise ValueError("dispatcher returned a mismatched condition_id")
            results.append(result)
            plan_results.append(result)
        _finalize_formal_experiment(
            suite_root=suite_root,
            plan=plan,
            condition_results=plan_results,
            execution_classification=classification,
        )


def _verified_adapter_case_root(
    *,
    plan_root: Path,
    condition_id: str,
    task_id: str,
    adapter_root: Path,
) -> Path:
    """证明清理目标是 plan 下唯一、精确的 adapter case 目录。"""

    for field_name, value in (
        ("condition_id", condition_id),
        ("task_id", task_id),
    ):
        if (
            not isinstance(value, str)
            or not value
            or not value[0].isalnum()
            or any(
                not (character.isalnum() or character in "._-")
                for character in value
            )
        ):
            raise ValueError(f"{field_name} must be a path-safe identifier")
    resolved_plan_root = Path(plan_root).resolve(strict=False)
    runs_root = (resolved_plan_root / "runs").resolve(strict=False)
    condition_root = (runs_root / condition_id).resolve(strict=False)
    expected = (condition_root / task_id).resolve(strict=False)
    resolved_target = Path(adapter_root).resolve(strict=False)
    if (
        resolved_target != expected
        or resolved_target in {resolved_plan_root, runs_root, condition_root}
        or resolved_target.parent != condition_root
    ):
        raise ValueError("cleanup target is not the exact adapter case root")
    return resolved_target


def _remove_checkpointed_adapter_tree(
    *,
    suite_root: Path,
    plan_root: Path,
    condition: Any,
    task_id: str,
    adapter_root: Path,
    commit_token: Mapping[str, Any] | None = None,
    terminal_task_keys: set[tuple[str, str, str, str]] | None = None,
) -> bool:
    """仅按同调用 commit token 或 full-load terminal key 回收 working tree。"""

    target = _verified_adapter_case_root(
        plan_root=plan_root,
        condition_id=condition.condition_id,
        task_id=task_id,
        adapter_root=adapter_root,
    )
    run_root = (
        Path(suite_root)
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    task_key = _formal_task_key(condition, task_id)
    if commit_token is not None:
        expected_commit = {
            "schema_version": "tokenshare.paper_checkpoint_commit.v1",
            "experiment_id": condition.experiment_id,
            "condition_id": condition.condition_id,
            "repeat_id": str(condition.repeat_id),
            "task_id": task_id,
            "run_root": run_root.relative_to(Path(suite_root)).as_posix(),
        }
        if any(
            commit_token.get(key) != value
            for key, value in expected_commit.items()
        ):
            raise ValueError("checkpoint commit token identity mismatch")
        for field_name in ("generation_id", "generation_manifest_digest"):
            if not isinstance(commit_token.get(field_name), str) or not commit_token.get(
                field_name
            ):
                raise ValueError("checkpoint commit token is incomplete")
        current = _required_json_object(
            run_root / "CURRENT.json",
            "checkpoint CURRENT",
        )
        if (
            current.get("schema_version")
            != "tokenshare.paper_checkpoint_current.v1"
            or current.get("generation_id")
            != commit_token["generation_id"]
            or current.get("generation_manifest_digest")
            != commit_token["generation_manifest_digest"]
        ):
            raise ValueError("checkpoint commit token does not match CURRENT")
    elif terminal_task_keys is None or task_key not in terminal_task_keys:
        return False
    if not target.exists():
        return False
    if not target.is_dir():
        raise ValueError("adapter case cleanup target is not a directory")
    shutil.rmtree(target)
    return True


def _cleanup_checkpointed_adapter_trees(
    *,
    suite_root: Path,
    bound_plans: Sequence[
        tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]
    ],
    normalized_root_filter: Mapping[str, tuple[str, ...]],
    terminal_task_keys: set[tuple[str, str, str, str]],
) -> int:
    """resume 前仅删除已有 canonical task 对应的 exact adapter case。"""

    removed = 0
    for plan, bound_items in bound_plans:
        plan_root = Path(plan.output_root)
        for condition, selection in bound_items:
            task_ids = normalized_root_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            for task_id in task_ids:
                removed += int(
                    _remove_checkpointed_adapter_tree(
                        suite_root=suite_root,
                        plan_root=plan_root,
                        condition=condition,
                        task_id=task_id,
                        adapter_root=(
                            plan_root
                            / "runs"
                            / condition.condition_id
                            / task_id
                        ),
                        terminal_task_keys=terminal_task_keys,
                    )
                )
    return removed


def _close_disk_resource_blocked_suite(
    *,
    suite_root: Path,
    suite_id: str,
    started_at: str,
    plans: Sequence[PaperExperimentDispatchPlan],
    bound_plans: Sequence[
        tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]
    ],
    condition_results: Sequence[PaperConditionResult],
    blocked_error: PaperInfrastructureBlockedError,
    root_case_filter: Mapping[str, tuple[str, ...]],
    usage: "_UsageTotals",
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    completed_task_keys: set[tuple[str, str, str, str]],
) -> PaperSuiteResult:
    """磁盘不足时仅写一个常量大小 marker，禁止逐 root closure。"""

    selection_hasher = hashlib.sha256()
    remaining_hasher = hashlib.sha256()
    selected_task_count = 0
    remaining_task_count = 0
    for _plan, items in bound_plans:
        for condition, selection in items:
            case_ids = root_case_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            for case_id in case_ids:
                task_key = _formal_task_key(condition, case_id)
                encoded = _serialized_json_text(list(task_key)).encode("utf-8")
                selection_hasher.update(encoded)
                selection_hasher.update(b"\n")
                selected_task_count += 1
                if task_key not in completed_task_keys:
                    remaining_hasher.update(encoded)
                    remaining_hasher.update(b"\n")
                    remaining_task_count += 1
    last_committed_cursor = (
        list(max(completed_task_keys)) if completed_task_keys else None
    )
    marker = {
        "schema_version": "tokenshare.paper_infrastructure_blocked.v1",
        "status": "infrastructure_blocked",
        "created_at": _utc_now(),
        "failure": blocked_error.to_summary(),
        "budget_digest": budget.budget_digest,
        "selection_digest": f"sha256:{selection_hasher.hexdigest()}",
        "selected_task_count": selected_task_count,
        "remaining_task_count": remaining_task_count,
        "remaining_task_identity_digest": (
            f"sha256:{remaining_hasher.hexdigest()}"
        ),
        "last_committed_cursor": last_committed_cursor,
        "derivation": "frozen_selection_minus_validated_terminal_task_identities",
    }
    closure_error: dict[str, Any] | None = None
    try:
        _atomic_write_json(suite_root / "infrastructure_blocked.json", marker)
        FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    except (OSError, TypeError, ValueError) as error:
        closure_error = {
            "outcome_status": "blocked_dependency",
            "evidence_integrity": "invalid",
            "failure_stage": "infrastructure_blocked_marker",
            "failure_kind": type(error).__name__,
            "message": str(error),
        }
    error_summary = (
        blocked_error.to_summary(),
        *((closure_error,) if closure_error is not None else ()),
    )
    return PaperSuiteResult(
        suite_id=suite_id,
        status=(
            PaperStatus.INCOMPLETE
            if closure_error is not None
            else PaperStatus.BLOCKED
        ),
        output_root=suite_root.as_posix(),
        started_at=started_at,
        ended_at=_utc_now(),
        experiment_ids=tuple(plan.experiment_id for plan in plans),
        condition_count=sum(len(items) for _plan, items in bound_plans),
        run_count=len(condition_results),
        task_count=selected_task_count,
        provider_attempt_count=usage.provider_attempt_count,
        total_tokens=usage.total_tokens,
        total_cost_estimate=usage.reportable_total_cost_estimate(),
        cost_estimate_by_currency=(
            dict(sorted(usage.cost_estimate_by_currency.items()))
            if usage.cost_estimate_by_currency
            else None
        ),
        total_cost_estimate_status=usage.cost_estimate_status(),
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref={
            "budget_digest": budget.budget_digest,
            "approval_mode": budget_approval["approval_mode"],
        },
        metrics_refs=(),
        audit_refs=(),
        error_summary=error_summary,
    )


def _close_blocked_formal_suite(
    *,
    suite_root: Path,
    suite_id: str,
    started_at: str,
    plans: Sequence[PaperExperimentDispatchPlan],
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    condition_results: Sequence[PaperConditionResult],
    blocked_error: PaperInfrastructureBlockedError,
    evidence_store: FormalEvidenceStore,
    root_case_filter: Mapping[str, tuple[str, ...]],
    usage: "_UsageTotals",
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    execution_classification: Mapping[str, Any] | None,
    completed_task_keys: set[tuple[str, str, str, str]],
) -> PaperSuiteResult:
    summary = blocked_error.to_summary()
    if blocked_error.terminal_outcome.failure_kind in {
        "insufficient_disk_capacity",
        "insufficient_rolling_root_capacity",
        "insufficient_condition_compaction_capacity",
    }:
        return _close_disk_resource_blocked_suite(
            suite_root=suite_root,
            suite_id=suite_id,
            started_at=started_at,
            plans=plans,
            bound_plans=bound_plans,
            condition_results=condition_results,
            blocked_error=blocked_error,
            root_case_filter=root_case_filter,
            usage=usage,
            budget=budget,
            budget_approval=budget_approval,
            completed_task_keys=completed_task_keys,
        )
    persistence_failures: list[dict[str, Any]] = []
    failure_checkpointed = False
    selected_task_count = 0
    for plan, bound_items in bound_plans:
        for condition, selection in bound_items:
            case_ids = root_case_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            selected_task_count += len(case_ids)
            existing = _validated_condition_task_ids(
                evidence_store=evidence_store,
                condition=condition,
            )
            for case_id in case_ids:
                if case_id in existing:
                    continue
                is_failure_root = (
                    blocked_error.condition_id == condition.condition_id
                    and blocked_error.task_id == case_id
                )
                if (
                    not failure_checkpointed
                    and blocked_error.condition_id is None
                ):
                    is_failure_root = True
                root_status = "blocked" if is_failure_root else "not_started"
                try:
                    _checkpoint_dependency_outcome(
                        evidence_store=evidence_store,
                        suite_root=suite_root,
                        condition=condition,
                        task_id=case_id,
                        root_status=root_status,
                        blocked_error=blocked_error,
                        execution_classification=execution_classification,
                    )
                    if is_failure_root:
                        failure_checkpointed = True
                except Exception as persistence_error:
                    persistence_failures.append(
                        {
                            "condition_id": condition.condition_id,
                            "task_id": case_id,
                            "failure_kind": type(persistence_error).__name__,
                            "message": str(persistence_error),
                        }
                    )
            try:
                _finalize_formal_condition_snapshot(
                    evidence_store=evidence_store,
                    condition=condition,
                    selected_case_ids=case_ids,
                )
            except Exception as persistence_error:
                persistence_failures.append(
                    {
                        "condition_id": condition.condition_id,
                        "task_id": None,
                        "failure_kind": type(persistence_error).__name__,
                        "message": str(persistence_error),
                    }
                )

    closed_results = _blocked_condition_results(
        bound_plans=bound_plans,
        suite_root=suite_root,
        root_case_filter=root_case_filter,
        existing_results=condition_results,
    )
    terminal_status: PaperStatus | str = (
        PaperStatus.INCOMPLETE
        if persistence_failures
        else PaperStatus.BLOCKED
    )
    error_summary: tuple[dict[str, Any], ...] = (
        summary,
        *(
            (
                {
                    "outcome_status": "blocked_dependency",
                    "evidence_integrity": "invalid",
                    "failure_stage": "manifest_closure",
                    "failure_kind": "persistence_failure",
                    "failures": persistence_failures,
                },
            )
            if persistence_failures
            else ()
        ),
    )
    suite_result = PaperSuiteResult(
        suite_id=suite_id,
        status=terminal_status,
        output_root=suite_root.as_posix(),
        started_at=started_at,
        ended_at=_utc_now(),
        experiment_ids=tuple(plan.experiment_id for plan in plans),
        condition_count=sum(len(items) for _plan, items in bound_plans),
        run_count=len(closed_results),
        task_count=selected_task_count,
        provider_attempt_count=usage.provider_attempt_count,
        total_tokens=usage.total_tokens,
        total_cost_estimate=usage.reportable_total_cost_estimate(),
        cost_estimate_by_currency=(
            dict(sorted(usage.cost_estimate_by_currency.items()))
            if usage.cost_estimate_by_currency
            else None
        ),
        total_cost_estimate_status=usage.cost_estimate_status(),
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref={
            "budget_digest": budget.budget_digest,
            "approval_mode": budget_approval["approval_mode"],
        },
        metrics_refs=(),
        audit_refs=(),
        error_summary=error_summary,
    )
    _finalize_formal_manifests(
        suite_root=suite_root,
        suite_result=suite_result,
        plans=plans,
        condition_results=closed_results,
        execution_classification=execution_classification,
    )
    try:
        FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        suite_result = replace(
            suite_result,
            status=PaperStatus.INCOMPLETE,
            error_summary=(
                *suite_result.error_summary,
                {
                    "outcome_status": "blocked_dependency",
                    "evidence_integrity": "invalid",
                    "failure_stage": "evidence_manifest_closure",
                    "failure_kind": type(error).__name__,
                    "message": str(error),
                },
            ),
        )
        _finalize_formal_manifests(
            suite_root=suite_root,
            suite_result=suite_result,
            plans=plans,
            condition_results=closed_results,
            execution_classification=execution_classification,
        )
    return suite_result


def _validated_condition_task_ids(
    *,
    evidence_store: FormalEvidenceStore,
    condition: Any,
) -> set[str]:
    run_root = (
        evidence_store.output_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    if not run_root.is_dir():
        return set()
    try:
        tasks = evidence_store._validate_run(
            run_root,
            experiment_id=condition.experiment_id,
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return set()
    return {str(task["task_id"]) for task in tasks}


def _checkpoint_dependency_outcome(
    *,
    evidence_store: FormalEvidenceStore,
    suite_root: Path,
    condition: Any,
    task_id: str,
    root_status: str,
    blocked_error: PaperInfrastructureBlockedError,
    execution_classification: Mapping[str, Any] | None,
) -> None:
    terminal = blocked_error.terminal_outcome.to_dict()
    event_type = (
        "EXPERIMENT_DEPENDENCY_BLOCKED"
        if root_status == "blocked"
        else "EXPERIMENT_NOT_STARTED_AFTER_DEPENDENCY_BLOCK"
    )
    event_id = (
        f"formal-{event_type.lower()}-{condition.condition_id}-"
        f"{condition.repeat_id}-{task_id}"
    )
    artifact = _write_runner_artifact(
        suite_root=suite_root,
        condition=condition,
        task_id=task_id,
        artifact_name=(
            "dependency-blocked.json"
            if root_status == "blocked"
            else "not-started.json"
        ),
        body={
            **terminal,
            "root_status": root_status,
            "condition_id": condition.condition_id,
            "task_id": task_id,
            "message": str(blocked_error),
        },
    )
    common = {
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
        **terminal,
        **_evidence_flags(
            paper_eligible=False,
            execution_classification=execution_classification,
        ),
        "paper_ineligibility_reasons": ["blocked_dependency"],
        "record_scope": "experiment",
    }
    task = {
        **common,
        "root_status": root_status,
        "error_kind": terminal["failure_kind"],
        "event_refs": [{"event_id": event_id, "event_type": event_type}],
        "evidence_artifact_refs": [artifact],
    }
    attempt = {
        **common,
        "attempt_id": (
            f"experiment-{root_status}-{condition.condition_id}-"
            f"{condition.repeat_id}-{task_id}"
        ),
        "attempt_status": (
            "blocked_dependency" if root_status == "blocked" else "not_started"
        ),
        "provider_attempt_index": 0,
        "error_kind": terminal["failure_kind"],
    }
    event = {
        **common,
        "event_id": event_id,
        "event_type": event_type,
    }
    evidence_store.checkpoint_root(
        experiment_id=condition.experiment_id,
        condition=_as_json(condition),
        repeat_id=condition.repeat_id,
        task=task,
        attempts=[attempt],
        faults=[],
        events=[event],
        artifact_refs=[artifact],
    )


def _blocked_condition_results(
    *,
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    suite_root: Path,
    root_case_filter: Mapping[str, tuple[str, ...]],
    existing_results: Sequence[PaperConditionResult],
) -> list[PaperConditionResult]:
    remaining_existing = list(existing_results)
    results: list[PaperConditionResult] = []
    for _plan, bound_items in bound_plans:
        for condition, selection in bound_items:
            prior = None
            if (
                remaining_existing
                and remaining_existing[0].condition_id == condition.condition_id
            ):
                prior = remaining_existing.pop(0)
            if prior is not None:
                results.append(prior)
                continue
            case_ids = root_case_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            run_root = (
                suite_root
                / "experiments"
                / condition.experiment_id
                / "runs"
                / condition.condition_id
                / str(condition.repeat_id)
            )
            tasks: list[dict[str, Any]] = []
            attempts: list[dict[str, Any]] = []
            try:
                store = FormalEvidenceStore(suite_root)
                tasks = store._validate_run(
                    run_root,
                    experiment_id=condition.experiment_id,
                )
                pointer = json.loads(
                    (run_root / "CURRENT.json").read_text(encoding="utf-8")
                )
                generation_root = (
                    run_root / ".generations" / str(pointer["generation_id"])
                )
                attempts = _read_jsonl_records(
                    generation_root / "per_attempt_results.jsonl"
                )
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                pass
            task_integrities = {
                str(task.get("evidence_integrity"))
                for task in tasks
                if task.get("evidence_integrity")
                in {"complete", "missing", "invalid", "corrupt"}
            }
            condition_integrity = (
                next(iter(task_integrities))
                if len(task_integrities) == 1
                else "missing"
            )
            outcome_counts = {
                "succeeded": sum(
                    _status_value(task.get("root_status", "")) == "completed"
                    for task in tasks
                ),
                "failed_experimental": sum(
                    task.get("outcome_status") == "failed_experimental"
                    for task in tasks
                ),
                "blocked_dependency": sum(
                    task.get("outcome_status") == "blocked_dependency"
                    for task in tasks
                ),
                "not_started": sum(
                    task.get("root_status") == "not_started" for task in tasks
                ),
            }
            results.append(
                PaperConditionResult(
                    condition_id=condition.condition_id,
                    status=PaperStatus.BLOCKED,
                    repeat_count=1,
                    task_count=len(case_ids),
                    completed_root_count=outcome_counts["succeeded"],
                    failed_root_count=outcome_counts["failed_experimental"],
                    blocked_root_count=outcome_counts["blocked_dependency"],
                    provider_attempt_count=_persisted_provider_attempt_count(
                        attempts
                    ),
                    metrics_ref={
                        "paper_eligible": False,
                        "paper_ineligibility_reasons": [
                            "blocked_dependency"
                        ],
                        "evidence_refs": [],
                        "infrastructure_blocked": True,
                        "evidence_integrity": condition_integrity,
                        "outcome_counts": outcome_counts,
                    },
                )
            )
    return results


def _dependency_integrity_for_error(
    error: Exception,
) -> PaperEvidenceIntegrity:
    if isinstance(error, SharedEvidenceError):
        return PaperEvidenceIntegrity(error.evidence_integrity)
    if isinstance(error, FileNotFoundError):
        return PaperEvidenceIntegrity.MISSING
    if isinstance(error, json.JSONDecodeError):
        return PaperEvidenceIntegrity.CORRUPT
    return PaperEvidenceIntegrity.INVALID


def _execution_version_identity(
    *,
    domain: str,
    split_profile_digest: str,
    runtime_generation_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if domain == "factorization":
        plugin_version = FACTORIZATION_PLUGIN_VERSION
        parser_version = "factorization.range_result.parser.v1"
        verifier_version = (
            f"factorization.plugin_verifier@{FACTORIZATION_PLUGIN_VERSION}"
        )
        prompt_version = RANGE_RESULT_SCHEMA_VERSION
    elif domain == "lean_proof":
        plugin_version = LEAN_PLUGIN_VERSION
        parser_version = PROOF_CANDIDATE_PARSER_ID
        verifier_version = f"lean_proof.local_checker@{LEAN_PLUGIN_VERSION}"
        prompt_version = LEAN_PROOF_CANDIDATE_SCHEMA_VERSION
    else:
        raise ValueError("shared reference domain is unsupported")
    body: dict[str, Any] = {
        "schema_version": "tokenshare.paper_execution_version_identity.v1",
        "plugin_version": plugin_version,
        "parser_version": parser_version,
        "verifier_version": verifier_version,
        "executor_version": "0.1.0",
        "prompt_version": prompt_version,
        "split_profile_digest": split_profile_digest,
        "runtime_generation_schema_version": (
            "tokenshare.paper_runtime_generation_identity.v1"
        ),
    }
    if runtime_generation_identity is not None:
        body["runtime_generation_identity_digest"] = digest_json(
            runtime_generation_identity
        )
    return body


def _budget_split_profile_digest(
    *,
    budget: Any,
    condition_id: str,
    case_id: str,
) -> str | None:
    quota_preflight = getattr(budget, "quota_preflight", None)
    commitments = (
        quota_preflight.get("budget_commitments")
        if isinstance(quota_preflight, Mapping)
        else None
    )
    items = (
        commitments.get("ai_unit_commitments")
        if isinstance(commitments, Mapping)
        else None
    )
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return None
    matches = [
        item.get("split_profile_digest")
        for item in items
        if isinstance(item, Mapping)
        and item.get("condition_id") == condition_id
        and item.get("case_id") == case_id
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        return None
    return matches[0]


def _execution_catalog_for_plan(
    *,
    plan: PaperExperimentDispatchPlan,
    catalog_manifest: Any,
) -> Any:
    """从 plan 冻结 identity 恢复执行 view；旧 synthetic plan 保持兼容。"""

    frozen = plan.catalog_execution_view
    if frozen is None:
        return catalog_manifest
    if not isinstance(catalog_manifest, PaperInputCatalogManifest):
        raise ValueError(
            "frozen catalog execution view requires PaperInputCatalogManifest"
        )
    return restore_catalog_execution_view(
        frozen,
        catalog_manifest=catalog_manifest,
    )


def _validate_suite_inputs(
    *,
    dispatch_plans: tuple[PaperExperimentDispatchPlan, ...],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    output_root: str | Path,
    ai_api_configs: Mapping[str, Any],
    hard_limits: Mapping[str, Any],
    resume: bool,
    replay_only: bool,
    root_case_filter: Mapping[str, tuple[str, ...]],
) -> tuple[
    tuple[
        PaperExperimentDispatchPlan,
        tuple[tuple[Any, Any], ...],
    ],
    ...,
]:
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if not isinstance(budget, PaperBudgetResult):
        raise ValueError("budget must be PaperBudgetResult")
    if not isinstance(budget_approval, Mapping):
        raise ValueError("budget_approval must be a mapping")
    approval_mode = budget_approval.get("approval_mode")
    if not isinstance(approval_mode, str) or not approval_mode:
        raise ValueError("budget_approval approval_mode is required")
    approved_digest = budget_approval.get("budget_digest")
    if approved_digest != budget.budget_digest:
        raise ValueError("budget approval digest mismatch")
    if not isinstance(ai_api_configs, Mapping):
        raise ValueError("ai_api_configs must be a mapping")
    if not isinstance(hard_limits, Mapping):
        raise ValueError("hard_limits must be a mapping")
    if any(
        not isinstance(plan, PaperExperimentDispatchPlan) for plan in dispatch_plans
    ):
        raise ValueError("dispatch_plans must contain dispatch plans")
    validate_experiment_dependency_order(
        tuple(plan.experiment_id for plan in dispatch_plans)
    )

    catalog_digest = (
        catalog_manifest.get("catalog_digest")
        if isinstance(catalog_manifest, Mapping)
        else getattr(catalog_manifest, "catalog_digest", None)
    )
    if not isinstance(catalog_digest, str) or not catalog_digest:
        raise ValueError("catalog manifest digest is required")
    suite_root = Path(output_root).resolve(strict=False)
    seen_experiments: set[str] = set()
    bound_plans: list[
        tuple[PaperExperimentDispatchPlan, tuple[tuple[Any, Any], ...]]
    ] = []

    for plan in dispatch_plans:
        if not isinstance(plan, PaperExperimentDispatchPlan):
            raise ValueError("dispatch_plans must contain dispatch plans")
        if plan.experiment_id in seen_experiments:
            raise ValueError("duplicate dispatch plan experiment_id")
        seen_experiments.add(plan.experiment_id)
        expected_root = (suite_root / plan.experiment_id).resolve(strict=False)
        if Path(plan.output_root).resolve(strict=False) != expected_root:
            raise ValueError("dispatch plan output_root is not experiment isolated")
        bound_items = plan.bound_items()
        for condition, selection in bound_items:
            if condition.experiment_id != plan.experiment_id:
                raise ValueError("dispatch plan experiment_id mismatch")
            if (
                condition.catalog_digest != catalog_digest
                or selection.catalog_digest != catalog_digest
            ):
                raise ValueError("dispatch plan catalog digest mismatch")
            if plan.status == "planned" and not replay_only:
                _condition_endpoint_contract(
                    experiment_id=plan.experiment_id,
                    condition=condition,
                    ai_api_configs=ai_api_configs,
                )
        bound_plans.append((plan, bound_items))

    planned = tuple(plan for plan in dispatch_plans if plan.status == "planned")
    planned_items = tuple(
        item
        for plan, items in bound_plans
        if plan.status == "planned"
        for item in items
    )
    if tuple(budget.planned_experiments) != tuple(
        plan.experiment_id for plan in planned
    ):
        raise ValueError("budget planned experiments mismatch")
    if budget.planned_conditions != len(planned_items):
        raise ValueError("budget planned condition count mismatch")
    catalog_cases = _catalog_cases_by_id(catalog_manifest)
    headline_root_runs_by_experiment = {
        plan.experiment_id: sum(
            len(
                root_case_filter.get(
                    condition.condition_id,
                    tuple(selection.ordered_case_ids),
                )
            )
            for condition, selection in items
        )
        for plan, items in bound_plans
        if plan.status == "planned"
    }
    headline_ai_units_by_experiment = {
        plan.experiment_id: sum(
            (
                _selected_ai_unit_count(
                    selection=selection,
                    case_ids=root_case_filter[condition.condition_id],
                    catalog_cases=catalog_cases,
                )
                if root_case_filter
                else selection.expected_ai_unit_count
            )
            for condition, selection in items
        )
        for plan, items in bound_plans
        if plan.status == "planned"
    }
    _validate_budget_execution_totals(
        budget=budget,
        planned_experiment_ids=tuple(
            plan.experiment_id for plan in planned
        ),
        headline_root_runs_by_experiment=headline_root_runs_by_experiment,
        headline_ai_units_by_experiment=headline_ai_units_by_experiment,
    )
    return tuple(bound_plans)


def _preflight_formal_disk_capacity(
    *,
    output_root: str | Path,
    budget: PaperBudgetResult,
    resume: bool,
) -> dict[str, Any]:
    """在 evidence/provider 之前验证目标卷可容纳剩余 formal evidence。"""

    try:
        estimate = _validated_formal_disk_estimate(budget)
    except (KeyError, TypeError, ValueError) as error:
        raise PaperInfrastructureBlockedError(
            f"formal disk estimate is invalid: {error}",
            evidence_integrity=PaperEvidenceIntegrity.INVALID,
            failure_stage="disk_preflight",
            failure_kind="invalid_disk_estimate",
            diagnostics={"validation_error": str(error)},
        ) from error
    forecast_bytes = int(estimate["forecast_bytes"])
    components = _required_mapping(estimate["components"], "disk components")
    policy = _required_mapping(estimate["policy"], "disk policy")
    root = Path(output_root).resolve(strict=False)
    existing_canonical_bytes, largest_existing_condition_bytes = (
        _existing_canonical_evidence_usage(root) if resume else (0, 0)
    )
    # actual canonical bytes 与 p95 forecast 不是同一进度单位；startup 保守保留
    # 全部 forecast，resume 的精确进度由 validated task identity 恢复到计数器。
    remaining_forecast_bytes = forecast_bytes
    headroom_bytes = max(
        (remaining_forecast_bytes + 3) // 4,
        2 * 1024**3,
    )
    condition_compaction_bytes = max(
        int(estimate["max_condition_compaction_bytes"]),
        largest_existing_condition_bytes,
    )
    required_bytes = (
        remaining_forecast_bytes + headroom_bytes + condition_compaction_bytes
    )
    volume_path = _nearest_existing_disk_path(root)
    available_bytes = int(shutil.disk_usage(volume_path).free)
    details = {
        "volume_path": volume_path.as_posix(),
        "forecast_bytes": forecast_bytes,
        "theoretical_max_payload_bytes": int(
            estimate["theoretical_max_payload_bytes"]
        ),
        "existing_canonical_bytes": existing_canonical_bytes,
        "remaining_forecast_bytes": remaining_forecast_bytes,
        "headroom_bytes": headroom_bytes,
        "condition_compaction_bytes": condition_compaction_bytes,
        "required_bytes": required_bytes,
        "available_bytes": available_bytes,
        "policy": dict(policy),
        "components": {
            **dict(components),
            "condition_compaction_bytes": condition_compaction_bytes,
            "headroom_bytes": headroom_bytes,
        },
    }
    if available_bytes < required_bytes:
        raise PaperInfrastructureBlockedError(
            "formal disk preflight failed: insufficient disk capacity",
            evidence_integrity=PaperEvidenceIntegrity.INVALID,
            failure_stage="disk_preflight",
            failure_kind="insufficient_disk_capacity",
            diagnostics=details,
        )
    return details


def _validated_formal_disk_estimate(
    budget: PaperBudgetResult,
) -> dict[str, Any]:
    estimate = budget.disk_estimate
    expected_top_keys = {
        "schema_version",
        "inputs",
        "policy",
        "components",
        "forecast_bytes",
        "theoretical_max_payload_bytes",
        "max_condition_compaction_bytes",
    }
    if not isinstance(estimate, Mapping) or set(estimate) != expected_top_keys:
        raise ValueError("disk estimate shape mismatch")
    if estimate.get("schema_version") != "tokenshare.paper_disk_estimate.v3":
        raise ValueError("disk estimate schema mismatch")
    inputs = estimate.get("inputs")
    policy = estimate.get("policy")
    components = estimate.get("components")
    if not isinstance(inputs, Mapping) or not isinstance(policy, Mapping):
        raise ValueError("disk estimate inputs/policy must be mappings")
    if not isinstance(components, Mapping):
        raise ValueError("disk estimate components must be a mapping")
    integer_values = [
        *inputs.values(),
        *(
            value
            for key, value in policy.items()
            if key
            not in {
                "schema_version",
                "provider_attempt_payload_calibration_source",
            }
        ),
        *components.values(),
        estimate.get("forecast_bytes"),
        estimate.get("theoretical_max_payload_bytes"),
        estimate.get("max_condition_compaction_bytes"),
    ]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in integer_values
    ):
        raise ValueError("disk estimate numeric fields must be non-negative integers")
    expected_counts = {
        "planned_conditions": budget.planned_conditions,
        "planned_root_runs": budget.planned_root_runs,
        "planned_ai_units": budget.planned_ai_units,
        "provider_attempt_upper_bound": budget.max_provider_attempts,
        "token_upper_bound": budget.token_upper_bound,
    }
    for field_name, expected_value in expected_counts.items():
        if inputs.get(field_name) != expected_value:
            raise ValueError(f"disk estimate input drift: {field_name}")
    quota = budget.quota_preflight
    commitments = quota.get("budget_commitments") if isinstance(quota, Mapping) else None
    request_limits = (
        commitments.get("request_limits")
        if isinstance(commitments, Mapping)
        else None
    )
    frozen_max_tokens = None
    if isinstance(request_limits, Mapping):
        frozen_max_tokens = request_limits.get("token_upper_bound_per_provider_attempt")
    if frozen_max_tokens is None:
        provider_attempts = budget.max_provider_attempts
        if (
            provider_attempts > 0
            and budget.token_upper_bound % provider_attempts == 0
        ):
            frozen_max_tokens = budget.token_upper_bound // provider_attempts
    if frozen_max_tokens is None and isinstance(request_limits, Mapping):
        frozen_max_tokens = request_limits.get("max_tokens")
    if inputs.get("max_tokens") != frozen_max_tokens:
        raise ValueError("disk estimate token ceiling drift")
    recomputed = _paper_disk_estimate(
        planned_conditions=int(inputs["planned_conditions"]),
        planned_root_runs=int(inputs["planned_root_runs"]),
        planned_ai_units=int(inputs["planned_ai_units"]),
        provider_attempt_upper_bound=int(
            inputs["provider_attempt_upper_bound"]
        ),
        max_tokens=int(inputs["max_tokens"]),
        token_upper_bound=int(inputs["token_upper_bound"]),
        max_condition_root_runs=int(inputs["max_condition_root_runs"]),
        max_condition_ai_units=int(inputs["max_condition_ai_units"]),
        max_condition_provider_attempts=int(
            inputs["max_condition_provider_attempts"]
        ),
    )
    if dict(estimate) != recomputed:
        raise ValueError("disk estimate components/policy mismatch")
    return dict(estimate)


def _root_disk_attempt_upper(
    *,
    condition: Any,
    case: Mapping[str, Any],
    request_limits: Mapping[str, Any],
) -> tuple[int, int]:
    ai_unit_count = (
        estimated_ai_units_for_case(dict(case))
        if "schema_version" in case or "expected_ai_unit_count" in case
        else 1
    )
    attempts_per_unit = request_limits.get("max_provider_attempts", 1)
    max_tokens = request_limits.get("max_tokens")
    if (
        isinstance(attempts_per_unit, bool)
        or not isinstance(attempts_per_unit, int)
        or attempts_per_unit < 1
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens < 1
    ):
        raise ValueError("rolling root request limits must be positive integers")
    replacement_policy = _condition_replacement_policy(condition)
    replacement_multiplier = (
        int(replacement_policy["max_retries"])
        if replacement_policy["replacement_attempts_allowed"] is True
        else 0
    )
    return (
        ai_unit_count,
        ai_unit_count * (1 + replacement_multiplier) * attempts_per_unit,
    )


def _rolling_disk_forecast_from_plan(
    *,
    budget: PaperBudgetResult,
    bound_plans: Sequence[tuple[Any, Sequence[tuple[Any, Any]]]],
    catalog_manifest: Any,
    ai_api_configs: Mapping[str, Any],
    normalized_root_filter: Mapping[str, tuple[str, ...]],
    completed_task_keys: set[tuple[str, str, str, str]],
    max_condition_compaction_bytes: int,
) -> _RollingDiskForecast:
    """从冻结 plan 与已验证 terminal identity 恢复 remaining 计数。"""

    estimate = _validated_formal_disk_estimate(budget)
    inputs = _required_mapping(estimate["inputs"], "disk inputs")
    policy = _required_mapping(estimate["policy"], "disk policy")
    components = _required_mapping(estimate["components"], "disk components")
    remaining_root_runs = int(inputs["planned_root_runs"])
    remaining_ai_units = int(inputs["planned_ai_units"])
    remaining_provider_attempts = int(inputs["provider_attempt_upper_bound"])
    cases_by_id = _catalog_cases_by_id(catalog_manifest)
    for plan, items in bound_plans:
        if plan.status != "planned":
            continue
        for condition, selection in items:
            selected_case_ids = normalized_root_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            terminal_case_ids = tuple(
                case_id
                for case_id in selected_case_ids
                if _formal_task_key(condition, case_id) in completed_task_keys
            )
            if not terminal_case_ids:
                continue
            _, request_limits, _ = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            for case_id in terminal_case_ids:
                case = cases_by_id.get(case_id)
                if case is None:
                    # 完整映射无法证明时仅扣 root identity，其他计数保持保守。
                    remaining_root_runs = max(0, remaining_root_runs - 1)
                    continue
                frozen_case = _case_with_selection_split_profile(case, selection)
                ai_unit_count, provider_attempt_count = _root_disk_attempt_upper(
                    condition=condition,
                    case=frozen_case,
                    request_limits=request_limits,
                )
                remaining_root_runs = max(0, remaining_root_runs - 1)
                remaining_ai_units = max(
                    0,
                    remaining_ai_units - ai_unit_count,
                )
                remaining_provider_attempts = max(
                    0,
                    remaining_provider_attempts - provider_attempt_count,
                )
    fixed_forecast_bytes = int(components["fixed_manifest_bytes"]) + int(
        components["fixed_temp_bytes"]
    )
    return _RollingDiskForecast(
        remaining_root_runs=remaining_root_runs,
        remaining_ai_units=remaining_ai_units,
        remaining_provider_attempts=remaining_provider_attempts,
        fixed_forecast_bytes=fixed_forecast_bytes,
        max_condition_compaction_bytes=max_condition_compaction_bytes,
        policy=dict(policy),
    )


def _preflight_formal_root_capacity(
    *,
    output_root: str | Path,
    condition: Any,
    task_id: str,
    case: Mapping[str, Any],
    request_limits: Mapping[str, Any],
    rolling_forecast: _RollingDiskForecast,
) -> dict[str, Any]:
    """在 root callback/provider 前校验剩余 suite forecast 与尾部补差。"""

    ai_unit_count, provider_attempt_count = _root_disk_attempt_upper(
        condition=condition,
        case=case,
        request_limits=request_limits,
    )
    max_tokens = int(request_limits["max_tokens"])
    details = rolling_forecast.root_guard_details(
        output_root=output_root,
        condition_id=str(condition.condition_id),
        task_id=str(task_id),
        ai_unit_count=ai_unit_count,
        provider_attempt_count=provider_attempt_count,
        max_tokens=max_tokens,
    )
    if details["available_bytes"] < details["required_bytes"]:
        raise PaperInfrastructureBlockedError(
            "formal rolling root disk guard failed: insufficient capacity",
            evidence_integrity=PaperEvidenceIntegrity.INVALID,
            failure_stage="disk_preflight",
            failure_kind="insufficient_rolling_root_capacity",
            condition_id=str(condition.condition_id),
            task_id=str(task_id),
            diagnostics=details,
        )
    return details


def _preflight_formal_condition_compaction_capacity(
    *,
    output_root: str | Path,
    condition_id: str,
    current_condition_reachable_bytes: int,
) -> dict[str, Any]:
    """为 v3 delta condition 的 terminal compaction 预留 COW 空间。"""

    if (
        isinstance(current_condition_reachable_bytes, bool)
        or not isinstance(current_condition_reachable_bytes, int)
        or current_condition_reachable_bytes < 0
    ):
        raise ValueError("condition reachable bytes must be a non-negative integer")
    fixed_safety_bytes = 2 * 1024**3
    required_bytes = current_condition_reachable_bytes + fixed_safety_bytes
    root = Path(output_root).resolve(strict=False)
    volume_path = _nearest_existing_disk_path(root)
    available_bytes = int(shutil.disk_usage(volume_path).free)
    details = {
        "volume_path": volume_path.as_posix(),
        "condition_id": str(condition_id),
        "current_condition_reachable_bytes": current_condition_reachable_bytes,
        "fixed_safety_bytes": fixed_safety_bytes,
        "required_bytes": required_bytes,
        "available_bytes": available_bytes,
    }
    if available_bytes < required_bytes:
        raise PaperInfrastructureBlockedError(
            "formal condition compaction disk guard failed: insufficient capacity",
            evidence_integrity=PaperEvidenceIntegrity.INVALID,
            failure_stage="condition_compaction",
            failure_kind="insufficient_condition_compaction_capacity",
            condition_id=str(condition_id),
            diagnostics=details,
        )
    return details


def _finalize_formal_condition_snapshot(
    *,
    evidence_store: FormalEvidenceStore,
    condition: Any,
    selected_case_ids: Sequence[str],
    condition_events: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """把冻结分母已齐的 condition 统一收口为唯一 tail 与 standalone snapshot。"""

    if not selected_case_ids:
        raise ValueError("formal condition snapshot requires selected roots")
    run_root = (
        evidence_store.output_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    condition_manifest = _required_json_object(
        run_root / "condition_manifest.json",
        "condition manifest",
    )
    if condition_manifest.get("terminal") is True:
        return
    if condition_manifest.get("condition_event_delta_count") == 1:
        if condition_events is not None:
            evidence_store.checkpoint_condition_tail_events(
                experiment_id=condition.experiment_id,
                condition=_as_json(condition),
                repeat_id=condition.repeat_id,
                anchor_task_id=str(selected_case_ids[-1]),
                events=tuple(condition_events),
            )
    else:
        evidence_store.checkpoint_condition_tail_events(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            anchor_task_id=str(selected_case_ids[-1]),
            events=tuple(condition_events or ()),
        )
    condition_manifest = _required_json_object(
        run_root / "condition_manifest.json",
        "condition manifest",
    )
    _preflight_formal_condition_compaction_capacity(
        output_root=evidence_store.output_root,
        condition_id=condition.condition_id,
        current_condition_reachable_bytes=int(
            condition_manifest["reachable_size_bytes"]
        ),
    )
    evidence_store.compact_condition_snapshot(
        experiment_id=condition.experiment_id,
        condition=_as_json(condition),
        repeat_id=condition.repeat_id,
        temp_parent=evidence_store.output_root.parent,
    )


def _nearest_existing_disk_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if candidate.is_file():
        return candidate.parent
    return candidate


def _existing_canonical_evidence_usage(root: Path) -> tuple[int, int]:
    manifest_path = root / "evidence_manifest.json"
    if not manifest_path.is_file():
        return 0, 0
    manifest = _required_json_object(manifest_path, "evidence manifest")
    schema_version = manifest.get("schema_version")
    if schema_version == "tokenshare.paper_evidence_manifest.v1":
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        total = 0
        condition_sizes: dict[tuple[str, ...], int] = {}
        for entry in entries:
            size = _manifest_entry_size(entry)
            total += size
            path_value = entry.get("path") if isinstance(entry, Mapping) else None
            parts = Path(str(path_value)).parts if isinstance(path_value, str) else ()
            if len(parts) >= 5 and parts[0] == "experiments" and parts[2] == "runs":
                key = tuple(parts[:5])
                condition_sizes[key] = condition_sizes.get(key, 0) + size
        return total, max(condition_sizes.values(), default=0)
    if schema_version == "tokenshare.paper_evidence_manifest.v2":
        static_files = manifest.get("files", manifest.get("static_files"))
        conditions = manifest.get("conditions")
        if not isinstance(static_files, list) or not isinstance(conditions, list):
            raise ValueError("evidence manifest v2 inventory is invalid")
        static_size = sum(_manifest_entry_size(entry) for entry in static_files)
        condition_size = 0
        for entry in conditions:
            if not isinstance(entry, Mapping):
                raise ValueError("evidence manifest v2 condition entry is invalid")
            reachable_size = entry.get("reachable_size_bytes")
            if (
                isinstance(reachable_size, bool)
                or not isinstance(reachable_size, int)
                or reachable_size < 0
            ):
                raise ValueError(
                    "evidence manifest v2 reachable size is invalid"
                )
            condition_size += reachable_size
        largest_condition_size = max(
            (int(entry["reachable_size_bytes"]) for entry in conditions),
            default=0,
        )
        return static_size + condition_size, largest_condition_size
    raise ValueError("evidence manifest schema version mismatch")


def _existing_canonical_evidence_bytes(root: Path) -> int:
    """保留内部标量入口；preflight 同时消费最大 condition size。"""

    return _existing_canonical_evidence_usage(root)[0]


def _manifest_entry_size(entry: Any) -> int:
    if not isinstance(entry, Mapping):
        raise ValueError("evidence manifest size entry is invalid")
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("evidence manifest entry size is invalid")
    return size


def _selected_ai_unit_count(
    *,
    selection: Any,
    case_ids: Sequence[str],
    catalog_cases: Mapping[str, Mapping[str, Any]],
) -> int:
    """按 selection 冻结的逐 case 口径计算 smoke AI-unit 分母。"""

    raw_case_counts = getattr(selection, "case_expected_ai_unit_counts", None)
    if raw_case_counts is None:
        return sum(
            estimated_ai_units_for_case(dict(catalog_cases[case_id]))
            for case_id in case_ids
        )
    if not isinstance(raw_case_counts, Mapping):
        raise ValueError("selection case AI-unit commitments must be a mapping")
    ordered_case_ids = tuple(str(case_id) for case_id in selection.ordered_case_ids)
    if set(raw_case_counts) != set(ordered_case_ids):
        raise ValueError("selection case AI-unit commitment coverage mismatch")
    normalized_counts: dict[str, int] = {}
    for case_id, count in raw_case_counts.items():
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("selection case AI-unit commitment ID is invalid")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("selection case AI-unit commitment count is invalid")
        normalized_counts[case_id] = count
    if sum(normalized_counts.values()) != selection.expected_ai_unit_count:
        raise ValueError("selection case AI-unit commitment total mismatch")
    if any(case_id not in normalized_counts for case_id in case_ids):
        raise ValueError("selected case is absent from AI-unit commitments")
    return sum(normalized_counts[case_id] for case_id in case_ids)


def _validate_budget_execution_totals(
    *,
    budget: PaperBudgetResult,
    planned_experiment_ids: tuple[str, ...],
    headline_root_runs_by_experiment: Mapping[str, int],
    headline_ai_units_by_experiment: Mapping[str, int],
) -> None:
    """校验 headline plan 与额外支撑执行共同组成的冻结预算身份。"""

    quota_preflight = budget.quota_preflight
    commitments = (
        quota_preflight.get("budget_commitments")
        if isinstance(quota_preflight, Mapping)
        else None
    )
    identity = (
        commitments.get("experiment_budget_identity")
        if isinstance(commitments, Mapping)
        else None
    )
    if identity is None:
        if budget.planned_root_runs != sum(
            headline_root_runs_by_experiment.values()
        ):
            raise ValueError("budget planned root run count mismatch")
        if budget.planned_ai_units != sum(
            headline_ai_units_by_experiment.values()
        ):
            raise ValueError("budget planned AI unit count mismatch")
        return
    if not isinstance(identity, Mapping):
        raise ValueError("budget experiment identity must be a mapping")
    if (
        identity.get("schema_version")
        != "tokenshare.paper_experiment_budget_identity.v1"
    ):
        raise ValueError("unsupported budget experiment identity schema")

    experiment_ids = set(planned_experiment_ids)

    def require_count_map(
        field_name: str,
        *,
        allow_omitted_zeroes: bool = False,
    ) -> dict[str, int]:
        value = identity.get(field_name)
        if (
            not isinstance(value, Mapping)
            or (
                set(value) > experiment_ids
                if allow_omitted_zeroes
                else set(value) != experiment_ids
            )
        ):
            raise ValueError(
                f"budget experiment identity {field_name} coverage mismatch"
            )
        result: dict[str, int] = (
            {experiment_id: 0 for experiment_id in experiment_ids}
            if allow_omitted_zeroes
            else {}
        )
        for experiment_id, count in value.items():
            if (
                not isinstance(experiment_id, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                raise ValueError(
                    f"budget experiment identity {field_name} is invalid"
                )
            result[experiment_id] = count
        return result

    frozen_headline_roots = require_count_map(
        "headline_root_runs_by_experiment"
    )
    supporting_roots = require_count_map(
        "supporting_baseline_root_runs_by_experiment",
        allow_omitted_zeroes=True,
    )
    actual_roots = require_count_map(
        "actual_scheduled_root_runs_by_experiment"
    )
    frozen_headline_ai_units = require_count_map(
        "headline_ai_units_by_experiment"
    )
    supporting_ai_units = require_count_map(
        "supporting_baseline_ai_units_by_experiment",
        allow_omitted_zeroes=True,
    )
    first_attempt_ai_units = require_count_map(
        "planned_first_attempt_ai_units_by_experiment"
    )
    if frozen_headline_roots != dict(headline_root_runs_by_experiment):
        raise ValueError("budget headline root run identity mismatch")
    if frozen_headline_ai_units != dict(headline_ai_units_by_experiment):
        raise ValueError("budget headline AI unit identity mismatch")
    if actual_roots != {
        experiment_id: (
            frozen_headline_roots[experiment_id]
            + supporting_roots[experiment_id]
        )
        for experiment_id in planned_experiment_ids
    }:
        raise ValueError("budget actual scheduled root run identity mismatch")
    if first_attempt_ai_units != {
        experiment_id: (
            frozen_headline_ai_units[experiment_id]
            + supporting_ai_units[experiment_id]
        )
        for experiment_id in planned_experiment_ids
    }:
        raise ValueError("budget first-attempt AI unit identity mismatch")
    if budget.planned_root_runs != sum(actual_roots.values()):
        raise ValueError("budget planned root run count mismatch")
    if budget.planned_ai_units != sum(first_attempt_ai_units.values()):
        raise ValueError("budget planned AI unit count mismatch")


def _suite_status(
    *,
    plans: tuple[PaperExperimentDispatchPlan, ...],
    results: list[PaperConditionResult],
) -> PaperStatus:
    if not results:
        return PaperStatus.BLOCKED if plans else PaperStatus.COMPLETED
    statuses = {_status_value(result.status) for result in results}
    if PaperStatus.BUDGET_EXHAUSTED.value in statuses:
        return PaperStatus.BUDGET_EXHAUSTED
    if PaperStatus.FAILED.value in statuses:
        return PaperStatus.FAILED
    if (
        PaperStatus.BLOCKED.value in statuses
        or any(plan.status == "blocked" for plan in plans)
    ):
        return PaperStatus.BLOCKED
    if statuses.intersection(
        {
            PaperStatus.PLANNED.value,
            PaperStatus.RUNNING.value,
            PaperStatus.INCOMPLETE.value,
        }
    ):
        return PaperStatus.INCOMPLETE
    if PaperStatus.COMPLETED_WITH_FAILURES.value in statuses:
        return PaperStatus.COMPLETED_WITH_FAILURES
    return PaperStatus.COMPLETED


def _classified_suite_status(
    status: PaperStatus,
    *,
    classification: Mapping[str, Any] | None,
) -> PaperStatus:
    """execution classification 不得覆盖 runner 已判定的 suite 终态。"""

    return status


def _normalize_execution_classification(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    body = dict(value)
    expected = {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
    }
    if any(body.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("unsupported execution classification")
    reasons = body.get("ineligibility_reasons")
    if not isinstance(reasons, list) or not {
        "smoke_suite",
        "pilot_only",
    }.issubset(reasons):
        raise ValueError("smoke execution classification reasons are incomplete")
    if any(not isinstance(reason, str) or not reason for reason in reasons):
        raise ValueError("smoke execution classification reasons are invalid")
    baseline_policy = body.get("baseline_policy", "required_by_formal_plan")
    if baseline_policy not in {
        "required_by_formal_plan",
        "omitted_for_smoke_regression",
    }:
        raise ValueError("unsupported smoke baseline policy")
    if (
        baseline_policy == "omitted_for_smoke_regression"
        and "smoke_baseline_not_requested" not in reasons
    ):
        raise ValueError("smoke baseline omission reason is missing")
    return {
        **expected,
        "ineligibility_reasons": list(dict.fromkeys(reasons)),
        "baseline_policy": baseline_policy,
    }


def _normalize_root_case_filter(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    root_case_filter: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if root_case_filter is None:
        return {}
    if not isinstance(root_case_filter, Mapping):
        raise ValueError("root_case_filter must be a mapping")
    expected: dict[str, tuple[str, ...]] = {
        condition.condition_id: tuple(selection.ordered_case_ids)
        for plan in plans
        if plan.status == "planned"
        for condition, selection in plan.bound_items()
    }
    if set(root_case_filter) != set(expected):
        raise ValueError("root_case_filter condition coverage mismatch")
    normalized: dict[str, tuple[str, ...]] = {}
    for condition_id, raw_case_ids in root_case_filter.items():
        if isinstance(raw_case_ids, (str, bytes, bytearray)) or not isinstance(
            raw_case_ids, Sequence
        ):
            raise ValueError("root_case_filter values must be sequences")
        case_ids = tuple(str(case_id) for case_id in raw_case_ids)
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("root_case_filter values must be non-empty and unique")
        if any(not case_id or case_id not in expected[condition_id] for case_id in case_ids):
            raise ValueError("root_case_filter references a non-canonical case")
        normalized[str(condition_id)] = case_ids
    return normalized


def _normalize_pre_execution_documents(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("pre_execution_documents must be a mapping")
    allowed = {
        "smoke_profile.json",
        "smoke_execution_plan.json",
        "smoke_launch_manifest.json",
    }
    if set(value).difference(allowed):
        raise ValueError("unsupported pre-execution document")
    normalized: dict[str, Any] = {}
    for name, body in value.items():
        if not isinstance(body, Mapping):
            raise ValueError("pre-execution documents must be JSON objects")
        normalized[str(name)] = _as_json(body)
    return normalized


def _normalize_recovery_documents(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("recovery_documents must be a mapping")
    normalized: dict[str, Any] = {}
    for raw_name, body in value.items():
        name = str(raw_name).replace("\\", "/")
        parts = name.split("/")
        filename = parts[-1] if parts else ""
        if (
            len(parts) != 2
            or parts[0] != "smoke_recoveries"
            or not filename.startswith("smoke_recovery_")
            or not filename.endswith(".json")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("invalid recovery document path")
        if not isinstance(body, Mapping):
            raise ValueError("recovery documents must be JSON objects")
        normalized[name] = _as_json(body)
    return normalized


def _persist_pre_execution_documents(
    *,
    suite_root: Path,
    documents: Mapping[str, Any],
) -> None:
    for name, body in documents.items():
        path = suite_root / name
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != body:
                raise ValueError(f"pre-execution document identity mismatch: {name}")
            continue
        _write_json(path, body)


def _suite_paper_eligible(
    *,
    plans: tuple[PaperExperimentDispatchPlan, ...],
    results: list[PaperConditionResult],
    transport: Any,
    real_transport: bool,
    execution_classification: Mapping[str, Any] | None = None,
    trace_evidence: bool = False,
) -> bool:
    if execution_classification is not None:
        return False
    if not trace_evidence and (
        not real_transport or _is_offline_capturing_transport(transport)
    ):
        return False
    if not results or any(not plan.paper_eligible_possible for plan in plans):
        return False
    expected_condition_ids = {
        condition.condition_id
        for plan in plans
        if plan.status != "blocked"
        for condition in plan.conditions
    }
    if {result.condition_id for result in results} != expected_condition_ids:
        return False
    return all(
        isinstance(result.metrics_ref, Mapping)
        and result.metrics_ref.get("paper_eligible") is True
        and bool(result.metrics_ref.get("evidence_refs"))
        and int(result.metrics_ref.get("task_evidence_count", 0))
        == result.task_count
        and int(result.metrics_ref.get("attempt_evidence_count", 0)) > 0
        for result in results
    )


def _audit_persisted_condition_evidence(
    *,
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    repeat_id: int,
    expected_task_ids: Sequence[str],
) -> dict[str, Any]:
    """从已提交 generation 聚合 attempt→task→condition，不读取 adapter 汇总布尔值。"""

    reasons: list[str] = []
    run_root = (
        suite_root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    try:
        logical = FormalEvidenceStore(suite_root).load_logical_run_records(
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
        )
        tasks = logical["tasks"]
        attempts = logical["attempts"]
        events = logical["events"]
        artifacts = logical["artifacts"]
        generation_roots = logical["source_generation_roots"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        tasks, attempts, events, artifacts = [], [], [], []
        generation_roots = ()
        reasons.append("persisted_condition_evidence_invalid")

    expected = tuple(str(task_id) for task_id in expected_task_ids)
    actual = tuple(str(task.get("task_id")) for task in tasks)
    if len(set(expected)) != len(expected) or set(actual) != set(expected):
        reasons.append("condition_task_inventory_mismatch")
    if not tasks or any(task.get("paper_eligible") is not True for task in tasks):
        reasons.append("persisted_task_not_paper_eligible")
    if any(task.get("record_scope") != "protocol" for task in tasks):
        reasons.append("persisted_task_not_protocol_scoped")
    if not attempts or any(
        attempt.get("paper_eligible") is not True for attempt in attempts
    ):
        reasons.append("persisted_attempt_not_paper_eligible")
    if any(attempt.get("record_scope") != "protocol" for attempt in attempts):
        reasons.append("persisted_attempt_not_protocol_scoped")
    task_ids = set(actual)
    if not events or any(
        event.get("task_id") not in task_ids for event in events
    ):
        reasons.append("persisted_event_inventory_incomplete")
    artifact_task_ids = {
        str(artifact.get("task_id")) for artifact in artifacts if artifact.get("task_id")
    }
    if not artifacts or not task_ids.issubset(artifact_task_ids):
        reasons.append("persisted_artifact_inventory_incomplete")
    event_ids = {
        str(event.get("event_id"))
        for event in events
        if isinstance(event.get("event_id"), str) and event.get("event_id")
    }
    if any(
        not _persisted_task_refs_resolve(
            task,
            event_ids=event_ids,
            artifacts=artifacts,
        )
        for task in tasks
    ) or any(
        not _persisted_attempt_refs_resolve(attempt, artifacts=artifacts)
        for attempt in attempts
    ):
        reasons.append("persisted_evidence_ref_unresolved")

    completed = sum(
        _status_value(task.get("root_status") or "") == "completed" for task in tasks
    )
    blocked = sum(
        _status_value(task.get("root_status") or "") == "blocked" for task in tasks
    )
    budget_exhausted = sum(
        _status_value(task.get("root_status") or "") == "budget_exhausted"
        for task in tasks
    )
    failed = len(expected) - completed - blocked
    evidence_refs: list[dict[str, Any]] = []
    for generation_root in generation_roots:
        for relative in (
            "per_task_results.jsonl",
            "per_attempt_results.jsonl",
            "events/event_log.jsonl",
            "artifacts/artifact_index.jsonl",
        ):
            path = generation_root / relative
            if not path.is_file():
                reasons.append("persisted_condition_evidence_ref_missing")
                continue
            evidence_refs.append(
                {
                    "path": path.relative_to(suite_root).as_posix(),
                    "content_hash": _sha256_bytes(path.read_bytes()),
                }
            )
    stable_reasons = list(dict.fromkeys(reasons))
    return {
        "paper_eligible": not stable_reasons,
        "paper_ineligibility_reasons": stable_reasons,
        "task_evidence_count": len(tasks),
        "attempt_evidence_count": len(attempts),
        "completed_root_count": completed,
        "failed_root_count": max(0, failed),
        "blocked_root_count": blocked,
        "budget_exhausted_root_count": budget_exhausted,
        "provider_attempt_count": _persisted_provider_attempt_count(attempts),
        "evidence_refs": evidence_refs,
    }


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _persisted_provider_attempt_count(
    attempts: Sequence[Mapping[str, Any]],
) -> int:
    """只统计 protocol attempt 中实际持久化的 provider 调用 inventory。"""

    count = 0
    for attempt in attempts:
        if attempt.get("record_scope") != "protocol":
            continue
        provider_attempts = attempt.get("provider_attempts")
        if isinstance(provider_attempts, Sequence) and not isinstance(
            provider_attempts,
            (str, bytes, bytearray),
        ):
            count += len(provider_attempts)
            continue
        persisted_count = attempt.get("provider_attempt_count")
        if (
            isinstance(persisted_count, int)
            and not isinstance(persisted_count, bool)
            and persisted_count > 0
        ):
            count += persisted_count
            continue
        provider_attempt_index = attempt.get("provider_attempt_index")
        if (
            isinstance(provider_attempt_index, int)
            and not isinstance(provider_attempt_index, bool)
            and provider_attempt_index > 0
            and isinstance(attempt.get("provider"), str)
            and bool(attempt.get("provider"))
        ):
            count += 1
    return count


def _persisted_task_refs_resolve(
    task: Mapping[str, Any],
    *,
    event_ids: set[str],
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    event_refs = task.get("event_refs")
    artifact_refs = task.get("evidence_artifact_refs") or task.get("artifact_refs")
    if (
        not isinstance(event_refs, Sequence)
        or isinstance(event_refs, (str, bytes))
        or not event_refs
        or not isinstance(artifact_refs, Sequence)
        or isinstance(artifact_refs, (str, bytes))
        or not artifact_refs
    ):
        return False
    return all(
        isinstance(ref, Mapping) and ref.get("event_id") in event_ids
        for ref in event_refs
    ) and all(
        _persisted_artifact_ref_resolves(ref, artifacts)
        for ref in artifact_refs
    )


def _persisted_attempt_refs_resolve(
    attempt: Mapping[str, Any],
    *,
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    field_names = [
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ]
    status = _status_value(attempt.get("attempt_status") or "")
    if status in {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    }:
        field_names.append("raw_output_ref")
    if status == "parse_failed":
        field_names.append("parse_failure_ref")
    return all(
        _persisted_artifact_ref_resolves(attempt.get(field_name), artifacts)
        for field_name in field_names
    )


def _persisted_artifact_ref_resolves(
    value: Any,
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for artifact in artifacts:
        if value.get("artifact_id") is not None and (
            artifact.get("artifact_id") != value.get("artifact_id")
        ):
            continue
        if value.get("path") is not None and artifact.get("path") != value.get("path"):
            continue
        if value.get("content_hash") is not None and (
            artifact.get("content_hash") != value.get("content_hash")
        ):
            continue
        if any(
            value.get(field_name) is not None
            for field_name in ("artifact_id", "path", "uri")
        ):
            return True
    return False


def _is_offline_capturing_transport(transport: Any) -> bool:
    return (
        transport is not None
        and getattr(transport, "tokenshare_offline_capturing_transport", False)
        is True
    )


def _status_value(status: PaperStatus | str) -> str:
    return str(getattr(status, "value", status))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _condition_endpoint_contract(
    *,
    experiment_id: str,
    condition: Any,
    ai_api_configs: Mapping[str, Any],
) -> tuple[Mapping[str, Any], dict[str, Any], AIAPIExecutorConfig]:
    provider_config_id = condition.provider_config_id
    if not isinstance(provider_config_id, str) or not provider_config_id:
        raise ValueError("formal condition provider_config_id is required")
    config = ai_api_configs.get(provider_config_id)
    if not isinstance(config, AIAPIExecutorConfig):
        raise ValueError("formal condition provider config is unavailable")
    if config.config_digest != condition.source_provider_config_digest:
        raise ValueError("formal condition source provider config digest mismatch")
    if config.provider_family != condition.provider_family:
        raise ValueError("formal condition provider family mismatch")

    entries = tuple(
        entry
        for entry in config.entries
        if entry.enabled and entry.entry_id == condition.model_entry_id
    )
    if len(entries) != 1:
        raise ValueError("formal condition selected model entry is unavailable")
    entry = entries[0]
    if entry.model != condition.provider_model_id:
        raise ValueError("formal condition provider model mismatch")
    if not isinstance(condition.reasoning_profile_id, str):
        raise ValueError("formal condition reasoning profile is required")
    if not isinstance(condition.model_endpoint_identity_digest, str):
        raise ValueError("formal condition endpoint identity digest is required")

    request_limits = {**dict(config.defaults), **dict(entry.request_overrides)}
    _validate_reasoning_controls(
        provider_family=config.provider_family,
        reasoning_profile_id=condition.reasoning_profile_id,
        request_limits=request_limits,
    )
    for field_name in ("max_tokens", "timeout_seconds"):
        value = request_limits.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"formal request control {field_name} must be positive")

    derived_binding: dict[str, Any] = {
        "provider_config_id": provider_config_id,
        "selected_entry_id": condition.model_entry_id,
        "model_entry_id": condition.model_entry_id,
        "provider_family": condition.provider_family,
        "provider_model_id": condition.provider_model_id,
        "reasoning_profile_id": condition.reasoning_profile_id,
        "source_provider_config_digest": condition.source_provider_config_digest,
        "model_endpoint_identity_digest": (
            condition.model_endpoint_identity_digest
        ),
        "request_controls": request_limits,
    }
    for field_name in (
        "model_cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
    ):
        value = getattr(condition, field_name)
        if value is not None:
            derived_binding[field_name] = value

    if experiment_id != EXP5_EXPERIMENT_ID:
        return derived_binding, request_limits, config

    # Exp5 的完整 cohort preflight 无法由单个 condition/config 重建；调用方必须
    # 在保留键下按 experiment_id 提供已批准 binding，普通 config map 不能冒充。
    scoped_bindings = ai_api_configs.get(APPROVED_ENDPOINT_BINDINGS_KEY)
    if not isinstance(scoped_bindings, Mapping):
        raise ValueError("Exp5 requires explicit approved endpoint bindings")
    approved_binding = scoped_bindings.get(experiment_id)
    if not isinstance(approved_binding, Mapping):
        raise ValueError("Exp5 approved endpoint binding is unavailable")
    _validate_exp5_binding(
        approved_binding=approved_binding,
        condition=condition,
        derived_binding=derived_binding,
    )
    return dict(approved_binding), request_limits, config


def _validate_reasoning_controls(
    *,
    provider_family: str,
    reasoning_profile_id: str,
    request_limits: Mapping[str, Any],
) -> None:
    if provider_family == "siliconflow":
        expected_thinking = reasoning_profile_id == "thinking"
        if request_limits.get("enable_thinking") is not expected_thinking:
            raise ValueError(
                "SiliconFlow formal execution thinking control does not match profile"
            )
    if provider_family == "deepseek" and (
        request_limits.get("thinking") != {"type": "enabled"}
        or request_limits.get("reasoning_effort") != "high"
    ):
        raise ValueError(
            "DeepSeek formal execution requires thinking enabled and high reasoning"
        )
    if reasoning_profile_id == "high" and (
        request_limits.get("reasoning_effort") != "high"
    ):
        raise ValueError("high reasoning profile requires reasoning_effort=high")


def _validate_exp5_binding(
    *,
    approved_binding: Mapping[str, Any],
    condition: Any,
    derived_binding: Mapping[str, Any],
) -> None:
    if approved_binding.get("status") != "planned":
        raise ValueError("Exp5 approved endpoint binding is not executable")
    approved_cohort_id = approved_binding.get(
        "cohort_id",
        approved_binding.get("model_cohort_id"),
    )
    if approved_cohort_id != condition.model_cohort_id:
        raise ValueError("Exp5 approved preflight cohort ID mismatch")
    if approved_binding.get("model_cohort_digest") != condition.model_cohort_digest:
        raise ValueError("Exp5 approved preflight cohort digest mismatch")
    member_plans = approved_binding.get("member_plans")
    if not isinstance(member_plans, Mapping):
        raise ValueError("Exp5 approved endpoint binding lacks member plans")
    member_plan = member_plans.get(condition.cohort_member_id)
    if not isinstance(member_plan, Mapping):
        raise ValueError("Exp5 condition lacks an approved cohort member plan")
    member_cohort_id = member_plan.get(
        "cohort_id",
        member_plan.get("model_cohort_id"),
    )
    if member_cohort_id != condition.model_cohort_id:
        raise ValueError("Exp5 approved member cohort ID mismatch")
    if member_plan.get("model_cohort_digest") != condition.model_cohort_digest:
        raise ValueError("Exp5 approved member cohort digest mismatch")
    if member_plan.get("cohort_member_id") != condition.cohort_member_id:
        raise ValueError("Exp5 approved member ID mismatch")
    member_controls = member_plan.get("request_controls")
    if not isinstance(member_controls, Mapping):
        raise ValueError("Exp5 approved member request controls are invalid")
    comparable = member_controls.get("comparable")
    if not isinstance(comparable, Mapping):
        raise ValueError("Exp5 approved member comparable controls are missing")
    comparable_fields = approved_binding.get(
        "comparable_request_control_fields",
        EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
    )
    if not isinstance(comparable_fields, (list, tuple)):
        raise ValueError("Exp5 approved comparable control field list is invalid")
    expected_comparable = {
        field_name: derived_binding["request_controls"].get(field_name)
        for field_name in comparable_fields
    }
    expected_comparable["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
    }
    if dict(comparable) != expected_comparable:
        raise ValueError("Exp5 approved member request controls mismatch")
    if member_controls.get("comparable_digest") != digest_json(
        expected_comparable
    ):
        raise ValueError("Exp5 approved member request controls digest mismatch")
    reasoning_controls = member_controls.get("provider_specific_reasoning")
    if not isinstance(reasoning_controls, Mapping):
        raise ValueError("Exp5 approved member reasoning controls are missing")
    expected_reasoning_controls = exp5_provider_specific_reasoning_controls(
        derived_binding["request_controls"]
    )
    if dict(reasoning_controls) != expected_reasoning_controls:
        raise ValueError("Exp5 approved member reasoning controls mismatch")
    if member_controls.get("provider_specific_reasoning_digest") != digest_json(
        expected_reasoning_controls
    ):
        raise ValueError("Exp5 approved member reasoning digest mismatch")
    snapshot = approved_binding.get("request_controls_snapshot")
    if (
        not isinstance(snapshot, Mapping)
        or dict(snapshot) != expected_comparable
        or approved_binding.get("request_controls_snapshot_digest")
        != digest_json(expected_comparable)
    ):
        raise ValueError("Exp5 approved cohort request controls snapshot mismatch")
    for field_name in (
        "provider_config_id",
        "selected_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ):
        if member_plan.get(field_name) != derived_binding.get(field_name):
            raise ValueError(f"Exp5 approved member {field_name} mismatch")


def _artifact_identity(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for field_name in ("artifact_id", "content_hash", "event_id"):
        candidate = value.get(field_name)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _trace_bank_role_json(
    *, resolver: Any, entry: Any, role: str
) -> Mapping[str, Any]:
    matches = tuple(
        locator for locator in entry.object_locators if locator.object_role == role
    )
    if len(matches) != 1:
        raise ValueError(f"trace source entry requires exactly one {role} object")
    try:
        value = json.loads(resolver.read_verified(matches[0]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"trace source {role} object is not canonical JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"trace source {role} object must be a mapping")
    return value


def _trace_usage_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"trace source usage has invalid {field_name}")
    return value


def _project_committed_trace_source_usage(
    *,
    adapter_result: Any,
    adapter_root: Path,
    trace_runtime: Any,
    condition: Any,
) -> dict[str, Any]:
    """只从 current ledger 已提交 delivery 投影不可变 source-bank 用量。"""

    result_root = Path(_required_field(adapter_result, "output_root"))
    store = ArtifactStore(result_root if result_root.is_dir() else adapter_root)
    provider_call_count = 0
    for attempt in _sequence_field(adapter_result, "attempt_results", "attempts"):
        count = _optional_field(attempt, "provider_attempt_count")
        if type(count) is not int or count < 0:
            raise ValueError("trace attempt provider_attempt_count is invalid")
        provider_call_count += count
    if provider_call_count != 0:
        raise ValueError(
            "trace-backed protocol run cannot dispatch current provider calls"
        )

    resolver = trace_runtime.resolver
    manifest = resolver.index.manifest
    rows_by_inventory_entry_id = {
        row.inventory_entry_id: row for row in resolver.index.inventory_rows
    }
    consumptions: list[dict[str, Any]] = []
    seen_consumption_ids: set[str] = set()
    for event in _sequence_field(adapter_result, "event_records"):
        if _optional_field(event, "event_type") != "TRACE_DELIVERY_COMMITTED.v1":
            continue
        consumption_id = _required_field(event, "event_id")
        if not isinstance(consumption_id, str) or not consumption_id:
            raise ValueError("trace commit event_id is invalid")
        if consumption_id in seen_consumption_ids:
            raise ValueError("duplicate trace source consumption identity")
        seen_consumption_ids.add(consumption_id)
        payload = _required_field(event, "payload")
        if not isinstance(payload, Mapping):
            raise ValueError("trace commit payload is malformed")
        attempt_id = payload.get("attempt_id")
        wrapper_ref = payload.get("current_wrapper_ref")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValueError("trace commit attempt_id is invalid")
        if not isinstance(wrapper_ref, Mapping):
            raise ValueError("trace commit current_wrapper_ref is invalid")
        delivery = PreparedTraceDelivery.from_dict(
            json.loads(
                store.read_bytes(ArtifactRef.from_dict(wrapper_ref)).decode("utf-8")
            )
        )
        if delivery.attempt_id != attempt_id:
            raise ValueError("trace commit attempt identity mismatch")
        entry = resolver.entry(delivery.entry_id)
        if (
            delivery.bank_root_id != manifest.bank_root_id
            or delivery.manifest_digest != manifest.manifest_digest
            or delivery.inference_request_digest != entry.inference_request_digest
            or delivery.source_terminal_kind != entry.terminal_kind
            or delivery.attempt_ordinal != entry.replacement_slot
        ):
            raise ValueError("trace committed delivery source identity mismatch")
        delivered_locators = tuple(
            sorted(
                (dict(locator) for locator in delivery.source_bank_object_locators),
                key=lambda value: str(value["object_role"]),
            )
        )
        entry_locators = tuple(
            sorted(
                (locator.to_dict() for locator in entry.object_locators),
                key=lambda value: str(value["object_role"]),
            )
        )
        if delivered_locators != entry_locators:
            raise ValueError("trace committed delivery locator set mismatch")
        inventory_row = rows_by_inventory_entry_id.get(entry.inventory_entry_id)
        if inventory_row is None or inventory_row.entry_id != entry.entry_id:
            raise ValueError("trace committed entry inventory identity mismatch")

        request_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="request_body"
        )
        provenance_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="provenance"
        )
        usage_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="usage"
        )
        latency_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="latency"
        )
        pricing_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="pricing"
        )
        acquisition_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="acquisition_attempt"
        )
        model_body = _trace_bank_role_json(
            resolver=resolver, entry=entry, role="model_record"
        )
        request_body_locator = next(
            locator
            for locator in entry.object_locators
            if locator.object_role == "request_body"
        )
        request_model = request_body.get("model")
        configured_model = model_body.get("configured_model")
        requested_model = model_body.get("requested_model")
        resolved_model = model_body.get("resolved_model")
        expected_model = _required_field(condition, "provider_model_id")
        expected_model_entry_id = _required_field(condition, "model_entry_id")
        expected_provider_config_digest = _required_field(
            condition, "source_provider_config_digest"
        )
        if (
            model_body.get("schema_version")
            != "tokenshare.response_bank_model_record.v1"
            or model_body.get("response_model_status") != "matched"
            or not isinstance(request_model, str)
            or not request_model
            or not isinstance(configured_model, str)
            or not configured_model
            or not isinstance(requested_model, str)
            or not requested_model
            or not isinstance(resolved_model, str)
            or not resolved_model
            or configured_model != expected_model
            or requested_model != expected_model
            or resolved_model != expected_model
            or request_model != expected_model
            or request_body_locator.object_digest != inventory_row.body_digest
            or provenance_body.get("schema_version")
            != "tokenshare.response_bank_provenance.v1"
            or provenance_body.get("entry_id") != expected_model_entry_id
            or provenance_body.get("provider_config_digest")
            != expected_provider_config_digest
            or inventory_row.provider_config_digest
            != expected_provider_config_digest
            or provenance_body.get("inference_request_digest")
            != entry.inference_request_digest
        ):
            # source 指标只有在 acquisition model 与冻结 wire identity 完整一致时可用。
            raise ValueError("trace source model identity mismatch")
        usage_schema = usage_body.get("schema_version")
        if usage_schema not in {None, "tokenshare.response_bank_usage.v1"}:
            raise ValueError("trace source usage schema is unsupported")
        usage_status = usage_body.get("usage_status")
        usage = usage_body.get("usage")
        if usage_status == "usage_missing":
            if usage is not None:
                raise ValueError("usage_missing trace source must persist null usage")
            prompt_tokens = completion_tokens = total_tokens = None
        elif usage_status == "reported":
            if not isinstance(usage, Mapping):
                raise ValueError("reported trace source usage must be a mapping")
            prompt_tokens = _trace_usage_int(
                usage.get("prompt_tokens"), "prompt_tokens"
            )
            completion_tokens = _trace_usage_int(
                usage.get("completion_tokens"), "completion_tokens"
            )
            total_tokens = _trace_usage_int(usage.get("total_tokens"), "total_tokens")
            if (
                prompt_tokens is not None
                and completion_tokens is not None
                and total_tokens is not None
                and total_tokens != prompt_tokens + completion_tokens
            ):
                raise ValueError("trace source usage token total mismatch")
        else:
            raise ValueError("trace source usage status is unsupported")

        latency_schema = latency_body.get("schema_version")
        if latency_schema not in {None, "tokenshare.response_bank_latency.v1"}:
            raise ValueError("trace source latency schema is unsupported")
        latency_ms = _trace_usage_int(latency_body.get("latency_ms"), "latency_ms")
        if latency_ms is None or latency_ms != delivery.source_latency_ms:
            raise ValueError("trace source latency does not match committed delivery")
        cost_estimate = None
        pricing_schema = pricing_body.get("schema_version")
        if pricing_schema == "tokenshare.response_bank_pricing.v1":
            if pricing_body.get("currency") != "CNY":
                raise ValueError("trace source pricing currency is unsupported")
            try:
                input_rate = Decimal(
                    str(pricing_body["input_per_million_tokens"])
                )
                output_rate = Decimal(
                    str(pricing_body["output_per_million_tokens"])
                )
            except (KeyError, ArithmeticError, ValueError) as exc:
                raise ValueError("trace source pricing rates are invalid") from exc
            if input_rate < 0 or output_rate < 0:
                raise ValueError("trace source pricing rates must be non-negative")
            if prompt_tokens is not None and completion_tokens is not None:
                cost_estimate = (
                    Decimal(prompt_tokens) * input_rate
                    + Decimal(completion_tokens) * output_rate
                ) / Decimal(1_000_000)
        elif pricing_schema is not None:
            raise ValueError("trace source pricing schema is unsupported")

        acquisition_schema = acquisition_body.get("schema_version")
        if acquisition_schema == "tokenshare.response_bank_acquisition_attempt.v1":
            if (
                acquisition_body.get("terminal_kind") != entry.terminal_kind
                or not isinstance(acquisition_body.get("attempt_id"), str)
                or not acquisition_body.get("attempt_id")
            ):
                raise ValueError("trace source acquisition attempt is invalid")
            source_acquisition_attempt_id = acquisition_body["attempt_id"]
        elif acquisition_schema is None:
            source_acquisition_attempt_id = entry.acquisition_state_ref
        else:
            raise ValueError("trace source acquisition schema is unsupported")

        available_roles = {
            _paper_trace_object_role(locator.object_role)
            for locator in entry.object_locators
        }
        source_roles = tuple(
            role for role in _SOURCE_BANK_METRIC_ROLE_ORDER if role in available_roles
        )
        source_roles += tuple(sorted(available_roles.difference(source_roles)))
        consumptions.append(
            {
                "consumption_id": consumption_id,
                "current_attempt_id": delivery.attempt_id,
                "unit_id": delivery.unit_id,
                "planned_ai_unit_id": inventory_row.planned_ai_unit_id,
                "entry_id": entry.entry_id,
                "replacement_slot": entry.replacement_slot,
                "source_terminal_kind": entry.terminal_kind,
                "source_acquisition_attempt_id": source_acquisition_attempt_id,
                "source_model_record": dict(model_body),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_ms": latency_ms,
                "cost_estimate_cny": (
                    str(cost_estimate) if cost_estimate is not None else None
                ),
                "source_bank_roles": list(source_roles),
            }
        )

    return {
        "schema_version": "tokenshare.paper_trace_source_usage.v1",
        "attribution_kind": "immutable_response_bank",
        "current_provider_call_count": 0,
        "current_provider_spend_cny": "0",
        "committed_consumption_count": len(consumptions),
        "consumptions": consumptions,
    }


def _evaluate_trace_root_evidence(
    *,
    adapter_result: Any,
    adapter_root: Path,
    trace_runtime: Any,
    direct_collector: _CanonicalDirectCollector | None = None,
    condition: Any = None,
    case_id: str | None = None,
):
    """从当前协议 ledger/artifact 与 canonical bank 构造 Task18 事实。"""

    result_root = Path(_required_field(adapter_result, "output_root"))
    store = ArtifactStore(result_root if result_root.is_dir() else adapter_root)
    events = _sequence_field(adapter_result, "event_records")
    request_events = {
        str(_required_field(event, "payload")["attempt_id"]): event
        for event in events
        if _optional_field(event, "event_type") == "EXECUTION_REQUEST_RECORDED"
    }
    verification_events = {
        str(_required_field(event, "payload")["attempt_id"]): event
        for event in events
        if _optional_field(event, "event_type") == "VERIFICATION_RECORDED"
    }
    commits = tuple(
        event
        for event in events
        if _optional_field(event, "event_type") == "TRACE_DELIVERY_COMMITTED.v1"
    )
    canonical_attempt_ids = {
        str(_required_field(event, "payload")["selected_attempt_id"])
        for event in events
        if _optional_field(event, "event_type") == "CANONICAL_OUTPUTS_BOUND"
        and isinstance(
            _required_field(event, "payload").get("selected_attempt_id"), str
        )
    }
    attempt_values = _sequence_field(adapter_result, "attempt_results", "attempts")
    current_provider_call_count = sum(
        int(_optional_field(attempt, "provider_attempt_count") or 0)
        for attempt in attempt_values
    )
    current_real_provider_attempt_refs = tuple(
        {
            "attempt_id": str(_required_field(attempt, "attempt_id")),
            "provider_attempt_count": int(
                _optional_field(attempt, "provider_attempt_count") or 0
            ),
        }
        for attempt in attempt_values
        if int(_optional_field(attempt, "provider_attempt_count") or 0) > 0
    )
    commit_candidates: list[dict[str, Any]] = []
    for commit in commits:
        payload = dict(_required_field(commit, "payload"))
        attempt_id = str(payload["attempt_id"])
        request_payload = dict(_required_field(request_events[attempt_id], "payload"))
        request_ref = ArtifactRef.from_dict(request_payload["request_ref"])
        request_body = json.loads(store.read_bytes(request_ref).decode("utf-8"))
        planned_ai_unit_id = str(request_body["soft_hints"]["planned_ai_unit_id"])
        delivery = PreparedTraceDelivery.from_dict(
            json.loads(
                store.read_bytes(
                    ArtifactRef.from_dict(payload["current_wrapper_ref"])
                ).decode("utf-8")
            )
        )
        commit_candidates.append(
            {
                "commit": commit,
                "payload": payload,
                "attempt_id": attempt_id,
                "request_body": request_body,
                "planned_ai_unit_id": planned_ai_unit_id,
                "delivery": delivery,
            }
        )

    # 一个协议 unit 可能提交多个 replacement；Task18 的 executed/current 事实
    # 只取 canonical attempt，若无 canonical 则取最大 attempt ordinal 的终态提交。
    authoritative_by_unit: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in commit_candidates:
        delivery = candidate["delivery"]
        key = (delivery.unit_id, candidate["planned_ai_unit_id"])
        previous = authoritative_by_unit.get(key)
        candidate_is_canonical = candidate["attempt_id"] in canonical_attempt_ids
        previous_is_canonical = (
            previous is not None
            and previous["attempt_id"] in canonical_attempt_ids
        )
        if (
            previous is None
            or (candidate_is_canonical and not previous_is_canonical)
            or (
                candidate_is_canonical == previous_is_canonical
                and delivery.attempt_ordinal
                > previous["delivery"].attempt_ordinal
            )
        ):
            authoritative_by_unit[key] = candidate

    executed_bindings: list[dict[str, Any]] = []
    wrappers: list[dict[str, Any]] = []
    typed_wrappers: list[CurrentTraceWrapper] = []
    executed_planned_ids: list[str] = []
    for candidate in sorted(
        authoritative_by_unit.values(),
        key=lambda item: (item["planned_ai_unit_id"], item["delivery"].unit_id),
    ):
        commit = candidate["commit"]
        payload = candidate["payload"]
        attempt_id = candidate["attempt_id"]
        request_body = candidate["request_body"]
        planned_ai_unit_id = candidate["planned_ai_unit_id"]
        delivery = candidate["delivery"]
        executed_planned_ids.append(planned_ai_unit_id)
        executed_bindings.append(
            build_ai_unit_binding_from_request(
                planned_ai_unit_id=planned_ai_unit_id,
                request_body=request_body,
                store=store,
                include_request_artifacts=False,
            )
        )
        domain = executed_bindings[-1]["domain_unit_commitment"]["domain"]
        checker_refs = tuple(payload.get("verifier_checker_refs", ()))
        verification = verification_events.get(attempt_id)
        typed_wrapper = CurrentTraceWrapper(
                current_run_id=delivery.current_run_id,
                current_task_id=delivery.task_id,
                current_unit_id=delivery.unit_id,
                current_attempt_id=delivery.attempt_id,
                attempt_ordinal=delivery.attempt_ordinal,
                bank_root_id=delivery.bank_root_id,
                manifest_digest=delivery.manifest_digest,
                root_binding_marker_digest=(
                    trace_runtime.resolver.index.manifest.root_binding_marker_digest
                ),
                inference_request_digest=delivery.inference_request_digest,
                entry_id=delivery.entry_id,
                locator_digests={
                    str(locator["object_role"]): str(locator["object_digest"])
                    for locator in delivery.source_bank_object_locators
                },
                logical_started_at=f"logical:{delivery.logical_start_ms}",
                logical_finished_at=f"logical:{delivery.logical_finish_ms}",
                source_latency_ms=delivery.source_latency_ms,
                current_parse_ref=_artifact_identity(payload.get("parser_result_ref")),
                current_verifier_ref=(
                    str(_required_field(verification, "event_id"))
                    if domain == "factorization" and verification is not None
                    else None
                ),
                current_checker_ref=(
                    _artifact_identity(checker_refs[0])
                    if domain == "lean_proof" and checker_refs
                    else None
                ),
                current_canonical_ref=_artifact_identity(payload.get("canonical_ref")),
                current_ledger_ref=str(_required_field(commit, "event_id")),
            )
        typed_wrappers.append(typed_wrapper)
        wrappers.append(typed_wrapper.to_dict())
    bindings_by_planned = {
        binding.planned_ai_unit_id: binding for binding in trace_runtime.bindings
    }
    manifest = trace_runtime.resolver.index.manifest
    facts = PaperEvidenceEligibilityFacts(
        evidence_class="real_model_trace_protocol_run",
        source_classification="approved_real_full_acquisition",
        executed_ai_unit_count=len(executed_bindings),
        executed_unit_bindings=tuple(executed_bindings),
        current_provider_call_count=current_provider_call_count,
        source_provider_call_count=len(trace_runtime.resolver.index.entries),
        current_real_provider_attempt_refs=current_real_provider_attempt_refs,
        current_lifecycle_refs=tuple(wrappers),
        trace_source_bindings=tuple(
            bindings_by_planned[planned_id].to_dict()
            for planned_id in executed_planned_ids
        ),
        source_manifest=manifest.to_dict(),
        source_inventory_rows=tuple(
            row.to_dict() for row in trace_runtime.resolver.index.inventory_rows
        ),
        source_entries=tuple(
            entry.to_dict() for entry in trace_runtime.resolver.index.entries
        ),
        paid_receipt_claim=trace_runtime.paid_receipt_claim,
        direct_evidence_complete=True,
        identity_consistent=True,
        regression_only=False,
    )
    if direct_collector is not None:
        if condition is None or not isinstance(case_id, str) or not case_id:
            raise ValueError("trace direct evidence identity is missing")
        inventory_row = direct_collector.rows_by_key.get(
            (str(condition.condition_id), case_id)
        )
        if inventory_row is None:
            raise ValueError("trace direct evidence inventory row is missing")
        selected_bindings = tuple(
            bindings_by_planned[planned_id] for planned_id in executed_planned_ids
        )
        with direct_collector.lock:
            root_id = inventory_row.preregistered_root_run_id
            direct_collector.current_trace_wrappers_by_root[root_id] = tuple(
                typed_wrappers
            )
            direct_collector.trace_source_bindings_by_root[root_id] = (
                selected_bindings
            )
            direct_collector.eligibility_facts_by_root[root_id] = facts
            direct_collector.source_resolvers[manifest.bank_root_id] = (
                _PaperTraceSourceResolver(trace_runtime.resolver)
            )
    return evaluate_versioned_paper_evidence(facts)


def _paired_trace_reference_from_runtime(
    *,
    trace_runtime: Any,
    case_id: str,
) -> dict[str, Any]:
    """从 validated bank index 构造 Exp3 同 sample 的 opaque reference。"""

    bindings = tuple(trace_runtime.bindings)
    sample_slots = {binding.sample_slot_index for binding in bindings}
    if len(sample_slots) != 1:
        raise ValueError("Exp3 paired trace reference must use the same sample slot")
    manifest = trace_runtime.resolver.index.manifest
    entries: list[Any] = []
    for binding in bindings:
        if (
            binding.bank_root_id != manifest.bank_root_id
            or binding.manifest_digest != manifest.manifest_digest
        ):
            raise ValueError("Exp3 paired trace reference root binding mismatch")
        for replacement in binding.replacements:
            entry = trace_runtime.resolver.entry(replacement.entry_id)
            if (
                entry.sample_slot_index != binding.sample_slot_index
                or entry.replacement_slot != replacement.replacement_slot
                or entry.inference_request_digest
                != replacement.inference_request_digest
            ):
                raise ValueError("Exp3 paired trace reference entry identity mismatch")
            entries.append(entry)
    entries.sort(key=lambda item: (item.entry_id, item.replacement_slot))
    return {
        "schema_version": "tokenshare.paper_exp3_paired_trace_reference.v1",
        "comparison_kind": "paired_trace_reference",
        "case_id": case_id,
        "sample_slot_index": next(iter(sample_slots)),
        "bank_root_id": manifest.bank_root_id,
        "manifest_digest": manifest.manifest_digest,
        "source_entry_ids": [entry.entry_id for entry in entries],
        "source_acquisition_state_refs": [
            entry.acquisition_state_ref for entry in entries
        ],
        "source_binding_digests": [
            binding.binding_digest for binding in bindings
        ],
        "source_bank_object_locators": [
            locator.to_dict()
            for entry in entries
            for locator in entry.object_locators
        ],
    }


@dataclass(frozen=True, kw_only=True)
class _RootExecutionOutcome:
    case_id: str
    root_status: str
    adapter_root: Path
    worker_id: str
    task: Any = None
    adapter_result: Any = None
    provider_attempt_count: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    cost_estimate_currency: str | None = None
    cost_estimate_status: str | None = None
    paper_eligible: bool = False
    provider_latency_ms: float | None = 0.0
    provider_latency_evidence_status: str = "not_applicable"
    provider_latency_unavailable_reason: str | None = "no_provider_attempts"
    provider_error_kind: str | None = None
    error: Exception | None = None
    runtime_records: tuple[dict[str, Any], ...] = ()
    experiment_records: tuple[dict[str, Any], ...] = ()
    matched_baseline_evidence_ref: dict[str, Any] | None = None


@dataclass
class _ConditionTraceClock:
    """为一个 condition 按 worker lane 原子分配 trace 逻辑区间。"""

    origin: datetime | None = None
    lane_elapsed: dict[str, timedelta] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock, repr=False)

    def reserve(
        self,
        *,
        worker_id: object,
        root_origin: datetime,
        root_duration: timedelta,
    ) -> tuple[datetime, timedelta]:
        lane_id = str(worker_id)
        if not lane_id:
            raise ValueError("trace condition worker lane id is required")
        with self.lock:
            if self.origin is None:
                self.origin = root_origin
            elif self.origin != root_origin:
                raise ValueError("trace roots must share one logical clock origin")
            lane_start = self.lane_elapsed.get(lane_id, timedelta(0))
            self.lane_elapsed[lane_id] = lane_start + root_duration
            return self.origin, lane_start


def _condition_scoped_trace_runtime(
    outcome: _RootExecutionOutcome,
    *,
    condition_clock: _ConditionTraceClock,
    worker_id: object,
) -> _RootExecutionOutcome:
    """把独立 root 的 trace clock 投影到 condition 内的顺序逻辑时钟。"""

    if outcome.error is not None or outcome.task is None:
        return outcome
    task = _as_json(outcome.task)
    observation = task.get("runtime_observation")
    if not isinstance(observation, Mapping) and outcome.adapter_result is not None:
        run_evidence = _optional_field(outcome.adapter_result, "run_evidence")
        protocol_runtime = (
            run_evidence.get("protocol_runtime")
            if isinstance(run_evidence, Mapping)
            else None
        )
        observation = (
            protocol_runtime.get("runtime_observation")
            if isinstance(protocol_runtime, Mapping)
            else None
        )
    if not isinstance(observation, Mapping):
        return outcome
    started_at = observation.get("runtime_started_at")
    ended_at = observation.get("runtime_ended_at")
    facts = observation.get("worker_execution_facts")
    if (
        not isinstance(started_at, str)
        or not isinstance(ended_at, str)
        or not isinstance(facts, Sequence)
        or isinstance(facts, (str, bytes))
    ):
        return outcome

    root_started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    root_ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    if root_ended < root_started:
        raise ValueError("trace runtime observation ended before it started")
    root_duration = root_ended - root_started
    shared_origin, lane_start = condition_clock.reserve(
        worker_id=worker_id,
        root_origin=root_started,
        root_duration=root_duration,
    )
    rebased_start = shared_origin + lane_start

    def rebase_timestamp(value: object) -> object:
        if not isinstance(value, str):
            return value
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (rebased_start + (timestamp - root_started)).isoformat().replace(
            "+00:00", "Z"
        )

    rebased_facts: list[dict[str, Any]] = []
    for raw_fact in facts:
        if not isinstance(raw_fact, Mapping):
            raise ValueError("trace runtime worker fact must be a mapping")
        fact = dict(raw_fact)
        fact["started_at"] = rebase_timestamp(fact.get("started_at"))
        fact["ended_at"] = rebase_timestamp(fact.get("ended_at"))
        if fact.get("kill_progress_observed_at") is not None:
            fact["kill_progress_observed_at"] = rebase_timestamp(
                fact["kill_progress_observed_at"]
            )
        rebased_facts.append(fact)
    rebased_observation = dict(observation)
    rebased_observation["runtime_started_at"] = rebase_timestamp(started_at)
    rebased_observation["runtime_ended_at"] = rebase_timestamp(ended_at)
    if observation.get("witness_observed_at") is not None:
        rebased_observation["witness_observed_at"] = rebase_timestamp(
            observation["witness_observed_at"]
        )
    rebased_observation["worker_execution_facts"] = rebased_facts
    task["runtime_observation"] = rebased_observation
    return replace(outcome, task=task, runtime_records=tuple(rebased_facts))


def _provider_latency_observation(
    *,
    attempts: Sequence[Any],
    expected_provider_attempt_count: int,
) -> tuple[float | None, str, str | None]:
    """只在真实 provider attempt 的 latency 全部存在时返回聚合值。"""

    if (
        isinstance(expected_provider_attempt_count, bool)
        or not isinstance(expected_provider_attempt_count, int)
        or expected_provider_attempt_count < 0
    ):
        raise ValueError("expected_provider_attempt_count must be non-negative")
    observed_provider_attempt_count = 0
    latency_sum_ms = 0.0
    missing_latency = False
    for attempt in attempts:
        raw_count = _optional_field(attempt, "provider_attempt_count")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
            return None, "incomplete", "provider_attempt_inventory_mismatch"
        if raw_count == 0:
            continue
        observed_provider_attempt_count += raw_count
        latency_ms = _optional_field(attempt, "latency_ms")
        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or latency_ms < 0
        ):
            missing_latency = True
            continue
        latency_sum_ms += float(latency_ms)
    if observed_provider_attempt_count != expected_provider_attempt_count:
        return None, "incomplete", "provider_attempt_inventory_mismatch"
    if expected_provider_attempt_count == 0:
        return 0.0, "not_applicable", "no_provider_attempts"
    if missing_latency:
        return None, "incomplete", "missing_provider_latency_evidence"
    return latency_sum_ms, "complete", None


def _runtime_final_artifact_ref(
    *,
    events: Sequence[Any],
    root_unit_id: str,
) -> ArtifactRef:
    matches: list[ArtifactRef] = []
    for event in events:
        if _status_value(event.event_type) != "MERGE_RECORDED":
            continue
        payload = event.payload
        if payload.get("parent_unit_id") != root_unit_id:
            continue
        refs = payload.get("merge_output_refs")
        if isinstance(refs, Mapping):
            preferred = refs.get("prime_factorization_result")
            if not isinstance(preferred, Mapping):
                preferred = refs.get("proof")
            if isinstance(preferred, Mapping):
                matches.append(ArtifactRef.from_dict(preferred))
            elif len(refs) == 1:
                value = next(iter(refs.values()))
                if isinstance(value, Mapping):
                    matches.append(ArtifactRef.from_dict(value))
    if len(matches) != 1:
        raise ValueError("canonical direct evidence requires one merged final artifact")
    return matches[0]


def _copy_runtime_artifact_store(
    *,
    native_store: ArtifactStore,
    target_store: ArtifactStore,
    execution_id: str,
    task_id: str,
    final_artifact_id: str | None,
) -> dict[str, ArtifactRef]:
    copied: dict[str, ArtifactRef] = {}
    for manifest_path in sorted(native_store.artifact_dir.glob("*.manifest.json")):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        ref = ArtifactRef.from_dict(raw)
        role = (
            "final_result"
            if ref.artifact_id == final_artifact_id
            else "protocol_runtime_artifact"
        )
        copied[ref.artifact_id] = target_store.save_bytes(
            native_store.read_bytes(ref),
            artifact_id=ref.artifact_id,
            artifact_type=ref.artifact_type,
            media_type=ref.media_type,
            artifact_schema_id=ref.artifact_schema_id,
            artifact_schema_version=ref.artifact_schema_version,
            source={
                "kind": "canonical_direct_runtime_projection",
                "role": role,
                "task_id": task_id,
                "execution_id": execution_id,
                "source_artifact_ref": ref.to_dict(),
            },
            metadata=dict(ref.metadata),
            created_at=ref.created_at,
        )
    return copied


def _copy_native_direct_role_artifact(
    *,
    native_store: ArtifactStore,
    target_store: ArtifactStore,
    native_ref: ArtifactRef,
    root_id: str,
    role: str,
    execution_id: str,
    task_id: str,
    ordinal: int,
) -> ArtifactRef:
    authoritative = native_store.load_artifact_ref(native_ref.artifact_id)
    if authoritative.to_dict() != native_ref.to_dict():
        raise ValueError("canonical direct native artifact identity mismatch")
    return target_store.save_bytes(
        native_store.read_bytes(authoritative),
        artifact_id=(
            "direct_"
            + role
            + "_"
            + hashlib.sha256(
                (
                    root_id
                    + "\0"
                    + str(ordinal)
                    + "\0"
                    + authoritative.artifact_id
                    + "\0"
                    + authoritative.content_hash
                ).encode("utf-8")
            ).hexdigest()[:24]
        ),
        artifact_type=authoritative.artifact_type,
        media_type=authoritative.media_type,
        artifact_schema_id=authoritative.artifact_schema_id,
        artifact_schema_version=authoritative.artifact_schema_version,
        source={
            "kind": "canonical_direct_native_artifact_projection",
            "role": role,
            "task_id": task_id,
            "execution_id": execution_id,
            "source_artifact_ref": authoritative.to_dict(),
        },
        metadata=dict(authoritative.metadata),
        created_at=authoritative.created_at,
    )


def _native_direct_role_refs(
    *,
    native_store: ArtifactStore,
    target_store: ArtifactStore,
    root_id: str,
    execution_id: str,
    task_id: str,
    task: Any,
    adapter_result: Any,
) -> tuple[
    tuple[ArtifactRef, ...],
    tuple[ArtifactRef, ...],
    tuple[ArtifactRef, ...],
    ArtifactRef | None,
]:
    del task
    run_evidence = _optional_field(adapter_result, "run_evidence")
    bundle = (
        run_evidence.get("paper_direct_native_artifacts")
        if isinstance(run_evidence, Mapping)
        else None
    )
    if not isinstance(bundle, Mapping):
        raise ValueError("native paper direct artifact bundle is missing")
    if bundle.get("schema_version") != "tokenshare.paper_direct_native_artifacts.v1":
        raise ValueError("unsupported native paper direct artifact bundle")

    def copy_bound_ref(
        value: Any,
        *,
        allowed_roles: frozenset[str],
        ordinal: int,
    ) -> ArtifactRef:
        if not isinstance(value, Mapping):
            raise ValueError("native paper direct artifact ref is missing")
        native_ref = ArtifactRef.from_dict(value)
        role = str(native_ref.source.get("role", ""))
        if role not in allowed_roles:
            raise ValueError("native paper direct artifact role mismatch")
        if native_ref.source.get("execution_id") != execution_id:
            raise ValueError("native paper direct execution binding mismatch")
        if native_ref.source.get("task_id") != task_id:
            raise ValueError("native paper direct task binding mismatch")
        return _copy_native_direct_role_artifact(
            native_store=native_store,
            target_store=target_store,
            native_ref=native_ref,
            root_id=root_id,
            role=role,
            execution_id=execution_id,
            task_id=task_id,
            ordinal=ordinal,
        )

    resource_value = bundle.get("actual_resource_book_ref")
    resource_body: Mapping[str, Any] | None = None
    if resource_value is not None:
        if not isinstance(resource_value, Mapping):
            raise ValueError("native actual resource book ref is invalid")
        native_resource_ref = ArtifactRef.from_dict(resource_value)
        if native_resource_ref.source.get("role") != "actual_resource_book":
            raise ValueError("native actual resource book role mismatch")
        loaded_resource = json.loads(native_store.read_bytes(native_resource_ref))
        if not isinstance(loaded_resource, Mapping) or loaded_resource.get(
            "schema_version"
        ) != "tokenshare.paper_actual_resource_book.v1":
            raise ValueError("unsupported native actual resource book")
        if loaded_resource.get("execution_id") != execution_id:
            raise ValueError("native actual resource execution binding mismatch")
        if loaded_resource.get("task_id") != task_id:
            raise ValueError("native actual resource task binding mismatch")
        resource_body = loaded_resource
    provider_values = (
        resource_body.get("current_provider_object_refs", [])
        if resource_body is not None
        else []
    )
    if not isinstance(provider_values, list):
        raise ValueError("native provider object inventory is invalid")
    expected_provider_roles = frozenset(
        {
            "request_body",
            "raw_output_or_provider_failure",
            "provenance",
            "usage_status",
            "latency",
            "pricing",
            "provider_attempt",
            "model_record",
        }
    )
    provider_refs = tuple(
        copy_bound_ref(
            value,
            allowed_roles=expected_provider_roles,
            ordinal=ordinal,
        )
        for ordinal, value in enumerate(provider_values)
    )
    if provider_refs and (
        frozenset(str(ref.source.get("role", "")) for ref in provider_refs)
        != expected_provider_roles
        or len(provider_refs) != len(expected_provider_roles)
    ):
        raise ValueError("native provider object role inventory is incomplete")
    parser_values = bundle.get("parser_refs", [])
    if not isinstance(parser_values, list):
        raise ValueError("native parser object inventory is invalid")
    parser_roles = frozenset({"parser_result", "parse_failure"})
    parser_refs = tuple(
        copy_bound_ref(
            value,
            allowed_roles=parser_roles,
            ordinal=ordinal,
        )
        for ordinal, value in enumerate(parser_values)
    )
    verdict_value = bundle.get("independent_verdict_ref")
    verifier_refs = (
        (
            copy_bound_ref(
                verdict_value,
                allowed_roles=frozenset({"independent_verdict"}),
                ordinal=0,
            ),
        )
        if verdict_value is not None
        else ()
    )
    copied_resource_ref = (
        copy_bound_ref(
            resource_value,
            allowed_roles=frozenset({"actual_resource_book"}),
            ordinal=0,
        )
        if resource_value is not None
        else None
    )
    return parser_refs, verifier_refs, provider_refs, copied_resource_ref


def _persist_trace_resource_book(
    *,
    native_store: ArtifactStore,
    canonical_store: ArtifactStore,
    root_id: str,
    execution_id: str,
    task_id: str,
    current_wrappers: Sequence[Any],
    native_wrapper_refs: Sequence[ArtifactRef],
    canonical_wrapper_refs: Sequence[ArtifactRef],
    source_locators: Sequence[ExternalBankObjectLocator],
) -> ArtifactRef:
    """单 entry 保留 v1 内容；多 entry 用 v2 root book 串起所有 replacement。"""

    wrappers = tuple(current_wrappers)
    native_refs = tuple(native_wrapper_refs)
    canonical_refs = tuple(canonical_wrapper_refs)
    locators = tuple(source_locators)
    if not wrappers or len(wrappers) != len(native_refs) or len(wrappers) != len(
        canonical_refs
    ):
        raise ValueError("canonical trace wrapper projection is incomplete")
    for native_ref in native_refs:
        authoritative = native_store.load_artifact_ref(native_ref.artifact_id)
        if authoritative.to_dict() != native_ref.to_dict():
            raise ValueError("native trace wrapper identity mismatch")
    entry_ids = tuple(sorted({locator.entry_id for locator in locators}))
    if not entry_ids:
        raise ValueError("canonical trace resource book has no source entries")
    native_last = native_refs[-1]
    artifact_id = (
        "direct_trace_resource_book_"
        + hashlib.sha256(root_id.encode("utf-8")).hexdigest()[:24]
    )
    if len(wrappers) == 1 and len(entry_ids) == 1:
        return canonical_store.save_json(
            wrappers[0].to_dict(),
            artifact_id=artifact_id,
            artifact_type=native_last.artifact_type,
            artifact_schema_id=native_last.artifact_schema_id,
            artifact_schema_version=native_last.artifact_schema_version,
            source={
                "kind": "canonical_trace_wrapper_role_projection",
                "role": "trace_resource_book",
                "task_id": task_id,
                "execution_id": execution_id,
                "source_artifact_ref": native_last.to_dict(),
            },
            metadata=dict(native_last.metadata),
            created_at=native_last.created_at,
        )
    return canonical_store.save_json(
        {
            "schema_version": "tokenshare.paper_trace_resource_book.v2",
            "execution_id": execution_id,
            "task_id": task_id,
            "preregistered_root_run_id": root_id,
            "replacement_entry_ids": list(entry_ids),
            "current_wrappers": [wrapper.to_dict() for wrapper in wrappers],
            "current_wrapper_refs": [ref.to_dict() for ref in canonical_refs],
            "source_bank_object_locators": [
                locator.to_dict() for locator in locators
            ],
        },
        artifact_id=artifact_id,
        artifact_type="PaperTraceResourceBook",
        artifact_schema_id="tokenshare.paper_trace_resource_book.v2",
        artifact_schema_version="v2",
        source={
            "kind": "canonical_trace_root_resource_book_projection",
            "role": "trace_resource_book",
            "task_id": task_id,
            "execution_id": execution_id,
            "source_artifact_refs": [ref.to_dict() for ref in native_refs],
        },
        metadata={
            "replacement_entry_count": len(entry_ids),
            "current_wrapper_count": len(wrappers),
        },
        created_at=native_last.created_at,
    )


def _canonical_direct_evidence_digest(
    evidence: CanonicalDirectRootEvidence,
) -> str:
    def optional(value: Any) -> Any:
        return value.to_dict() if value is not None else None

    return digest_json(
        {
            "root_id": evidence.preregistered_root_run_id,
            "evidence_class": evidence.evidence_class,
            "execution_binding": evidence.execution_binding.to_dict(),
            "runtime_status": evidence.canonical_runtime_status,
            "final": optional(evidence.final_result_ref),
            "terminal": optional(evidence.terminal_root_event_ref),
            "canonical": optional(evidence.canonical_acceptance_ref),
            "merge": optional(evidence.merge_ref),
            "correct": evidence.independently_verified_correct,
            "complete": evidence.paper_evidence_complete,
            "identity_consistent": evidence.identity_consistent,
            "infrastructure_valid": evidence.infrastructure_valid,
            "parser": [value.to_dict() for value in evidence.parser_refs],
            "verifier": [
                value.to_dict() for value in evidence.verifier_checker_refs
            ],
            "provider": [
                value.to_dict()
                for value in evidence.current_provider_object_refs
            ],
            "locators": [
                value.to_dict() for value in evidence.source_bank_object_locators
            ],
            "actual_resource": optional(evidence.actual_resource_book_ref),
            "trace_resource": optional(evidence.trace_resource_book_ref),
            "reasons": list(evidence.ineligibility_reasons),
        }
    )


def _persist_canonical_direct_checkpoint(
    *,
    projection_root: Path,
    row: PaperDirectRootInventoryRow,
    evidence: CanonicalDirectRootEvidence,
    native_ledger: EventLedger,
    collector: _CanonicalDirectCollector,
) -> None:
    ledger_relative = Path("events") / native_ledger.path.name
    _atomic_write_bytes(
        projection_root / ledger_relative,
        native_ledger.path.read_bytes(),
    )
    root_id = row.preregistered_root_run_id
    adjunct = {
        "current_trace_wrappers": collector.current_trace_wrappers_by_root.get(
            root_id,
            (),
        ),
        "trace_source_bindings": collector.trace_source_bindings_by_root.get(
            root_id,
            (),
        ),
        "eligibility_facts": collector.eligibility_facts_by_root.get(root_id),
        "source_resolvers": dict(collector.source_resolvers),
        "producer_facts": collector.producer_facts_by_root.get(root_id),
    }
    adjunct_content = pickle.dumps(adjunct, protocol=5)
    adjunct_name = "canonical_direct_adjunct.pickle"
    _atomic_write_bytes(projection_root / adjunct_name, adjunct_content)
    _atomic_write_json(
        projection_root / "canonical_direct_checkpoint.json",
        {
            "schema_version": "tokenshare.canonical_direct_checkpoint.v1",
            "preregistered_root_run_id": root_id,
            "inventory_row_digest": row.inventory_row_digest,
            "execution_id": evidence.execution_binding.execution_id,
            "task_id": evidence.execution_binding.task_id,
            "root_unit_id": evidence.execution_binding.root_unit_id,
            "ledger_path": ledger_relative.as_posix(),
            "final_artifact_id": (
                evidence.final_result_ref.artifact_id
                if evidence.final_result_ref is not None
                else None
            ),
            "parser_artifact_ids": [
                value.artifact_id for value in evidence.parser_refs
            ],
            "verifier_artifact_ids": [
                value.artifact_id for value in evidence.verifier_checker_refs
            ],
            "provider_artifact_ids": [
                value.artifact_id
                for value in evidence.current_provider_object_refs
            ],
            "source_bank_object_locators": [
                value.to_dict() for value in evidence.source_bank_object_locators
            ],
            "actual_resource_artifact_id": (
                evidence.actual_resource_book_ref.artifact_id
                if evidence.actual_resource_book_ref is not None
                else None
            ),
            "trace_resource_artifact_id": (
                evidence.trace_resource_book_ref.artifact_id
                if evidence.trace_resource_book_ref is not None
                else None
            ),
            "evidence_digest": _canonical_direct_evidence_digest(evidence),
            "adjunct_path": adjunct_name,
            "adjunct_digest": _sha256_bytes(adjunct_content),
        },
    )


def _restore_canonical_direct_checkpoints(
    *,
    suite_root: Path,
    collector: _CanonicalDirectCollector,
) -> None:
    projection_base = suite_root.with_name(
        suite_root.name + ".canonical_direct_evidence"
    )
    if not projection_base.is_dir():
        return
    rows_by_id = {
        row.preregistered_root_run_id: row for row in collector.inventory.rows
    }
    for checkpoint_path in sorted(
        projection_base.glob("*/canonical_direct_checkpoint.json")
    ):
        checkpoint = _required_json_object(
            checkpoint_path,
            label="canonical direct checkpoint",
        )
        if checkpoint.get("schema_version") != (
            "tokenshare.canonical_direct_checkpoint.v1"
        ):
            raise ValueError("unsupported canonical direct checkpoint")
        root_id = str(checkpoint["preregistered_root_run_id"])
        row = rows_by_id.get(root_id)
        if row is None or checkpoint.get("inventory_row_digest") != (
            row.inventory_row_digest
        ):
            raise ValueError("canonical direct checkpoint inventory mismatch")
        projection_root = checkpoint_path.parent
        store = ArtifactStore(projection_root)

        def load_optional(artifact_id: Any) -> ArtifactRef | None:
            return (
                store.load_artifact_ref(str(artifact_id))
                if artifact_id is not None
                else None
            )

        ledger = EventLedger(projection_root / str(checkpoint["ledger_path"]))
        runtime = project_protocol_run(
            run_id=str(checkpoint["execution_id"]),
            task_id=str(checkpoint["task_id"]),
            root_unit_id=str(checkpoint["root_unit_id"]),
            event_ledger=ledger,
            artifact_store=store,
        )
        runtime = replace(
            runtime,
            artifact_refs=tuple(
                store.load_artifact_ref(ref.artifact_id)
                for ref in runtime.artifact_refs
            ),
        )
        locators = tuple(
            ExternalBankObjectLocator(**value)
            for value in checkpoint.get("source_bank_object_locators", [])
        )
        evidence = build_canonical_direct_evidence(
            inventory_row=row,
            execution_id=str(checkpoint["execution_id"]),
            event_ledger=ledger,
            artifact_store=store,
            runtime_result=runtime,
            final_result_ref=load_optional(checkpoint.get("final_artifact_id")),
            parser_refs=tuple(
                store.load_artifact_ref(str(value))
                for value in checkpoint.get("parser_artifact_ids", [])
            ),
            verifier_checker_refs=tuple(
                store.load_artifact_ref(str(value))
                for value in checkpoint.get("verifier_artifact_ids", [])
            ),
            current_provider_object_refs=tuple(
                store.load_artifact_ref(str(value))
                for value in checkpoint.get("provider_artifact_ids", [])
            ),
            source_bank_object_locators=locators,
            actual_resource_book_ref=load_optional(
                checkpoint.get("actual_resource_artifact_id")
            ),
            trace_resource_book_ref=load_optional(
                checkpoint.get("trace_resource_artifact_id")
            ),
        )
        if _canonical_direct_evidence_digest(evidence) != checkpoint.get(
            "evidence_digest"
        ):
            raise ValueError("canonical direct checkpoint evidence mismatch")
        adjunct_path = projection_root / str(checkpoint["adjunct_path"])
        adjunct_content = adjunct_path.read_bytes()
        if _sha256_bytes(adjunct_content) != checkpoint.get("adjunct_digest"):
            raise ValueError("canonical direct checkpoint adjunct mismatch")
        adjunct = pickle.loads(adjunct_content)
        if not isinstance(adjunct, Mapping):
            raise ValueError("canonical direct checkpoint adjunct is invalid")
        collector.evidence.append(evidence)
        collector.current_provider_object_files.update(
            {
                ref.artifact_id: projection_root / ref.uri
                for ref in tuple(
                    store.load_artifact_ref(str(value))
                    for value in checkpoint.get("provider_artifact_ids", [])
                )
            }
        )
        wrappers = tuple(adjunct.get("current_trace_wrappers", ()))
        bindings = tuple(adjunct.get("trace_source_bindings", ()))
        facts = adjunct.get("eligibility_facts")
        if wrappers:
            collector.current_trace_wrappers_by_root[root_id] = wrappers
        if bindings:
            collector.trace_source_bindings_by_root[root_id] = bindings
        if facts is not None:
            collector.eligibility_facts_by_root[root_id] = facts
        resolvers = adjunct.get("source_resolvers", {})
        if not isinstance(resolvers, Mapping):
            raise ValueError("canonical direct checkpoint resolvers are invalid")
        collector.source_resolvers.update(resolvers)
        producer_facts = adjunct.get("producer_facts")
        if producer_facts is not None:
            if not isinstance(producer_facts, Mapping):
                raise ValueError("canonical direct producer facts are invalid")
            collector.producer_facts_by_root[root_id] = producer_facts


def _capture_canonical_direct_evidence(
    *,
    collector: _CanonicalDirectCollector,
    suite_root: Path,
    condition: Any,
    case_id: str,
    task: Any,
    adapter_result: Any,
) -> None:
    row = collector.rows_by_key.get((condition.condition_id, case_id))
    if row is None or row.evidence_class not in {
        "online_real_provider",
        "real_model_trace_protocol_run",
    }:
        return
    protocol_runtime = _optional_field(adapter_result, "run_evidence")
    protocol_runtime = (
        protocol_runtime.get("protocol_runtime")
        if isinstance(protocol_runtime, Mapping)
        else None
    )
    if not isinstance(protocol_runtime, Mapping):
        return
    execution_id = str(protocol_runtime["run_id"])
    protocol_task_id = str(protocol_runtime["task_id"])
    root_unit_id = str(protocol_runtime["root_unit_id"])
    native_root = Path(str(_required_field(adapter_result, "output_root")))
    native_store = ArtifactStore(native_root)
    native_ledger = EventLedger(
        native_root / "events" / f"{protocol_task_id}.jsonl"
    )
    events = tuple(native_ledger.read_all())
    if not events or not native_ledger.verify_hash_chain():
        raise ValueError("canonical direct evidence native ledger is invalid")
    native_runtime = project_protocol_run(
        run_id=execution_id,
        task_id=protocol_task_id,
        root_unit_id=root_unit_id,
        event_ledger=native_ledger,
        artifact_store=native_store,
    )
    final_native: ArtifactRef | None
    if _status_value(native_runtime.status) == "completed":
        final_native = _runtime_final_artifact_ref(
            events=events,
            root_unit_id=root_unit_id,
        )
    else:
        final_native = None
    projection_root = (
        suite_root.with_name(suite_root.name + ".canonical_direct_evidence")
        / hashlib.sha256(row.preregistered_root_run_id.encode("utf-8")).hexdigest()
    )
    canonical_store = ArtifactStore(projection_root)
    copied = _copy_runtime_artifact_store(
        native_store=native_store,
        target_store=canonical_store,
        execution_id=execution_id,
        task_id=protocol_task_id,
        final_artifact_id=(
            final_native.artifact_id if final_native is not None else None
        ),
    )
    final_ref = (
        copied[final_native.artifact_id] if final_native is not None else None
    )
    runtime_result = replace(
        native_runtime,
        artifact_refs=tuple(
            copied[ref.artifact_id] for ref in native_runtime.artifact_refs
        ),
    )
    (
        parser_refs,
        verifier_refs,
        provider_refs,
        actual_resource_book_ref,
    ) = _native_direct_role_refs(
        native_store=native_store,
        target_store=canonical_store,
        root_id=row.preregistered_root_run_id,
        execution_id=execution_id,
        task_id=protocol_task_id,
        task=task,
        adapter_result=adapter_result,
    )
    source_locators: tuple[ExternalBankObjectLocator, ...] = ()
    trace_resource_book_ref: ArtifactRef | None = None
    if row.evidence_class == "real_model_trace_protocol_run":
        with collector.lock:
            trace_bindings = collector.trace_source_bindings_by_root.get(
                row.preregistered_root_run_id,
                (),
            )
            resolvers = dict(collector.source_resolvers)
        expected_sample_slot = row.condition_axes.get("sample_slot_index")
        if any(
            binding.sample_slot_index != expected_sample_slot
            for binding in trace_bindings
        ):
            raise ValueError("canonical trace repeat/sample binding mismatch")
        locator_values: dict[tuple[str, str, str, str], ExternalBankObjectLocator] = {}
        for binding in trace_bindings:
            resolver = resolvers.get(binding.bank_root_id)
            if resolver is None:
                raise ValueError("canonical trace source resolver is missing")
            for replacement in binding.replacements:
                entry = resolver.entry(replacement.entry_id)
                for locator in entry.object_locators:
                    paper_role = _paper_trace_object_role(locator.object_role)
                    converted = ExternalBankObjectLocator(
                        bank_root_id=locator.bank_root_id,
                        manifest_digest=locator.manifest_digest,
                        entry_id=locator.entry_id,
                        object_role=paper_role,
                        object_digest=locator.object_digest,
                    )
                    locator_values[
                        (
                            converted.bank_root_id,
                            converted.entry_id,
                            converted.object_role,
                            converted.object_digest,
                        )
                    ] = converted
        source_locators = tuple(locator_values[key] for key in sorted(locator_values))
        wrapper_values = tuple(
            _required_field(event, "payload").get("current_wrapper_ref")
            for event in events
            if _optional_field(event, "event_type") == "TRACE_DELIVERY_COMMITTED.v1"
        )
        wrapper_refs = tuple(
            ArtifactRef.from_dict(value)
            for value in wrapper_values
            if isinstance(value, Mapping)
        )
        if not wrapper_refs:
            raise ValueError("canonical trace resource book is missing")
        with collector.lock:
            current_wrappers = collector.current_trace_wrappers_by_root.get(
                row.preregistered_root_run_id,
                (),
            )
        if len(current_wrappers) != len(wrapper_refs):
            raise ValueError("canonical trace wrapper projection is incomplete")
        trace_resource_book_ref = _persist_trace_resource_book(
            native_store=native_store,
            canonical_store=canonical_store,
            root_id=row.preregistered_root_run_id,
            execution_id=execution_id,
            task_id=protocol_task_id,
            current_wrappers=current_wrappers,
            native_wrapper_refs=wrapper_refs,
            canonical_wrapper_refs=tuple(
                copied[ref.artifact_id] for ref in wrapper_refs
            ),
            source_locators=source_locators,
        )
        provider_refs = ()
    evidence = build_canonical_direct_evidence(
        inventory_row=row,
        execution_id=execution_id,
        event_ledger=native_ledger,
        artifact_store=canonical_store,
        runtime_result=runtime_result,
        final_result_ref=final_ref,
        parser_refs=parser_refs,
        verifier_checker_refs=verifier_refs,
        current_provider_object_refs=provider_refs,
        source_bank_object_locators=source_locators,
        actual_resource_book_ref=actual_resource_book_ref,
        trace_resource_book_ref=trace_resource_book_ref,
    )
    producer_facts = {
        "task": _as_json(task),
        "attempts": [
            _as_json(value)
            for value in _sequence_field(
                adapter_result,
                "attempt_results",
                "attempts",
            )
        ],
        "faults": [
            _as_json(value)
            for value in _sequence_field(adapter_result, "fault_records", "faults")
        ],
        "events": [
            _as_json(value)
            for value in _sequence_field(adapter_result, "event_records", "events")
        ],
        "run_evidence": _as_json(
            _optional_field(adapter_result, "run_evidence") or {}
        ),
        "ledger_root_clock": _root_clock_from_verified_ledger(
            events=events,
            run_id=execution_id,
            task_id=protocol_task_id,
            root_unit_id=root_unit_id,
        ),
    }
    with collector.lock:
        collector.producer_facts_by_root[row.preregistered_root_run_id] = (
            producer_facts
        )
    _persist_canonical_direct_checkpoint(
        projection_root=projection_root,
        row=row,
        evidence=evidence,
        native_ledger=native_ledger,
        collector=collector,
    )
    provider_files = {
        ref.artifact_id: projection_root / ref.uri for ref in provider_refs
    }
    with collector.lock:
        if any(
            prior.preregistered_root_run_id == row.preregistered_root_run_id
            for prior in collector.evidence
        ):
            raise ValueError("duplicate canonical direct root evidence")
        collector.evidence.append(evidence)
        collector.current_provider_object_files.update(provider_files)


def _root_scheduler_worker_count(
    *,
    protocol_worker_count: int,
    online_root_callback_factory: Callable[..., Any] | None,
) -> int:
    """在线检查按权威计划逐 root 调度，协议 worker_count 仍保留在 condition 中。"""

    if online_root_callback_factory is not None:
        return 1
    return int(protocol_worker_count)


@dataclass(frozen=True)
class _FormalConditionExecutionCallback:
    catalog_manifest: Any
    config: AIAPIExecutorConfig
    transport: Any
    real_transport: bool
    output_root: Path
    request_limits: Mapping[str, Any]
    evidence_store: FormalEvidenceStore
    completed_task_keys: set[tuple[str, str, str, str]]
    usage: "_UsageTotals"
    budget: PaperBudgetResult
    rolling_disk_forecast: _RollingDiskForecast
    hard_limits: Mapping[str, Any]
    root_case_ids: tuple[str, ...] | None
    execution_classification: Mapping[str, Any] | None
    direct_collector: _CanonicalDirectCollector | None = None
    trace_context: PaperFormalTraceContext | None = None
    online_root_callback_factory: Callable[..., Any] | None = None
    usage_lock: Lock = field(default_factory=Lock, compare=False, repr=False)
    def __call__(self, **kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        cases_by_id = _catalog_cases_by_id(self.catalog_manifest)
        selected_case_ids = (
            self.root_case_ids
            if self.root_case_ids is not None
            else tuple(selection.ordered_case_ids)
        )
        completed = sum(
            _formal_task_key(condition, case_id) in self.completed_task_keys
            for case_id in selected_case_ids
        )
        blocked = 0
        failed = 0
        provider_attempts = 0
        budget_exhausted = False
        pending_case_ids = tuple(
            case_id
            for case_id in selected_case_ids
            if _formal_task_key(condition, case_id) not in self.completed_task_keys
        )
        exp5_identity_fail_stop = condition.experiment_id == EXP5_EXPERIMENT_ID
        persisted_exp5_fail_stop = (
            exp5_identity_fail_stop
            and self._has_checkpointed_exp5_identity_fail_stop(condition=condition)
        )
        if persisted_exp5_fail_stop:
            persisted_task_ids = _validated_condition_task_ids(
                evidence_store=self.evidence_store,
                condition=condition,
            )
            for case_id in selected_case_ids:
                if case_id not in persisted_task_ids:
                    self._checkpoint_exp5_identity_not_started(
                        condition=condition,
                        task_id=case_id,
                    )
            pending_case_ids = ()
        if self.hard_limits.get("stop_after_current_task") is True:
            pending_case_ids = pending_case_ids[:1]
        # 协议容量采用冻结 worker_count；在线检查的 root 调度必须保持全局单并发。
        worker_count = int(condition.worker_count)
        root_scheduler_worker_count = _root_scheduler_worker_count(
            protocol_worker_count=worker_count,
            online_root_callback_factory=self.online_root_callback_factory,
        )
        budget_exhausted_during_exp5 = False
        condition_trace_clock = _ConditionTraceClock()

        def execute_case(case_id: str, worker_id: int) -> _RootExecutionOutcome:
            case = cases_by_id.get(case_id)
            if case is None:
                raise ValueError("frozen selection case is absent from catalog")
            frozen_case = _case_with_selection_split_profile(case, selection)
            rolling_reservation = _preflight_formal_root_capacity(
                output_root=self.output_root,
                condition=condition,
                task_id=case_id,
                case=frozen_case,
                request_limits=self.request_limits,
                rolling_forecast=self.rolling_disk_forecast,
            )
            try:
                outcome = self._dispatch_root_case(
                    condition=condition,
                    selection=selection,
                    case_id=case_id,
                    case=case,
                    worker_id=worker_id,
                    callback_kwargs=kwargs,
                )
            finally:
                self.rolling_disk_forecast.consume_root_forecast(
                    root_reservation_bytes=int(
                        rolling_reservation["root_reservation_bytes"]
                    )
                )
            outcome = self._apply_experiment_runtime(
                condition=condition,
                case_id=case_id,
                case=cases_by_id[case_id],
                outcome=outcome,
                callback_kwargs=kwargs,
            )
            if self.trace_context is not None:
                outcome = _condition_scoped_trace_runtime(
                    outcome,
                    condition_clock=condition_trace_clock,
                    worker_id=worker_id,
                )
            return outcome

        def checkpoint_case(
            case_id: str,
            outcome: _RootExecutionOutcome,
        ) -> None:
            nonlocal budget_exhausted_during_exp5
            if outcome.root_status == "budget_exhausted":
                budget_exhausted_during_exp5 = True
                self._checkpoint_budget_exhausted(
                    condition=condition,
                    task_id=case_id,
                    extra_events=(),
                )
            elif outcome.error is not None:
                self._checkpoint_exception(
                    condition=condition,
                    task_id=case_id,
                    error=outcome.error,
                    extra_events=(),
                )
            else:
                self._checkpoint_adapter_result(
                    condition=condition,
                    task_id=case_id,
                    task=outcome.task,
                    adapter_result=outcome.adapter_result,
                    adapter_root=outcome.adapter_root,
                    extra_events=(),
                )

        def continue_after_exp5_case(
            case_id: str,
            outcome: _RootExecutionOutcome,
        ) -> bool:
            del case_id
            return not exp5_identity_fail_stop_required(outcome.adapter_result)

        strategy = run_scheduled_cases(
            ordered_case_ids=pending_case_ids,
            worker_count=root_scheduler_worker_count,
            execute_case=execute_case,
            should_continue_after_case=(
                continue_after_exp5_case if exp5_identity_fail_stop else None
            ),
            on_case_complete=checkpoint_case,
            retain_outcomes=False,
        )
        if exp5_identity_fail_stop and len(strategy.ordered_case_ids) < len(
            pending_case_ids
        ):
            for case_id in pending_case_ids[len(strategy.ordered_case_ids) :]:
                self._checkpoint_exp5_identity_not_started(
                    condition=condition,
                    task_id=case_id,
                )
        budget_exhausted = budget_exhausted or budget_exhausted_during_exp5
        merge_gate_events = tuple(
            event
            for event in strategy.events
            if str(event.get("event_type", "")).startswith("MERGE_GATE_")
        )
        _finalize_formal_condition_snapshot(
            evidence_store=self.evidence_store,
            condition=condition,
            selected_case_ids=selected_case_ids,
            condition_events=(
                merge_gate_events if strategy.ordered_case_ids else None
            ),
        )

        if self.execution_classification is not None:
            self._publish_compatibility_view(condition=condition)

        persisted = _audit_persisted_condition_evidence(
            suite_root=self.evidence_store.output_root,
            experiment_id=condition.experiment_id,
            condition_id=condition.condition_id,
            repeat_id=condition.repeat_id,
            expected_task_ids=selected_case_ids,
        )
        completed = int(persisted["completed_root_count"])
        failed = int(persisted["failed_root_count"])
        blocked = int(persisted["blocked_root_count"])
        provider_attempts = int(persisted["provider_attempt_count"])
        budget_exhausted = (
            budget_exhausted
            or int(persisted["budget_exhausted_root_count"]) > 0
        )
        status = (
            PaperStatus.BUDGET_EXHAUSTED
            if budget_exhausted
            else PaperStatus.COMPLETED
            if completed == len(selected_case_ids)
            else PaperStatus.BLOCKED
            if blocked == len(selected_case_ids)
            else PaperStatus.COMPLETED_WITH_FAILURES
        )
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=status,
            repeat_count=1,
            task_count=len(selected_case_ids),
            completed_root_count=completed,
            failed_root_count=failed,
            blocked_root_count=blocked,
            provider_attempt_count=provider_attempts,
            metrics_ref={
                "paper_eligible": persisted["paper_eligible"],
                "paper_ineligibility_reasons": persisted[
                    "paper_ineligibility_reasons"
                ],
                "task_evidence_count": persisted["task_evidence_count"],
                "attempt_evidence_count": persisted["attempt_evidence_count"],
                "evidence_refs": persisted["evidence_refs"],
                **strategy.metrics,
            },
        )

    def _dispatch_root_case(
        self,
        *,
        condition: Any,
        selection: Any,
        case_id: str,
        case: Any,
        worker_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        if case is None:
            raise ValueError("frozen selection case is absent from catalog")
        case = _case_with_selection_split_profile(case, selection)
        condition = _condition_with_frozen_case_metadata(
            condition=condition,
            case=case,
        )
        matched_baseline_evidence_ref = self._prepare_exp3_trace_reference(
            condition=condition,
            case_id=case_id,
            callback_kwargs=callback_kwargs,
        )
        adapter_root = self.output_root / "runs" / condition.condition_id / case_id
        online_hook = None
        if self.online_root_callback_factory is not None:
            online_hook = self.online_root_callback_factory(
                condition=condition,
                selection=selection,
                case_id=case_id,
                case=case,
                worker_id=worker_id,
                output_root=adapter_root,
                ai_api_config=self.config,
                request_limits=dict(self.request_limits),
            )
            if online_hook is not None and not callable(online_hook):
                raise TypeError("online root callback factory must return a callable hook")
        reservation = (
            _root_budget_reservation(
                case=case,
                request_limits=self.request_limits,
                budget=self.budget,
                currency=_config_currency(self.config),
            )
            if _has_resource_hard_limit(self.hard_limits)
            else _RootBudgetReservation(0, 0, 0.0)
        )
        with self.usage_lock:
            if not _reserve_hard_limit_capacity(
                usage=self.usage,
                reservation=reservation,
                hard_limits=self.hard_limits,
            ):
                return _RootExecutionOutcome(
                    case_id=case_id,
                    root_status="budget_exhausted",
                    adapter_root=self.output_root,
                    worker_id=worker_id,
                )
        experiment_records: list[dict[str, Any]] = []
        post_raw_output_hook = _compose_official_runtime_hooks(
            online_hook,
            self._exp3_post_raw_output_hook(
                condition=condition,
                case_id=case_id,
                callback_kwargs=callback_kwargs,
                runtime_records=experiment_records,
            ),
        )
        ablation_mode = None
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            mode_config = callback_kwargs.get("mode_config")
            ablation_mode = str(
                _optional_field(mode_config, "ablation_mode")
                or condition.ablation_mode
            )
        worker_termination_policy = self._exp3_worker_termination_policy(
            condition=condition,
            case_id=case_id,
            callback_kwargs=callback_kwargs,
        )
        trace_runtime = (
            self.trace_context.runtime_for(
                condition_id=condition.condition_id,
                case_id=case_id,
            )
            if self.trace_context is not None
            else None
        )
        try:
            adapter_result = dispatch_paper_case(
                case=case,
                condition=condition,
                output_root=adapter_root.as_posix(),
                transport=self.transport,
                real_transport=self.real_transport,
                ai_api_config=self.config,
                entry_id=condition.model_entry_id,
                max_tokens=int(self.request_limits["max_tokens"]),
                timeout_seconds=int(self.request_limits["timeout_seconds"]),
                post_raw_output_hook=post_raw_output_hook,
                ablation_mode=ablation_mode,
                worker_termination_policy=worker_termination_policy,
                trace_context=trace_runtime,
            )
        except (RuntimeError, OSError, TimeoutError) as error:
            with self.usage_lock:
                _settle_hard_limit_reservation(
                    usage=self.usage,
                    reservation=reservation,
                )
            raise PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="adapter_runtime",
                failure_kind=type(error).__name__,
                condition_id=condition.condition_id,
                task_id=case_id,
            ) from error
        task = _required_field(adapter_result, "task_result")
        provider_attempt_count = int(
            _required_field(task, "provider_attempt_count")
        )
        total_tokens = int(_optional_field(task, "total_tokens") or 0)
        cost_estimate = float(_optional_field(task, "cost_estimate") or 0.0)
        cost_estimate_currency = _optional_field(task, "cost_estimate_currency")
        cost_estimate_status = _optional_field(task, "cost_estimate_status")
        with self.usage_lock:
            _settle_hard_limit_reservation(
                usage=self.usage,
                reservation=reservation,
                provider_attempt_count=provider_attempt_count,
                total_tokens=total_tokens,
                total_cost_estimate=cost_estimate,
                cost_estimate_currency=(
                    str(cost_estimate_currency)
                    if isinstance(cost_estimate_currency, str)
                    else None
                ),
                cost_estimate_status=(
                    str(cost_estimate_status)
                    if isinstance(cost_estimate_status, str)
                    else None
                ),
            )
        eligibility = _optional_field(adapter_result, "eligibility_report")
        attempts = _sequence_field(adapter_result, "attempt_results", "attempts")
        if trace_runtime is not None:
            # model/source identity 必须先通过；否则不能先生成 identity_consistent=True evidence。
            trace_source_usage = _project_committed_trace_source_usage(
                adapter_result=adapter_result,
                adapter_root=adapter_root,
                trace_runtime=trace_runtime,
                condition=condition,
            )
            eligibility = _evaluate_trace_root_evidence(
                adapter_result=adapter_result,
                adapter_root=adapter_root,
                trace_runtime=trace_runtime,
                direct_collector=self.direct_collector,
                condition=condition,
                case_id=case_id,
            )
            task = _as_json(task)
            task["paper_eligible"] = eligibility.paper_eligible
            task["versioned_paper_evidence_report"] = eligibility.to_dict()
            task["trace_source_usage"] = trace_source_usage
        (
            provider_latency_ms,
            provider_latency_evidence_status,
            provider_latency_unavailable_reason,
        ) = _provider_latency_observation(
            attempts=attempts,
            expected_provider_attempt_count=provider_attempt_count,
        )
        return _RootExecutionOutcome(
            case_id=case_id,
            root_status=_status_value(_required_field(task, "root_status")),
            adapter_root=adapter_root,
            worker_id=worker_id,
            task=task,
            adapter_result=adapter_result,
            provider_attempt_count=provider_attempt_count,
            total_tokens=total_tokens,
            cost_estimate=cost_estimate,
            cost_estimate_currency=(
                str(cost_estimate_currency)
                if isinstance(cost_estimate_currency, str)
                else None
            ),
            cost_estimate_status=(
                str(cost_estimate_status)
                if isinstance(cost_estimate_status, str)
                else None
            ),
            paper_eligible=_optional_field(eligibility, "paper_eligible") is True,
            provider_latency_ms=provider_latency_ms,
            provider_latency_evidence_status=provider_latency_evidence_status,
            provider_latency_unavailable_reason=(
                provider_latency_unavailable_reason
            ),
            provider_error_kind=next(
                (
                    str(_optional_field(attempt, "error_kind"))
                    for attempt in attempts
                    if _optional_field(attempt, "error_kind") is not None
                ),
                None,
            ),
            experiment_records=tuple(experiment_records),
            matched_baseline_evidence_ref=matched_baseline_evidence_ref,
        )

    def _prepare_exp3_trace_reference(
        self,
        *,
        condition: Any,
        case_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        execution_manifest = _required_mapping(
            callback_kwargs.get("execution_manifest"),
            "Exp3 execution_manifest",
        )
        reference_policy = (
            self.execution_classification.get("baseline_policy")
            if isinstance(self.execution_classification, Mapping)
            else None
        ) or execution_manifest.get(
            "baseline_policy",
            "required_by_formal_plan",
        )
        if reference_policy == "omitted_for_smoke_regression":
            if self.execution_classification is None:
                raise ValueError(
                    "formal Exp3 scope cannot omit paired trace evidence"
                )
            return None
        if self.trace_context is None:
            raise PaperInfrastructureBlockedError(
                "Exp3 paired trace bank runtime is missing",
                evidence_integrity=PaperEvidenceIntegrity.MISSING,
                failure_stage="paired_trace_reference",
                failure_kind="source_bank_runtime_unavailable",
                condition_id=condition.condition_id,
                task_id=case_id,
            )
        try:
            trace_runtime = self.trace_context.runtime_for(
                condition_id=condition.condition_id,
                case_id=case_id,
            )
            return _paired_trace_reference_from_runtime(
                trace_runtime=trace_runtime,
                case_id=case_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=_dependency_integrity_for_error(error),
                failure_stage="paired_trace_reference",
                failure_kind="source_bank_reference_unavailable",
                condition_id=condition.condition_id,
                task_id=case_id,
            ) from error

    def _apply_experiment_runtime(
        self,
        *,
        condition: Any,
        case_id: str,
        case: Mapping[str, Any],
        outcome: "_RootExecutionOutcome",
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        if outcome.error is not None or outcome.adapter_result is None:
            return outcome
        if condition.experiment_id == "exp3_real_ai_fault_recovery":
            manifest = _required_mapping(
                callback_kwargs.get("execution_manifest"),
                "Exp3 execution_manifest",
            )
            if isinstance(manifest.get("fault_target_manifest"), Mapping):
                return self._apply_exp3_rate_fault(
                    condition=condition,
                    case=case,
                    outcome=outcome,
                    manifest=manifest,
                )
            if isinstance(manifest.get("worker_death_manifest"), Mapping):
                return self._apply_exp3_worker_death(
                    condition=condition,
                    case_id=case_id,
                    outcome=outcome,
                    manifest=manifest,
                )
            raise ValueError("Exp3 execution_manifest has no executable strategy")
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            return self._apply_exp4_mode(
                condition=condition,
                outcome=outcome,
                callback_kwargs=callback_kwargs,
            )
        if condition.experiment_id == EXP5_EXPERIMENT_ID:
            return self._apply_exp5_identity(condition=condition, outcome=outcome)
        return outcome

    def _exp3_post_raw_output_hook(
        self,
        *,
        condition: Any,
        case_id: str,
        callback_kwargs: Mapping[str, Any],
        runtime_records: list[dict[str, Any]],
    ) -> Any | None:
        """构造同时覆盖 raw 与 parsed-candidate 边界的稳定 runtime hook。"""

        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        manifest = callback_kwargs.get("execution_manifest")
        if not isinstance(manifest, Mapping):
            return None
        target_manifest = manifest.get("fault_target_manifest")
        if not isinstance(target_manifest, Mapping):
            return None
        selected = _string_sequence(
            target_manifest.get("selected_target_ai_unit_ids", ()),
            "selected_target_ai_unit_ids",
        )
        reserve = _string_sequence(
            target_manifest.get("reserve_target_ai_unit_ids", ()),
            "reserve_target_ai_unit_ids",
        )
        fault_type = str(target_manifest.get("fault_type") or condition.fault_type)
        return _Exp3RuntimeHookBridge(
            condition=condition,
            case_id=case_id,
            fault_type=fault_type,
            selected_unit_ids=selected,
            reserve_unit_ids=reserve,
            runtime_records=runtime_records,
        )

    def _exp3_worker_termination_policy(
        self,
        *,
        condition: Any,
        case_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> WorkerTerminationPolicy | None:
        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        manifest = callback_kwargs.get("execution_manifest")
        if not isinstance(manifest, Mapping):
            return None
        worker_manifest = manifest.get("worker_death_manifest")
        if not isinstance(worker_manifest, Mapping):
            return None
        targets_by_case = _required_mapping(
            worker_manifest.get("selected_target_ai_unit_ids_by_case"),
            "selected_target_ai_unit_ids_by_case",
        )
        frozen_targets = _string_sequence(
            targets_by_case.get(case_id),
            f"selected worker death targets for {case_id}",
        )
        prefix = f"{case_id}:"
        planned_targets = tuple(
            target[len(prefix) :] if target.startswith(prefix) else target
            for target in frozen_targets
        )
        expected_count = int(worker_manifest["dead_worker_count_target"])
        if not planned_targets or len(planned_targets) > expected_count:
            raise ValueError(
                "worker death planned targets do not fit manifest condition"
            )
        progress = int(worker_manifest["kill_progress_target_percent"])
        return WorkerTerminationPolicy(
            target_planned_ai_unit_ids=planned_targets,
            termination_count_target=expected_count,
            kill_point=f"progress_{progress}",
            total_planned_ai_unit_count=int(
                _required_mapping(
                    worker_manifest.get("planned_ai_unit_count_by_case"),
                    "planned_ai_unit_count_by_case",
                )[case_id]
            ),
            process_timeout_seconds=max(
                30.0,
                float(self.request_limits["timeout_seconds"]) + 30.0,
            ),
        )

    def _apply_exp3_rate_fault(
        self,
        *,
        condition: Any,
        case: Mapping[str, Any],
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        """只投影 runtime 已执行的 fault/recovery，不再次调用 adapter。"""

        del case
        task_body = self._exp3_task_with_baseline(
            task=_as_json(outcome.task),
            outcome=outcome,
            manifest=manifest,
        )
        target_manifest = _required_mapping(
            manifest.get("fault_target_manifest"),
            "Exp3 fault_target_manifest",
        )
        selected_unit_ids = set(
            _string_sequence(
                target_manifest.get("selected_target_ai_unit_ids"),
                "selected_target_ai_unit_ids",
            )
        )
        reserve_unit_ids = set(
            _string_sequence(
                target_manifest.get("reserve_target_ai_unit_ids", ()),
                "reserve_target_ai_unit_ids",
            )
        )
        if not selected_unit_ids:
            return replace(
                outcome,
                task=task_body,
                adapter_result=_adapter_result_with(
                    outcome.adapter_result,
                    task_result=task_body,
                    attempts=_sequence_field(
                        outcome.adapter_result,
                        "attempt_results",
                        "attempts",
                    ),
                    faults=_sequence_field(
                        outcome.adapter_result,
                        "fault_records",
                        "faults",
                    ),
                    events=_sequence_field(
                        outcome.adapter_result,
                        "event_records",
                    ),
                ),
            )
        candidate_target_ids = selected_unit_ids | reserve_unit_ids
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        runtime_faults = tuple(
            dict(record)
            for record in outcome.experiment_records
            if str(record.get("selected_target_ai_unit_id"))
            in candidate_target_ids
            and record.get("applicability_status", "injected") == "injected"
        )
        not_applicable_records = tuple(
            dict(record)
            for record in outcome.experiment_records
            if str(record.get("selected_target_ai_unit_id"))
            in candidate_target_ids
            and record.get("applicability_status") == "not_applicable"
        )
        fault_attempt_ids = {
            str(record.get("attempt_id"))
            for record in runtime_faults
            if record.get("attempt_id") is not None
        }
        faulted_protocol_unit_ids = {
            str(record.get("unit_id"))
            for record in runtime_faults
            if record.get("unit_id") is not None
        }
        replacement_attempts = tuple(
            attempt
            for attempt in attempts
            if str(_required_field(attempt, "unit_id"))
            in faulted_protocol_unit_ids
            and str(_required_field(attempt, "attempt_id")) not in fault_attempt_ids
        )
        approved_identity = {
            "provider_family": condition.provider_family,
            "provider_model_id": condition.provider_model_id,
            "model_entry_id": condition.model_entry_id,
        }
        for attempt in replacement_attempts:
            _validate_runtime_attempt_identity(attempt, approved_identity)
        task_body["fault_injected"] = bool(runtime_faults)
        task_body["fault_applicability"] = {
            "candidate_target_count": int(
                target_manifest.get(
                    "candidate_target_count",
                    len(candidate_target_ids),
                )
            ),
            "selected_target_count": int(
                target_manifest.get(
                    "selected_target_count",
                    len(selected_unit_ids),
                )
            ),
            "eligible_target_count": len(runtime_faults),
            "injected_target_count": len(runtime_faults),
            "not_applicable_target_count": len(not_applicable_records),
            "injection_denominator": len(runtime_faults),
            "denominator_kind": "eligible_parsed_candidates",
        }
        task_body["fault_applicability_records"] = list(
            not_applicable_records
        )
        task_body["recovered_fault_target_count"] = len(
            {
                str(_required_field(attempt, "unit_id"))
                for attempt in replacement_attempts
            }
        )
        protocol_event_refs = tuple(
            dict(ref)
            for ref in task_body.get("event_refs", ())
            if isinstance(ref, Mapping)
        )
        observation_events = tuple(
            {
                "event_type": "EXPERIMENT_FAULT_OBSERVED",
                "condition_id": condition.condition_id,
                "unit_id": record.get("unit_id"),
                "selected_target_ai_unit_id": record.get(
                    "selected_target_ai_unit_id"
                ),
                "attempt_id": record.get("attempt_id"),
                "fault_type": record.get("fault_type"),
                "protocol_event_refs": list(protocol_event_refs),
                "artifact_refs": [
                    dict(ref)
                    for ref in (
                        record.get("original_output_ref"),
                        record.get("original_provenance_ref"),
                        record.get("pre_fault_usage_ref"),
                        record.get("mutated_output_ref"),
                        record.get("record_ref"),
                    )
                    if isinstance(ref, Mapping)
                ],
            }
            for record in runtime_faults
        )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=attempts,
            faults=(
                *_sequence_field(outcome.adapter_result, "fault_records", "faults"),
                *runtime_faults,
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *observation_events,
            ),
        )
        return replace(outcome, task=task_body, adapter_result=adapter_result)

    def _exp3_task_with_baseline(
        self,
        *,
        task: dict[str, Any],
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        policy = (
            self.execution_classification.get("baseline_policy")
            if isinstance(self.execution_classification, Mapping)
            else None
        ) or manifest.get("baseline_policy", "required_by_formal_plan")
        if policy == "omitted_for_smoke_regression":
            task.update(
                {
                    "baseline": None,
                    "baseline_comparison_eligible": False,
                    "baseline_unavailable_reason": (
                        "smoke_baseline_not_requested"
                    ),
                }
            )
            return task
        reference = outcome.matched_baseline_evidence_ref
        if reference is None:
            raise ValueError("Exp3 result is missing paired trace evidence")
        task.update(
            {
                "baseline": dict(reference),
                "matched_baseline_evidence_ref": dict(reference),
                "paired_trace_reference": dict(reference),
                "baseline_comparison_eligible": True,
                "baseline_unavailable_reason": None,
            }
        )
        if isinstance(manifest.get("matched_baseline"), Mapping):
            task["matched_baseline"] = _as_json(manifest["matched_baseline"])
        return task

    def _apply_exp3_worker_death(
        self,
        *,
        condition: Any,
        case_id: str,
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        del case_id
        worker_manifest = _required_mapping(
            manifest.get("worker_death_manifest"),
            "Exp3 worker_death_manifest",
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        faults = tuple(
            item
            for item in _sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            )
            if _optional_field(item, "fault_type") == "worker_death"
            or _optional_field(item, "schema_version")
            in {
                "tokenshare.paper_worker_death.v1",
                "tokenshare.paper_worker_death_incomplete.v1",
            }
        )
        dead_attempts = tuple(
            attempt
            for attempt in attempts
            if _status_value(_optional_field(attempt, "attempt_status") or "")
            == "worker_died"
        )
        dead_attempt_units = {
            str(_required_field(attempt, "unit_id"))
            for attempt in dead_attempts
        }
        recovered_units = {
            str(_required_field(attempt, "unit_id"))
            for attempt in attempts
            if _status_value(_optional_field(attempt, "attempt_status") or "")
            == "succeeded"
            and str(_required_field(attempt, "unit_id")) in dead_attempt_units
        }
        attempt_count_by_dead_unit = {
            unit_id: sum(
                1
                for attempt in attempts
                if str(_required_field(attempt, "unit_id")) == unit_id
            )
            for unit_id in dead_attempt_units
        }
        replacement_process_count = sum(
            max(0, count - 1)
            for count in attempt_count_by_dead_unit.values()
        )
        target_count = int(worker_manifest["dead_worker_count_target"])
        coordinator_survived = bool(faults) and all(
            _optional_field(
                _required_mapping(
                    _optional_field(record, "coordinator"),
                    "worker death coordinator evidence",
                ),
                "survived",
            )
            is True
            for record in faults
        )
        evidence_complete = (
            len(faults) == target_count
            and len(dead_attempts) == target_count
            and recovered_units == dead_attempt_units
            and replacement_process_count >= target_count
            and coordinator_survived
        )
        task_body = self._exp3_task_with_baseline(
            task=_as_json(outcome.task),
            outcome=outcome,
            manifest=manifest,
        )
        task_body.update(
            {
                "dead_worker_count_target": target_count,
                "kill_progress_target_percent": int(
                    worker_manifest["kill_progress_target_percent"]
                ),
                "worker_death_count": len(faults),
                "worker_replacement_count": replacement_process_count,
                "coordinator_survived": coordinator_survived,
                "worker_death_evidence_complete": evidence_complete,
            }
        )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=attempts,
            faults=_sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            ),
            events=_sequence_field(outcome.adapter_result, "event_records"),
        )
        return replace(outcome, task=task_body, adapter_result=adapter_result)

    def _apply_exp4_mode(
        self,
        *,
        condition: Any,
        outcome: "_RootExecutionOutcome",
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        mode_config = callback_kwargs.get("mode_config")
        mode = (
            _optional_field(mode_config, "ablation_mode")
            or condition.ablation_mode
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        statuses = {
            _status_value(_optional_field(attempt, "attempt_status") or "")
            for attempt in attempts
        }
        run_evidence = _optional_field(outcome.adapter_result, "run_evidence")
        runtime = _optional_field(run_evidence, "ablation_runtime")
        observation = {
            "protocol_event_refs": tuple(
                dict(ref)
                for ref in (_optional_field(outcome.task, "event_refs") or ())
                if isinstance(ref, Mapping)
            ),
            "artifact_refs": tuple(
                dict(ref)
                for ref in (_optional_field(outcome.task, "artifact_refs") or ())
                if isinstance(ref, Mapping)
            ),
            "candidate_rejected": bool(
                statuses & {"verification_rejected", "checker_rejected"}
            ),
            "parse_failed": (
                "parse_failed" in statuses
                or _optional_field(runtime, "raw_only_exposed") is True
            ),
            "replacement_created": len(attempts) > 1,
            "merge_gate_blocked": (
                outcome.root_status != "completed"
                or _optional_field(runtime, "premature_merge_attempted") is True
            ),
            "slot_binding_valid": (
                _optional_field(runtime, "slot_integrity_violation") is not True
            ),
            "deterministic_validity": (
                _optional_field(runtime, "root_validity_audit_passed")
                if runtime is not None
                else outcome.root_status == "completed"
            ),
        }
        strategy = run_exp4_ablation_strategy(
            mode=str(mode),
            adapter_observation=observation,
        )
        task_body = _as_json(outcome.task)
        task_body.update(
            {
                "ablation_mode": strategy.mode,
                "ablation_runtime_flags": strategy.runtime_flags,
                "ablation_applicable": strategy.metrics["applicable"],
                "exposed_error_count": strategy.metrics[
                    "exposed_error_count"
                ],
                "escaped_error_count": strategy.metrics[
                    "escaped_error_count"
                ],
                "final_deterministic_validity": strategy.metrics[
                    "final_deterministic_validity"
                ],
            }
        )
        runtime_body = _as_json(runtime or {})
        if not isinstance(runtime_body, Mapping):
            runtime_body = {}
        raw_hook_observations = runtime_body.get("hook_observations", [])
        if not isinstance(raw_hook_observations, list):
            raise ValueError("runtime hook observations must be a list")
        typed_hook_observations: list[RuntimeHookObservationV1] = []
        for item in raw_hook_observations:
            if not isinstance(item, Mapping):
                raise ValueError("runtime hook observation must be an object")
            typed_hook_observations.append(
                RuntimeHookObservationV1.from_dict(item)
            )
        hook_observations = [
            observation.to_dict() for observation in typed_hook_observations
        ]
        target_mechanism = {
            "NO_VERIFICATION": "verification",
            "NO_PARSER_POLICY": "parser_policy",
            "NO_REQUEUE": "requeue",
            "NO_MERGE_GATE": "merge_gate",
        }.get(strategy.mode)
        target_observed = any(
            observation.kind
            is RuntimeHookObservationKind.EXPERIMENT_ABLATION_GATE_APPLIED
            and isinstance(
                observation.payload,
                ExperimentAblationGateAppliedPayloadV1,
            )
            and observation.payload.disabled_mechanism == target_mechanism
            for observation in typed_hook_observations
        )
        not_applicable_summary: dict[str, Any] | None = None
        if (
            target_mechanism is not None
            and not target_observed
            and strategy.metrics.get("applicable") is False
        ):
            not_applicable_summary = {
                "ablation_mode": strategy.mode,
                "disabled_mechanism": target_mechanism,
                "applicability": "not_applicable",
                "not_applicable_reason": "target_lifecycle_boundary_not_reached",
                "protocol_event_refs": list(observation["protocol_event_refs"]),
                "artifact_refs": list(observation["artifact_refs"]),
            }
        runtime_body = {
            **dict(runtime_body),
            "condition_id": condition.condition_id,
            "case_id": outcome.case_id,
            "repeat_id": condition.repeat_id,
            "mode": strategy.mode,
            "attempt_observations": [
                {**dict(item), "ablation_mode": strategy.mode}
                for item in runtime_body.get("attempt_observations", ())
                if isinstance(item, Mapping)
            ],
            "hook_observations": hook_observations,
        }
        runtime_body.pop("target_hook_not_applicable", None)
        if not_applicable_summary is not None:
            runtime_body["target_hook_not_applicable"] = not_applicable_summary
        task_body["ablation_runtime"] = runtime_body
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=attempts,
            faults=_sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *strategy.events,
            ),
        )
        return replace(
            outcome,
            task=task_body,
            adapter_result=adapter_result,
            root_status=outcome.root_status,
        )

    def _apply_exp5_identity(
        self,
        *,
        condition: Any,
        outcome: "_RootExecutionOutcome",
    ) -> "_RootExecutionOutcome":
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        task_body = _record_with_context(
            outcome.task,
            condition=condition,
            task_id=outcome.case_id,
        )
        task_body.setdefault("paper_eligible", outcome.paper_eligible)
        strategy = run_exp5_identity_strategy(
            attempts=attempts,
            approved_identity={
                "provider_family": condition.provider_family,
                "provider_model_id": condition.provider_model_id,
                "model_entry_id": condition.model_entry_id,
            },
            condition_id=condition.condition_id,
            cohort_member_id=str(condition.cohort_member_id),
            adapter_root=outcome.adapter_root,
            task=task_body,
            transport_kind=("ai_api" if self.real_transport else "capturing"),
            model_policy=str(condition.model_policy),
            pilot_only=bool(self.execution_classification),
        )
        identity_status_by_attempt = strategy.metrics[
            "identity_status_by_attempt"
        ]
        task_body, enriched_attempts, identity_mismatch = (
            finalize_exp5_identity_evidence(
                attempts=attempts,
                task=task_body,
                identity_status_by_attempt=identity_status_by_attempt,
                cohort_member_id=str(condition.cohort_member_id),
            )
        )
        task_body["model_execution_records"] = list(
            strategy.model_execution_records
        )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=enriched_attempts,
            faults=_sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            ),
            events=_sequence_field(outcome.adapter_result, "event_records"),
        )
        return replace(
            outcome,
            task=task_body,
            adapter_result=adapter_result,
            root_status=str(task_body.get("root_status") or outcome.root_status),
            paper_eligible=(outcome.paper_eligible and not identity_mismatch),
        )

    def _checkpoint_exp5_identity_not_started(
        self,
        *,
        condition: Any,
        task_id: str,
    ) -> None:
        event_type = "EXPERIMENT_NOT_STARTED_AFTER_MODEL_IDENTITY_FAILURE"
        event_id = (
            f"formal-exp5-identity-fail-stop-{condition.condition_id}-"
            f"{condition.repeat_id}-{task_id}"
        )
        terminal = PaperTerminalOutcome(
            outcome_status=PaperOutcomeStatus.FAILED_EXPERIMENTAL,
            evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
            failure_stage="model_identity_audit",
            failure_kind="model_identity_fail_stop",
        ).to_dict()
        artifact = _write_runner_artifact(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            artifact_name="model-identity-not-started.json",
            body={
                **terminal,
                "root_status": "not_started",
                "condition_id": condition.condition_id,
                "task_id": task_id,
                "cohort_member_id": condition.cohort_member_id,
            },
        )
        common = {
            "experiment_id": condition.experiment_id,
            "condition_id": condition.condition_id,
            "repeat_id": condition.repeat_id,
            "task_id": task_id,
            "cohort_member_id": condition.cohort_member_id,
            **terminal,
            **self._evidence_flags(paper_eligible=False),
            "paper_ineligibility_reasons": ["model_identity_fail_stop"],
            "record_scope": "experiment",
        }
        task = {
            **common,
            "root_status": "not_started",
            "error_kind": "model_identity_fail_stop",
            "event_refs": [{"event_id": event_id, "event_type": event_type}],
            "evidence_artifact_refs": [artifact],
        }
        attempt = {
            **common,
            "attempt_id": (
                f"experiment-not-started-{condition.condition_id}-"
                f"{condition.repeat_id}-{task_id}"
            ),
            "attempt_status": "not_started",
            "provider_attempt_index": 0,
            "provider_attempt_count": 0,
            "error_kind": "model_identity_fail_stop",
        }
        event = {
            **common,
            "event_id": event_id,
            "event_type": event_type,
        }
        self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task,
            attempts=[attempt],
            faults=[],
            events=[event],
            artifact_refs=[artifact],
        )

    def _has_checkpointed_exp5_identity_fail_stop(
        self,
        *,
        condition: Any,
    ) -> bool:
        run_root = (
            self.evidence_store.output_root
            / "experiments"
            / condition.experiment_id
            / "runs"
            / condition.condition_id
            / str(condition.repeat_id)
        )
        if not run_root.is_dir():
            return False
        tasks = self.evidence_store._validate_run(
            run_root,
            experiment_id=condition.experiment_id,
        )
        return any(
            task.get("error_kind")
            in {"model_identity_mismatch", "model_identity_fail_stop"}
            for task in tasks
        )

    def _checkpoint_adapter_result(
        self,
        *,
        condition: Any,
        task_id: str,
        task: Any,
        adapter_result: Any,
        adapter_root: Path,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> bool:
        task_body = _record_with_context(
            task,
            condition=condition,
            task_id=task_id,
        )
        run_evidence = _optional_field(adapter_result, "run_evidence")
        protocol_runtime = (
            run_evidence.get("protocol_runtime")
            if isinstance(run_evidence, Mapping)
            and isinstance(run_evidence.get("protocol_runtime"), Mapping)
            else None
        )
        generation_identity = (
            protocol_runtime.get("generation_identity")
            if protocol_runtime is not None
            else None
        )
        if isinstance(generation_identity, Mapping):
            task_body["runtime_generation_identity"] = dict(generation_identity)
            split_profile_digest = _budget_split_profile_digest(
                budget=self.budget,
                condition_id=condition.condition_id,
                case_id=task_id,
            )
            if split_profile_digest is not None:
                task_body["execution_version_identity"] = (
                    _execution_version_identity(
                        domain=condition.domain,
                        split_profile_digest=split_profile_digest,
                        runtime_generation_identity=generation_identity,
                    )
                )
        runtime_observation = (
            protocol_runtime.get("runtime_observation")
            if protocol_runtime is not None
            else None
        )
        if isinstance(runtime_observation, Mapping):
            task_body["runtime_observation"] = dict(runtime_observation)
        if protocol_runtime is not None:
            for field_name in (
                "case_id",
                "factor_position_quantile",
                "execution_scope",
                "selected_ai_unit_ids",
            ):
                value = protocol_runtime.get(field_name)
                if value is not None:
                    task_body[field_name] = value
        task_body["record_scope"] = (
            "protocol" if protocol_runtime is not None else "experiment"
        )
        root_status = _status_value(task_body.get("root_status", "failed"))
        task_body.update(
            PaperTerminalOutcome(
                outcome_status=(
                    PaperOutcomeStatus.SUCCEEDED
                    if root_status == "completed"
                    else PaperOutcomeStatus.FAILED_EXPERIMENTAL
                ),
                evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
                **(
                    {}
                    if root_status == "completed"
                    else {
                        "failure_stage": "experiment_runtime",
                        "failure_kind": str(
                            task_body.get("error_kind") or root_status
                        ),
                    }
                ),
            ).to_dict()
        )
        versioned_report = task_body.get("versioned_paper_evidence_report")
        trace_evidence_reasons = (
            list(versioned_report.get("ineligibility_reasons", ()))
            if isinstance(versioned_report, Mapping)
            and versioned_report.get("evidence_class")
            == "real_model_trace_protocol_run"
            else None
        )
        source_task_eligible = (
            versioned_report.get("paper_eligible") is True
            if trace_evidence_reasons is not None
            else task_body.get("paper_eligible") is True
        )
        task_body.update(self._evidence_flags(paper_eligible=False))
        task_body["source_projection_paper_eligible"] = source_task_eligible
        attempt_values = _sequence_field(adapter_result, "attempt_results", "attempts")
        attempts = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in attempt_values
        ]
        if not attempts:
            if protocol_runtime is not None:
                raise ValueError(
                    "protocol checkpoint requires real attempt evidence"
                )
            attempts = [_record_with_context(
                _experiment_attempt_record(
                    condition=condition,
                    task_id=task_id,
                    status="blocked",
                    error_kind="experiment_blocked",
                ),
                condition=condition,
                task_id=task_id,
            )]
        for attempt in attempts:
            attempt.setdefault("record_scope", task_body["record_scope"])
            source_attempt_eligible = attempt.get("paper_eligible") is True
            attempt_reasons = (
                list(trace_evidence_reasons)
                if trace_evidence_reasons is not None
                else _attempt_checkpoint_ineligibility_reasons(
                    attempt=attempt,
                    protocol_runtime=protocol_runtime,
                    real_transport=self.real_transport,
                    transport=self.transport,
                    source_attempt_eligible=source_attempt_eligible,
                )
            )
            attempt.update(self._evidence_flags(paper_eligible=not attempt_reasons))
            attempt["source_projection_paper_eligible"] = source_attempt_eligible
            attempt["paper_ineligibility_reasons"] = attempt_reasons
        protocol_event_values = _sequence_field(adapter_result, "event_records")
        if protocol_runtime is not None and not protocol_event_values:
            raise ValueError("protocol checkpoint requires real lifecycle events")
        events = [
            _event_record_with_context(item, condition=condition, task_id=task_id)
            for item in protocol_event_values
        ]
        if not events:
            events = [_record_with_context(
                _experiment_event_record(
                    condition=condition,
                    task_id=task_id,
                    event_type="EXPERIMENT_BLOCKED",
                ),
                condition=condition,
                task_id=task_id,
            )]
        for event in events:
            if _is_protocol_ledger_event(event):
                continue
            event.setdefault(
                "record_scope",
                "experiment"
                if str(event.get("event_type", "")).startswith("EXPERIMENT_")
                else task_body["record_scope"],
            )
        experiment_events = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in extra_events
        ]
        for event in experiment_events:
            event.setdefault("record_scope", "experiment")
        events.extend(experiment_events)
        for index, event in enumerate(events):
            if _is_protocol_ledger_event(event):
                continue
            event.setdefault(
                "event_id",
                (
                    f"formal-{str(event.get('event_type', 'event')).lower()}-"
                    f"{condition.condition_id}-{condition.repeat_id}-{task_id}-{index}"
                ),
            )
            event.update(
                self._evidence_flags(
                    paper_eligible=(
                        event.get("record_scope") == "protocol"
                        and isinstance(event.get("event_id"), str)
                        and bool(event.get("event_id"))
                    )
                )
            )
        faults = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in _sequence_field(adapter_result, "fault_records", "faults")
        ]
        for fault in faults:
            fault.update(self._evidence_flags(paper_eligible=False))
        if self.direct_collector is not None:
            _capture_canonical_direct_evidence(
                collector=self.direct_collector,
                suite_root=self.evidence_store.output_root,
                condition=condition,
                case_id=task_id,
                task=task,
                adapter_result=adapter_result,
            )
        artifact_refs = _materialize_artifacts(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            adapter_root=adapter_root,
            source_refs=_adapter_artifact_refs(
                task_body,
                attempts,
                faults,
                events=events,
            ),
        )
        if not artifact_refs:
            artifact_refs = [_write_runner_artifact(
                suite_root=self.evidence_store.output_root,
                condition=condition,
                task_id=task_id,
                artifact_name="task-record.json",
                body=task_body,
            )]
        task_body["evidence_artifact_refs"] = list(artifact_refs)
        task_reasons = (
            list(trace_evidence_reasons)
            if trace_evidence_reasons is not None
            else _task_checkpoint_ineligibility_reasons(
                task=task_body,
                attempts=attempts,
                events=events,
                protocol_runtime=protocol_runtime,
                real_transport=self.real_transport,
                transport=self.transport,
                source_task_eligible=source_task_eligible,
            )
        )
        task_body.update(self._evidence_flags(paper_eligible=not task_reasons))
        task_body["source_projection_paper_eligible"] = source_task_eligible
        task_body["paper_ineligibility_reasons"] = task_reasons
        commit_token = self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task_body,
            attempts=attempts,
            faults=faults,
            events=events,
            artifact_refs=artifact_refs,
        )
        _remove_checkpointed_adapter_tree(
            suite_root=self.evidence_store.output_root,
            plan_root=self.output_root,
            condition=condition,
            task_id=task_id,
            adapter_root=adapter_root,
            commit_token=commit_token,
        )
        if _status_value(task_body.get("root_status", "failed")) == "completed":
            self.completed_task_keys.add(_formal_task_key(condition, task_id))
        return task_body["paper_eligible"] is True

    def _checkpoint_exception(
        self,
        *,
        condition: Any,
        task_id: str,
        error: Exception,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task = _record_with_context(
            {
                "task_id": task_id,
                "root_status": "failed",
                "error_kind": type(error).__name__,
                "record_scope": "experiment",
                **PaperTerminalOutcome(
                    outcome_status=PaperOutcomeStatus.FAILED_EXPERIMENTAL,
                    evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
                    failure_stage="experiment_runtime",
                    failure_kind=type(error).__name__,
                ).to_dict(),
            },
            condition=condition,
            task_id=task_id,
        )
        task.update(self._evidence_flags())
        attempt = _record_with_context(
            _experiment_attempt_record(
                condition=condition,
                task_id=task_id,
                status="failed",
                error_kind=type(error).__name__,
            ),
            condition=condition,
            task_id=task_id,
        )
        attempt.update(
            {
                "attempt_status": "failed",
                "error_kind": type(error).__name__,
                "outcome_status": "failed_experimental",
                "evidence_integrity": "complete",
                **self._evidence_flags(),
            }
        )
        events = [
            _record_with_context(
                _experiment_event_record(
                    condition=condition,
                    task_id=task_id,
                    event_type="EXPERIMENT_RUNNER_EXCEPTION",
                ),
                condition=condition,
                task_id=task_id,
            ),
            *(
                _record_with_context(item, condition=condition, task_id=task_id)
                for item in extra_events
            ),
        ]
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                f"formal-exception-{condition.condition_id}-{task_id}-{index}",
            )
            event.update(self._evidence_flags())
        artifact = _write_runner_artifact(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            artifact_name="runner-error.json",
            body={"error_type": type(error).__name__, "message": str(error)},
        )
        commit_token = self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task,
            attempts=[attempt],
            faults=[],
            events=events,
            artifact_refs=[artifact],
        )
        self._cleanup_terminal_adapter_tree(
            condition=condition,
            task_id=task_id,
            commit_token=commit_token,
        )

    def _publish_compatibility_view(self, *, condition: Any) -> None:
        """保留 dispatcher output_root 下的只读兼容运行视图。"""

        source = self.evidence_store.output_root / "experiments" / condition.experiment_id
        if source == self.output_root:
            return
        shutil.copytree(source, self.output_root, dirs_exist_ok=True)

    def _checkpoint_budget_exhausted(
        self,
        *,
        condition: Any,
        task_id: str,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task = _record_with_context(
            {
                "task_id": task_id,
                "root_status": "budget_exhausted",
                "error_kind": "budget_limit",
                "record_scope": "experiment",
            },
            condition=condition,
            task_id=task_id,
        )
        task.update(self._evidence_flags())
        attempt = _record_with_context(
            _experiment_attempt_record(
                condition=condition,
                task_id=task_id,
                status="cancelled_by_budget",
                error_kind="budget_limit",
            ),
            condition=condition,
            task_id=task_id,
        )
        attempt.update(
            {
                "attempt_status": "cancelled_by_budget",
                "error_kind": "budget_limit",
                **self._evidence_flags(),
            }
        )
        events = [
            _record_with_context(
                {
                    "event_id": (
                        f"formal-budget-exhausted-{condition.condition_id}-"
                        f"{condition.repeat_id}-{task_id}"
                    ),
                    "event_type": "EXPERIMENT_BUDGET_EXHAUSTED",
                    "record_scope": "experiment",
                },
                condition=condition,
                task_id=task_id,
            ),
            *(
                _record_with_context(item, condition=condition, task_id=task_id)
                for item in extra_events
            ),
        ]
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                f"formal-budget-{condition.condition_id}-{task_id}-{index}",
            )
            event.update(self._evidence_flags())
        artifact = _write_runner_artifact(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            artifact_name="budget-exhausted.json",
            body={
                "status": "budget_exhausted",
                "hard_limits": _as_json(self.hard_limits),
                "usage": {
                    "provider_attempt_count": self.usage.provider_attempt_count,
                    "total_tokens": self.usage.total_tokens,
                    "total_cost_estimate": self.usage.reportable_total_cost_estimate(),
                    "cost_estimate_by_currency": dict(
                        sorted(self.usage.cost_estimate_by_currency.items())
                    ),
                    "cost_estimate_status": self.usage.cost_estimate_status(),
                    "usage_missing_count": self.usage.usage_missing_count,
                },
            },
        )
        commit_token = self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task,
            attempts=[attempt],
            faults=[],
            events=events,
            artifact_refs=[artifact],
        )
        self._cleanup_terminal_adapter_tree(
            condition=condition,
            task_id=task_id,
            commit_token=commit_token,
        )

    def _cleanup_terminal_adapter_tree(
        self,
        *,
        condition: Any,
        task_id: str,
        commit_token: Mapping[str, Any],
    ) -> None:
        adapter_root = (
            self.output_root
            / "runs"
            / condition.condition_id
            / task_id
        )
        _remove_checkpointed_adapter_tree(
            suite_root=self.evidence_store.output_root,
            plan_root=self.output_root,
            condition=condition,
            task_id=task_id,
            adapter_root=adapter_root,
            commit_token=commit_token,
        )

    def _evidence_flags(self, *, paper_eligible: bool = False) -> dict[str, Any]:
        return _evidence_flags(
            paper_eligible=paper_eligible,
            execution_classification=self.execution_classification,
        )


@dataclass
class _UsageTotals:
    """当前 runner 进程内已经消费的受限资源。"""

    provider_attempt_count: int = 0
    total_tokens: int = 0
    total_cost_estimate: float = 0.0
    cost_estimate_by_currency: dict[str, float] = field(default_factory=dict)
    usage_missing_count: int = 0
    reserved_provider_attempt_count: int = 0
    reserved_total_tokens: int = 0
    reserved_total_cost_estimate: float = 0.0
    reserved_cost_estimate_by_currency: dict[str, float] = field(default_factory=dict)

    def reportable_total_cost_estimate(self) -> float:
        if len(self.cost_estimate_by_currency) > 1:
            return 0.0
        if self.cost_estimate_by_currency:
            return next(iter(self.cost_estimate_by_currency.values()))
        return self.total_cost_estimate

    def cost_estimate_status(self) -> str:
        if len(self.cost_estimate_by_currency) > 1:
            return "mixed_currency_not_aggregated"
        if self.usage_missing_count:
            return "usage_missing"
        if self.cost_estimate_by_currency:
            return "single_currency_estimate"
        return "single_currency_or_legacy"


@dataclass(frozen=True, slots=True)
class _RootBudgetReservation:
    provider_attempt_count: int
    total_tokens: int
    total_cost_estimate: float
    currency: str | None = None


def _evidence_bodies(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    hard_limits: Mapping[str, Any],
    ai_api_configs: Mapping[str, Any],
    root_case_filter: Mapping[str, tuple[str, ...]],
    execution_classification: Mapping[str, Any] | None,
    suite_id: str,
    pre_execution_documents: Mapping[str, Any],
) -> dict[str, Any]:
    """构造 FormalEvidenceStore 所需的稳定 suite identity bodies。"""

    plan_bodies = [_as_json(plan) for plan in plans]
    endpoint_plans: list[dict[str, Any]] = []
    request_limits: list[dict[str, Any]] = []
    for plan in plans:
        for condition, _selection in plan.bound_items():
            binding, limits, _config = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            endpoint_plans.append(
                {
                    "experiment_id": plan.experiment_id,
                    "condition_id": condition.condition_id,
                    "endpoint_binding": _as_json(binding),
                }
            )
            request_limits.append(
                {
                    "experiment_id": plan.experiment_id,
                    "condition_id": condition.condition_id,
                    "limits": _as_json(limits),
                }
            )
    return {
        "suite": {
            "schema_version": "tokenshare.paper_formal_runner.v1",
            "suite_id": suite_id,
            "experiment_ids": [plan.experiment_id for plan in plans],
            "root_case_filter": {
                condition_id: list(case_ids)
                for condition_id, case_ids in sorted(root_case_filter.items())
            },
            **(
                {
                    "execution_classification": dict(
                        execution_classification
                    ),
                }
                if execution_classification is not None
                else {}
            ),
            **(
                {
                    "pre_execution_document_digests": {
                        name: digest_json(body)
                        for name, body in sorted(
                            pre_execution_documents.items()
                        )
                    }
                }
                if pre_execution_documents
                else {}
            ),
        },
        "dispatch": {
            "schema_version": "tokenshare.paper_dispatch.v1",
            "plans": plan_bodies,
        },
        "budget": _as_json(budget),
        "catalog": _as_json(catalog_manifest),
        "identity": {
            "schema_version": "tokenshare.paper_formal_runner_identity.v1",
            "endpoints": endpoint_plans,
        },
        "request_limits": {
            "schema_version": "tokenshare.paper_formal_runner_request_limits.v1",
            "conditions": request_limits,
        },
        "hard_limits": _as_json(hard_limits),
    }


def _stored_evidence_bodies(suite_root: Path) -> dict[str, Any]:
    """从冻结 suite manifest 恢复 replay 所需 identity，不读取 adapter。"""

    path = suite_root / "suite_manifest.json"
    if not path.is_file():
        raise ValueError("required evidence file is missing: suite_manifest.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    frozen = body.get("suite_identity")
    if not isinstance(frozen, Mapping):
        raise ValueError("suite identity evidence is missing")
    result: dict[str, Any] = {}
    for name in (
        "suite",
        "dispatch",
        "budget",
        "catalog",
        "identity",
        "request_limits",
        "hard_limits",
    ):
        component = frozen.get(name)
        if not isinstance(component, Mapping) or "body" not in component:
            raise ValueError(f"suite identity evidence is missing: {name}")
        result[name] = component["body"]
    return result


_REPLAY_COMPARISON_FIELDS = (
    "suite_id",
    "status",
    "experiment_ids",
    "condition_count",
    "run_count",
    "task_count",
    "provider_attempt_count",
    "total_tokens",
    "total_cost_estimate",
    "cost_estimate_by_currency",
    "total_cost_estimate_status",
    "paper_eligible",
    "error_summary",
)


def _verified_replay_summary(
    suite_root: Path,
) -> tuple[PaperSuiteResult, dict[str, Any], dict[str, Any]]:
    """校验 frozen evidence，并拒绝 runner summary 与独立复算结果漂移。"""

    bodies = _stored_evidence_bodies(suite_root)
    FormalEvidenceStore.load(
        output_root=suite_root,
        **{f"expected_{name}": body for name, body in bodies.items()},
    )
    persisted = _suite_result_from_evidence(suite_root)
    recomputed = _independently_recomputed_suite_summary(
        suite_root=suite_root,
        bodies=bodies,
    )
    mismatched_fields = _replay_summary_mismatches(
        persisted=persisted.to_dict(),
        recomputed=recomputed,
    )
    comparison = {
        "status": "matched" if not mismatched_fields else "mismatched",
        "compared_fields": list(_REPLAY_COMPARISON_FIELDS),
        "mismatched_fields": mismatched_fields,
    }
    if mismatched_fields:
        raise ValueError(
            "formal runner result does not match independently recomputed evidence: "
            + ", ".join(mismatched_fields)
        )
    return persisted, recomputed, comparison


def _independently_recomputed_suite_summary(
    *,
    suite_root: Path,
    bodies: Mapping[str, Any],
) -> dict[str, Any]:
    """从 frozen dispatch 和 CURRENT generation evidence 重算 suite 论文汇总。"""

    suite_manifest = _required_json_object(
        suite_root / "suite_manifest.json",
        "suite manifest",
    )
    frozen_suite = _required_replay_mapping(bodies.get("suite"), "frozen suite")
    dispatch = _required_replay_mapping(bodies.get("dispatch"), "frozen dispatch")
    plans = dispatch.get("plans", dispatch.get("dispatch_plans"))
    if not isinstance(plans, list):
        raise ValueError("frozen dispatch plans are missing")

    root_case_filter = frozen_suite.get("root_case_filter")
    if root_case_filter is not None and not isinstance(root_case_filter, Mapping):
        raise ValueError("frozen root case filter is invalid")
    filtered_cases = root_case_filter or {}
    formal_execution = (
        suite_manifest.get("formal") is True
        and suite_manifest.get("pilot_only") is False
        and suite_manifest.get("regression_only") is False
        and suite_manifest.get("capturing") is False
    )
    hard_limits = _required_replay_mapping(
        bodies.get("hard_limits"),
        "frozen hard limits",
    )
    partial_checkpoint_prefix_allowed = (
        suite_manifest.get("capturing") is True
        and suite_manifest.get("regression_only") is True
        and hard_limits.get("stop_after_current_task") is True
    )

    condition_summaries: list[dict[str, Any]] = []
    source_refs: list[dict[str, Any]] = []
    expected_condition_keys: set[tuple[str, str, str]] = set()
    experiment_ids: list[str] = []
    experiment_conditions: dict[str, list[dict[str, Any]]] = {}
    any_blocked_plan = False
    all_plans_paper_eligible = True
    total_tokens = 0
    legacy_cost = 0.0
    currency_costs: dict[str, float] = {}
    usage_missing_count = 0

    terminal_failure = suite_manifest.get("terminal_failure")
    if terminal_failure is not None and not isinstance(terminal_failure, Mapping):
        raise ValueError("suite terminal failure evidence is invalid")

    for raw_plan in plans:
        plan = _required_replay_mapping(raw_plan, "frozen dispatch plan")
        experiment_id = _required_replay_string(
            plan.get("experiment_id"),
            "dispatch experiment_id",
        )
        if experiment_id in experiment_ids:
            raise ValueError("duplicate replay experiment_id")
        experiment_ids.append(experiment_id)
        plan_status = _required_replay_string(
            plan.get("status"),
            "dispatch plan status",
        )
        any_blocked_plan = any_blocked_plan or plan_status == "blocked"
        all_plans_paper_eligible = (
            all_plans_paper_eligible
            and plan_status != "blocked"
            and plan.get("paper_eligible_possible") is True
        )
        raw_conditions = plan.get("conditions")
        raw_bindings = plan.get("condition_selection_bindings")
        if not isinstance(raw_conditions, list) or not isinstance(raw_bindings, list):
            raise ValueError("frozen dispatch condition inventory is invalid")
        bindings = {
            _required_replay_string(
                _required_replay_mapping(binding, "condition binding").get(
                    "condition_id"
                ),
                "condition binding condition_id",
            ): _required_replay_mapping(binding, "condition binding")
            for binding in raw_bindings
        }
        if len(bindings) != len(raw_bindings):
            raise ValueError("duplicate frozen condition binding")

        per_experiment: list[dict[str, Any]] = []
        for raw_condition in raw_conditions:
            condition = _required_replay_mapping(
                raw_condition,
                "frozen condition",
            )
            condition_id = _required_replay_string(
                condition.get("condition_id"),
                "condition_id",
            )
            repeat_id = condition.get("repeat_id")
            if isinstance(repeat_id, bool) or not isinstance(repeat_id, (int, str)):
                raise ValueError("condition repeat_id is invalid")
            binding = bindings.get(condition_id)
            if binding is None:
                raise ValueError("frozen condition binding is missing")
            selection = _required_replay_mapping(
                binding.get("selection"),
                "frozen condition selection",
            )
            selected = filtered_cases.get(
                condition_id,
                selection.get("ordered_case_ids"),
            )
            if not isinstance(selected, (list, tuple)) or any(
                not isinstance(task_id, str) or not task_id for task_id in selected
            ):
                raise ValueError("frozen selected task inventory is invalid")
            expected_task_ids = tuple(str(task_id) for task_id in selected)
            if len(set(expected_task_ids)) != len(expected_task_ids):
                raise ValueError("frozen selected task inventory has duplicates")

            run = _current_replay_run_evidence(
                suite_root=suite_root,
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=repeat_id,
            )
            tasks = run["tasks"]
            attempts = run["attempts"]
            actual_task_ids = tuple(str(task.get("task_id")) for task in tasks)
            if len(set(actual_task_ids)) != len(actual_task_ids):
                raise ValueError("canonical task inventory contains duplicates")
            if partial_checkpoint_prefix_allowed:
                task_inventory_matches = (
                    bool(actual_task_ids)
                    and actual_task_ids
                    == expected_task_ids[: len(actual_task_ids)]
                )
            else:
                task_inventory_matches = (
                    len(actual_task_ids) == len(expected_task_ids)
                    and set(actual_task_ids) == set(expected_task_ids)
                )
            if not task_inventory_matches:
                raise ValueError(
                    "canonical task inventory does not match frozen selection"
                )
            source_refs.extend(run["source_refs"])
            completed = sum(
                _status_value(task.get("root_status") or "") == "completed"
                for task in tasks
            )
            blocked = sum(
                _status_value(task.get("root_status") or "") == "blocked"
                for task in tasks
            )
            failed = max(0, len(expected_task_ids) - completed - blocked)
            dependency_blocked = any(
                task.get("outcome_status") == "blocked_dependency"
                for task in tasks
            )
            if any(
                _status_value(task.get("root_status") or "")
                == "budget_exhausted"
                for task in tasks
            ):
                condition_status = "budget_exhausted"
            elif dependency_blocked:
                condition_status = "blocked"
            elif completed == len(expected_task_ids):
                condition_status = "completed"
            elif blocked == len(expected_task_ids):
                condition_status = "blocked"
            else:
                condition_status = "completed_with_failures"

            audit = _audit_persisted_condition_evidence(
                suite_root=suite_root,
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=int(repeat_id),
                expected_task_ids=expected_task_ids,
            )
            condition_paper_eligible = (
                formal_execution and audit.get("paper_eligible") is True
            )
            provider_attempt_count = _persisted_provider_attempt_count(attempts)
            for task in tasks:
                task_tokens = task.get("total_tokens")
                if task_tokens is not None:
                    if (
                        isinstance(task_tokens, bool)
                        or not isinstance(task_tokens, int)
                        or task_tokens < 0
                    ):
                        raise ValueError("canonical task total_tokens is invalid")
                    total_tokens += task_tokens
                task_cost = task.get("cost_estimate")
                if task_cost is not None:
                    if (
                        isinstance(task_cost, bool)
                        or not isinstance(task_cost, (int, float))
                        or float(task_cost) < 0.0
                    ):
                        raise ValueError("canonical task cost_estimate is invalid")
                    currency = task.get("cost_estimate_currency")
                    if currency is None:
                        legacy_cost += float(task_cost)
                    elif isinstance(currency, str) and currency:
                        currency_costs[currency] = (
                            currency_costs.get(currency, 0.0) + float(task_cost)
                        )
                    else:
                        raise ValueError(
                            "canonical task cost_estimate_currency is invalid"
                        )
                if task.get("cost_estimate_status") == "usage_missing":
                    usage_missing_count += 1

            summary = {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "status": condition_status,
                "task_count": len(expected_task_ids),
                "completed_root_count": completed,
                "failed_root_count": failed,
                "blocked_root_count": blocked,
                "provider_attempt_count": provider_attempt_count,
                "paper_eligible": condition_paper_eligible,
                "dependency_blocked": dependency_blocked,
            }
            condition_summaries.append(summary)
            per_experiment.append(summary)
            expected_condition_keys.add(
                (experiment_id, condition_id, str(repeat_id))
            )
        if set(bindings) != {
            summary["condition_id"] for summary in per_experiment
        }:
            raise ValueError("frozen condition binding inventory mismatch")
        experiment_conditions[experiment_id] = per_experiment

    _validate_recomputed_condition_rows(
        suite_root=suite_root,
        expected_condition_keys=expected_condition_keys,
        condition_summaries=condition_summaries,
    )

    if terminal_failure is not None:
        _validate_terminal_failure_against_checkpoint_tasks(
            terminal_failure=terminal_failure,
            condition_summaries=condition_summaries,
            suite_root=suite_root,
        )
        status = _required_replay_string(
            suite_manifest.get("status"),
            "suite manifest terminal status",
        )
        if status not in {"blocked", "incomplete"}:
            raise ValueError("suite terminal status is inconsistent with failure evidence")
        error_summary = [dict(terminal_failure)]
    else:
        status = _recomputed_suite_status(
            condition_statuses=[
                str(summary["status"]) for summary in condition_summaries
            ],
            has_plans=bool(plans),
            any_blocked_plan=any_blocked_plan,
        )
        error_summary = []

    paper_eligible = (
        formal_execution
        and bool(condition_summaries)
        and all_plans_paper_eligible
        and len(condition_summaries) == len(expected_condition_keys)
        and all(
            summary["paper_eligible"] is True for summary in condition_summaries
        )
        and terminal_failure is None
    )
    _validate_recomputed_experiment_manifests(
        suite_root=suite_root,
        experiment_conditions=experiment_conditions,
        plans=plans,
        terminal_status=status if terminal_failure is not None else None,
        formal_execution=formal_execution,
    )
    if suite_manifest.get("status") != status:
        raise ValueError("suite manifest status does not match canonical evidence")
    if suite_manifest.get("paper_eligible") is not paper_eligible:
        raise ValueError(
            "suite manifest paper eligibility does not match canonical evidence"
        )

    if len(currency_costs) > 1:
        total_cost_estimate = 0.0
        total_cost_estimate_status = "mixed_currency_not_aggregated"
    elif currency_costs:
        total_cost_estimate = next(iter(currency_costs.values()))
        total_cost_estimate_status = (
            "usage_missing"
            if usage_missing_count
            else "single_currency_estimate"
        )
    else:
        total_cost_estimate = legacy_cost
        total_cost_estimate_status = (
            "usage_missing"
            if usage_missing_count
            else "single_currency_or_legacy"
        )

    canonical_evidence = {
        "suite_manifest": _suite_file_ref(suite_root, "suite_manifest.json"),
        "condition_results": _suite_file_ref(
            suite_root,
            "condition_results.jsonl",
        ),
        "current_run_sources": sorted(
            source_refs,
            key=lambda item: str(item["path"]),
        ),
    }
    return {
        "schema_version": "tokenshare.paper_recomputed_suite_summary.v1",
        "suite_id": _required_replay_string(
            frozen_suite.get("suite_id"),
            "frozen suite_id",
        ),
        "status": status,
        "experiment_ids": experiment_ids,
        "condition_count": len(expected_condition_keys),
        "run_count": len(expected_condition_keys),
        "task_count": sum(
            int(summary["task_count"]) for summary in condition_summaries
        ),
        "provider_attempt_count": sum(
            int(summary["provider_attempt_count"])
            for summary in condition_summaries
        ),
        "total_tokens": total_tokens,
        "total_cost_estimate": total_cost_estimate,
        "cost_estimate_by_currency": (
            dict(sorted(currency_costs.items())) if currency_costs else None
        ),
        "total_cost_estimate_status": total_cost_estimate_status,
        "paper_eligible": paper_eligible,
        "error_summary": error_summary,
        "condition_summaries": condition_summaries,
        "canonical_evidence_digest": _digest_replay_summary(canonical_evidence),
    }


def _current_replay_run_evidence(
    *,
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    repeat_id: int | str,
) -> dict[str, Any]:
    run_root = (
        suite_root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    tasks = FormalEvidenceStore(suite_root)._validate_run(
        run_root,
        experiment_id=experiment_id,
    )
    pointer = _required_json_object(run_root / "CURRENT.json", "run CURRENT")
    generation_id = _required_replay_string(
        pointer.get("generation_id"),
        "run generation_id",
    )
    generation = run_root / ".generations" / generation_id
    records = {
        "tasks": tasks,
        "attempts": _read_jsonl_records(
            generation / "per_attempt_results.jsonl"
        ),
        "events": _read_jsonl_records(generation / "events" / "event_log.jsonl"),
        "artifacts": _read_jsonl_records(
            generation / "artifacts" / "artifact_index.jsonl"
        ),
    }
    relative_paths = (
        "run_manifest.json",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
    )
    records["source_refs"] = [
        _suite_file_ref(
            suite_root,
            (generation / relative_path).relative_to(suite_root).as_posix(),
        )
        for relative_path in relative_paths
    ]
    return records


def _validate_recomputed_condition_rows(
    *,
    suite_root: Path,
    expected_condition_keys: set[tuple[str, str, str]],
    condition_summaries: Sequence[Mapping[str, Any]],
) -> None:
    rows = _read_jsonl_records(suite_root / "condition_results.jsonl")
    indexed: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("experiment_id")),
            str(row.get("condition_id")),
            str(row.get("repeat_id")),
        )
        if key in indexed:
            raise ValueError("duplicate persisted condition result")
        indexed[key] = row
    if set(indexed) != expected_condition_keys:
        raise ValueError("persisted condition result inventory mismatch")
    for summary in condition_summaries:
        key = (
            str(summary["experiment_id"]),
            str(summary["condition_id"]),
            str(summary["repeat_id"]),
        )
        row = indexed[key]
        expected = {
            "status": summary["status"],
            "repeat_count": 1,
            "task_count": summary["task_count"],
            "completed_root_count": summary["completed_root_count"],
            "failed_root_count": summary["failed_root_count"],
            "blocked_root_count": summary["blocked_root_count"],
            "provider_attempt_count": summary["provider_attempt_count"],
            "paper_eligible": summary["paper_eligible"],
        }
        for field_name, expected_value in expected.items():
            if row.get(field_name) != expected_value:
                raise ValueError(
                    "persisted condition result does not match canonical evidence: "
                    + field_name
                )


def _validate_recomputed_experiment_manifests(
    *,
    suite_root: Path,
    experiment_conditions: Mapping[str, Sequence[Mapping[str, Any]]],
    plans: Sequence[Any],
    terminal_status: str | None,
    formal_execution: bool,
) -> None:
    plan_by_id = {
        str(_required_replay_mapping(plan, "dispatch plan")["experiment_id"]):
        _required_replay_mapping(plan, "dispatch plan")
        for plan in plans
    }
    for experiment_id, conditions in experiment_conditions.items():
        manifest = _required_json_object(
            suite_root
            / "experiments"
            / experiment_id
            / "experiment_manifest.json",
            "experiment manifest",
        )
        plan = plan_by_id[experiment_id]
        if plan.get("status") == "blocked":
            expected_status = "blocked"
        elif terminal_status is not None and any(
            condition.get("dependency_blocked") is True
            for condition in conditions
        ):
            expected_status = (
                "incomplete" if terminal_status == "incomplete" else "blocked"
            )
        else:
            expected_status = _recomputed_suite_status(
                condition_statuses=[
                    str(condition["status"]) for condition in conditions
                ],
                has_plans=True,
                any_blocked_plan=False,
            )
        expected_paper_eligible = (
            formal_execution
            and plan.get("paper_eligible_possible") is True
            and bool(conditions)
            and all(
                condition.get("paper_eligible") is True
                for condition in conditions
            )
            and terminal_status is None
        )
        if manifest.get("status") != expected_status:
            raise ValueError(
                "experiment manifest status does not match canonical evidence"
            )
        if manifest.get("paper_eligible") is not expected_paper_eligible:
            raise ValueError(
                "experiment manifest paper eligibility does not match canonical evidence"
            )


def _validate_terminal_failure_against_checkpoint_tasks(
    *,
    terminal_failure: Mapping[str, Any],
    condition_summaries: Sequence[Mapping[str, Any]],
    suite_root: Path,
) -> None:
    required_fields = (
        "outcome_status",
        "evidence_integrity",
        "failure_stage",
        "failure_kind",
    )
    if any(not isinstance(terminal_failure.get(field), str) for field in required_fields):
        raise ValueError("suite terminal failure summary is incomplete")
    candidates: list[dict[str, Any]] = []
    for summary in condition_summaries:
        run = _current_replay_run_evidence(
            suite_root=suite_root,
            experiment_id=str(summary["experiment_id"]),
            condition_id=str(summary["condition_id"]),
            repeat_id=summary["repeat_id"],
        )
        candidates.extend(run["tasks"])
    for task in candidates:
        if all(
            task.get(field_name) == terminal_failure.get(field_name)
            for field_name in required_fields
        ) and (
            terminal_failure.get("condition_id") is None
            or task.get("condition_id") == terminal_failure.get("condition_id")
        ) and (
            terminal_failure.get("task_id") is None
            or task.get("task_id") == terminal_failure.get("task_id")
        ):
            return
    raise ValueError("suite terminal failure is not backed by checkpoint task evidence")


def _recomputed_suite_status(
    *,
    condition_statuses: Sequence[str],
    has_plans: bool,
    any_blocked_plan: bool,
) -> str:
    if not condition_statuses:
        return "blocked" if has_plans else "completed"
    statuses = set(condition_statuses)
    if "budget_exhausted" in statuses:
        return "budget_exhausted"
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses or any_blocked_plan:
        return "blocked"
    if statuses.intersection({"planned", "running", "incomplete"}):
        return "incomplete"
    if "completed_with_failures" in statuses:
        return "completed_with_failures"
    return "completed"


def _replay_summary_mismatches(
    *,
    persisted: Mapping[str, Any],
    recomputed: Mapping[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for field_name in _REPLAY_COMPARISON_FIELDS:
        persisted_value = (
            persisted.get(field_name, "single_currency_or_legacy")
            if field_name == "total_cost_estimate_status"
            else persisted.get(field_name)
        )
        recomputed_value = recomputed.get(field_name)
        if field_name == "cost_estimate_by_currency":
            if not _replay_cost_mapping_equal(persisted_value, recomputed_value):
                mismatches.append(field_name)
        elif field_name == "total_cost_estimate":
            if not _replay_float_equal(persisted_value, recomputed_value):
                mismatches.append(field_name)
        elif persisted_value != recomputed_value:
            mismatches.append(field_name)
    return mismatches


def _replay_cost_mapping_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return set(left) == set(right) and all(
        _replay_float_equal(left[currency], right[currency])
        for currency in left
    )


def _replay_float_equal(left: Any, right: Any) -> bool:
    if (
        isinstance(left, bool)
        or isinstance(right, bool)
        or not isinstance(left, (int, float))
        or not isinstance(right, (int, float))
    ):
        return False
    return abs(float(left) - float(right)) <= 1e-12


def _digest_replay_summary(body: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _required_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"{label} must be a JSON object")
    return body


def _required_replay_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _required_replay_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _suite_result_from_evidence(suite_root: Path) -> PaperSuiteResult:
    path = suite_root / "formal_runner_result.json"
    if not path.is_file():
        raise ValueError("required evidence file is missing: formal_runner_result.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, Mapping):
        raise ValueError("formal runner result must be a JSON object")
    return PaperSuiteResult(
        suite_id=str(body["suite_id"]),
        status=PaperStatus(str(body["status"])),
        output_root=str(body["output_root"]),
        started_at=str(body["started_at"]),
        ended_at=body.get("ended_at"),
        experiment_ids=tuple(str(item) for item in body.get("experiment_ids", ())),
        condition_count=int(body["condition_count"]),
        run_count=int(body["run_count"]),
        task_count=int(body["task_count"]),
        provider_attempt_count=int(body["provider_attempt_count"]),
        total_tokens=int(body["total_tokens"]),
        total_cost_estimate=float(body["total_cost_estimate"]),
        paper_eligible=body["paper_eligible"] is True,
        eligibility_report_ref=_mapping_or_none(body.get("eligibility_report_ref")),
        budget_ref=_mapping_or_none(body.get("budget_ref")),
        metrics_refs=tuple(_as_json(item) for item in body.get("metrics_refs", ())),
        audit_refs=tuple(_as_json(item) for item in body.get("audit_refs", ())),
        error_summary=tuple(_as_json(item) for item in body.get("error_summary", ())),
        model_policy_preflight=_mapping_or_none(body.get("model_policy_preflight")),
        model_endpoint_cohort_preflight=_mapping_or_none(
            body.get("model_endpoint_cohort_preflight")
        ),
        cost_estimate_by_currency=_mapping_or_none(
            body.get("cost_estimate_by_currency")
        ),
        total_cost_estimate_status=str(
            body.get("total_cost_estimate_status", "single_currency_or_legacy")
        ),
        schema_version=str(body.get("schema_version", "tokenshare.paper_suite_result.v1")),
    )


def _formal_suite_closure_complete(
    *,
    suite_root: Path,
    plans: Sequence[PaperExperimentDispatchPlan],
) -> bool:
    """确认三层终态都已提交；缺任何一层时必须由 checkpoint 重建。"""

    try:
        persisted_result = _suite_result_from_evidence(suite_root)
        suite_manifest = _required_json_object(
            suite_root / "suite_manifest.json",
            "suite manifest",
        )
        if suite_manifest.get("status") != _status_value(persisted_result.status):
            return False
        if suite_manifest.get("status") in {"planned", "running"}:
            return False
        rows = _read_jsonl_records(suite_root / "condition_results.jsonl")
        row_keys = {
            (
                str(row["experiment_id"]),
                str(row["condition_id"]),
                str(row["repeat_id"]),
            )
            for row in rows
        }
        if len(row_keys) != len(rows):
            return False
        expected_keys = {
            (
                plan.experiment_id,
                condition.condition_id,
                str(condition.repeat_id),
            )
            for plan in plans
            if plan.status == "planned"
            for condition in plan.conditions
        }
        if not expected_keys.issubset(row_keys):
            return False
        for plan in plans:
            manifest = _required_json_object(
                suite_root
                / "experiments"
                / plan.experiment_id
                / "experiment_manifest.json",
                "experiment manifest",
            )
            if manifest.get("status") in {"planned", "running"}:
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _usage_from_evidence(suite_root: Path) -> _UsageTotals:
    """resume 时以已持久化 suite result 继续累计硬预算。"""

    path = suite_root / "formal_runner_result.json"
    if not path.is_file():
        return _usage_from_current_checkpoints(suite_root)
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, Mapping):
        raise ValueError("formal runner result must be a JSON object")
    currency_costs = body.get("cost_estimate_by_currency")
    normalized_currency_costs = (
        {
            str(currency): float(value)
            for currency, value in currency_costs.items()
        }
        if isinstance(currency_costs, Mapping)
        else {}
    )
    return _UsageTotals(
        provider_attempt_count=int(body.get("provider_attempt_count", 0)),
        total_tokens=int(body.get("total_tokens", 0)),
        total_cost_estimate=(
            0.0
            if normalized_currency_costs
            else float(body.get("total_cost_estimate", 0.0))
        ),
        cost_estimate_by_currency=normalized_currency_costs,
        usage_missing_count=(
            1 if body.get("total_cost_estimate_status") == "usage_missing" else 0
        ),
    )


def _usage_from_current_checkpoints(suite_root: Path) -> _UsageTotals:
    """suite finalizer 未提交时，从每个 canonical CURRENT generation 恢复 usage。"""

    usage = _UsageTotals()
    for pointer_path in sorted(
        suite_root.glob("experiments/*/runs/*/*/CURRENT.json")
    ):
        pointer = _required_json_object(pointer_path, "condition CURRENT pointer")
        generation_root = (
            pointer_path.parent / ".generations" / str(pointer["generation_id"])
        )
        for attempt in _read_jsonl_records(
            generation_root / "per_attempt_results.jsonl"
        ):
            if attempt.get("record_scope") != "protocol":
                continue
            usage.provider_attempt_count += _persisted_provider_attempt_count(
                (attempt,)
            )
            total_tokens = attempt.get("total_tokens", 0)
            if isinstance(total_tokens, int) and not isinstance(total_tokens, bool):
                usage.total_tokens += max(0, total_tokens)
            cost_estimate = attempt.get("cost_estimate", 0.0)
            normalized_cost = (
                float(cost_estimate)
                if isinstance(cost_estimate, (int, float))
                and not isinstance(cost_estimate, bool)
                else 0.0
            )
            currency = attempt.get("cost_estimate_currency")
            if isinstance(currency, str) and currency:
                usage.cost_estimate_by_currency[currency] = (
                    usage.cost_estimate_by_currency.get(currency, 0.0)
                    + normalized_cost
                )
            else:
                usage.total_cost_estimate += normalized_cost
            if attempt.get("cost_estimate_status") == "usage_missing":
                usage.usage_missing_count += 1
    return usage


def _all_selected_roots_completed(
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    completed_task_keys: set[tuple[str, str, str, str]],
    *,
    root_case_filter: Mapping[str, tuple[str, ...]],
) -> bool:
    return all(
        _formal_task_key(condition, case_id) in completed_task_keys
        for plan, items in bound_plans
        if plan.status == "planned"
        for condition, selection in items
        for case_id in root_case_filter.get(
            condition.condition_id,
            tuple(selection.ordered_case_ids),
        )
    )


def _formal_task_key(condition: Any, task_id: str) -> tuple[str, str, str, str]:
    return (
        str(condition.experiment_id),
        str(condition.condition_id),
        str(condition.repeat_id),
        str(task_id),
    )


def _root_budget_reservation(
    *,
    case: Mapping[str, Any],
    request_limits: Mapping[str, Any],
    budget: PaperBudgetResult,
    currency: str | None,
) -> _RootBudgetReservation:
    """按冻结 case 和 suite budget 上界预留一个 root 的并发额度。"""

    ai_unit_count = (
        estimated_ai_units_for_case(dict(case))
        if "schema_version" in case or "expected_ai_unit_count" in case
        else 1
    )
    attempts_per_unit = int(request_limits.get("max_provider_attempts", 1))
    attempt_count = ai_unit_count * max(1, attempts_per_unit)
    if budget.max_provider_attempts > 0:
        token_upper_per_attempt = (
            budget.token_upper_bound + budget.max_provider_attempts - 1
        ) // budget.max_provider_attempts
        cost_upper_per_attempt = (
            budget.cost_upper_bound / budget.max_provider_attempts
        )
    else:
        token_upper_per_attempt = int(request_limits.get("max_tokens", 0))
        cost_upper_per_attempt = 0.0
    return _RootBudgetReservation(
        provider_attempt_count=attempt_count,
        total_tokens=attempt_count * token_upper_per_attempt,
        total_cost_estimate=attempt_count * cost_upper_per_attempt,
        currency=currency,
    )


def _config_currency(config: AIAPIExecutorConfig) -> str | None:
    currencies = {
        str(entry.pricing["currency"])
        for entry in config.entries
        if entry.enabled and isinstance(entry.pricing.get("currency"), str)
    }
    if len(currencies) > 1:
        raise ValueError("one prepared provider config cannot mix pricing currencies")
    return next(iter(currencies), None)


def _has_resource_hard_limit(hard_limits: Mapping[str, Any]) -> bool:
    return any(
        isinstance(hard_limits.get(field_name), (int, float))
        and not isinstance(hard_limits.get(field_name), bool)
        for field_name in (
            "max_total_provider_attempts",
            "max_provider_attempts",
            "max_total_tokens",
            "max_tokens",
            "max_cost_estimate",
            "max_total_cost_estimate",
        )
    )


def _reserve_hard_limit_capacity(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
    hard_limits: Mapping[str, Any],
) -> bool:
    projected_costs = _projected_cost_estimates_by_currency(
        usage=usage,
        reservation=reservation,
    )
    projected = (
        (
            "max_total_provider_attempts",
            usage.provider_attempt_count
            + usage.reserved_provider_attempt_count
            + reservation.provider_attempt_count,
        ),
        (
            "max_provider_attempts",
            usage.provider_attempt_count
            + usage.reserved_provider_attempt_count
            + reservation.provider_attempt_count,
        ),
        (
            "max_total_tokens",
            usage.total_tokens
            + usage.reserved_total_tokens
            + reservation.total_tokens,
        ),
        (
            "max_tokens",
            usage.total_tokens
            + usage.reserved_total_tokens
            + reservation.total_tokens,
        ),
    )
    for field_name, value in projected:
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and value > limit
        ):
            return False
    for field_name in ("max_cost_estimate", "max_total_cost_estimate"):
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and any(value > limit for value in projected_costs.values())
        ):
            return False
    usage.reserved_provider_attempt_count += reservation.provider_attempt_count
    usage.reserved_total_tokens += reservation.total_tokens
    if reservation.currency is None:
        usage.reserved_total_cost_estimate += reservation.total_cost_estimate
    else:
        usage.reserved_cost_estimate_by_currency[reservation.currency] = (
            usage.reserved_cost_estimate_by_currency.get(reservation.currency, 0.0)
            + reservation.total_cost_estimate
        )
    return True


def _settle_hard_limit_reservation(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
    provider_attempt_count: int = 0,
    total_tokens: int = 0,
    total_cost_estimate: float = 0.0,
    cost_estimate_currency: str | None = None,
    cost_estimate_status: str | None = None,
) -> None:
    usage.reserved_provider_attempt_count -= reservation.provider_attempt_count
    usage.reserved_total_tokens -= reservation.total_tokens
    if reservation.currency is None:
        usage.reserved_total_cost_estimate -= reservation.total_cost_estimate
    else:
        remaining = (
            usage.reserved_cost_estimate_by_currency.get(reservation.currency, 0.0)
            - reservation.total_cost_estimate
        )
        if abs(remaining) <= 1e-12:
            usage.reserved_cost_estimate_by_currency.pop(reservation.currency, None)
        else:
            usage.reserved_cost_estimate_by_currency[reservation.currency] = remaining
    usage.provider_attempt_count += provider_attempt_count
    usage.total_tokens += total_tokens
    if cost_estimate_currency is None:
        usage.total_cost_estimate += total_cost_estimate
    else:
        if reservation.currency not in (None, cost_estimate_currency):
            raise ValueError("provider usage currency drifted from the reservation")
        usage.cost_estimate_by_currency[cost_estimate_currency] = (
            usage.cost_estimate_by_currency.get(cost_estimate_currency, 0.0)
            + total_cost_estimate
        )
    if cost_estimate_status == "usage_missing":
        usage.usage_missing_count += 1


def _hard_limit_reached(usage: _UsageTotals, hard_limits: Mapping[str, Any]) -> bool:
    limits = (
        ("max_total_provider_attempts", usage.provider_attempt_count),
        ("max_provider_attempts", usage.provider_attempt_count),
        ("max_total_tokens", usage.total_tokens),
        ("max_tokens", usage.total_tokens),
    )
    for field_name, consumed in limits:
        limit = hard_limits.get(field_name)
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and consumed >= limit:
            return True
    cost_values = (
        tuple(usage.cost_estimate_by_currency.values())
        if usage.cost_estimate_by_currency
        else (usage.total_cost_estimate,)
    )
    for field_name in ("max_cost_estimate", "max_total_cost_estimate"):
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and any(consumed >= limit for consumed in cost_values)
        ):
            return True
    return False


def _projected_cost_estimates_by_currency(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
) -> dict[str, float]:
    if reservation.currency is None:
        return {
            "legacy_or_unspecified": (
                usage.total_cost_estimate
                + usage.reserved_total_cost_estimate
                + reservation.total_cost_estimate
            )
        }
    currencies = set(usage.cost_estimate_by_currency) | set(
        usage.reserved_cost_estimate_by_currency
    )
    currencies.add(reservation.currency)
    return {
        currency: (
            usage.cost_estimate_by_currency.get(currency, 0.0)
            + usage.reserved_cost_estimate_by_currency.get(currency, 0.0)
            + (
                reservation.total_cost_estimate
                if currency == reservation.currency
                else 0.0
            )
        )
        for currency in currencies
    }


def _condition_result(
    *,
    condition: Any,
    selection: Any,
    completed: int,
    blocked: int,
    failed: int,
    provider_attempts: int,
    paper_eligible: bool,
    status: PaperStatus,
) -> PaperConditionResult:
    return PaperConditionResult(
        condition_id=condition.condition_id,
        status=status,
        repeat_count=1,
        task_count=len(selection.ordered_case_ids),
        completed_root_count=completed,
        failed_root_count=failed,
        blocked_root_count=blocked,
        provider_attempt_count=provider_attempts,
        metrics_ref={"paper_eligible": paper_eligible},
    )


def _record_with_context(value: Any, *, condition: Any, task_id: str) -> dict[str, Any]:
    record = _as_json(value)
    if not isinstance(record, Mapping):
        raise ValueError("adapter evidence record must be an object")
    contextualized = {
        **dict(record),
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
    }
    protocol_task_id = record.get("task_id")
    if isinstance(protocol_task_id, str) and protocol_task_id != task_id:
        contextualized.setdefault("protocol_task_id", protocol_task_id)
    return contextualized


def _event_record_with_context(
    value: Any,
    *,
    condition: Any,
    task_id: str,
) -> dict[str, Any]:
    """协议 ledger event 属于哈希体；实验 case 映射由 evidence store 旁路保存。"""

    record = _as_json(value)
    if not isinstance(record, Mapping):
        raise ValueError("adapter evidence event must be an object")
    if _is_protocol_ledger_event(record):
        return dict(record)
    return _record_with_context(record, condition=condition, task_id=task_id)


def _attempt_checkpoint_ineligibility_reasons(
    *,
    attempt: Mapping[str, Any],
    protocol_runtime: Mapping[str, Any] | None,
    real_transport: bool,
    transport: Any,
    source_attempt_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    if not real_transport or _is_offline_capturing_transport(transport):
        reasons.append("capturing_or_non_real_transport")
    if protocol_runtime is None or attempt.get("record_scope") != "protocol":
        reasons.append("attempt_not_from_protocol_runtime")
    if protocol_runtime is not None and (
        protocol_runtime.get("execution_scope") != "whole_root"
        or bool(protocol_runtime.get("selected_ai_unit_ids"))
    ):
        reasons.append("partial_or_selected_unit_execution_scope")
    if not source_attempt_eligible:
        reasons.append("source_attempt_evidence_incomplete")
    for field_name in (
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "worker_id",
        "started_at",
        "ended_at",
    ):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"missing_attempt_{field_name}")
    for field_name in (
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        if not _complete_evidence_ref(attempt.get(field_name)):
            reasons.append(f"missing_attempt_{field_name}")
    status = _status_value(attempt.get("attempt_status") or "")
    if status in {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    } and not _complete_evidence_ref(attempt.get("raw_output_ref")):
        reasons.append("missing_attempt_raw_output_ref")
    if status == "parse_failed" and not _complete_evidence_ref(
        attempt.get("parse_failure_ref")
    ):
        reasons.append("missing_attempt_parse_failure_ref")
    provider_attempt_count = attempt.get("provider_attempt_count")
    if (
        isinstance(provider_attempt_count, bool)
        or not isinstance(provider_attempt_count, int)
        or provider_attempt_count < 1
    ):
        reasons.append("missing_provider_attempt_inventory")
    if attempt.get("synthetic") is True or attempt.get("synthetic_fallback") is True:
        reasons.append("synthetic_attempt_forbidden")
    return list(dict.fromkeys(reasons))


def _task_checkpoint_ineligibility_reasons(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    protocol_runtime: Mapping[str, Any] | None,
    real_transport: bool,
    transport: Any,
    source_task_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    if not real_transport or _is_offline_capturing_transport(transport):
        reasons.append("capturing_or_non_real_transport")
    if protocol_runtime is None or task.get("record_scope") != "protocol":
        reasons.append("task_not_from_protocol_runtime")
    if protocol_runtime is not None and (
        protocol_runtime.get("execution_scope") != "whole_root"
        or bool(protocol_runtime.get("selected_ai_unit_ids"))
    ):
        reasons.append("partial_or_selected_unit_execution_scope")
    if not source_task_eligible:
        reasons.append("source_task_evidence_incomplete")
    if _status_value(task.get("root_status") or "") == "partial":
        reasons.append("partial_root_result")
    # paper_eligible 还会被 smoke/pilot 分类强制为 false；技术完整性只看独立原因字段。
    if not attempts or any(
        not isinstance(attempt.get("paper_ineligibility_reasons"), list)
        or bool(attempt.get("paper_ineligibility_reasons"))
        for attempt in attempts
    ):
        reasons.append("attempt_evidence_incomplete")
    protocol_events = [
        event
        for event in events
        if (
            event.get("record_scope") == "protocol"
            or _is_protocol_ledger_event(event)
        )
        and isinstance(event.get("event_id"), str)
        and event.get("event_id")
    ]
    if not protocol_events:
        reasons.append("missing_protocol_lifecycle_events")
    refs = task.get("evidence_artifact_refs")
    if (
        not isinstance(refs, Sequence)
        or isinstance(refs, (str, bytes))
        or not refs
        or any(not _complete_evidence_ref(ref) for ref in refs)
    ):
        reasons.append("missing_task_artifact_evidence_refs")
    if task.get("synthetic") is True or task.get("synthetic_fallback") is True:
        reasons.append("synthetic_task_forbidden")
    return list(dict.fromkeys(reasons))


def _complete_evidence_ref(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return bool(
        value.get("artifact_id")
        or (
            isinstance(value.get("path"), str)
            and value.get("path")
            and isinstance(value.get("content_hash"), str)
            and value.get("content_hash")
        )
    )


def _evidence_flags(
    *,
    paper_eligible: bool = False,
    execution_classification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if execution_classification is None:
        return {
            "formal": True,
            "pilot_only": False,
            "paper_eligible": paper_eligible,
        }
    return {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": list(
            execution_classification["ineligibility_reasons"]
        ),
    }


def _sequence_field(value: Any, *field_names: str) -> tuple[Any, ...]:
    for field_name in field_names:
        result = _optional_field(value, field_name)
        if result is None:
            continue
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
            return tuple(result)
        raise ValueError(f"adapter field {field_name} must be a sequence")
    return ()


def _experiment_attempt_record(
    *,
    condition: Any,
    task_id: str,
    status: str,
    error_kind: str,
) -> dict[str, Any]:
    return {
        "attempt_id": (
            f"experiment-{condition.condition_id}-{condition.repeat_id}-{task_id}"
        ),
        "attempt_status": status,
        "provider_attempt_index": 0,
        "error_kind": error_kind,
        "record_scope": "experiment",
    }


def _experiment_event_record(
    *, condition: Any, task_id: str, event_type: str
) -> dict[str, Any]:
    return {
        "event_id": (
            f"experiment-{event_type.lower()}-{condition.condition_id}-"
            f"{condition.repeat_id}-{task_id}"
        ),
        "event_type": event_type,
        "record_scope": "experiment",
    }


def _adapter_artifact_refs(
    task: Any,
    attempts: Sequence[Mapping[str, Any]],
    faults: Sequence[Mapping[str, Any]],
    *,
    events: Sequence[Mapping[str, Any]] = (),
) -> tuple[Mapping[str, Any], ...]:
    refs: list[Mapping[str, Any]] = []
    for record_group in (task, attempts, faults, events):
        refs.extend(_nested_artifact_refs(record_group))
    unique: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = _artifact_ref_identity(ref)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return tuple(unique)


def _nested_artifact_refs(value: Any) -> list[Mapping[str, Any]]:
    """递归发现 ArtifactRef；其 source/metadata 仍可能继续引用 artifact。"""

    result: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if _is_artifact_ref_candidate(value):
            result.append(value)
        for child in value.values():
            result.extend(_nested_artifact_refs(child))
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for child in value:
            result.extend(_nested_artifact_refs(child))
    return result


def _is_artifact_ref_candidate(value: Mapping[str, Any]) -> bool:
    if value.get("schema_version") == "ArtifactRef.v1":
        return True
    return isinstance(value.get("uri"), str) and isinstance(
        value.get("content_hash"),
        str,
    )


def _artifact_ref_identity(ref: Mapping[str, Any]) -> tuple[str, str, str]:
    artifact_id = ref.get("artifact_id")
    uri = ref.get("uri")
    content_hash = ref.get("content_hash")
    if isinstance(artifact_id, str) and artifact_id:
        return (
            "artifact_id",
            artifact_id,
            content_hash if isinstance(content_hash, str) else "",
        )
    return (
        "uri",
        uri if isinstance(uri, str) else "",
        content_hash if isinstance(content_hash, str) else "",
    )


def _validate_complete_artifact_ref(ref: Mapping[str, Any]) -> None:
    if ref.get("schema_version") != "ArtifactRef.v1":
        return
    for field_name in (
        "artifact_id",
        "artifact_type",
        "uri",
        "content_hash",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "created_at",
    ):
        value = ref.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"complete ArtifactRef requires {field_name}")
    size_bytes = ref.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
    ):
        raise ValueError("complete ArtifactRef requires non-negative size_bytes")
    for field_name in ("source", "metadata"):
        if not isinstance(ref.get(field_name), Mapping):
            raise ValueError(f"complete ArtifactRef requires mapping {field_name}")


def _materialize_artifacts(
    *,
    suite_root: Path,
    condition: Any,
    task_id: str,
    adapter_root: Path,
    source_refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    artifact_root = (
        suite_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
        / "artifacts"
        / _artifact_task_directory(task_id)
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    queue = deque(source_refs)
    seen: set[tuple[str, str, str]] = set()
    artifact_id_hashes: dict[str, str] = {}
    while queue:
        source_ref = queue.popleft()
        _validate_complete_artifact_ref(source_ref)
        uri = source_ref.get("uri")
        expected_content_hash = source_ref.get("content_hash")
        if not isinstance(uri, str) or not uri:
            continue
        if not isinstance(expected_content_hash, str) or not expected_content_hash:
            raise ValueError(f"adapter artifact evidence hash is missing: {uri}")
        artifact_id = source_ref.get("artifact_id")
        key = _artifact_ref_identity(source_ref)
        if key in seen:
            continue
        seen.add(key)
        if isinstance(artifact_id, str) and artifact_id:
            prior_hash = artifact_id_hashes.setdefault(
                artifact_id,
                expected_content_hash,
            )
            if prior_hash != expected_content_hash:
                raise ValueError(
                    f"adapter artifact_id has conflicting content: {artifact_id}"
                )
        source = adapter_root / uri
        if not source.is_file():
            matches = _find_nested_adapter_artifacts(
                adapter_root=adapter_root,
                uri=uri,
                expected_content_hash=expected_content_hash,
            )
            if len(matches) != 1:
                raise ValueError(
                    f"adapter artifact evidence is missing: {uri}"
                )
            source = matches[0]
        source_bytes = source.read_bytes()
        if _sha256_bytes(source_bytes) != expected_content_hash:
            raise ValueError(f"adapter artifact evidence hash mismatched: {uri}")
        expected_size = source_ref.get("size_bytes")
        if expected_size is not None and (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size != len(source_bytes)
        ):
            raise ValueError(f"adapter artifact evidence size mismatched: {uri}")
        name = _content_addressed_artifact_name(
            uri=uri,
            content_hash=expected_content_hash,
            artifact_id=artifact_id,
        )
        target = artifact_root / name
        if target.is_file():
            if _sha256_bytes(target.read_bytes()) != expected_content_hash:
                raise ValueError(f"materialized artifact collision: {name}")
        else:
            shutil.copyfile(source, target)
        materialized_ref = {
            "experiment_id": condition.experiment_id,
            "condition_id": condition.condition_id,
            "repeat_id": condition.repeat_id,
            "task_id": task_id,
            "path": target.relative_to(suite_root).as_posix(),
            "content_hash": expected_content_hash,
            "size_bytes": len(source_bytes),
            "source_uri": uri,
            "source_artifact_ref": dict(source_ref),
        }
        if isinstance(artifact_id, str) and artifact_id:
            materialized_ref["artifact_id"] = artifact_id
        result.append(materialized_ref)
        queue.extend(_nested_artifact_refs(source_ref.get("source")))
        queue.extend(_nested_artifact_refs(source_ref.get("metadata")))
        declared_media_type = source_ref.get("media_type")
        declared_json = (
            isinstance(declared_media_type, str)
            and (
                declared_media_type.split(";", maxsplit=1)[0].strip().lower()
                == "application/json"
                or declared_media_type.split(";", maxsplit=1)[0]
                .strip()
                .lower()
                .endswith("+json")
            )
        )
        try:
            payload = json.loads(source_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            if declared_json:
                raise ValueError(
                    f"declared JSON artifact payload is invalid: {uri}"
                ) from error
            payload = None
        if payload is not None:
            queue.extend(_nested_artifact_refs(payload))
    result.sort(
        key=lambda item: (
            str(item.get("artifact_id", "")),
            str(item["path"]),
            str(item["content_hash"]),
        )
    )
    return result


def _content_addressed_artifact_name(
    *,
    uri: str,
    content_hash: str,
    artifact_id: Any,
) -> str:
    """用 digest 隔离同 basename artifact，并移除 Windows 非法文件名字符。"""

    digest = content_hash.removeprefix("sha256:")
    basename = Path(uri).name or "artifact"
    invalid = '<>:"/\\|?*'
    safe_basename = "".join(
        "_" if character in invalid or ord(character) < 32 else character
        for character in basename
    )[:96]
    identity_name = artifact_id if isinstance(artifact_id, str) else ""
    safe_identity = "".join(
        "_" if character in invalid or ord(character) < 32 else character
        for character in identity_name
    )[:64]
    identity_prefix = f"{safe_identity}-" if safe_identity else ""
    return f"{digest}-{identity_prefix}{safe_basename}"


def _find_nested_adapter_artifacts(
    *,
    adapter_root: Path,
    uri: str,
    expected_content_hash: Any,
) -> list[Path]:
    """定位 adapter 内层 run root；显式构造路径以兼容 Windows ADS 名称。"""

    uri_path = Path(uri)
    parts = uri_path.parts
    candidates: list[Path] = []
    if parts:
        for anchor in adapter_root.rglob(parts[0]):
            if anchor.is_dir():
                candidates.append(anchor.joinpath(*parts[1:]))
    candidates.extend(adapter_root.rglob(uri_path.name))
    matches: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen or not candidate.is_file():
            continue
        seen.add(candidate_key)
        if (
            isinstance(expected_content_hash, str)
            and _sha256_bytes(candidate.read_bytes())
            != expected_content_hash
        ):
            continue
        matches.append(candidate)
    return matches


def _write_runner_artifact(
    *,
    suite_root: Path,
    condition: Any,
    task_id: str,
    artifact_name: str,
    body: Any,
) -> dict[str, Any]:
    path = (
        suite_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
        / "artifacts"
        / _artifact_task_directory(task_id)
        / artifact_name
    )
    _write_json(path, body)
    return {
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
        "path": path.relative_to(suite_root).as_posix(),
        "content_hash": _sha256_bytes(path.read_bytes()),
    }


def _as_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _as_json(value.to_dict())
    if is_dataclass(value):
        return _as_json(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "__dict__"):
        return _as_json(vars(value))
    raise ValueError(f"value is not JSON serializable: {type(value).__name__}")


def _artifact_task_directory(task_id: str) -> str:
    """避免同一 condition 内不同 root 的同名 adapter artifact 相互覆盖。"""

    return str(task_id).replace("\\", "_").replace("/", "_")


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _string_sequence(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{label} must be a sequence")
    return tuple(str(item) for item in value)


def _adapter_store_root(
    *,
    adapter_root: Path,
    case_id: str,
    attempts: Sequence[Any],
) -> Path:
    candidates = (adapter_root, adapter_root / case_id)
    for candidate in candidates:
        for attempt in attempts:
            raw_ref = _optional_field(attempt, "raw_output_ref")
            uri = raw_ref.get("uri") if isinstance(raw_ref, Mapping) else None
            if isinstance(uri, str) and (candidate / uri).is_file():
                return candidate
    raise ValueError("adapter artifact store root cannot be resolved")


def _adapter_result_with(
    original: Any,
    *,
    task_result: Any,
    attempts: Sequence[Any],
    faults: Sequence[Any],
    events: Sequence[Any],
) -> dict[str, Any]:
    return {
        "task_result": task_result,
        "attempt_results": tuple(attempts),
        "fault_records": tuple(faults),
        "event_records": tuple(events),
        "eligibility_report": _optional_field(original, "eligibility_report"),
        "run_evidence": _optional_field(original, "run_evidence"),
        "output_root": _optional_field(original, "output_root"),
    }


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_json(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_as_json(body), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # bytes 写入既支持固定 LF 的 JSON，也支持 protected typed handle。
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, body: Any) -> None:
    _atomic_write_text(path, _serialized_json_text(body))


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _atomic_write_text(path, _serialized_jsonl_text(rows))


def _serialized_json_text(body: Any) -> str:
    return (
        json.dumps(
            _as_json(body),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _serialized_jsonl_text(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(
            _as_json(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    )


def _file_digest_or_none(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else None


def _formal_finalization_target(
    *,
    suite_root: Path,
    path: Path,
    content: str,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(suite_root).as_posix(),
        "expected_prior_digest": _file_digest_or_none(path),
        "target_digest": _sha256_bytes(content.encode("utf-8")),
        "content": content,
    }


def _apply_formal_finalization_target(
    *,
    suite_root: Path,
    target: Mapping[str, Any],
    expected_path: str,
) -> None:
    if set(target) != {
        "path",
        "expected_prior_digest",
        "target_digest",
        "content",
    }:
        raise ValueError("formal finalization target shape mismatch")
    if target.get("path") != expected_path:
        raise ValueError("formal finalization target path mismatch")
    content = target.get("content")
    target_digest = target.get("target_digest")
    prior_digest = target.get("expected_prior_digest")
    if not isinstance(content, str) or not isinstance(target_digest, str):
        raise ValueError("formal finalization target content is invalid")
    if prior_digest is not None and not isinstance(prior_digest, str):
        raise ValueError("formal finalization prior digest is invalid")
    if _sha256_bytes(content.encode("utf-8")) != target_digest:
        raise ValueError("formal finalization target digest mismatch")
    path = (suite_root / expected_path).resolve(strict=False)
    if not path.is_relative_to(suite_root.resolve(strict=False)):
        raise ValueError("formal finalization target escapes suite root")
    actual_digest = _file_digest_or_none(path)
    if actual_digest == target_digest:
        return
    if actual_digest != prior_digest:
        raise ValueError("formal finalization target prior digest conflict")
    _atomic_write_text(path, content)
    if _file_digest_or_none(path) != target_digest:
        raise ValueError("formal finalization target write verification failed")


def _formal_finalization_hook(*, stage: str, suite_root: Path) -> None:
    """供断点恢复测试注入进程中断；生产路径默认无操作。"""

    del stage, suite_root


def _repair_interrupted_formal_finalization(suite_root: Path) -> dict[str, Any] | None:
    """只按 top-level intent 补完 runner 两个明确 target，并重建 inventory。"""

    pending_path = suite_root / _FORMAL_FINALIZATION_PENDING
    if not pending_path.is_file():
        return None
    pending = _required_json_object(pending_path, "formal finalization intent")
    publication_kind = pending.get("publication_kind")
    if publication_kind == "experiment_terminal_commit":
        if set(pending) != {
            "schema_version",
            "publication_kind",
            "experiment_id",
            "condition_results_target",
            "experiment_manifest_target",
        } or pending.get("schema_version") != _FORMAL_FINALIZATION_SCHEMA:
            raise ValueError("formal finalization intent shape mismatch")
        experiment_id = pending.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ValueError("formal finalization experiment identity is invalid")
        rows_target = pending.get("condition_results_target")
        manifest_target = pending.get("experiment_manifest_target")
        if not isinstance(rows_target, Mapping) or not isinstance(
            manifest_target,
            Mapping,
        ):
            raise ValueError("formal finalization targets are invalid")
        _apply_formal_finalization_target(
            suite_root=suite_root,
            target=rows_target,
            expected_path="condition_results.jsonl",
        )
        _apply_formal_finalization_target(
            suite_root=suite_root,
            target=manifest_target,
            expected_path=(
                f"experiments/{experiment_id}/experiment_manifest.json"
            ),
        )
    elif publication_kind == "suite_terminal_commit":
        if set(pending) != {
            "schema_version",
            "publication_kind",
            "formal_runner_result_target",
            "suite_manifest_target",
        } or pending.get("schema_version") != _FORMAL_SUITE_FINALIZATION_SCHEMA:
            raise ValueError("formal suite finalization intent shape mismatch")
        result_target = pending.get("formal_runner_result_target")
        suite_target = pending.get("suite_manifest_target")
        if not isinstance(result_target, Mapping) or not isinstance(
            suite_target,
            Mapping,
        ):
            raise ValueError("formal suite finalization targets are invalid")
        _apply_formal_finalization_target(
            suite_root=suite_root,
            target=result_target,
            expected_path="formal_runner_result.json",
        )
        _apply_formal_finalization_target(
            suite_root=suite_root,
            target=suite_target,
            expected_path="suite_manifest.json",
        )
    else:
        raise ValueError("formal finalization intent schema mismatch")
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    pending_path.unlink()
    return pending


def _last_complete_event_ref(suite_root: Path) -> dict[str, Any] | None:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for pointer_path in sorted(suite_root.glob("experiments/*/runs/*/*/CURRENT.json")):
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            generation_root = (
                pointer_path.parent
                / ".generations"
                / str(pointer["generation_id"])
            )
            event_path = generation_root / "events" / "event_log.jsonl"
            for event in _read_jsonl_records(event_path):
                if event.get("event_type") in {
                    "TASK_COMPLETED",
                    "EXPERIMENT_FAULT_OBSERVED",
                }:
                    candidates.append((event_path, event))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    path, event = candidates[-1]
    return {
        "path": path.relative_to(suite_root).as_posix(),
        "event_id": event.get("event_id"),
        "task_id": event.get("task_id"),
        "record_hash": digest_json(event),
    }


_DIRECT_METRIC_INPUT_KEYS = (
    "exp1_feasibility",
    "exp2_trace_scalability",
    "exp2_online_concurrency",
    "exp3_trace_robustness",
    "exp3_online_recovery",
    "exp4_ablation",
    "experiment_5",
)
_CURRENT_PROVIDER_METRIC_ROLE_ORDER = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "provider_attempt",
    "model_record",
)
_SOURCE_BANK_METRIC_ROLE_ORDER = (
    "request_body",
    "raw_output_or_provider_failure",
    "provenance",
    "usage_status",
    "latency",
    "pricing",
    "acquisition_attempt",
    "model_record",
)


def _direct_metric_input_key(*, experiment_id: str, evidence_class: str) -> str:
    routes = {
        ("exp1_real_ai_feasibility", "online_real_provider"): "exp1_feasibility",
        ("exp2_real_ai_scalability", "real_model_trace_protocol_run"): (
            "exp2_trace_scalability"
        ),
        ("exp2_real_ai_scalability", "online_real_provider"): (
            "exp2_online_concurrency"
        ),
        ("exp3_real_ai_fault_recovery", "real_model_trace_protocol_run"): (
            "exp3_trace_robustness"
        ),
        ("exp3_real_ai_fault_recovery", "online_real_provider"): (
            "exp3_online_recovery"
        ),
        ("exp4_real_ai_protocol_ablation", "real_model_trace_protocol_run"): (
            "exp4_ablation"
        ),
        ("exp5_real_ai_model_endpoint_comparison", "online_real_provider"): (
            "experiment_5"
        ),
    }
    try:
        return routes[(experiment_id, evidence_class)]
    except KeyError as exc:
        raise ValueError(
            "no official metric input route for frozen experiment/evidence class"
        ) from exc


def _canonical_metric_inputs(
    projection_rows: Sequence[Any],
    *,
    producer_facts_by_root: Mapping[str, Mapping[str, Any]],
) -> dict[str, object]:
    routed: dict[str, list[Any]] = {key: [] for key in _DIRECT_METRIC_INPUT_KEYS}
    facts_by_root: dict[str, Mapping[str, Any]] = {}
    for row in projection_rows:
        facts = producer_facts_by_root.get(row.preregistered_root_run_id)
        if facts is None:
            if row.root_status != "not_started":
                raise ValueError("persisted metric producer facts are missing")
            facts = {}
        if not isinstance(facts, Mapping):
            raise ValueError("persisted metric producer facts are invalid")
        facts_by_root[row.preregistered_root_run_id] = facts
        sample_slot = row.condition_axes.get("sample_slot_index")
        if sample_slot != row.repeat_id:
            raise ValueError("metric repeat/sample-slot binding mismatch")
        key = _direct_metric_input_key(
            experiment_id=row.experiment_id,
            evidence_class=row.evidence_class,
        )
        routed[key].append(row)

    result: dict[str, object] = {key: () for key in _DIRECT_METRIC_INPUT_KEYS}
    result["exp1_feasibility"] = tuple(
        _hydrate_exp1_metric_row(row, facts_by_root[row.preregistered_root_run_id])
        for row in routed["exp1_feasibility"]
    )
    result["exp2_trace_scalability"] = tuple(
        _hydrate_exp2_trace_metric_row(
            row,
            facts_by_root[row.preregistered_root_run_id],
        )
        for row in routed["exp2_trace_scalability"]
    )
    result["exp2_online_concurrency"] = tuple(
        _hydrate_exp2_online_metric_row(
            row,
            facts_by_root[row.preregistered_root_run_id],
        )
        for row in routed["exp2_online_concurrency"]
    )
    trace_groups: dict[tuple[str, str, int, str], list[Any]] = {}
    for row in routed["exp3_trace_robustness"]:
        fault_type = (
            row.condition_axes.get("fault_condition")
            or row.condition_axes.get("death_condition")
        )
        sample_slot = row.condition_axes.get("sample_slot_index")
        if (
            not isinstance(fault_type, str)
            or not fault_type
            or isinstance(sample_slot, bool)
            or not isinstance(sample_slot, int)
            or sample_slot < 0
        ):
            raise ValueError("Exp3 trace metric identity is not legally typed")
        trace_groups.setdefault(
            (row.condition_id, fault_type, row.repeat_id, str(sample_slot)),
            [],
        ).append(row)
    result["exp3_trace_robustness"] = tuple(
        _RunnerExp3TraceMetricInput(
            condition_id=condition_id,
            fault_type=fault_type,
            repeat_id=repeat_id,
            sample_slot_id=sample_slot_id,
            observations=tuple(
                observation
                for row in trace_groups[
                    (condition_id, fault_type, repeat_id, sample_slot_id)
                ]
                for observation in _persisted_metric_observations(
                    row,
                    facts_by_root[row.preregistered_root_run_id],
                    observation_type=Exp3PersistedObservation,
                )
            ),
            direct_results=tuple(trace_groups[
                (condition_id, fault_type, repeat_id, sample_slot_id)
            ]),
        )
        for condition_id, fault_type, repeat_id, sample_slot_id in sorted(trace_groups)
    )
    result["exp3_online_recovery"] = tuple(
        _RunnerExp3OnlineMetricInput(
            recovery_source=row.condition_id,
            case_id=row.case_id,
            repeat_id=row.repeat_id,
            observations=_persisted_metric_observations(
                row,
                facts_by_root[row.preregistered_root_run_id],
                observation_type=Exp3PersistedObservation,
            ),
            direct_results=(row,),
        )
        for row in routed["exp3_online_recovery"]
    )
    exp4_groups: dict[tuple[str, str, int, str], list[Any]] = {}
    for row in routed["exp4_ablation"]:
        mode = row.condition_axes.get("ablation_mode")
        domain = row.condition_axes.get("domain")
        if not isinstance(mode, str) or not isinstance(domain, str):
            raise ValueError("Exp4 metric identity is not legally typed")
        exp4_groups.setdefault(
            (row.condition_id, domain, row.repeat_id, mode), []
        ).append(row)
    result["exp4_ablation"] = tuple(
        Exp4ModeInput(
            condition_id=condition_id,
            domain=domain,
            repeat_id=repeat_id,
            ablation_mode=mode,
            roots=tuple(
                _hydrate_exp4_root(
                    row,
                    facts_by_root[row.preregistered_root_run_id],
                )
                for row in exp4_groups[(condition_id, domain, repeat_id, mode)]
            ),
            observations=tuple(
                observation
                for row in exp4_groups[(condition_id, domain, repeat_id, mode)]
                for observation in _persisted_metric_observations(
                    row,
                    facts_by_root[row.preregistered_root_run_id],
                    observation_type=Exp4PersistedObservation,
                )
            ),
            identity_consistent=all(
                row.identity_consistent
                for row in exp4_groups[(condition_id, domain, repeat_id, mode)]
            ),
            paper_evidence_complete=all(
                row.paper_evidence_complete
                for row in exp4_groups[(condition_id, domain, repeat_id, mode)]
            ),
            infrastructure_valid=all(
                row.infrastructure_valid
                for row in exp4_groups[(condition_id, domain, repeat_id, mode)]
            ),
        )
        for condition_id, domain, repeat_id, mode in sorted(exp4_groups)
    )
    result["experiment_5"] = _hydrate_exp5_metric_rows(
        routed["experiment_5"],
        facts_by_root,
    )
    return result


def _producer_parts(
    facts: Mapping[str, Any],
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    task = facts.get("task", {})
    attempts = facts.get("attempts", ())
    run_evidence = facts.get("run_evidence", {})
    if not isinstance(task, Mapping) or not isinstance(run_evidence, Mapping):
        raise ValueError("persisted metric producer facts are malformed")
    if not isinstance(attempts, Sequence) or isinstance(
        attempts, (str, bytes, bytearray)
    ):
        raise ValueError("persisted metric attempts are malformed")
    normalized_attempts = tuple(attempts)
    if any(not isinstance(value, Mapping) for value in normalized_attempts):
        raise ValueError("persisted metric attempts must be mappings")
    return task, normalized_attempts, run_evidence


def _runtime_observation(run_evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = run_evidence.get("protocol_runtime", {})
    if not isinstance(runtime, Mapping):
        raise ValueError("persisted protocol runtime facts are malformed")
    observation = runtime.get("runtime_observation", {})
    if observation is None:
        return {}
    if not isinstance(observation, Mapping):
        raise ValueError("persisted runtime observation is malformed")
    return observation


def _root_clock_from_verified_ledger(
    *,
    events: Sequence[object],
    run_id: str,
    task_id: str,
    root_unit_id: str,
) -> dict[str, str]:
    """从已通过 hash-chain 校验的 native ledger 提取 root 生命周期时钟。"""

    root_events = []
    terminal_events = []
    terminal_states = {"completed", "failed", "cancelled", "canceled"}
    for event in events:
        if _optional_field(event, "task_id") != task_id:
            continue
        payload = _optional_field(event, "payload")
        unit = payload.get("task_unit") if isinstance(payload, Mapping) else None
        event_targets_root = _optional_field(event, "object_id") == root_unit_id
        if isinstance(unit, Mapping):
            event_targets_root = event_targets_root or unit.get("unit_id") == root_unit_id
        if not event_targets_root:
            continue
        occurred_at = _optional_field(event, "occurred_at")
        if not isinstance(occurred_at, str) or not occurred_at:
            raise ValueError("verified root ledger event has no occurred_at")
        root_events.append(event)
        if (
            isinstance(unit, Mapping)
            and str(unit.get("state") or "").lower() in terminal_states
        ):
            terminal_events.append(event)
    if not root_events or not terminal_events:
        raise ValueError("verified ledger root lifecycle clock is incomplete")
    started_at = str(_required_field(root_events[0], "occurred_at"))
    terminal_at = str(_required_field(terminal_events[-1], "occurred_at"))
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    terminal = datetime.fromisoformat(terminal_at.replace("Z", "+00:00"))
    if started.utcoffset() is None or terminal.utcoffset() is None:
        raise ValueError("verified ledger root clock must include timezone")
    if terminal < started:
        raise ValueError("verified ledger root terminal clock precedes start")
    return {
        "schema_version": "tokenshare.paper_ledger_root_clock.v1",
        "run_id": run_id,
        "task_id": task_id,
        "root_unit_id": root_unit_id,
        "root_started_at": started_at,
        "root_terminal_at": terminal_at,
    }


def _persisted_ledger_root_clock_ms(
    facts: Mapping[str, Any],
    row: Any,
) -> tuple[Decimal | None, Decimal | None]:
    clock = facts.get("ledger_root_clock")
    if clock is None:
        return None, None
    if not isinstance(clock, Mapping) or set(clock) != {
        "schema_version",
        "run_id",
        "task_id",
        "root_unit_id",
        "root_started_at",
        "root_terminal_at",
    }:
        raise ValueError("persisted ledger root clock is malformed")
    binding = getattr(row, "execution_binding", None)
    if (
        clock.get("schema_version") != "tokenshare.paper_ledger_root_clock.v1"
        or binding is None
        or clock.get("run_id") != binding.execution_id
        or clock.get("task_id") != binding.task_id
        or clock.get("root_unit_id") != binding.root_unit_id
    ):
        raise ValueError("persisted ledger root clock identity mismatch")
    try:
        started = datetime.fromisoformat(
            str(clock["root_started_at"]).replace("Z", "+00:00")
        )
        terminal = datetime.fromisoformat(
            str(clock["root_terminal_at"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("persisted ledger root clock timestamp is invalid") from exc
    if started.utcoffset() is None or terminal.utcoffset() is None:
        raise ValueError("persisted ledger root clock must include timezone")
    if terminal < started:
        raise ValueError("persisted ledger root terminal clock precedes start")
    return _datetime_epoch_ms(started), _datetime_epoch_ms(terminal)


def _datetime_epoch_ms(value: datetime) -> Decimal:
    normalized = value.astimezone(timezone.utc)
    delta = normalized - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return Decimal(delta.days * 86_400_000 + delta.seconds * 1000) + (
        Decimal(delta.microseconds) / Decimal(1000)
    )


def _complete_cny_costs(
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Decimal | int | None]:
    provider_attempts = tuple(
        attempt
        for attempt in attempts
        if isinstance(attempt.get("provider_attempt_count"), int)
        and not isinstance(attempt.get("provider_attempt_count"), bool)
        and int(attempt["provider_attempt_count"]) > 0
    )
    expected_count = task.get("provider_attempt_count")
    count_complete = (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count > 0
        and expected_count == len(provider_attempts)
    )
    values: dict[str, Decimal | int | None] = {
        str(attempt.get("attempt_id") or ""): None for attempt in attempts
    }
    if not count_complete:
        return values
    for attempt in provider_attempts:
        prompt = attempt.get("prompt_tokens")
        completion = attempt.get("completion_tokens")
        total = attempt.get("total_tokens")
        cost = attempt.get("cost_estimate")
        usage_ref = attempt.get("usage_ref")
        if (
            any(type(value) is not int or value < 0 for value in (prompt, completion, total))
            or total != prompt + completion
            or isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or cost < 0
            or attempt.get("cost_estimate_currency") != "CNY"
            or attempt.get("cost_estimate_status") != "estimated"
            or not isinstance(usage_ref, Mapping)
            or not usage_ref
        ):
            return values
    for attempt in provider_attempts:
        values[str(attempt.get("attempt_id") or "")] = Decimal(
            str(attempt["cost_estimate"])
        )
    return values


def _current_roles(row: Any) -> tuple[str, ...] | None:
    available = {ref.source_role for ref in row.current_provider_object_refs}
    roles = tuple(
        role for role in _CURRENT_PROVIDER_METRIC_ROLE_ORDER if role in available
    )
    roles += tuple(sorted(available.difference(roles)))
    return roles or None


def _source_roles(row: Any) -> tuple[str, ...] | None:
    available = {locator.object_role for locator in row.source_bank_object_locators}
    roles = tuple(role for role in _SOURCE_BANK_METRIC_ROLE_ORDER if role in available)
    roles += tuple(sorted(available.difference(roles)))
    return roles or None


def _persisted_trace_source_consumptions(
    task: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...] | None:
    summary = task.get("trace_source_usage")
    if summary is None:
        return None
    if not isinstance(summary, Mapping):
        raise ValueError("persisted trace source usage is malformed")
    if (
        summary.get("schema_version") != "tokenshare.paper_trace_source_usage.v1"
        or summary.get("attribution_kind") != "immutable_response_bank"
        or summary.get("current_provider_call_count") != 0
        or str(summary.get("current_provider_spend_cny")) != "0"
    ):
        raise ValueError("persisted trace source usage header is invalid")
    values = summary.get("consumptions")
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise ValueError("persisted trace source consumptions are malformed")
    consumptions = tuple(values)
    if (
        summary.get("committed_consumption_count") != len(consumptions)
        or any(not isinstance(value, Mapping) for value in consumptions)
    ):
        raise ValueError("persisted trace source consumption count is invalid")
    ids = tuple(value.get("consumption_id") for value in consumptions)
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("persisted trace source consumption identity is invalid")
    if len(set(ids)) != len(ids):
        raise ValueError("persisted trace source consumption identity is duplicated")
    return consumptions


def _complete_trace_source_sum(
    consumptions: tuple[Mapping[str, Any], ...] | None,
    field_name: str,
    *,
    decimal: bool = False,
) -> int | Decimal | None:
    if not consumptions:
        return None
    values = tuple(value.get(field_name) for value in consumptions)
    if any(value is None for value in values):
        return None
    if decimal:
        try:
            normalized = tuple(Decimal(str(value)) for value in values)
        except (ArithmeticError, ValueError) as exc:
            raise ValueError(f"persisted trace source {field_name} is invalid") from exc
        if any(value < 0 for value in normalized):
            raise ValueError(f"persisted trace source {field_name} is negative")
        return sum(normalized, Decimal(0))
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError(f"persisted trace source {field_name} is invalid")
    return sum(values)


def _hydrate_exp1_metric_row(
    row: Any,
    facts: Mapping[str, Any],
) -> Exp1HydratedDirectRow:
    task, attempts, run_evidence = _producer_parts(facts)
    _runtime_observation(run_evidence)
    root_started_at_ms, root_terminal_at_ms = _persisted_ledger_root_clock_ms(
        facts, row
    )
    cny_costs = _complete_cny_costs(task, attempts)
    return Exp1HydratedDirectRow(
        direct_result=row,
        root_start_at_ms=root_started_at_ms,
        root_terminal_at_ms=root_terminal_at_ms,
        actual_provider_attempts=(
            tuple(
                Exp1ActualProviderAttemptFacts(
                    attempt_id=str(attempt.get("attempt_id") or ""),
                    provider_latency_ms=attempt.get("latency_ms"),
                    total_tokens=attempt.get("total_tokens"),
                    cost_estimate_cny=cny_costs.get(
                        str(attempt.get("attempt_id") or "")
                    ),
                    current_provider_roles=_current_roles(row),
                )
                for attempt in attempts
            )
            if facts
            else None
        ),
    )


def _hydrate_exp2_trace_metric_row(
    row: Any,
    facts: Mapping[str, Any],
) -> _RunnerExp2TraceMetricInput:
    task, _attempts, run_evidence = _producer_parts(facts)
    observation = _runtime_observation(run_evidence)
    planned = tuple(str(value) for value in observation.get("planned_ai_unit_ids", ()))
    dispatched = set(str(value) for value in observation.get("dispatched_ai_unit_ids", ()))
    completed = set(str(value) for value in observation.get("completed_ai_unit_ids", ()))
    consumptions = _persisted_trace_source_consumptions(task)
    latency_by_unit: dict[str, int] = {}
    latency_complete = consumptions is not None
    for consumption in consumptions or ():
        latency = consumption.get("latency_ms")
        if type(latency) is not int or latency < 0:
            latency_complete = False
            continue
        identities = {
            str(consumption.get("unit_id") or ""),
            str(consumption.get("planned_ai_unit_id") or ""),
        }
        identities.discard("")
        if not identities:
            raise ValueError("persisted trace source consumption lacks unit identity")
        for unit_id in identities:
            latency_by_unit[unit_id] = latency_by_unit.get(unit_id, 0) + latency
    if any(unit_id not in latency_by_unit for unit_id in completed):
        latency_complete = False
    return _RunnerExp2TraceMetricInput(
        direct_result=row,
        persisted_logical_makespan_ms=observation.get("runtime_wall_clock_ms"),
        trace_consumptions=(
            tuple(
                Exp2TraceConsumptionFacts(
                    consumption_id=str(consumption["consumption_id"]),
                    committed=True,
                    source_total_tokens=consumption.get("total_tokens"),
                    source_cost_estimate_cny=(
                        Decimal(str(consumption["cost_estimate_cny"]))
                        if consumption.get("cost_estimate_cny") is not None
                        else None
                    ),
                    source_bank_roles=(
                        tuple(consumption["source_bank_roles"])
                        if consumption.get("source_bank_roles") is not None
                        else None
                    ),
                )
                for consumption in consumptions
            )
            if consumptions is not None
            else None
        ),
        ai_units=(
            tuple(
                Exp2AIUnitFacts(
                    unit_id=unit_id,
                    planned=True,
                    scheduled=unit_id in dispatched,
                    executed=unit_id in completed,
                    busy_worker_time_ms=(
                        latency_by_unit[unit_id]
                        if unit_id in completed
                        else None
                    ),
                )
                for unit_id in planned
            )
            if facts and latency_complete
            else None
        ),
        in_flight_at_witness=(
            len(tuple(observation.get("in_flight_ai_unit_ids_at_witness", ())))
            if observation
            else None
        ),
        observed_peak_concurrency=observation.get("observed_peak_concurrency"),
    )


def _hydrate_exp2_online_metric_row(
    row: Any,
    facts: Mapping[str, Any],
) -> _RunnerExp2OnlineMetricInput:
    task, attempts, run_evidence = _producer_parts(facts)
    _runtime_observation(run_evidence)
    root_started_at_ms, root_terminal_at_ms = _persisted_ledger_root_clock_ms(
        facts, row
    )
    cny_costs = _complete_cny_costs(task, attempts)
    first_by_unit: dict[str, Mapping[str, Any]] = {}
    for attempt in attempts:
        unit_id = str(
            attempt.get("unit_id") or attempt.get("planned_ai_unit_id") or ""
        )
        if not unit_id:
            raise ValueError("persisted online attempt has no unit identity")
        first_by_unit.setdefault(unit_id, attempt)
    return _RunnerExp2OnlineMetricInput(
        direct_result=row,
        protocol_first_dispatch_at_ms=root_started_at_ms,
        root_terminal_at_ms=root_terminal_at_ms,
        first_provider_attempts=(
            tuple(
                Exp2OnlineFirstAttemptFacts(
                    attempt_identity=str(attempt.get("attempt_id") or ""),
                    actual_call=bool(attempt.get("provider")),
                    provider_429=str(attempt.get("error_kind") or "") == "provider_429",
                    provider_timeout=str(attempt.get("error_kind") or "")
                    in {"provider_timeout", "timeout"},
                    total_tokens=attempt.get("total_tokens"),
                    cost_estimate_cny=cny_costs.get(
                        str(attempt.get("attempt_id") or "")
                    ),
                    provider_latency_ms=attempt.get("latency_ms"),
                    current_provider_roles=_current_roles(row),
                )
                for attempt in first_by_unit.values()
            )
            if facts
            else None
        ),
    )


def _persisted_metric_observations(
    row: Any,
    facts: Mapping[str, Any],
    *,
    observation_type: type,
) -> tuple[Any, ...]:
    records: list[tuple[str, Mapping[str, Any]]] = []
    for category in ("attempts", "faults", "events"):
        values = facts.get(category, ())
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes, bytearray)
        ):
            raise ValueError(f"persisted metric {category} are malformed")
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise ValueError(f"persisted metric {category} must be mappings")
            records.append((f"{category}:{index}", value))
    task = facts.get("task")
    if isinstance(task, Mapping):
        records.append(("task", task))
    if not records:
        records.append(
            (
                "inventory",
                {
                    "member_kind": "preregistered_root",
                    "root_status": row.root_status,
                    "case_id": row.case_id,
                },
            )
        )
    return tuple(
        observation_type(
            observation_id=(
                f"{row.preregistered_root_run_id}:{suffix}"
            ),
            facts=dict(value),
        )
        for suffix, value in records
    )


def _hydrate_exp4_root(row: Any, facts: Mapping[str, Any]) -> Exp4DirectRootFacts:
    task, _attempts, run_evidence = _producer_parts(facts)
    observation = _runtime_observation(run_evidence)
    consumptions = _persisted_trace_source_consumptions(task)
    replacements = tuple(
        dict.fromkeys(
            str(consumption["entry_id"])
            for consumption in consumptions or ()
            if type(consumption.get("replacement_slot")) is int
            and consumption["replacement_slot"] > 0
        )
    )
    return Exp4DirectRootFacts(
        preregistered_root_run_id=row.preregistered_root_run_id,
        case_id=row.case_id,
        case_record_digest=str(row.preregistered_case_ref["case_record_digest"]),
        sample_slot_index=row.repeat_id,
        replacement_slot_ids=replacements,
        final_result_reference_complete=row.final_result_reference_complete,
        end_to_end_verified_success=row.end_to_end_verified_success,
        trace_replay_wall_clock_ms=observation.get("runtime_wall_clock_ms"),
        trace_attributed_tokens=_complete_trace_source_sum(
            consumptions, "total_tokens"
        ),
        trace_attributed_cost=_complete_trace_source_sum(
            consumptions, "cost_estimate_cny", decimal=True
        ),
        identity_consistent=row.identity_consistent,
        paper_evidence_complete=row.paper_evidence_complete,
        infrastructure_valid=row.infrastructure_valid,
        source_bank_roles=_source_roles(row),
    )


def _hydrate_exp5_metric_rows(
    rows: Sequence[Any],
    facts_by_root: Mapping[str, Mapping[str, Any]],
) -> tuple[Exp5ModelRepeatFacts, ...]:
    groups: dict[tuple[str, int], list[Any]] = {}
    for row in rows:
        model_arm = row.condition_axes.get("model_endpoint_id")
        if not isinstance(model_arm, str) or not model_arm:
            raise ValueError("Exp5 model endpoint identity is missing")
        groups.setdefault((model_arm, row.repeat_id), []).append(row)
    hydrated: list[Exp5ModelRepeatFacts] = []
    for (model_arm, repeat_id), grouped_rows in sorted(groups.items()):
        roots: list[Exp5PreregisteredRootFacts] = []
        units: dict[str, Exp5PlannedAIUnitFacts] = {}
        first_attempts: dict[str, Exp5FirstProviderAttemptFacts] = {}
        first_dispatch: int | float | None = None
        for row in grouped_rows:
            facts = facts_by_root[row.preregistered_root_run_id]
            task, attempts, run_evidence = _producer_parts(facts)
            observation = _runtime_observation(run_evidence)
            root_started_at_ms, root_terminal_at_ms = _persisted_ledger_root_clock_ms(
                facts, row
            )
            cny_costs = _complete_cny_costs(task, attempts)
            if root_started_at_ms is not None and (
                first_dispatch is None or root_started_at_ms < first_dispatch
            ):
                first_dispatch = root_started_at_ms
            roots.append(
                Exp5PreregisteredRootFacts(
                    preregistered_root_run_id=row.preregistered_root_run_id,
                    root_dispatched_at_ms=root_started_at_ms,
                    root_terminal_at_ms=root_terminal_at_ms,
                    final_result_reference_complete=(
                        row.final_result_reference_complete
                    ),
                    end_to_end_verified_success=row.end_to_end_verified_success,
                )
            )
            planned = tuple(
                str(value) for value in observation.get("planned_ai_unit_ids", ())
            )
            for unit_id in planned:
                units.setdefault(
                    unit_id,
                    Exp5PlannedAIUnitFacts(unit_id=unit_id, planned_call=True),
                )
            for attempt in attempts:
                unit_id = str(
                    attempt.get("unit_id")
                    or attempt.get("planned_ai_unit_id")
                    or ""
                )
                if not unit_id:
                    raise ValueError("Exp5 attempt has no planned AI-unit identity")
                units.setdefault(
                    unit_id,
                    Exp5PlannedAIUnitFacts(unit_id=unit_id, planned_call=True),
                )
                if unit_id in first_attempts:
                    continue
                error = str(attempt.get("error_kind") or "")
                accepted = (
                    str(attempt.get("attempt_status") or "").lower()
                    in {"succeeded", "completed"}
                    and not error
                )
                first_attempts[unit_id] = Exp5FirstProviderAttemptFacts(
                    attempt_id=str(attempt.get("attempt_id") or ""),
                    planned_ai_unit_id=unit_id,
                    actual_call=bool(attempt.get("provider")),
                    provider_transport_failure=error.startswith("provider_")
                    or error in {"timeout", "executor_error"},
                    parse_schema_unusable=error in {"parse_error", "schema_error"},
                    verification_checker_rejected=(
                        not accepted
                        and error
                        not in {
                            "parse_error",
                            "schema_error",
                            "timeout",
                            "executor_error",
                        }
                        and not error.startswith("provider_")
                    ),
                    verifier_accepted_candidate=accepted,
                    actual_total_tokens=attempt.get("total_tokens"),
                    actual_cost_estimate_cny=cny_costs.get(
                        str(attempt.get("attempt_id") or "")
                    ),
                    current_provider_roles=_current_roles(row),
                )
        hydrated.append(
            Exp5ModelRepeatFacts(
                model_arm_id=model_arm,
                repeat_id=repeat_id,
                frozen_identity=None,
                observed_identity=None,
                persisted_model_endpoint_identity_digest=None,
                protocol_first_dispatch_at_ms=first_dispatch,
                preregistered_roots=tuple(roots),
                planned_ai_units=tuple(units.values()),
                first_provider_attempts=tuple(first_attempts.values()),
                max_retries=0,
                replacement_attempts_allowed=False,
                infrastructure_valid=all(
                    row.infrastructure_valid for row in grouped_rows
                ),
            )
        )
    return tuple(hydrated)


def _finalize_canonical_direct_closure(
    *,
    suite_root: Path,
    collector: _CanonicalDirectCollector,
) -> None:
    with collector.lock:
        evidence = tuple(collector.evidence)
        provider_files = dict(collector.current_provider_object_files)
        producer_facts = dict(collector.producer_facts_by_root)
    projection = project_paper_direct_results(
        root_inventory_manifest=collector.inventory,
        condition_manifests=collector.condition_manifests,
        catalog_manifests=collector.catalog_manifests,
        canonical_runtime_evidence=evidence,
    )
    direct_rows = _canonical_metric_inputs(
        projection.rows,
        producer_facts_by_root=producer_facts,
    )
    if not any(direct_rows.values()):
        raise ValueError("canonical direct projection produced no metric rows")
    with tempfile.TemporaryDirectory(
        prefix="tokenshare-direct-evidence-",
        dir=suite_root.parent,
    ) as staging_directory:
        staged_evidence_root = Path(staging_directory) / "formal_evidence"
        shutil.copytree(suite_root, staged_evidence_root)
        for derived_name in (
            "condition_results.jsonl",
            "formal_runner_result.json",
        ):
            derived_path = staged_evidence_root / derived_name
            if derived_path.is_file():
                derived_path.unlink()
        _prepare_protected_formal_evidence_closure(
            staged_evidence_root,
            suite_root=suite_root,
            canonical_direct_rows=projection.rows,
            trace_source_bindings_by_root=(
                collector.trace_source_bindings_by_root
            ),
        )
        protected = persist_paper_traceability_replay_input_root(
            replay_input_root=suite_root.with_name(
                suite_root.name + ".traceability_replay_inputs"
            ),
            canonical_direct_rows=direct_rows,
            canonical_runtime_evidence=evidence,
            current_trace_wrappers_by_root=(
                collector.current_trace_wrappers_by_root
            ),
            trace_source_bindings_by_root=(
                collector.trace_source_bindings_by_root
            ),
            eligibility_facts_by_root=collector.eligibility_facts_by_root,
            source_resolvers=collector.source_resolvers,
            current_provider_object_files=provider_files,
            current_evidence_root=staged_evidence_root,
        )
    handle_relative = "traceability_replay_input_root.handle.pickle"
    handle_path = suite_root / handle_relative
    handle_content = pickle.dumps(protected, protocol=5)
    _atomic_write_bytes(handle_path, handle_content)
    suite_path = suite_root / "suite_manifest.json"
    suite_manifest = _required_json_object(suite_path, label="suite manifest")
    suite_manifest["traceability_replay_input_root_ref"] = {
        "schema_version": "tokenshare.paper_traceability_replay_input_ref.v1",
        "handle_path": handle_relative,
        "handle_digest": _sha256_bytes(handle_content),
        "descriptor_path": protected.descriptor_path.as_posix(),
        "descriptor_digest": protected.descriptor_digest,
    }
    _atomic_write_json(suite_path, suite_manifest)
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()


def _prepare_protected_formal_evidence_closure(
    evidence_root: Path,
    *,
    suite_root: Path,
    canonical_direct_rows: Sequence[Any],
    trace_source_bindings_by_root: Mapping[str, Sequence[Any]] | None = None,
) -> None:
    """在隔离副本中把权威 case evidence 投影为 L4 canonical closure。"""

    store = FormalEvidenceStore(evidence_root)
    store._refresh_evidence_manifest()
    store._validate_evidence_manifest()
    manifest = _required_json_object(
        evidence_root / "evidence_manifest.json",
        label="formal evidence manifest",
    )
    condition_entries = manifest.get("conditions")
    if not isinstance(condition_entries, list) or not condition_entries:
        raise ValueError("protected formal evidence requires condition closure")
    for entry in condition_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("protected formal evidence condition entry is invalid")
        condition_ref = entry.get("condition_manifest_ref")
        if not isinstance(condition_ref, Mapping):
            raise ValueError("protected formal evidence condition ref is missing")
        condition_path = evidence_root / str(condition_ref.get("path", ""))
        condition = _required_json_object(
            condition_path,
            label="formal condition manifest",
        )
        generation_ref = condition.get("generation_manifest_ref")
        current_ref = condition.get("current_ref")
        if not isinstance(generation_ref, Mapping) or not isinstance(
            current_ref,
            Mapping,
        ):
            raise ValueError("protected formal evidence condition closure is incomplete")
        generation_path = evidence_root / str(generation_ref.get("path", ""))
        current_path = evidence_root / str(current_ref.get("path", ""))
        generation = _required_json_object(
            generation_path,
            label="formal generation manifest",
        )
        if (
            generation.get("generation_kind") != "snapshot"
            or generation.get("parent_generation_id") is not None
        ):
            raise ValueError("protected formal evidence requires terminal snapshot")
        run_root = condition_path.parent
        generation_root = generation_path.parent
        experiment_id = str(condition.get("experiment_id", ""))
        condition_id = str(condition.get("condition_id", ""))
        repeat_id = condition.get("repeat_id")
        projected_by_case = {
            row.case_id: row
            for row in canonical_direct_rows
            if row.experiment_id == experiment_id
            and row.condition_id == condition_id
            and row.repeat_id == repeat_id
        }
        task_path = generation_root / "per_task_results.jsonl"
        task_rows = _read_jsonl_records(task_path)
        task_id_projection: dict[str, str] = {}
        for row in task_rows:
            case_id = str(row.get("task_id", ""))
            direct = projected_by_case.get(case_id)
            if direct is None:
                raise ValueError(
                    "protected formal evidence canonical task binding is missing"
                )
            binding = direct.execution_binding
            row["case_id"] = direct.case_id
            row["preregistered_root_run_id"] = direct.preregistered_root_run_id
            if binding is None:
                task_id_projection[case_id] = case_id
                continue
            runtime_identity = row.get("runtime_generation_identity")
            if not isinstance(runtime_identity, Mapping) or any(
                runtime_identity.get(name) != expected
                for name, expected in (
                    ("run_id", binding.execution_id),
                    ("task_id", binding.task_id),
                    ("root_unit_id", binding.root_unit_id),
                )
            ):
                raise ValueError(
                    "protected formal evidence runtime task binding mismatch"
                )
            canonical_runtime_identity = {
                "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
                "run_id": binding.execution_id,
                "task_id": binding.task_id,
                "root_unit_id": binding.root_unit_id,
                "ledger_digest": binding.ledger_digest,
            }
            execution_version_identity = row.get("execution_version_identity")
            if execution_version_identity is not None and not isinstance(
                execution_version_identity,
                Mapping,
            ):
                raise ValueError(
                    "protected formal evidence execution version identity is invalid"
                )
            row["runtime_generation_identity"] = canonical_runtime_identity
            if execution_version_identity is not None:
                row["execution_version_identity"] = {
                    **execution_version_identity,
                    "runtime_generation_schema_version": canonical_runtime_identity[
                        "schema_version"
                    ],
                    "runtime_generation_identity_digest": digest_json(
                        canonical_runtime_identity
                    ),
                }
            task_id_projection[case_id] = binding.task_id
            row["task_id"] = binding.task_id
            ledger_mapping = row.get("protocol_event_ledger")
            if isinstance(ledger_mapping, Mapping):
                row["protocol_event_ledger"] = {
                    **ledger_mapping,
                    "case_task_id": binding.task_id,
                }
        if len(task_id_projection) != len(task_rows):
            raise ValueError("protected formal evidence task projection is ambiguous")
        _atomic_write_jsonl(task_path, task_rows)

        projected_jsonl: dict[str, list[dict[str, Any]]] = {
            "per_task_results.jsonl": task_rows,
        }
        for relative_name in (
            "per_attempt_results.jsonl",
            "fault_injections.jsonl",
            "events/event_log.jsonl",
        ):
            path = generation_root / relative_name
            rows = _read_jsonl_records(path)
            for row in rows:
                if (
                    relative_name == "events/event_log.jsonl"
                    and _is_protocol_ledger_event(row)
                ):
                    continue
                original_task_id = str(row.get("task_id", ""))
                projected_task_id = task_id_projection.get(original_task_id)
                if projected_task_id is None:
                    raise ValueError(
                        "protected formal evidence contextual task binding is missing"
                    )
                row["task_id"] = projected_task_id
                if relative_name == "per_attempt_results.jsonl":
                    direct = projected_by_case.get(original_task_id)
                    bindings = (
                        ()
                        if direct is None or trace_source_bindings_by_root is None
                        else trace_source_bindings_by_root.get(
                            direct.preregistered_root_run_id,
                            (),
                        )
                    )
                    planned_ai_unit_id = row.get("planned_ai_unit_id")
                    matching_bindings = tuple(
                        value
                        for value in bindings
                        if value.planned_ai_unit_id == planned_ai_unit_id
                    )
                    if len(matching_bindings) > 1:
                        raise ValueError(
                            "protected formal evidence trace binding is ambiguous"
                        )
                    if matching_bindings:
                        row["trace_source_binding_digest"] = (
                            matching_bindings[0].binding_digest
                        )
            _atomic_write_jsonl(path, rows)
            projected_jsonl[relative_name] = rows

        run_manifest_path = generation_root / "run_manifest.json"
        run_manifest = _required_json_object(
            run_manifest_path,
            label="formal run manifest",
        )
        run_manifest["task_ids"] = [str(row["task_id"]) for row in task_rows]
        completed_case_ids = {
            str(value) for value in run_manifest.get("completed_task_ids", ())
        }
        run_manifest["completed_task_ids"] = [
            task_id_projection[case_id]
            for case_id in task_id_projection
            if case_id in completed_case_ids
        ]
        _atomic_write_json(run_manifest_path, run_manifest)

        artifact_index_path = (
            generation_root / "artifacts" / "artifact_index.jsonl"
        )
        artifact_rows = _read_jsonl_records(artifact_index_path)
        flat_artifact_root = run_root / "artifacts"
        flat_artifact_root.mkdir(parents=True, exist_ok=True)
        for row in artifact_rows:
            original_task_id = str(row.get("task_id", ""))
            projected_task_id = task_id_projection.get(original_task_id)
            if projected_task_id is None:
                raise ValueError(
                    "protected formal evidence artifact task binding is missing"
                )
            row["task_id"] = projected_task_id
            relative = Path(str(row.get("path", "")))
            source = (evidence_root / relative).resolve(strict=False)
            authoritative_root = flat_artifact_root.resolve(strict=False)
            if (
                not source.is_file()
                or authoritative_root not in source.parents
                or source.parent == authoritative_root
            ):
                raise ValueError(
                    "protected formal evidence requires task-scoped artifact source"
                )
            content = source.read_bytes()
            if (
                row.get("content_hash") != _sha256_bytes(content)
                or (
                    row.get("size_bytes") is not None
                    and row.get("size_bytes") != len(content)
                )
            ):
                raise ValueError("protected formal evidence artifact identity mismatch")
            target = flat_artifact_root / (
                hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
                + ".bin"
            )
            if target.is_file() and target.read_bytes() != content:
                raise ValueError("protected formal evidence flat artifact collision")
            if not target.is_file():
                shutil.copyfile(source, target)
            row["path"] = target.relative_to(evidence_root).as_posix()
        for direct in projected_by_case.values():
            binding = direct.execution_binding
            if binding is None:
                continue
            canonical_store = ArtifactStore(
                suite_root.with_name(
                    suite_root.name + ".canonical_direct_evidence"
                )
                / hashlib.sha256(
                    direct.preregistered_root_run_id.encode("utf-8")
                ).hexdigest()
            )
            for snapshot in direct.artifact_refs:
                canonical_ref = canonical_store.load_artifact_ref(
                    snapshot.artifact_id
                )
                if any(
                    actual != expected
                    for actual, expected in (
                        (canonical_ref.artifact_type, snapshot.artifact_type),
                        (canonical_ref.uri, snapshot.uri),
                        (canonical_ref.content_hash, snapshot.content_hash),
                        (canonical_ref.size_bytes, snapshot.size_bytes),
                        (canonical_ref.media_type, snapshot.media_type),
                        (
                            canonical_ref.artifact_schema_id,
                            snapshot.artifact_schema_id,
                        ),
                        (
                            canonical_ref.artifact_schema_version,
                            snapshot.artifact_schema_version,
                        ),
                        (canonical_ref.created_at, snapshot.created_at),
                    )
                ):
                    raise ValueError(
                        "protected formal evidence direct artifact identity mismatch"
                    )
                canonical_content = canonical_store.read_bytes(canonical_ref)
                target = flat_artifact_root / (
                    hashlib.sha256(
                        (
                            direct.preregistered_root_run_id
                            + "\0"
                            + snapshot.artifact_id
                            + "\0"
                            + snapshot.uri
                        ).encode("utf-8")
                    ).hexdigest()
                    + ".bin"
                )
                _atomic_write_bytes(target, canonical_content)
                matches = [
                    row
                    for row in artifact_rows
                    if isinstance(row.get("source_artifact_ref"), Mapping)
                    and row["source_artifact_ref"].get("artifact_id")
                    == snapshot.artifact_id
                    and row["source_artifact_ref"].get("content_hash")
                    == snapshot.content_hash
                ]
                if len(matches) > 1:
                    raise ValueError(
                        "protected formal evidence direct artifact is ambiguous"
                    )
                record = matches[0] if matches else {}
                record.update(
                    {
                        "artifact_id": snapshot.artifact_id,
                        "experiment_id": direct.experiment_id,
                        "condition_id": direct.condition_id,
                        "repeat_id": direct.repeat_id,
                        "task_id": binding.task_id,
                        "path": target.relative_to(evidence_root).as_posix(),
                        "content_hash": snapshot.content_hash,
                        "size_bytes": snapshot.size_bytes,
                        "source_artifact_ref": canonical_ref.to_dict(),
                    }
                )
                if not matches:
                    artifact_rows.append(record)
        _atomic_write_jsonl(artifact_index_path, artifact_rows)
        projected_jsonl["artifacts/artifact_index.jsonl"] = artifact_rows

        files = generation.get("files")
        if not isinstance(files, list):
            raise ValueError("protected formal evidence generation files are invalid")
        file_refs = {
            str(value.get("path")): value
            for value in files
            if isinstance(value, Mapping)
        }
        expected_paths = {"run_manifest.json", *projected_jsonl}
        if not expected_paths <= set(file_refs):
            raise ValueError("protected formal evidence generation refs are incomplete")
        file_refs["run_manifest.json"].update(
            _formal_file_evidence(generation_root, run_manifest_path, [run_manifest])
        )
        for relative_name, rows in projected_jsonl.items():
            file_refs[relative_name].update(
                _formal_file_evidence(
                    generation_root,
                    generation_root / relative_name,
                    rows,
                )
            )
        _atomic_write_json(generation_path, generation)

        current = _required_json_object(current_path, label="formal CURRENT")
        current["generation_manifest_digest"] = digest_json(generation)
        _atomic_write_json(current_path, current)
        condition["current_ref"] = _formal_json_file_evidence(
            evidence_root,
            current_path,
        )
        condition["generation_manifest_ref"] = _formal_json_file_evidence(
            evidence_root,
            generation_path,
        )
        condition["run_manifest_ref"] = _formal_json_file_evidence(
            evidence_root,
            run_manifest_path,
        )
        condition["logical_records_digest"] = digest_json(
            [
                {
                    "path": value["path"],
                    "record_count": value["record_count"],
                    "records_digest": value["records_digest"],
                }
                for value in files
                if isinstance(value, Mapping)
            ]
        )
        payload_paths = {
            (evidence_root / str(row["path"])).resolve(strict=False)
            for row in artifact_rows
        }
        condition["reachable_size_bytes"] = (
            generation_path.stat().st_size
            + sum(int(value["size"]) for value in files if isinstance(value, Mapping))
            + sum(path.stat().st_size for path in payload_paths)
        )
        _atomic_write_json(condition_path, condition)
        store._refresh_evidence_manifest(run_root=run_root)
    store = FormalEvidenceStore(evidence_root)
    store._validate_evidence_manifest()


def _formal_json_file_evidence(root: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    body = json.loads(content.decode("utf-8"))
    return {
        "path": path.relative_to(root).as_posix(),
        "size": len(content),
        "content_sha256": _sha256_bytes(content),
        "record_count": 1,
        "records_digest": digest_json([body]),
    }


def _formal_file_evidence(
    root: Path,
    path: Path,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": len(content),
        "content_sha256": _sha256_bytes(content),
        "record_count": len(rows),
        "records_digest": digest_json(rows),
    }


def _finalize_formal_manifests(
    *,
    suite_root: Path,
    suite_result: PaperSuiteResult,
    plans: Sequence[PaperExperimentDispatchPlan],
    condition_results: Sequence[PaperConditionResult],
    execution_classification: Mapping[str, Any] | None,
) -> None:
    results_by_plan = _partition_condition_results(
        plans=plans,
        condition_results=condition_results,
    )
    for plan in plans:
        _finalize_formal_experiment(
            suite_root=suite_root,
            plan=plan,
            condition_results=results_by_plan[plan.experiment_id],
            execution_classification=execution_classification,
            suite_status=_status_value(suite_result.status),
        )
    suite_path = suite_root / "suite_manifest.json"
    suite_manifest = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_manifest["status"] = _status_value(suite_result.status)
    suite_manifest["paper_eligible"] = suite_result.paper_eligible
    if suite_result.error_summary:
        suite_manifest["terminal_failure"] = _as_json(
            suite_result.error_summary[0]
        )
        suite_manifest["last_complete_event_ref"] = _last_complete_event_ref(
            suite_root
        )
    result_path = suite_root / "formal_runner_result.json"
    result_target = _formal_finalization_target(
        suite_root=suite_root,
        path=result_path,
        content=_serialized_json_text(suite_result.to_dict()),
    )
    suite_target = _formal_finalization_target(
        suite_root=suite_root,
        path=suite_path,
        content=_serialized_json_text(suite_manifest),
    )
    pending_path = suite_root / _FORMAL_FINALIZATION_PENDING
    _atomic_write_json(
        pending_path,
        {
            "schema_version": _FORMAL_SUITE_FINALIZATION_SCHEMA,
            "publication_kind": "suite_terminal_commit",
            "formal_runner_result_target": result_target,
            "suite_manifest_target": suite_target,
        },
    )
    _apply_formal_finalization_target(
        suite_root=suite_root,
        target=result_target,
        expected_path="formal_runner_result.json",
    )
    _formal_finalization_hook(
        stage="formal_runner_result_written",
        suite_root=suite_root,
    )
    _apply_formal_finalization_target(
        suite_root=suite_root,
        target=suite_target,
        expected_path="suite_manifest.json",
    )
    _formal_finalization_hook(stage="suite_manifest_written", suite_root=suite_root)
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    _formal_finalization_hook(
        stage="suite_inventory_refreshed",
        suite_root=suite_root,
    )
    pending_path.unlink()


def _partition_condition_results(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    condition_results: Sequence[PaperConditionResult],
) -> dict[str, list[PaperConditionResult]]:
    """按冻结 plan 顺序消费结果；不假设 condition_id 在实验间全局唯一。"""

    remaining = list(condition_results)
    result: dict[str, list[PaperConditionResult]] = {}
    for plan in plans:
        plan_results: list[PaperConditionResult] = []
        for condition in plan.conditions:
            if not remaining:
                continue
            if remaining[0].condition_id != condition.condition_id:
                if plan.status == "blocked":
                    continue
                raise ValueError("condition results violate frozen dispatch order")
            plan_results.append(remaining.pop(0))
        result[plan.experiment_id] = plan_results
    if remaining:
        raise ValueError("condition results do not belong to frozen plans")
    return result


def _finalize_formal_experiment(
    *,
    suite_root: Path,
    plan: PaperExperimentDispatchPlan,
    condition_results: Sequence[PaperConditionResult],
    execution_classification: Mapping[str, Any] | None,
    suite_status: str | None = None,
) -> None:
    _repair_interrupted_formal_finalization(suite_root)
    bindings = _condition_result_bindings(
        plan=plan,
        condition_results=condition_results,
    )
    rows_path = suite_root / "condition_results.jsonl"
    indexed_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in _read_jsonl_records(rows_path):
        key = (
            str(row.get("experiment_id")),
            str(row.get("condition_id")),
            str(row.get("repeat_id")),
        )
        if key in indexed_rows:
            raise ValueError("duplicate persisted condition result")
        indexed_rows[key] = row
    for condition, result in bindings:
        row = _formal_condition_result_row(
            plan=plan,
            condition=condition,
            result=result,
            execution_classification=execution_classification,
        )
        key = (
            plan.experiment_id,
            condition.condition_id,
            str(condition.repeat_id),
        )
        indexed_rows[key] = row
    ordered_rows = [indexed_rows[key] for key in sorted(indexed_rows)]

    infrastructure_blocked = any(
        isinstance(result.metrics_ref, Mapping)
        and result.metrics_ref.get("infrastructure_blocked") is True
        for _condition, result in bindings
    )
    expected_keys = {
        (condition.condition_id, str(condition.repeat_id))
        for condition in plan.conditions
    }
    actual_keys = {
        (condition.condition_id, str(condition.repeat_id))
        for condition, _result in bindings
    }
    if suite_status == "incomplete" and infrastructure_blocked:
        experiment_status: PaperStatus | str = PaperStatus.INCOMPLETE
    elif plan.status == "blocked" or infrastructure_blocked:
        experiment_status = PaperStatus.BLOCKED
    elif expected_keys and actual_keys == expected_keys:
        experiment_status = _suite_status(
            plans=(plan,),
            results=[result for _condition, result in bindings],
        )
    elif bindings:
        experiment_status = PaperStatus.RUNNING
    else:
        experiment_status = (
            PaperStatus.BLOCKED
            if plan.status == "blocked"
            else PaperStatus.PLANNED
        )
    manifest_path = (
        suite_root
        / "experiments"
        / plan.experiment_id
        / "experiment_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = _status_value(experiment_status)
    manifest["paper_eligible"] = (
        execution_classification is None
        and plan.paper_eligible_possible
        and plan.status != "blocked"
        and bool(expected_keys)
        and actual_keys == expected_keys
        and all(
            isinstance(result.metrics_ref, Mapping)
            and result.metrics_ref.get("paper_eligible") is True
            and bool(result.metrics_ref.get("evidence_refs"))
            for _condition, result in bindings
        )
    )
    rows_target = _formal_finalization_target(
        suite_root=suite_root,
        path=rows_path,
        content=_serialized_jsonl_text(ordered_rows),
    )
    manifest_target = _formal_finalization_target(
        suite_root=suite_root,
        path=manifest_path,
        content=_serialized_json_text(manifest),
    )
    pending_path = suite_root / _FORMAL_FINALIZATION_PENDING
    _atomic_write_json(
        pending_path,
        {
            "schema_version": _FORMAL_FINALIZATION_SCHEMA,
            "publication_kind": "experiment_terminal_commit",
            "experiment_id": plan.experiment_id,
            "condition_results_target": rows_target,
            "experiment_manifest_target": manifest_target,
        },
    )
    # rows 先 durable；experiment manifest 是 terminal commit marker。PENDING 使两步可恢复。
    _apply_formal_finalization_target(
        suite_root=suite_root,
        target=rows_target,
        expected_path="condition_results.jsonl",
    )
    _formal_finalization_hook(
        stage="condition_results_written",
        suite_root=suite_root,
    )
    _apply_formal_finalization_target(
        suite_root=suite_root,
        target=manifest_target,
        expected_path=(
            f"experiments/{plan.experiment_id}/experiment_manifest.json"
        ),
    )
    _formal_finalization_hook(
        stage="experiment_manifest_written",
        suite_root=suite_root,
    )
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    _formal_finalization_hook(stage="inventory_refreshed", suite_root=suite_root)
    pending_path.unlink()


def _condition_result_bindings(
    *,
    plan: PaperExperimentDispatchPlan,
    condition_results: Sequence[PaperConditionResult],
) -> list[tuple[Any, PaperConditionResult]]:
    if len(condition_results) > len(plan.conditions):
        raise ValueError("experiment condition results do not match frozen plan")
    bindings = list(zip(plan.conditions, condition_results, strict=False))
    if any(
        result.condition_id != condition.condition_id
        for condition, result in bindings
    ):
        raise ValueError("experiment condition results violate frozen order")
    return bindings


def _formal_condition_result_row(
    *,
    plan: PaperExperimentDispatchPlan,
    condition: Any,
    result: PaperConditionResult,
    execution_classification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    row = {
        **result.to_dict(),
        "experiment_id": plan.experiment_id,
        "repeat_id": condition.repeat_id,
        "formal": execution_classification is None,
        "pilot_only": execution_classification is not None,
        "regression_only": execution_classification is not None,
        "execution_scope": (
            "formal_matrix"
            if execution_classification is None
            else "smoke_suite"
        ),
        "ineligibility_reasons": (
            []
            if execution_classification is None
            else list(execution_classification["ineligibility_reasons"])
        ),
        "paper_eligible": (
            execution_classification is None
            and isinstance(result.metrics_ref, Mapping)
            and result.metrics_ref.get("paper_eligible") is True
        ),
    }
    if isinstance(result.metrics_ref, Mapping):
        outcome_counts = result.metrics_ref.get("outcome_counts")
        if isinstance(outcome_counts, Mapping):
            row["outcome_counts"] = dict(outcome_counts)
            row["evidence_integrity"] = result.metrics_ref.get(
                "evidence_integrity",
                "invalid"
                if result.metrics_ref.get("infrastructure_blocked")
                else "complete",
            )
    return row


def _catalog_cases_by_id(catalog_manifest: Any) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for field_name in (
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
    ):
        values = (
            catalog_manifest.get(field_name, ())
            if isinstance(catalog_manifest, Mapping)
            else getattr(catalog_manifest, field_name, ())
        )
        for case in values:
            if not isinstance(case, Mapping) or not isinstance(
                case.get("case_id"), str
            ):
                raise ValueError("formal catalog case is invalid")
            case_id = str(case["case_id"])
            if case_id in cases:
                raise ValueError("duplicate case_id in formal catalog")
            cases[case_id] = dict(case)
    return cases


def _case_with_selection_split_profile(
    case: Mapping[str, Any],
    selection: Any,
) -> dict[str, Any]:
    profile_id = getattr(selection, "split_profile_id", None)
    if profile_id is None:
        return dict(case)
    split_params = case.get("split_params")
    if not isinstance(split_params, Mapping):
        raise ValueError("split profile requires factorization split_params")
    return {
        **dict(case),
        "split_params": {
            "strategy_id": split_params.get("strategy_id"),
            "range_policy": split_params.get("range_policy"),
            "split_profile_id": profile_id,
        },
    }


def _condition_with_frozen_case_metadata(
    *,
    condition: PaperExperimentCondition,
    case: Mapping[str, Any],
) -> PaperExperimentCondition:
    """把冻结 case 的逐题元数据绑定到临时 adapter 执行 condition。"""

    if condition.domain == "factorization":
        difficulty = case.get("difficulty")
        paper_difficulty = case.get("paper_difficulty")
        if difficulty is None and paper_difficulty is None:
            return condition
        if not isinstance(difficulty, str) or not difficulty:
            raise ValueError("frozen factorization case difficulty is required")
        if not isinstance(paper_difficulty, str) or not paper_difficulty:
            raise ValueError(
                "frozen factorization case paper_difficulty is required"
            )
        return replace(
            condition,
            difficulty=difficulty,
            paper_difficulty=paper_difficulty,
        )
    if condition.domain != "lean_proof":
        return condition
    updates: dict[str, Any] = {}
    for field_name in (
        "paper_difficulty",
        "topic_family",
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
    ):
        condition_value = getattr(condition, field_name)
        case_value = case.get(field_name)
        if condition_value is not None and condition_value != case_value:
            raise ValueError(
                f"condition {field_name} must match frozen Lean case metadata"
            )
        if condition_value is None:
            updates[field_name] = case_value
    return replace(condition, **updates) if updates else condition


def _required_field(value: Any, field_name: str) -> Any:
    result = _optional_field(value, field_name)
    if result is None:
        raise ValueError(f"adapter result requires {field_name}")
    return result


def _validate_runtime_attempt_identity(
    attempt: Any,
    approved_identity: Mapping[str, Any],
) -> None:
    expected_by_field = {
        "provider": approved_identity.get("provider_family"),
        "model": approved_identity.get("provider_model_id"),
        "entry_id": approved_identity.get("model_entry_id"),
    }
    for field_name, expected in expected_by_field.items():
        if expected is None or _required_field(attempt, field_name) != expected:
            raise ValueError(f"runtime replacement identity drift: {field_name}")


def _optional_field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)
