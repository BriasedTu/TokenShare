"""EPD-027 bounded paper-pipeline CLI 的薄路由层。

本模块主要解析、校验调用边界并委派既有实验服务。中性 representative 命令
只在 full-plan authority 成功后构造正式 transport；本模块不实现协议状态机。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import shutil
import sqlite3
import sys
import tempfile
from threading import Lock
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_formal_plan import FormalExecutionCoverage
    from tokenshare.experiments.paper_models import PaperBudgetResult
    from tokenshare.experiments.paper_response_bank import SemanticInventoryPlan

from tokenshare.experiments.paper_budget import validate_provider_budget_mode
from tokenshare.experiments.paper_paid_authorization import (
    PaidAuthorizationValidation,
    output_root_path_digest,
    validate_paid_execution_receipt,
)
from tokenshare.experiments.paper_pipeline_profile import (
    PaperPipelineProfile,
    load_paper_pipeline_profile,
)


RESULT_SCHEMA_VERSION = "tokenshare.paper_pipeline_cli_result.v1"
PAPER_PIPELINE_COMMANDS = frozenset(
    {
        "validate-profile",
        "plan-bank",
        "acquire-bank",
        "audit-bank",
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-capability-smoke",
        "run-exp5-online",
        "render",
        "replay",
        "audit-cell-lineage",
        "validate-formal-execution-gate",
        "validate-paper-publication-gate",
        "run-results-first",
        "representative-full-plan-smoke",
        "audit-results-first-metric-merge",
        "render-results-first-combined",
    }
)

_PROVIDER_SCOPES = {
    "acquire-bank": "epd027_full_bank_acquisition",
    "run-online-checks": "epd027_l3_capability_and_online_checks",
    "run-exp1-online": "exp1_full_online",
    "run-exp5-capability-smoke": "exp5_capability_smoke",
    "run-exp5-online": "exp5_full_online",
}
_EVIDENCE_CLASSES = {
    "validate-profile": "offline_profile_validation",
    "plan-bank": "offline_bank_plan",
    "audit-bank": "offline_bank_audit",
    "run-trace": "real_model_trace_protocol_run",
    "render": "offline_render",
    "replay": "offline_replay",
    "audit-cell-lineage": "offline_cell_lineage",
    "validate-formal-execution-gate": "offline_gate_parser_only",
    "validate-paper-publication-gate": "offline_gate_parser_only",
    "run-results-first": "results_first_execution",
    "representative-full-plan-smoke": "results_first_execution",
    "audit-results-first-metric-merge": "offline_results_first_metric_merge_audit",
    "render-results-first-combined": "offline_results_first_combined_render",
}
_RESULTS_FIRST_COMMANDS = frozenset(
    {"run-results-first", "representative-full-plan-smoke"}
)
_PAID_RESTORE_CANONICAL_DERIVATION_MINT = object()
_PAID_RESTORE_WARM_EVIDENCE_REGISTRY: dict[int, object] = {}
_PAID_RESTORE_WARM_EVIDENCE_REGISTRY_LOCK = Lock()
_PAID_PLAN_ONLY_CERTIFICATE_MINT = object()
_PAID_PLAN_ONLY_CERTIFICATE_REGISTRY: dict[int, object] = {}
_PAID_PLAN_ONLY_CERTIFICATE_REGISTRY_LOCK = Lock()
_PAID_LAUNCH_READINESS_MINT = object()
_PAID_LAUNCH_READINESS_REGISTRY: dict[int, object] = {}
_PAID_LAUNCH_READINESS_REGISTRY_LOCK = Lock()
_PAID_LAUNCH_HELD_LOCKS: set[Path] = set()
_PAID_LAUNCH_HELD_LOCKS_LOCK = Lock()
PAID_LAUNCH_ATTESTATION_NAME = "paid_launch_key_readiness.v1.json"
PAID_LAUNCH_LOCK_NAME = "paid_launch_single_instance.lock"
_PROVIDER_ZERO_REFERENCE_BINDING_NAME = "provider_zero_reference_binding.v2.json"
_PROVIDER_ZERO_REFERENCE_BINDING_REF_NAME = (
    "provider_zero_reference_binding_ref.v2.json"
)
_PROVIDER_ZERO_REFERENCE_BINDING_SCHEMA = (
    "tokenshare.provider_zero_reference_binding.v2"
)
_PROVIDER_ZERO_REFERENCE_BINDING_REF_SCHEMA = (
    "tokenshare.provider_zero_reference_binding_ref.v2"
)
_FORMAL_GATED_COMMANDS = frozenset(
    {"run-trace", "run-exp1-online", "run-exp5-online"}
)


@dataclass(frozen=True, kw_only=True)
class ExternalBankResolverBinding:
    """只在当前进程保存 root；持久化参数视图永远不包含路径。"""

    root: Path

    def open(self) -> Any:
        from tokenshare.executors.response_bank import ResponseBankResolver

        return ResponseBankResolver.open(self.root)


def validate_results_first_external_bank_binding(
    *,
    external_bank_root: str | Path,
    preparation: Mapping[str, object],
    authority: object,
    selection: str,
) -> Any:
    """在 canonical loader 内核对外部 bank 与 scope/预算 authority。

    PowerShell wrapper 的 inventory 检查不能替代这里的校验，因为 direct
    ``run-results-first`` CLI 也必须拒绝把另一份 bank 接到同一 scope。
    """

    resolver = ExternalBankResolverBinding(
        root=Path(external_bank_root).resolve(strict=False)
    ).open()
    manifest = getattr(getattr(resolver, "index", None), "manifest", None)
    expected_manifest_digest = preparation.get("source_bank_manifest_digest")
    expected_terminal_count = preparation.get("source_bank_terminal_entry_count")
    bundle = getattr(authority, "bundle", None)
    expected_budget_digest = getattr(
        getattr(bundle, "full_budget", None), "budget_digest", None
    )
    expected_inventory_digest = getattr(bundle, "inventory_digest", None)
    if (
        manifest is None
        or not isinstance(expected_manifest_digest, str)
        or not isinstance(expected_terminal_count, int)
        or manifest.manifest_digest != expected_manifest_digest
        or manifest.terminal_entry_count != expected_terminal_count
        or (
            selection == "full_exp1_exp3_exp5"
            and (
                not isinstance(expected_inventory_digest, str)
                or manifest.inventory_digest != expected_inventory_digest
                or not isinstance(expected_budget_digest, str)
                or manifest.budget_digest != expected_budget_digest
            )
        )
    ):
        raise ValueError("external response bank manifest binding drift")
    return resolver


@dataclass(frozen=True, kw_only=True)
class PipelineCommandRequest:
    command: str
    scope: str
    evidence_class: str
    profile: PaperPipelineProfile | Any
    provider_authorization: PaidAuthorizationValidation | Any | None
    output_root: Path | None
    replay_input_root: Path | None
    external_bank_resolver: ExternalBankResolverBinding | None
    plan_digest: str | None
    inventory_digest: str | None
    budget_mode: str
    serialized_arguments: Mapping[str, object]
    plan_bundle_root: Path | None = None
    _formal_authority: object | None = None
    _service_input: object | None = None

@dataclass(frozen=True, kw_only=True)
class AcquisitionServiceInput:
    """acquisition adapter 的进程内 typed 构造输入；不会进入 CLI 输出。"""

    scope: str
    orchestrator_arguments: Mapping[str, object]
    acquisition_requests: Sequence[object]
    max_in_flight: int = 1
    bundle: object | None = None
    manifest: object | None = None
    supervised_no_response_closure_root: Path | None = None


@dataclass(frozen=True, kw_only=True)
class FormalSuiteServiceInput:
    """official formal runner 的进程内 keyword 输入。"""

    scope: str
    keyword_arguments: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class FormalSuiteServiceResult:
    """保留 official runner typed terminal，同时只序列化公开摘要。"""

    public_result: Mapping[str, object]
    terminal_result: object

    def to_dict(self) -> dict[str, object]:
        return dict(self.public_result)


@dataclass(frozen=True, kw_only=True)
class ReportRenderServiceInput:
    """Task 21 renderer 所需的已验证 metrics 与 contract。"""

    metrics: object
    contract: object | None = None


@dataclass(frozen=True, kw_only=True)
class CellLineageAuditServiceInput:
    """Task 20 cell-lineage writer 所需的 typed observations。"""

    contract: object
    observations: Sequence[object]


@dataclass(frozen=True, kw_only=True)
class FormalExecutionGateServiceInput:
    """execution gate 的进程内 typed authority；不从 CLI JSON 伪造。"""

    selection: object
    prerequisites: object


@dataclass(frozen=True, kw_only=True)
class PaperPublicationGateServiceInput:
    """publication gate 的进程内 terminal evidence；不触发 replay/provider。"""

    selection: object
    terminal_evidence: object


@dataclass(frozen=True, kw_only=True)
class FormalRunGateServiceInput:
    """正式 run 的同进程两阶段 gate authority；不能从 argv/JSON 构造。"""

    execution_gate: FormalExecutionGateServiceInput
    publication_gate_factory: Callable[[object], object]


@dataclass(frozen=True, kw_only=True)
class ResultsFirstServiceInput:
    """full/representative 共用的 process-local typed service input。"""

    authority: object
    selection: str
    plan_only: bool
    allow_provider_calls: bool
    external_bank_root: Path | None
    resume: bool
    paid_launch_readiness: object | None = None
    supervised_no_response_closure_root: Path | None = None
    paid_authorization: object | None = None
    scope_preparation_root: Path | None = None


@dataclass(frozen=True, kw_only=True)
class ResultsFirstMetricMergeAuditServiceInput:
    """只读 results-first 合并分母审计的进程内输入。"""

    output_root: Path
    audit_output_root: Path
    selection: str


@dataclass(frozen=True, kw_only=True)
class ResultsFirstCombinedRenderServiceInput:
    """只读 115-root merge audit 后合成 publication 的进程内输入。"""

    output_root: Path
    metric_merge_audit_root: Path
    combined_output_root: Path
    selection: str


@dataclass(frozen=True, kw_only=True)
class ResultsFirstPaidLaunchKeyReadiness:
    attestation_root: Path
    attestation_digest: str
    output_root: Path
    provider_budget_authority_digest: str
    _mint_identity: object = field(repr=False, compare=False)

    def __copy__(self) -> object:
        raise TypeError("paid launch readiness token cannot be copied")

    def __deepcopy__(self, _memo: object) -> object:
        raise TypeError("paid launch readiness token cannot be copied")


@dataclass(frozen=True, kw_only=True)
class ResultsFirstTraceClosurePreflight:
    """从 immutable CURRENT/checkpoint facts 重算的 trace closure 摘要。"""

    condition_count: int
    root_count: int
    checkpoint_count: int
    attempt_count: int
    current_provider_attempt_count: int
    legacy_attempt_row_count: int
    positive_provider_attempt_ordinal_count: int
    source_consumption_count: int
    checkpoint_inventory_digest: str


@dataclass(frozen=True, kw_only=True)
class ResultsFirstClosureReplaySnapshot:
    """只在受信本地恢复中使用的 typed results-first authority handle。"""

    full_dispatch_plans: tuple[PaperExperimentDispatchPlan, ...]
    catalog_manifest: PaperInputCatalogManifest
    full_budget: PaperBudgetResult
    ai_api_configs: Mapping[
        str,
        AIAPIExecutorConfig | Mapping[str, object],
    ]
    coverage: FormalExecutionCoverage
    source_exp1_inventory_plan: SemanticInventoryPlan
    replay_semantic_authority: object
    semantic_inventory_plan: SemanticInventoryPlan
    execution_budget_projection: PaperExecutionBudgetProjection
    hard_limits: Mapping[str, Any]
    source_validation_digest: str
    suite_id: str
    snapshot_digest: str
    provider_calls_made: int = 0
    schema_version: str = (
        "tokenshare.results_first_closure_replay_snapshot.v1"
    )

    @property
    def coverage_digest(self) -> str:
        return str(getattr(self.coverage, "coverage_digest"))

    @property
    def full_budget_digest(self) -> str:
        return str(getattr(self.full_budget, "budget_digest"))

    def __post_init__(self) -> None:
        _validate_results_first_closure_replay_snapshot(self)


@dataclass(frozen=True, kw_only=True)
class _ResultsFirstCliProfile:
    """results-first authority 构造前的中性 CLI 占位；不会作为实验 authority。"""

    profile_digest: str | None = None
    budget_digest: str | None = None


_RESULTS_FIRST_CLI_PROFILE = _ResultsFirstCliProfile()


_REPRESENTATIVE_EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
    "exp5_real_ai_model_endpoint_comparison",
)
_REPRESENTATIVE_TRACE_EXPERIMENT_IDS = _REPRESENTATIVE_EXPERIMENT_IDS[:4]
_REPRESENTATIVE_EXP5_EXPERIMENT_ID = _REPRESENTATIVE_EXPERIMENT_IDS[-1]
_EXP4_EXCLUDED_EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp5_real_ai_model_endpoint_comparison",
)


def results_first_paid_scope_for_selection(selection: str) -> str:
    """只有两种已预注册 Exp4 排除 selection 可取得 paid receipt。"""

    scopes = {
        "representative_exp1_exp3_exp5": (
            "results_first_representative_exp1_exp3_exp5"
        ),
        "full_exp1_exp3_exp5": "results_first_full_exp1_exp3_exp5",
    }
    try:
        return scopes[selection]
    except KeyError as exc:
        raise ValueError("results-first selection has no dedicated paid scope") from exc


def _results_first_exp1_acquisition_is_new(
    *,
    selection: str,
    external_bank_root: Path | None,
) -> bool:
    """R13 external bank 仅在 representative 中是历史输入，不是本轮新付费 acquisition。"""

    return not (
        selection == "representative_exp1_exp3_exp5"
        and external_bank_root is not None
    )


def _results_first_experiment_ids_for_coverage(*, coverage: object) -> tuple[str, ...]:
    """只接受原完整集或预注册的 Exp4 排除集，避免临时条件过滤。"""

    observed = tuple(
        dict.fromkeys(
            str(getattr(condition, "experiment_id", ""))
            for condition in getattr(coverage, "conditions", ())
        )
    )
    if observed not in {
        _REPRESENTATIVE_EXPERIMENT_IDS,
        _EXP4_EXCLUDED_EXPERIMENT_IDS,
    }:
        raise ValueError("results-first coverage experiment set is not preregistered")
    return observed


@dataclass(frozen=True, kw_only=True)
class ResultsFirstExecutionPolicy:
    """results-first 两种规模共享且不得按 selection 改写的执行策略。"""

    trace_evidence_class: str = "real_model_trace_protocol_run"
    online_evidence_class: str = "online_real_provider"
    authorization_kind: str = "user_authorized_results_first"
    enforce_publication_closure: bool = False
    enable_metric_closure: bool = True
    bypass_nonmetric_facility_gates: bool = True
    adapter_chain: tuple[str, ...] = (
        "paper_formal_runner.execute_paper_formal_suite",
        "ProtocolEngine",
        "registered_domain_adapter",
    )
    schema_version: str = "tokenshare.results_first_execution_policy.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.results_first_execution_policy.v1":
            raise ValueError("results-first execution policy schema drift")
        if (
            self.trace_evidence_class != "real_model_trace_protocol_run"
            or self.online_evidence_class != "online_real_provider"
            or self.authorization_kind != "user_authorized_results_first"
            or self.enforce_publication_closure is not False
            or self.enable_metric_closure is not True
            or self.bypass_nonmetric_facility_gates is not True
            or self.adapter_chain
            != (
                "paper_formal_runner.execute_paper_formal_suite",
                "ProtocolEngine",
                "registered_domain_adapter",
            )
        ):
            raise ValueError("results-first execution policy cannot vary by scale")


_RESULTS_FIRST_EXECUTION_POLICY = ResultsFirstExecutionPolicy()


@dataclass(frozen=True, kw_only=True)
class ResultsFirstExecutionAuthority:
    """同一 full authority 上承载任意已验证 results-first selection。"""

    source_validation_digest: str
    full_dispatch_plans: tuple[object, ...]
    catalog_manifest: object
    full_snapshot: object
    full_prepared_inventory: object
    full_budget: object
    ai_api_configs: Mapping[str, object]
    coverage: object
    execution_budget_projection: object
    bundle: object
    bundle_root: Path
    output_root: Path
    resume: bool
    provider_budget_authority: object | None = None
    source_rebind_binding_digest: str | None = None
    source_rebind_ref_digest: str | None = None
    provider_zero_reference_binding_digest: str | None = None
    provider_zero_reference_binding_ref_digest: str | None = None
    current_provider_zero_preflight_report_digest: str | None = None
    policy: ResultsFirstExecutionPolicy = _RESULTS_FIRST_EXECUTION_POLICY
    provider_calls_made: int = 0

    def __post_init__(self) -> None:
        from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
        from tokenshare.experiments.paper_budget import ResultsFirstProviderBudgetAuthority
        from tokenshare.experiments.paper_formal_plan import (
            FormalExecutionCoverage,
            FormalPlanSnapshot,
            FormalPreparedRequestInventory,
        )
        from tokenshare.experiments.paper_models import PaperBudgetResult
        from tokenshare.experiments.paper_response_bank import (
            AcquisitionPlanBundle,
            ResultsFirstAcquisitionPlan,
        )

        if (
            type(self.full_snapshot) is not FormalPlanSnapshot
            or type(self.full_prepared_inventory) is not FormalPreparedRequestInventory
            or type(self.full_budget) is not PaperBudgetResult
            or type(self.coverage) is not FormalExecutionCoverage
            or type(self.execution_budget_projection)
            is not PaperExecutionBudgetProjection
            or type(self.bundle) not in {
                ResultsFirstAcquisitionPlan,
                AcquisitionPlanBundle,
            }
        ):
            raise TypeError("results-first execution authority object type drift")
        plans = tuple(self.full_dispatch_plans)
        conditions = self.coverage.conditions
        roots = self.coverage.roots
        root_filter = dict(self.coverage.root_case_filter)
        condition_ids = tuple(
            str(getattr(condition, "condition_id", "")) for condition in conditions
        )
        if tuple(getattr(plan, "experiment_id", None) for plan in plans) != (
            _REPRESENTATIVE_EXPERIMENT_IDS
        ):
            raise ValueError("representative service requires all formal experiments")
        if tuple(
            dict.fromkeys(
                str(getattr(condition, "experiment_id", ""))
                for condition in conditions
            )
        ) not in {
            _REPRESENTATIVE_EXPERIMENT_IDS,
            _EXP4_EXCLUDED_EXPERIMENT_IDS,
        }:
            raise ValueError("results-first coverage experiment set is not preregistered")
        if (
            not conditions
            or not roots
            or len(set(condition_ids)) != len(condition_ids)
            or set(root_filter) != set(condition_ids)
            or self.coverage.condition_count != len(conditions)
            or self.coverage.root_run_count != len(roots)
            or sum(len(case_ids) for case_ids in root_filter.values()) != len(roots)
            or self.coverage.provider_calls_made != 0
        ):
            raise ValueError("representative coverage authority is incomplete")
        for value, label in (
            (self.source_validation_digest, "validation"),
            (getattr(self.coverage, "source_snapshot_digest", None), "snapshot"),
            (getattr(self.coverage, "coverage_digest", None), "coverage"),
            (getattr(self.full_budget, "budget_digest", None), "budget"),
        ):
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError(f"representative {label} digest is invalid")
        if (
            self.coverage.source_snapshot is not self.full_snapshot
            or self.full_prepared_inventory.source_snapshot_digest
            != self.full_snapshot.snapshot_digest
            or self.execution_budget_projection.source_snapshot
            is not self.full_snapshot
            or self.execution_budget_projection.source_budget is not self.full_budget
            or self.execution_budget_projection.coverage is not self.coverage
            or self.full_snapshot.budget_digest != self.full_budget.budget_digest
        ):
            raise ValueError("results-first full authority lineage mismatch")
        if (
            getattr(self.bundle, "source_snapshot_digest", None)
            != self.full_snapshot.snapshot_digest
            or getattr(self.bundle, "source_prepared_inventory_digest", None)
            != self.full_prepared_inventory.inventory_digest
            or getattr(self.bundle, "coverage_digest", None)
            != self.coverage.coverage_digest
        ):
            raise ValueError("representative bundle/coverage lineage mismatch")
        if type(self.bundle) is AcquisitionPlanBundle and (
            self.bundle.semantic_inventory_plan is None
        ):
            raise ValueError("materialized results-first bundle is incomplete")
        if not isinstance(self.ai_api_configs, Mapping) or not self.ai_api_configs:
            raise ValueError("representative AI API configs are missing")
        if self.policy is not _RESULTS_FIRST_EXECUTION_POLICY:
            raise ValueError("results-first execution policy identity drift")
        if (
            type(self.provider_calls_made) is not int
            or self.provider_calls_made != 0
        ):
            raise ValueError("representative service planning called a provider")
        rebind_digests = (
            self.source_rebind_binding_digest,
            self.source_rebind_ref_digest,
        )
        if (rebind_digests[0] is None) is not (rebind_digests[1] is None):
            raise ValueError("results-first source rebind lineage is incomplete")
        if rebind_digests[0] is not None and any(
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            for value in rebind_digests
        ):
            raise ValueError("results-first source rebind digest is invalid")
        reference_digests = (
            self.provider_zero_reference_binding_digest,
            self.provider_zero_reference_binding_ref_digest,
            self.current_provider_zero_preflight_report_digest,
        )
        if any(value is None for value in reference_digests) and not all(
            value is None for value in reference_digests
        ):
            raise ValueError("results-first provider-zero reference lineage is incomplete")
        if reference_digests[0] is not None and any(
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            for value in reference_digests
        ):
            raise ValueError("results-first provider-zero reference digest is invalid")
        if self.provider_budget_authority is not None and (
            type(self.provider_budget_authority) is not ResultsFirstProviderBudgetAuthority
            or self.provider_budget_authority.to_dict().get("provider_calls_made") != 0
        ):
            raise ValueError("results-first provider budget authority drift")
        object.__setattr__(self, "full_dispatch_plans", plans)
        object.__setattr__(self, "bundle_root", Path(self.bundle_root))
        object.__setattr__(self, "output_root", Path(self.output_root))
        if not isinstance(self.ai_api_configs, MappingProxyType):
            object.__setattr__(
                self,
                "ai_api_configs",
                MappingProxyType(dict(self.ai_api_configs)),
            )

    @property
    def execution_policy(self) -> ResultsFirstExecutionPolicy:
        """旧属性名兼容；所有 scale 仍共享同一 singleton。"""

        return self.policy

    @property
    def hard_limits(self) -> Mapping[str, object]:
        """运行硬限制只能来自 typed execution budget projection。"""

        return self.execution_budget_projection.hard_limits


@dataclass(frozen=True, kw_only=True, init=False)
class ResultsFirstPaidPlanOnlyAuthority:
    """只读核验 frozen paid restore 后生成的 plan-only certificate。"""

    snapshot: ResultsFirstClosureReplaySnapshot
    bundle: object
    provider_budget_authority: object
    frozen_snapshot_root: Path
    frozen_prepared_inventory_root: Path
    plan_bundle_root: Path
    planning_artifact_root: Path
    expected_preflight_path: Path
    output_root: Path
    selection: str
    resume: bool
    source_validation_digest: str
    source_snapshot_digest: str
    coverage_digest: str
    full_budget_digest: str
    profile_digest: str
    condition_count: int
    root_run_count: int
    inventory_digest: str
    inventory_records_sha256: str
    inventory_records_size_bytes: int
    inventory_record_count: int
    preflight_report_digest: str
    preflight_file_sha256: str
    preflight_file_size_bytes: int
    binding_digest: str
    binding_ref_digest: str
    provider_calls_made: int = 0
    _mint_token: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ResultsFirstPaidPlanOnlyAuthority requires the loader mint")

    def __copy__(self) -> object:
        raise TypeError("paid plan-only certificate is process-local and non-copyable")

    def __deepcopy__(self, _memo: object) -> object:
        raise TypeError("paid plan-only certificate is process-local and non-copyable")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("paid plan-only certificate is process-local and non-picklable")

    def __post_init__(self) -> None:
        from tokenshare.experiments.paper_budget import (
            ResultsFirstProviderBudgetAuthority,
        )
        from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

        if (
            getattr(self, "_mint_token", None) is not _PAID_PLAN_ONLY_CERTIFICATE_MINT
        ):
            raise TypeError("paid plan-only certificate loader mint drift")
        if (
            type(self.snapshot) is not ResultsFirstClosureReplaySnapshot
            or type(self.bundle) is not AcquisitionPlanBundle
            or type(self.provider_budget_authority)
            is not ResultsFirstProviderBudgetAuthority
        ):
            raise TypeError("paid plan-only certificate typed authority drift")
        for name in (
            "frozen_snapshot_root",
            "frozen_prepared_inventory_root",
            "plan_bundle_root",
            "planning_artifact_root",
            "expected_preflight_path",
            "output_root",
        ):
            object.__setattr__(
                self,
                name,
                Path(getattr(self, name)).resolve(strict=False),
            )
        expected_selection = (
            "full"
            if self.snapshot.coverage.selection_kind == "full"
            else "representative"
        )
        if (
            self.selection != expected_selection
            or type(self.provider_calls_made) is not int
            or self.provider_calls_made != 0
            or self.snapshot.provider_calls_made != 0
            or self.provider_budget_authority.to_dict().get("provider_calls_made")
            != 0
            or self.provider_budget_authority.exp2_4.get("current_provider_calls")
            != 0
        ):
            raise ValueError("paid plan-only certificate mode or provider drift")
        for value, label in (
            (self.source_validation_digest, "source validation"),
            (self.source_snapshot_digest, "source snapshot"),
            (self.coverage_digest, "coverage"),
            (self.full_budget_digest, "full budget"),
            (self.profile_digest, "profile"),
            (self.inventory_digest, "inventory"),
            (self.inventory_records_sha256, "inventory records"),
            (self.preflight_report_digest, "preflight report"),
            (self.preflight_file_sha256, "preflight file"),
            (self.binding_digest, "binding"),
            (self.binding_ref_digest, "binding ref"),
        ):
            _paid_restore_require_sha256(value, label=label)
        for value, label in (
            (self.condition_count, "condition count"),
            (self.root_run_count, "root count"),
            (self.inventory_records_size_bytes, "inventory records size"),
            (self.inventory_record_count, "inventory record count"),
            (self.preflight_file_size_bytes, "preflight file size"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(f"paid plan-only certificate {label} is invalid")
        if (
            self.condition_count < 1
            or self.root_run_count < 1
            or self.inventory_records_size_bytes < 1
            or self.inventory_record_count < 1
            or self.preflight_file_size_bytes < 1
        ):
            raise ValueError("paid plan-only certificate fixed denominator is empty")
        if (
            self.source_validation_digest != self.snapshot.source_validation_digest
            or self.source_snapshot_digest
            != self.snapshot.coverage.source_snapshot.snapshot_digest
            or self.coverage_digest != self.snapshot.coverage.coverage_digest
            or self.full_budget_digest != self.snapshot.full_budget.budget_digest
            or self.condition_count != self.snapshot.coverage.condition_count
            or self.root_run_count != self.snapshot.coverage.root_run_count
            or self.inventory_digest != self.bundle.source_prepared_inventory_digest
        ):
            raise ValueError("paid plan-only certificate frozen lineage drift")


def _mint_results_first_paid_plan_only_authority(
    **values: object,
) -> ResultsFirstPaidPlanOnlyAuthority:
    expected_fields = set(ResultsFirstPaidPlanOnlyAuthority.__dataclass_fields__)
    expected_fields.remove("_mint_token")
    if set(values) != expected_fields:
        raise TypeError("paid plan-only certificate loader mint fields drift")
    certificate = object.__new__(ResultsFirstPaidPlanOnlyAuthority)
    for name, value in values.items():
        object.__setattr__(certificate, name, value)
    object.__setattr__(
        certificate,
        "_mint_token",
        _PAID_PLAN_ONLY_CERTIFICATE_MINT,
    )
    certificate.__post_init__()
    return certificate


def _register_paid_plan_only_certificate(
    certificate: ResultsFirstPaidPlanOnlyAuthority,
) -> None:
    if (
        type(certificate) is not ResultsFirstPaidPlanOnlyAuthority
        or getattr(certificate, "_mint_token", None)
        is not _PAID_PLAN_ONLY_CERTIFICATE_MINT
    ):
        raise TypeError("paid plan-only certificate loader mint drift")
    identity = id(certificate)
    with _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY_LOCK:
        if identity in _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY:
            raise TypeError("paid plan-only certificate is already registered")
        _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY[identity] = certificate


def _consume_paid_plan_only_certificate(
    certificate: ResultsFirstPaidPlanOnlyAuthority,
) -> None:
    identity = id(certificate)
    with _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY_LOCK:
        registered = _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY.get(identity)
        if registered is not certificate:
            raise TypeError(
                "paid plan-only certificate is not registered or was already consumed"
            )
        del _PAID_PLAN_ONLY_CERTIFICATE_REGISTRY[identity]


@dataclass(frozen=True, kw_only=True, init=False)
class PaidRestoreCanonicalDerivationEvidence:
    """同进程 immediate reload 使用的已验证 typed authority 封印。"""

    authority: ResultsFirstExecutionAuthority
    snapshot: ResultsFirstClosureReplaySnapshot
    inventory: object
    frozen_snapshot_root: Path
    frozen_prepared_inventory_root: Path
    plan_bundle_root: Path
    planning_artifact_root: Path
    output_root: Path
    selection: str
    resume: bool
    prepared_inventory_manifest: Mapping[str, object]
    prepared_inventory_manifest_digest: str
    inventory_records_sha256: str
    inventory_records_size_bytes: int
    inventory_record_count: int
    inventory_digest: str
    source_exp1_inventory_plan: Mapping[str, object]
    source_exp1_inventory_plan_digest: str
    acquisition_bundle_digest: str
    representative_plan_digest: str
    expected_preflight_path: Path
    expected_preflight_report_digest: str
    expected_preflight_file_sha256: str
    expected_preflight_file_size_bytes: int
    provider_budget_authority_digest: str
    binding: Mapping[str, object]
    binding_ref: Mapping[str, object]
    provider_calls_made: int = 0
    _mint_token: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "PaidRestoreCanonicalDerivationEvidence requires the persist mint"
        )

    def __copy__(self) -> object:
        raise TypeError("paid restore warm evidence is process-local and non-copyable")

    def __deepcopy__(self, _memo: object) -> object:
        raise TypeError("paid restore warm evidence is process-local and non-copyable")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("paid restore warm evidence is process-local and non-picklable")

    def __post_init__(self) -> None:
        from tokenshare.experiments.paper_budget import (
            ResultsFirstProviderBudgetAuthority,
        )
        from tokenshare.experiments.paper_formal_plan import (
            FormalPreparedRequestInventory,
        )
        from tokenshare.experiments.paper_models import digest_json
        from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

        if (
            getattr(self, "_mint_token", None)
            is not _PAID_RESTORE_CANONICAL_DERIVATION_MINT
        ):
            raise TypeError("paid restore warm evidence persist mint drift")
        if (
            type(self.authority) is not ResultsFirstExecutionAuthority
            or type(self.snapshot) is not ResultsFirstClosureReplaySnapshot
            or type(self.inventory) is not FormalPreparedRequestInventory
            or type(self.authority.bundle) is not AcquisitionPlanBundle
            or type(self.authority.provider_budget_authority)
            is not ResultsFirstProviderBudgetAuthority
        ):
            raise TypeError("paid restore warm evidence object type drift")
        if (
            self.authority.full_prepared_inventory is not self.inventory
            or self.selection not in {"full", "representative"}
            or self.authority.resume is not self.resume
            or self.authority.provider_calls_made != 0
            or self.snapshot.provider_calls_made != 0
            or type(self.provider_calls_made) is not int
            or self.provider_calls_made != 0
        ):
            raise ValueError("paid restore warm evidence authority drift")
        for value, label in (
            (self.prepared_inventory_manifest_digest, "inventory manifest"),
            (self.inventory_records_sha256, "inventory records"),
            (self.inventory_digest, "inventory"),
            (self.source_exp1_inventory_plan_digest, "source plan"),
            (self.acquisition_bundle_digest, "acquisition bundle"),
            (self.representative_plan_digest, "representative plan"),
            (self.expected_preflight_report_digest, "preflight"),
            (self.expected_preflight_file_sha256, "preflight file"),
            (self.provider_budget_authority_digest, "provider budget"),
            (self.binding.get("binding_digest"), "binding"),
            (self.binding_ref.get("ref_digest"), "binding ref"),
        ):
            _paid_restore_require_sha256(value, label=label)
        if (
            isinstance(self.inventory_records_size_bytes, bool)
            or not isinstance(self.inventory_records_size_bytes, int)
            or self.inventory_records_size_bytes < 0
            or isinstance(self.inventory_record_count, bool)
            or not isinstance(self.inventory_record_count, int)
            or self.inventory_record_count < 0
            or isinstance(self.expected_preflight_file_size_bytes, bool)
            or not isinstance(self.expected_preflight_file_size_bytes, int)
            or self.expected_preflight_file_size_bytes < 0
            or self.inventory_record_count != self.inventory.record_count
            or self.source_exp1_inventory_plan_digest
            != digest_json(self.source_exp1_inventory_plan)
            or self.authority.bundle.bundle_digest != self.acquisition_bundle_digest
            or self.authority.bundle.representative_plan_digest
            != self.representative_plan_digest
            or self.authority.provider_budget_authority.authority_digest
            != self.provider_budget_authority_digest
        ):
            raise ValueError("paid restore warm evidence digest drift")
        resolved_paths = {
            name: Path(value).resolve(strict=False)
            for name, value in (
                ("frozen_snapshot_root", self.frozen_snapshot_root),
                (
                    "frozen_prepared_inventory_root",
                    self.frozen_prepared_inventory_root,
                ),
                ("plan_bundle_root", self.plan_bundle_root),
                ("planning_artifact_root", self.planning_artifact_root),
                ("output_root", self.output_root),
                ("expected_preflight_path", self.expected_preflight_path),
            )
        }
        for name, value in resolved_paths.items():
            object.__setattr__(self, name, value)
        for name in (
            "prepared_inventory_manifest",
            "source_exp1_inventory_plan",
            "binding",
            "binding_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))
        expected_selection = (
            "full"
            if self.authority.coverage.selection_kind == "full"
            else "representative"
        )
        authority_source_plan = self.authority.bundle.to_dict().get(
            "semantic_inventory_plan"
        )
        if (
            self.plan_bundle_root
            != self.authority.bundle_root.resolve(strict=False)
            or self.output_root != self.authority.output_root.resolve(strict=False)
            or self.selection != expected_selection
            or self.snapshot.coverage.source_snapshot.snapshot_digest
            != self.authority.full_snapshot.snapshot_digest
            or self.snapshot.coverage.coverage_digest
            != self.authority.coverage.coverage_digest
            or self.snapshot.full_budget.budget_digest
            != self.authority.full_budget.budget_digest
            or self.snapshot.execution_budget_projection.projection_digest
            != self.authority.execution_budget_projection.projection_digest
            or self.snapshot.source_validation_digest
            != self.authority.source_validation_digest
            or authority_source_plan != dict(self.source_exp1_inventory_plan)
        ):
            raise ValueError("paid restore warm evidence typed lineage drift")

    @property
    def binding_digest(self) -> str:
        return str(self.binding["binding_digest"])

    @property
    def binding_ref_digest(self) -> str:
        return str(self.binding_ref["ref_digest"])


def _mint_paid_restore_canonical_derivation_evidence(
    **values: object,
) -> PaidRestoreCanonicalDerivationEvidence:
    expected_fields = set(PaidRestoreCanonicalDerivationEvidence.__dataclass_fields__)
    expected_fields.remove("_mint_token")
    if set(values) != expected_fields:
        raise TypeError("paid restore warm evidence persist mint fields drift")
    evidence = object.__new__(PaidRestoreCanonicalDerivationEvidence)
    for name, value in values.items():
        object.__setattr__(evidence, name, value)
    object.__setattr__(
        evidence,
        "_mint_token",
        _PAID_RESTORE_CANONICAL_DERIVATION_MINT,
    )
    evidence.__post_init__()
    return evidence


def _register_paid_restore_warm_evidence(
    evidence: PaidRestoreCanonicalDerivationEvidence,
) -> None:
    if (
        type(evidence) is not PaidRestoreCanonicalDerivationEvidence
        or getattr(evidence, "_mint_token", None)
        is not _PAID_RESTORE_CANONICAL_DERIVATION_MINT
    ):
        raise TypeError("paid restore warm evidence persist mint drift")
    identity = id(evidence)
    with _PAID_RESTORE_WARM_EVIDENCE_REGISTRY_LOCK:
        if identity in _PAID_RESTORE_WARM_EVIDENCE_REGISTRY:
            raise TypeError("paid restore warm evidence is already registered")
        _PAID_RESTORE_WARM_EVIDENCE_REGISTRY[identity] = evidence


def _consume_paid_restore_warm_evidence(
    evidence: PaidRestoreCanonicalDerivationEvidence,
) -> None:
    identity = id(evidence)
    with _PAID_RESTORE_WARM_EVIDENCE_REGISTRY_LOCK:
        registered = _PAID_RESTORE_WARM_EVIDENCE_REGISTRY.get(identity)
        if registered is not evidence:
            raise TypeError(
                "paid restore warm evidence is not registered or was already consumed"
            )
        del _PAID_RESTORE_WARM_EVIDENCE_REGISTRY[identity]


# 兼容旧调用方；旧名称不再拥有独立执行语义。
RepresentativeFullPlanSmokeServiceAuthority = ResultsFirstExecutionAuthority


@dataclass(frozen=True, kw_only=True)
class ResultsFirstTraceClosureReplayAuthority:
    """从digest-bound snapshot恢复的Exp1--4 production resume authority。"""

    snapshot: ResultsFirstClosureReplaySnapshot
    output_root: Path
    resume: bool = True
    policy: ResultsFirstExecutionPolicy = _RESULTS_FIRST_EXECUTION_POLICY

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ResultsFirstClosureReplaySnapshot:
            raise TypeError("typed closure replay snapshot is required")
        _validate_results_first_closure_replay_snapshot(self.snapshot)
        root = Path(self.output_root).resolve(strict=False)
        if self.resume is not True:
            raise ValueError("closure replay authority requires resume mode")
        if self.policy is not _RESULTS_FIRST_EXECUTION_POLICY:
            raise ValueError("closure replay execution policy identity drift")
        object.__setattr__(self, "output_root", root)

    @property
    def full_dispatch_plans(self) -> tuple[PaperExperimentDispatchPlan, ...]:
        return self.snapshot.full_dispatch_plans

    @property
    def catalog_manifest(self) -> PaperInputCatalogManifest:
        return self.snapshot.catalog_manifest

    @property
    def full_budget(self) -> PaperBudgetResult:
        return self.snapshot.full_budget

    @property
    def ai_api_configs(self) -> Mapping[str, object]:
        return self.snapshot.ai_api_configs

    @property
    def coverage(self) -> FormalExecutionCoverage:
        return self.snapshot.coverage

    @property
    def hard_limits(self) -> Mapping[str, Any]:
        return self.snapshot.hard_limits

    @property
    def execution_policy(self) -> ResultsFirstExecutionPolicy:
        return self.policy


_CLOSURE_REPLAY_SNAPSHOT_SCHEMA_VERSION = (
    "tokenshare.results_first_closure_replay_snapshot.v1"
)
_CLOSURE_REPLAY_DESCRIPTOR_SCHEMA_VERSION = (
    "tokenshare.results_first_closure_replay_snapshot_descriptor.v1"
)
_CLOSURE_REPLAY_REF_SCHEMA_VERSION = (
    "tokenshare.results_first_closure_replay_snapshot_ref.v1"
)
_CLOSURE_REPLAY_DIRECTORY = "closure-replay-snapshot"
_CLOSURE_REPLAY_DESCRIPTOR_NAME = "closure_replay_snapshot.v1.json"
_CLOSURE_REPLAY_PICKLE_NAME = "closure_replay_snapshot.v1.pickle"
_CLOSURE_REPLAY_REF_NAME = "closure_replay_snapshot_ref.v1.json"
_PAID_RESTORE_BINDING_SCHEMA_VERSION = (
    "tokenshare.results_first_paid_restore_binding.v2"
)
_PAID_RESTORE_BINDING_REF_SCHEMA_VERSION = (
    "tokenshare.results_first_paid_restore_binding_ref.v2"
)
_PAID_RESTORE_BINDING_NAME = "closure_replay_paid_restore_binding.v2.json"
_PAID_RESTORE_BINDING_REF_NAME = (
    "closure_replay_paid_restore_binding_ref.v2.json"
)
_PAID_SOURCE_REBIND_SCHEMA_VERSION = (
    "tokenshare.results_first_paid_source_rebind.v1"
)
_PAID_SOURCE_REBIND_REF_SCHEMA_VERSION = (
    "tokenshare.results_first_paid_source_rebind_ref.v1"
)
_PAID_SOURCE_REBIND_PREFLIGHT_NAME = "paid_source_rebind_preflight.v1.json"
_PAID_SOURCE_REBIND_BINDING_NAME = "results_first_paid_source_rebind.v1.json"
_PAID_SOURCE_REBIND_REF_NAME = "results_first_paid_source_rebind_ref.v1.json"
_FORMAL_DISPATCH_SCHEMA_VERSION = "tokenshare.paper_dispatch.v1"
_POST_ACQUISITION_TRACE_SEAL_NAME = "post_acquisition_trace_seal.v1.json"


def _post_acquisition_trace_artifact(
    *, output_root: Path, path: str | Path
) -> dict[str, object]:
    resolved = Path(path).resolve(strict=False)
    try:
        relative = resolved.relative_to(output_root)
    except ValueError as exc:
        raise ValueError("post-acquisition authority is outside output root") from exc
    if not resolved.is_file():
        raise ValueError("post-acquisition authority is missing")
    payload = resolved.read_bytes()
    return {
        "relative_path": relative.as_posix(),
        "sha256": "sha256:" + sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def persist_results_first_post_acquisition_trace_seal(
    *,
    output_root: str | Path,
    closure_snapshot_digest: str,
    source_manifest_digest: str,
    attempt_history_path: str | Path,
    router_authority_path: str | Path,
    exp2_derivation_path: str | Path,
) -> dict[str, object]:
    """Write-once binding for the post-acquisition trace authorities used by resume."""

    from tokenshare.experiments.paper_models import digest_json

    root = Path(output_root).resolve(strict=False)
    artifacts = {
        "attempt_history": _post_acquisition_trace_artifact(
            output_root=root, path=attempt_history_path
        ),
        "router_authority": _post_acquisition_trace_artifact(
            output_root=root, path=router_authority_path
        ),
        "exp2_derivation": _post_acquisition_trace_artifact(
            output_root=root, path=exp2_derivation_path
        ),
    }
    body = {
        "schema_version": "tokenshare.results_first_post_acquisition_trace_seal.v1",
        "closure_snapshot_digest": closure_snapshot_digest,
        "source_manifest_digest": source_manifest_digest,
        "artifacts": artifacts,
        "current_provider_calls": 0,
    }
    seal = {**body, "seal_digest": digest_json(body)}
    path = root / _CLOSURE_REPLAY_DIRECTORY / _POST_ACQUISITION_TRACE_SEAL_NAME
    encoded = _closure_snapshot_json_bytes(seal)
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("existing post-acquisition trace seal drifted")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
    return seal


def load_results_first_post_acquisition_trace_seal(
    *,
    output_root: str | Path,
    expected_closure_snapshot_digest: str,
    expected_source_manifest_digest: str,
) -> dict[str, object]:
    """Validate the seal and every bound sidecar before any resume router rebuild."""

    from tokenshare.experiments.paper_models import digest_json

    root = Path(output_root).resolve(strict=False)
    path = root / _CLOSURE_REPLAY_DIRECTORY / _POST_ACQUISITION_TRACE_SEAL_NAME
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("post-acquisition trace seal is missing or invalid") from exc
    expected_keys = {
        "schema_version",
        "closure_snapshot_digest",
        "source_manifest_digest",
        "artifacts",
        "current_provider_calls",
        "seal_digest",
    }
    if not isinstance(body, dict) or set(body) != expected_keys:
        raise ValueError("post-acquisition trace seal fields drifted")
    digest = body.pop("seal_digest")
    if (
        body["schema_version"]
        != "tokenshare.results_first_post_acquisition_trace_seal.v1"
        or body["closure_snapshot_digest"] != expected_closure_snapshot_digest
        or body["source_manifest_digest"] != expected_source_manifest_digest
        or body["current_provider_calls"] != 0
        or not isinstance(digest, str)
        or digest != digest_json(body)
    ):
        raise ValueError("post-acquisition trace seal identity drifted")
    artifacts = body["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "attempt_history",
        "router_authority",
        "exp2_derivation",
    }:
        raise ValueError("post-acquisition trace seal artifact coverage drifted")
    for value in artifacts.values():
        if not isinstance(value, dict) or set(value) != {
            "relative_path",
            "sha256",
            "size_bytes",
        }:
            raise ValueError("post-acquisition trace seal artifact fields drifted")
        relative = value["relative_path"]
        if not isinstance(relative, str) or not relative:
            raise ValueError("post-acquisition trace seal artifact path drifted")
        resolved = (root / PurePosixPath(relative)).resolve(strict=False)
        actual = _post_acquisition_trace_artifact(output_root=root, path=resolved)
        if actual != value:
            raise ValueError("post-acquisition trace seal artifact digest drifted")
    return {**body, "seal_digest": digest}


def _restore_closure_mapping_proxy(value: dict[object, object]) -> Mapping:
    """pickle 只在本模块内恢复不可变 mapping authority。"""

    return MappingProxyType(value)


class _ClosureReplaySnapshotPickler(pickle.Pickler):
    def reducer_override(self, value: object) -> object:
        if type(value) is MappingProxyType:
            return (_restore_closure_mapping_proxy, (dict(value),))
        return NotImplemented


def _closure_snapshot_pickle(value: object) -> bytes:
    stream = io.BytesIO()
    _ClosureReplaySnapshotPickler(
        stream,
        protocol=pickle.HIGHEST_PROTOCOL,
    ).dump(value)
    return stream.getvalue()


def _closure_snapshot_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _atomic_replace_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _atomic_write_once_bytes(path: Path, value: bytes) -> None:
    """同目录原子发布不可覆盖文件；存在即 fail closed。"""

    if not path.parent.is_dir():
        raise ValueError("write-once parent directory is missing")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _closure_snapshot_semantic_body(value: object) -> dict[str, object]:
    from tokenshare.experiments.paper_response_bank import (
        SemanticInventoryPlan,
        _semantic_inventory_plan_to_dict,
    )

    if type(value) is not SemanticInventoryPlan:
        raise TypeError("closure replay semantic inventory type drift")
    return _semantic_inventory_plan_to_dict(value)


def _closure_snapshot_dispatch_body(
    plans: Sequence[object],
    *,
    active_experiment_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """与 formal runner 的 persisted dispatch wrapper 保持同一 canonical shape。"""

    selected_plans = tuple(plans)
    if active_experiment_ids is not None:
        active = tuple(active_experiment_ids)
        if not active or len(set(active)) != len(active):
            raise ValueError("closure replay active experiment ids are invalid")
        selected_plans = tuple(
            plan for plan in selected_plans if plan.experiment_id in set(active)
        )
        if tuple(plan.experiment_id for plan in selected_plans) != active:
            raise ValueError("closure replay active dispatch order drift")
    return {
        "schema_version": _FORMAL_DISPATCH_SCHEMA_VERSION,
        "plans": [plan.to_dict() for plan in selected_plans],
    }


def _closure_snapshot_trace_experiment_ids(
    snapshot: ResultsFirstClosureReplaySnapshot,
) -> tuple[str, ...]:
    expected_experiment_ids = _results_first_experiment_ids_for_coverage(
        coverage=snapshot.coverage
    )
    trace_experiment_ids = tuple(
        experiment_id
        for experiment_id in expected_experiment_ids
        if experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    expected_suite_id = (
        "results_first_exp1_exp4_trace"
        if trace_experiment_ids == _REPRESENTATIVE_TRACE_EXPERIMENT_IDS
        else "results_first_exp1_exp3_trace"
    )
    if snapshot.suite_id != expected_suite_id:
        raise ValueError("closure replay suite id is not the selected formal trace suite")
    if tuple(
        plan.experiment_id for plan in snapshot.full_dispatch_plans
    ) != _REPRESENTATIVE_EXPERIMENT_IDS:
        raise ValueError("closure replay full dispatch authority is incomplete")
    return trace_experiment_ids


def _closure_snapshot_config_digests(
    values: Mapping[str, object],
) -> dict[str, str]:
    from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
    from tokenshare.experiments.paper_formal_runner import (
        APPROVED_ENDPOINT_BINDINGS_KEY,
    )
    from tokenshare.experiments.paper_models import digest_json

    if not isinstance(values, Mapping) or not values:
        raise TypeError("closure replay AI API configs are missing")
    result: dict[str, str] = {}
    for config_id in sorted(values):
        if not isinstance(config_id, str) or not config_id:
            raise TypeError("closure replay AI API config id is invalid")
        value = values[config_id]
        if config_id == APPROVED_ENDPOINT_BINDINGS_KEY:
            if not isinstance(value, Mapping) or not value:
                raise TypeError("closure replay endpoint binding authority is invalid")
            result[config_id] = digest_json(value)
            continue
        if type(value) is not AIAPIExecutorConfig:
            raise TypeError("closure replay AI API config type drift")
        safe_config = value.to_safe_dict()
        _closure_snapshot_reject_secret_fields(safe_config)
        if any(
            not entry.api_key_env.isidentifier()
            or entry.api_key_env != entry.api_key_env.upper()
            for entry in value.entries
        ):
            raise ValueError("closure replay API key reference is not an env name")
        result[config_id] = value.config_digest
    if not any(config_id != APPROVED_ENDPOINT_BINDINGS_KEY for config_id in result):
        raise ValueError("closure replay provider config authority is missing")
    return result


def _closure_snapshot_reject_secret_fields(value: object) -> None:
    forbidden = {
        "api_key",
        "api_key_value",
        "authorization",
        "bearer_token",
        "secret",
        "secret_value",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in forbidden:
                raise ValueError("closure replay snapshot contains a secret field")
            _closure_snapshot_reject_secret_fields(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _closure_snapshot_reject_secret_fields(item)


def _closure_snapshot_lineage(
    snapshot: ResultsFirstClosureReplaySnapshot,
) -> dict[str, object]:
    from tokenshare.experiments.paper_models import digest_json

    full_dispatch_body = _closure_snapshot_dispatch_body(
        snapshot.full_dispatch_plans,
    )
    dispatch_body = _closure_snapshot_dispatch_body(
        snapshot.full_dispatch_plans,
        active_experiment_ids=_closure_snapshot_trace_experiment_ids(snapshot),
    )
    catalog_body = snapshot.catalog_manifest.to_dict()
    budget_body = snapshot.full_budget.to_dict()
    semantic_body = _closure_snapshot_semantic_body(
        snapshot.semantic_inventory_plan
    )
    source_semantic_body = _closure_snapshot_semantic_body(
        snapshot.source_exp1_inventory_plan
    )
    return {
        "source_validation_digest": snapshot.source_validation_digest,
        "source_snapshot_digest": snapshot.coverage.source_snapshot_digest,
        "full_dispatch_body_digest": digest_json(full_dispatch_body),
        "dispatch_body_digest": digest_json(dispatch_body),
        "catalog_body_digest": digest_json(catalog_body),
        "catalog_digest": snapshot.catalog_manifest.catalog_digest,
        "full_budget_body_digest": digest_json(budget_body),
        "full_budget_digest": snapshot.full_budget.budget_digest,
        "coverage_digest": snapshot.coverage.coverage_digest,
        "semantic_inventory_digest": (
            snapshot.semantic_inventory_plan.inventory_digest
        ),
        "semantic_inventory_body_digest": digest_json(semantic_body),
        "source_exp1_inventory_digest": (
            snapshot.source_exp1_inventory_plan.inventory_digest
        ),
        "source_exp1_inventory_body_digest": digest_json(
            source_semantic_body
        ),
        "execution_budget_projection_digest": (
            snapshot.execution_budget_projection.projection_digest
        ),
        "hard_limits_digest": digest_json(dict(snapshot.hard_limits)),
        "ai_api_config_digests": _closure_snapshot_config_digests(
            snapshot.ai_api_configs
        ),
    }


def _closure_snapshot_digest_body(
    *,
    schema_version: str,
    suite_id: str,
    lineage: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "suite_id": suite_id,
        "lineage": dict(lineage),
        "provider_calls_made": 0,
    }


def _validate_results_first_closure_replay_snapshot(
    snapshot: ResultsFirstClosureReplaySnapshot,
) -> None:
    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_formal_plan import FormalExecutionCoverage
    from tokenshare.experiments.paper_models import PaperBudgetResult, digest_json
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstReplaySemanticAuthority,
        SemanticInventoryPlan,
    )

    if type(snapshot) is not ResultsFirstClosureReplaySnapshot:
        raise TypeError("typed closure replay snapshot is required")
    if snapshot.schema_version != _CLOSURE_REPLAY_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("closure replay snapshot schema drift")
    plans = tuple(snapshot.full_dispatch_plans)
    if not plans or any(type(plan) is not PaperExperimentDispatchPlan for plan in plans):
        raise TypeError("closure replay dispatch plan type drift")
    if tuple(plan.experiment_id for plan in plans) != _REPRESENTATIVE_EXPERIMENT_IDS:
        raise ValueError("closure replay requires exact full experiment dispatch order")
    if (
        type(snapshot.catalog_manifest) is not PaperInputCatalogManifest
        or type(snapshot.full_budget) is not PaperBudgetResult
        or type(snapshot.coverage) is not FormalExecutionCoverage
        or type(snapshot.source_exp1_inventory_plan) is not SemanticInventoryPlan
        or type(snapshot.replay_semantic_authority)
        is not ResultsFirstReplaySemanticAuthority
        or type(snapshot.semantic_inventory_plan) is not SemanticInventoryPlan
        or type(snapshot.execution_budget_projection)
        is not PaperExecutionBudgetProjection
    ):
        raise TypeError("closure replay authority object type drift")
    if (
        snapshot.replay_semantic_authority.semantic_inventory_plan
        is not snapshot.semantic_inventory_plan
        or snapshot.replay_semantic_authority.source_snapshot_digest
        != snapshot.coverage.source_snapshot_digest
        or snapshot.replay_semantic_authority.coverage_digest
        != snapshot.coverage.coverage_digest
        or any(
            ref.get("experiment_id") != "exp1_real_ai_feasibility"
            for ref in snapshot.source_exp1_inventory_plan.condition_refs
        )
    ):
        raise ValueError("closure replay trace source authority drift")
    if not isinstance(snapshot.suite_id, str) or not snapshot.suite_id:
        raise ValueError("closure replay suite id is missing")
    if (
        snapshot.provider_calls_made != 0
        or snapshot.coverage.provider_calls_made != 0
        or snapshot.execution_budget_projection.provider_calls_made != 0
        or any(plan.provider_calls_made != 0 for plan in plans)
    ):
        raise ValueError("closure replay snapshot planning called a provider")
    projection = snapshot.execution_budget_projection
    if (
        projection.coverage is not snapshot.coverage
        or projection.source_budget is not snapshot.full_budget
        or projection.source_snapshot is not snapshot.coverage.source_snapshot
        or snapshot.coverage.source_snapshot.budget_digest
        != snapshot.full_budget.budget_digest
        or dict(snapshot.hard_limits) != dict(projection.hard_limits)
    ):
        raise ValueError("closure replay snapshot authority lineage mismatch")
    source_conditions = tuple(
        item.condition for item in snapshot.coverage.source_snapshot.conditions
    )
    dispatch_conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    if len(source_conditions) != len(dispatch_conditions) or any(
        source is not dispatched
        for source, dispatched in zip(
            source_conditions,
            dispatch_conditions,
            strict=True,
        )
    ):
        raise ValueError("closure replay dispatch/snapshot authority drift")
    if any(
        condition.catalog_digest != snapshot.catalog_manifest.catalog_digest
        for condition in dispatch_conditions
    ):
        raise ValueError("closure replay catalog authority drift")
    if (
        not isinstance(snapshot.source_validation_digest, str)
        or not snapshot.source_validation_digest.startswith("sha256:")
    ):
        raise ValueError("closure replay validation digest is invalid")
    _closure_snapshot_reject_secret_fields(snapshot.ai_api_configs)
    lineage = _closure_snapshot_lineage(snapshot)
    expected_digest = digest_json(
        _closure_snapshot_digest_body(
            schema_version=snapshot.schema_version,
            suite_id=snapshot.suite_id,
            lineage=lineage,
        )
    )
    if snapshot.snapshot_digest != expected_digest:
        raise ValueError("closure replay snapshot digest mismatch")
    if not isinstance(snapshot.ai_api_configs, MappingProxyType):
        object.__setattr__(
            snapshot,
            "ai_api_configs",
            MappingProxyType(dict(snapshot.ai_api_configs)),
        )
    if not isinstance(snapshot.hard_limits, MappingProxyType):
        object.__setattr__(
            snapshot,
            "hard_limits",
            MappingProxyType(dict(snapshot.hard_limits)),
        )
    object.__setattr__(snapshot, "full_dispatch_plans", plans)


def _closure_snapshot_ref(
    *, descriptor_digest: str, snapshot_digest: str, pickle_sha256: str
) -> dict[str, object]:
    from tokenshare.experiments.paper_models import digest_json

    body: dict[str, object] = {
        "schema_version": _CLOSURE_REPLAY_REF_SCHEMA_VERSION,
        "descriptor_path": (
            f"{_CLOSURE_REPLAY_DIRECTORY}/{_CLOSURE_REPLAY_DESCRIPTOR_NAME}"
        ),
        "descriptor_digest": descriptor_digest,
        "pickle_path": (
            f"{_CLOSURE_REPLAY_DIRECTORY}/{_CLOSURE_REPLAY_PICKLE_NAME}"
        ),
        "snapshot_digest": snapshot_digest,
        "pickle_sha256": pickle_sha256,
    }
    return {**body, "ref_digest": digest_json(body)}


def persist_results_first_closure_replay_snapshot(
    *,
    authority: ResultsFirstExecutionAuthority,
    output_root: str | Path,
    suite_id: str = "results_first_exp1_exp4_trace",
) -> Mapping[str, object]:
    """原子持久化不含 secret 的受信 typed closure replay handle。"""

    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first execution authority is required")
    root = Path(output_root).resolve(strict=False)
    if root != authority.output_root.resolve(strict=False):
        raise ValueError("closure replay snapshot output root mismatch")
    if (
        authority.provider_calls_made != 0
        or type(authority.bundle) is not AcquisitionPlanBundle
    ):
        raise ValueError("closure replay snapshot requires materialized zero-call authority")
    if tuple(
        plan.experiment_id for plan in authority.full_dispatch_plans
    ) != _REPRESENTATIVE_EXPERIMENT_IDS:
        raise ValueError("closure replay requires exact full experiment dispatch order")
    trace_experiment_ids = tuple(
        experiment_id
        for experiment_id in _results_first_experiment_ids_for_coverage(
            coverage=authority.coverage
        )
        if experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    expected_suite_id = (
        "results_first_exp1_exp4_trace"
        if trace_experiment_ids == _REPRESENTATIVE_TRACE_EXPERIMENT_IDS
        else "results_first_exp1_exp3_trace"
    )
    if suite_id != expected_suite_id:
        raise ValueError("closure replay suite id does not match selected coverage")
    replay_semantic_authority = _RESULTS_FIRST_REPLAY_SEMANTIC_AUTHORITY_BUILDER(
        authority=authority,
        output_root=root,
    )
    semantic_inventory_plan = replay_semantic_authority.semantic_inventory_plan
    seed = object.__new__(ResultsFirstClosureReplaySnapshot)
    seed_values = {
        "full_dispatch_plans": tuple(authority.full_dispatch_plans),
        "catalog_manifest": authority.catalog_manifest,
        "full_budget": authority.full_budget,
        "ai_api_configs": authority.ai_api_configs,
        "coverage": authority.coverage,
        "source_exp1_inventory_plan": authority.bundle.semantic_inventory_plan,
        "replay_semantic_authority": replay_semantic_authority,
        "semantic_inventory_plan": semantic_inventory_plan,
        "execution_budget_projection": authority.execution_budget_projection,
        "hard_limits": authority.execution_budget_projection.hard_limits,
        "source_validation_digest": authority.source_validation_digest,
        "suite_id": suite_id,
        "snapshot_digest": "sha256:pending",
        "provider_calls_made": 0,
        "schema_version": _CLOSURE_REPLAY_SNAPSHOT_SCHEMA_VERSION,
    }
    for field_name, value in seed_values.items():
        object.__setattr__(seed, field_name, value)
    # frozen dataclass 的唯一循环是 snapshot_digest 本身；lineage 不包该字段。
    lineage = _closure_snapshot_lineage(seed)
    snapshot_digest = digest_json(
        _closure_snapshot_digest_body(
            schema_version=seed.schema_version,
            suite_id=seed.suite_id,
            lineage=lineage,
        )
    )
    snapshot = replace(seed, snapshot_digest=snapshot_digest)
    payload = _closure_snapshot_pickle(snapshot)
    pickle_sha256 = f"sha256:{sha256(payload).hexdigest()}"
    descriptor_body: dict[str, object] = {
        "schema_version": _CLOSURE_REPLAY_DESCRIPTOR_SCHEMA_VERSION,
        "snapshot_schema_version": snapshot.schema_version,
        "suite_id": snapshot.suite_id,
        "snapshot_digest": snapshot.snapshot_digest,
        "pickle_path": _CLOSURE_REPLAY_PICKLE_NAME,
        "pickle_sha256": pickle_sha256,
        "provider_calls_made": 0,
        "lineage": lineage,
    }
    descriptor_digest = digest_json(descriptor_body)
    descriptor = {**descriptor_body, "descriptor_digest": descriptor_digest}
    snapshot_root = (root / _CLOSURE_REPLAY_DIRECTORY).resolve(strict=False)
    if root not in snapshot_root.parents:
        raise ValueError("closure replay snapshot path escaped output root")
    persisted_ref = _closure_snapshot_ref(
        descriptor_digest=descriptor_digest,
        snapshot_digest=snapshot.snapshot_digest,
        pickle_sha256=pickle_sha256,
    )
    target_paths = (
        snapshot_root / _CLOSURE_REPLAY_PICKLE_NAME,
        snapshot_root / _CLOSURE_REPLAY_DESCRIPTOR_NAME,
        snapshot_root / _CLOSURE_REPLAY_REF_NAME,
    )
    if any(path.exists() for path in target_paths):
        existing = load_results_first_closure_replay_snapshot(
            output_root=root,
            suite_root=None,
        )
        if (
            existing.snapshot_digest != snapshot.snapshot_digest
            or existing.suite_id != snapshot.suite_id
            or _closure_snapshot_lineage(existing) != lineage
        ):
            raise ValueError("closure replay existing snapshot authority drift")
        existing_ref = _closure_snapshot_read_json(
            snapshot_root / _CLOSURE_REPLAY_REF_NAME,
            label="closure replay existing ref",
        )
        if not isinstance(existing_ref, Mapping):
            raise ValueError("closure replay existing ref is invalid")
        return MappingProxyType(dict(existing_ref))
    _atomic_replace_bytes(snapshot_root / _CLOSURE_REPLAY_PICKLE_NAME, payload)
    _atomic_replace_bytes(
        snapshot_root / _CLOSURE_REPLAY_DESCRIPTOR_NAME,
        _closure_snapshot_json_bytes(descriptor),
    )
    # ref 最后落盘；中断时旧/新任一组合都会在 loader 的交叉摘要处 fail closed。
    _atomic_replace_bytes(
        snapshot_root / _CLOSURE_REPLAY_REF_NAME,
        _closure_snapshot_json_bytes(persisted_ref),
    )
    return MappingProxyType(persisted_ref)


def _closure_snapshot_read_json(path: Path, *, label: str) -> object:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    return value


def load_results_first_closure_replay_snapshot(
    *,
    output_root: str | Path,
    suite_root: str | Path | None = None,
    expected_ref: Mapping[str, object] | None = None,
) -> ResultsFirstClosureReplaySnapshot:
    """先校验 descriptor/handle，再与 scratch suite 三个正式 body 对账。"""

    from tokenshare.experiments.paper_models import digest_json

    ref_keys = {
        "schema_version",
        "descriptor_path",
        "descriptor_digest",
        "pickle_path",
        "snapshot_digest",
        "pickle_sha256",
        "ref_digest",
    }
    root = Path(output_root).resolve(strict=False)
    ref_path = (
        root / _CLOSURE_REPLAY_DIRECTORY / _CLOSURE_REPLAY_REF_NAME
    ).resolve(strict=False)
    if root not in ref_path.parents:
        raise ValueError("closure replay ref escaped output root")
    persisted_ref = _closure_snapshot_read_json(
        ref_path,
        label="closure replay ref",
    )
    if not isinstance(persisted_ref, Mapping) or set(persisted_ref) != ref_keys:
        raise TypeError("closure replay snapshot ref is invalid")
    persisted_ref = dict(persisted_ref)
    declared_ref_digest = persisted_ref.pop("ref_digest")
    actual_ref_digest = digest_json(persisted_ref)
    persisted_ref["ref_digest"] = declared_ref_digest
    if declared_ref_digest != actual_ref_digest:
        raise ValueError("closure replay snapshot ref digest mismatch")
    if expected_ref is not None and (
        not isinstance(expected_ref, Mapping)
        or dict(expected_ref) != persisted_ref
    ):
        raise ValueError("closure replay expected ref mismatch")
    if persisted_ref["schema_version"] != _CLOSURE_REPLAY_REF_SCHEMA_VERSION:
        raise ValueError("closure replay snapshot ref schema drift")
    canonical_descriptor_path = (
        f"{_CLOSURE_REPLAY_DIRECTORY}/{_CLOSURE_REPLAY_DESCRIPTOR_NAME}"
    )
    canonical_pickle_path = (
        f"{_CLOSURE_REPLAY_DIRECTORY}/{_CLOSURE_REPLAY_PICKLE_NAME}"
    )
    if (
        persisted_ref["descriptor_path"] != canonical_descriptor_path
        or persisted_ref["pickle_path"] != canonical_pickle_path
    ):
        raise ValueError("closure replay descriptor path drift")
    descriptor_path = (
        root / _CLOSURE_REPLAY_DIRECTORY / _CLOSURE_REPLAY_DESCRIPTOR_NAME
    ).resolve(strict=False)
    if root not in descriptor_path.parents:
        raise ValueError("closure replay descriptor escaped output root")
    descriptor = _closure_snapshot_read_json(
        descriptor_path,
        label="closure replay descriptor",
    )
    if not isinstance(descriptor, Mapping):
        raise ValueError("closure replay descriptor must be an object")
    descriptor = dict(descriptor)
    declared_descriptor_digest = descriptor.pop("descriptor_digest", None)
    expected_descriptor_keys = {
        "schema_version",
        "snapshot_schema_version",
        "suite_id",
        "snapshot_digest",
        "pickle_path",
        "pickle_sha256",
        "provider_calls_made",
        "lineage",
    }
    if (
        set(descriptor) != expected_descriptor_keys
        or not isinstance(descriptor.get("lineage"), Mapping)
        or descriptor.get("schema_version")
        != _CLOSURE_REPLAY_DESCRIPTOR_SCHEMA_VERSION
        or descriptor.get("snapshot_schema_version")
        != _CLOSURE_REPLAY_SNAPSHOT_SCHEMA_VERSION
        or descriptor.get("pickle_path") != _CLOSURE_REPLAY_PICKLE_NAME
        or descriptor.get("provider_calls_made") != 0
    ):
        raise ValueError("closure replay descriptor schema or path drift")
    actual_descriptor_digest = digest_json(descriptor)
    if (
        declared_descriptor_digest != actual_descriptor_digest
        or persisted_ref["descriptor_digest"] != actual_descriptor_digest
        or persisted_ref["snapshot_digest"] != descriptor.get("snapshot_digest")
        or persisted_ref["pickle_sha256"] != descriptor.get("pickle_sha256")
    ):
        raise ValueError("closure replay descriptor digest mismatch")
    pickle_path = (
        root / _CLOSURE_REPLAY_DIRECTORY / _CLOSURE_REPLAY_PICKLE_NAME
    ).resolve(strict=False)
    if root not in pickle_path.parents:
        raise ValueError("closure replay pickle escaped output root")
    try:
        payload = pickle_path.read_bytes()
    except OSError as exc:
        raise ValueError("closure replay pickle is missing") from exc
    pickle_sha256 = f"sha256:{sha256(payload).hexdigest()}"
    if pickle_sha256 != descriptor["pickle_sha256"]:
        raise ValueError("closure replay pickle digest mismatch")
    try:
        snapshot = pickle.loads(payload)
    except Exception as exc:
        raise ValueError("closure replay pickle is invalid") from exc
    if type(snapshot) is not ResultsFirstClosureReplaySnapshot:
        raise TypeError("closure replay pickle object type drift")
    _validate_results_first_closure_replay_snapshot(snapshot)
    if (
        snapshot.snapshot_digest != descriptor["snapshot_digest"]
        or snapshot.suite_id != descriptor["suite_id"]
        or _closure_snapshot_lineage(snapshot) != descriptor.get("lineage")
    ):
        raise ValueError("closure replay descriptor authority lineage mismatch")
    if suite_root is None:
        return snapshot
    suite = Path(suite_root).resolve(strict=False)
    if not suite.is_dir():
        raise ValueError("closure replay scratch suite root is missing")
    persisted_dispatch = _closure_snapshot_read_json(
        suite / "paper_dispatch_plans.json",
        label="closure replay dispatch body",
    )
    persisted_catalog = _closure_snapshot_read_json(
        suite / "input_catalog_manifest.json",
        label="closure replay catalog body",
    )
    persisted_budget = _closure_snapshot_read_json(
        suite / "run_budget.json",
        label="closure replay budget body",
    )
    expected_dispatch = _closure_snapshot_dispatch_body(
        snapshot.full_dispatch_plans,
        active_experiment_ids=_closure_snapshot_trace_experiment_ids(snapshot),
    )
    expected_catalog = snapshot.catalog_manifest.to_dict()
    expected_budget = snapshot.full_budget.to_dict()
    lineage = descriptor["lineage"]
    if (
        not isinstance(persisted_dispatch, Mapping)
        or set(persisted_dispatch) != {"schema_version", "plans"}
        or persisted_dispatch.get("schema_version")
        != _FORMAL_DISPATCH_SCHEMA_VERSION
        or not isinstance(persisted_dispatch.get("plans"), list)
    ):
        raise ValueError("closure replay dispatch wrapper drift")
    for label, persisted, expected, digest_key in (
        (
            "dispatch",
            persisted_dispatch,
            expected_dispatch,
            "dispatch_body_digest",
        ),
        ("catalog", persisted_catalog, expected_catalog, "catalog_body_digest"),
        ("budget", persisted_budget, expected_budget, "full_budget_body_digest"),
    ):
        if persisted != expected or digest_json(persisted) != lineage[digest_key]:
            raise ValueError(f"closure replay {label} suite body drift")
    return snapshot


def _paid_restore_canonical_digest(
    value: Mapping[str, object],
    *,
    digest_field: str,
    label: str,
) -> tuple[dict[str, object], str]:
    from tokenshare.experiments.paper_models import digest_json

    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    body = dict(value)
    declared = body.pop(digest_field, None)
    if not isinstance(declared, str) or declared != digest_json(body):
        raise ValueError(f"{label} digest mismatch")
    return body, declared


def _paid_restore_require_sha256(value: object, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"paid restore {label} digest is invalid")
    return value


def _paid_restore_read_preflight_file_exact(
    path: str | Path,
) -> tuple[Mapping[str, object], Path, str, int]:
    resolved = Path(path).resolve(strict=False)
    try:
        payload = resolved.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("paid restore expected preflight file is unreadable") from exc
    if not isinstance(value, Mapping):
        raise TypeError("paid restore expected preflight file is invalid")
    return (
        MappingProxyType(dict(value)),
        resolved,
        f"sha256:{sha256(payload).hexdigest()}",
        len(payload),
    )


def _paid_restore_validate_inventory_snapshot_lineage(
    *,
    manifest: Mapping[str, object],
    snapshot: object,
) -> None:
    source_snapshot_digest = getattr(
        getattr(getattr(snapshot, "coverage", None), "source_snapshot", None),
        "snapshot_digest",
        None,
    )
    if manifest.get("source_snapshot_digest") != source_snapshot_digest:
        raise ValueError("paid restore inventory manifest snapshot lineage drift")


def _paid_restore_verify_inventory_files_once(
    *,
    inventory_root: str | Path,
    expected_manifest: Mapping[str, object],
) -> tuple[Mapping[str, object], str, int, int]:
    """只流式封印 records bytes；不解析 40,520 条 JSON 对象。"""

    from tokenshare.experiments.paper_formal_plan import (
        FORMAL_PREPARED_INVENTORY_MANIFEST_NAME,
        FORMAL_PREPARED_INVENTORY_MANIFEST_SCHEMA_VERSION,
        FORMAL_PREPARED_INVENTORY_RECORDS_NAME,
    )

    root = Path(inventory_root).resolve(strict=False)
    manifest_path = (root / FORMAL_PREPARED_INVENTORY_MANIFEST_NAME).resolve(
        strict=False
    )
    records_path = (root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME).resolve(
        strict=False
    )
    if (
        not root.is_dir()
        or root not in manifest_path.parents
        or root not in records_path.parents
    ):
        raise ValueError("paid restore prepared inventory root is invalid")
    try:
        manifest_bytes = manifest_path.read_bytes()
        persisted_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("paid restore prepared inventory manifest is unreadable") from exc
    expected_fields = {
        "schema_version",
        "inventory_schema_version",
        "records_path",
        "records_sha256",
        "records_size_bytes",
        "record_count",
        "unique_inference_request_count",
        "provider_calls_made",
        "source_snapshot_digest",
        "ordered_keyset_digest",
        "inventory_digest",
        "manifest_digest",
    }
    if (
        not isinstance(persisted_manifest, Mapping)
        or set(persisted_manifest) != expected_fields
        or dict(persisted_manifest) != dict(expected_manifest)
        or _closure_snapshot_json_bytes(persisted_manifest) != manifest_bytes
    ):
        raise ValueError("paid restore prepared inventory manifest drift")
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        persisted_manifest,
        digest_field="manifest_digest",
        label="paid restore prepared inventory manifest",
    )
    if (
        persisted_manifest.get("schema_version")
        != FORMAL_PREPARED_INVENTORY_MANIFEST_SCHEMA_VERSION
        or persisted_manifest.get("inventory_schema_version")
        != "tokenshare.paper_formal_prepared_request_inventory.v1"
        or persisted_manifest.get("records_path")
        != FORMAL_PREPARED_INVENTORY_RECORDS_NAME
        or type(persisted_manifest.get("provider_calls_made")) is not int
        or persisted_manifest.get("provider_calls_made") != 0
        or any(
            isinstance(persisted_manifest.get(field), bool)
            or not isinstance(persisted_manifest.get(field), int)
            or int(persisted_manifest[field]) < 0
            for field in (
                "records_size_bytes",
                "record_count",
                "unique_inference_request_count",
            )
        )
    ):
        raise ValueError("paid restore prepared inventory manifest authority drift")
    _paid_restore_require_sha256(manifest_digest, label="inventory manifest")
    for field in (
        "records_sha256",
        "source_snapshot_digest",
        "ordered_keyset_digest",
        "inventory_digest",
    ):
        _paid_restore_require_sha256(
            persisted_manifest.get(field),
            label=f"inventory manifest {field}",
        )
    content_digest = sha256()
    content_size = 0
    record_count = 0
    try:
        with records_path.open("rb") as stream:
            for raw_line in stream:
                content_digest.update(raw_line)
                content_size += len(raw_line)
                record_count += 1
                if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
                    raise ValueError(
                        "paid restore prepared inventory records are not canonical"
                    )
    except OSError as exc:
        raise ValueError("paid restore prepared inventory records are unreadable") from exc
    records_sha256 = f"sha256:{content_digest.hexdigest()}"
    if (
        records_sha256 != persisted_manifest["records_sha256"]
        or content_size != persisted_manifest["records_size_bytes"]
        or record_count != persisted_manifest["record_count"]
    ):
        raise ValueError("paid restore inventory records digest or count drift")
    return (
        MappingProxyType(dict(persisted_manifest)),
        records_sha256,
        content_size,
        record_count,
    )


def _load_paid_restore_acquisition_bundle_exact(bundle_root: str | Path):
    """拒绝 raw parser 的任何宽松归一化，保留原始 JSON artifact 身份。"""

    from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequest
    from tokenshare.experiments.paper_response_bank import (
        ACQUISITION_PLAN_BUNDLE_FILENAME,
        _load_acquisition_plan_bundle_raw,
    )

    root = Path(bundle_root).resolve(strict=False)
    path = (root / ACQUISITION_PLAN_BUNDLE_FILENAME).resolve(strict=False)
    if root not in path.parents:
        raise ValueError("paid restore acquisition bundle path escaped root")
    try:
        raw_bytes = path.read_bytes()
        raw_value = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("paid restore acquisition bundle is unreadable") from exc
    if not isinstance(raw_value, Mapping):
        raise TypeError("paid restore acquisition bundle must be an object")
    if raw_bytes != _closure_snapshot_json_bytes(raw_value):
        raise ValueError("paid restore acquisition bundle JSON is not canonical")
    raw_requests = raw_value.get("acquisition_requests")
    if not isinstance(raw_requests, list):
        raise TypeError("paid restore acquisition requests are invalid")
    strict_prepared = []
    for raw_request in raw_requests:
        if not isinstance(raw_request, Mapping):
            raise TypeError("paid restore acquisition request is invalid")
        prepared = raw_request.get("prepared_request")
        strict_prepared.append(PreparedOutboundRequest.from_dict(prepared))
    bundle = _load_acquisition_plan_bundle_raw(root)
    if (
        bundle.to_dict() != raw_value
        or _closure_snapshot_json_bytes(bundle.to_dict()) != raw_bytes
        or len(strict_prepared) != len(bundle.acquisition_requests)
        or any(
            expected != request.prepared_request
            for expected, request in zip(
                strict_prepared,
                bundle.acquisition_requests,
                strict=True,
            )
        )
    ):
        raise ValueError("paid restore acquisition bundle parser normalized identity")
    return bundle


def _paid_restore_file_inventory(
    value: object,
    *,
    label: str,
    expected_paths: set[Path] | None = None,
) -> None:
    if not isinstance(value, Mapping) or not value:
        raise TypeError(f"{label} must be a non-empty object")
    actual_paths: set[Path] = set()
    for raw_path, raw_ref in value.items():
        if not isinstance(raw_path, str) or not raw_path:
            raise TypeError(f"{label} path is invalid")
        path = Path(raw_path).resolve(strict=False)
        if not path.is_file():
            raise ValueError(f"{label} file is missing")
        if not isinstance(raw_ref, Mapping) or set(raw_ref) != {
            "sha256",
            "size_bytes",
        }:
            raise TypeError(f"{label} ref schema drift")
        size = raw_ref.get("size_bytes")
        digest = raw_ref.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(digest, str):
            raise TypeError(f"{label} ref value is invalid")
        payload = path.read_bytes()
        if len(payload) != size or f"sha256:{sha256(payload).hexdigest()}" != digest:
            raise ValueError(f"{label} immutable file identity drift")
        actual_paths.add(path)
    if expected_paths is not None and actual_paths != expected_paths:
        raise ValueError(f"{label} file set drift")


def _paid_restore_production_source_paths() -> set[Path]:
    experiment_root = Path(__file__).resolve().parent
    paths = {
        (experiment_root / name).resolve(strict=False)
        for name in (
            "paper_budget.py",
            "paper_exp1_trace_reuse.py",
            "paper_formal_plan.py",
            "paper_formal_runner.py",
            "paper_models.py",
            "paper_response_bank.py",
            "run_paper_pipeline.py",
        )
    }
    paths.add(
        (experiment_root.parent / "executors" / "response_bank.py").resolve(
            strict=False
        )
    )
    return paths


def _paid_restore_validate_projection_shape(
    value: object,
    *,
    snapshot: ResultsFirstClosureReplaySnapshot,
    condition_count: int,
    root_run_count: int,
    first_attempt_ai_unit_count: int,
) -> Mapping[str, object]:
    from math import isfinite
    from tokenshare.experiments.paper_models import digest_json

    def same_mapping_shape(candidate: object, reference: object) -> bool:
        if isinstance(reference, Mapping):
            return (
                isinstance(candidate, Mapping)
                and set(candidate) == set(reference)
                and all(
                    same_mapping_shape(candidate[key], reference[key])
                    for key in reference
                )
            )
        return not isinstance(candidate, Mapping)

    if not isinstance(value, Mapping):
        raise TypeError("paid restore experiment projection is invalid")
    expected_keys = set(snapshot.execution_budget_projection.to_dict()) | {
        "time_upper_bound",
        "time_missing_reason",
    }
    if set(value) != expected_keys:
        raise ValueError("paid restore experiment projection fields drift")
    body = dict(value)
    if (
        body.pop("time_upper_bound") is not None
        or body.pop("time_missing_reason")
        != "tokenshare.paper_execution_budget_projection.v1_has_no_time_upper_bound"
    ):
        raise ValueError("paid restore experiment projection time authority drift")
    declared_digest = body.pop("projection_digest")
    integers = (
        "condition_count",
        "root_run_count",
        "first_attempt_ai_unit_count",
        "protocol_replacement_reserve",
        "provider_attempt_upper_bound",
        "token_upper_bound",
        "disk_upper_bound_bytes",
        "provider_calls_made",
    )
    if any(type(body.get(name)) is not int or body[name] < 0 for name in integers):
        raise TypeError("paid restore experiment projection count is invalid")
    cost = body.get("cost_upper_bound")
    hard_limits = body.get("hard_limits")
    disk = body.get("disk_estimate")
    if (
        type(cost) is not float
        or not isfinite(cost)
        or cost < 0
        or not isinstance(hard_limits, Mapping)
        or dict(hard_limits)
        != {
            "max_total_provider_attempts": body["provider_attempt_upper_bound"],
            "max_total_tokens": body["token_upper_bound"],
            "max_cost_estimate": cost,
            "max_disk_bytes": body["disk_upper_bound_bytes"],
        }
        or not isinstance(disk, Mapping)
        or not same_mapping_shape(
            disk,
            snapshot.execution_budget_projection.to_dict()["disk_estimate"],
        )
        or type(disk.get("forecast_bytes")) is not int
        or disk.get("forecast_bytes") != body["disk_upper_bound_bytes"]
    ):
        raise ValueError("paid restore experiment projection limits drift")
    if (
        body.get("schema_version")
        != "tokenshare.paper_execution_budget_projection.v1"
        or body.get("source_budget_digest") != snapshot.full_budget.budget_digest
        or body.get("source_snapshot_digest")
        != snapshot.coverage.source_snapshot.snapshot_digest
        or body.get("selection_kind") != "filtered"
        or body.get("condition_count") != condition_count
        or body.get("root_run_count") != root_run_count
        or body.get("first_attempt_ai_unit_count")
        != first_attempt_ai_unit_count
        or body.get("provider_calls_made") != 0
        or not isinstance(body.get("coverage_digest"), str)
        or not body["coverage_digest"].startswith("sha256:")
        or declared_digest != digest_json(body)
    ):
        raise ValueError("paid restore experiment projection authority drift")
    return value


def _paid_restore_project_paper_execution_budget(*, snapshot, budget, coverage):
    from tokenshare.experiments.paper_budget import project_paper_execution_budget

    return project_paper_execution_budget(
        snapshot=snapshot,
        budget=budget,
        coverage=coverage,
    )


_PAID_RESTORE_BUDGET_PROJECTOR = _paid_restore_project_paper_execution_budget


def _validate_results_first_paid_preflight(
    value: Mapping[str, object],
    *,
    snapshot: ResultsFirstClosureReplaySnapshot,
    selection: str,
    frozen_snapshot_root: str | Path,
) -> str:
    """把外部 cost preflight 精确绑定到已验证的 frozen authority。"""

    body, report_digest = _paid_restore_canonical_digest(
        value,
        digest_field="report_digest",
        label="paid restore preflight",
    )
    if body.get("schema_version") != (
        "tokenshare.paid_representative_projection_preflight.v1"
    ):
        raise ValueError("paid restore preflight schema drift")
    expected_top_keys = {
        "schema_version",
        "source",
        "production_source_files",
        "selection",
        "root_counts_by_experiment",
        "projection",
        "experiment_projections",
        "condition_projections",
        "api_key_status",
        "tripwires",
        "source_unchanged",
    }
    if set(body) != expected_top_keys:
        raise ValueError("paid restore preflight top-level fields drift")
    _closure_snapshot_reject_secret_fields(body)
    source = body.get("source")
    selected = body.get("selection")
    projection = body.get("projection")
    if not all(isinstance(item, Mapping) for item in (source, selected, projection)):
        raise TypeError("paid restore preflight sections are invalid")
    expected_source_values = {
        "snapshot_digest": snapshot.snapshot_digest,
        "full_snapshot_digest": snapshot.coverage.source_snapshot.snapshot_digest,
        "coverage_digest": snapshot.coverage.coverage_digest,
        "full_budget_digest": snapshot.full_budget.budget_digest,
        "provider_config_digests": _closure_snapshot_config_digests(
            snapshot.ai_api_configs
        ),
    }
    expected_selection = {
        "kind": selection,
        "condition_count": snapshot.coverage.condition_count,
        "root_run_count": snapshot.coverage.root_run_count,
    }
    expected_repeat_ids: list[int] = []
    for condition in snapshot.coverage.conditions:
        repeat_id = condition.repeat_id
        if type(repeat_id) is not int or repeat_id < 0:
            raise TypeError("paid restore coverage repeat id is invalid")
        if repeat_id not in expected_repeat_ids:
            expected_repeat_ids.append(repeat_id)
    expected_source_keys = {
        "execution_root",
        "snapshot_digest",
        "full_snapshot_digest",
        "coverage_digest",
        "full_budget_digest",
        "catalog_digest",
        "provider_config_digests",
        "source_validation_digest",
        "source_files",
    }
    if set(source) != expected_source_keys or any(
        source.get(key) != expected
        for key, expected in expected_source_values.items()
    ):
        raise ValueError("paid restore preflight source authority drift")
    source_authority = {
        "catalog_digest": snapshot.catalog_manifest.catalog_digest,
        "source_validation_digest": snapshot.source_validation_digest,
    }
    if any(
        source.get(key) != expected for key, expected in source_authority.items()
    ):
        raise ValueError("paid restore preflight source lineage digest drift")
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    if Path(source["execution_root"]).resolve(strict=False) != snapshot_root:
        raise ValueError("paid restore preflight execution root drift")
    expected_source_files = {
        (snapshot_root / _CLOSURE_REPLAY_DIRECTORY / name).resolve(strict=False)
        for name in (
            _CLOSURE_REPLAY_DESCRIPTOR_NAME,
            _CLOSURE_REPLAY_PICKLE_NAME,
            _CLOSURE_REPLAY_REF_NAME,
        )
    }
    _paid_restore_file_inventory(
        source["source_files"],
        label="paid restore frozen source inventory",
        expected_paths=expected_source_files,
    )
    _paid_restore_file_inventory(
        body["production_source_files"],
        label="paid restore production source inventory",
        expected_paths=_paid_restore_production_source_paths(),
    )
    if (
        set(selected)
        != {"kind", "repeat_ids", "condition_count", "root_run_count"}
        or type(selected.get("condition_count")) is not int
        or type(selected.get("root_run_count")) is not int
        or any(
            selected.get(key) != expected
            for key, expected in expected_selection.items()
        )
        or selected.get("repeat_ids") != expected_repeat_ids
        or any(type(item) is not int for item in selected["repeat_ids"])
    ):
        raise ValueError("paid restore preflight selection drift")
    expected_projection = snapshot.execution_budget_projection.to_dict()
    expected_projection.update(
        {
            "time_upper_bound": None,
            "time_missing_reason": (
                "tokenshare.paper_execution_budget_projection.v1_has_no_time_upper_bound"
            ),
        }
    )
    if _closure_snapshot_json_bytes(dict(projection)) != (
        _closure_snapshot_json_bytes(expected_projection)
    ):
        raise ValueError("paid restore preflight projection or hard limits drift")
    expected_kind = "full" if selection == "full" else "filtered"
    if snapshot.coverage.selection_kind != expected_kind:
        raise ValueError("paid restore coverage selection kind drift")
    api_status = body.get("api_key_status")
    expected_api_status = {
        "schema_version": "tokenshare.provider_key_presence_authority.v1",
        "scope": "provider_zero_preparation",
        "scope_provenance": "fixed_unset_without_secret_resolution",
        "statuses": {
            "DEEPSEEK_API_KEY": "UNSET",
            "SILICONFLOW_API_KEY": "UNSET",
        },
    }
    if (
        not isinstance(api_status, Mapping)
        or _closure_snapshot_json_bytes(dict(api_status))
        != _closure_snapshot_json_bytes(expected_api_status)
    ):
        raise ValueError("paid restore preflight API key status drift")
    tripwires = body.get("tripwires")
    expected_tripwires = {
        "authority_rebuilds",
        "inventory_rebuilds",
        "provider_calls",
        "network_calls",
        "acquisition_calls",
        "adapter_calls",
        "checker_calls",
        "verifier_calls",
    }
    if not isinstance(tripwires, Mapping) or set(tripwires) != expected_tripwires or any(
        type(item) is not int or item != 0 for item in tripwires.values()
    ):
        raise ValueError("paid restore preflight tripwire drift")
    if body.get("source_unchanged") is not True:
        raise ValueError("paid restore preflight source is not immutable")
    roots_by_experiment = {
        experiment_id: tuple(
            root
            for root in snapshot.coverage.roots
            if root.condition.experiment_id == experiment_id
        )
        for experiment_id in _REPRESENTATIVE_EXPERIMENT_IDS
    }
    conditions_by_experiment = {
        experiment_id: tuple(
            condition
            for condition in snapshot.coverage.conditions
            if condition.experiment_id == experiment_id
        )
        for experiment_id in _REPRESENTATIVE_EXPERIMENT_IDS
    }
    expected_root_counts = {
        key: len(roots_by_experiment[key])
        for key in _REPRESENTATIVE_EXPERIMENT_IDS
    }
    root_counts = body.get("root_counts_by_experiment")
    if (
        not isinstance(root_counts, Mapping)
        or set(root_counts) != set(_REPRESENTATIVE_EXPERIMENT_IDS)
        or any(type(item) is not int for item in root_counts.values())
        or dict(root_counts) != expected_root_counts
    ):
        raise ValueError("paid restore preflight experiment root counts drift")
    experiment_rows = body.get("experiment_projections")
    if not isinstance(experiment_rows, list) or len(experiment_rows) != len(
        _REPRESENTATIVE_EXPERIMENT_IDS
    ):
        raise ValueError("paid restore experiment projections drift")
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_execution_coverage,
    )

    for experiment_id, row in zip(
        _REPRESENTATIVE_EXPERIMENT_IDS, experiment_rows, strict=True
    ):
        if not isinstance(row, Mapping) or set(row) != {"experiment_id", "projection"} or row.get("experiment_id") != experiment_id:
            raise ValueError("paid restore experiment projection order drift")
        roots = roots_by_experiment[experiment_id]
        _paid_restore_validate_projection_shape(
            row.get("projection"),
            snapshot=snapshot,
            condition_count=len(conditions_by_experiment[experiment_id]),
            root_run_count=len(roots),
            first_attempt_ai_unit_count=sum(
                len(root.planned_ai_unit_ids) for root in roots
            ),
        )
        selected_condition_ids = tuple(
            condition.condition_id
            for condition in conditions_by_experiment[experiment_id]
        )
        subset_coverage = derive_paper_formal_execution_coverage(
            snapshot=snapshot.coverage.source_snapshot,
            dispatch_plans=snapshot.full_dispatch_plans,
            catalog_manifest=snapshot.catalog_manifest,
            selected_condition_ids=selected_condition_ids,
            root_case_filter={
                condition_id: snapshot.coverage.root_case_filter[condition_id]
                for condition_id in selected_condition_ids
            },
            selection_kind="filtered",
        )
        recomputed_projection = _PAID_RESTORE_BUDGET_PROJECTOR(
            snapshot=snapshot.coverage.source_snapshot,
            budget=snapshot.full_budget,
            coverage=subset_coverage,
        ).to_dict()
        recomputed_projection.update(
            {
                "time_upper_bound": None,
                "time_missing_reason": (
                    "tokenshare.paper_execution_budget_projection.v1_has_no_time_upper_bound"
                ),
            }
        )
        if _closure_snapshot_json_bytes(row["projection"]) != (
            _closure_snapshot_json_bytes(recomputed_projection)
        ):
            raise ValueError(
                "paid restore experiment projection does not match production projector"
            )
    condition_rows = body.get("condition_projections")
    condition_keys = {
        "condition_id", "condition_digest", "experiment_id", "repeat_id",
        "seed", "planned_root_run_count", "planned_first_attempt_ai_unit_count",
        "provider_attempt_upper_bound", "token_upper_bound", "cost_upper_bound",
        "disk_upper_bound_bytes", "time_upper_bound", "projection_missing_reason",
    }
    if not isinstance(condition_rows, list) or len(condition_rows) != len(
        snapshot.coverage.conditions
    ):
        raise ValueError("paid restore condition projections drift")
    for condition, row in zip(
        snapshot.coverage.conditions, condition_rows, strict=True
    ):
        roots = tuple(
            root for root in snapshot.coverage.roots if root.condition is condition
        )
        expected_condition = {
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
            "experiment_id": condition.experiment_id,
            "repeat_id": condition.repeat_id,
            "seed": condition.seed,
            "planned_root_run_count": len(roots),
            "planned_first_attempt_ai_unit_count": sum(
                len(root.planned_ai_unit_ids) for root in roots
            ),
            "provider_attempt_upper_bound": None,
            "token_upper_bound": None,
            "cost_upper_bound": None,
            "disk_upper_bound_bytes": None,
            "time_upper_bound": None,
            "projection_missing_reason": (
                "condition_breakdown_is_coverage_planned_counts_only"
            ),
        }
        if (
            not isinstance(row, Mapping)
            or set(row) != condition_keys
            or any(
                type(row.get(name)) is not int
                for name in (
                    "repeat_id",
                    "seed",
                    "planned_root_run_count",
                    "planned_first_attempt_ai_unit_count",
                )
            )
            or _closure_snapshot_json_bytes(dict(row))
            != _closure_snapshot_json_bytes(expected_condition)
        ):
            raise ValueError("paid restore condition projection authority drift")
    return report_digest


def _paid_restore_binding_paths(root: Path) -> tuple[Path, Path]:
    snapshot_root = (root / _CLOSURE_REPLAY_DIRECTORY).resolve(strict=False)
    if root not in snapshot_root.parents:
        raise ValueError("paid restore binding path escaped snapshot root")
    return (
        snapshot_root / _PAID_RESTORE_BINDING_NAME,
        snapshot_root / _PAID_RESTORE_BINDING_REF_NAME,
    )


def persist_results_first_paid_restore_binding_v2(
    *,
    authority: ResultsFirstExecutionAuthority,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    planning_artifact_root: str | Path,
    prepared_inventory_manifest: Mapping[str, object],
    expected_preflight: str | Path,
) -> PaidRestoreCanonicalDerivationEvidence:
    """为 paid restore 持久化只引用既有 typed artifacts 的 v2 overlay。"""

    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionPlanBundle,
    )

    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first execution authority is required")
    authority.__post_init__()
    if type(authority.bundle) is not AcquisitionPlanBundle:
        raise TypeError("paid restore requires a materialized acquisition bundle")
    root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    if isinstance(expected_preflight, Mapping):
        raise TypeError("paid restore persist requires a persisted preflight path")
    (
        persisted_preflight,
        preflight_path,
        preflight_file_sha256,
        preflight_file_size_bytes,
    ) = _paid_restore_read_preflight_file_exact(expected_preflight)
    snapshot = load_results_first_closure_replay_snapshot(output_root=root)
    replay_semantic_authority = (
        _RESULTS_FIRST_REPLAY_SEMANTIC_AUTHORITY_BUILDER(
            authority=authority,
            output_root=root,
        )
    )
    if (
        snapshot.coverage.source_snapshot.snapshot_digest
        != authority.full_snapshot.snapshot_digest
        or snapshot.coverage.coverage_digest != authority.coverage.coverage_digest
        or snapshot.full_budget.budget_digest != authority.full_budget.budget_digest
        or snapshot.execution_budget_projection.projection_digest
        != authority.execution_budget_projection.projection_digest
        or _closure_snapshot_lineage(snapshot)
        != _closure_snapshot_lineage(
            ResultsFirstClosureReplaySnapshot(
                full_dispatch_plans=tuple(authority.full_dispatch_plans),
                catalog_manifest=authority.catalog_manifest,
                full_budget=authority.full_budget,
                ai_api_configs=authority.ai_api_configs,
                coverage=authority.coverage,
                source_exp1_inventory_plan=(
                    authority.bundle.semantic_inventory_plan
                ),
                replay_semantic_authority=replay_semantic_authority,
                semantic_inventory_plan=(
                    replay_semantic_authority.semantic_inventory_plan
                ),
                execution_budget_projection=authority.execution_budget_projection,
                hard_limits=authority.hard_limits,
                source_validation_digest=authority.source_validation_digest,
                suite_id=snapshot.suite_id,
                snapshot_digest=snapshot.snapshot_digest,
            )
        )
    ):
        raise ValueError("paid restore closure snapshot authority drift")
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        prepared_inventory_manifest,
        digest_field="manifest_digest",
        label="paid restore prepared inventory manifest",
    )
    manifest = {**manifest_body, "manifest_digest": manifest_digest}
    _paid_restore_validate_inventory_snapshot_lineage(
        manifest=manifest,
        snapshot=snapshot,
    )
    (
        persisted_manifest,
        records_sha256,
        records_size_bytes,
        record_count,
    ) = _paid_restore_verify_inventory_files_once(
        inventory_root=inventory_root,
        expected_manifest=manifest,
    )
    inventory_digest = authority.full_prepared_inventory.inventory_digest
    if (
        manifest.get("source_snapshot_digest")
        != authority.full_snapshot.snapshot_digest
        or manifest.get("inventory_digest")
        != inventory_digest
        or type(manifest.get("provider_calls_made")) is not int
        or manifest.get("provider_calls_made") != 0
    ):
        raise ValueError("paid restore prepared inventory lineage drift")
    persisted_bundle = _load_paid_restore_acquisition_bundle_exact(
        authority.bundle_root
    )
    if persisted_bundle.to_dict() != authority.bundle.to_dict():
        raise ValueError("paid restore persisted acquisition bundle drift")
    persisted_bundle_body = persisted_bundle.to_dict()
    source_exp1_inventory_plan = persisted_bundle_body.get(
        "semantic_inventory_plan"
    )
    if not isinstance(source_exp1_inventory_plan, Mapping):
        raise TypeError("paid restore Exp1 source plan is invalid")
    condition_refs = source_exp1_inventory_plan.get("condition_refs")
    if (
        not isinstance(condition_refs, list)
        or not condition_refs
        or any(
            not isinstance(ref, Mapping)
            or ref.get("experiment_id") != "exp1_real_ai_feasibility"
            for ref in condition_refs
        )
    ):
        raise ValueError("paid restore acquisition bundle is not Exp1-only")
    source_exp1_inventory_plan_digest = digest_json(source_exp1_inventory_plan)
    derivation_body: dict[str, object] = {
        "schema_version": "tokenshare.results_first_paid_restore_derivation.v1",
        "source_snapshot_digest": persisted_bundle.source_snapshot_digest,
        "source_prepared_inventory_digest": (
            persisted_bundle.source_prepared_inventory_digest
        ),
        "coverage_digest": persisted_bundle.coverage_digest,
        "representative_plan_digest": persisted_bundle.representative_plan_digest,
        "acquisition_bundle_digest": persisted_bundle.bundle_digest,
        "provider_calls_made": 0,
    }
    acquisition_derivation = {
        **derivation_body,
        "derivation_digest": digest_json(derivation_body),
    }
    selection = "full" if authority.coverage.selection_kind == "full" else "representative"
    report_digest = _validate_results_first_paid_preflight(
        persisted_preflight,
        snapshot=snapshot,
        selection=selection,
        frozen_snapshot_root=root,
    )
    ref_path = root / _CLOSURE_REPLAY_DIRECTORY / _CLOSURE_REPLAY_REF_NAME
    base_ref = _closure_snapshot_read_json(ref_path, label="closure replay ref")
    if not isinstance(base_ref, Mapping):
        raise TypeError("closure replay ref is invalid")
    body: dict[str, object] = {
        "schema_version": _PAID_RESTORE_BINDING_SCHEMA_VERSION,
        "base_snapshot_ref": dict(base_ref),
        "source_validation_digest": authority.source_validation_digest,
        "source_snapshot_digest": authority.full_snapshot.snapshot_digest,
        "coverage_digest": authority.coverage.coverage_digest,
        "full_budget_digest": authority.full_budget.budget_digest,
        "projection_digest": authority.execution_budget_projection.projection_digest,
        "hard_limits_digest": digest_json(dict(authority.hard_limits)),
        "provider_config_digests": _closure_snapshot_config_digests(
            authority.ai_api_configs
        ),
        "selection": selection,
        "prepared_inventory_manifest": manifest,
        "prepared_inventory_manifest_digest": manifest_digest,
        "prepared_inventory_digest": inventory_digest,
        "source_exp1_inventory_plan": dict(source_exp1_inventory_plan),
        "source_exp1_inventory_plan_digest": source_exp1_inventory_plan_digest,
        "acquisition_bundle_digest": authority.bundle.bundle_digest,
        "acquisition_inventory_digest": authority.bundle.inventory_digest,
        "representative_plan_digest": authority.bundle.representative_plan_digest,
        "acquisition_derivation": acquisition_derivation,
        "preflight_report_digest": report_digest,
        "provider_calls_made": 0,
    }
    binding = {**body, "binding_digest": digest_json(body)}
    ref_body: dict[str, object] = {
        "schema_version": _PAID_RESTORE_BINDING_REF_SCHEMA_VERSION,
        "binding_path": _PAID_RESTORE_BINDING_NAME,
        "binding_digest": binding["binding_digest"],
        "base_ref_digest": base_ref.get("ref_digest"),
        "snapshot_digest": snapshot.snapshot_digest,
        "prepared_inventory_manifest_digest": manifest_digest,
        "source_exp1_inventory_plan_digest": source_exp1_inventory_plan_digest,
        "acquisition_bundle_digest": authority.bundle.bundle_digest,
        "acquisition_derivation_digest": acquisition_derivation[
            "derivation_digest"
        ],
        "preflight_report_digest": report_digest,
    }
    persisted_ref = {**ref_body, "ref_digest": digest_json(ref_body)}
    provider_budget = _load_results_first_provider_budget_exact(
        planning_artifact_root=planning_root,
        expected_authority_digest=getattr(
            authority.provider_budget_authority,
            "authority_digest",
            None,
        ),
    )
    if provider_budget != authority.provider_budget_authority:
        raise ValueError("paid restore provider budget authority drift")
    evidence = _mint_paid_restore_canonical_derivation_evidence(
        authority=authority,
        snapshot=snapshot,
        inventory=authority.full_prepared_inventory,
        frozen_snapshot_root=root,
        frozen_prepared_inventory_root=inventory_root,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=planning_root,
        output_root=authority.output_root,
        selection=selection,
        resume=authority.resume,
        prepared_inventory_manifest=persisted_manifest,
        prepared_inventory_manifest_digest=manifest_digest,
        inventory_records_sha256=records_sha256,
        inventory_records_size_bytes=records_size_bytes,
        inventory_record_count=record_count,
        inventory_digest=inventory_digest,
        source_exp1_inventory_plan=source_exp1_inventory_plan,
        source_exp1_inventory_plan_digest=source_exp1_inventory_plan_digest,
        acquisition_bundle_digest=authority.bundle.bundle_digest,
        representative_plan_digest=authority.bundle.representative_plan_digest,
        expected_preflight_path=preflight_path,
        expected_preflight_report_digest=report_digest,
        expected_preflight_file_sha256=preflight_file_sha256,
        expected_preflight_file_size_bytes=preflight_file_size_bytes,
        provider_budget_authority_digest=provider_budget.authority_digest,
        binding=binding,
        binding_ref=persisted_ref,
        provider_calls_made=0,
    )
    binding_path, binding_ref_path = _paid_restore_binding_paths(root)
    binding_exists = binding_path.exists()
    ref_exists = binding_ref_path.exists()
    if binding_exists is not ref_exists:
        raise ValueError("paid restore binding publication is incomplete")
    if binding_exists:
        existing_binding, existing_ref = _load_results_first_paid_restore_binding_v2(
            root
        )
        if existing_binding != binding or existing_ref != persisted_ref:
            raise ValueError("paid restore existing binding authority drift")
    else:
        _atomic_write_once_bytes(
            binding_path,
            _closure_snapshot_json_bytes(binding),
        )
        # ref 是唯一 commit marker；若此步失败，binding-only 必须保留为 incomplete。
        _atomic_write_once_bytes(
            binding_ref_path,
            _closure_snapshot_json_bytes(persisted_ref),
        )
    _register_paid_restore_warm_evidence(evidence)
    return evidence


def _load_results_first_paid_restore_binding_v2(
    root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    from tokenshare.experiments.paper_models import digest_json

    binding_path, ref_path = _paid_restore_binding_paths(root)
    binding_exists = binding_path.exists()
    ref_exists = ref_path.exists()
    if binding_exists is not ref_exists:
        raise ValueError("paid restore binding publication is incomplete")
    if not binding_exists:
        raise ValueError(
            "legacy v1 closure snapshot is replay-only and cannot authorize paid restore"
        )
    if not binding_path.is_file() or not ref_path.is_file():
        raise ValueError("paid restore binding publication is invalid")
    persisted_ref = _closure_snapshot_read_json(ref_path, label="paid restore ref")
    ref_body, ref_digest = _paid_restore_canonical_digest(
        persisted_ref, digest_field="ref_digest", label="paid restore ref"
    )
    expected_ref_keys = {
        "schema_version", "binding_path", "binding_digest", "base_ref_digest",
        "snapshot_digest", "prepared_inventory_manifest_digest",
        "source_exp1_inventory_plan_digest", "acquisition_bundle_digest",
        "acquisition_derivation_digest", "preflight_report_digest",
    }
    if set(ref_body) != expected_ref_keys or ref_body.get("schema_version") != (
        _PAID_RESTORE_BINDING_REF_SCHEMA_VERSION
    ) or ref_body.get("binding_path") != _PAID_RESTORE_BINDING_NAME:
        raise ValueError("paid restore ref schema or path drift")
    binding_value = _closure_snapshot_read_json(
        binding_path, label="paid restore binding"
    )
    binding_body, binding_digest = _paid_restore_canonical_digest(
        binding_value, digest_field="binding_digest", label="paid restore binding"
    )
    expected_binding_keys = {
        "schema_version", "base_snapshot_ref", "source_validation_digest",
        "source_snapshot_digest", "coverage_digest", "full_budget_digest",
        "projection_digest", "hard_limits_digest", "provider_config_digests",
        "selection", "prepared_inventory_manifest",
        "prepared_inventory_manifest_digest", "prepared_inventory_digest",
        "source_exp1_inventory_plan", "source_exp1_inventory_plan_digest",
        "acquisition_bundle_digest", "acquisition_inventory_digest",
        "representative_plan_digest", "acquisition_derivation",
        "preflight_report_digest",
        "provider_calls_made",
    }
    source_plan = binding_body.get("source_exp1_inventory_plan")
    derivation = binding_body.get("acquisition_derivation")
    source_plan_digest = binding_body.get("source_exp1_inventory_plan_digest")
    derivation_digest = (
        derivation.get("derivation_digest")
        if isinstance(derivation, Mapping)
        else None
    )
    derivation_body = (
        {key: value for key, value in derivation.items() if key != "derivation_digest"}
        if isinstance(derivation, Mapping)
        else None
    )
    source_condition_refs = (
        source_plan.get("condition_refs")
        if isinstance(source_plan, Mapping)
        else None
    )
    expected_derivation_keys = {
        "schema_version",
        "source_snapshot_digest",
        "source_prepared_inventory_digest",
        "coverage_digest",
        "representative_plan_digest",
        "acquisition_bundle_digest",
        "provider_calls_made",
    }
    if (
        set(binding_body) != expected_binding_keys
        or binding_body.get("schema_version") != _PAID_RESTORE_BINDING_SCHEMA_VERSION
        or type(binding_body.get("provider_calls_made")) is not int
        or binding_body.get("provider_calls_made") != 0
        or ref_body.get("binding_digest") != binding_digest
        or ref_body.get("prepared_inventory_manifest_digest")
        != binding_body.get("prepared_inventory_manifest_digest")
        or not isinstance(source_plan, Mapping)
        or source_plan_digest != digest_json(source_plan)
        or ref_body.get("source_exp1_inventory_plan_digest")
        != source_plan_digest
        or not isinstance(source_condition_refs, list)
        or not source_condition_refs
        or any(
            not isinstance(item, Mapping)
            or item.get("experiment_id") != "exp1_real_ai_feasibility"
            for item in source_condition_refs
        )
        or source_plan.get("inventory_digest")
        != binding_body.get("acquisition_inventory_digest")
        or not isinstance(derivation_body, Mapping)
        or set(derivation_body) != expected_derivation_keys
        or derivation_body.get("schema_version")
        != "tokenshare.results_first_paid_restore_derivation.v1"
        or derivation_digest != digest_json(derivation_body)
        or ref_body.get("acquisition_derivation_digest") != derivation_digest
        or derivation.get("provider_calls_made") != 0
        or ref_body.get("acquisition_bundle_digest")
        != binding_body.get("acquisition_bundle_digest")
        or derivation.get("acquisition_bundle_digest")
        != binding_body.get("acquisition_bundle_digest")
        or derivation.get("representative_plan_digest")
        != binding_body.get("representative_plan_digest")
        or derivation.get("source_snapshot_digest")
        != binding_body.get("source_snapshot_digest")
        or derivation.get("source_prepared_inventory_digest")
        != binding_body.get("prepared_inventory_digest")
        or derivation.get("coverage_digest")
        != binding_body.get("coverage_digest")
        or ref_body.get("preflight_report_digest")
        != binding_body.get("preflight_report_digest")
    ):
        raise ValueError("paid restore binding/ref authority drift")
    return (
        {**binding_body, "binding_digest": binding_digest},
        {**ref_body, "ref_digest": ref_digest},
    )


def _paid_source_rebind_paths(root: Path) -> tuple[Path, Path, Path]:
    resolved = root.resolve(strict=False)
    return (
        resolved / _PAID_SOURCE_REBIND_PREFLIGHT_NAME,
        resolved / _PAID_SOURCE_REBIND_BINDING_NAME,
        resolved / _PAID_SOURCE_REBIND_REF_NAME,
    )


def _paid_source_rebind_read_canonical(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, object], str, int]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} is invalid")
    body = dict(value)
    if _closure_snapshot_json_bytes(body) != payload:
        raise ValueError(f"{label} bytes are not canonical")
    return body, f"sha256:{sha256(payload).hexdigest()}", len(payload)


def _load_results_first_paid_source_rebind_v1(
    root: str | Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """只接受 preflight+binding+last-commit ref 完整三件套。"""

    from tokenshare.experiments.paper_models import digest_json

    resolved = Path(root).resolve(strict=False)
    preflight_path, binding_path, ref_path = _paid_source_rebind_paths(resolved)
    states = tuple(path.is_file() for path in (preflight_path, binding_path, ref_path))
    if not any(states):
        raise ValueError("paid source rebind publication is missing")
    if states != (True, True, True):
        raise ValueError("paid source rebind publication is incomplete")
    preflight, preflight_file_sha256, preflight_file_size = (
        _paid_source_rebind_read_canonical(
            preflight_path,
            label="paid source rebind preflight",
        )
    )
    binding, binding_file_sha256, binding_file_size = (
        _paid_source_rebind_read_canonical(
            binding_path,
            label="paid source rebind binding",
        )
    )
    ref, _ref_file_sha256, _ref_file_size = _paid_source_rebind_read_canonical(
        ref_path,
        label="paid source rebind ref",
    )
    preflight_body, preflight_report_digest = _paid_restore_canonical_digest(
        preflight,
        digest_field="report_digest",
        label="paid source rebind preflight",
    )
    binding_body, binding_digest = _paid_restore_canonical_digest(
        binding,
        digest_field="binding_digest",
        label="paid source rebind binding",
    )
    ref_body, ref_digest = _paid_restore_canonical_digest(
        ref,
        digest_field="ref_digest",
        label="paid source rebind ref",
    )
    binding_fields = {
        "schema_version",
        "base_binding_digest",
        "base_binding_ref_digest",
        "base_preflight_report_digest",
        "base_preflight_file_sha256",
        "base_preflight_file_size_bytes",
        "current_preflight_path",
        "current_preflight_report_digest",
        "current_preflight_file_sha256",
        "current_preflight_file_size_bytes",
        "production_source_files",
        "production_source_inventory_digest",
        "snapshot_digest",
        "source_validation_digest",
        "source_snapshot_digest",
        "coverage_digest",
        "full_budget_digest",
        "projection_digest",
        "hard_limits_digest",
        "provider_config_digests",
        "selection",
        "condition_count",
        "root_run_count",
        "prepared_inventory_manifest_digest",
        "prepared_inventory_digest",
        "inventory_records_sha256",
        "inventory_records_size_bytes",
        "inventory_record_count",
        "acquisition_bundle_digest",
        "acquisition_inventory_digest",
        "representative_plan_digest",
        "source_exp1_inventory_plan_digest",
        "provider_budget_authority_digest",
        "provider_calls_made",
    }
    ref_fields = {
        "schema_version",
        "binding_path",
        "binding_digest",
        "binding_file_sha256",
        "binding_file_size_bytes",
        "preflight_path",
        "current_preflight_report_digest",
        "current_preflight_file_sha256",
        "current_preflight_file_size_bytes",
        "base_binding_digest",
        "base_binding_ref_digest",
        "provider_calls_made",
    }
    production_sources = binding_body.get("production_source_files")
    _paid_restore_file_inventory(
        production_sources,
        label="paid source rebind production source inventory",
        expected_paths=_paid_restore_production_source_paths(),
    )
    if (
        set(binding_body) != binding_fields
        or binding_body.get("schema_version")
        != _PAID_SOURCE_REBIND_SCHEMA_VERSION
        or set(ref_body) != ref_fields
        or ref_body.get("schema_version")
        != _PAID_SOURCE_REBIND_REF_SCHEMA_VERSION
        or type(binding_body.get("provider_calls_made")) is not int
        or binding_body.get("provider_calls_made") != 0
        or type(ref_body.get("provider_calls_made")) is not int
        or ref_body.get("provider_calls_made") != 0
        or binding_body.get("production_source_inventory_digest")
        != digest_json(production_sources)
        or binding_body.get("current_preflight_path")
        != _PAID_SOURCE_REBIND_PREFLIGHT_NAME
        or binding_body.get("current_preflight_report_digest")
        != preflight_report_digest
        or binding_body.get("current_preflight_file_sha256")
        != preflight_file_sha256
        or binding_body.get("current_preflight_file_size_bytes")
        != preflight_file_size
        or preflight_body.get("production_source_files") != production_sources
        or ref_body.get("binding_path") != _PAID_SOURCE_REBIND_BINDING_NAME
        or ref_body.get("binding_digest") != binding_digest
        or ref_body.get("binding_file_sha256") != binding_file_sha256
        or ref_body.get("binding_file_size_bytes") != binding_file_size
        or ref_body.get("preflight_path") != _PAID_SOURCE_REBIND_PREFLIGHT_NAME
        or ref_body.get("current_preflight_report_digest")
        != preflight_report_digest
        or ref_body.get("current_preflight_file_sha256")
        != preflight_file_sha256
        or ref_body.get("current_preflight_file_size_bytes")
        != preflight_file_size
        or ref_body.get("base_binding_digest")
        != binding_body.get("base_binding_digest")
        or ref_body.get("base_binding_ref_digest")
        != binding_body.get("base_binding_ref_digest")
    ):
        raise ValueError("paid source rebind binding/ref authority drift")
    return (
        {**preflight_body, "report_digest": preflight_report_digest},
        {**binding_body, "binding_digest": binding_digest},
        {**ref_body, "ref_digest": ref_digest},
    )


def _validate_paid_source_rebind_base_and_current(
    *,
    source_rebind_root: str | Path,
    binding: Mapping[str, object],
    binding_ref: Mapping[str, object],
    old_preflight_path: str | Path,
    snapshot: ResultsFirstClosureReplaySnapshot,
    selection: str,
    frozen_snapshot_root: Path,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    """绑定历史 preflight bytes，并只对 fresh preflight 执行 live source gate。"""

    old_preflight, old_path, old_file_sha256, old_file_size = (
        _paid_restore_read_preflight_file_exact(old_preflight_path)
    )
    old_body, old_report_digest = _paid_restore_canonical_digest(
        old_preflight,
        digest_field="report_digest",
        label="paid source rebind base preflight",
    )
    current_preflight, rebind, rebind_ref = (
        _load_results_first_paid_source_rebind_v1(source_rebind_root)
    )
    current_report_digest = _validate_results_first_paid_preflight(
        current_preflight,
        snapshot=snapshot,
        selection=selection,
        frozen_snapshot_root=frozen_snapshot_root,
    )
    if (
        old_path != Path(old_preflight_path).resolve(strict=False)
        or binding.get("preflight_report_digest") != old_report_digest
        or binding_ref.get("preflight_report_digest") != old_report_digest
        or rebind.get("base_binding_digest") != binding.get("binding_digest")
        or rebind.get("base_binding_ref_digest") != binding_ref.get("ref_digest")
        or rebind.get("base_preflight_report_digest") != old_report_digest
        or rebind.get("base_preflight_file_sha256") != old_file_sha256
        or rebind.get("base_preflight_file_size_bytes") != old_file_size
        or rebind.get("current_preflight_report_digest")
        != current_report_digest
        or rebind_ref.get("current_preflight_report_digest")
        != current_report_digest
    ):
        raise ValueError("paid source rebind base/current preflight drift")
    # 历史与当前 authority 只允许 production source inventory 变化。
    old_common = dict(old_body)
    current_common = dict(current_preflight)
    current_common.pop("report_digest", None)
    old_sources = old_common.pop("production_source_files", None)
    current_sources = current_common.pop("production_source_files", None)
    if old_common != current_common or old_sources == current_sources:
        raise ValueError("paid source rebind preflight body drift")
    return current_preflight, rebind, rebind_ref


def _validate_paid_source_rebind_authorities(
    *,
    rebind: Mapping[str, object],
    binding: Mapping[str, object],
    snapshot: ResultsFirstClosureReplaySnapshot,
    persisted_manifest: Mapping[str, object],
    records_sha256: str,
    records_size_bytes: int,
    record_count: int,
    corrected_bundle: object,
    provider_budget: object,
) -> None:
    from tokenshare.experiments.paper_models import digest_json

    source_plan = corrected_bundle.to_dict().get("semantic_inventory_plan")
    if (
        rebind.get("base_binding_digest") != binding.get("binding_digest")
        or rebind.get("snapshot_digest") != snapshot.snapshot_digest
        or rebind.get("source_validation_digest")
        != snapshot.source_validation_digest
        or rebind.get("source_snapshot_digest")
        != snapshot.coverage.source_snapshot.snapshot_digest
        or rebind.get("coverage_digest") != snapshot.coverage.coverage_digest
        or rebind.get("full_budget_digest") != snapshot.full_budget.budget_digest
        or rebind.get("projection_digest")
        != snapshot.execution_budget_projection.projection_digest
        or rebind.get("hard_limits_digest") != digest_json(dict(snapshot.hard_limits))
        or rebind.get("provider_config_digests")
        != _closure_snapshot_config_digests(snapshot.ai_api_configs)
        or rebind.get("selection") != binding.get("selection")
        or rebind.get("condition_count") != snapshot.coverage.condition_count
        or rebind.get("root_run_count") != snapshot.coverage.root_run_count
        or rebind.get("prepared_inventory_manifest_digest")
        != binding.get("prepared_inventory_manifest_digest")
        or rebind.get("prepared_inventory_digest")
        != persisted_manifest.get("inventory_digest")
        or rebind.get("inventory_records_sha256") != records_sha256
        or rebind.get("inventory_records_size_bytes") != records_size_bytes
        or rebind.get("inventory_record_count") != record_count
        or rebind.get("acquisition_bundle_digest")
        != corrected_bundle.bundle_digest
        or rebind.get("acquisition_inventory_digest")
        != corrected_bundle.inventory_digest
        or rebind.get("representative_plan_digest")
        != corrected_bundle.representative_plan_digest
        or not isinstance(source_plan, Mapping)
        or rebind.get("source_exp1_inventory_plan_digest")
        != digest_json(source_plan)
        or rebind.get("source_exp1_inventory_plan_digest")
        != binding.get("source_exp1_inventory_plan_digest")
        or rebind.get("provider_budget_authority_digest")
        != getattr(provider_budget, "authority_digest", None)
        or rebind.get("provider_calls_made") != 0
    ):
        raise ValueError("paid source rebind authority drift")


def persist_results_first_paid_source_rebind_v1(
    *,
    source_rebind_root: str | Path,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    old_preflight: str | Path,
    fresh_preflight: Mapping[str, object],
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    selection: str,
    expected_provider_budget_digest: str | None,
) -> Mapping[str, object]:
    """只读重验 base v2 后，以 ref-last 顺序签发 current-source rebind。"""

    from tokenshare.experiments.paper_budget import derive_results_first_provider_budget
    from tokenshare.experiments.paper_models import digest_json

    if selection not in {"representative", "full"}:
        raise ValueError("paid source rebind selection is invalid")
    if not isinstance(fresh_preflight, Mapping):
        raise TypeError("paid source rebind fresh preflight is invalid")
    root = Path(source_rebind_root).resolve(strict=False)
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    old_preflight_path = Path(old_preflight).resolve(strict=False)
    input_paths = (
        snapshot_root,
        inventory_root,
        bundle_root,
        planning_root,
        old_preflight_path,
    )
    if root.exists():
        raise ValueError("paid source rebind output root must be fresh")
    if any(
        root == source or root in source.parents or source in root.parents
        for source in input_paths
    ):
        raise ValueError("paid source rebind input/output topology drift")
    provider_budget_digest = _paid_restore_require_sha256(
        expected_provider_budget_digest,
        label="provider budget",
    )
    binding, binding_ref = _load_results_first_paid_restore_binding_v2(
        snapshot_root
    )
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_root,
        expected_ref=binding["base_snapshot_ref"],
    )
    old_value, _old_path, old_file_sha256, old_file_size = (
        _paid_restore_read_preflight_file_exact(old_preflight_path)
    )
    old_body, old_report_digest = _paid_restore_canonical_digest(
        old_value,
        digest_field="report_digest",
        label="paid source rebind base preflight",
    )
    if (
        old_body.get("schema_version")
        != "tokenshare.paid_representative_projection_preflight.v1"
        or binding.get("preflight_report_digest") != old_report_digest
        or binding_ref.get("preflight_report_digest") != old_report_digest
    ):
        raise ValueError("paid source rebind historical preflight drift")
    current_body, current_report_digest = _paid_restore_canonical_digest(
        fresh_preflight,
        digest_field="report_digest",
        label="paid source rebind current preflight",
    )
    validated_current_digest = _validate_results_first_paid_preflight(
        fresh_preflight,
        snapshot=snapshot,
        selection=selection,
        frozen_snapshot_root=snapshot_root,
    )
    old_common = dict(old_body)
    current_common = dict(current_body)
    old_sources = old_common.pop("production_source_files", None)
    current_sources = current_common.pop("production_source_files", None)
    _paid_restore_file_inventory(
        current_sources,
        label="paid source rebind current production source inventory",
        expected_paths=_paid_restore_production_source_paths(),
    )
    if (
        validated_current_digest != current_report_digest
        or old_common != current_common
        or old_sources == current_sources
    ):
        raise ValueError("paid source rebind preflight body drift")

    manifest = binding.get("prepared_inventory_manifest")
    if not isinstance(manifest, Mapping):
        raise TypeError("paid source rebind inventory manifest is invalid")
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        manifest,
        digest_field="manifest_digest",
        label="paid source rebind inventory manifest",
    )
    _paid_restore_validate_inventory_snapshot_lineage(
        manifest=manifest,
        snapshot=snapshot,
    )
    persisted_manifest, records_sha256, records_size, record_count = (
        _paid_restore_verify_inventory_files_once(
            inventory_root=inventory_root,
            expected_manifest={**manifest_body, "manifest_digest": manifest_digest},
        )
    )
    corrected_bundle = _load_paid_restore_acquisition_bundle_exact(bundle_root)
    corrected_body = corrected_bundle.to_dict()
    source_plan = corrected_body.get("semantic_inventory_plan")
    source_plan_digest = digest_json(source_plan) if isinstance(source_plan, Mapping) else None
    if (
        manifest_digest != binding.get("prepared_inventory_manifest_digest")
        or persisted_manifest.get("inventory_digest")
        != binding.get("prepared_inventory_digest")
        or corrected_bundle.bundle_digest
        != binding.get("acquisition_bundle_digest")
        or corrected_bundle.inventory_digest
        != binding.get("acquisition_inventory_digest")
        or corrected_bundle.representative_plan_digest
        != binding.get("representative_plan_digest")
        or source_plan != binding.get("source_exp1_inventory_plan")
        or source_plan_digest != binding.get("source_exp1_inventory_plan_digest")
        or binding.get("selection") != selection
        or binding.get("source_validation_digest")
        != snapshot.source_validation_digest
        or binding.get("source_snapshot_digest")
        != snapshot.coverage.source_snapshot.snapshot_digest
        or binding.get("coverage_digest") != snapshot.coverage.coverage_digest
        or binding.get("full_budget_digest") != snapshot.full_budget.budget_digest
        or binding.get("projection_digest")
        != snapshot.execution_budget_projection.projection_digest
        or binding.get("hard_limits_digest") != digest_json(dict(snapshot.hard_limits))
        or binding.get("provider_config_digests")
        != _closure_snapshot_config_digests(snapshot.ai_api_configs)
    ):
        raise ValueError("paid source rebind frozen authority drift")
    expected_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=corrected_bundle.full_budget,
        exp5_execution_projection=(
            _selection_exact_exp5_execution_projection_from_snapshot(snapshot)
        ),
    )
    provider_budget = _load_results_first_provider_budget_exact(
        planning_artifact_root=planning_root,
        expected_authority_digest=provider_budget_digest,
    )
    if provider_budget != expected_budget:
        raise ValueError("paid source rebind provider budget drift")

    # TOCTOU gate：在任何 mkdir/write 前重新读取全部 current source bytes。
    if (
        _validate_results_first_paid_preflight(
            fresh_preflight,
            snapshot=snapshot,
            selection=selection,
            frozen_snapshot_root=snapshot_root,
        )
        != current_report_digest
    ):
        raise ValueError("paid source rebind current preflight TOCTOU drift")
    current_preflight_bytes = _closure_snapshot_json_bytes(dict(fresh_preflight))
    current_file_sha256 = f"sha256:{sha256(current_preflight_bytes).hexdigest()}"
    production_source_digest = digest_json(current_sources)
    body: dict[str, object] = {
        "schema_version": _PAID_SOURCE_REBIND_SCHEMA_VERSION,
        "base_binding_digest": binding["binding_digest"],
        "base_binding_ref_digest": binding_ref["ref_digest"],
        "base_preflight_report_digest": old_report_digest,
        "base_preflight_file_sha256": old_file_sha256,
        "base_preflight_file_size_bytes": old_file_size,
        "current_preflight_path": _PAID_SOURCE_REBIND_PREFLIGHT_NAME,
        "current_preflight_report_digest": current_report_digest,
        "current_preflight_file_sha256": current_file_sha256,
        "current_preflight_file_size_bytes": len(current_preflight_bytes),
        "production_source_files": dict(current_sources),
        "production_source_inventory_digest": production_source_digest,
        "snapshot_digest": snapshot.snapshot_digest,
        "source_validation_digest": snapshot.source_validation_digest,
        "source_snapshot_digest": snapshot.coverage.source_snapshot.snapshot_digest,
        "coverage_digest": snapshot.coverage.coverage_digest,
        "full_budget_digest": snapshot.full_budget.budget_digest,
        "projection_digest": snapshot.execution_budget_projection.projection_digest,
        "hard_limits_digest": digest_json(dict(snapshot.hard_limits)),
        "provider_config_digests": _closure_snapshot_config_digests(
            snapshot.ai_api_configs
        ),
        "selection": selection,
        "condition_count": snapshot.coverage.condition_count,
        "root_run_count": snapshot.coverage.root_run_count,
        "prepared_inventory_manifest_digest": manifest_digest,
        "prepared_inventory_digest": persisted_manifest["inventory_digest"],
        "inventory_records_sha256": records_sha256,
        "inventory_records_size_bytes": records_size,
        "inventory_record_count": record_count,
        "acquisition_bundle_digest": corrected_bundle.bundle_digest,
        "acquisition_inventory_digest": corrected_bundle.inventory_digest,
        "representative_plan_digest": corrected_bundle.representative_plan_digest,
        "source_exp1_inventory_plan_digest": source_plan_digest,
        "provider_budget_authority_digest": provider_budget.authority_digest,
        "provider_calls_made": 0,
    }
    persisted_binding = {**body, "binding_digest": digest_json(body)}
    binding_bytes = _closure_snapshot_json_bytes(persisted_binding)
    ref_body: dict[str, object] = {
        "schema_version": _PAID_SOURCE_REBIND_REF_SCHEMA_VERSION,
        "binding_path": _PAID_SOURCE_REBIND_BINDING_NAME,
        "binding_digest": persisted_binding["binding_digest"],
        "binding_file_sha256": f"sha256:{sha256(binding_bytes).hexdigest()}",
        "binding_file_size_bytes": len(binding_bytes),
        "preflight_path": _PAID_SOURCE_REBIND_PREFLIGHT_NAME,
        "current_preflight_report_digest": current_report_digest,
        "current_preflight_file_sha256": current_file_sha256,
        "current_preflight_file_size_bytes": len(current_preflight_bytes),
        "base_binding_digest": binding["binding_digest"],
        "base_binding_ref_digest": binding_ref["ref_digest"],
        "provider_calls_made": 0,
    }
    persisted_ref = {**ref_body, "ref_digest": digest_json(ref_body)}
    root.mkdir(parents=False, exist_ok=False)
    preflight_path, binding_path, ref_path = _paid_source_rebind_paths(root)
    _atomic_write_once_bytes(preflight_path, current_preflight_bytes)
    _atomic_write_once_bytes(binding_path, binding_bytes)
    # ref 是唯一 commit marker；两种 partial 状态均不能被 loader 接受。
    _atomic_write_once_bytes(ref_path, _closure_snapshot_json_bytes(persisted_ref))
    return MappingProxyType(persisted_ref)


def _load_results_first_provider_budget_exact(
    *,
    planning_artifact_root: str | Path,
    expected_authority_digest: str | None = None,
) -> object:
    from tokenshare.experiments.paper_budget import ResultsFirstProviderBudgetAuthority

    path = (
        Path(planning_artifact_root).resolve(strict=False)
        / "results_first_provider_budget.v1.json"
    )
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("results-first provider budget authority is missing") from exc
    expected_fields = {
        "schema_version",
        "exp1",
        "exp2_4",
        "exp5",
        "global_new_paid",
        "provider_calls_made",
        "authority_digest",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_fields:
        raise ValueError("results-first provider budget authority fields drift")
    if raw.get("provider_calls_made") != 0:
        raise ValueError("results-first provider budget recorded provider calls")
    authority = ResultsFirstProviderBudgetAuthority(
        schema_version=str(raw["schema_version"]),
        exp1=raw["exp1"],
        exp2_4=raw["exp2_4"],
        exp5=raw["exp5"],
        global_new_paid=raw["global_new_paid"],
        authority_digest=str(raw["authority_digest"]),
    )
    if (
        _closure_snapshot_json_bytes(authority.to_dict()) != raw_bytes
        or (
            expected_authority_digest is not None
            and authority.authority_digest != expected_authority_digest
        )
    ):
        raise ValueError("results-first provider budget authority digest drift")
    return authority


def _load_results_first_paid_execution_authority_v2_warm(
    *,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    expected_preflight: str | Path | Mapping[str, object],
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    output_root: str | Path,
    selection: str,
    resume: bool,
    expected_corrected_bundle_digest: str | None,
    expected_provider_budget_digest: str | None,
    evidence: PaidRestoreCanonicalDerivationEvidence,
) -> ResultsFirstExecutionAuthority:
    from tokenshare.experiments.paper_models import digest_json

    """校验所有持久化 bytes 后复用本次 persist 已封印的 typed objects。"""

    from tokenshare.experiments.paper_budget import derive_results_first_provider_budget
    from tokenshare.experiments.paper_models import digest_json

    if type(evidence) is not PaidRestoreCanonicalDerivationEvidence:
        raise TypeError("exact paid restore warm derivation evidence is required")
    if (
        getattr(evidence, "_mint_token", None)
        is not _PAID_RESTORE_CANONICAL_DERIVATION_MINT
    ):
        raise TypeError("paid restore warm evidence persist mint drift")
    # 先原子消费；此后任何 root/digest/bytes 校验失败都不得重用该授权对象。
    _consume_paid_restore_warm_evidence(evidence)
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    resolved_output_root = Path(output_root).resolve(strict=False)
    if selection not in {"full", "representative"}:
        raise ValueError("paid restore selection is invalid")
    if (
        snapshot_root != evidence.frozen_snapshot_root
        or inventory_root != evidence.frozen_prepared_inventory_root
        or bundle_root != evidence.plan_bundle_root
        or planning_root != evidence.planning_artifact_root
        or resolved_output_root != evidence.output_root
        or selection != evidence.selection
        or resume is not evidence.resume
        or evidence.authority.output_root.resolve(strict=False)
        != resolved_output_root
    ):
        raise ValueError("paid restore warm evidence root or mode drift")
    corrected_digest = _paid_restore_require_sha256(
        expected_corrected_bundle_digest,
        label="corrected bundle",
    )
    provider_budget_digest = _paid_restore_require_sha256(
        expected_provider_budget_digest,
        label="provider budget",
    )
    if (
        corrected_digest != evidence.acquisition_bundle_digest
        or provider_budget_digest != evidence.provider_budget_authority_digest
    ):
        raise ValueError("paid restore warm expected authority digest drift")

    binding, binding_ref = _load_results_first_paid_restore_binding_v2(snapshot_root)
    if binding != dict(evidence.binding) or binding_ref != dict(evidence.binding_ref):
        raise ValueError("paid restore warm binding evidence drift")
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_root,
        expected_ref=binding["base_snapshot_ref"],
    )
    if (
        snapshot.snapshot_digest != evidence.snapshot.snapshot_digest
        or _closure_snapshot_lineage(snapshot)
        != _closure_snapshot_lineage(evidence.snapshot)
    ):
        raise ValueError("paid restore warm closure snapshot drift")
    if isinstance(expected_preflight, Mapping):
        raise TypeError("paid restore warm reload requires a persisted preflight path")
    (
        preflight,
        preflight_path,
        preflight_file_sha256,
        preflight_file_size_bytes,
    ) = _paid_restore_read_preflight_file_exact(expected_preflight)
    if preflight_path != evidence.expected_preflight_path:
        raise ValueError("paid restore warm preflight path drift")
    if (
        preflight_file_sha256 != evidence.expected_preflight_file_sha256
        or preflight_file_size_bytes != evidence.expected_preflight_file_size_bytes
    ):
        raise ValueError("paid restore warm preflight byte digest drift")
    report_digest = _validate_results_first_paid_preflight(
        preflight,
        snapshot=snapshot,
        selection=selection,
        frozen_snapshot_root=snapshot_root,
    )
    manifest = binding.get("prepared_inventory_manifest")
    if not isinstance(manifest, Mapping):
        raise TypeError("paid restore prepared inventory manifest is invalid")
    _paid_restore_validate_inventory_snapshot_lineage(
        manifest=manifest,
        snapshot=snapshot,
    )
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        manifest,
        digest_field="manifest_digest",
        label="paid restore prepared inventory manifest",
    )
    (
        persisted_manifest,
        records_sha256,
        records_size_bytes,
        record_count,
    ) = _paid_restore_verify_inventory_files_once(
        inventory_root=inventory_root,
        expected_manifest={**manifest_body, "manifest_digest": manifest_digest},
    )
    # nested request/body 虽来自 frozen authority 仍是 mutable；每次 warm call 重算一次。
    current_inventory_digest = evidence.inventory.inventory_digest
    if (
        persisted_manifest != dict(evidence.prepared_inventory_manifest)
        or manifest_digest != evidence.prepared_inventory_manifest_digest
        or manifest_digest != binding["prepared_inventory_manifest_digest"]
        or records_sha256 != evidence.inventory_records_sha256
        or records_size_bytes != evidence.inventory_records_size_bytes
        or record_count != evidence.inventory_record_count
        or current_inventory_digest != evidence.inventory_digest
        or current_inventory_digest != manifest.get("inventory_digest")
        or current_inventory_digest != binding["prepared_inventory_digest"]
        or report_digest != evidence.expected_preflight_report_digest
        or report_digest != binding["preflight_report_digest"]
        or snapshot.snapshot_digest != binding_ref["snapshot_digest"]
    ):
        raise ValueError("paid restore warm inventory or preflight evidence drift")

    corrected_bundle = _load_paid_restore_acquisition_bundle_exact(bundle_root)
    corrected_bundle_body = corrected_bundle.to_dict()
    evidence_bundle_body = evidence.authority.bundle.to_dict()
    source_exp1_inventory_plan = corrected_bundle_body.get(
        "semantic_inventory_plan"
    )
    if not isinstance(source_exp1_inventory_plan, Mapping):
        raise TypeError("paid restore warm Exp1 source plan is invalid")
    source_plan_digest = digest_json(source_exp1_inventory_plan)
    if (
        corrected_bundle_body != evidence_bundle_body
        or corrected_bundle.bundle_digest != corrected_digest
        or corrected_bundle.bundle_digest != binding["acquisition_bundle_digest"]
        or corrected_bundle.inventory_digest
        != binding["acquisition_inventory_digest"]
        or corrected_bundle.representative_plan_digest
        != binding["representative_plan_digest"]
        or source_exp1_inventory_plan != dict(evidence.source_exp1_inventory_plan)
        or source_exp1_inventory_plan != binding["source_exp1_inventory_plan"]
        or source_plan_digest != evidence.source_exp1_inventory_plan_digest
        or source_plan_digest != binding["source_exp1_inventory_plan_digest"]
    ):
        raise ValueError("paid restore warm corrected acquisition bundle drift")
    if (
        binding["selection"] != selection
        or binding["source_validation_digest"]
        != evidence.authority.source_validation_digest
        or binding["source_snapshot_digest"]
        != evidence.authority.full_snapshot.snapshot_digest
        or binding["coverage_digest"] != evidence.authority.coverage.coverage_digest
        or binding["full_budget_digest"]
        != evidence.authority.full_budget.budget_digest
        or binding["projection_digest"]
        != evidence.authority.execution_budget_projection.projection_digest
        or binding["hard_limits_digest"]
        != digest_json(dict(evidence.authority.hard_limits))
        or binding["provider_config_digests"]
        != _closure_snapshot_config_digests(evidence.authority.ai_api_configs)
        or binding.get("provider_calls_made") != 0
    ):
        raise ValueError("paid restore warm typed authority lineage drift")

    expected_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=corrected_bundle.full_budget,
        exp5_execution_projection=(
            _selection_exact_exp5_execution_projection(evidence.authority)
        ),
    )
    persisted_budget = _load_results_first_provider_budget_exact(
        planning_artifact_root=planning_root,
        expected_authority_digest=provider_budget_digest,
    )
    if (
        persisted_budget != expected_budget
        or persisted_budget != evidence.authority.provider_budget_authority
        or persisted_budget.authority_digest
        != evidence.provider_budget_authority_digest
    ):
        raise ValueError("results-first provider budget does not match frozen selection")
    if not resume and resolved_output_root.exists():
        raise ValueError("paid restore warm new-run output target must be fresh")
    return evidence.authority


def load_results_first_paid_plan_only_authority_v2(
    *,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    expected_preflight: str | Path | Mapping[str, object],
    source_rebind_root: str | Path | None = None,
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    output_root: str | Path,
    selection: str,
    resume: bool,
    expected_provider_budget_digest: str | None = None,
) -> ResultsFirstPaidPlanOnlyAuthority:
    """只读签发 paid plan-only certificate；不恢复 inventory objects。"""

    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )
    from tokenshare.experiments.paper_models import digest_json

    if selection not in {"full", "representative"}:
        raise ValueError("paid restore selection is invalid")
    if isinstance(expected_preflight, Mapping):
        raise TypeError("paid plan-only requires a persisted preflight path")
    provider_budget_digest = _paid_restore_require_sha256(
        expected_provider_budget_digest,
        label="provider budget",
    )
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    resolved_output_root = Path(output_root).resolve(strict=False)
    preflight_input_path = Path(expected_preflight).resolve(strict=False)
    if not all(
        path.is_dir()
        for path in (snapshot_root, inventory_root, bundle_root, planning_root)
    ):
        raise ValueError("paid plan-only frozen authority root is missing")
    if not resume and resolved_output_root.exists():
        raise ValueError("paid plan-only new-run output target must be fresh")

    # ref 是 binding 的 last-commit marker；partial pair 必须先在这里失败。
    binding, binding_ref = _load_results_first_paid_restore_binding_v2(
        snapshot_root
    )
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_root,
        expected_ref=binding["base_snapshot_ref"],
    )
    source_rebind: Mapping[str, object] | None = None
    source_rebind_ref: Mapping[str, object] | None = None
    if source_rebind_root is None:
        (
            preflight,
            preflight_path,
            preflight_file_sha256,
            preflight_file_size_bytes,
        ) = _paid_restore_read_preflight_file_exact(preflight_input_path)
        canonical_preflight = _closure_snapshot_json_bytes(dict(preflight))
        if (
            len(canonical_preflight) != preflight_file_size_bytes
            or f"sha256:{sha256(canonical_preflight).hexdigest()}"
            != preflight_file_sha256
        ):
            raise ValueError("paid plan-only preflight bytes are not canonical")
        report_digest = _validate_results_first_paid_preflight(
            preflight,
            snapshot=snapshot,
            selection=selection,
            frozen_snapshot_root=snapshot_root,
        )
    else:
        preflight, source_rebind, source_rebind_ref = (
            _validate_paid_source_rebind_base_and_current(
                source_rebind_root=source_rebind_root,
                binding=binding,
                binding_ref=binding_ref,
                old_preflight_path=preflight_input_path,
                snapshot=snapshot,
                selection=selection,
                frozen_snapshot_root=snapshot_root,
            )
        )
        preflight_path = _paid_source_rebind_paths(
            Path(source_rebind_root).resolve(strict=False)
        )[0]
        canonical_preflight = _closure_snapshot_json_bytes(dict(preflight))
        preflight_file_sha256 = f"sha256:{sha256(canonical_preflight).hexdigest()}"
        preflight_file_size_bytes = len(canonical_preflight)
        report_digest = str(preflight["report_digest"])

    manifest = binding.get("prepared_inventory_manifest")
    if not isinstance(manifest, Mapping):
        raise TypeError("paid plan-only inventory manifest is invalid")
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        manifest,
        digest_field="manifest_digest",
        label="paid plan-only inventory manifest",
    )
    _paid_restore_validate_inventory_snapshot_lineage(
        manifest=manifest,
        snapshot=snapshot,
    )
    (
        persisted_manifest,
        records_sha256,
        records_size_bytes,
        record_count,
    ) = _paid_restore_verify_inventory_files_once(
        inventory_root=inventory_root,
        expected_manifest={**manifest_body, "manifest_digest": manifest_digest},
    )
    fixed_first_attempt_count = (
        snapshot.coverage.source_snapshot.first_attempt_ai_unit_count
    )
    manifest_record_count = persisted_manifest.get("record_count")
    manifest_unique_request_count = persisted_manifest.get(
        "unique_inference_request_count"
    )
    if (
        type(fixed_first_attempt_count) is not int
        or fixed_first_attempt_count < 1
        or type(manifest_record_count) is not int
        or manifest_record_count != fixed_first_attempt_count
        or record_count != fixed_first_attempt_count
        or type(manifest_unique_request_count) is not int
        or manifest_unique_request_count < 1
        or manifest_unique_request_count > fixed_first_attempt_count
    ):
        raise ValueError("paid plan-only inventory fixed denominator drift")
    if (
        manifest_digest != binding["prepared_inventory_manifest_digest"]
        or (
            source_rebind is None
            and report_digest != binding["preflight_report_digest"]
        )
        or snapshot.snapshot_digest != binding_ref["snapshot_digest"]
        or binding_ref["base_ref_digest"]
        != binding["base_snapshot_ref"].get("ref_digest")
        or binding["selection"] != selection
        or binding["source_validation_digest"]
        != snapshot.source_validation_digest
        or binding["source_snapshot_digest"]
        != snapshot.coverage.source_snapshot.snapshot_digest
        or binding["coverage_digest"] != snapshot.coverage.coverage_digest
        or binding["full_budget_digest"] != snapshot.full_budget.budget_digest
        or binding["projection_digest"]
        != snapshot.execution_budget_projection.projection_digest
        or binding["hard_limits_digest"] != digest_json(dict(snapshot.hard_limits))
        or binding["provider_config_digests"]
        != _closure_snapshot_config_digests(snapshot.ai_api_configs)
        or binding["prepared_inventory_digest"]
        != persisted_manifest["inventory_digest"]
    ):
        raise ValueError("paid plan-only frozen authority lineage drift")

    corrected_bundle = _load_paid_restore_acquisition_bundle_exact(bundle_root)
    corrected_source_plan = corrected_bundle.to_dict().get(
        "semantic_inventory_plan"
    )
    if (
        corrected_bundle.bundle_digest != binding["acquisition_bundle_digest"]
        or corrected_bundle.inventory_digest
        != binding["acquisition_inventory_digest"]
        or corrected_bundle.representative_plan_digest
        != binding["representative_plan_digest"]
        or corrected_bundle.source_snapshot_digest
        != snapshot.coverage.source_snapshot.snapshot_digest
        or corrected_bundle.source_prepared_inventory_digest
        != persisted_manifest["inventory_digest"]
        or corrected_bundle.coverage_digest != snapshot.coverage.coverage_digest
        or corrected_source_plan != binding["source_exp1_inventory_plan"]
    ):
        raise ValueError("paid plan-only corrected acquisition bundle drift")

    expected_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=corrected_bundle.full_budget,
        exp5_execution_projection=(
            _selection_exact_exp5_execution_projection_from_snapshot(snapshot)
        ),
    )
    persisted_budget = _load_results_first_provider_budget_exact(
        planning_artifact_root=planning_root,
        expected_authority_digest=provider_budget_digest,
    )
    if persisted_budget != expected_budget:
        raise ValueError("paid plan-only provider budget does not match selection")
    if source_rebind is not None:
        _validate_paid_source_rebind_authorities(
            rebind=source_rebind,
            binding=binding,
            snapshot=snapshot,
            persisted_manifest=persisted_manifest,
            records_sha256=records_sha256,
            records_size_bytes=records_size_bytes,
            record_count=record_count,
            corrected_bundle=corrected_bundle,
            provider_budget=persisted_budget,
        )
    certificate = _mint_results_first_paid_plan_only_authority(
        snapshot=snapshot,
        bundle=corrected_bundle,
        provider_budget_authority=persisted_budget,
        frozen_snapshot_root=snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        plan_bundle_root=bundle_root,
        planning_artifact_root=planning_root,
        expected_preflight_path=preflight_path,
        output_root=resolved_output_root,
        selection=selection,
        resume=resume,
        source_validation_digest=snapshot.source_validation_digest,
        source_snapshot_digest=snapshot.coverage.source_snapshot.snapshot_digest,
        coverage_digest=snapshot.coverage.coverage_digest,
        full_budget_digest=snapshot.full_budget.budget_digest,
        profile_digest=corrected_bundle.profile_digest,
        condition_count=snapshot.coverage.condition_count,
        root_run_count=snapshot.coverage.root_run_count,
        inventory_digest=str(persisted_manifest["inventory_digest"]),
        inventory_records_sha256=records_sha256,
        inventory_records_size_bytes=records_size_bytes,
        inventory_record_count=record_count,
        preflight_report_digest=report_digest,
        preflight_file_sha256=preflight_file_sha256,
        preflight_file_size_bytes=preflight_file_size_bytes,
        binding_digest=str(
            source_rebind["binding_digest"]
            if source_rebind is not None
            else binding["binding_digest"]
        ),
        binding_ref_digest=str(
            source_rebind_ref["ref_digest"]
            if source_rebind_ref is not None
            else binding_ref["ref_digest"]
        ),
        provider_calls_made=0,
    )
    _register_paid_plan_only_certificate(certificate)
    return certificate


def load_results_first_paid_execution_authority_v2(
    *,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    expected_preflight: str | Path | Mapping[str, object],
    source_rebind_root: str | Path | None = None,
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    output_root: str | Path,
    selection: str,
    resume: bool,
    expected_corrected_bundle_digest: str | None = None,
    expected_provider_budget_digest: str | None = None,
    warm_derivation_evidence: PaidRestoreCanonicalDerivationEvidence | None = None,
) -> ResultsFirstExecutionAuthority:
    """只读恢复 frozen authority，并核验预先物化的 Exp1-only plan/budget。"""

    if warm_derivation_evidence is not None and source_rebind_root is not None:
        raise ValueError(
            "paid restore warm evidence and source rebind are mutually exclusive"
        )
    if warm_derivation_evidence is not None:
        return _load_results_first_paid_execution_authority_v2_warm(
            frozen_snapshot_root=frozen_snapshot_root,
            frozen_prepared_inventory_root=frozen_prepared_inventory_root,
            expected_preflight=expected_preflight,
            plan_bundle_root=plan_bundle_root,
            planning_artifact_root=planning_artifact_root,
            output_root=output_root,
            selection=selection,
            resume=resume,
            expected_corrected_bundle_digest=expected_corrected_bundle_digest,
            expected_provider_budget_digest=expected_provider_budget_digest,
            evidence=warm_derivation_evidence,
        )

    from tokenshare.experiments.paper_formal_plan import (
        load_formal_prepared_request_inventory,
    )
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        FullAcquisitionBudget,
        materialize_results_first_unified_acquisition_plan,
        prepare_results_first_acquisition_authority,
    )
    from tokenshare.experiments.paper_exp1_trace_reuse import (
        select_exp1_source_roots,
    )
    from decimal import Decimal

    if selection not in {"full", "representative"}:
        raise ValueError("paid restore selection is invalid")
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    if not all(path.is_dir() for path in (snapshot_root, inventory_root)):
        raise ValueError("paid restore frozen authority root is missing")
    binding, binding_ref = _load_results_first_paid_restore_binding_v2(snapshot_root)
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_root,
        expected_ref=binding["base_snapshot_ref"],
    )
    source_rebind: Mapping[str, object] | None = None
    source_rebind_ref: Mapping[str, object] | None = None
    if source_rebind_root is None:
        if isinstance(expected_preflight, Mapping):
            preflight = expected_preflight
        else:
            preflight = _closure_snapshot_read_json(
                Path(expected_preflight).resolve(strict=False),
                label="paid restore expected preflight",
            )
        if not isinstance(preflight, Mapping):
            raise TypeError("paid restore expected preflight is invalid")
        report_digest = _validate_results_first_paid_preflight(
            preflight,
            snapshot=snapshot,
            selection=selection,
            frozen_snapshot_root=snapshot_root,
        )
    else:
        if isinstance(expected_preflight, Mapping):
            raise TypeError("paid source rebind requires persisted base preflight")
        preflight, source_rebind, source_rebind_ref = (
            _validate_paid_source_rebind_base_and_current(
                source_rebind_root=source_rebind_root,
                binding=binding,
                binding_ref=binding_ref,
                old_preflight_path=expected_preflight,
                snapshot=snapshot,
                selection=selection,
                frozen_snapshot_root=snapshot_root,
            )
        )
        report_digest = str(preflight["report_digest"])
    manifest = binding["prepared_inventory_manifest"]
    if not isinstance(manifest, Mapping):
        raise TypeError("paid restore prepared inventory manifest is invalid")
    manifest_body, manifest_digest = _paid_restore_canonical_digest(
        manifest,
        digest_field="manifest_digest",
        label="paid restore prepared inventory manifest",
    )
    if (
        manifest_digest != binding["prepared_inventory_manifest_digest"]
        or (
            source_rebind is None
            and report_digest != binding["preflight_report_digest"]
        )
        or snapshot.snapshot_digest != binding_ref["snapshot_digest"]
        or binding_ref["base_ref_digest"]
        != binding["base_snapshot_ref"].get("ref_digest")
    ):
        raise ValueError("paid restore persisted authority digest drift")
    inventory = load_formal_prepared_request_inventory(
        output_root=inventory_root,
        snapshot=snapshot.coverage.source_snapshot,
        expected_manifest={**manifest_body, "manifest_digest": manifest_digest},
    )
    if (
        binding["selection"] != selection
        or binding["source_validation_digest"] != snapshot.source_validation_digest
        or binding["source_snapshot_digest"]
        != snapshot.coverage.source_snapshot.snapshot_digest
        or binding["coverage_digest"] != snapshot.coverage.coverage_digest
        or binding["full_budget_digest"] != snapshot.full_budget.budget_digest
        or binding["projection_digest"]
        != snapshot.execution_budget_projection.projection_digest
        or binding["hard_limits_digest"] != digest_json(dict(snapshot.hard_limits))
        or binding["provider_config_digests"]
        != _closure_snapshot_config_digests(snapshot.ai_api_configs)
        or binding["prepared_inventory_digest"] != inventory.inventory_digest
    ):
        raise ValueError("paid restore typed authority lineage drift")

    source_roots = select_exp1_source_roots(
        full_roots=snapshot.coverage.source_snapshot.roots,
        selected_roots=snapshot.coverage.roots,
    )
    source_keys = {
        (root.condition.condition_id, root.case_id, planned_ai_unit_id)
        for root in source_roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    }
    records_by_key = {
        (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        ): record
        for record in inventory.records
    }
    if not source_keys or not source_keys.issubset(records_by_key):
        raise ValueError("paid restore Exp1 source authority is incomplete")
    api_key_env_by_family: dict[str, str] = {}
    pricing_by_family: dict[str, FrozenPricing] = {}
    for key in sorted(source_keys):
        record = records_by_key[key]
        config = snapshot.ai_api_configs.get(record.provider_config_id)
        matches = tuple(
            entry
            for entry in getattr(config, "entries", ())
            if getattr(entry, "enabled", None) is True
            and getattr(entry, "entry_id", None) == record.model_entry_id
        )
        if len(matches) != 1:
            raise ValueError("paid restore Exp1 provider authority is incomplete")
        entry = matches[0]
        api_key_env = getattr(entry, "api_key_env", None)
        pricing_body = getattr(entry, "pricing", None)
        if not isinstance(api_key_env, str) or not isinstance(pricing_body, Mapping):
            raise ValueError("paid restore Exp1 transport authority is incomplete")
        pricing = FrozenPricing(
            currency=str(pricing_body["currency"]),
            input_per_million_tokens=Decimal(
                str(
                    pricing_body.get(
                        "input_per_million_tokens",
                        pricing_body.get("uncached_input_per_million_tokens"),
                    )
                )
            ),
            output_per_million_tokens=Decimal(
                str(pricing_body["output_per_million_tokens"])
            ),
        )
        previous_env = api_key_env_by_family.setdefault(
            record.provider_family, api_key_env
        )
        previous_pricing = pricing_by_family.setdefault(
            record.provider_family, pricing
        )
        if previous_env != api_key_env or previous_pricing != pricing:
            raise ValueError("paid restore Exp1 transport authority is ambiguous")
    atomic_authority = prepare_results_first_acquisition_authority(
        full_snapshot=snapshot.coverage.source_snapshot,
        full_prepared_inventory=inventory,
        full_budget=snapshot.full_budget,
        coverage=snapshot.coverage,
        execution_budget_projection=snapshot.execution_budget_projection,
        catalog_manifest=snapshot.catalog_manifest,
        ai_api_configs=snapshot.ai_api_configs,
        planning_artifact_root=planning_root,
        api_key_env_by_provider_family=api_key_env_by_family,
        frozen_pricing_by_provider_family=pricing_by_family,
        requested_at="2026-08-09T00:00:00Z",
        max_acquisition_concurrency=10,
    )
    if expected_corrected_bundle_digest is not None and (
        not isinstance(expected_corrected_bundle_digest, str)
        or len(expected_corrected_bundle_digest) != 71
        or not expected_corrected_bundle_digest.startswith("sha256:")
    ):
        raise ValueError("paid restore corrected bundle digest is invalid")
    if not bundle_root.is_dir():
        raise ValueError("paid restore corrected bundle root is missing")
    from tokenshare.experiments.paper_response_bank import (
        materialize_results_first_unified_acquisition_plan,
        validate_representative_acquisition_plan_bundle,
    )

    corrected_bundle = _load_paid_restore_acquisition_bundle_exact(bundle_root)
    materialized_plan = materialize_results_first_unified_acquisition_plan(
        plan=atomic_authority.plan
    )
    validate_representative_acquisition_plan_bundle(
        bundle=corrected_bundle,
        plan=materialized_plan,
    )
    corrected_source_plan = corrected_bundle.to_dict().get(
        "semantic_inventory_plan"
    )
    if (
        (
            expected_corrected_bundle_digest is not None
            and corrected_bundle.bundle_digest != expected_corrected_bundle_digest
        )
        or corrected_bundle.bundle_digest != binding["acquisition_bundle_digest"]
        or corrected_bundle.inventory_digest
        != binding["acquisition_inventory_digest"]
        or corrected_bundle.representative_plan_digest
        != binding["representative_plan_digest"]
        or corrected_source_plan != binding["source_exp1_inventory_plan"]
    ):
        raise ValueError("paid restore corrected acquisition bundle drift")
    authority = ResultsFirstExecutionAuthority(
        source_validation_digest=atomic_authority.validation_digest,
        full_dispatch_plans=snapshot.full_dispatch_plans,
        catalog_manifest=snapshot.catalog_manifest,
        full_snapshot=atomic_authority.full_snapshot,
        full_prepared_inventory=atomic_authority.full_prepared_inventory,
        full_budget=atomic_authority.full_budget,
        ai_api_configs=snapshot.ai_api_configs,
        coverage=atomic_authority.coverage,
        execution_budget_projection=atomic_authority.execution_budget_projection,
        bundle=corrected_bundle,
        bundle_root=bundle_root,
        output_root=Path(output_root),
        resume=resume,
        source_rebind_binding_digest=(
            str(source_rebind["binding_digest"])
            if source_rebind is not None
            else None
        ),
        source_rebind_ref_digest=(
            str(source_rebind_ref["ref_digest"])
            if source_rebind_ref is not None
            else None
        ),
    )
    from tokenshare.experiments.paper_budget import derive_results_first_provider_budget

    expected_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=corrected_bundle.full_budget,
        exp5_execution_projection=_selection_exact_exp5_execution_projection(authority),
    )
    persisted_budget = _load_results_first_provider_budget_exact(
        planning_artifact_root=planning_root,
        expected_authority_digest=expected_provider_budget_digest,
    )
    if persisted_budget != expected_budget:
        raise ValueError("results-first provider budget does not match frozen selection")
    if source_rebind is not None:
        _validate_paid_source_rebind_authorities(
            rebind=source_rebind,
            binding=binding,
            snapshot=snapshot,
            persisted_manifest=manifest,
            records_sha256=str(manifest["records_sha256"]),
            records_size_bytes=int(manifest["records_size_bytes"]),
            record_count=int(manifest["record_count"]),
            corrected_bundle=corrected_bundle,
            provider_budget=persisted_budget,
        )
    return replace(authority, provider_budget_authority=persisted_budget)


def _load_provider_zero_reference_publication(
    root: str | Path,
) -> tuple[dict[str, object], dict[str, object]]:
    from tokenshare.experiments.paper_models import digest_json

    resolved = Path(root).resolve(strict=False)
    binding_path = resolved / _PROVIDER_ZERO_REFERENCE_BINDING_NAME
    ref_path = resolved / _PROVIDER_ZERO_REFERENCE_BINDING_REF_NAME
    states = (binding_path.is_file(), ref_path.is_file())
    if states != (True, True):
        raise ValueError("provider-zero reference publication is incomplete")
    binding, binding_file_sha256, binding_file_size = (
        _paid_source_rebind_read_canonical(
            binding_path,
            label="provider-zero reference binding",
        )
    )
    ref, _ref_sha256, _ref_size = _paid_source_rebind_read_canonical(
        ref_path,
        label="provider-zero reference binding ref",
    )
    binding_body, binding_digest = _paid_restore_canonical_digest(
        binding,
        digest_field="binding_digest",
        label="provider-zero reference binding",
    )
    ref_body, ref_digest = _paid_restore_canonical_digest(
        ref,
        digest_field="ref_digest",
        label="provider-zero reference binding ref",
    )
    binding_fields = {
        "schema_version",
        "legacy_source_v1_root",
        "legacy_closure_snapshot_digest",
        "paid_restore_v2_root",
        "paid_restore_v2_binding_digest",
        "paid_restore_v2_binding_ref_digest",
        "paid_restore_v2_closure_snapshot_digest",
        "corrected_bundle_root",
        "corrected_bundle_digest",
        "planning_artifact_root",
        "provider_budget_authority_digest",
        "selection",
        "lineage",
        "source_validation_digest",
        "source_snapshot_digest",
        "coverage_digest",
        "full_budget_digest",
        "projection_digest",
        "hard_limits_digest",
        "catalog_digest",
        "provider_config_digests",
        "source_exp1_inventory_plan_digest",
        "provider_calls_made",
    }
    ref_fields = {
        "schema_version",
        "binding_path",
        "binding_digest",
        "binding_file_sha256",
        "binding_file_size_bytes",
        "provider_calls_made",
    }
    if (
        set(binding_body) != binding_fields
        or binding_body.get("schema_version")
        != _PROVIDER_ZERO_REFERENCE_BINDING_SCHEMA
        or set(ref_body) != ref_fields
        or ref_body.get("schema_version")
        != _PROVIDER_ZERO_REFERENCE_BINDING_REF_SCHEMA
        or ref_body.get("binding_path") != _PROVIDER_ZERO_REFERENCE_BINDING_NAME
        or ref_body.get("binding_digest") != binding_digest
        or ref_body.get("binding_file_sha256") != binding_file_sha256
        or ref_body.get("binding_file_size_bytes") != binding_file_size
        or type(binding_body.get("provider_calls_made")) is not int
        or binding_body.get("provider_calls_made") != 0
        or type(ref_body.get("provider_calls_made")) is not int
        or ref_body.get("provider_calls_made") != 0
    ):
        raise ValueError("provider-zero reference binding/ref drift")
    return (
        {**binding_body, "binding_digest": binding_digest},
        {**ref_body, "ref_digest": ref_digest},
    )


def _load_paid_reference_execution_authority(
    *,
    paid_reference_binding_root: str | Path,
    frozen_snapshot_root: str | Path,
    frozen_prepared_inventory_root: str | Path,
    expected_preflight: str | Path,
    source_rebind_root: str | Path,
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    output_root: str | Path,
    selection: str,
    resume: bool,
    expected_provider_budget_digest: str,
) -> ResultsFirstExecutionAuthority:
    from tokenshare.experiments.paper_models import digest_json

    reference, reference_ref = _load_provider_zero_reference_publication(
        paid_reference_binding_root
    )
    lineage = reference.get("lineage")
    if not isinstance(lineage, Mapping) or set(lineage) != {
        "sealed_inventory",
        "current_source_rebind",
        "current_provider_zero_preflight",
    }:
        raise ValueError("provider-zero reference lineage drift")
    sealed_inventory = lineage["sealed_inventory"]
    current_rebind = lineage["current_source_rebind"]
    current_preflight = lineage["current_provider_zero_preflight"]
    if not all(
        isinstance(value, Mapping)
        for value in (sealed_inventory, current_rebind, current_preflight)
    ):
        raise TypeError("provider-zero reference lineage object drift")
    snapshot_root = Path(frozen_snapshot_root).resolve(strict=False)
    inventory_root = Path(frozen_prepared_inventory_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    rebind_root = Path(source_rebind_root).resolve(strict=False)
    base_preflight_path = Path(expected_preflight).resolve(strict=False)
    current_preflight_path = (
        rebind_root / _PAID_SOURCE_REBIND_PREFLIGHT_NAME
    ).resolve(strict=False)
    if (
        Path(str(reference["paid_restore_v2_root"])).resolve(strict=False)
        != snapshot_root
        or Path(str(reference["corrected_bundle_root"])).resolve(strict=False)
        != bundle_root
        or Path(str(reference["planning_artifact_root"])).resolve(strict=False)
        != planning_root
        or Path(str(sealed_inventory["root"])).resolve(strict=False)
        != inventory_root
        or Path(str(current_rebind["root"])).resolve(strict=False) != rebind_root
        or Path(str(current_preflight["path"])).resolve(strict=False)
        != current_preflight_path
        or reference.get("selection") != selection
        or reference.get("provider_budget_authority_digest")
        != expected_provider_budget_digest
    ):
        raise ValueError("provider-zero reference CLI cross-edge drift")
    preflight, source_rebind, source_rebind_ref = (
        _load_results_first_paid_source_rebind_v1(rebind_root)
    )
    paid_restore_binding, paid_restore_ref = (
        _load_results_first_paid_restore_binding_v2(snapshot_root)
    )
    if (
        source_rebind.get("binding_digest")
        != current_rebind.get("binding_digest")
        or source_rebind_ref.get("ref_digest") != current_rebind.get("ref_digest")
        or preflight.get("report_digest")
        != current_preflight.get("report_digest")
        or "sha256:" + sha256(current_preflight_path.read_bytes()).hexdigest()
        != current_preflight.get("file_sha256")
        or reference.get("paid_restore_v2_binding_digest")
        != paid_restore_binding.get("binding_digest")
        or reference.get("paid_restore_v2_binding_ref_digest")
        != paid_restore_ref.get("ref_digest")
        or reference.get("paid_restore_v2_closure_snapshot_digest")
        != paid_restore_ref.get("snapshot_digest")
        or source_rebind.get("base_binding_digest")
        != paid_restore_binding.get("binding_digest")
        or source_rebind.get("base_binding_ref_digest")
        != paid_restore_ref.get("ref_digest")
        or source_rebind.get("snapshot_digest")
        != paid_restore_ref.get("snapshot_digest")
    ):
        raise ValueError("provider-zero current-source cross-edge drift")
    authority = load_results_first_paid_execution_authority_v2(
        frozen_snapshot_root=snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=base_preflight_path,
        source_rebind_root=rebind_root,
        plan_bundle_root=bundle_root,
        planning_artifact_root=planning_root,
        output_root=output_root,
        selection=selection,
        resume=resume,
        expected_corrected_bundle_digest=str(reference["corrected_bundle_digest"]),
        expected_provider_budget_digest=expected_provider_budget_digest,
    )
    if (
        type(authority) is not ResultsFirstExecutionAuthority
        or authority.source_validation_digest
        != reference.get("source_validation_digest")
        or authority.full_snapshot.snapshot_digest
        != reference.get("source_snapshot_digest")
        or authority.coverage.coverage_digest != reference.get("coverage_digest")
        or authority.full_budget.budget_digest != reference.get("full_budget_digest")
        or authority.execution_budget_projection.projection_digest
        != reference.get("projection_digest")
        or digest_json(dict(authority.hard_limits))
        != reference.get("hard_limits_digest")
        or authority.catalog_manifest.catalog_digest != reference.get("catalog_digest")
        or _closure_snapshot_config_digests(authority.ai_api_configs)
        != reference.get("provider_config_digests")
        or authority.full_prepared_inventory.inventory_digest
        != sealed_inventory.get("inventory_digest")
        or authority.bundle.bundle_digest != reference.get("corrected_bundle_digest")
        or authority.provider_budget_authority.authority_digest
        != reference.get("provider_budget_authority_digest")
        or digest_json(authority.bundle.to_dict()["semantic_inventory_plan"])
        != reference.get("source_exp1_inventory_plan_digest")
    ):
        raise ValueError("provider-zero reference returned authority lineage drift")
    return replace(
        authority,
        provider_zero_reference_binding_digest=str(reference["binding_digest"]),
        provider_zero_reference_binding_ref_digest=str(reference_ref["ref_digest"]),
        current_provider_zero_preflight_report_digest=str(
            preflight["report_digest"]
        ),
    )


_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER = (
    _load_paid_reference_execution_authority
)


def _validate_results_first_paid_launch_topology(
    *,
    frozen_snapshot_root: Path,
    frozen_prepared_inventory_root: Path,
    expected_preflight: Path,
    source_rebind_root: Path,
    plan_bundle_root: Path,
    planning_artifact_root: Path,
    paid_reference_binding_root: Path,
    output_root: Path,
    paid_launch_attestation_root: Path,
    paid_launch_key_config: Path,
    resume: bool,
) -> None:
    sources = (
        Path(frozen_snapshot_root).resolve(strict=False),
        Path(frozen_prepared_inventory_root).resolve(strict=False),
        Path(expected_preflight).resolve(strict=False),
        Path(plan_bundle_root).resolve(strict=False),
        Path(planning_artifact_root).resolve(strict=False),
        Path(paid_reference_binding_root).resolve(strict=False),
        Path(source_rebind_root).resolve(strict=False),
        Path(paid_launch_key_config).resolve(strict=False),
    )
    preflight = Path(expected_preflight).resolve(strict=False)
    if not preflight.is_file():
        raise ValueError("paid launch base preflight is missing")
    output = Path(output_root).resolve(strict=False)
    attestation = Path(paid_launch_attestation_root).resolve(strict=False)
    for target in (output, attestation):
        if target.is_symlink():
            raise ValueError("paid launch target cannot be a symlink")
        if any(
            target == source or target in source.parents or source in target.parents
            for source in sources
        ):
            raise ValueError("paid launch source/target topology overlap")
    if output == attestation or output in attestation.parents or attestation in output.parents:
        raise ValueError("paid output and attestation roots overlap")
    if not resume and (output.exists() or attestation.exists()):
        raise ValueError("paid launch new-run targets must be fresh")


def _acquire_paid_launch_single_instance(*, attestation_root: Path) -> Path:
    root = Path(attestation_root).resolve(strict=False)
    if root.exists() or root.is_symlink():
        raise ValueError("paid launch attestation root must be fresh")
    root.mkdir(parents=False, exist_ok=False)
    lock_path = root / PAID_LAUNCH_LOCK_NAME
    with lock_path.open("xb") as stream:
        stream.write(
            _closure_snapshot_json_bytes(
                {
                    "schema_version": "tokenshare.paid_launch_single_instance.v1",
                    "status": "HELD",
                }
            )
        )
        stream.flush()
        os.fsync(stream.fileno())
    with _PAID_LAUNCH_HELD_LOCKS_LOCK:
        if root in _PAID_LAUNCH_HELD_LOCKS:
            raise ValueError("paid launch lock is already held")
        _PAID_LAUNCH_HELD_LOCKS.add(root)
    return lock_path


_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER = _acquire_paid_launch_single_instance


def paid_launch_lock_is_held(attestation_root: str | Path) -> bool:
    root = Path(attestation_root).resolve(strict=False)
    with _PAID_LAUNCH_HELD_LOCKS_LOCK:
        return root in _PAID_LAUNCH_HELD_LOCKS and (
            root / PAID_LAUNCH_LOCK_NAME
        ).is_file()


def _load_paid_launch_local_config(*, key_config_path: Path) -> Mapping[str, str]:
    from tokenshare.executors.ai_api_local_config import load_local_ai_api_config

    load_local_ai_api_config(key_config_path)
    statuses = {
        name: "SET" if isinstance(os.environ.get(name), str) and os.environ[name] else "UNSET"
        for name in ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY")
    }
    if set(statuses.values()) != {"SET"}:
        raise ValueError("paid launch provider key readiness is incomplete")
    return MappingProxyType(statuses)


_PAID_LAUNCH_LOCAL_CONFIG_LOADER = _load_paid_launch_local_config


def _write_paid_launch_attestation(
    *,
    attestation_root: Path,
    authority: ResultsFirstExecutionAuthority,
    statuses: Mapping[str, str],
    expected_provider_budget_digest: str,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_models import digest_json

    if (
        type(authority) is not ResultsFirstExecutionAuthority
        or authority.provider_budget_authority.authority_digest
        != expected_provider_budget_digest
        or dict(statuses)
        != {"DEEPSEEK_API_KEY": "SET", "SILICONFLOW_API_KEY": "SET"}
    ):
        raise ValueError("paid launch attestation authority drift")
    body = {
        "schema_version": "tokenshare.paid_launch_key_readiness.v1",
        "scope": "results_first_paid_representative",
        "statuses": dict(statuses),
        "provider_budget_authority_digest": expected_provider_budget_digest,
        "source_validation_digest": authority.source_validation_digest,
        "provider_zero_reference_binding_digest": (
            authority.provider_zero_reference_binding_digest
        ),
        "provider_zero_reference_binding_ref_digest": (
            authority.provider_zero_reference_binding_ref_digest
        ),
        "current_provider_zero_preflight_report_digest": (
            authority.current_provider_zero_preflight_report_digest
        ),
        "provider_calls_made": 0,
    }
    attestation = {**body, "attestation_digest": digest_json(body)}
    path = Path(attestation_root).resolve(strict=False) / PAID_LAUNCH_ATTESTATION_NAME
    with path.open("xb") as stream:
        stream.write(_closure_snapshot_json_bytes(attestation))
        stream.flush()
        os.fsync(stream.fileno())
    return MappingProxyType(attestation)


_PAID_LAUNCH_ATTESTATION_WRITER = _write_paid_launch_attestation


def _mint_results_first_paid_launch_key_readiness(
    *,
    attestation_root: Path,
    attestation: Mapping[str, object],
    output_root: Path,
    expected_provider_budget_digest: str,
) -> ResultsFirstPaidLaunchKeyReadiness:
    token = ResultsFirstPaidLaunchKeyReadiness(
        attestation_root=Path(attestation_root).resolve(strict=False),
        attestation_digest=str(attestation["attestation_digest"]),
        output_root=Path(output_root).resolve(strict=False),
        provider_budget_authority_digest=expected_provider_budget_digest,
        _mint_identity=_PAID_LAUNCH_READINESS_MINT,
    )
    with _PAID_LAUNCH_READINESS_REGISTRY_LOCK:
        _PAID_LAUNCH_READINESS_REGISTRY[id(token)] = token
    return token


def consume_results_first_paid_launch_key_readiness(
    token: object,
) -> None:
    if (
        type(token) is not ResultsFirstPaidLaunchKeyReadiness
        or token._mint_identity is not _PAID_LAUNCH_READINESS_MINT
    ):
        raise TypeError("paid launch readiness token is invalid")
    with _PAID_LAUNCH_READINESS_REGISTRY_LOCK:
        registered = _PAID_LAUNCH_READINESS_REGISTRY.pop(id(token), None)
    if registered is not token:
        raise TypeError("paid launch readiness token was already consumed")


def _closure_replay_sibling(root: Path, suffix: str) -> Path:
    candidate = root.with_name(root.name + suffix).resolve(strict=False)
    if candidate.parent != root.parent or candidate == root:
        raise ValueError("closure replay sibling path escaped scratch parent")
    return candidate


def _closure_replay_require_fresh_target(
    *,
    source_root: Path,
    scratch_root: Path,
) -> tuple[Path, Path, Path]:
    if source_root == scratch_root or source_root in scratch_root.parents:
        raise ValueError("closure replay scratch cannot equal or be inside source")
    if not source_root.is_dir():
        raise ValueError("closure replay source suite root is missing")
    source_checkpoints = _closure_replay_sibling(
        source_root,
        ".canonical_direct_evidence",
    )
    if not source_checkpoints.is_dir():
        raise ValueError("closure replay canonical checkpoint source is missing")
    scratch_checkpoints = _closure_replay_sibling(
        scratch_root,
        ".canonical_direct_evidence",
    )
    scratch_traceability = _closure_replay_sibling(
        scratch_root,
        ".traceability_replay_inputs",
    )
    occupied = tuple(
        path
        for path in (scratch_root, scratch_checkpoints, scratch_traceability)
        if path.exists() or path.is_symlink()
    )
    if occupied:
        raise ValueError("closure replay scratch targets must be fresh")
    return source_checkpoints, scratch_checkpoints, scratch_traceability


def _closure_replay_reject_reparse_path(
    root: Path,
    *,
    recursive: bool,
    check_existing_parents: bool,
) -> None:
    from tokenshare.experiments.paper_formal_runner import (
        _reject_formal_reparse_path,
    )

    _reject_formal_reparse_path(
        root,
        recursive=recursive,
        check_existing_parents=check_existing_parents,
    )


def _closure_replay_remove_new_tree(
    *,
    path: Path,
    expected_parent: Path,
    expected_name_prefix: str,
) -> None:
    """只清理由本次调用新建且仍在指定parent内的partial tree。"""

    candidate = Path(os.path.abspath(path))
    parent = Path(os.path.abspath(expected_parent))
    if (
        candidate.parent != parent
        or not candidate.name.startswith(expected_name_prefix)
    ):
        raise ValueError("closure replay partial cleanup target is invalid")
    _closure_replay_reject_reparse_path(
        candidate,
        recursive=True,
        check_existing_parents=False,
    )
    if candidate.exists():
        if not candidate.is_dir():
            raise ValueError("closure replay partial cleanup target is not a directory")
        shutil.rmtree(candidate)


def _closure_replay_immutable_digests(suite_root: Path) -> dict[str, str]:
    checkpoint_root = _closure_replay_sibling(
        suite_root,
        ".canonical_direct_evidence",
    )
    paths = tuple(sorted(suite_root.rglob("CURRENT.json"))) + tuple(
        sorted(suite_root.glob("experiments/*/runs/*/*/.generations/*/generation_manifest.json"))
    ) + tuple(sorted(checkpoint_root.glob("*/canonical_direct_checkpoint.json")))
    result: dict[str, str] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        if suite_root not in resolved.parents and checkpoint_root not in resolved.parents:
            raise ValueError("closure replay immutable evidence escaped its root")
        prefix = "suite" if suite_root in resolved.parents else "checkpoints"
        base = suite_root if prefix == "suite" else checkpoint_root
        key = f"{prefix}/{resolved.relative_to(base).as_posix()}"
        if key in result or not resolved.is_file():
            raise ValueError("closure replay immutable evidence inventory is invalid")
        result[key] = "sha256:" + sha256(resolved.read_bytes()).hexdigest()
    return result


def _closure_replay_remove_derived(
    *,
    scratch_root: Path,
    scratch_traceability_root: Path,
) -> None:
    for path in (
        scratch_root / "formal_runner_result.json",
        scratch_root / "traceability_replay_input_root.handle.pickle",
    ):
        _closure_replay_reject_reparse_path(
            path,
            recursive=False,
            check_existing_parents=False,
        )
        resolved = path.resolve(strict=False)
        if resolved.parent != scratch_root:
            raise ValueError("closure replay derived path escaped scratch root")
        if path.exists():
            if not path.is_file():
                raise ValueError("closure replay derived entry is not a file")
            path.unlink()
    resolved_traceability = scratch_traceability_root.resolve(strict=False)
    _closure_replay_reject_reparse_path(
        scratch_traceability_root,
        recursive=True,
        check_existing_parents=False,
    )
    if (
        resolved_traceability.parent != scratch_root.parent
        or resolved_traceability.name
        != scratch_root.name + ".traceability_replay_inputs"
    ):
        raise ValueError("closure replay derived path escaped scratch parent")
    if scratch_traceability_root.exists():
        if not scratch_traceability_root.is_dir():
            raise ValueError("closure replay derived entry is not a directory")
        shutil.rmtree(scratch_traceability_root)

    suite_manifest_path = (scratch_root / "suite_manifest.json").resolve(
        strict=False
    )
    if suite_manifest_path.parent != scratch_root or not suite_manifest_path.is_file():
        raise ValueError("closure replay suite manifest is missing")
    suite_manifest = _closure_snapshot_read_json(
        suite_manifest_path,
        label="closure replay scratch suite manifest",
    )
    if not isinstance(suite_manifest, Mapping):
        raise ValueError("closure replay scratch suite manifest is invalid")
    if "traceability_replay_input_root_ref" in suite_manifest:
        cleaned = dict(suite_manifest)
        cleaned.pop("traceability_replay_input_root_ref")
        _atomic_replace_bytes(
            suite_manifest_path,
            _closure_snapshot_json_bytes(cleaned),
        )
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    FormalEvidenceStore(scratch_root)._refresh_evidence_manifest()


def resume_results_first_trace_closure_from_snapshot(
    *,
    source_trace_root: Path,
    snapshot_root: Path,
    scratch_output_root: Path,
    response_bank_resolver: object,
    transport: object,
) -> object:
    """复制immutable trace evidence并以同一formal runner重建收口。"""

    source_input = Path(os.path.abspath(Path(source_trace_root)))
    scratch_input = Path(os.path.abspath(Path(scratch_output_root)))
    snapshot_input = Path(os.path.abspath(Path(snapshot_root)))
    source_checkpoint_input = source_input.with_name(
        source_input.name + ".canonical_direct_evidence"
    )
    for path, recursive in (
        (source_input, True),
        (source_checkpoint_input, True),
        (scratch_input, False),
        (
            scratch_input.with_name(
                scratch_input.name + ".canonical_direct_evidence"
            ),
            False,
        ),
        (
            scratch_input.with_name(
                scratch_input.name + ".traceability_replay_inputs"
            ),
            False,
        ),
        (snapshot_input, False),
        (snapshot_input / _CLOSURE_REPLAY_DIRECTORY, True),
    ):
        _closure_replay_reject_reparse_path(
            path,
            recursive=recursive,
            check_existing_parents=True,
        )
    source_root = source_input.resolve(strict=False)
    scratch_root = scratch_input.resolve(strict=False)
    snapshot_output_root = snapshot_input.resolve(strict=False)
    # 所有 scratch/fresh-target/copy 操作之前，先只读验证 frozen snapshot 与
    # post-acquisition 三份 trace authority；缺失或漂移必须保持零副作用。
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_output_root,
        suite_root=None,
    )
    resolver_manifest = getattr(
        getattr(response_bank_resolver, "index", None), "manifest", None
    )
    source_manifest_digest = getattr(resolver_manifest, "manifest_digest", None)
    if not isinstance(source_manifest_digest, str) or not source_manifest_digest:
        raise ValueError("closure replay response bank manifest identity is missing")
    load_results_first_post_acquisition_trace_seal(
        output_root=snapshot_output_root,
        expected_closure_snapshot_digest=snapshot.snapshot_digest,
        expected_source_manifest_digest=source_manifest_digest,
    )
    (
        source_checkpoints,
        scratch_checkpoints,
        _scratch_traceability,
    ) = _closure_replay_require_fresh_target(
        source_root=source_root,
        scratch_root=scratch_root,
    )
    preflight = audit_results_first_trace_closure_source(suite_root=source_root)
    if (
        type(preflight) is not ResultsFirstTraceClosurePreflight
        or preflight.current_provider_attempt_count != 0
        or preflight.root_count != preflight.checkpoint_count
    ):
        raise ValueError("closure replay source preflight is not resumable")
    source_immutable = _closure_replay_immutable_digests(source_root)
    partial_prefix = f".{scratch_root.name}.closure-replay-partial-"
    partial_root = Path(
        tempfile.mkdtemp(
            prefix=partial_prefix,
            dir=scratch_root.parent,
        )
    )
    staged_suite = partial_root / "suite"
    staged_checkpoints = partial_root / "suite.canonical_direct_evidence"
    staged_traceability = partial_root / "suite.traceability_replay_inputs"
    published_checkpoints = False
    try:
        shutil.copytree(source_root, staged_suite)
        shutil.copytree(source_checkpoints, staged_checkpoints)
        from tokenshare.experiments.paper_formal_runner import (
            _repair_interrupted_formal_finalization,
        )

        _repair_interrupted_formal_finalization(staged_suite)
        if (staged_suite / "PENDING.json").exists():
            raise ValueError("closure replay top-level finalization repair is incomplete")
        _closure_replay_remove_derived(
            scratch_root=staged_suite,
            scratch_traceability_root=staged_traceability,
        )
        staged_immutable = _closure_replay_immutable_digests(staged_suite)
        if staged_immutable != source_immutable:
            raise ValueError("closure replay scratch immutable evidence copy drift")
        staged_snapshot = load_results_first_closure_replay_snapshot(
            output_root=snapshot_output_root,
            suite_root=staged_suite,
        )
        if staged_snapshot.snapshot_digest != snapshot.snapshot_digest:
            raise ValueError("closure replay snapshot digest drifted after staging")
        snapshot = staged_snapshot
        trace_experiment_ids = _closure_snapshot_trace_experiment_ids(snapshot)
        trace_conditions = tuple(
            condition
            for experiment_id in trace_experiment_ids
            for condition in snapshot.coverage.conditions
            if condition.experiment_id == experiment_id
        )
        if not trace_conditions:
            raise ValueError("closure replay trace condition selection is empty")
        expected_root_count = sum(
            len(snapshot.coverage.root_case_filter[condition.condition_id])
            for condition in trace_conditions
        )
        if (
            preflight.condition_count != len(trace_conditions)
            or preflight.root_count != expected_root_count
        ):
            raise ValueError("closure replay source/snapshot denominator drift")
        # sibling先发布；cleaned suite最后出现，因此失败时不会留下可误用旧terminal。
        os.replace(staged_checkpoints, scratch_checkpoints)
        published_checkpoints = True
        os.replace(staged_suite, scratch_root)
    except BaseException:
        if published_checkpoints and not scratch_root.exists():
            _closure_replay_remove_new_tree(
                path=scratch_checkpoints,
                expected_parent=scratch_root.parent,
                expected_name_prefix=scratch_checkpoints.name,
            )
        _closure_replay_remove_new_tree(
            path=partial_root,
            expected_parent=scratch_root.parent,
            expected_name_prefix=partial_prefix,
        )
        raise
    else:
        _closure_replay_remove_new_tree(
            path=partial_root,
            expected_parent=scratch_root.parent,
            expected_name_prefix=partial_prefix,
        )
    scratch_immutable = _closure_replay_immutable_digests(scratch_root)
    if scratch_immutable != source_immutable:
        raise ValueError("closure replay published immutable evidence drift")
    authority = ResultsFirstTraceClosureReplayAuthority(
        snapshot=snapshot,
        output_root=scratch_root,
    )
    trace_context = _TRACE_CONTEXT_BUILDER(
        closure_snapshot=snapshot,
        resolver=response_bank_resolver,
        output_root=snapshot_output_root,
    )
    result = _run_results_first_formal_subset(
        authority=authority,
        conditions=trace_conditions,
        output_root=scratch_root,
        transport=transport,
        real_transport=False,
        trace_context=trace_context,
        suite_id=snapshot.suite_id,
        resume=True,
    )
    if _closure_replay_immutable_digests(source_root) != source_immutable:
        raise ValueError("closure replay mutated immutable source evidence")
    if _closure_replay_immutable_digests(scratch_root) != scratch_immutable:
        raise ValueError("closure replay mutated immutable scratch evidence")
    if Path(str(getattr(result, "output_root", ""))).resolve(
        strict=False
    ) != scratch_root:
        raise ValueError("closure replay terminal output root drift")
    _validate_representative_terminal(
        terminal=result,
        coverage=snapshot.coverage,
        conditions=trace_conditions,
        expected_experiment_ids=trace_experiment_ids,
        require_zero_current_provider_calls=True,
    )
    return result


@dataclass(frozen=True, kw_only=True)
class ResultsFirstExecutionResult:
    status: str
    trace_terminal: object
    exp5_terminal: object
    expected_condition_count: int
    expected_root_count: int
    acquisition_current_provider_calls: int
    acquisition_current_spend: float | None
    acquisition_spend_missing_reason: str | None
    trace_current_provider_calls: int
    trace_current_spend: float | None
    trace_spend_missing_reason: str | None
    exp5_current_provider_calls: int
    exp5_current_spend: float | None
    exp5_spend_missing_reason: str | None
    total_current_provider_calls: int
    total_current_spend: float | None
    total_spend_missing_reason: str | None


# 兼容旧调用方；结果由共享 entrypoint 产生。
RepresentativeFullPlanSmokeServiceResult = ResultsFirstExecutionResult


@dataclass(frozen=True, kw_only=True)
class RepresentativeCurrentProviderUsage:
    provider_calls: int
    spend: float | None
    spend_missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.provider_calls < 0:
            raise ValueError("current provider calls must be non-negative")
        if self.spend is None:
            if not self.spend_missing_reason:
                raise ValueError("missing current spend requires a reason")
        elif self.spend < 0 or self.spend_missing_reason is not None:
            raise ValueError("current provider spend is inconsistent")


class RepresentativeSmokeExecutionError(ValueError):
    """执行中止时仍携带已经发生的 current provider accounting。"""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str,
        acquisition_usage: RepresentativeCurrentProviderUsage,
        trace_usage: RepresentativeCurrentProviderUsage,
        exp5_usage: RepresentativeCurrentProviderUsage,
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.acquisition_usage = acquisition_usage
        self.trace_usage = trace_usage
        self.exp5_usage = exp5_usage

    def to_summary(self) -> dict[str, object]:
        usages = (
            self.acquisition_usage,
            self.trace_usage,
            self.exp5_usage,
        )
        missing_reasons = tuple(
            usage.spend_missing_reason
            for usage in usages
            if usage.spend_missing_reason is not None
        )
        total_provider_calls = sum(usage.provider_calls for usage in usages)
        total_spend = (
            None
            if missing_reasons
            else sum(
                float(usage.spend)
                for usage in usages
                if usage.spend is not None
            )
        )
        return {
            "failure_kind": "representative_smoke_execution_rejected",
            "failure_stage": self.failure_stage,
            "provider_calls": total_provider_calls,
            "total_current_provider_calls": total_provider_calls,
            "acquisition_current_provider_calls": self.acquisition_usage.provider_calls,
            "acquisition_current_spend": self.acquisition_usage.spend,
            "acquisition_spend_missing_reason": (
                self.acquisition_usage.spend_missing_reason
            ),
            "trace_current_provider_calls": self.trace_usage.provider_calls,
            "trace_current_spend": self.trace_usage.spend,
            "trace_spend_missing_reason": self.trace_usage.spend_missing_reason,
            "exp5_current_provider_calls": self.exp5_usage.provider_calls,
            "exp5_current_spend": self.exp5_usage.spend,
            "exp5_spend_missing_reason": self.exp5_usage.spend_missing_reason,
            "total_current_spend": total_spend,
            "total_spend_missing_reason": (
                None if not missing_reasons else ";".join(missing_reasons)
            ),
        }


@dataclass(frozen=True, kw_only=True)
class RepresentativeAcquisitionStageResult:
    resolver: object
    usage: RepresentativeCurrentProviderUsage
    max_in_flight: int
    bundle: object | None = None


Delegate = Callable[[PipelineCommandRequest], Mapping[str, object] | Any]
ServiceInputFactory = Callable[[PipelineCommandRequest], object | None]


def _UTC_NOW() -> datetime:
    return datetime.now(timezone.utc)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route bounded EPD-027 paper-pipeline operations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--profile", required=True)
        return command_parser

    common("validate-profile")
    plan_bank = common("plan-bank")
    plan_bank.add_argument("--plan-bundle-root")

    provider_parsers: dict[str, argparse.ArgumentParser] = {}
    for name in _PROVIDER_SCOPES:
        command_parser = common(name)
        provider_parsers[name] = command_parser
        if name == "acquire-bank":
            command_parser.add_argument("--receipt", required=True)
        else:
            command_parser.add_argument("--receipt", required=True)
        command_parser.add_argument(
            "--allow-provider-calls",
            action="store_true",
            required=True,
        )
        mode = command_parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--new-run", action="store_true")
        mode.add_argument("--resume", action="store_true")
        command_parser.add_argument("--output-root", required=True)
        command_parser.add_argument("--plan-digest", required=True)
        command_parser.add_argument("--inventory-digest", required=True)
        command_parser.add_argument(
            "--budget-mode",
            choices=("bounded", "unlimited"),
            default="bounded",
        )
    provider_parsers["acquire-bank"].add_argument("--plan-bundle-root")
    provider_parsers["acquire-bank"].add_argument(
        "--supervised-no-response-closure-root"
    )
    provider_parsers["run-exp5-capability-smoke"].add_argument(
        "--results-first-scope-preparation-root"
    )
    provider_parsers["run-exp5-capability-smoke"].add_argument(
        "--local-ai-api-config"
    )

    audit = common("audit-bank")
    audit.add_argument("--external-bank-root", required=True)

    trace = common("run-trace")
    trace.add_argument("--external-bank-root", required=True)
    trace.add_argument("--output-root", required=True)
    trace.add_argument("--plan-bundle-root", required=True)
    trace.add_argument("--plan-digest", required=True)
    trace.add_argument("--inventory-digest", required=True)
    trace.add_argument("--full-bank-acquisition-receipt")
    trace.add_argument("--l3-online-check-receipt")
    trace.add_argument("--l3-online-check-root")

    render = common("render")
    render.add_argument("--output-root", required=True)

    replay = common("replay")
    replay.add_argument("--output-root", required=True)
    replay.add_argument("--replay-input-root", required=True)

    lineage = common("audit-cell-lineage")
    lineage.add_argument("--output-root", required=True)

    for name in (
        "validate-formal-execution-gate",
        "validate-paper-publication-gate",
    ):
        gate = common(name)
        gate.add_argument("--output-root", required=True)

    def results_first(name: str) -> argparse.ArgumentParser:
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--profile")
        command_parser.add_argument("--receipt")
        command_parser.add_argument("--output-root", required=True)
        command_parser.add_argument("--planning-artifact-root", required=True)
        command_parser.add_argument("--plan-bundle-root", required=True)
        command_parser.add_argument("--external-bank-root")
        mode = command_parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--new-run", action="store_true")
        mode.add_argument("--resume", action="store_true")
        command_parser.add_argument("--plan-only", action="store_true")
        command_parser.add_argument("--allow-provider-calls", action="store_true")
        command_parser.add_argument("--frozen-closure-snapshot-root")
        command_parser.add_argument("--frozen-prepared-inventory-root")
        command_parser.add_argument("--expected-preflight")
        command_parser.add_argument("--source-rebind-root")
        command_parser.add_argument("--expected-provider-budget-digest")
        command_parser.add_argument("--paid-reference-binding-root")
        command_parser.add_argument("--paid-launch-key-config")
        command_parser.add_argument("--paid-launch-attestation-root")
        command_parser.add_argument("--results-first-scope-preparation-root")
        command_parser.add_argument("--full-budget-approval-authority")
        command_parser.add_argument("--supervised-no-response-closure-root")
        return command_parser

    canonical = results_first("run-results-first")
    canonical.add_argument(
        "--selection",
        choices=(
            "full",
            "representative",
            "full_exp1_exp3_exp5",
            "representative_exp1_exp3_exp5",
        ),
        required=True,
    )
    representative = results_first("representative-full-plan-smoke")
    representative.set_defaults(selection="representative")

    metric_merge_audit = subparsers.add_parser(
        "audit-results-first-metric-merge"
    )
    metric_merge_audit.add_argument("--output-root", required=True)
    metric_merge_audit.add_argument(
        "--selection",
        choices=("representative_exp1_exp3_exp5",),
        required=True,
    )
    metric_merge_audit.add_argument("--audit-output-root", required=True)

    combined_render = subparsers.add_parser("render-results-first-combined")
    combined_render.add_argument("--output-root", required=True)
    combined_render.add_argument(
        "--selection",
        choices=("representative_exp1_exp3_exp5",),
        required=True,
    )
    combined_render.add_argument("--metric-merge-audit-root", required=True)
    combined_render.add_argument("--combined-output-root", required=True)
    return parser


def _load_receipt(path: str | Path) -> Mapping[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("paid receipt must be a JSON object")
    return value


_PROFILE_LOADER = load_paper_pipeline_profile
_RECEIPT_LOADER = _load_receipt
_RECEIPT_VALIDATOR = validate_paid_execution_receipt
_BUDGET_VALIDATOR = validate_provider_budget_mode


def _load_acquisition_bundle(path: str | Path):
    from tokenshare.experiments.paper_response_bank import (
        load_acquisition_plan_bundle,
    )

    return load_acquisition_plan_bundle(path)


_ACQUISITION_BUNDLE_LOADER = _load_acquisition_bundle


def _create_representative_bundle(path: str | Path, *, plan: object):
    from tokenshare.experiments.paper_response_bank import (
        create_representative_acquisition_plan_bundle,
    )

    return create_representative_acquisition_plan_bundle(path, plan=plan)


def _load_representative_bundle(path: str | Path, *, plan: object):
    from tokenshare.experiments.paper_response_bank import (
        load_representative_acquisition_plan_bundle,
    )

    return load_representative_acquisition_plan_bundle(path, plan=plan)


_CREATE_REPRESENTATIVE_BUNDLE = _create_representative_bundle
_LOAD_REPRESENTATIVE_BUNDLE = _load_representative_bundle


def prepare_representative_formal_bundle(
    *,
    bundle_root: str | Path,
    plan: object,
    resume: bool,
) -> object:
    """formal bundle 只允许 typed create/load，并始终绑定 fresh plan。"""

    from tokenshare.experiments.paper_response_bank import (
        RepresentativeUnifiedAcquisitionPlan,
    )

    if type(plan) is not RepresentativeUnifiedAcquisitionPlan:
        raise TypeError("representative formal bundle requires a typed fresh plan")
    loader = (
        _LOAD_REPRESENTATIVE_BUNDLE if resume else _CREATE_REPRESENTATIVE_BUNDLE
    )
    return loader(bundle_root, plan=plan)


def _build_formal_authority(**kwargs: object):
    from tokenshare.experiments.run_paper_experiments import (
        build_epd027_formal_service_authority,
    )

    return build_epd027_formal_service_authority(**kwargs)


_FORMAL_AUTHORITY_BUILDER = _build_formal_authority


def _paid_scope_binding(
    *,
    validation: PaidAuthorizationValidation,
    scope: str,
    authorized_plan_digest: str,
    profile_digest: str,
    budget_digest: str,
    inventory_digest: str,
    prompt_admission_profile_digest: str,
    output_root: str | Path,
):
    """把Task26已验证结果绑定到validator实际使用的scope authority。"""

    from tokenshare.experiments.paper_formal_gate import (
        PaidAuthorizationScopeAuthority,
        SelectedPaidAuthorizationBinding,
        selected_experiments_for_provider_scope,
    )

    selected = selected_experiments_for_provider_scope(scope)
    return SelectedPaidAuthorizationBinding(
        authority=PaidAuthorizationScopeAuthority(
            scope=scope,
            authorized_plan_digest=authorized_plan_digest,
            profile_digest=profile_digest,
            budget_digest=budget_digest,
            inventory_digest=inventory_digest,
            prompt_admission_profile_digest=prompt_admission_profile_digest,
            selected_experiments=selected,
            output_root_path_digest=output_root_path_digest(output_root),
        ),
        validation=validation,
    )


def _build_smoke_authority(**kwargs: object):
    from tokenshare.experiments.run_paper_experiments import (
        build_epd027_capability_smoke_service_authority,
    )

    return build_epd027_capability_smoke_service_authority(**kwargs)


_SMOKE_AUTHORITY_BUILDER = _build_smoke_authority


def _inject_exp5_capability_key(local_config_path: str) -> None:
    """只为已收据绑定的 Exp5 smoke 注入本地 SiliconFlow 密钥。"""

    from tokenshare.experiments.run_paper_experiments import _inject_exp5_v3_api_key

    _inject_exp5_v3_api_key(
        local_config_path=Path(local_config_path),
        provider_config_args=(
            "siliconflow=benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
        ),
    )


_EXP5_CAPABILITY_KEY_INJECTOR = _inject_exp5_capability_key


def _service_result(
    request: PipelineCommandRequest,
    *,
    status: object = "completed",
    provider_calls: int = 0,
    **digests: object,
) -> dict[str, object]:
    normalized_status = getattr(status, "value", status)
    return {
        "status": str(normalized_status),
        "plan_digest": request.plan_digest,
        "inventory_digest": request.inventory_digest,
        "evidence_class": request.evidence_class,
        "provider_calls": provider_calls,
        **digests,
    }


def _validate_profile_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    # profile_loader 已在 request 建立前完成权威 schema/digest 验证。
    return _service_result(request)


def _plan_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_online_checks import (
        freeze_paper_online_checks_plan,
    )

    plan = freeze_paper_online_checks_plan()
    return _service_result(
        request,
        plan_digest=plan.plan_digest,
        inventory_digest=request.inventory_digest,
    )


def _acquire_response_bank_after_supervised_no_response_closure(
    *,
    orchestrator: object,
    requests: Sequence[object],
    max_in_flight: int,
    resume: bool,
    supervised_no_response_closure_root: Path | None,
) -> object:
    """resume 时先零网络闭合已停 attempts，再派发尚未发生的 primary。"""

    if supervised_no_response_closure_root is not None:
        if not resume:
            raise ValueError("supervised no-response closure requires resume")
        from tokenshare.experiments.paper_response_bank import (
            apply_supervised_no_response_closure,
            load_supervised_no_response_closure_authority,
        )

        authority = load_supervised_no_response_closure_authority(
            supervised_no_response_closure_root
        )
        requests_by_id: dict[str, object] = {}
        for acquisition_request in requests:
            inventory_entry_id = str(
                acquisition_request.inventory_row.inventory_entry_id
            )
            if inventory_entry_id in requests_by_id:
                raise ValueError(
                    "supervised no-response resume requests contain duplicates"
                )
            requests_by_id[inventory_entry_id] = acquisition_request
        missing_target_ids = tuple(
            inventory_entry_id
            for inventory_entry_id in authority.target_inventory_entry_ids
            if inventory_entry_id not in requests_by_id
        )
        if missing_target_ids:
            raise ValueError(
                "supervised no-response resume requests have missing authority targets"
            )
        request_ids = tuple(requests_by_id)
        full_inventory_ids = tuple(
            row.inventory_entry_id for row in orchestrator.inventory_rows
        )
        if request_ids not in {
            authority.target_inventory_entry_ids,
            full_inventory_ids,
        }:
            raise ValueError(
                "supervised no-response resume requests are not exact targets or full inventory"
            )
        closure_requests = tuple(
            requests_by_id[inventory_entry_id]
            for inventory_entry_id in authority.target_inventory_entry_ids
        )
        receipt = apply_supervised_no_response_closure(
            closure_root=supervised_no_response_closure_root,
            orchestrator=orchestrator,
            requests=closure_requests,
        )
        if (
            type(receipt.provider_calls_made) is not int
            or receipt.provider_calls_made != 0
        ):
            raise ValueError("supervised no-response closure made provider calls")
    return orchestrator.acquire_all(requests, max_in_flight=max_in_flight)


def create_provider_zero_supervised_no_response_closure(
    *,
    frozen_snapshot_root: Path,
    frozen_prepared_inventory_root: Path,
    expected_preflight: Path,
    source_rebind_root: Path,
    plan_bundle_root: Path,
    planning_artifact_root: Path,
    paid_reference_binding_root: Path,
    paid_output_root: Path,
    stop_evidence_path: Path,
    fresh_closure_root: Path,
    selection: str,
    expected_provider_budget_digest: str,
) -> Mapping[str, object]:
    """从既有 paid output 只读构造 exact stopped-attempt closure authority。"""

    from tokenshare.experiments.paper_budget import ResultsFirstProviderBudgetAuthority
    from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionPlanBundle,
        ResponseBankAcquisitionOrchestrator,
        establish_results_first_acquisition_authorization,
        load_supervised_no_response_closure_authority,
        persist_supervised_no_response_closure_authority,
        results_first_response_bank_manifest_for_bundle,
    )

    if selection != "representative":
        raise ValueError("supervised no-response producer is representative-only")
    roots = tuple(
        Path(value).resolve(strict=False)
        for value in (
            frozen_snapshot_root,
            frozen_prepared_inventory_root,
            expected_preflight,
            source_rebind_root,
            plan_bundle_root,
            planning_artifact_root,
            paid_reference_binding_root,
            paid_output_root,
            stop_evidence_path,
            fresh_closure_root,
        )
    )
    closure_root = roots[-1]
    if closure_root.exists() or closure_root.is_symlink():
        raise ValueError("supervised no-response closure root must be fresh")
    for source in roots[:-1]:
        try:
            closure_root.relative_to(source)
            overlap = True
        except ValueError:
            try:
                source.relative_to(closure_root)
                overlap = True
            except ValueError:
                overlap = False
        if overlap:
            raise ValueError("supervised no-response closure paths overlap")
    authority = _PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER(
        paid_reference_binding_root=Path(paid_reference_binding_root),
        frozen_snapshot_root=Path(frozen_snapshot_root),
        frozen_prepared_inventory_root=Path(frozen_prepared_inventory_root),
        expected_preflight=Path(expected_preflight),
        source_rebind_root=Path(source_rebind_root),
        plan_bundle_root=Path(plan_bundle_root),
        planning_artifact_root=Path(planning_artifact_root),
        output_root=Path(paid_output_root),
        selection=selection,
        resume=True,
        expected_provider_budget_digest=expected_provider_budget_digest,
    )
    if (
        type(authority) is not ResultsFirstExecutionAuthority
        or type(authority.bundle) is not AcquisitionPlanBundle
        or type(authority.provider_budget_authority)
        is not ResultsFirstProviderBudgetAuthority
        or authority.provider_budget_authority.authority_digest
        != expected_provider_budget_digest
        or authority.output_root.resolve(strict=False)
        != Path(paid_output_root).resolve(strict=False)
        or authority.resume is not True
        or type(authority.provider_calls_made) is not int
        or authority.provider_calls_made != 0
    ):
        raise ValueError("supervised no-response paid authority drift")
    stop_evidence = _closure_json_object(
        Path(stop_evidence_path),
        label="supervised no-response stop evidence",
    )
    target_ids = stop_evidence.get("target_inventory_entry_ids")
    if (
        not isinstance(target_ids, list)
        or len(target_ids) != 2
        or any(not isinstance(value, str) or not value for value in target_ids)
        or len(set(target_ids)) != 2
    ):
        raise ValueError("official supervised closure requires exact two targets")
    bundle = authority.bundle
    requests_by_id = {
        request.inventory_row.inventory_entry_id: request
        for request in bundle.acquisition_requests
    }
    if len(requests_by_id) != len(bundle.acquisition_requests):
        raise ValueError("supervised closure bundle request identities duplicate")
    if any(target_id not in requests_by_id for target_id in target_ids):
        raise ValueError("supervised closure target is outside acquisition bundle")
    acquisition_root = Path(paid_output_root) / "acquisition"
    ledger_path = acquisition_root / "acquisition_budget.v1.sqlite3"

    class ExistingReadOnlyPaperBudgetLedger(PaperBudgetLedger):
        """只读复用已结算 ledger；禁止构造器、WAL 或 schema 写入。"""

        class _ClosingConnection(sqlite3.Connection):
            def __exit__(self, *args: object) -> bool:
                try:
                    return bool(super().__exit__(*args))
                finally:
                    self.close()

        def __init__(self, path: Path) -> None:
            candidate = Path(path)
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(
                    "existing read-only acquisition budget ledger is missing"
                ) from exc
            if not resolved.is_file() or candidate.is_symlink():
                raise ValueError(
                    "existing read-only acquisition budget ledger is missing"
                )
            sidecars = (
                Path(str(resolved) + "-wal"),
                Path(str(resolved) + "-shm"),
            )
            if any(path.exists() or path.is_symlink() for path in sidecars):
                raise ValueError(
                    "existing read-only acquisition budget ledger is not quiescent"
                )
            self.path = resolved
            self.limits = bundle.full_budget.to_limits()
            self.category_policy = None
            self.busy_timeout_ms = 100
            self.lock_retries = 0
            self.lock_retry_backoff_seconds = 0.0
            try:
                with self._connect() as connection:
                    quick_check = connection.execute("PRAGMA quick_check").fetchone()
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }
            except (OSError, sqlite3.DatabaseError) as exc:
                raise ValueError(
                    "existing read-only acquisition budget ledger is invalid"
                ) from exc
            required_tables = {
                "inventories",
                "inventory_rows",
                "reservations",
                "reacquisitions",
                "budget_category_policy",
            }
            if quick_check is None or quick_check[0] != "ok" or not required_tables.issubset(
                tables
            ):
                raise ValueError(
                    "existing read-only acquisition budget ledger is invalid"
                )

        def _connect(self) -> sqlite3.Connection:
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=ro&immutable=1",
                uri=True,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                factory=self._ClosingConnection,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only = ON")
                connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
                connection.execute("PRAGMA foreign_keys = ON")
            except BaseException:
                connection.close()
                raise
            return connection

        def _write(self, _operation: object) -> object:
            raise ValueError("read-only acquisition budget ledger cannot mutate")

    ledger = ExistingReadOnlyPaperBudgetLedger(ledger_path)
    authorization = establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=acquisition_root,
        output_mode="resume",
        allow_provider_calls=True,
    )
    manifest = results_first_response_bank_manifest_for_bundle(
        bundle,
        authorization,
    )
    class ProviderZeroTransportBomb:
        def tokenshare_transport_for_provider(self, _provider_family: str):
            return self

        def post_chat_completion(self, **_kwargs: object) -> object:
            raise AssertionError("provider-zero closure cannot dispatch transport")

    orchestrator = ResponseBankAcquisitionOrchestrator(
        output_root=acquisition_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        facility_authorization=authorization,
        invocation_mode="resume",
        transport=ProviderZeroTransportBomb(),
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("provider-zero closure cannot resolve a secret")
        ),
        now_epoch=int(_UTC_NOW().timestamp()),
    )
    requests = tuple(requests_by_id[target_id] for target_id in target_ids)
    closure = persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=orchestrator,
        requests=requests,
        target_inventory_entry_ids=tuple(target_ids),
        stop_evidence=stop_evidence,
    )
    if (
        load_supervised_no_response_closure_authority(closure_root) != closure
        or closure.provider_calls_made != 0
    ):
        raise ValueError("supervised no-response closure strict reload drift")
    return {
        "schema_version": "tokenshare.provider_zero_supervised_no_response_terminal.v1",
        "status": "READY_PROVIDER_ZERO_SUPERVISED_NO_RESPONSE",
        "authority_digest": closure.authority_digest,
        "stop_evidence_digest": closure.stop_evidence_digest,
        "target_inventory_entry_ids": list(closure.target_inventory_entry_ids),
        "provider_calls": 0,
    }


def _acquire_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_response_bank import (
        ResponseBankAcquisitionOrchestrator,
        finalize_acquisition_child_bank,
    )

    value = request._service_input
    if not isinstance(value, AcquisitionServiceInput):
        raise ValueError("acquire-bank requires typed acquisition service input")
    if value.scope != request.scope:
        raise ValueError("acquisition service scope mismatch")
    arguments = dict(value.orchestrator_arguments)
    if Path(str(arguments.get("output_root"))) != request.output_root:
        raise ValueError("acquisition service output root mismatch")
    if arguments.get("inventory_digest") != request.inventory_digest:
        raise ValueError("acquisition service inventory digest mismatch")
    acquisition_requests = tuple(value.acquisition_requests)
    if not acquisition_requests:
        raise ValueError("acquisition service requests are empty")
    orchestrator = ResponseBankAcquisitionOrchestrator(**arguments)
    batch = _acquire_response_bank_after_supervised_no_response_closure(
        orchestrator=orchestrator,
        requests=acquisition_requests,
        max_in_flight=value.max_in_flight,
        resume=(arguments.get("invocation_mode") == "resume"),
        supervised_no_response_closure_root=(
            value.supervised_no_response_closure_root
        ),
    )
    results = tuple(batch.results)
    provider_calls = sum(
        1 for result in results if bool(getattr(result, "transport_invoked", False))
    )
    if value.bundle is not None or value.manifest is not None:
        if value.bundle is None or value.manifest is None:
            raise ValueError("acquisition child-bank authority is partial")
        finalize_acquisition_child_bank(
            orchestrator=orchestrator,
            bundle=value.bundle,
            manifest=value.manifest,
            batch_result=batch,
        )
    status = "completed" if batch.status == "complete" else batch.status
    return _service_result(
        request,
        status=status,
        provider_calls=provider_calls,
    )


def _formal_suite_adapter(
    request: PipelineCommandRequest,
    *,
    trace_required: bool,
) -> FormalSuiteServiceResult:
    from tokenshare.experiments.paper_formal_runner import (
        execute_paper_formal_suite,
    )

    value = request._service_input
    if not isinstance(value, FormalSuiteServiceInput):
        raise ValueError(f"{request.command} requires typed formal-suite service input")
    if value.scope != request.scope:
        raise ValueError("formal-suite service scope mismatch")
    arguments = dict(value.keyword_arguments)
    if Path(str(arguments.get("output_root"))) != request.output_root:
        raise ValueError("formal-suite service output root mismatch")
    real_transport = arguments.get("real_transport")
    if trace_required:
        if real_transport is not False or arguments.get("trace_context") is None:
            raise ValueError("trace service requires trace context and no real transport")
    elif real_transport is not True or request.provider_authorization is None:
        raise ValueError("online service requires authorized real transport")
    result = execute_paper_formal_suite(**arguments)
    from tokenshare.experiments.paper_models import PaperSuiteResult

    if not isinstance(result, PaperSuiteResult):
        raise TypeError("official formal service returned invalid terminal result")
    return FormalSuiteServiceResult(
        public_result=_service_result(
            request,
            status=result.status,
            provider_calls=int(result.provider_attempt_count),
        ),
        terminal_result=result,
    )


def _run_trace_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=True)


def _run_online_checks_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _run_exp1_online_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _run_exp5_capability_smoke_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_smoke import execute_paper_smoke_suite

    value = request._service_input
    if not isinstance(value, FormalSuiteServiceInput):
        raise ValueError(
            "run-exp5-capability-smoke requires typed smoke-suite service input"
        )
    if value.scope != request.scope:
        raise ValueError("smoke-suite service scope mismatch")
    arguments = dict(value.keyword_arguments)
    execution_plan = arguments.get("execution_plan")
    if Path(str(getattr(execution_plan, "output_root", None))) != request.output_root:
        raise ValueError("smoke-suite service output root mismatch")
    if (
        arguments.get("real_transport") is not True
        or request.provider_authorization is None
    ):
        raise ValueError("smoke online service requires authorized real transport")
    result = execute_paper_smoke_suite(**arguments)
    return _service_result(
        request,
        status=getattr(result, "status", "completed"),
        provider_calls=int(getattr(result, "provider_attempt_count", 0)),
    )


def _run_exp5_online_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _audit_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    resolver = request.external_bank_resolver
    if resolver is None:
        raise ValueError("audit-bank requires an external bank resolver")
    index = resolver.open().index
    return _service_result(
        request,
        inventory_digest=index.manifest.inventory_digest,
    )


_RESULTS_FIRST_METRIC_MERGE_PARTITIONS = (
    (
        "exp1_real_ai_feasibility",
        "exp1",
        12,
        "real_model_trace_protocol_run",
    ),
    (
        "exp2_real_ai_scalability",
        "exp2",
        6,
        "real_model_trace_protocol_run",
    ),
    (
        "exp3_real_ai_fault_recovery",
        "exp3",
        81,
        "real_model_trace_protocol_run",
    ),
    (
        "exp5_real_ai_model_endpoint_comparison",
        "exp5",
        16,
        "online_real_provider",
    ),
)
_RESULTS_FIRST_METRIC_MERGE_SELECTION = "representative_exp1_exp3_exp5"
_RESULTS_FIRST_METRIC_MERGE_AUDIT_NAME = (
    "results_first_combined_metric_merge.v1.json"
)
_RESULTS_FIRST_COMBINED_RENDER_AUDIT_NAME = (
    "results_first_combined_metrics_input.v1.json"
)
_RESULTS_FIRST_COMBINED_RENDER_AUDIT_SCHEMA = (
    "tokenshare.results_first_combined_metrics_input.v1"
)


def _metric_merge_required_digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
    ):
        raise ValueError(f"results-first metric merge {label} digest is invalid")
    return value


def _metric_merge_root_id(
    *,
    experiment_id: object,
    condition_id: object,
    case_id: object,
    repeat_id: object,
) -> str:
    if (
        not isinstance(experiment_id, str)
        or not experiment_id
        or not isinstance(condition_id, str)
        or not condition_id
        or not isinstance(case_id, str)
        or not case_id
        or isinstance(repeat_id, bool)
        or not isinstance(repeat_id, int)
        or repeat_id < 0
    ):
        raise ValueError("results-first metric merge root identity is invalid")
    return "paper-direct-root:" + _closure_digest(
        {
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "case_id": case_id,
            "repeat_id": repeat_id,
        }
    )


def _metric_merge_expected_root_inventory(
    snapshot: object,
) -> tuple[tuple[str, ...], dict[str, int], dict[str, str]]:
    """从 frozen results-first closure snapshot 复算固定的 115-root 分母。"""

    coverage = getattr(snapshot, "coverage", None)
    if coverage is None:
        raise ValueError("results-first metric merge closure snapshot is incomplete")
    if (
        getattr(snapshot, "suite_id", None) != "results_first_exp1_exp3_trace"
        or getattr(snapshot, "provider_calls_made", None) != 0
        or getattr(coverage, "selection_kind", None)
        != _RESULTS_FIRST_METRIC_MERGE_SELECTION
        or getattr(coverage, "condition_count", None) != 115
        or getattr(coverage, "root_run_count", None) != 115
        or getattr(coverage, "provider_calls_made", None) != 0
    ):
        raise ValueError("results-first metric merge closure snapshot drift")
    _metric_merge_required_digest(
        getattr(snapshot, "snapshot_digest", None),
        label="snapshot",
    )
    source_validation_digest = _metric_merge_required_digest(
        getattr(snapshot, "source_validation_digest", None),
        label="source validation",
    )
    coverage_digest = _metric_merge_required_digest(
        getattr(coverage, "coverage_digest", None),
        label="coverage",
    )
    conditions = tuple(getattr(coverage, "conditions", ()))
    roots = tuple(getattr(coverage, "roots", ()))
    expected_experiment_ids = tuple(
        experiment_id
        for experiment_id, _partition, _count, _evidence_class in (
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS
        )
    )
    observed_experiment_ids = tuple(
        dict.fromkeys(
            getattr(condition, "experiment_id", None) for condition in conditions
        )
    )
    if (
        len(conditions) != 115
        or len(roots) != 115
        or observed_experiment_ids != expected_experiment_ids
    ):
        raise ValueError("results-first metric merge closure partition is invalid")
    expected_partition = {
        partition: count
        for _experiment_id, partition, count, _evidence_class in (
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS
        )
    }
    actual_partition = {partition: 0 for partition in expected_partition}
    partition_by_experiment = {
        experiment_id: partition
        for experiment_id, partition, _count, _evidence_class in (
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS
        )
    }
    root_ids: list[str] = []
    for root in roots:
        condition = getattr(root, "condition", None)
        experiment_id = getattr(condition, "experiment_id", None)
        partition = partition_by_experiment.get(experiment_id)
        if partition is None:
            raise ValueError("results-first metric merge includes an unselected experiment")
        root_ids.append(
            _metric_merge_root_id(
                experiment_id=experiment_id,
                condition_id=getattr(condition, "condition_id", None),
                case_id=getattr(root, "case_id", None),
                repeat_id=getattr(root, "repeat_id", None),
            )
        )
        actual_partition[partition] += 1
    if actual_partition != expected_partition or len(set(root_ids)) != len(root_ids):
        raise ValueError("results-first metric merge fixed partition is invalid")
    return tuple(root_ids), actual_partition, {
        "snapshot_digest": str(getattr(snapshot, "snapshot_digest")),
        "source_validation_digest": source_validation_digest,
        "coverage_digest": coverage_digest,
    }


def _metric_merge_protected_replay_input(
    *,
    suite_root: Path,
) -> tuple[object, dict[str, object]]:
    """通过 official loader 读取 protected inputs，并记录可审计 handle/descriptor。"""

    from tokenshare.experiments.paper_formal_runner import (
        load_paper_traceability_replay_input_root,
        load_paper_traceability_replay_inputs,
    )

    protected = load_paper_traceability_replay_input_root(suite_root)
    loaded = load_paper_traceability_replay_inputs(suite_root)
    if not all(
        isinstance(getattr(loaded, name, None), Mapping)
        for name in ("direct", "current", "source")
    ):
        raise ValueError("results-first metric merge protected inputs are invalid")
    descriptor_digest = _metric_merge_required_digest(
        getattr(protected, "descriptor_digest", None),
        label="protected descriptor",
    )
    descriptor_path = getattr(protected, "descriptor_path", None)
    root_path = getattr(protected, "root_path", None)
    classification = getattr(protected, "classification", None)
    if (
        not isinstance(descriptor_path, Path)
        or not isinstance(root_path, Path)
        or classification != "normal_formal_artifact_root"
    ):
        raise ValueError("results-first metric merge protected handle is invalid")
    suite_manifest = _closure_json_object(
        suite_root / "suite_manifest.json",
        label="results-first metric merge suite manifest",
    )
    ref = suite_manifest.get("traceability_replay_input_root_ref")
    if (
        not isinstance(ref, Mapping)
        or ref.get("schema_version")
        != "tokenshare.paper_traceability_replay_input_ref.v1"
        or not isinstance(ref.get("handle_path"), str)
        or not ref.get("handle_path")
        or _metric_merge_required_digest(ref.get("handle_digest"), label="protected handle")
        != ref.get("handle_digest")
        or ref.get("descriptor_path") != descriptor_path.as_posix()
        or ref.get("descriptor_digest") != descriptor_digest
    ):
        raise ValueError("results-first metric merge protected handle binding is invalid")
    return loaded, {
        "suite_root": suite_root.as_posix(),
        "handle": {
            "path": ref["handle_path"],
            "digest": ref["handle_digest"],
            "root_path": root_path.as_posix(),
        },
        "descriptor": {
            "path": descriptor_path.as_posix(),
            "digest": descriptor_digest,
            "classification": classification,
        },
    }


def _metric_merge_direct_rows(value: object) -> tuple[object, ...]:
    from tokenshare.experiments.paper_formal_evidence import (
        _typed_direct_results_in_inputs,
    )
    from tokenshare.experiments.paper_formal_runner import _DIRECT_METRIC_INPUT_KEYS

    if not isinstance(value, Mapping) or set(value) != set(_DIRECT_METRIC_INPUT_KEYS):
        raise ValueError("results-first metric merge direct input inventory is invalid")
    rows = tuple(_typed_direct_results_in_inputs(value))
    if not rows:
        raise ValueError("results-first metric merge direct input is empty")
    return rows


def _metric_merge_validate_observed_partition(
    *,
    rows: Sequence[object],
    group: str,
    expected_root_ids: Sequence[str],
) -> dict[str, int]:
    from tokenshare.experiments.paper_direct_results import PaperDirectRootResult

    allowed = {
        "trace": {
            experiment_id: evidence_class
            for experiment_id, _partition, _count, evidence_class in (
                _RESULTS_FIRST_METRIC_MERGE_PARTITIONS[:-1]
            )
        },
        "exp5": {
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS[-1][0]: (
                _RESULTS_FIRST_METRIC_MERGE_PARTITIONS[-1][3]
            )
        },
    }.get(group)
    if allowed is None:
        raise ValueError("results-first metric merge group is invalid")
    partition_by_experiment = {
        experiment_id: partition
        for experiment_id, partition, _count, _evidence_class in (
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS
        )
    }
    counts = {
        partition: 0
        for _experiment_id, partition, _count, _evidence_class in (
            _RESULTS_FIRST_METRIC_MERGE_PARTITIONS
        )
    }
    expected_set = set(expected_root_ids)
    for row in rows:
        if type(row) is not PaperDirectRootResult:
            raise ValueError("results-first metric merge direct row type is invalid")
        experiment_id = row.experiment_id
        if experiment_id not in allowed or row.evidence_class != allowed[experiment_id]:
            raise ValueError("results-first metric merge evidence class partition is invalid")
        if row.preregistered_root_run_id not in expected_set:
            raise ValueError("results-first metric merge observed root is outside snapshot")
        if row.preregistered_root_run_id != _metric_merge_root_id(
            experiment_id=experiment_id,
            condition_id=row.condition_id,
            case_id=row.case_id,
            repeat_id=row.repeat_id,
        ):
            raise ValueError("results-first metric merge direct root identity drift")
        counts[partition_by_experiment[experiment_id]] += 1
    return counts


def _audit_results_first_metric_merge(
    *,
    output_root: Path,
    audit_output_root: Path,
    selection: str,
) -> dict[str, object]:
    """合并前只读验证 trace/Exp5 两个 protected closure 的固定 115-root 分母。"""

    from tokenshare.experiments.paper_formal_runner import (
        merge_canonical_metric_input_roots,
    )

    output = output_root.resolve(strict=False)
    audit = audit_output_root.resolve(strict=False)
    if (
        selection != _RESULTS_FIRST_METRIC_MERGE_SELECTION
        or not output.is_dir()
        or audit == output
        or audit.parent != output.parent
        or audit.exists()
        or audit.is_symlink()
    ):
        raise ValueError("results-first metric merge audit topology is invalid")
    snapshot = load_results_first_closure_replay_snapshot(output_root=output)
    expected_root_ids, expected_partition, source_binding = (
        _metric_merge_expected_root_inventory(snapshot)
    )
    trace_inputs, trace_binding = _metric_merge_protected_replay_input(
        suite_root=output / "exp1-exp3-trace"
    )
    exp5_inputs, exp5_binding = _metric_merge_protected_replay_input(
        suite_root=output / "exp5-online"
    )
    trace_direct = getattr(trace_inputs, "direct", None)
    exp5_direct = getattr(exp5_inputs, "direct", None)
    trace_rows = _metric_merge_direct_rows(trace_direct)
    exp5_rows = _metric_merge_direct_rows(exp5_direct)
    # 必须实际调用 canonical merge；不能以组内计数替代跨 closure 重复/缺失检查。
    merge_canonical_metric_input_roots(
        (trace_direct, exp5_direct),
        expected_root_ids=expected_root_ids,
    )
    trace_partition = _metric_merge_validate_observed_partition(
        rows=trace_rows,
        group="trace",
        expected_root_ids=expected_root_ids,
    )
    exp5_partition = _metric_merge_validate_observed_partition(
        rows=exp5_rows,
        group="exp5",
        expected_root_ids=expected_root_ids,
    )
    observed_partition = {
        partition: trace_partition[partition] + exp5_partition[partition]
        for partition in expected_partition
    }
    observed_root_ids = tuple(
        row.preregistered_root_run_id for row in (*trace_rows, *exp5_rows)
    )
    if observed_partition != expected_partition or set(observed_root_ids) != set(
        expected_root_ids
    ):
        raise ValueError("results-first metric merge observed partition is invalid")
    body = {
        "schema_version": "tokenshare.results_first_combined_metric_merge.v1",
        "status": "verified",
        "selection": selection,
        "provider_calls_made": 0,
        "source_binding": source_binding,
        "protected_replay_inputs": {
            "trace": trace_binding,
            "exp5": exp5_binding,
        },
        "expected_root_count": len(expected_root_ids),
        "observed_root_count": len(observed_root_ids),
        "expected_root_ids_digest": _closure_digest(tuple(expected_root_ids)),
        # merge 已验证 observed 集合与 frozen 分母完全相同；审计 digest 固定使用
        # frozen root 顺序，避免 closure 内 pickle/映射排序影响唯一的分母身份。
        "observed_root_ids_digest": _closure_digest(tuple(expected_root_ids)),
        "root_partition": {
            "expected": expected_partition,
            "observed": observed_partition,
        },
    }
    audit.mkdir()
    audit_path = audit / _RESULTS_FIRST_METRIC_MERGE_AUDIT_NAME
    content = _closure_snapshot_json_bytes(body)
    _atomic_write_once_bytes(audit_path, content)
    return {
        "status": "verified",
        "selection": selection,
        "provider_calls": 0,
        "source_validation_digest": source_binding["source_validation_digest"],
        "source_snapshot_digest": source_binding["snapshot_digest"],
        "coverage_digest": source_binding["coverage_digest"],
        "root_run_count": len(expected_root_ids),
        "metric_merge_audit_path": audit_path.as_posix(),
        "metric_merge_audit_digest": "sha256:" + sha256(content).hexdigest(),
    }


def _audit_results_first_metric_merge_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    value = request._service_input
    if not isinstance(value, ResultsFirstMetricMergeAuditServiceInput):
        raise ValueError("results-first metric merge audit requires typed service input")
    if request.output_root != value.output_root:
        raise ValueError("results-first metric merge audit output root mismatch")
    return _audit_results_first_metric_merge(
        output_root=value.output_root,
        audit_output_root=value.audit_output_root,
        selection=value.selection,
    )


def _combined_render_output_refs(
    *,
    output_root: Path,
    refs: object,
    label: str,
    digest_key: str,
) -> tuple[dict[str, object], ...]:
    """只接受 publication root 内、带 digest 的既有 derived artifact refs。"""

    if not isinstance(refs, (tuple, list)) or not refs:
        raise ValueError(f"combined render {label} output refs are invalid")
    checked: list[dict[str, object]] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, Mapping):
            raise ValueError(f"combined render {label} output ref is invalid")
        path = _closure_relative_file(
            root=output_root,
            value=ref.get("path"),
            label=f"combined render {label} output ref",
        )
        digest = _metric_merge_required_digest(
            ref.get(digest_key),
            label=f"combined render {label} output ref",
        )
        if path.resolve(strict=False) == output_root.resolve(strict=False):
            raise ValueError(f"combined render {label} output ref is invalid")
        checked.append(
            {
                **dict(ref),
                "path": path.relative_to(output_root).as_posix(),
                digest_key: digest,
            }
        )
    if len({(ref["path"], ref[digest_key]) for ref in checked}) != len(checked):
        raise ValueError(f"combined render {label} output refs are ambiguous")
    return tuple(checked)


def _load_verified_results_first_combined_metric_merge_audit(
    *,
    output_root: Path,
    metric_merge_audit_root: Path,
    combined_output_root: Path,
    selection: str,
) -> tuple[tuple[str, ...], dict[str, object], str, Path]:
    """在 recompute 前复验 immutable 115-root merge audit 与两个 protected handle。"""

    output = output_root.resolve(strict=False)
    audit_root = metric_merge_audit_root.resolve(strict=False)
    publication = combined_output_root.resolve(strict=False)
    if (
        selection != _RESULTS_FIRST_METRIC_MERGE_SELECTION
        or not output.is_dir()
        or audit_root == output
        or audit_root.parent != output.parent
        or not audit_root.is_dir()
        or audit_root.is_symlink()
        or publication == output
        or publication.parent != output.parent
        or publication.exists()
        or publication.is_symlink()
    ):
        raise ValueError("results-first combined render topology is invalid")

    audit_path = audit_root / _RESULTS_FIRST_METRIC_MERGE_AUDIT_NAME
    body = _closure_json_object(
        audit_path,
        label="results-first combined metric merge audit",
    )
    if set(body) != {
        "schema_version",
        "status",
        "selection",
        "provider_calls_made",
        "source_binding",
        "protected_replay_inputs",
        "expected_root_count",
        "observed_root_count",
        "expected_root_ids_digest",
        "observed_root_ids_digest",
        "root_partition",
    }:
        raise ValueError("results-first combined metric merge audit schema is invalid")
    if (
        body.get("schema_version")
        != "tokenshare.results_first_combined_metric_merge.v1"
        or body.get("status") != "verified"
        or body.get("selection") != selection
        or body.get("provider_calls_made") != 0
    ):
        raise ValueError("results-first combined metric merge audit status is invalid")

    snapshot = load_results_first_closure_replay_snapshot(output_root=output)
    expected_root_ids, expected_partition, source_binding = (
        _metric_merge_expected_root_inventory(snapshot)
    )
    expected_digest = _closure_digest(tuple(expected_root_ids))
    if (
        body.get("source_binding") != source_binding
        or body.get("expected_root_count") != len(expected_root_ids)
        or body.get("observed_root_count") != len(expected_root_ids)
        or body.get("expected_root_ids_digest") != expected_digest
        or _metric_merge_required_digest(
            body.get("observed_root_ids_digest"),
            label="observed root ids",
        )
        != expected_digest
    ):
        raise ValueError("results-first combined metric merge audit binding is invalid")
    partition = body.get("root_partition")
    if (
        not isinstance(partition, Mapping)
        or set(partition) != {"expected", "observed"}
        or partition.get("expected") != expected_partition
        or partition.get("observed") != expected_partition
    ):
        raise ValueError("results-first combined metric merge audit partition is invalid")

    protected = body.get("protected_replay_inputs")
    if not isinstance(protected, Mapping) or set(protected) != {"trace", "exp5"}:
        raise ValueError("results-first combined metric merge audit references are invalid")
    trace_binding = _metric_merge_protected_replay_input(
        suite_root=output / "exp1-exp3-trace"
    )[1]
    exp5_binding = _metric_merge_protected_replay_input(
        suite_root=output / "exp5-online"
    )[1]
    if protected != {"trace": trace_binding, "exp5": exp5_binding}:
        raise ValueError("results-first combined metric merge audit reference drift")

    audit_digest = "sha256:" + sha256(audit_path.read_bytes()).hexdigest()
    return tuple(expected_root_ids), body, audit_digest, audit_path


def _render_results_first_combined(
    *,
    output_root: Path,
    metric_merge_audit_root: Path,
    combined_output_root: Path,
    selection: str,
) -> dict[str, object]:
    """严格离线复水两个 closure、渲染 report，并写入 write-once 输入审计。"""

    from tokenshare.experiments.paper_formal_report import (
        generate_paper_formal_report,
    )
    from tokenshare.experiments.paper_formal_runner import (
        recompute_paper_formal_metrics_from_runner_input_roots,
    )
    from tokenshare.experiments.paper_metric_contract import (
        load_paper_metric_contract,
    )

    expected_root_ids, merge_audit, merge_audit_digest, merge_audit_path = (
        _load_verified_results_first_combined_metric_merge_audit(
            output_root=output_root,
            metric_merge_audit_root=metric_merge_audit_root,
            combined_output_root=combined_output_root,
            selection=selection,
        )
    )
    publication = combined_output_root.resolve(strict=False)
    metrics = recompute_paper_formal_metrics_from_runner_input_roots(
        publication_root=publication,
        trace_suite_root=(output_root / "exp1-exp3-trace").resolve(strict=False),
        exp5_suite_root=(output_root / "exp5-online").resolve(strict=False),
        expected_root_ids=expected_root_ids,
    )
    if getattr(metrics, "provider_calls", None) != 0:
        raise ValueError("results-first combined metric recompute attempted provider calls")
    metrics_digest = _metric_merge_required_digest(
        getattr(metrics, "metrics_digest", None),
        label="combined metrics",
    )
    metrics_output_refs = _combined_render_output_refs(
        output_root=publication,
        refs=getattr(metrics, "output_refs", None),
        label="metrics",
        digest_key="content_digest",
    )
    report = generate_paper_formal_report(
        output_root=publication,
        metrics=metrics,
        contract=load_paper_metric_contract(),
    )
    paper_eligible = getattr(report, "paper_eligible", None)
    if type(paper_eligible) is not bool:
        raise ValueError("results-first combined report eligibility is invalid")
    report_output_refs = _combined_render_output_refs(
        output_root=publication,
        refs=getattr(report, "artifact_refs", None),
        label="report",
        digest_key="content_hash",
    )
    report_manifest = _closure_json_object(
        publication / "audit" / "paper_formal_report_manifest.v2.json",
        label="results-first combined report manifest",
    )
    if report_manifest.get("provider_calls") != 0:
        raise ValueError("results-first combined report provider accounting is invalid")
    input_audit = {
        "schema_version": _RESULTS_FIRST_COMBINED_RENDER_AUDIT_SCHEMA,
        "status": "verified",
        "selection": selection,
        "provider_calls_made": 0,
        "metric_merge_audit": {
            "path": merge_audit_path.as_posix(),
            "digest": merge_audit_digest,
        },
        "protected_replay_inputs": merge_audit["protected_replay_inputs"],
        "metrics_digest": metrics_digest,
        "metrics_output_refs": list(metrics_output_refs),
        "report_output_refs": list(report_output_refs),
        "paper_eligible": paper_eligible,
    }
    audit_path = publication / "audit" / _RESULTS_FIRST_COMBINED_RENDER_AUDIT_NAME
    _atomic_write_once_bytes(audit_path, _closure_snapshot_json_bytes(input_audit))
    return {
        "status": "verified",
        "selection": selection,
        "provider_calls": 0,
        "root_run_count": len(expected_root_ids),
        "metric_merge_audit_path": merge_audit_path.as_posix(),
        "metric_merge_audit_digest": merge_audit_digest,
        "metrics_digest": metrics_digest,
        "paper_eligible": paper_eligible,
        "combined_metrics_input_path": audit_path.as_posix(),
    }


def _render_results_first_combined_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    value = request._service_input
    if not isinstance(value, ResultsFirstCombinedRenderServiceInput):
        raise ValueError("results-first combined render requires typed service input")
    if request.output_root != value.output_root:
        raise ValueError("results-first combined render output root mismatch")
    return _render_results_first_combined(
        output_root=value.output_root,
        metric_merge_audit_root=value.metric_merge_audit_root,
        combined_output_root=value.combined_output_root,
        selection=value.selection,
    )


def _render_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_report import (
        generate_paper_formal_report,
    )

    value = request._service_input
    if not isinstance(value, ReportRenderServiceInput):
        raise ValueError("render requires typed formal-metrics service input")
    result = generate_paper_formal_report(
        output_root=request.output_root,
        metrics=value.metrics,
        contract=value.contract,
    )
    return _service_result(request, status=getattr(result, "status", "completed"))


def _replay_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_runner import (
        load_paper_traceability_replay_input_root,
        recompute_paper_traceability_replay,
    )

    if request.replay_input_root is None:
        raise ValueError("replay requires a runner replay-input root")
    protected_root = load_paper_traceability_replay_input_root(
        request.replay_input_root
    )
    result = recompute_paper_traceability_replay(
        output_root=request.output_root,
        replay_input_root=protected_root,
    )
    return _service_result(
        request,
        provider_calls=int(getattr(result, "provider_calls", 0)),
    )


def _audit_cell_lineage_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_traceability import write_cell_lineage_audit

    value = request._service_input
    if not isinstance(value, CellLineageAuditServiceInput):
        raise ValueError("audit-cell-lineage requires typed observation service input")
    write_cell_lineage_audit(
        output_root=request.output_root,
        contract=value.contract,
        observations=tuple(value.observations),
    )
    return _service_result(request)


def _validate_formal_execution_gate_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_gate import formal_execution_gate

    value = request._service_input
    if not isinstance(value, FormalExecutionGateServiceInput):
        raise ValueError(
            "validate-formal-execution-gate requires typed prerequisite authority"
        )
    return formal_execution_gate(value.selection, value.prerequisites).to_dict()


def _validate_paper_publication_gate_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_gate import paper_publication_gate

    value = request._service_input
    if not isinstance(value, PaperPublicationGateServiceInput):
        raise ValueError(
            "validate-paper-publication-gate requires typed terminal evidence"
        )
    return paper_publication_gate(
        value.selection, value.terminal_evidence
    ).to_dict()


def _representative_full_plan_smoke_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    value = request._service_input
    if not isinstance(value, ResultsFirstServiceInput):
        raise ValueError("results-first execution requires typed service input")
    return _execute_results_first_service_input(request=request, value=value)


_AUTHORITATIVE_SERVICE_ADAPTERS: dict[str, Delegate] = {
    "validate-profile": _validate_profile_adapter,
    "plan-bank": _plan_bank_adapter,
    "acquire-bank": _acquire_bank_adapter,
    "audit-bank": _audit_bank_adapter,
    "run-trace": _run_trace_adapter,
    "run-online-checks": _run_online_checks_adapter,
    "run-exp1-online": _run_exp1_online_adapter,
    "run-exp5-capability-smoke": _run_exp5_capability_smoke_adapter,
    "run-exp5-online": _run_exp5_online_adapter,
    "render": _render_adapter,
    "replay": _replay_adapter,
    "audit-cell-lineage": _audit_cell_lineage_adapter,
    "validate-formal-execution-gate": _validate_formal_execution_gate_adapter,
    "validate-paper-publication-gate": _validate_paper_publication_gate_adapter,
    "run-results-first": _representative_full_plan_smoke_adapter,
    "representative-full-plan-smoke": _representative_full_plan_smoke_adapter,
    "audit-results-first-metric-merge": _audit_results_first_metric_merge_adapter,
    "render-results-first-combined": _render_results_first_combined_adapter,
}


def _no_service_input(_request: PipelineCommandRequest) -> None:
    return None


def _provided_gate_service_input(request: PipelineCommandRequest) -> object:
    if request._service_input is None:
        raise ValueError("paper gate typed service input is missing")
    return request._service_input


def _acquisition_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> AcquisitionServiceInput:
    if request.plan_bundle_root is None:
        raise ValueError("acquire-bank requires --plan-bundle-root")
    if request.output_root is None:
        raise ValueError("acquire-bank requires an output root")
    return _finish_acquisition_service_input_from_persisted_authorities(request)


def _execute_formal_suite(**kwargs: object):
    from tokenshare.experiments.paper_formal_runner import (
        execute_paper_formal_suite,
    )

    return execute_paper_formal_suite(**kwargs)


def _build_trace_context(**kwargs: object):
    authority = kwargs.pop("authority", None)
    closure_snapshot = kwargs.pop("closure_snapshot", None)
    if authority is not None and closure_snapshot is not None:
        raise TypeError("trace routing authority is ambiguous")
    if authority is not None:
        from tokenshare.experiments.paper_exp1_trace_reuse import (
            build_results_first_trace_context_router,
        )
        if type(authority) is not ResultsFirstExecutionAuthority:
            raise TypeError("typed results-first authority is required for trace routing")
        resolver = kwargs.pop("resolver")
        output_root = Path(kwargs.pop("output_root"))
        if kwargs:
            raise TypeError("unexpected results-first trace router arguments")
        replay_authority = _RESULTS_FIRST_REPLAY_SEMANTIC_AUTHORITY_BUILDER(
            authority=authority,
            output_root=output_root,
        )
        return build_results_first_trace_context_router(
            resolver=resolver,
            source_exp1_inventory_plan=authority.bundle.semantic_inventory_plan,
            replay_semantic_authority=replay_authority,
            target_roots=authority.coverage.roots,
            exp2_projection_root=output_root / "exp2-projected-response-bank",
        )
    if closure_snapshot is not None:
        from tokenshare.experiments.paper_exp1_trace_reuse import (
            build_results_first_trace_context_router,
        )

        if type(closure_snapshot) is not ResultsFirstClosureReplaySnapshot:
            raise TypeError("typed closure replay snapshot is required for routing")
        resolver = kwargs.pop("resolver")
        output_root = Path(kwargs.pop("output_root"))
        if kwargs:
            raise TypeError("unexpected closure replay trace router arguments")
        return build_results_first_trace_context_router(
            resolver=resolver,
            source_exp1_inventory_plan=(
                closure_snapshot.source_exp1_inventory_plan
            ),
            replay_semantic_authority=(
                closure_snapshot.replay_semantic_authority
            ),
            target_roots=closure_snapshot.coverage.roots,
            exp2_projection_root=(
                output_root / "exp2-projected-response-bank"
            ),
        )
    from tokenshare.experiments.paper_response_bank import (
        build_paper_formal_trace_context,
    )

    return build_paper_formal_trace_context(**kwargs)


def _build_results_first_replay_semantic_authority(
    *,
    authority: ResultsFirstExecutionAuthority,
    output_root: str | Path,
) -> object:
    from tokenshare.experiments.paper_response_bank import (
        build_results_first_replay_semantic_plan,
    )

    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first authority is required")
    return build_results_first_replay_semantic_plan(
        snapshot=authority.full_snapshot,
        prepared_inventory=authority.full_prepared_inventory,
        coverage=authority.coverage,
        catalog_manifest=authority.catalog_manifest,
        ai_api_configs=authority.ai_api_configs,
        planning_artifact_root=(
            Path(output_root) / "replay-semantic-request-authority"
        ),
    )


def _load_formal_resume_baseline(output_root: str | Path) -> object:
    from tokenshare.experiments.paper_formal_runner import (
        load_paper_formal_resume_baseline,
    )

    return load_paper_formal_resume_baseline(output_root)


def _zero_formal_resume_baseline() -> object:
    from tokenshare.experiments.paper_formal_runner import (
        PaperFormalResumeBaseline,
    )

    return PaperFormalResumeBaseline(
        provider_attempts_by_condition={},
        provider_attempt_count=0,
        total_cost_estimate=0.0,
        cost_estimate_by_currency={},
        total_cost_estimate_status="not_applicable",
        evidence_exists=False,
    )


_FORMAL_SUITE_EXECUTOR = _execute_formal_suite
_TRACE_CONTEXT_BUILDER = _build_trace_context
_RESULTS_FIRST_REPLAY_SEMANTIC_AUTHORITY_BUILDER = (
    _build_results_first_replay_semantic_authority
)
_REPRESENTATIVE_FORMAL_RESUME_BASELINE_LOADER = _load_formal_resume_baseline


def _build_results_first_exp5_ledger_factory(
    *,
    authority: ResultsFirstExecutionAuthority,
    ledger_path: str | Path,
    coverage: object,
) -> object:
    from tokenshare.experiments.paper_formal_callbacks import (
        PaperExp5LedgerRootCallbackFactory,
    )

    return PaperExp5LedgerRootCallbackFactory(
        ledger_path=ledger_path,
        full_prepared_inventory=authority.full_prepared_inventory,
        coverage=coverage,
        ai_api_configs=authority.ai_api_configs,
    )


_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER = (
    _build_results_first_exp5_ledger_factory
)


def build_results_first_execution_authority(
    *,
    atomic_authority: object,
    full_dispatch_plans: Sequence[object],
    catalog_manifest: object,
    ai_api_configs: Mapping[str, object],
    bundle_root: str | Path,
    output_root: str | Path,
    resume: bool,
) -> ResultsFirstExecutionAuthority:
    """从一次 full freeze 与显式 typed selection 建立执行 authority。"""

    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstAcquisitionAuthority,
    )
    from tokenshare.experiments.paper_formal_runner import (
        validate_paper_formal_suite_plan,
    )

    if type(atomic_authority) is not ResultsFirstAcquisitionAuthority:
        raise TypeError("results-first service requires typed acquisition authority")
    atomic_authority.__post_init__()
    plans = tuple(full_dispatch_plans)
    coverage = atomic_authority.coverage
    full_budget = atomic_authority.full_budget
    projection = atomic_authority.execution_budget_projection
    if (
        coverage.source_snapshot is not atomic_authority.full_snapshot
        or projection.coverage is not coverage
        or projection.source_budget is not full_budget
    ):
        raise ValueError("results-first budget/snapshot lineage mismatch")
    source_conditions = tuple(
        item.condition for item in atomic_authority.snapshot.conditions
    )
    plan_conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    if len(source_conditions) != len(plan_conditions) or any(
        source is not planned
        for source, planned in zip(source_conditions, plan_conditions, strict=True)
    ):
        raise ValueError("representative service dispatch authority drift")
    bundle = atomic_authority.plan
    plan_roots = {Path(plan.output_root).resolve(strict=False).parent for plan in plans}
    if len(plan_roots) != 1:
        raise ValueError("formal dispatch plans do not share one authority root")
    full_plan_root = next(iter(plan_roots))
    execution_experiment_ids = _results_first_experiment_ids_for_coverage(
        coverage=coverage
    )
    trace_experiment_ids = tuple(
        experiment_id
        for experiment_id in execution_experiment_ids
        if experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    condition_ids_by_experiment = {
        experiment_id: tuple(
            condition.condition_id
            for condition in coverage.conditions
            if condition.experiment_id == experiment_id
        )
        for experiment_id in execution_experiment_ids
    }
    for experiment_ids in (
        trace_experiment_ids,
        (_REPRESENTATIVE_EXP5_EXPERIMENT_ID,),
    ):
        selected = tuple(
            condition_id
            for experiment_id in experiment_ids
            for condition_id in condition_ids_by_experiment[experiment_id]
        )
        root_filter = {
            condition_id: coverage.root_case_filter[condition_id]
            for condition_id in selected
        }
        validate_paper_formal_suite_plan(
            dispatch_plans=plans,
            catalog_manifest=catalog_manifest,
            budget=full_budget,
            output_root=full_plan_root,
            ai_api_configs=ai_api_configs,
            hard_limits=projection.hard_limits,
            selected_condition_ids=selected,
            root_case_filter=root_filter,
        )
    return ResultsFirstExecutionAuthority(
        source_validation_digest=atomic_authority.validation_digest,
        full_dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        full_snapshot=atomic_authority.full_snapshot,
        full_prepared_inventory=atomic_authority.full_prepared_inventory,
        full_budget=full_budget,
        ai_api_configs=ai_api_configs,
        coverage=coverage,
        execution_budget_projection=projection,
        bundle=bundle,
        bundle_root=Path(bundle_root),
        output_root=Path(output_root),
        resume=bool(resume),
    )


def build_representative_full_plan_smoke_service_authority(
    *,
    atomic_authority: object,
    full_dispatch_plans: Sequence[object],
    catalog_manifest: object,
    full_budget: object,
    ai_api_configs: Mapping[str, object],
    bundle_root: str | Path,
    output_root: str | Path,
    resume: bool,
    hard_limits: Mapping[str, object],
) -> ResultsFirstExecutionAuthority:
    """旧 wrapper；仅接受与 typed authority 完全一致的 budget/limits。"""

    if full_budget is not getattr(atomic_authority, "full_budget", None):
        raise ValueError("representative wrapper full budget identity drift")
    projection = getattr(atomic_authority, "execution_budget_projection", None)
    if projection is None or dict(hard_limits) != dict(projection.hard_limits):
        raise ValueError("representative wrapper hard limits projection drift")
    return build_results_first_execution_authority(
        atomic_authority=atomic_authority,
        full_dispatch_plans=full_dispatch_plans,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        bundle_root=bundle_root,
        output_root=output_root,
        resume=resume,
    )


def _full_current_exp1_acquisition_pricing_authority(
    *,
    approval_authority: Mapping[str, object],
    frozen_ai_api_configs: Mapping[str, object],
    current_exp1_config: object,
) -> object:
    """把已验证的 A 收窄为 Full materializer 可消费的 current pricing authority。"""

    from decimal import Decimal

    from tokenshare.experiments.paper_response_bank import (
        FullCurrentAcquisitionPricingAuthority,
    )
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.run_paper_experiments import (
        _pricing_refresh_execution_identity,
    )

    exp1_authority = approval_authority.get("exp1_acquisition_authority")
    if not isinstance(exp1_authority, Mapping):
        raise ValueError("Full current Exp1 acquisition authority is missing")
    current_authority = exp1_authority.get("current_pricing_authority")
    budget = exp1_authority.get("budget")
    approval_digest = approval_authority.get("authority_digest")
    if (
        not isinstance(current_authority, Mapping)
        or not isinstance(budget, Mapping)
        or not isinstance(approval_digest, str)
        or not approval_digest.startswith("sha256:")
    ):
        raise ValueError("Full current Exp1 acquisition authority is invalid")
    current_authority_digest = current_authority.get("authority_digest")
    current_authority_preimage = {
        key: value for key, value in current_authority.items() if key != "authority_digest"
    }
    if (
        current_authority.get("schema_version")
        != "tokenshare.newfullrun_exp1_current_pricing_authority.v1"
        or current_authority.get("provider_calls_made") != 0
        or not isinstance(current_authority_digest, str)
        or digest_json(current_authority_preimage) != current_authority_digest
    ):
        raise ValueError("Full current Exp1 pricing authority drift")
    provider_config_id = current_authority.get("provider_config_id")
    current_config_digest = getattr(current_exp1_config, "config_digest", None)
    if (
        not isinstance(provider_config_id, str)
        or not provider_config_id
        or current_authority.get("provider_config_digest") != current_config_digest
    ):
        raise ValueError("Full current Exp1 pricing authority drift")
    source_config = frozen_ai_api_configs.get(provider_config_id)
    source_config_digest = getattr(source_config, "config_digest", None)
    if (
        source_config is None
        or current_authority.get("source_provider_config_digest")
        != source_config_digest
    ):
        raise ValueError("Full current Exp1 source provenance drift")
    source_identity = _pricing_refresh_execution_identity(source_config)
    current_identity = _pricing_refresh_execution_identity(current_exp1_config)
    if (
        source_identity != current_identity
        or current_authority.get("source_execution_identity_digest")
        != source_identity
        or current_authority.get("execution_identity_digest") != current_identity
    ):
        raise ValueError("Full current Exp1 pricing execution identity drift")
    raw_pricing_by_entry = current_authority.get("pricing_by_entry")
    if not isinstance(raw_pricing_by_entry, Mapping):
        raise ValueError("Full current Exp1 pricing authority drift")
    current_entries = tuple(
        entry
        for entry in getattr(current_exp1_config, "entries", ())
        if getattr(entry, "enabled", None) is True
    )
    actual_pricing_by_entry = {
        getattr(entry, "entry_id", None): dict(getattr(entry, "pricing", {}))
        for entry in current_entries
    }
    if (
        not current_entries
        or None in actual_pricing_by_entry
        or dict(raw_pricing_by_entry) != actual_pricing_by_entry
    ):
        raise ValueError("Full current Exp1 pricing authority drift")
    pricing_by_entry: dict[str, FrozenPricing] = {}
    try:
        for entry_id, raw_pricing in actual_pricing_by_entry.items():
            if not isinstance(entry_id, str) or not isinstance(raw_pricing, Mapping):
                raise ValueError("Full current Exp1 pricing authority drift")
            pricing_by_entry[entry_id] = FrozenPricing(
                currency=str(raw_pricing["currency"]),
                input_per_million_tokens=Decimal(
                    str(
                        raw_pricing.get(
                            "input_per_million_tokens",
                            raw_pricing["uncached_input_per_million_tokens"],
                        )
                    )
                ),
                output_per_million_tokens=Decimal(
                    str(raw_pricing["output_per_million_tokens"])
                ),
            )
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("Full current Exp1 pricing authority drift") from exc
    expected_budget_digest = budget.get("budget_digest")
    if not isinstance(expected_budget_digest, str) or not expected_budget_digest.startswith(
        "sha256:"
    ):
        raise ValueError("Full current Exp1 acquisition budget authority drift")
    return FullCurrentAcquisitionPricingAuthority(
        selection_kind="full_exp1_exp3_exp5",
        provider_config_id=provider_config_id,
        source_provider_config_digest=source_config_digest,
        source_execution_identity_digest=source_identity,
        current_provider_config_digest=current_config_digest,
        current_execution_identity_digest=current_identity,
        pricing_by_entry=pricing_by_entry,
        current_pricing_authority_digest=current_authority_digest,
        full_budget_approval_authority_digest=approval_digest,
        approved_exp1_budget_digest=expected_budget_digest,
    )


def load_exp4_excluded_results_first_execution_authority_v1(
    *,
    scope_preparation_root: str | Path,
    plan_bundle_root: str | Path,
    planning_artifact_root: str | Path,
    output_root: str | Path,
    selection: str,
    resume: bool,
    full_budget_approval_authority: str | Path | None = None,
    external_bank_root: str | Path | None = None,
) -> ResultsFirstExecutionAuthority:
    """由 scope preparation 重新派生 Exp4 排除 selection 的 typed authority。"""

    from decimal import Decimal

    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
        project_paper_execution_budget,
    )
    from tokenshare.experiments.paper_exp1_trace_reuse import (
        select_exp1_source_roots,
    )
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_exp4_excluded_coverage,
        load_formal_prepared_request_inventory,
    )
    from tokenshare.experiments.paper_formal_runner import (
        APPROVED_ENDPOINT_BINDINGS_KEY,
    )
    from tokenshare.experiments.paper_model_policy import (
        load_model_endpoint_cohort,
        load_model_entry_map,
        load_provider_config_map,
    )
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        FullAcquisitionBudget,
        materialize_results_first_unified_acquisition_plan,
        prepare_results_first_acquisition_authority,
    )
    from tokenshare.experiments.run_paper_experiments import (
        DEFAULT_EXP5_MODEL_COHORT,
        DEFAULT_EXP5_MODEL_ENTRY_MAP,
        DEFAULT_EXP5_PROVIDER_CONFIG,
        refresh_results_first_exp5_execution_authority,
    )

    approval_authority: Mapping[str, object] | None = None
    if full_budget_approval_authority is not None:
        approval_path = Path(full_budget_approval_authority).resolve(strict=False)
        approval_authority = _closure_snapshot_read_json(
            approval_path,
            label="Full current-pricing approval authority",
        )
        expected_authority_digest = approval_authority.get("authority_digest")
        if (
            approval_authority.get("schema_version")
            != "tokenshare.newfullrun_full_budget_approval_authority.v1"
            or approval_authority.get("selection") != selection
            or not isinstance(expected_authority_digest, str)
            or digest_json(
                {
                    key: value
                    for key, value in approval_authority.items()
                    if key != "authority_digest"
                }
            )
            != expected_authority_digest
        ):
            raise ValueError("Full current-pricing approval authority is invalid")
    elif selection == "full_exp1_exp3_exp5":
        raise ValueError(
            "Full Exp4-excluded results-first requires current-pricing approval authority"
        )

    if selection not in {
        "representative_exp1_exp3_exp5",
        "full_exp1_exp3_exp5",
    }:
        raise ValueError("Exp4-excluded results-first selection is invalid")
    preparation_root = Path(scope_preparation_root).resolve(strict=False)
    bundle_root = Path(plan_bundle_root).resolve(strict=False)
    planning_root = Path(planning_artifact_root).resolve(strict=False)
    output = Path(output_root).resolve(strict=False)
    manifest_path = preparation_root / "exp4_excluded_scope_preparation.v1.json"
    preparation = _closure_snapshot_read_json(
        manifest_path,
        label="Exp4-excluded scope preparation manifest",
    )
    required = {
        "schema_version",
        "selection",
        "base_selection",
        "included_experiment_ids",
        "excluded_experiment_ids",
        "exp4_excluded",
        "condition_count",
        "root_run_count",
        "selected_first_attempt_ai_unit_count",
        "coverage_digest",
        "source_snapshot_digest",
        "source_snapshot_root",
        "source_prepared_inventory_root",
        "prepared_inventory_digest",
        "source_bank_manifest_digest",
        "source_bank_terminal_entry_count",
        "execution_projection_digest",
        "execution_projection",
        "preparation_plan_digest",
        "provider_calls_made",
        "status",
    }
    if (
        set(preparation) != required
        or preparation.get("schema_version")
        != "tokenshare.exp4_excluded_results_first_preparation.v1"
        or preparation.get("selection") != selection
        or preparation.get("base_selection")
        != (
            "representative"
            if selection == "representative_exp1_exp3_exp5"
            else "full"
        )
        or preparation.get("included_experiment_ids")
        != list(_EXP4_EXCLUDED_EXPERIMENT_IDS)
        or preparation.get("excluded_experiment_ids")
        != ["exp4_real_ai_protocol_ablation"]
        or preparation.get("exp4_excluded") is not True
        or preparation.get("provider_calls_made") != 0
        or preparation.get("status") != "ready_provider_zero_scope_prepared"
    ):
        raise ValueError("Exp4-excluded scope preparation identity is invalid")
    plan_path = planning_root / "scope_preparation_plan.v1.json"
    plan = _closure_snapshot_read_json(
        plan_path,
        label="Exp4-excluded scope preparation plan",
    )
    binding_path = bundle_root / "source_bank_binding.v1.json"
    bank_binding = _closure_snapshot_read_json(
        binding_path,
        label="Exp4-excluded source bank binding",
    )
    if (
        digest_json(plan) != preparation["preparation_plan_digest"]
        or plan.get("selection") != selection
        or plan.get("coverage_digest") != preparation["coverage_digest"]
        or plan.get("source_snapshot_digest")
        != preparation["source_snapshot_digest"]
        or bank_binding.get("selection") != selection
        or bank_binding.get("coverage_digest") != preparation["coverage_digest"]
        or bank_binding.get("source_bank_manifest_digest")
        != preparation["source_bank_manifest_digest"]
        or bank_binding.get("provider_calls_made") != 0
    ):
        raise ValueError("Exp4-excluded scope preparation companion drift")
    snapshot_root = Path(str(preparation["source_snapshot_root"])).resolve(
        strict=False
    )
    inventory_root = Path(
        str(preparation["source_prepared_inventory_root"])
    ).resolve(strict=False)
    source_bank_root = Path(str(bank_binding.get("source_bank_root", ""))).resolve(
        strict=False
    )
    if external_bank_root is not None:
        supplied_bank_root = Path(external_bank_root).resolve(strict=False)
        if supplied_bank_root != source_bank_root:
            raise ValueError(
                "Exp4-excluded external bank root does not match scope preparation binding"
            )
    bank_manifest = _closure_snapshot_read_json(
        source_bank_root / "manifest.v1.json",
        label="Exp4-excluded source immutable bank manifest",
    )
    if (
        bank_manifest.get("manifest_digest")
        != preparation["source_bank_manifest_digest"]
        or bank_manifest.get("terminal_entry_count")
        != preparation["source_bank_terminal_entry_count"]
        or (
            selection == "representative_exp1_exp3_exp5"
            and bank_manifest.get("terminal_entry_count") != 90
        )
    ):
        raise ValueError("Exp4-excluded source immutable bank binding drift")
    snapshot = load_results_first_closure_replay_snapshot(
        output_root=snapshot_root,
    )
    full_snapshot = snapshot.coverage.source_snapshot
    coverage = derive_paper_formal_exp4_excluded_coverage(
        snapshot=full_snapshot,
        dispatch_plans=snapshot.full_dispatch_plans,
        catalog_manifest=snapshot.catalog_manifest,
        selection=str(preparation["base_selection"]),
    )
    inventory = load_formal_prepared_request_inventory(
        output_root=inventory_root,
        snapshot=full_snapshot,
    )
    if inventory.inventory_digest != preparation["prepared_inventory_digest"]:
        raise ValueError("Exp4-excluded prepared inventory digest drift")
    projection = project_paper_execution_budget(
        snapshot=full_snapshot,
        budget=snapshot.full_budget,
        coverage=coverage,
    )
    if (
        preparation.get("source_snapshot_digest") != full_snapshot.snapshot_digest
        or preparation.get("prepared_inventory_digest") is None
        or preparation.get("coverage_digest") != coverage.coverage_digest
        or preparation.get("condition_count") != coverage.condition_count
        or preparation.get("root_run_count") != coverage.root_run_count
        or preparation.get("execution_projection_digest") != projection.projection_digest
        or preparation.get("execution_projection") != projection.to_dict()
    ):
        raise ValueError("Exp4-excluded scope preparation projection drift")
    if approval_authority is not None:
        if (
            approval_authority.get("source_snapshot_digest")
            != full_snapshot.snapshot_digest
            or approval_authority.get("prepared_inventory_digest")
            != inventory.inventory_digest
            or approval_authority.get("coverage_digest")
            != coverage.coverage_digest
            or approval_authority.get("provider_calls_made") != 0
        ):
            raise ValueError("Full current-pricing approval scope drift")
    source_roots = select_exp1_source_roots(
        full_roots=full_snapshot.roots,
        selected_roots=coverage.roots,
    )
    records_by_key = {
        (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        ): record
        for record in inventory.records
    }
    source_keys = {
        (root.condition.condition_id, root.case_id, planned_ai_unit_id)
        for root in source_roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    }
    if not source_keys or not source_keys.issubset(records_by_key):
        raise ValueError("Exp4-excluded Exp1 source inventory is incomplete")
    api_key_env_by_family: dict[str, str] = {}
    pricing_by_family: dict[str, FrozenPricing] = {}
    for key in sorted(source_keys):
        record = records_by_key[key]
        config = snapshot.ai_api_configs.get(record.provider_config_id)
        matches = tuple(
            entry
            for entry in getattr(config, "entries", ())
            if getattr(entry, "enabled", None) is True
            and getattr(entry, "entry_id", None) == record.model_entry_id
        )
        if len(matches) != 1:
            raise ValueError("Exp4-excluded Exp1 provider authority is incomplete")
        entry = matches[0]
        api_key_env = getattr(entry, "api_key_env", None)
        pricing_body = getattr(entry, "pricing", None)
        if not isinstance(api_key_env, str) or not isinstance(pricing_body, Mapping):
            raise ValueError("Exp4-excluded Exp1 transport authority is incomplete")
        pricing = FrozenPricing(
            currency=str(pricing_body["currency"]),
            input_per_million_tokens=Decimal(
                str(
                    pricing_body.get(
                        "input_per_million_tokens",
                        pricing_body.get("uncached_input_per_million_tokens"),
                    )
                )
            ),
            output_per_million_tokens=Decimal(
                str(pricing_body["output_per_million_tokens"])
            ),
        )
        previous_env = api_key_env_by_family.setdefault(
            record.provider_family,
            api_key_env,
        )
        previous_pricing = pricing_by_family.setdefault(
            record.provider_family,
            pricing,
        )
        if previous_env != api_key_env or previous_pricing != pricing:
            raise ValueError("Exp4-excluded Exp1 provider authority is ambiguous")
    current_exp5_cohort = load_model_endpoint_cohort(DEFAULT_EXP5_MODEL_COHORT)
    current_exp5_entry_map = load_model_entry_map(DEFAULT_EXP5_MODEL_ENTRY_MAP)
    current_exp5_provider_configs = load_provider_config_map(
        {"siliconflow": DEFAULT_EXP5_PROVIDER_CONFIG}
    )
    execution_configs = refresh_results_first_exp5_execution_authority(
        base_ai_api_configs=snapshot.ai_api_configs,
        cohort=current_exp5_cohort,
        entry_map=current_exp5_entry_map,
        provider_configs=current_exp5_provider_configs,
    )
    full_current_exp1_pricing_authority = None
    current_exp1_configs = None
    if approval_authority is not None:
        from tokenshare.executors.ai_api_config import load_ai_api_config
        from tokenshare.experiments.run_paper_experiments import (
            EXP1_EXP4_REQUIRED_PROVIDER_CONFIG,
            EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID,
        )

        current_exp1_config = load_ai_api_config(
            json.loads(
                EXP1_EXP4_REQUIRED_PROVIDER_CONFIG.read_text(encoding="utf-8")
            )
        )
        current_exp1_configs = {
            **execution_configs,
            EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID: current_exp1_config,
        }
        full_current_exp1_pricing_authority = (
            _full_current_exp1_acquisition_pricing_authority(
                approval_authority=approval_authority,
                frozen_ai_api_configs=snapshot.ai_api_configs,
                current_exp1_config=current_exp1_config,
            )
        )
        if (
            full_current_exp1_pricing_authority.provider_config_id
            != EXP1_EXP4_REQUIRED_PROVIDER_CONFIG_ID
        ):
            raise ValueError("Full current Exp1 pricing authority drift")
        current_entries = tuple(
            entry for entry in current_exp1_config.entries if entry.enabled
        )
        if len(current_entries) != 1:
            raise ValueError("Full current Exp1 pricing entry inventory drift")
        current_entry = current_entries[0]
        pricing_by_family["deepseek"] = (
            full_current_exp1_pricing_authority.pricing_by_entry[
                current_entry.entry_id
            ]
        )
        api_key_env_by_family["deepseek"] = str(current_entry.api_key_env)
    atomic_authority = prepare_results_first_acquisition_authority(
        full_snapshot=full_snapshot,
        full_prepared_inventory=inventory,
        full_budget=snapshot.full_budget,
        coverage=coverage,
        execution_budget_projection=projection,
        catalog_manifest=snapshot.catalog_manifest,
        ai_api_configs=execution_configs,
        source_ai_api_configs=snapshot.ai_api_configs,
        planning_artifact_root=planning_root,
        api_key_env_by_provider_family=api_key_env_by_family,
        frozen_pricing_by_provider_family=pricing_by_family,
        requested_at="2026-08-18T00:00:00Z",
        max_acquisition_concurrency=10,
        full_current_pricing_authority=full_current_exp1_pricing_authority,
        current_ai_api_configs=current_exp1_configs,
    )
    authority = build_results_first_execution_authority(
        atomic_authority=atomic_authority,
        full_dispatch_plans=snapshot.full_dispatch_plans,
        catalog_manifest=snapshot.catalog_manifest,
        ai_api_configs=execution_configs,
        bundle_root=output / "scope-exp1-acquisition-bundle",
        output_root=output,
        resume=resume,
    )
    materialized_source_plan = materialize_results_first_unified_acquisition_plan(
        plan=authority.bundle
    )
    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=FullAcquisitionBudget.create(
            materialized_source_plan.acquisition_requests
        ),
        exp5_execution_projection=_selection_exact_exp5_execution_projection(
            authority
        ),
        exp5_pricing_authority=(
            execution_configs.get(APPROVED_ENDPOINT_BINDINGS_KEY, {}).get(
                "exp5_real_ai_model_endpoint_comparison"
            )
            if approval_authority is not None
            else None
        ),
        exp1_acquisition_is_new=_results_first_exp1_acquisition_is_new(
            selection=selection,
            external_bank_root=(
                None
                if external_bank_root is None
                else Path(external_bank_root).resolve(strict=False)
            ),
        ),
    )
    if approval_authority is not None:
        approved_exp5 = approval_authority.get("exp5_online_budget")
        approved_total = approval_authority.get("budget", {}).get("total") if isinstance(approval_authority.get("budget"), Mapping) else None
        actual_exp5 = provider_budget.exp5
        actual_total = provider_budget.global_new_paid
        if (
            not isinstance(approved_exp5, Mapping)
            or approved_exp5.get("calls") != actual_exp5.get("calls")
            or approved_exp5.get("tokens") != actual_exp5.get("tokens")
            or str(approved_exp5.get("cny")) != str(actual_exp5.get("cny"))
            or approved_exp5.get("pricing_snapshot_digest_by_member")
            != actual_exp5.get("pricing_snapshot_digest_by_member")
            or approved_exp5.get("pricing_authority_digest")
            != actual_exp5.get("pricing_authority_digest")
            or not isinstance(approved_total, Mapping)
            or approved_total.get("calls") != actual_total.get("calls")
            or approved_total.get("tokens") != actual_total.get("tokens")
            or str(approved_total.get("cny")) != str(actual_total.get("cny"))
        ):
            raise ValueError("Full current-pricing approval budget drift")
    if approval_authority is not None:
        expected_exp1_budget = (
            approval_authority.get("exp1_acquisition_authority")
            if isinstance(approval_authority.get("exp1_acquisition_authority"), Mapping)
            else None
        )
        expected_budget_body = (
            expected_exp1_budget.get("budget")
            if isinstance(expected_exp1_budget, Mapping)
            else None
        )
        expected_budget_digest = (
            expected_budget_body.get("budget_digest")
            if isinstance(expected_budget_body, Mapping)
            else None
        )
        actual_exp1_budget = FullAcquisitionBudget.create(
            materialized_source_plan.acquisition_requests
        )
        if expected_budget_digest != actual_exp1_budget.budget_digest:
            raise ValueError("Full current Exp1 acquisition budget authority drift")
    return replace(authority, provider_budget_authority=provider_budget)


def _results_first_suite_resume(*, requested_resume: bool, suite_root: Path) -> bool:
    if not requested_resume:
        return False
    root = Path(suite_root)
    if not root.exists():
        return False
    manifest_path = root / "suite_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("suite root exists without suite manifest")
    return True


def execute_results_first_experiments(
    *,
    authority: ResultsFirstExecutionAuthority,
    response_bank_resolver: object,
    exp5_transport: object | None = None,
    exp5_transport_factory: Callable[[], object] | None = None,
    acquisition_usage: RepresentativeCurrentProviderUsage | None = None,
) -> ResultsFirstExecutionResult:
    """按 authority coverage 执行 trace experiments 与 Exp5 online。"""

    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first execution authority is required")
    if (exp5_transport is None) == (exp5_transport_factory is None):
        raise TypeError(
            "results-first execution requires exactly one Exp5 transport boundary"
        )
    exp5_coverage = _results_first_formal_exp5_coverage(authority)
    acquisition_current = acquisition_usage or RepresentativeCurrentProviderUsage(
        provider_calls=0,
        spend=0.0,
    )
    if type(acquisition_current) is not RepresentativeCurrentProviderUsage:
        raise TypeError("typed representative acquisition usage is required")
    zero_usage = RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    coverage = authority.coverage
    execution_experiment_ids = _results_first_experiment_ids_for_coverage(
        coverage=coverage
    )
    trace_experiment_ids = tuple(
        experiment_id
        for experiment_id in execution_experiment_ids
        if experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    if not trace_experiment_ids or execution_experiment_ids[-1] != (
        _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    ):
        raise ValueError("results-first coverage trace/Exp5 partition is invalid")
    conditions_by_experiment = {
        experiment_id: tuple(
            condition
            for condition in coverage.conditions
            if condition.experiment_id == experiment_id
        )
        for experiment_id in execution_experiment_ids
    }
    trace_conditions = tuple(
        condition
        for experiment_id in trace_experiment_ids
        for condition in conditions_by_experiment[experiment_id]
    )
    exp5_conditions = tuple(
        condition
        for condition in exp5_coverage.conditions
        if condition.experiment_id == _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    if not exp5_conditions:
        raise ValueError("formal Exp5 coverage selected no conditions")
    trace_suite_suffix = (
        "exp1_exp4" if trace_experiment_ids == _REPRESENTATIVE_TRACE_EXPERIMENT_IDS else "exp1_exp3"
    )
    trace_output_root = authority.output_root / f"{trace_suite_suffix.replace('_', '-')}-trace"
    exp5_output_root = authority.output_root / "exp5-online"
    try:
        trace_resume = _results_first_suite_resume(
            requested_resume=authority.resume,
            suite_root=trace_output_root,
        )
        exp5_resume = _results_first_suite_resume(
            requested_resume=authority.resume,
            suite_root=exp5_output_root,
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="suite_resume",
            acquisition_usage=acquisition_current,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        resolver_manifest = getattr(
            getattr(response_bank_resolver, "index", None), "manifest", None
        )
        source_manifest_digest = getattr(resolver_manifest, "manifest_digest", None)
        if not isinstance(source_manifest_digest, str) or not source_manifest_digest:
            raise ValueError("results-first response bank manifest identity is missing")
        closure_snapshot = load_results_first_closure_replay_snapshot(
            output_root=authority.output_root,
            suite_root=None,
        )
        if authority.resume:
            load_results_first_post_acquisition_trace_seal(
                output_root=authority.output_root,
                expected_closure_snapshot_digest=closure_snapshot.snapshot_digest,
                expected_source_manifest_digest=source_manifest_digest,
            )
        trace_context = _TRACE_CONTEXT_BUILDER(
            authority=authority,
            resolver=response_bank_resolver,
            output_root=authority.output_root,
        )
        if not authority.resume:
            persist_results_first_post_acquisition_trace_seal(
                output_root=authority.output_root,
                closure_snapshot_digest=closure_snapshot.snapshot_digest,
                source_manifest_digest=source_manifest_digest,
                attempt_history_path=(
                    authority.output_root
                    / "exp1_attempt_history_authority.final.v1.json"
                ),
                router_authority_path=(
                    authority.output_root
                    / "results_first_trace_router_authority.v1.json"
                ),
                exp2_derivation_path=(
                    authority.output_root
                    / "exp2-projected-response-bank"
                    / "exp2_projection_derivation.v1.json"
                ),
            )
        trace_terminal = _run_results_first_formal_subset(
            authority=authority,
            coverage=coverage,
            conditions=trace_conditions,
            output_root=trace_output_root,
            transport=object(),
            real_transport=False,
            trace_context=trace_context,
            suite_id=f"results_first_{trace_suite_suffix}_trace",
            resume=trace_resume,
        )
    except RepresentativeSmokeExecutionError:
        raise
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="trace_runner",
            acquisition_usage=acquisition_current,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        trace_usage = _current_provider_usage_from_terminal(
            terminal=trace_terminal,
            force_zero_spend_when_no_calls=True,
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="trace_usage",
            acquisition_usage=acquisition_current,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        _validate_representative_terminal(
            terminal=trace_terminal,
            coverage=coverage,
            conditions=trace_conditions,
            expected_experiment_ids=trace_experiment_ids,
            require_zero_current_provider_calls=True,
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="trace_terminal",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        from tokenshare.experiments.paper_exp1_trace_reuse import (
            finalize_exp1_protocol_outcomes_from_formal_trace,
            load_exp1_attempt_history_authority,
        )

        initial_attempt_history = load_exp1_attempt_history_authority(
            resolver_root=response_bank_resolver.root_path,
            expected_source_manifest_digest=source_manifest_digest,
            expected_source_inventory_digest=(
                authority.bundle.semantic_inventory_plan.inventory_digest
            ),
        )
        finalize_exp1_protocol_outcomes_from_formal_trace(
            authority=initial_attempt_history,
            trace_context=trace_context,
            trace_output_root=trace_output_root,
            authority_root=authority.output_root,
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="exp1_protocol_outcome_authority",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        trace_closure_preflight = audit_results_first_trace_closure_source(
            suite_root=trace_output_root,
        )
        expected_trace_root_count = sum(
            len(coverage.root_case_filter[condition.condition_id])
            for condition in trace_conditions
        )
        if (
            type(trace_closure_preflight) is not ResultsFirstTraceClosurePreflight
            or trace_closure_preflight.condition_count != len(trace_conditions)
            or trace_closure_preflight.root_count != expected_trace_root_count
            or trace_closure_preflight.checkpoint_count != expected_trace_root_count
            or trace_closure_preflight.current_provider_attempt_count != 0
        ):
            raise ValueError("trace closure source preflight is incomplete")
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="trace_closure_source_preflight",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=zero_usage,
        ) from exc
    try:
        from tokenshare.experiments.paper_formal_runner import (
            PaperFormalResumeBaseline,
        )

        exp5_baseline = (
            _REPRESENTATIVE_FORMAL_RESUME_BASELINE_LOADER(
                exp5_output_root
            )
            if exp5_resume
            else _zero_formal_resume_baseline()
        )
        if type(exp5_baseline) is not PaperFormalResumeBaseline:
            raise TypeError("typed formal resume baseline is required")
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="exp5_resume_baseline",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=zero_usage,
        ) from exc
    if exp5_transport_factory is not None:
        try:
            exp5_transport = exp5_transport_factory()
        except RepresentativeSmokeExecutionError:
            raise
        except Exception as exc:
            raise RepresentativeSmokeExecutionError(
                str(exc),
                failure_stage="exp5_transport_factory",
                acquisition_usage=acquisition_current,
                trace_usage=trace_usage,
                exp5_usage=zero_usage,
            ) from exc
        if exp5_transport is None:
            raise RepresentativeSmokeExecutionError(
                "Exp5 transport factory returned no transport",
                failure_stage="exp5_transport_factory",
                acquisition_usage=acquisition_current,
                trace_usage=trace_usage,
                exp5_usage=zero_usage,
            )
    assert exp5_transport is not None
    try:
        exp5_callback_factory = _RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER(
            authority=authority,
            coverage=exp5_coverage,
            ledger_path=(
                authority.output_root
                / "exp5-online-ledger"
                / "exp5_online_budget.v1.sqlite3"
            ),
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="exp5_ledger_factory",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=zero_usage,
        ) from exc
    counting_exp5_transport = _CountingRepresentativeTransport(exp5_transport)
    try:
        exp5_terminal = _run_results_first_formal_subset(
            authority=authority,
            coverage=exp5_coverage,
            conditions=exp5_conditions,
            output_root=exp5_output_root,
            transport=counting_exp5_transport,
            real_transport=True,
            trace_context=None,
            suite_id="results_first_exp5_online",
            online_root_callback_factory=exp5_callback_factory,
            resume=exp5_resume,
        )
    except RepresentativeSmokeExecutionError:
        raise
    except Exception as exc:
        exp5_usage = _exp5_usage_from_ledger(
            callback_factory=exp5_callback_factory,
            observed_transport_calls=counting_exp5_transport.provider_calls,
        )
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="exp5_runner",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=exp5_usage,
        ) from exc
    exp5_usage = _exp5_usage_from_ledger(
        callback_factory=exp5_callback_factory,
        observed_transport_calls=counting_exp5_transport.provider_calls,
    )
    exp5_ledger_audit = exp5_callback_factory.audit_usage()
    try:
        _validate_representative_terminal(
            terminal=exp5_terminal,
            coverage=exp5_coverage,
            conditions=exp5_conditions,
            expected_experiment_ids=(_REPRESENTATIVE_EXP5_EXPERIMENT_ID,),
            require_zero_current_provider_calls=False,
        )
        _validate_representative_exp5_terminal(
            terminal=exp5_terminal,
            coverage=exp5_coverage,
            conditions=exp5_conditions,
            ai_api_configs=authority.ai_api_configs,
            transport=counting_exp5_transport,
            baseline=exp5_baseline,
            ledger_audit=exp5_ledger_audit,
        )
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="exp5_terminal",
            acquisition_usage=acquisition_current,
            trace_usage=trace_usage,
            exp5_usage=exp5_usage,
        ) from exc
    statuses = {
        str(getattr(getattr(item, "status", None), "value", getattr(item, "status", None)))
        for item in (trace_terminal, exp5_terminal)
    }
    aggregate_status = (
        "completed_with_failures"
        if "completed_with_failures" in statuses
        else "completed"
    )
    usages = (acquisition_current, trace_usage, exp5_usage)
    missing_reasons = tuple(
        usage.spend_missing_reason
        for usage in usages
        if usage.spend_missing_reason is not None
    )
    total_spend = (
        None
        if missing_reasons
        else sum(float(usage.spend) for usage in usages if usage.spend is not None)
    )
    return ResultsFirstExecutionResult(
        status=aggregate_status,
        trace_terminal=trace_terminal,
        exp5_terminal=exp5_terminal,
        expected_condition_count=int(coverage.condition_count),
        expected_root_count=int(coverage.root_run_count),
        acquisition_current_provider_calls=acquisition_current.provider_calls,
        acquisition_current_spend=acquisition_current.spend,
        acquisition_spend_missing_reason=(
            acquisition_current.spend_missing_reason
        ),
        trace_current_provider_calls=trace_usage.provider_calls,
        trace_current_spend=trace_usage.spend,
        trace_spend_missing_reason=trace_usage.spend_missing_reason,
        exp5_current_provider_calls=exp5_usage.provider_calls,
        exp5_current_spend=exp5_usage.spend,
        exp5_spend_missing_reason=exp5_usage.spend_missing_reason,
        total_current_provider_calls=sum(
            usage.provider_calls for usage in usages
        ),
        total_current_spend=total_spend,
        total_spend_missing_reason=(
            None if not missing_reasons else ";".join(missing_reasons)
        ),
    )


def execute_representative_full_plan_smoke(
    *,
    authority: ResultsFirstExecutionAuthority,
    response_bank_resolver: object,
    exp5_transport: object | None = None,
    exp5_transport_factory: Callable[[], object] | None = None,
    acquisition_usage: RepresentativeCurrentProviderUsage | None = None,
) -> ResultsFirstExecutionResult:
    """旧 API 兼容 wrapper；全部语义委托共享 results-first entrypoint。"""

    return execute_results_first_experiments(
        authority=authority,
        response_bank_resolver=response_bank_resolver,
        exp5_transport=exp5_transport,
        exp5_transport_factory=exp5_transport_factory,
        acquisition_usage=acquisition_usage,
    )


def _current_provider_usage_from_terminal(
    *,
    terminal: object,
    force_zero_spend_when_no_calls: bool,
) -> RepresentativeCurrentProviderUsage:
    calls = int(getattr(terminal, "provider_attempt_count", 0))
    if calls < 0:
        raise ValueError("terminal current provider calls are invalid")
    if calls == 0 and force_zero_spend_when_no_calls:
        return RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    status = str(getattr(terminal, "total_cost_estimate_status", ""))
    if status in {"single_currency_estimate", "single_currency_or_legacy"}:
        value = getattr(terminal, "total_cost_estimate", None)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return RepresentativeCurrentProviderUsage(
                provider_calls=calls,
                spend=float(value),
            )
    reason = status or "terminal_cost_estimate_missing"
    return RepresentativeCurrentProviderUsage(
        provider_calls=calls,
        spend=None,
        spend_missing_reason=reason,
    )


def _exp5_usage_from_dispatch_boundary(
    *,
    transport: _CountingRepresentativeTransport,
    terminal: object | None,
    baseline: object,
) -> RepresentativeCurrentProviderUsage:
    """用本次实际 dispatch 与 cumulative cost delta 报告 current usage。"""

    calls = transport.provider_calls
    if calls == 0:
        return RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    if terminal is None:
        return RepresentativeCurrentProviderUsage(
            provider_calls=calls,
            spend=None,
            spend_missing_reason="exp5_usage_missing",
        )
    spend, missing_reason = _exp5_current_spend_delta(
        terminal=terminal,
        baseline=baseline,
    )
    return RepresentativeCurrentProviderUsage(
        provider_calls=calls,
        spend=spend,
        spend_missing_reason=missing_reason,
    )


def _exp5_usage_from_ledger(
    *,
    callback_factory: object,
    observed_transport_calls: int,
) -> RepresentativeCurrentProviderUsage:
    audit_method = getattr(callback_factory, "audit_usage", None)
    if not callable(audit_method):
        raise TypeError("Exp5 ledger callback factory has no audit authority")
    audit = audit_method()
    calls = getattr(audit, "current_provider_calls", None)
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        raise ValueError("Exp5 ledger current provider calls are invalid")
    if calls != observed_transport_calls:
        raise ValueError("Exp5 ledger/transport current provider call drift")
    spend = getattr(audit, "current_spend", None)
    reason = getattr(audit, "current_spend_missing_reason", None)
    if spend is None:
        return RepresentativeCurrentProviderUsage(
            provider_calls=calls,
            spend=None,
            spend_missing_reason=str(reason or "exp5_ledger_usage_missing"),
        )
    return RepresentativeCurrentProviderUsage(
        provider_calls=calls,
        spend=float(spend),
    )


def _exp5_current_spend_delta(
    *,
    terminal: object,
    baseline: object,
) -> tuple[float | None, str | None]:
    final_status = str(getattr(terminal, "total_cost_estimate_status", ""))
    raw_final_costs = getattr(terminal, "cost_estimate_by_currency", None)
    final_costs = (
        {
            str(currency): float(value)
            for currency, value in raw_final_costs.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if isinstance(raw_final_costs, Mapping)
        else {}
    )
    baseline_costs = dict(getattr(baseline, "cost_estimate_by_currency", {}))
    baseline_missing = getattr(baseline, "spend_missing_reason", None)
    baseline_exists = getattr(baseline, "evidence_exists", True) is True
    if baseline_exists and isinstance(baseline_missing, str) and baseline_missing:
        return None, f"exp5_cost_delta_baseline_{baseline_missing}"
    final_cost: float | None = None
    baseline_cost: float | None = None
    if final_status == "single_currency_estimate" and len(final_costs) == 1:
        currency, final_cost = next(iter(final_costs.items()))
        if not baseline_exists:
            baseline_cost = 0.0
        elif baseline_missing is None and set(baseline_costs) == {currency}:
            baseline_cost = float(baseline_costs[currency])
        elif (
            baseline_missing is None
            and int(getattr(baseline, "provider_attempt_count", -1)) == 0
            and getattr(baseline, "total_cost_estimate", None) == 0.0
        ):
            baseline_cost = 0.0
    elif final_status == "single_currency_estimate" and not baseline_exists:
        raw_final_cost = getattr(terminal, "total_cost_estimate", None)
        if (
            isinstance(raw_final_cost, (int, float))
            and not isinstance(raw_final_cost, bool)
        ):
            final_cost = float(raw_final_cost)
            baseline_cost = 0.0
    elif final_status == "single_currency_or_legacy" and not final_costs:
        raw_final_cost = getattr(terminal, "total_cost_estimate", None)
        if (
            isinstance(raw_final_cost, (int, float))
            and not isinstance(raw_final_cost, bool)
        ):
            final_cost = float(raw_final_cost)
        if not baseline_exists:
            baseline_cost = 0.0
        elif (
            baseline_missing is None
            and not baseline_costs
            and getattr(baseline, "total_cost_estimate_status", None)
            == "single_currency_or_legacy"
        ):
            raw_baseline_cost = getattr(baseline, "total_cost_estimate", None)
            if isinstance(raw_baseline_cost, (int, float)) and not isinstance(
                raw_baseline_cost,
                bool,
            ):
                baseline_cost = float(raw_baseline_cost)
    if final_cost is None or baseline_cost is None:
        reason = (
            f"exp5_cost_delta_{final_status}"
            if final_status
            else "exp5_cost_delta_unavailable"
        )
        return None, reason
    delta = final_cost - baseline_cost
    if delta < -1e-12:
        return None, "exp5_cost_delta_negative"
    return max(0.0, delta), None


def _run_results_first_formal_subset(
    *,
    authority: (
        ResultsFirstExecutionAuthority
        | ResultsFirstTraceClosureReplayAuthority
    ),
    coverage: object | None = None,
    conditions: Sequence[object],
    output_root: Path,
    transport: object,
    real_transport: bool,
    trace_context: object | None,
    suite_id: str,
    online_root_callback_factory: object | None = None,
    resume: bool = False,
) -> object:
    execution_coverage = authority.coverage if coverage is None else coverage
    selected = tuple(condition.condition_id for condition in conditions)
    root_filter = {
        condition_id: execution_coverage.root_case_filter[condition_id]
        for condition_id in selected
    }
    active_experiment_ids = tuple(
        dict.fromkeys(condition.experiment_id for condition in conditions)
    )
    execution_roots = {
        experiment_id: output_root / experiment_id
        for experiment_id in active_experiment_ids
    }
    policy = authority.execution_policy
    return _FORMAL_SUITE_EXECUTOR(
        dispatch_plans=authority.full_dispatch_plans,
        catalog_manifest=authority.catalog_manifest,
        budget=authority.full_budget,
        budget_approval={
            "approval_mode": "results_first_execution",
            # Formal trace runner 校验的是 structural full budget；paid
            # provider-budget authority 由 receipt/results-first closure
            # 单独绑定，不能把两种 digest 混成一个 approval 字段。
            "budget_digest": authority.full_budget.budget_digest,
        },
        output_root=output_root,
        ai_api_configs=authority.ai_api_configs,
        transport=transport,
        real_transport=real_transport,
        hard_limits=authority.hard_limits,
        resume=resume,
        replay_only=False,
        root_case_filter=root_filter,
        selected_condition_ids=selected,
        execution_output_root_by_experiment=execution_roots,
        trace_context=trace_context,
        enforce_publication_closure=policy.enforce_publication_closure,
        enable_metric_closure=policy.enable_metric_closure,
        bypass_nonmetric_facility_gates=(
            policy.bypass_nonmetric_facility_gates
        ),
        suite_id=suite_id,
        online_root_callback_factory=online_root_callback_factory,
    )


def _validate_representative_terminal(
    *,
    terminal: object,
    coverage: object,
    conditions: Sequence[object],
    expected_experiment_ids: Sequence[str],
    require_zero_current_provider_calls: bool,
) -> None:
    status = str(
        getattr(
            getattr(terminal, "status", None),
            "value",
            getattr(terminal, "status", None),
        )
    )
    if status not in {"completed", "completed_with_failures"}:
        raise ValueError("representative runner terminal is not acceptable")
    selected_ids = tuple(condition.condition_id for condition in conditions)
    expected_roots = sum(
        len(coverage.root_case_filter[item]) for item in selected_ids
    )
    if (
        int(getattr(terminal, "condition_count", -1)) != len(selected_ids)
        or int(getattr(terminal, "task_count", -1)) != expected_roots
        or tuple(getattr(terminal, "experiment_ids", ()))
        != tuple(expected_experiment_ids)
    ):
        raise ValueError("representative runner denominator is incomplete")
    if require_zero_current_provider_calls and int(
        getattr(terminal, "provider_attempt_count", -1)
    ) != 0:
        raise ValueError("trace runner made current provider calls")


def _validate_representative_exp5_terminal(
    *,
    terminal: object,
    coverage: object,
    conditions: Sequence[object],
    ai_api_configs: Mapping[str, object],
    transport: _CountingRepresentativeTransport,
    baseline: object,
    ledger_audit: object,
) -> None:
    if getattr(coverage, "selection_kind", None) not in {"full", "filtered"}:
        raise ValueError("Exp5 selection kind is invalid")
    if any(
        condition.experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
        for condition in coverage.conditions
    ):
        raise ValueError("Exp5 terminal coverage includes non-Exp5 conditions")
    if tuple(conditions) != tuple(coverage.conditions):
        raise ValueError("Exp5 terminal coverage condition identity drift")
    selected_ids = tuple(str(condition.condition_id) for condition in conditions)
    summaries = getattr(terminal, "condition_results", ())
    if not isinstance(summaries, Sequence) or isinstance(summaries, (str, bytes)):
        raise ValueError("Exp5 per-condition terminal summaries are missing")
    attempts_by_condition: dict[str, int] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("Exp5 per-condition terminal summary is invalid")
        condition_id = summary.get("condition_id")
        attempts = summary.get("provider_attempt_count")
        if (
            not isinstance(condition_id, str)
            or not condition_id
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or attempts < 0
            or condition_id in attempts_by_condition
        ):
            raise ValueError("Exp5 per-condition provider attempts are incomplete")
        attempts_by_condition[condition_id] = attempts
    if set(attempts_by_condition) != set(selected_ids):
        raise ValueError("Exp5 per-condition terminal coverage is incomplete")
    attempt_sum = sum(attempts_by_condition.values())
    if int(getattr(terminal, "provider_attempt_count", -1)) != attempt_sum:
        raise ValueError("Exp5 terminal provider attempt total is inconsistent")

    def condition_counts(field_name: str) -> dict[str, int]:
        raw = getattr(ledger_audit, field_name, None)
        if not isinstance(raw, Mapping) or set(raw) != set(selected_ids):
            raise ValueError("Exp5 ledger condition slot authority is incomplete")
        counts = dict(raw)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts.values()
        ):
            raise ValueError("Exp5 ledger condition slot counts are invalid")
        return counts

    expected_by_condition = condition_counts(
        "expected_provider_calls_by_condition"
    )
    current_by_condition = condition_counts(
        "current_provider_calls_by_condition"
    )
    total_by_condition = condition_counts("total_provider_calls_by_condition")
    current_terminal_by_condition = condition_counts(
        "current_terminal_provider_calls_by_condition"
    )
    total_terminal_by_condition = condition_counts(
        "total_terminal_provider_calls_by_condition"
    )

    def canonical_slots(field_name: str) -> tuple[tuple[str, str, str], ...]:
        raw = getattr(ledger_audit, field_name, None)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("Exp5 ledger canonical slot identities are missing")
        slots: list[tuple[str, str, str]] = []
        for item in raw:
            if (
                not isinstance(item, Sequence)
                or isinstance(item, (str, bytes))
                or len(item) != 3
                or any(not isinstance(value, str) or not value for value in item)
            ):
                raise ValueError("Exp5 ledger canonical slot identity is invalid")
            slot = (str(item[0]), str(item[1]), str(item[2]))
            if slot[0] not in selected_ids:
                raise ValueError("Exp5 ledger slot condition is unselected")
            slots.append(slot)
        if len(slots) != len(set(slots)):
            raise ValueError("Exp5 ledger canonical slot identity is duplicate")
        return tuple(slots)

    prepared_slots = canonical_slots("prepared_canonical_slots")
    current_terminal_slots = canonical_slots("current_terminal_slots")
    total_terminal_slots = canonical_slots("total_terminal_slots")
    prepared_slot_set = set(prepared_slots)
    total_terminal_slot_set = set(total_terminal_slots)
    if not total_terminal_slot_set.issubset(prepared_slot_set):
        raise ValueError("Exp5 actual terminal slot is outside prepared authority")
    if not set(current_terminal_slots).issubset(total_terminal_slot_set):
        raise ValueError("Exp5 current terminal slot is outside total terminal facts")

    def slot_counts(slots: Sequence[tuple[str, str, str]]) -> dict[str, int]:
        return {
            condition_id: sum(slot[0] == condition_id for slot in slots)
            for condition_id in selected_ids
        }

    prepared_slot_counts = slot_counts(prepared_slots)
    current_terminal_slot_counts = slot_counts(current_terminal_slots)
    total_terminal_slot_counts = slot_counts(total_terminal_slots)
    if prepared_slot_counts != expected_by_condition:
        raise ValueError("Exp5 prepared canonical slot grouping is inconsistent")
    if any(
        attempts_by_condition[condition_id] > prepared_slot_counts[condition_id]
        for condition_id in selected_ids
    ):
        raise ValueError("Exp5 terminal provider attempts exceed prepared slots")
    if any(
        total_terminal_slot_counts[condition_id] < 1
        for condition_id in selected_ids
    ):
        raise ValueError("Exp5 selected condition has no actual terminal slot")
    if (
        attempts_by_condition != total_terminal_by_condition
        or total_terminal_by_condition != total_terminal_slot_counts
        or current_terminal_by_condition != current_terminal_slot_counts
        or total_by_condition != total_terminal_by_condition
    ):
        raise ValueError("Exp5 terminal/ledger actual slot coverage is inconsistent")
    current_calls = getattr(ledger_audit, "current_provider_calls", None)
    total_calls = getattr(ledger_audit, "total_provider_calls", None)
    if (
        isinstance(current_calls, bool)
        or not isinstance(current_calls, int)
        or isinstance(total_calls, bool)
        or not isinstance(total_calls, int)
        or current_calls != sum(current_by_condition.values())
        or total_calls != sum(total_by_condition.values())
        or current_calls != len(current_terminal_slots)
        or total_calls != len(total_terminal_slots)
        or current_by_condition != current_terminal_by_condition
    ):
        raise ValueError("Exp5 ledger provider call grouping is inconsistent")
    baseline_attempts = dict(
        getattr(baseline, "provider_attempts_by_condition", {})
    )
    if not set(baseline_attempts).issubset(selected_ids):
        raise ValueError("Exp5 resume baseline contains unselected conditions")
    deltas: dict[str, int] = {}
    for condition_id in selected_ids:
        prior = baseline_attempts.get(condition_id, 0)
        final = attempts_by_condition[condition_id]
        if (
            isinstance(prior, bool)
            or not isinstance(prior, int)
            or prior < 0
            or final < prior
        ):
            raise ValueError("Exp5 resume provider attempt delta is invalid")
        deltas[condition_id] = final - prior
    if (
        deltas != current_terminal_by_condition
        or sum(deltas.values()) != transport.provider_calls
    ):
        raise ValueError("Exp5 current provider attempt delta does not match dispatches")
    expected = _expected_exp5_endpoint_observations(
        conditions=conditions,
        ai_api_configs=ai_api_configs,
    )
    conditions_by_id = {
        str(condition.condition_id): condition for condition in conditions
    }
    total_actual_conditions = tuple(
        conditions_by_id[slot[0]] for slot in total_terminal_slots
    )
    total_actual = _expected_exp5_endpoint_observations(
        conditions=total_actual_conditions,
        ai_api_configs=ai_api_configs,
    )
    if (
        len(set(expected.values())) != 4
        or set(total_actual.items()) != set(expected.items())
    ):
        raise ValueError("Exp5 actual terminal endpoint/model coverage is incomplete")
    current_conditions = tuple(
        condition
        for condition in conditions
        if deltas[str(condition.condition_id)] > 0
    )
    current_expected = _expected_exp5_endpoint_observations(
        conditions=current_conditions,
        ai_api_configs=ai_api_configs,
    )
    observed = set(transport.observations)
    if observed != set(current_expected):
        raise ValueError("Exp5 endpoint/model execution coverage is incomplete")


def _expected_exp5_endpoint_observations(
    *,
    conditions: Sequence[object],
    ai_api_configs: Mapping[str, object],
) -> dict[tuple[str, str], str]:
    from tokenshare.executors.ai_api_request_identity import (
        _normalized_absolute_endpoint,
    )

    expected: dict[tuple[str, str], str] = {}
    for condition in conditions:
        config_id = getattr(condition, "provider_config_id", None)
        entry_id = getattr(condition, "model_entry_id", None)
        model_id = getattr(condition, "provider_model_id", None)
        digest = getattr(condition, "model_endpoint_identity_digest", None)
        config = ai_api_configs.get(config_id) if isinstance(config_id, str) else None
        entries = getattr(config, "entries", None)
        if (
            not isinstance(entry_id, str)
            or not isinstance(model_id, str)
            or not isinstance(digest, str)
            or not isinstance(entries, Sequence)
            or isinstance(entries, (str, bytes))
        ):
            raise ValueError("Exp5 endpoint authority is incomplete")
        matches = tuple(
            entry
            for entry in entries
            if getattr(entry, "entry_id", None) == entry_id
            and getattr(entry, "enabled", None) is True
        )
        if len(matches) != 1:
            raise ValueError("Exp5 endpoint authority is not unique")
        entry = matches[0]
        base_url = getattr(entry, "base_url", None)
        endpoint = getattr(entry, "endpoint", None)
        model = getattr(entry, "model", None)
        if (
            not isinstance(base_url, str)
            or not isinstance(endpoint, str)
            or not isinstance(model, str)
            or model != model_id
        ):
            raise ValueError("Exp5 endpoint/model authority drifted")
        observation = (_normalized_absolute_endpoint(base_url, endpoint), model)
        previous = expected.get(observation)
        if previous is not None and previous != digest:
            raise ValueError("Exp5 endpoint identity digest is ambiguous")
        expected[observation] = digest
    return expected


def _build_representative_service_authority(**kwargs: object):
    from tokenshare.experiments.run_paper_experiments import (
        build_results_first_execution_authority_for_selection,
    )

    return build_results_first_execution_authority_for_selection(**kwargs)


def _create_representative_exp5_transport() -> object:
    from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport

    return UrlLibSiliconFlowTransport()


def _create_representative_acquisition_transport() -> object:
    from tokenshare.executors.ai_api_transport import UrlLibDeepSeekTransport

    return UrlLibDeepSeekTransport()


def _representative_secret_resolver(name: str) -> str:
    import os

    return os.environ.get(name, "")


_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER = (
    _build_representative_service_authority
)
_RESULTS_FIRST_CLOSURE_SNAPSHOT_PERSISTER = (
    persist_results_first_closure_replay_snapshot
)
_REPRESENTATIVE_SMOKE_EXECUTOR = execute_representative_full_plan_smoke
_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY = _create_representative_exp5_transport
_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY = (
    _create_representative_acquisition_transport
)
_REPRESENTATIVE_SECRET_RESOLVER = _representative_secret_resolver
_REPRESENTATIVE_ACQUISITION_CRASH_HOOK: Callable[[str], None] | None = None


class _CountingRepresentativeTransport:
    def __init__(self, transport: object) -> None:
        self.transport = transport
        self._root_post_chat_completion = getattr(
            transport,
            "post_chat_completion",
            None,
        )
        self.provider_calls = 0
        self._observations: list[tuple[str, str]] = []
        self._instrumented_transports: dict[int, object] = {}
        self._lock = Lock()

    @property
    def tokenshare_offline_capturing_transport(self) -> bool:
        return (
            getattr(
                self.transport,
                "tokenshare_offline_capturing_transport",
                False,
            )
            is True
        )

    def tokenshare_transport_for_provider(self, provider_family: str) -> object:
        resolver = getattr(
            self.transport,
            "tokenshare_transport_for_provider",
            None,
        )
        if callable(resolver):
            resolved = resolver(provider_family)
        else:
            resolved = self.transport
        post_chat_completion = getattr(resolved, "post_chat_completion", None)
        if not callable(post_chat_completion):
            return resolved
        identity = id(resolved)
        with self._lock:
            if self._instrumented_transports.get(identity) is resolved:
                return resolved

            def counted_post_chat_completion(**kwargs: object) -> object:
                return self._post_chat_completion(
                    post_chat_completion,
                    **kwargs,
                )

            setattr(resolved, "post_chat_completion", counted_post_chat_completion)
            self._instrumented_transports[identity] = resolved
        return resolved

    def post_chat_completion(self, **kwargs: object) -> object:
        if not callable(self._root_post_chat_completion):
            raise TypeError("representative transport has no post_chat_completion")
        return self._post_chat_completion(
            self._root_post_chat_completion,
            **kwargs,
        )

    def _post_chat_completion(
        self,
        delegate: Callable[..., object],
        **kwargs: object,
    ) -> object:
        endpoint = kwargs.get("normalized_absolute_endpoint")
        body_bytes = kwargs.get("body_bytes")
        model: object = None
        if isinstance(body_bytes, bytes):
            try:
                body = json.loads(body_bytes.decode("utf-8"))
                if isinstance(body, Mapping):
                    model = body.get("model")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        with self._lock:
            self.provider_calls += 1
            if isinstance(endpoint, str) and isinstance(model, str):
                self._observations.append((endpoint, model))
        return delegate(**kwargs)

    @property
    def observations(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(self._observations)


def _acquire_representative_response_bank(
    *,
    authority: RepresentativeFullPlanSmokeServiceAuthority,
    resume: bool,
    supervised_no_response_closure_root: Path | None = None,
) -> RepresentativeAcquisitionStageResult:
    from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionPlanBundle,
        ResponseBankAcquisitionOrchestrator,
        ResultsFirstAcquisitionPlan,
        establish_results_first_acquisition_authorization,
        finalize_acquisition_child_bank,
        materialize_results_first_acquisition_bundle,
        results_first_response_bank_manifest_for_bundle,
    )

    if type(authority.bundle) is ResultsFirstAcquisitionPlan:
        bundle = materialize_results_first_acquisition_bundle(
            plan=authority.bundle,
            bundle_root=authority.bundle_root,
            resume=resume,
        )
    elif type(authority.bundle) is AcquisitionPlanBundle:
        bundle = authority.bundle
        bundle.validate()
    else:
        raise TypeError("results-first acquisition bundle type drift")
    acquisition_root = authority.output_root / "acquisition"
    authorization = establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=acquisition_root,
        output_mode="resume" if resume else "new_run",
        allow_provider_calls=True,
    )
    manifest = results_first_response_bank_manifest_for_bundle(
        bundle,
        authorization,
    )
    ledger = PaperBudgetLedger(
        acquisition_root / "acquisition_budget.v1.sqlite3",
        limits=bundle.full_budget.to_limits(),
    )
    ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )
    max_in_flight = int(bundle.max_acquisition_concurrency)
    if not 1 <= max_in_flight <= 10:
        raise ValueError("representative acquisition concurrency exceeds 10")
    transport = _CountingRepresentativeTransport(
        _REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY()
    )
    orchestrator = ResponseBankAcquisitionOrchestrator(
        output_root=acquisition_root,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        inventory_digest=bundle.inventory_digest,
        inventory_rows=bundle.inventory_rows,
        budget_ledger=ledger,
        facility_authorization=authorization,
        invocation_mode=authorization.output_mode,
        transport=transport,
        secret_resolver=_REPRESENTATIVE_SECRET_RESOLVER,
        now_epoch=int(_UTC_NOW().timestamp()),
        crash_hook=_REPRESENTATIVE_ACQUISITION_CRASH_HOOK,
    )
    zero_usage = RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    try:
        batch = _acquire_response_bank_after_supervised_no_response_closure(
            orchestrator=orchestrator,
            requests=bundle.acquisition_requests,
            max_in_flight=max_in_flight,
            resume=resume,
            supervised_no_response_closure_root=(
                supervised_no_response_closure_root
            ),
        )
    except Exception as exc:
        usage = RepresentativeCurrentProviderUsage(
            provider_calls=transport.provider_calls,
            spend=(0.0 if transport.provider_calls == 0 else None),
            spend_missing_reason=(
                None
                if transport.provider_calls == 0
                else "acquisition_terminal_accounting_unavailable"
            ),
        )
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="acquisition_dispatch",
            acquisition_usage=usage,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    usage: RepresentativeCurrentProviderUsage | None = None
    post_failure_stage = "acquisition_accounting"
    missing_spend_reason = "post_acquisition_usage_missing"
    try:
        usage = _acquisition_current_usage(
            batch=batch,
            ledger=ledger,
            inventory_digest=bundle.inventory_digest,
            current_provider_calls=transport.provider_calls,
        )
        if (
            batch.status != "complete"
            or batch.missing_inventory_entry_ids
            or batch.ambiguous_inventory_entry_ids
        ):
            raise RepresentativeSmokeExecutionError(
                batch.blocked_reason or "representative acquisition is incomplete",
                failure_stage="acquisition_terminal",
                acquisition_usage=usage,
                trace_usage=zero_usage,
                exp5_usage=zero_usage,
            )
        post_failure_stage = "acquisition_finalize"
        missing_spend_reason = "child_bank_finalize_failed"
        resolver = finalize_acquisition_child_bank(
            orchestrator=orchestrator,
            bundle=bundle,
            manifest=manifest,
            batch_result=batch,
        )
        if resolver is None:
            raise RepresentativeSmokeExecutionError(
                "representative immutable child bank was not finalized",
                failure_stage="acquisition_finalize",
                acquisition_usage=usage,
                trace_usage=zero_usage,
                exp5_usage=zero_usage,
            )
        from tokenshare.experiments.paper_exp1_trace_reuse import (
            build_exp1_attempt_history_authority,
            persist_exp1_attempt_history_authority,
        )

        attempt_history = build_exp1_attempt_history_authority(
            resolver=resolver,
            source_exp1_inventory_plan=bundle.semantic_inventory_plan,
            budget_ledger=ledger,
        )
        persist_exp1_attempt_history_authority(
            resolver_root=resolver.root_path,
            authority=attempt_history,
        )
    except RepresentativeSmokeExecutionError:
        raise
    except Exception as exc:
        recovered_usage = usage or _recover_acquisition_current_usage(
            batch=batch,
            ledger=ledger,
            inventory_digest=bundle.inventory_digest,
            current_provider_calls=transport.provider_calls,
            missing_spend_reason=missing_spend_reason,
        )
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage=post_failure_stage,
            acquisition_usage=recovered_usage,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    return RepresentativeAcquisitionStageResult(
        resolver=resolver,
        bundle=bundle,
        usage=usage,
        max_in_flight=max_in_flight,
    )


def _acquisition_current_usage(
    *,
    batch: object,
    ledger: object,
    inventory_digest: str,
    current_provider_calls: int,
) -> RepresentativeCurrentProviderUsage:
    return _acquisition_current_usage_from_ledger(
        batch=batch,
        ledger=ledger,
        inventory_digest=inventory_digest,
        current_provider_calls=current_provider_calls,
    )


def _recover_acquisition_current_usage(
    *,
    batch: object,
    ledger: object,
    inventory_digest: str,
    current_provider_calls: int,
    missing_spend_reason: str,
) -> RepresentativeCurrentProviderUsage:
    try:
        return _acquisition_current_usage_from_ledger(
            batch=batch,
            ledger=ledger,
            inventory_digest=inventory_digest,
            current_provider_calls=current_provider_calls,
        )
    except Exception:
        return RepresentativeCurrentProviderUsage(
            provider_calls=current_provider_calls,
            spend=(0.0 if current_provider_calls == 0 else None),
            spend_missing_reason=(
                None if current_provider_calls == 0 else missing_spend_reason
            ),
        )


def _acquisition_current_usage_from_ledger(
    *,
    batch: object,
    ledger: object,
    inventory_digest: str,
    current_provider_calls: int,
) -> RepresentativeCurrentProviderUsage:
    if current_provider_calls == 0:
        return RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    costs: list[float] = []
    missing = False
    invoked_results = tuple(
        result
        for result in getattr(batch, "results", ())
        if bool(getattr(result, "transport_invoked", False))
    )
    for result in invoked_results:
        entry = getattr(result, "entry", None)
        if entry is None:
            missing = True
            continue
        record = ledger.get_reservation(
            inventory_digest,
            entry.inventory_entry_id,
        )
        if (
            record is None
            or record.cost_estimate is None
            or bool(record.usage_missing)
        ):
            missing = True
            continue
        costs.append(float(record.cost_estimate))
    if missing or len(invoked_results) != current_provider_calls:
        return RepresentativeCurrentProviderUsage(
            provider_calls=current_provider_calls,
            spend=None,
            spend_missing_reason="acquisition_usage_missing",
        )
    return RepresentativeCurrentProviderUsage(
        provider_calls=current_provider_calls,
        spend=sum(costs),
    )


def _materialize_results_first_service_authority(
    *,
    authority: ResultsFirstExecutionAuthority,
    resume: bool,
) -> ResultsFirstExecutionAuthority:
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionPlanBundle,
        ResultsFirstAcquisitionPlan,
        materialize_results_first_acquisition_bundle,
    )

    if type(authority.bundle) is AcquisitionPlanBundle:
        authority.bundle.validate()
        return authority
    if type(authority.bundle) is not ResultsFirstAcquisitionPlan:
        raise TypeError("results-first service bundle type drift")
    materialized = materialize_results_first_acquisition_bundle(
        plan=authority.bundle,
        bundle_root=authority.bundle_root,
        resume=resume,
    )
    return replace(authority, bundle=materialized)


def _selection_exact_exp5_execution_projection(
    authority: ResultsFirstExecutionAuthority,
) -> object:
    """Project only selected Exp5 roots from the already-frozen typed authority."""

    from tokenshare.experiments.paper_budget import project_paper_execution_budget
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_execution_coverage,
    )

    selected = tuple(
        condition.condition_id
        for condition in authority.coverage.conditions
        if condition.experiment_id == _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    if not selected:
        raise ValueError("selection-exact Exp5 coverage is empty")
    root_filter = {
        condition_id: authority.coverage.root_case_filter[condition_id]
        for condition_id in selected
    }
    coverage = derive_paper_formal_execution_coverage(
        snapshot=authority.full_snapshot,
        dispatch_plans=authority.full_dispatch_plans,
        catalog_manifest=authority.catalog_manifest,
        selected_condition_ids=selected,
        root_case_filter=root_filter,
        # Exp5 是 full snapshot 的严格子域；即使父 authority 为 Full，独立
        # provider budget projection 仍必须是 filtered coverage。
        selection_kind="filtered",
    )
    return project_paper_execution_budget(
        snapshot=authority.full_snapshot,
        budget=authority.full_budget,
        coverage=coverage,
    )


def _results_first_formal_exp5_coverage(
    authority: ResultsFirstExecutionAuthority,
) -> object:
    """为正式 results-first Exp5 取唯一允许的 typed coverage。"""

    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first execution authority is required")
    coverage = authority.coverage
    selection_kind = getattr(coverage, "selection_kind", None)
    if selection_kind == "exp5_capability_smoke":
        raise ValueError(
            "capability pilot/smoke scope cannot enter formal Exp5 results-first execution"
        )
    if selection_kind in {"full", "filtered"}:
        if any(
            condition.experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
            for condition in coverage.conditions
        ):
            raise ValueError(
                "direct formal Exp5 coverage includes non-Exp5 conditions"
            )
        return coverage
    if selection_kind not in {
        "full_exp1_exp3_exp5",
        "representative_exp1_exp3_exp5",
    }:
        raise ValueError("formal Exp5 results-first scope selection is invalid")
    projection = _selection_exact_exp5_execution_projection(authority)
    exp5_coverage = getattr(projection, "coverage", None)
    if getattr(exp5_coverage, "selection_kind", None) not in {"full", "filtered"}:
        raise ValueError("formal Exp5 projection selection kind is invalid")
    selected_parent_conditions = tuple(
        condition
        for condition in coverage.conditions
        if condition.experiment_id == _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    selected_projected_conditions = tuple(
        condition
        for condition in exp5_coverage.conditions
        if condition.experiment_id == _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    if (
        not selected_parent_conditions
        or len(selected_parent_conditions) != len(selected_projected_conditions)
        or any(
            parent is not projected
            for parent, projected in zip(
                selected_parent_conditions,
                selected_projected_conditions,
                strict=True,
            )
        )
        or any(
            coverage.root_case_filter[condition.condition_id]
            != exp5_coverage.root_case_filter.get(condition.condition_id)
            for condition in selected_parent_conditions
        )
    ):
        raise ValueError("formal Exp5 projection does not match scope authority")
    if any(
        condition.experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
        for condition in exp5_coverage.conditions
    ):
        raise ValueError("formal Exp5 projection includes non-Exp5 conditions")
    return exp5_coverage


def _selection_exact_exp5_execution_projection_from_snapshot(
    snapshot: ResultsFirstClosureReplaySnapshot,
) -> object:
    """只从已验证 closure snapshot 投影当前 selection 的 Exp5 预算。"""

    from tokenshare.experiments.paper_budget import project_paper_execution_budget
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_execution_coverage,
    )

    if type(snapshot) is not ResultsFirstClosureReplaySnapshot:
        raise TypeError("typed closure snapshot is required for plan-only budget")
    selected = tuple(
        condition.condition_id
        for condition in snapshot.coverage.conditions
        if condition.experiment_id == _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    if not selected:
        raise ValueError("selection-exact Exp5 coverage is empty")
    root_filter = {
        condition_id: snapshot.coverage.root_case_filter[condition_id]
        for condition_id in selected
    }
    coverage = derive_paper_formal_execution_coverage(
        snapshot=snapshot.coverage.source_snapshot,
        dispatch_plans=snapshot.full_dispatch_plans,
        catalog_manifest=snapshot.catalog_manifest,
        selected_condition_ids=selected,
        root_case_filter=root_filter,
        selection_kind="filtered",
    )
    return project_paper_execution_budget(
        snapshot=snapshot.coverage.source_snapshot,
        budget=snapshot.full_budget,
        coverage=coverage,
    )


def _execute_results_first_service_input(
    *,
    request: PipelineCommandRequest,
    value: ResultsFirstServiceInput,
) -> dict[str, object]:
    """共享 adapter：typed full authority 成功后才允许构造 provider transport。"""

    authority = value.authority
    if (
        value.plan_only
        and value.selection
        in {
            "representative_exp1_exp3_exp5",
            "full_exp1_exp3_exp5",
        }
    ):
        if (
            type(authority) is not ResultsFirstExecutionAuthority
            or value.allow_provider_calls
            or value.paid_launch_readiness is not None
            or value.scope_preparation_root is None
            or request.output_root is None
            or authority.output_root != Path(request.output_root).resolve(strict=False)
            or authority.coverage.provider_calls_made != 0
            or authority.provider_budget_authority.to_dict().get("provider_calls_made")
            != 0
        ):
            raise TypeError("Exp4-excluded plan-only authority drift")
        preparation = _closure_snapshot_read_json(
            value.scope_preparation_root
            / "exp4_excluded_scope_preparation.v1.json",
            label="Exp4-excluded plan-only scope preparation",
        )
        plan_digest = preparation.get("preparation_plan_digest")
        profile_digest = getattr(request.profile, "profile_digest", None)
        admission_digest = getattr(
            request.profile,
            "prompt_admission_profile_digest",
            None,
        )
        if (
            not isinstance(plan_digest, str)
            or not plan_digest.startswith("sha256:")
            or not isinstance(profile_digest, str)
            or not isinstance(admission_digest, str)
        ):
            raise TypeError("Exp4-excluded plan-only receipt inputs are invalid")
        return {
            "selection": value.selection,
            "evidence_class": "results_first_plan_only_boundary",
            "exp4_excluded": True,
            "execution_performed": False,
            "protocol_calls": 0,
            "checker_calls": 0,
            "verifier_calls": 0,
            "metrics_calls": 0,
            "provider_calls": 0,
            "metrics_eligible": False,
            "publication_eligible": False,
            "fixed_denominator_validated": True,
            "source_validation_digest": authority.source_validation_digest,
            "source_snapshot_digest": authority.full_snapshot.snapshot_digest,
            "coverage_digest": authority.coverage.coverage_digest,
            "budget_digest": authority.provider_budget_authority.authority_digest,
            "profile_digest": profile_digest,
            "prompt_admission_profile_digest": admission_digest,
            "authorized_plan_digest": plan_digest,
            "inventory_digest": authority.full_prepared_inventory.inventory_digest,
            "condition_count": authority.coverage.condition_count,
            "root_run_count": authority.coverage.root_run_count,
            "provider_budget": authority.provider_budget_authority.to_dict(),
            "status": "ready",
        }
    if value.plan_only:
        if type(authority) is not ResultsFirstPaidPlanOnlyAuthority:
            raise TypeError(
                "typed results-first paid plan-only certificate is required"
            )
        if value.allow_provider_calls:
            raise ValueError("plan-only certificate cannot allow provider calls")
        _consume_paid_plan_only_certificate(authority)
        if (
            request.output_root is None
            or authority.output_root
            != Path(request.output_root).resolve(strict=False)
            or authority.selection != value.selection
            or authority.resume is not value.resume
        ):
            raise ValueError("results-first plan-only certificate mode drift")
        return {
            "selection": value.selection,
            "evidence_class": "results_first_plan_only_boundary",
            "execution_performed": False,
            "protocol_calls": 0,
            "checker_calls": 0,
            "verifier_calls": 0,
            "metrics_calls": 0,
            "provider_calls": 0,
            "metrics_eligible": False,
            "publication_eligible": False,
            "fixed_denominator_validated": True,
            "source_validation_digest": authority.source_validation_digest,
            "source_snapshot_digest": authority.source_snapshot_digest,
            "coverage_digest": authority.coverage_digest,
            "budget_digest": authority.provider_budget_authority.authority_digest,
            "profile_digest": authority.profile_digest,
            "condition_count": authority.condition_count,
            "root_run_count": authority.root_run_count,
            "provider_budget": authority.provider_budget_authority.to_dict(),
            "status": "ready",
        }
    if type(authority) is ResultsFirstPaidPlanOnlyAuthority:
        raise TypeError("plan-only certificate cannot authorize provider execution")
    if type(authority) is not ResultsFirstExecutionAuthority:
        raise TypeError("typed results-first execution authority is required")
    if authority.output_root != request.output_root:
        raise ValueError("results-first service output root mismatch")
    # 必须在任何 paid readiness 消耗、response-bank acquisition 或 Exp5 transport
    # 构造前拒绝 capability/pilot coverage，避免其进入正式 results-first 路径。
    _results_first_formal_exp5_coverage(authority)
    if value.allow_provider_calls:
        if value.selection in {
            "representative_exp1_exp3_exp5",
            "full_exp1_exp3_exp5",
        } and type(value.paid_authorization) is not PaidAuthorizationValidation:
            raise TypeError("Exp4-excluded paid execution requires exact receipt validation")
        readiness = value.paid_launch_readiness
        if (
            type(readiness) is not ResultsFirstPaidLaunchKeyReadiness
            or readiness.output_root != authority.output_root.resolve(strict=False)
            or readiness.provider_budget_authority_digest
            != authority.provider_budget_authority.authority_digest
        ):
            raise TypeError("paid execution requires exact one-shot key readiness")
        consume_results_first_paid_launch_key_readiness(readiness)
    elif value.paid_launch_readiness is not None:
        raise ValueError("provider-zero execution cannot carry paid readiness")
    elif value.paid_authorization is not None:
        raise ValueError("provider-zero execution cannot carry paid authorization")
    coverage = authority.coverage
    common = {
        "selection": value.selection,
        "evidence_class": "results_first_execution",
        "provider_calls": 0,
        "source_validation_digest": authority.source_validation_digest,
        "source_snapshot_digest": coverage.source_snapshot_digest,
        "coverage_digest": coverage.coverage_digest,
        "budget_digest": authority.provider_budget_authority.authority_digest,
        "profile_digest": getattr(
            authority.bundle,
            "profile_digest",
            coverage.coverage_digest,
        ),
        "condition_count": int(coverage.condition_count),
        "root_run_count": int(coverage.root_run_count),
    }
    if type(value.paid_authorization) is PaidAuthorizationValidation:
        common.update(
            {
                "receipt_digest": value.paid_authorization.receipt.receipt_digest,
                "output_marker_digest": value.paid_authorization.marker.marker_digest,
            }
        )
    if authority.source_rebind_binding_digest is not None:
        common.update(
            {
                "source_rebind_binding_digest": (
                    authority.source_rebind_binding_digest
                ),
                "source_rebind_ref_digest": authority.source_rebind_ref_digest,
            }
        )
    authority = _materialize_results_first_service_authority(
        authority=authority,
        resume=value.resume,
    )
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )
    from tokenshare.experiments.paper_formal_runner import (
        APPROVED_ENDPOINT_BINDINGS_KEY,
    )
    from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

    if type(authority.bundle) is not AcquisitionPlanBundle:
        raise TypeError("results-first exact acquisition bundle is missing")
    if any(
        ref.get("experiment_id") != "exp1_real_ai_feasibility"
        for ref in authority.bundle.semantic_inventory_plan.condition_refs
    ):
        raise ValueError("results-first paid acquisition bundle is not Exp1-only")
    derived_provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            _selection_exact_exp5_execution_projection(authority)
        ),
        exp5_pricing_authority=(
            authority.ai_api_configs.get(APPROVED_ENDPOINT_BINDINGS_KEY, {}).get(
                "exp5_real_ai_model_endpoint_comparison"
            )
        ),
        exp1_acquisition_is_new=_results_first_exp1_acquisition_is_new(
            selection=value.selection,
            external_bank_root=value.external_bank_root,
        ),
    )
    provider_budget = authority.provider_budget_authority
    if provider_budget != derived_provider_budget:
        raise ValueError(
            "results-first execution requires the exact prepared provider budget"
        )
    common["provider_budget"] = provider_budget.to_dict()
    if not value.allow_provider_calls:
        raise ValueError("results-first execution requires --allow-provider-calls")
    trace_experiment_ids = tuple(
        experiment_id
        for experiment_id in _results_first_experiment_ids_for_coverage(
            coverage=authority.coverage
        )
        if experiment_id != _REPRESENTATIVE_EXP5_EXPERIMENT_ID
    )
    closure_snapshot_ref = _RESULTS_FIRST_CLOSURE_SNAPSHOT_PERSISTER(
        authority=authority,
        output_root=authority.output_root,
        suite_id=(
            "results_first_exp1_exp4_trace"
            if trace_experiment_ids == _REPRESENTATIVE_TRACE_EXPERIMENT_IDS
            else "results_first_exp1_exp3_trace"
        ),
    )
    closure_snapshot_ref_path = (
        authority.output_root
        / _CLOSURE_REPLAY_DIRECTORY
        / _CLOSURE_REPLAY_REF_NAME
    ).resolve(strict=False)
    common.update(
        {
            "closure_replay_snapshot_ref": dict(closure_snapshot_ref),
            "closure_replay_snapshot_ref_path": (
                closure_snapshot_ref_path.as_posix()
            ),
        }
    )
    if value.external_bank_root is None:
        acquisition = _acquire_representative_response_bank(
            authority=authority,
            resume=value.resume,
            supervised_no_response_closure_root=(
                value.supervised_no_response_closure_root
            ),
        )
        resolver = acquisition.resolver
        acquisition_usage = acquisition.usage
        if (
            acquisition.bundle is not None
            and acquisition.bundle is not authority.bundle
        ):
            authority = replace(authority, bundle=acquisition.bundle)
    else:
        resolver = ExternalBankResolverBinding(
            root=value.external_bank_root.resolve(strict=False)
        ).open()
        acquisition_usage = RepresentativeCurrentProviderUsage(
            provider_calls=0,
            spend=0.0,
        )
    zero_usage = RepresentativeCurrentProviderUsage(provider_calls=0, spend=0.0)
    try:
        terminal = _REPRESENTATIVE_SMOKE_EXECUTOR(
            authority=authority,
            response_bank_resolver=resolver,
            exp5_transport_factory=_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY,
            acquisition_usage=acquisition_usage,
        )
    except RepresentativeSmokeExecutionError:
        raise
    except Exception as exc:
        raise RepresentativeSmokeExecutionError(
            str(exc),
            failure_stage="representative_smoke_executor",
            acquisition_usage=acquisition_usage,
            trace_usage=zero_usage,
            exp5_usage=zero_usage,
        ) from exc
    return {
        **common,
        "status": terminal.status,
        "provider_calls": int(terminal.total_current_provider_calls),
        "total_current_provider_calls": int(
            terminal.total_current_provider_calls
        ),
        "condition_count": int(terminal.expected_condition_count),
        "root_run_count": int(terminal.expected_root_count),
        "acquisition_current_provider_calls": int(
            terminal.acquisition_current_provider_calls
        ),
        "acquisition_current_spend": terminal.acquisition_current_spend,
        "acquisition_spend_missing_reason": (
            terminal.acquisition_spend_missing_reason
        ),
        "trace_current_provider_calls": int(
            terminal.trace_current_provider_calls
        ),
        "trace_current_spend": terminal.trace_current_spend,
        "trace_spend_missing_reason": terminal.trace_spend_missing_reason,
        "exp5_current_provider_calls": int(
            terminal.exp5_current_provider_calls
        ),
        "exp5_current_spend": terminal.exp5_current_spend,
        "exp5_spend_missing_reason": terminal.exp5_spend_missing_reason,
        "total_current_spend": terminal.total_current_spend,
        "total_spend_missing_reason": terminal.total_spend_missing_reason,
    }


def _finish_acquisition_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> AcquisitionServiceInput:
    from tokenshare.executors.ai_api_transport import (
        UrlLibDeepSeekTransport,
        UrlLibOpenAITransport,
        UrlLibSiliconFlowTransport,
    )
    from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstAcquisitionAuthorization,
        load_acquisition_plan_bundle,
        results_first_response_bank_manifest_for_bundle,
        response_bank_manifest_for_bundle,
    )

    bundle = load_acquisition_plan_bundle(request.plan_bundle_root)
    if bundle.authorized_plan_digest != request.plan_digest:
        raise ValueError("acquisition bundle plan digest mismatch")
    if bundle.inventory_digest != request.inventory_digest:
        raise ValueError("acquisition bundle inventory digest mismatch")
    if bundle.profile_digest != request.profile.profile_digest:
        raise ValueError("acquisition bundle profile digest mismatch")
    paid_authorization = request.provider_authorization
    facility_authorization = None
    if type(paid_authorization) is PaidAuthorizationValidation:
        manifest = response_bank_manifest_for_bundle(bundle, paid_authorization)
    elif type(paid_authorization) is ResultsFirstAcquisitionAuthorization:
        facility_authorization = paid_authorization
        paid_authorization = None
        manifest = results_first_response_bank_manifest_for_bundle(
            bundle, facility_authorization
        )
    else:
        raise ValueError("acquire-bank requires an explicit acquisition authority")
    authorization_arguments = (
        {"paid_authorization": paid_authorization}
        if paid_authorization is not None
        else {"facility_authorization": facility_authorization}
    )
    ledger = PaperBudgetLedger(
        request.output_root / "acquisition_budget.v1.sqlite3",
        limits=bundle.full_budget.to_limits(),
    )
    ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )

    class OfficialTransportSet:
        _transports = {
            "deepseek": UrlLibDeepSeekTransport(),
            "openai": UrlLibOpenAITransport(),
            "siliconflow": UrlLibSiliconFlowTransport(),
        }

        def tokenshare_transport_for_provider(self, provider_family: str):
            try:
                return self._transports[provider_family]
            except KeyError as exc:
                raise ValueError("unsupported acquisition provider family") from exc

    def resolve_secret(name: str) -> str:
        import os

        return os.environ.get(name, "")

    return AcquisitionServiceInput(
        scope=request.scope,
        orchestrator_arguments={
            "output_root": request.output_root,
            "bank_root_id": manifest.bank_root_id,
            "manifest_digest": manifest.manifest_digest,
            "inventory_digest": bundle.inventory_digest,
            "inventory_rows": bundle.inventory_rows,
            "budget_ledger": ledger,
            **authorization_arguments,
            "invocation_mode": request.provider_authorization.output_mode,
            "transport": OfficialTransportSet(),
            "secret_resolver": resolve_secret,
            "now_epoch": int(_UTC_NOW().timestamp()),
        },
        acquisition_requests=bundle.acquisition_requests,
        max_in_flight=bundle.max_acquisition_concurrency,
        bundle=bundle,
        manifest=manifest,
        supervised_no_response_closure_root=(
            None
            if request.serialized_arguments.get(
                "supervised_no_response_closure_root"
            )
            is None
            else Path(
                str(
                    request.serialized_arguments[
                        "supervised_no_response_closure_root"
                    ]
                )
            )
        ),
    )


def _formal_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> FormalSuiteServiceInput:
    if request.command not in {
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-online",
    }:
        raise ValueError("unsupported formal service authority command")
    if request.output_root is None:
        raise ValueError(f"{request.command} requires an output root")
    if request.command != "run-trace" and type(
        request.provider_authorization
    ) is not PaidAuthorizationValidation:
        raise ValueError(f"{request.command} requires Task26 paid authorization")
    authority = request._formal_authority
    if authority is None:
        bindings = ()
        if request.provider_authorization is not None:
            receipt = request.provider_authorization.receipt
            bindings = (
                _paid_scope_binding(
                    validation=request.provider_authorization,
                    scope=request.scope,
                    authorized_plan_digest=str(request.plan_digest),
                    profile_digest=request.profile.profile_digest,
                    budget_digest=receipt.budget_digest,
                    inventory_digest=str(request.inventory_digest),
                    prompt_admission_profile_digest=(
                        request.profile.prompt_admission_profile_digest
                    ),
                    output_root=request.output_root,
                ),
            )
        authority = _FORMAL_AUTHORITY_BUILDER(
            command=request.command,
            profile=request.profile,
            output_root=request.output_root,
            resume=bool(request.serialized_arguments.get("resume", False)),
            plan_bundle_root=request.plan_bundle_root,
            external_bank_resolver=request.external_bank_resolver,
            paid_authorization_bindings=bindings,
        )
    if getattr(authority, "plan_digest", None) != request.plan_digest:
        raise ValueError("formal authority plan digest mismatch")
    if getattr(authority, "inventory_digest", None) != request.inventory_digest:
        raise ValueError("formal authority inventory digest mismatch")
    if request.command != "run-trace":
        receipt = request.provider_authorization.receipt
        if getattr(authority, "budget_digest", None) != receipt.budget_digest:
            raise ValueError("formal authority receipt budget digest mismatch")
    keyword_arguments = getattr(authority, "keyword_arguments", None)
    if not isinstance(keyword_arguments, Mapping):
        raise ValueError("formal authority keyword arguments are missing")
    resolved_arguments = dict(keyword_arguments)
    if (
        request.command == "run-online-checks"
        and "online_root_callback_factory" not in resolved_arguments
    ):
        from decimal import Decimal

        from tokenshare.experiments.paper_budget import PaperBudgetLimits
        from tokenshare.experiments.paper_budget_ledger import (
            L3_ONLINE_CHECK_CATEGORY_POLICY,
            PaperBudgetLedger,
        )
        from tokenshare.experiments.paper_formal_callbacks import (
            PaperOnlineRootCallbackFactory,
        )

        profile_budget = request.profile.budget
        ledger = PaperBudgetLedger(
            request.output_root / "online_checks_budget.v1.sqlite3",
            limits=PaperBudgetLimits(
                calls=int(profile_budget.calls_hard_limit),
                tokens=int(profile_budget.tokens_hard_limit),
                cny=Decimal(profile_budget.cny_reservation_hard_limit),
                deepseek_cumulative_cny=Decimal(
                    profile_budget.cny_absolute_hard_stop
                ),
            ),
            category_policy=L3_ONLINE_CHECK_CATEGORY_POLICY,
        )
        resolved_arguments["online_root_callback_factory"] = (
            PaperOnlineRootCallbackFactory(
                budget_ledger=ledger,
                token_upper_bound=int(profile_budget.tokens_per_call),
                cost_upper_bound=Decimal(profile_budget.cny_per_call_reservation),
                prompt_admission_profile_digest=(
                    request.profile.prompt_admission_profile_digest
                ),
            )
        )
    return FormalSuiteServiceInput(
        scope=request.scope,
        keyword_arguments=resolved_arguments,
    )


def _smoke_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> FormalSuiteServiceInput:
    if request.command != "run-exp5-capability-smoke" or request.scope != (
        "exp5_capability_smoke"
    ):
        raise ValueError("full Exp5 scope cannot use capability smoke factory")
    if request.output_root is None:
        raise ValueError("capability smoke requires an output root")
    if type(request.provider_authorization) is not PaidAuthorizationValidation:
        raise ValueError("capability smoke requires Task26 paid authorization")
    preparation_root = request.serialized_arguments.get(
        "results_first_scope_preparation_root"
    )
    if not isinstance(preparation_root, str) or not preparation_root:
        raise ValueError(
            "capability smoke requires results-first scope preparation root"
        )
    local_config_path = request.serialized_arguments.get("local_ai_api_config")
    if not isinstance(local_config_path, str) or not local_config_path:
        raise ValueError("capability smoke requires local AI API config")
    _EXP5_CAPABILITY_KEY_INJECTOR(local_config_path)
    authority = _SMOKE_AUTHORITY_BUILDER(
        profile=request.profile,
        output_root=request.output_root,
        resume=bool(request.serialized_arguments.get("resume", False)),
        paid_authorization=request.provider_authorization,
        results_first_scope_preparation_root=preparation_root,
    )
    if getattr(authority, "scope", None) != request.scope:
        raise ValueError("capability smoke authority scope mismatch")
    if getattr(authority, "plan_digest", None) != request.plan_digest:
        raise ValueError("capability smoke authority plan digest mismatch")
    if getattr(authority, "inventory_digest", None) != request.inventory_digest:
        raise ValueError("capability smoke authority inventory digest mismatch")
    if getattr(authority, "budget_digest", None) != (
        request.provider_authorization.receipt.budget_digest
    ):
        raise ValueError("capability smoke receipt budget digest mismatch")
    keyword_arguments = getattr(authority, "keyword_arguments", None)
    if not isinstance(keyword_arguments, Mapping):
        raise ValueError("capability smoke keyword arguments are missing")
    classification = keyword_arguments.get("execution_plan")
    if getattr(classification, "execution_plan_digest", None) != request.inventory_digest:
        raise ValueError("capability smoke execution plan digest mismatch")
    return FormalSuiteServiceInput(
        scope=request.scope,
        keyword_arguments=dict(keyword_arguments),
    )


def _render_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> ReportRenderServiceInput:
    if request.output_root is None:
        raise ValueError("render requires an output root")
    from tokenshare.experiments.paper_formal_runner import (
        recompute_paper_formal_metrics_from_runner_inputs,
    )
    from tokenshare.experiments.paper_metric_contract import (
        load_paper_metric_contract,
    )

    return ReportRenderServiceInput(
        metrics=recompute_paper_formal_metrics_from_runner_inputs(
            request.output_root
        ),
        contract=load_paper_metric_contract(),
    )


def _lineage_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> CellLineageAuditServiceInput:
    if request.output_root is None:
        raise ValueError("audit-cell-lineage requires an output root")
    from tokenshare.experiments.paper_formal_runner import (
        recompute_paper_formal_metrics_from_runner_inputs,
    )
    from tokenshare.experiments.paper_metric_contract import (
        load_paper_metric_contract,
    )

    metrics = recompute_paper_formal_metrics_from_runner_inputs(request.output_root)
    return CellLineageAuditServiceInput(
        contract=load_paper_metric_contract(),
        observations=tuple(metrics.metric_observations),
    )


def _results_first_service_input_from_cli_authority(
    request: PipelineCommandRequest,
) -> ResultsFirstServiceInput:
    arguments = request.serialized_arguments
    selection = arguments.get("selection")
    if selection not in {
        "full",
        "representative",
        "full_exp1_exp3_exp5",
        "representative_exp1_exp3_exp5",
    }:
        raise ValueError("results-first selection is invalid")
    if request.output_root is None:
        raise ValueError("results-first execution requires an output root")
    planning_root = arguments.get("planning_artifact_root")
    bundle_root = arguments.get("plan_bundle_root")
    if not isinstance(planning_root, str) or not isinstance(bundle_root, str):
        raise ValueError("results-first planning roots are missing")
    paid_restore_names = (
        "frozen_closure_snapshot_root",
        "frozen_prepared_inventory_root",
        "expected_preflight",
    )
    paid_restore_values = tuple(arguments.get(name) for name in paid_restore_names)
    present_count = sum(value is not None for value in paid_restore_values)
    plan_only = bool(arguments.get("plan_only"))
    allow_provider_calls = bool(arguments.get("allow_provider_calls"))
    paid_launch_readiness: object | None = None
    if plan_only == allow_provider_calls:
        raise ValueError(
            "results-first requires exactly one execution mode: "
            "--plan-only XOR --allow-provider-calls"
        )
    scope_selections = {
        "full_exp1_exp3_exp5",
        "representative_exp1_exp3_exp5",
    }
    if selection in scope_selections:
        if present_count != 0:
            raise ValueError(
                "Exp4-excluded selection cannot consume legacy paid restore authority"
            )
        raw_scope_preparation = arguments.get(
            "results_first_scope_preparation_root"
        )
        if (
            not isinstance(raw_scope_preparation, str)
            or not raw_scope_preparation
        ):
            raise ValueError(
                "Exp4-excluded selection requires scope preparation authority"
            )
        authority = load_exp4_excluded_results_first_execution_authority_v1(
            scope_preparation_root=Path(raw_scope_preparation),
            plan_bundle_root=Path(bundle_root),
            planning_artifact_root=Path(planning_root),
            output_root=request.output_root,
            selection=selection,
            resume=bool(arguments.get("resume")),
            full_budget_approval_authority=arguments.get(
                "full_budget_approval_authority"
            ),
            external_bank_root=arguments.get("external_bank_root"),
        )
        paid_authorization: object | None = None
        if allow_provider_calls:
            raw_receipt = arguments.get("receipt")
            raw_key_config = arguments.get("paid_launch_key_config")
            raw_attestation = arguments.get("paid_launch_attestation_root")
            profile_digest = getattr(request.profile, "profile_digest", None)
            admission_digest = getattr(
                request.profile,
                "prompt_admission_profile_digest",
                None,
            )
            if (
                not isinstance(raw_receipt, str)
                or not raw_receipt
                or not isinstance(raw_key_config, str)
                or not raw_key_config
                or not isinstance(raw_attestation, str)
                or not raw_attestation
                or not isinstance(profile_digest, str)
                or not isinstance(admission_digest, str)
            ):
                raise ValueError(
                    "Exp4-excluded paid execution requires receipt, local key config, "
                    "fresh attestation root, and pipeline profile"
                )
            preparation = _closure_snapshot_read_json(
                Path(raw_scope_preparation)
                / "exp4_excluded_scope_preparation.v1.json",
                label="Exp4-excluded paid scope preparation",
            )
            plan_digest = preparation.get("preparation_plan_digest")
            if not isinstance(plan_digest, str) or not plan_digest.startswith("sha256:"):
                raise ValueError("Exp4-excluded paid plan digest is invalid")
            output = request.output_root.resolve(strict=False)
            attestation_root = Path(raw_attestation).resolve(strict=False)
            key_config = Path(raw_key_config).resolve(strict=False)
            sources = tuple(
                Path(value).resolve(strict=False)
                for value in (
                    raw_scope_preparation,
                    bundle_root,
                    planning_root,
                    raw_key_config,
                )
            )
            if (
                not key_config.is_file()
                or output == attestation_root
                or output in attestation_root.parents
                or attestation_root in output.parents
                or any(
                    target == source
                    or target in source.parents
                    or source in target.parents
                    for target in (output, attestation_root)
                    for source in sources
                )
                or (
                    not bool(arguments.get("resume"))
                    and (output.exists() or attestation_root.exists())
                )
            ):
                raise ValueError("Exp4-excluded paid launch topology is invalid")
            paid_authorization = _RECEIPT_VALIDATOR(
                receipt=_RECEIPT_LOADER(raw_receipt),
                requested_scope=results_first_paid_scope_for_selection(selection),
                authorized_plan_digest=plan_digest,
                profile_digest=profile_digest,
                budget_digest=authority.provider_budget_authority.authority_digest,
                inventory_digest=authority.full_prepared_inventory.inventory_digest,
                prompt_admission_profile_digest=admission_digest,
                selected_experiments=("exp1", "exp5"),
                output_root=output,
                output_mode=(
                    "resume" if bool(arguments.get("resume")) else "new_run"
                ),
                action="dispatch",
                allow_provider_calls=True,
                now=_UTC_NOW(),
            )
            _PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER(
                attestation_root=attestation_root
            )
            statuses = _PAID_LAUNCH_LOCAL_CONFIG_LOADER(
                key_config_path=key_config
            )
            attestation = _PAID_LAUNCH_ATTESTATION_WRITER(
                attestation_root=attestation_root,
                authority=authority,
                statuses=statuses,
                expected_provider_budget_digest=(
                    authority.provider_budget_authority.authority_digest
                ),
            )
            paid_launch_readiness = _mint_results_first_paid_launch_key_readiness(
                attestation_root=attestation_root,
                attestation=attestation,
                output_root=output,
                expected_provider_budget_digest=(
                    authority.provider_budget_authority.authority_digest
                ),
            )
        elif any(
            arguments.get(name) is not None
            for name in (
                "receipt",
                "paid_launch_key_config",
                "paid_launch_attestation_root",
            )
        ):
            raise ValueError("Exp4-excluded plan-only cannot carry paid launch inputs")
        external_root = arguments.get("external_bank_root")
        if external_root is not None and not isinstance(external_root, str):
            raise ValueError("results-first external bank root is invalid")
        if external_root is not None:
            preparation = _closure_snapshot_read_json(
                Path(raw_scope_preparation)
                / "exp4_excluded_scope_preparation.v1.json",
                label="Exp4-excluded external bank scope preparation",
            )
            validate_results_first_external_bank_binding(
                external_bank_root=external_root,
                preparation=preparation,
                authority=authority,
                selection=selection,
            )
        return ResultsFirstServiceInput(
            authority=authority,
            selection=selection,
            plan_only=plan_only,
            allow_provider_calls=allow_provider_calls,
            external_bank_root=(
                None if external_root is None else Path(external_root)
            ),
            resume=bool(arguments.get("resume")),
            paid_launch_readiness=paid_launch_readiness,
            paid_authorization=paid_authorization,
            scope_preparation_root=Path(raw_scope_preparation),
        )
    if present_count not in {0, len(paid_restore_values)}:
        raise ValueError("results-first paid restore flags must be provided together")
    if (plan_only or allow_provider_calls) and present_count != len(
        paid_restore_values
    ):
        raise ValueError(
            "results-first plan-only or paid execution requires frozen restore authority"
        )
    if present_count:
        if selection in {
            "full_exp1_exp3_exp5",
            "representative_exp1_exp3_exp5",
        }:
            raise ValueError(
                "Exp4-excluded selection requires fresh scope-specific restore authority"
            )
        if any(not isinstance(value, str) or not value for value in paid_restore_values):
            raise ValueError("results-first paid restore path is invalid")
        expected_provider_budget_digest = _paid_restore_require_sha256(
            arguments.get("expected_provider_budget_digest"),
            label="provider budget",
        )
        output = request.output_root.resolve(strict=False)
        planning = Path(planning_root).resolve(strict=False)
        bundle = Path(bundle_root).resolve(strict=False)
        frozen_snapshot = Path(paid_restore_values[0]).resolve(strict=False)
        frozen_inventory = Path(paid_restore_values[1]).resolve(strict=False)
        preflight_path = Path(paid_restore_values[2]).resolve(strict=False)
        raw_source_rebind = arguments.get("source_rebind_root")
        if raw_source_rebind is not None and (
            not isinstance(raw_source_rebind, str) or not raw_source_rebind
        ):
            raise ValueError("paid source rebind root is invalid")
        source_rebind = (
            Path(raw_source_rebind).resolve(strict=False)
            if raw_source_rebind is not None
            else None
        )
        if plan_only:
            if any(
                arguments.get(name) is not None
                for name in (
                    "paid_reference_binding_root",
                    "paid_launch_key_config",
                    "paid_launch_attestation_root",
                )
            ):
                raise ValueError("plan-only cannot consume paid launch readiness inputs")
            source_paths = (
                frozen_snapshot,
                frozen_inventory,
                preflight_path,
                planning,
                bundle,
                *((source_rebind,) if source_rebind is not None else ()),
            )
            if any(
                output == source
                or output in source.parents
                or source in output.parents
                for source in source_paths
            ):
                raise ValueError("paid restore source and output paths must be separated")
            if not planning.is_dir() or not bundle.is_dir():
                raise ValueError("paid restore prepared plan authorities are missing")
            if not bool(arguments.get("resume")) and output.exists():
                raise ValueError("paid restore new-run output target must be fresh")
            authority = load_results_first_paid_plan_only_authority_v2(
                frozen_snapshot_root=frozen_snapshot,
                frozen_prepared_inventory_root=frozen_inventory,
                expected_preflight=preflight_path,
                source_rebind_root=source_rebind,
                plan_bundle_root=bundle,
                planning_artifact_root=planning,
                output_root=output,
                selection=selection,
                resume=bool(arguments.get("resume")),
                expected_provider_budget_digest=expected_provider_budget_digest,
            )
        else:
            raw_reference = arguments.get("paid_reference_binding_root")
            raw_key_config = arguments.get("paid_launch_key_config")
            raw_attestation = arguments.get("paid_launch_attestation_root")
            if (
                source_rebind is None
                or any(
                    not isinstance(value, str) or not value
                    for value in (raw_reference, raw_key_config, raw_attestation)
                )
            ):
                raise ValueError(
                    "paid execution requires source rebind, reference binding, "
                    "local key config, and fresh attestation root"
                )
            reference_root = Path(raw_reference).resolve(strict=False)
            key_config_path = Path(raw_key_config).resolve(strict=False)
            attestation_root = Path(raw_attestation).resolve(strict=False)
            _validate_results_first_paid_launch_topology(
                frozen_snapshot_root=frozen_snapshot,
                frozen_prepared_inventory_root=frozen_inventory,
                expected_preflight=preflight_path,
                source_rebind_root=source_rebind,
                plan_bundle_root=bundle,
                planning_artifact_root=planning,
                paid_reference_binding_root=reference_root,
                output_root=output,
                paid_launch_attestation_root=attestation_root,
                paid_launch_key_config=key_config_path,
                resume=bool(arguments.get("resume")),
            )
            authority = _PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER(
                paid_reference_binding_root=reference_root,
                frozen_snapshot_root=frozen_snapshot,
                frozen_prepared_inventory_root=frozen_inventory,
                expected_preflight=preflight_path,
                source_rebind_root=source_rebind,
                plan_bundle_root=bundle,
                planning_artifact_root=planning,
                output_root=output,
                selection=selection,
                resume=bool(arguments.get("resume")),
                expected_provider_budget_digest=expected_provider_budget_digest,
            )
            _PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER(
                attestation_root=attestation_root
            )
            statuses = _PAID_LAUNCH_LOCAL_CONFIG_LOADER(
                key_config_path=key_config_path
            )
            attestation = _PAID_LAUNCH_ATTESTATION_WRITER(
                attestation_root=attestation_root,
                authority=authority,
                statuses=statuses,
                expected_provider_budget_digest=expected_provider_budget_digest,
            )
            paid_launch_readiness = _mint_results_first_paid_launch_key_readiness(
                attestation_root=attestation_root,
                attestation=attestation,
                output_root=output,
                expected_provider_budget_digest=expected_provider_budget_digest,
            )
    else:
        authority = _REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER(
            selection=selection,
            output_root=request.output_root,
            planning_artifact_root=Path(planning_root),
            plan_bundle_root=Path(bundle_root),
            resume=bool(arguments.get("resume")),
        )
    external_root = arguments.get("external_bank_root")
    if external_root is not None and not isinstance(external_root, str):
        raise ValueError("results-first external bank root is invalid")
    raw_supervised_closure = arguments.get(
        "supervised_no_response_closure_root"
    )
    if raw_supervised_closure is not None and (
        not isinstance(raw_supervised_closure, str) or not raw_supervised_closure
    ):
        raise ValueError("supervised no-response closure root is invalid")
    if raw_supervised_closure is not None and not bool(arguments.get("resume")):
        raise ValueError("supervised no-response closure requires resume")
    return ResultsFirstServiceInput(
        authority=authority,
        selection=selection,
        plan_only=plan_only,
        allow_provider_calls=allow_provider_calls,
        external_bank_root=(
            None if external_root is None else Path(external_root)
        ),
        resume=bool(arguments.get("resume")),
        paid_launch_readiness=paid_launch_readiness,
        supervised_no_response_closure_root=(
            None
            if raw_supervised_closure is None
            else Path(raw_supervised_closure)
        ),
    )


def _results_first_metric_merge_audit_service_input_from_cli(
    request: PipelineCommandRequest,
) -> ResultsFirstMetricMergeAuditServiceInput:
    """严格收窄只读分母审计 argv，拒绝任何 execution/receipt 参数。"""

    if request.command != "audit-results-first-metric-merge":
        raise ValueError("results-first metric merge audit command is invalid")
    if request.output_root is None:
        raise ValueError("results-first metric merge audit requires an output root")
    arguments = request.serialized_arguments
    if set(arguments) != {
        "command",
        "selection",
        "output_root",
        "audit_output_root",
    }:
        raise ValueError("results-first metric merge audit arguments are invalid")
    selection = arguments.get("selection")
    output_root = arguments.get("output_root")
    audit_output_root = arguments.get("audit_output_root")
    if (
        selection != _RESULTS_FIRST_METRIC_MERGE_SELECTION
        or not isinstance(output_root, str)
        or not output_root
        or not isinstance(audit_output_root, str)
        or not audit_output_root
    ):
        raise ValueError("results-first metric merge audit arguments are invalid")
    resolved_output = Path(output_root).resolve(strict=False)
    if request.output_root.resolve(strict=False) != resolved_output:
        raise ValueError("results-first metric merge audit output root drift")
    return ResultsFirstMetricMergeAuditServiceInput(
        output_root=resolved_output,
        audit_output_root=Path(audit_output_root).resolve(strict=False),
        selection=selection,
    )


def _results_first_combined_render_service_input_from_cli(
    request: PipelineCommandRequest,
) -> ResultsFirstCombinedRenderServiceInput:
    """严格收窄 combined renderer 的纯离线 argv，不允许任何 paid/transport 输入。"""

    if request.command != "render-results-first-combined":
        raise ValueError("results-first combined render command is invalid")
    if request.output_root is None:
        raise ValueError("results-first combined render requires an output root")
    arguments = request.serialized_arguments
    if set(arguments) != {
        "command",
        "selection",
        "output_root",
        "metric_merge_audit_root",
        "combined_output_root",
    }:
        raise ValueError("results-first combined render arguments are invalid")
    selection = arguments.get("selection")
    output_root = arguments.get("output_root")
    metric_merge_audit_root = arguments.get("metric_merge_audit_root")
    combined_output_root = arguments.get("combined_output_root")
    if (
        selection != _RESULTS_FIRST_METRIC_MERGE_SELECTION
        or not isinstance(output_root, str)
        or not output_root
        or not isinstance(metric_merge_audit_root, str)
        or not metric_merge_audit_root
        or not isinstance(combined_output_root, str)
        or not combined_output_root
    ):
        raise ValueError("results-first combined render arguments are invalid")
    resolved_output = Path(output_root).resolve(strict=False)
    if request.output_root.resolve(strict=False) != resolved_output:
        raise ValueError("results-first combined render output root drift")
    return ResultsFirstCombinedRenderServiceInput(
        output_root=resolved_output,
        metric_merge_audit_root=Path(metric_merge_audit_root).resolve(strict=False),
        combined_output_root=Path(combined_output_root).resolve(strict=False),
        selection=selection,
    )


_PRODUCTION_SERVICE_INPUT_FACTORIES: Mapping[str, ServiceInputFactory] = MappingProxyType(
    {
        "validate-profile": _no_service_input,
        "plan-bank": _no_service_input,
        "acquire-bank": _acquisition_service_input_from_persisted_authorities,
        "audit-bank": _no_service_input,
        "run-trace": _formal_service_input_from_persisted_authorities,
        "run-online-checks": _formal_service_input_from_persisted_authorities,
        "run-exp1-online": _formal_service_input_from_persisted_authorities,
        "run-exp5-capability-smoke": _smoke_service_input_from_persisted_authorities,
        "run-exp5-online": _formal_service_input_from_persisted_authorities,
        "render": _render_service_input_from_persisted_authorities,
        "replay": _no_service_input,
        "audit-cell-lineage": _lineage_service_input_from_persisted_authorities,
        "validate-formal-execution-gate": _provided_gate_service_input,
        "validate-paper-publication-gate": _provided_gate_service_input,
        "run-results-first": _results_first_service_input_from_cli_authority,
        "representative-full-plan-smoke": (
            _results_first_service_input_from_cli_authority
        ),
        "audit-results-first-metric-merge": (
            _results_first_metric_merge_audit_service_input_from_cli
        ),
        "render-results-first-combined": (
            _results_first_combined_render_service_input_from_cli
        ),
    }
)


def _formal_run_gate_input(request: PipelineCommandRequest) -> FormalRunGateServiceInput:
    from tokenshare.experiments.paper_formal_gate import (
        PaperGatePrerequisiteEnvelope,
        PaperGateSelectionEnvelope,
    )

    authority = request._formal_authority
    selection = getattr(authority, "gate_selection", None)
    prerequisites = getattr(authority, "gate_prerequisites", None)
    publication_factory = getattr(authority, "publication_gate_factory", None)
    if not isinstance(selection, PaperGateSelectionEnvelope) or not isinstance(
        prerequisites, PaperGatePrerequisiteEnvelope
    ):
        raise ValueError("formal run requires process-local typed gate authority")
    if not callable(publication_factory):
        raise TypeError("formal run typed gate authority is invalid")
    return FormalRunGateServiceInput(
        execution_gate=FormalExecutionGateServiceInput(
            selection=selection,
            prerequisites=prerequisites,
        ),
        publication_gate_factory=publication_factory,
    )


def _gate_blocked_service_result(
    request: PipelineCommandRequest,
    decision: Mapping[str, object],
    *,
    child: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result = dict(child or _service_result(request))
    result.update(
        {
            "status": "blocked",
            "stage": decision.get("stage"),
            "classification": decision.get("classification"),
            "blocked_reasons": list(decision.get("blocked_reasons", ())),
        }
    )
    return result


def _execute_gated_formal_service(
    request: PipelineCommandRequest,
    *,
    service_input: object,
    gate_input: FormalRunGateServiceInput,
) -> Mapping[str, object]:
    """在同一进程以 pre gate → official service → post gate 顺序执行。"""

    execution_request = replace(
        request,
        command="validate-formal-execution-gate",
        scope="validate-formal-execution-gate",
        evidence_class=_EVIDENCE_CLASSES["validate-formal-execution-gate"],
        provider_authorization=None,
        _service_input=gate_input.execution_gate,
    )
    execution = _validate_formal_execution_gate_adapter(execution_request)
    if execution.get("status") != "ready":
        return _gate_blocked_service_result(request, execution)

    child_request = replace(request, _service_input=service_input)
    child_value = _AUTHORITATIVE_SERVICE_ADAPTERS[request.command](child_request)
    if not isinstance(child_value, FormalSuiteServiceResult):
        raise TypeError("gated formal service must preserve typed terminal result")
    child = child_value.to_dict()
    if child.get("status") not in {"completed", "completed_with_failures", "ready"}:
        return child

    terminal_evidence = gate_input.publication_gate_factory(
        child_value.terminal_result
    )
    publication_input = PaperPublicationGateServiceInput(
        selection=gate_input.execution_gate.selection,
        terminal_evidence=terminal_evidence,
    )
    publication_request = replace(
        request,
        command="validate-paper-publication-gate",
        scope="validate-paper-publication-gate",
        evidence_class=_EVIDENCE_CLASSES["validate-paper-publication-gate"],
        provider_authorization=None,
        _service_input=publication_input,
    )
    publication = _validate_paper_publication_gate_adapter(publication_request)
    if publication.get("status") != "ready":
        return _gate_blocked_service_result(request, publication, child=child)
    return {
        **child,
        "execution_gate_status": "ready",
        "publication_gate_status": "ready",
    }


def _default_delegate(request: PipelineCommandRequest) -> Mapping[str, object]:
    """通过固定生产映射调用命令唯一的 official service adapter。"""

    factory = _PRODUCTION_SERVICE_INPUT_FACTORIES[request.command]
    if request.command in _FORMAL_GATED_COMMANDS:
        gate_input = _formal_run_gate_input(request)
        return _execute_gated_formal_service(
            request,
            service_input=factory(request),
            gate_input=gate_input,
        )
    resolved = replace(request, _service_input=factory(request))
    return _AUTHORITATIVE_SERVICE_ADAPTERS[request.command](resolved)


def _validate_prompt_admission(profile: Any) -> None:
    digest = getattr(profile, "prompt_admission_profile_digest", None)
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("prepared prompt admission digest is missing")
    budget = getattr(profile, "budget", None)
    bound_digest = getattr(budget, "prompt_admission_profile_digest", digest)
    if bound_digest != digest:
        raise ValueError("prepared prompt admission budget binding mismatch")


def _json_result(
    *,
    request: PipelineCommandRequest,
    delegated: Mapping[str, object] | Any,
) -> dict[str, object]:
    if isinstance(delegated, Mapping):
        body = delegated
    elif hasattr(delegated, "to_dict"):
        body = delegated.to_dict()
    else:
        raise TypeError("pipeline delegate must return a mapping or to_dict result")
    authorization = request.provider_authorization
    paid_authorization = (
        authorization
        if type(authorization) is PaidAuthorizationValidation
        else None
    )
    receipt_digest = (
        None
        if paid_authorization is None
        else paid_authorization.receipt.receipt_digest
    )
    marker_digest = (
        None
        if authorization is None
        else authorization.marker.marker_digest
    )
    delegated_plan_digest = body.get("plan_digest", request.plan_digest)
    delegated_inventory_digest = body.get(
        "inventory_digest", request.inventory_digest
    )
    if (
        request.plan_digest is not None
        and delegated_plan_digest != request.plan_digest
    ):
        raise ValueError("pipeline service plan digest mismatch")
    if (
        request.inventory_digest is not None
        and delegated_inventory_digest != request.inventory_digest
    ):
        raise ValueError("pipeline service inventory digest mismatch")
    delegated_provider_calls = body.get("provider_calls")
    if delegated_provider_calls is None:
        provider_calls: int | None = None
        provider_calls_missing_reason = body.get(
            "provider_calls_missing_reason",
            "delegated_provider_call_accounting_missing",
        )
        if not isinstance(provider_calls_missing_reason, str) or not (
            provider_calls_missing_reason
        ):
            raise ValueError("missing provider calls require a reason")
    else:
        if isinstance(delegated_provider_calls, bool):
            raise ValueError("pipeline provider calls must be a non-negative integer")
        provider_calls = int(delegated_provider_calls)
        if provider_calls < 0 or provider_calls != delegated_provider_calls:
            raise ValueError("pipeline provider calls must be a non-negative integer")
        provider_calls_missing_reason = None
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": str(body.get("status", "completed")),
        "scope": request.scope,
        "profile_digest": body.get(
            "profile_digest", request.profile.profile_digest
        ),
        "budget_digest": body.get(
            "budget_digest",
            request.profile.budget_digest
            if authorization is None
            else getattr(
                getattr(authorization, "receipt", authorization),
                "budget_digest",
                request.profile.budget_digest,
            ),
        ),
        "plan_digest": delegated_plan_digest,
        "inventory_digest": delegated_inventory_digest,
        "receipt_digest": receipt_digest,
        "output_marker_digest": marker_digest,
        "evidence_class": str(body.get("evidence_class", request.evidence_class)),
        "provider_calls": provider_calls,
        "provider_calls_missing_reason": provider_calls_missing_reason,
    }
    if authorization is not None and paid_authorization is None:
        result.update(
            {
                "facility_authorization_schema": getattr(
                    authorization, "schema_version", None
                ),
                "facility_authorization_digest": getattr(
                    authorization, "authorization_digest", None
                ),
            }
        )
    for name in (
        "stage",
        "classification",
        "blocked_reasons",
        "facility_gate_verified",
        "paper_eligible",
        "bundle_digest",
        "inventory_entry_count",
        "completed_experiment_count",
        "root_run_count",
        "selection",
        "source_validation_digest",
        "source_snapshot_digest",
            "coverage_digest",
            "condition_count",
            "metric_merge_audit_path",
            "metric_merge_audit_digest",
            "provider_calls_missing_reason",
        "total_current_provider_calls",
        "acquisition_current_provider_calls",
        "acquisition_current_spend",
        "acquisition_spend_missing_reason",
        "trace_current_provider_calls",
        "trace_current_spend",
        "trace_spend_missing_reason",
        "exp5_current_provider_calls",
        "exp5_current_spend",
        "exp5_spend_missing_reason",
        "total_current_spend",
        "total_spend_missing_reason",
        "provider_budget",
    ):
        if name in body:
            result[name] = body[name]
    return result


def execute_typed_gate_command(
    *,
    command: str,
    profile: PaperPipelineProfile | Any,
    output_root: str | Path,
    service_input: FormalExecutionGateServiceInput
    | PaperPublicationGateServiceInput,
) -> dict[str, object]:
    """从正式 producer 的 typed authority 执行 gate，不经过 argv/JSON 造证据。"""

    expected_type = {
        "validate-formal-execution-gate": FormalExecutionGateServiceInput,
        "validate-paper-publication-gate": PaperPublicationGateServiceInput,
    }.get(command)
    if expected_type is None:
        raise ValueError("typed gate command is unsupported")
    if not isinstance(service_input, expected_type):
        raise TypeError("typed gate command service input mismatch")
    request = PipelineCommandRequest(
        command=command,
        scope=command,
        evidence_class=_EVIDENCE_CLASSES[command],
        profile=profile,
        provider_authorization=None,
        output_root=Path(output_root),
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={
            "command": command,
            "profile_digest": profile.profile_digest,
        },
        _service_input=service_input,
    )
    return _json_result(request=request, delegated=_default_delegate(request))


def _closure_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(body, dict):
        raise ValueError(f"{label} must be an object")
    return body


def _closure_digest(value: object) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(content).hexdigest()


def _closure_relative_file(
    *,
    root: Path,
    value: object,
    label: str,
) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path is invalid")
    relative = PurePosixPath(value)
    if (
        "\\" in value
        or relative.is_absolute()
        or relative.as_posix() != value
        or ".." in relative.parts
    ):
        raise ValueError(f"{label} path is invalid")
    resolved_root = root.resolve(strict=False)
    path = (resolved_root / Path(*relative.parts)).resolve(strict=False)
    if resolved_root not in path.parents or not path.is_file():
        raise ValueError(f"{label} file is missing")
    return path


def _closure_nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _closure_manifest_ref_matches(
    *,
    root: Path,
    path: Path,
    value: object,
) -> bool:
    if not isinstance(value, Mapping) or not path.is_file():
        return False
    content = path.read_bytes()
    return (
        value.get("path") == path.relative_to(root).as_posix()
        and value.get("size") == len(content)
        and value.get("content_sha256")
        == "sha256:" + sha256(content).hexdigest()
    )


def _closure_jsonl_records(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{label} record is invalid")
            result.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid") from error
    return result


def _closure_formal_root_inventory(
    *,
    root: Path,
    frozen_suite: Mapping[str, Any],
    frozen_dispatch: Mapping[str, Any],
) -> tuple[
    dict[tuple[str, str, str], dict[str, Any]],
    dict[tuple[str, str, str, str], Mapping[str, Any]],
]:
    """从冻结 dispatch/catalog 重建 selected canonical direct inventory。"""

    from tokenshare.experiments.paper_catalog import (
        PaperInputCatalogManifest,
        _with_factorization_paper_difficulty,
        _with_lean_v1_paper_difficulty,
    )
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_exp2_scalability import (
        Exp2FactorizationCaseSelection,
    )
    from tokenshare.experiments.paper_exp3_fault_recovery import (
        Exp3MixedLeanCaseSelection,
    )
    from tokenshare.experiments.paper_exp4_ablation_runner import (
        Exp4MixedLeanCaseSelection,
    )
    from tokenshare.experiments.paper_experiment_contracts import (
        FrozenCaseSelection,
        FrozenConditionSelectionBinding,
    )
    from tokenshare.experiments.paper_formal_runner import (
        _build_canonical_direct_collector,
    )
    from tokenshare.experiments.paper_models import PaperExperimentCondition

    def deserialize_selection(body: Mapping[str, Any]) -> FrozenCaseSelection:
        mixed_selection_type: type[FrozenCaseSelection] | None = None
        if body.get("domain") == "lean_proof" and body.get("topic_family") is None:
            if body.get("experiment_id") == "exp3_real_ai_fault_recovery":
                mixed_selection_type = Exp3MixedLeanCaseSelection
            elif body.get("experiment_id") == "exp4_real_ai_protocol_ablation":
                mixed_selection_type = Exp4MixedLeanCaseSelection
        if "split_profile_id" not in body and mixed_selection_type is None:
            return FrozenCaseSelection.from_dict(body)
        selection_type = mixed_selection_type or Exp2FactorizationCaseSelection
        selection_arguments: dict[str, Any] = dict(
            schema_version=str(body.get("schema_version")),
            selection_id=str(body.get("selection_id")),
            experiment_id=str(body.get("experiment_id")),
            suite_version=str(body.get("suite_version")),
            catalog_version=str(body.get("catalog_version")),
            domain=str(body.get("domain")),
            paper_difficulty=str(body.get("paper_difficulty")),
            topic_family=body.get("topic_family"),
            ordered_case_ids=body.get("ordered_case_ids"),
            catalog_digest=str(body.get("catalog_digest")),
            expected_ai_unit_count=body.get("expected_ai_unit_count"),
            paper_eligible_required=body.get("paper_eligible_required"),
            blocked_reason=body.get("blocked_reason"),
        )
        if mixed_selection_type is None:
            selection_arguments["split_profile_id"] = str(
                body.get("split_profile_id")
            )
        selection = selection_type(**selection_arguments)
        for field_name in (
            "catalog_source_kind",
            "slice_digest",
            "split_metadata_digest",
            "case_expected_ai_unit_counts",
        ):
            if field_name in body:
                object.__setattr__(selection, field_name, body[field_name])
        if (
            body.get("selection_digest") != selection.selection_digest
            or body.get("case_selection_digest") != selection.case_selection_digest
        ):
            raise ValueError("trace closure formal selection digest mismatch")
        return selection

    raw_plans = frozen_dispatch.get("plans")
    if not isinstance(raw_plans, list):
        raise ValueError("trace closure formal condition inventory is invalid")
    formal_conditions: dict[tuple[str, str, str], dict[str, Any]] = {}
    plans: list[PaperExperimentDispatchPlan] = []
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, Mapping):
            raise ValueError("trace closure formal condition inventory is invalid")
        raw_conditions = raw_plan.get("conditions")
        raw_bindings = raw_plan.get("condition_selection_bindings")
        if not isinstance(raw_conditions, list) or not isinstance(raw_bindings, list):
            raise ValueError("trace closure formal condition inventory is invalid")
        conditions: list[PaperExperimentCondition] = []
        for raw_condition in raw_conditions:
            if not isinstance(raw_condition, Mapping):
                raise ValueError("trace closure formal condition inventory is invalid")
            condition_body = dict(raw_condition)
            declared_digest = condition_body.pop("condition_digest", None)
            condition = PaperExperimentCondition(**condition_body)
            if declared_digest != condition.condition_digest:
                raise ValueError("trace closure formal condition inventory is invalid")
            key = (
                condition.experiment_id,
                condition.condition_id,
                str(condition.repeat_id),
            )
            if key in formal_conditions:
                raise ValueError("duplicate trace closure formal condition")
            formal_conditions[key] = dict(raw_condition)
            conditions.append(condition)
        bindings: list[FrozenConditionSelectionBinding] = []
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, Mapping) or not isinstance(
                raw_binding.get("selection"), Mapping
            ):
                raise ValueError("trace closure formal selection inventory is invalid")
            bindings.append(
                FrozenConditionSelectionBinding(
                    schema_version=str(raw_binding.get("schema_version")),
                    condition_id=str(raw_binding.get("condition_id")),
                    condition_digest=str(raw_binding.get("condition_digest")),
                    selection=deserialize_selection(raw_binding["selection"]),
                )
            )
        plans.append(
            PaperExperimentDispatchPlan(
                schema_version=str(raw_plan.get("schema_version")),
                experiment_id=str(raw_plan.get("experiment_id")),
                output_root=str(raw_plan.get("output_root")),
                conditions=tuple(conditions),
                condition_selection_bindings=tuple(bindings),
                status=str(raw_plan.get("status")),
                blocked_reason=raw_plan.get("blocked_reason"),
                paper_eligible_possible=raw_plan.get("paper_eligible_possible"),
                provider_calls_made=raw_plan.get("provider_calls_made"),
                catalog_execution_view=(
                    dict(raw_plan["catalog_execution_view"])
                    if isinstance(raw_plan.get("catalog_execution_view"), Mapping)
                    else None
                ),
            )
        )

    catalog_body = _closure_json_object(
        root / "input_catalog_manifest.json",
        label="trace closure catalog",
    )
    source_files = catalog_body.get("source_files")
    if not isinstance(source_files, list) or len(source_files) != 3:
        raise ValueError("trace closure formal catalog sources are invalid")
    resolved_sources: list[Path] = []
    repository_root = Path(__file__).resolve().parents[3]
    for index, value in enumerate(source_files):
        if not isinstance(value, str) or not value:
            raise ValueError("trace closure formal catalog source is invalid")
        relative = PurePosixPath(value)
        if (
            "\\" in value
            or relative.is_absolute()
            or relative.as_posix() != value
            or ".." in relative.parts
        ):
            raise ValueError("trace closure formal catalog source is invalid")
        candidates = (
            root.joinpath(*relative.parts),
            repository_root.joinpath(*relative.parts),
        )
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            raise ValueError(
                f"trace closure formal catalog source is missing: {index}"
            )
        resolved_sources.append(source)
    factorization_cases = tuple(
        _with_factorization_paper_difficulty(case)
        for case in _closure_jsonl_records(
            resolved_sources[0], label="trace closure factorization catalog"
        )
    )
    lean_cases = tuple(
        _with_lean_v1_paper_difficulty(case)
        for case in _closure_jsonl_records(
            resolved_sources[1], label="trace closure Lean catalog"
        )
    )
    lean_graph_cases = tuple(
        _closure_jsonl_records(
            resolved_sources[2], label="trace closure Lean graph catalog"
        )
    )
    recomputed_catalog_digest = _closure_digest(
        {
            "catalog_id": catalog_body.get("catalog_id"),
            "catalog_version": catalog_body.get("catalog_version"),
            "factorization_cases": factorization_cases,
            "lean_cases": lean_cases,
            "lean_lemma_graph_cases": lean_graph_cases,
        }
    )
    if recomputed_catalog_digest != catalog_body.get("catalog_digest"):
        raise ValueError("trace closure formal catalog digest mismatch")
    catalog = PaperInputCatalogManifest(
        **{
            name: catalog_body[name]
            for name in PaperInputCatalogManifest.__dataclass_fields__
            if name
            not in {
                "factorization_cases",
                "lean_cases",
                "lean_lemma_graph_cases",
            }
        },
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_graph_cases,
    )

    root_case_filter = frozen_suite.get("root_case_filter")
    if not isinstance(root_case_filter, Mapping) or not root_case_filter:
        raise ValueError("trace closure formal root filter is invalid")
    normalized_filter: dict[str, tuple[str, ...]] = {}
    selected_keys: set[tuple[str, str, str]] = set()
    bound_plans: list[tuple[PaperExperimentDispatchPlan, tuple[tuple[Any, Any], ...]]] = []
    for plan in plans:
        selected_items: list[tuple[Any, Any]] = []
        for condition, selection in plan.bound_items():
            if condition.condition_id not in root_case_filter:
                continue
            selected = root_case_filter[condition.condition_id]
            if (
                not isinstance(selected, list)
                or not selected
                or any(not isinstance(case_id, str) or not case_id for case_id in selected)
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(selection.ordered_case_ids)
            ):
                raise ValueError("trace closure formal root filter is invalid")
            normalized_filter[condition.condition_id] = tuple(selected)
            selected_keys.add(
                (condition.experiment_id, condition.condition_id, str(condition.repeat_id))
            )
            selected_items.append((condition, selection))
        if selected_items:
            bound_plans.append((plan, tuple(selected_items)))
    if set(root_case_filter) != set(normalized_filter):
        raise ValueError("trace closure formal root filter is invalid")
    collector = _build_canonical_direct_collector(
        bound_plans=tuple(bound_plans),
        catalog_manifest=catalog,
        normalized_root_filter=normalized_filter,
        evidence_class="real_model_trace_protocol_run",
    )
    rows: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for row in collector.inventory.rows:
        key = (row.experiment_id, row.condition_id, str(row.repeat_id), row.case_id)
        if key in rows:
            raise ValueError("duplicate trace closure formal root inventory row")
        rows[key] = row.to_dict()
    if selected_keys != {key[:3] for key in rows}:
        raise ValueError("trace closure formal root inventory is incomplete")
    return formal_conditions, rows


def audit_results_first_trace_closure_source(
    *,
    suite_root: Path,
) -> ResultsFirstTraceClosurePreflight:
    """只读验证 Exp1--4 canonical source，忽略 runner terminal 自报计数。"""

    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
    from tokenshare.experiments.paper_formal_runner import (
        _persisted_provider_attempt_count,
    )
    from tokenshare.experiments.paper_experiment_contracts import (
        formal_runtime_task_id,
    )
    from tokenshare.storage.events import EventLedger

    root = Path(suite_root).resolve(strict=False)
    if not root.is_dir():
        raise ValueError("trace closure suite root is missing")
    store = FormalEvidenceStore(root)
    suite_manifest = _closure_json_object(
        root / "suite_manifest.json",
        label="trace closure suite manifest",
    )
    frozen = suite_manifest.get("suite_identity")
    if not isinstance(frozen, Mapping):
        raise ValueError("trace closure suite identity is missing")

    def frozen_body(name: str) -> Mapping[str, Any]:
        component = frozen.get(name)
        if not isinstance(component, Mapping):
            raise ValueError(f"trace closure suite identity is missing: {name}")
        body = component.get("body")
        if not isinstance(body, Mapping) or component.get("digest") != _closure_digest(
            body
        ):
            raise ValueError(f"trace closure suite identity is invalid: {name}")
        return body

    frozen_suite = frozen_body("suite")
    frozen_dispatch = frozen_body("dispatch")
    # 只读复用 FormalEvidenceStore 的 frozen suite/experiment manifest validator；
    # evidence_manifest 的 static/condition refs 在下方按 content digest 快速验证。
    # 不能调用 load() 的 publication repair，也不能在 preflight 重哈希数万 payload。
    frozen_suite_validation_view = dict(suite_manifest)
    # replay input 是 terminal 后由同一 runner 写出的 derived ref，不属于 frozen
    # execution identity；原文件仍由 static manifest content hash 完整绑定。
    frozen_suite_validation_view.pop("traceability_replay_input_root_ref", None)
    store._validate_suite_and_experiment_manifests(
        suite_manifest=frozen_suite_validation_view,
        frozen_suite=frozen_suite,
        frozen_dispatch=frozen_dispatch,
    )

    evidence_manifest = _closure_json_object(
        root / "evidence_manifest.json",
        label="trace closure evidence manifest",
    )
    if evidence_manifest.get("schema_version") != (
        "tokenshare.paper_evidence_manifest.v2"
    ):
        raise ValueError("trace closure evidence manifest schema is invalid")
    static_entries = evidence_manifest.get("files")
    condition_entries = evidence_manifest.get("conditions")
    if not isinstance(static_entries, list) or not isinstance(
        condition_entries,
        list,
    ):
        raise ValueError("trace closure evidence manifest inventory is invalid")
    static_seen: set[str] = set()
    for entry in static_entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise ValueError("trace closure static manifest entry is invalid")
        relative_path = str(entry["path"])
        if relative_path in static_seen:
            raise ValueError("duplicate trace closure static manifest entry")
        static_seen.add(relative_path)
        static_path = (root / relative_path).resolve(strict=False)
        if root not in static_path.parents or not _closure_manifest_ref_matches(
            root=root,
            path=static_path,
            value=entry,
        ):
            raise ValueError("trace closure static manifest integrity mismatch")
    actual_static = {
        path.relative_to(root).as_posix() for path in store._v2_static_paths()
    }
    if static_seen != actual_static:
        raise ValueError("trace closure static manifest inventory mismatch")
    required_static = {
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "conditions.jsonl",
    }
    if not required_static <= static_seen:
        raise ValueError("trace closure required static manifest entry is missing")
    for name, path_name in (
        ("budget", "run_budget.json"),
        ("catalog", "input_catalog_manifest.json"),
        ("dispatch", "paper_dispatch_plans.json"),
    ):
        if _closure_json_object(root / path_name, label=f"trace closure {name}") != dict(
            frozen_body(name)
        ):
            raise ValueError(f"trace closure frozen {name} identity mismatch")

    formal_conditions, formal_root_rows = _closure_formal_root_inventory(
        root=root,
        frozen_suite=frozen_suite,
        frozen_dispatch=frozen_dispatch,
    )
    conditions_records = _closure_jsonl_records(
        root / "conditions.jsonl",
        label="trace closure conditions inventory",
    )
    persisted_conditions: dict[tuple[str, str, str], dict[str, Any]] = {}
    for condition in conditions_records:
        key = (
            str(condition.get("experiment_id")),
            str(condition.get("condition_id")),
            str(condition.get("repeat_id")),
        )
        if key in persisted_conditions:
            raise ValueError("duplicate trace closure formal condition inventory")
        persisted_conditions[key] = condition
    if persisted_conditions != formal_conditions:
        raise ValueError("trace closure formal condition inventory mismatch")
    selected_condition_keys = {key[:3] for key in formal_root_rows}

    condition_inventory: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for entry in condition_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("trace closure condition manifest entry is invalid")
        experiment_id = entry.get("experiment_id")
        condition_id = entry.get("condition_id")
        repeat_id = entry.get("repeat_id")
        if (
            not isinstance(experiment_id, str)
            or not experiment_id
            or not isinstance(condition_id, str)
            or not condition_id
            or isinstance(repeat_id, bool)
            or not isinstance(repeat_id, (int, str))
        ):
            raise ValueError("trace closure condition manifest identity is invalid")
        key = (experiment_id, condition_id, str(repeat_id))
        if key in condition_inventory:
            raise ValueError("duplicate trace closure condition manifest entry")
        condition_inventory[key] = entry
    if set(condition_inventory) != selected_condition_keys:
        raise ValueError("trace closure formal condition inventory mismatch")

    experiment_ids = frozen_suite.get("experiment_ids")
    if not isinstance(experiment_ids, list) or any(
        not isinstance(value, str) or not value for value in experiment_ids
    ):
        raise ValueError("trace closure experiment inventory is invalid")

    condition_count = 0
    root_count = 0
    attempt_count = 0
    current_provider_attempt_count = 0
    task_provider_attempt_count = 0
    trace_provider_attempt_count = 0
    legacy_attempt_row_count = 0
    positive_provider_attempt_ordinal_count = 0
    source_consumption_count = 0
    execution_bindings: dict[
        str,
        tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            str,
            tuple[Mapping[str, Any], ...],
            tuple[Mapping[str, Any], ...],
            int,
        ],
    ] = {}
    global_attempt_ids: set[str] = set()

    for experiment_id in experiment_ids:
        runs_root = root / "experiments" / experiment_id / "runs"
        run_roots = (
            sorted(
                repeat_root
                for condition_root in runs_root.iterdir()
                if condition_root.is_dir()
                for repeat_root in condition_root.iterdir()
                if repeat_root.is_dir()
            )
            if runs_root.is_dir()
            else []
        )
        for run_root in run_roots:
            condition_count += 1
            condition_manifest = _closure_json_object(
                run_root / "condition_manifest.json",
                label="trace closure condition manifest",
            )
            repeat_id = condition_manifest.get("repeat_id")
            if isinstance(repeat_id, bool) or not isinstance(
                repeat_id,
                (int, str),
            ):
                raise ValueError("trace closure repeat identity is invalid")
            condition_key = (experiment_id, run_root.parent.name, str(repeat_id))
            formal_condition = formal_conditions.get(condition_key)
            condition_identity = condition_manifest.get("condition_identity")
            if (
                formal_condition is None
                or run_root.parent.parent.parent.name != experiment_id
                or run_root.name != str(repeat_id)
                or condition_manifest.get("experiment_id") != experiment_id
                or condition_manifest.get("condition_id") != run_root.parent.name
                or not isinstance(condition_identity, Mapping)
                or condition_identity.get("body") != formal_condition
                or condition_identity.get("digest") != _closure_digest(formal_condition)
            ):
                raise ValueError("trace closure formal condition identity mismatch")
            inventory_entry = condition_inventory.pop(
                condition_key,
                None,
            )
            if inventory_entry is None or not _closure_manifest_ref_matches(
                root=root,
                path=run_root / "condition_manifest.json",
                value=inventory_entry.get("condition_manifest_ref"),
            ):
                raise ValueError("trace closure condition manifest ref mismatch")
            generation_root = store._current_generation_root(
                run_root,
                required=True,
            )
            assert generation_root is not None
            generation_manifest = _closure_json_object(
                generation_root / "generation_manifest.json",
                label="trace closure generation manifest",
            )
            if not _closure_manifest_ref_matches(
                root=root,
                path=run_root / "CURRENT.json",
                value=condition_manifest.get("current_ref"),
            ):
                raise ValueError("trace closure CURRENT manifest ref mismatch")
            if not _closure_manifest_ref_matches(
                root=root,
                path=generation_root / "generation_manifest.json",
                value=condition_manifest.get("generation_manifest_ref"),
            ):
                raise ValueError("trace closure generation manifest ref mismatch")
            if not _closure_manifest_ref_matches(
                root=root,
                path=generation_root / "run_manifest.json",
                value=condition_manifest.get("run_manifest_ref"),
            ):
                raise ValueError("trace closure run manifest ref mismatch")
            if (
                generation_manifest.get("generation_kind") != "snapshot"
                or condition_manifest.get("terminal") is not True
            ):
                raise ValueError("trace closure source is not a terminal snapshot")
            tasks = _closure_jsonl_records(
                generation_root / "per_task_results.jsonl",
                label="trace closure task evidence",
            )
            attempts = _closure_jsonl_records(
                generation_root / "per_attempt_results.jsonl",
                label="trace closure attempt evidence",
            )
            expected_root_count = _closure_nonnegative_int(
                condition_manifest.get("expected_root_count"),
                label="trace closure expected root count",
            )
            if len(tasks) != expected_root_count:
                raise ValueError("trace closure task denominator mismatch")
            run_manifest = _closure_json_object(
                generation_root / "run_manifest.json",
                label="trace closure run manifest",
            )
            task_ids = [task.get("task_id") for task in tasks]
            if (
                any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
                or len(set(task_ids)) != len(task_ids)
                or run_manifest.get("task_ids") != task_ids
            ):
                raise ValueError("trace closure task/attempt inventory mismatch")
            root_count += len(tasks)
            attempt_count += len(attempts)

            protocol_attempts: list[Mapping[str, Any]] = []
            attempts_by_task: dict[str, list[Mapping[str, Any]]] = {}
            run_attempt_ids: set[str] = set()
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    raise ValueError("trace closure attempt evidence is invalid")
                if attempt.get("record_scope") != "protocol":
                    raise ValueError("trace closure attempt scope is invalid")
                protocol_attempts.append(attempt)
                task_id = attempt.get("task_id")
                attempt_id = attempt.get("attempt_id")
                if (
                    not isinstance(task_id, str)
                    or not task_id
                    or not isinstance(attempt_id, str)
                    or not attempt_id
                ):
                    raise ValueError("trace closure attempt identity is invalid")
                if attempt_id in run_attempt_ids or attempt_id in global_attempt_ids:
                    raise ValueError("trace closure task/attempt inventory mismatch")
                if task_id not in set(task_ids):
                    raise ValueError("trace closure task/attempt inventory mismatch")
                run_attempt_ids.add(attempt_id)
                global_attempt_ids.add(attempt_id)
                attempts_by_task.setdefault(task_id, []).append(attempt)
                if "provider_attempt_count" not in attempt:
                    legacy_attempt_row_count += 1
                ordinal = attempt.get("provider_attempt_index")
                if ordinal is not None:
                    ordinal = _closure_nonnegative_int(
                        ordinal,
                        label="provider attempt ordinal",
                    )
                    positive_provider_attempt_ordinal_count += int(ordinal > 0)
            if set(attempts_by_task) != set(task_ids):
                raise ValueError("trace closure task/attempt inventory mismatch")

            current_provider_attempt_count += _persisted_provider_attempt_count(
                protocol_attempts
            )
            for task in tasks:
                if not isinstance(task, Mapping):
                    raise ValueError("trace closure task evidence is invalid")
                task_id = task.get("task_id")
                protocol_task_id = task.get("protocol_task_id")
                runtime = task.get("runtime_generation_identity")
                if (
                    not isinstance(task_id, str)
                    or not task_id
                    or not isinstance(protocol_task_id, str)
                    or not protocol_task_id
                    or not isinstance(runtime, Mapping)
                ):
                    raise ValueError("trace closure task identity is invalid")
                domain = formal_condition.get("domain")
                if not isinstance(domain, str) or not domain:
                    raise ValueError("trace closure formal condition identity is invalid")
                try:
                    expected_protocol_task_id = formal_runtime_task_id(domain, task_id)
                except ValueError as error:
                    raise ValueError(
                        "trace closure task protocol mapping is invalid"
                    ) from error
                if protocol_task_id != expected_protocol_task_id:
                    raise ValueError("trace closure task protocol mapping mismatch")
                execution_id = runtime.get("run_id")
                root_unit_id = runtime.get("root_unit_id")
                ledger_digest = runtime.get("ledger_digest")
                if (
                    not isinstance(execution_id, str)
                    or not execution_id
                    or not isinstance(root_unit_id, str)
                    or not root_unit_id
                    or not isinstance(ledger_digest, str)
                    or not ledger_digest.startswith("sha256:")
                ):
                    raise ValueError("trace closure runtime identity is invalid")
                if execution_id in execution_bindings:
                    raise ValueError("duplicate trace closure execution root")
                usage = task.get("trace_source_usage")
                if not isinstance(usage, Mapping):
                    raise ValueError("trace source usage evidence is missing")
                task_current_header = _closure_nonnegative_int(
                    task.get("provider_attempt_count"),
                    label="task provider attempt count",
                )
                task_current = _closure_nonnegative_int(
                    usage.get("current_provider_call_count"),
                    label="trace current provider call count",
                )
                task_provider_attempt_count += task_current_header
                trace_provider_attempt_count += task_current
                consumptions = usage.get("consumptions")
                committed_count = _closure_nonnegative_int(
                    usage.get("committed_consumption_count"),
                    label="trace source consumption count",
                )
                if not isinstance(consumptions, list) or len(consumptions) != (
                    committed_count
                ):
                    raise ValueError("trace source consumption inventory is invalid")
                if task_current > 0:
                    raise ValueError("trace source/current provider domains are mixed")
                source_consumption_count += committed_count
                consumed_attempt_ids: list[str] = []
                for consumption in consumptions:
                    if (
                        not isinstance(consumption, Mapping)
                        or not isinstance(consumption.get("consumption_id"), str)
                        or not consumption.get("consumption_id")
                        or not isinstance(
                            consumption.get("current_attempt_id"), str
                        )
                        or not consumption.get("current_attempt_id")
                    ):
                        raise ValueError("trace source consumption identity is invalid")
                    consumed_attempt_ids.append(str(consumption["current_attempt_id"]))
                if len(set(consumed_attempt_ids)) != len(consumed_attempt_ids):
                    raise ValueError("duplicate trace source consumption")
                task_attempts = attempts_by_task.get(task_id, [])
                task_current_from_attempts = _persisted_provider_attempt_count(
                    task_attempts
                )
                if (
                    task_current_from_attempts != task_current
                    or task_current_header != task_current
                ):
                    raise ValueError("trace source/current provider accounting mismatch")
                execution_bindings[execution_id] = (
                    protocol_task_id,
                    root_unit_id,
                    ledger_digest,
                    experiment_id,
                    run_root.parent.name,
                    str(repeat_id),
                    task_id,
                    tuple(task_attempts),
                    tuple(consumptions),
                    task_current,
                )

    if condition_inventory:
        raise ValueError("trace closure condition manifest has no CURRENT run")

    checkpoint_base = root.with_name(root.name + ".canonical_direct_evidence")
    checkpoint_paths = (
        sorted(checkpoint_base.glob("*/canonical_direct_checkpoint.json"))
        if checkpoint_base.is_dir()
        else []
    )
    checkpoint_bodies = [
        (
            path,
            _closure_json_object(path, label="canonical direct checkpoint"),
        )
        for path in checkpoint_paths
    ]
    root_ids = [
        body.get("preregistered_root_run_id") for _path, body in checkpoint_bodies
    ]
    if any(not isinstance(value, str) or not value for value in root_ids):
        raise ValueError("canonical checkpoint root identity is invalid")
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("duplicate canonical checkpoint root")
    if len(checkpoint_bodies) != root_count:
        raise ValueError("canonical checkpoint/root inventory mismatch")

    checkpoint_inventory: list[dict[str, object]] = []
    checkpoint_executions: set[str] = set()
    for checkpoint_path, checkpoint in checkpoint_bodies:
        if checkpoint.get("schema_version") != (
            "tokenshare.canonical_direct_checkpoint.v1"
        ):
            raise ValueError("unsupported canonical direct checkpoint")
        root_id = str(checkpoint["preregistered_root_run_id"])
        if checkpoint_path.parent.name != sha256(root_id.encode("utf-8")).hexdigest():
            raise ValueError("canonical checkpoint root path identity mismatch")
        execution_id = checkpoint.get("execution_id")
        expected_binding = execution_bindings.get(str(execution_id))
        if expected_binding is None or str(execution_id) in checkpoint_executions:
            raise ValueError("canonical checkpoint execution binding mismatch")
        checkpoint_executions.add(str(execution_id))
        (
            protocol_task_id,
            root_unit_id,
            expected_ledger_digest,
            experiment_id,
            condition_id,
            repeat_id,
            case_id,
            task_attempts,
            task_consumptions,
            task_current_provider_attempt_count,
        ) = expected_binding
        expected_row = formal_root_rows.get(
            (experiment_id, condition_id, repeat_id, case_id)
        )
        if (
            expected_row is None
            or root_id != expected_row.get("preregistered_root_run_id")
            or checkpoint.get("inventory_row_digest")
            != expected_row.get("inventory_row_digest")
        ):
            raise ValueError("canonical checkpoint formal root inventory mismatch")
        if (
            checkpoint.get("task_id") != protocol_task_id
            or checkpoint.get("root_unit_id") != root_unit_id
        ):
            raise ValueError("canonical checkpoint protocol identity mismatch")
        provider_artifacts = checkpoint.get("provider_artifact_ids")
        if not isinstance(provider_artifacts, list):
            raise ValueError("canonical checkpoint provider inventory is invalid")
        if provider_artifacts:
            raise ValueError("trace source/current provider domains are mixed")

        checkpoint_root = checkpoint_path.parent
        ledger_path = _closure_relative_file(
            root=checkpoint_root,
            value=checkpoint.get("ledger_path"),
            label="canonical checkpoint ledger",
        )
        snapshot = EventLedger(ledger_path).read_verified_snapshot()
        if snapshot.event_count <= 0 or snapshot.ledger_events_digest != (
            expected_ledger_digest
        ):
            raise ValueError("canonical checkpoint ledger identity mismatch")
        attempts_by_id = {
            str(attempt["attempt_id"]): attempt for attempt in task_attempts
        }
        commits_by_attempt: dict[str, str] = {}
        request_counts: dict[str, int] = {}
        for event in snapshot.events:
            if event.event_type not in {
                "EXECUTION_REQUEST_RECORDED",
                "TRACE_DELIVERY_COMMITTED.v1",
            }:
                continue
            payload = event.payload
            attempt_id = payload.get("attempt_id")
            if (
                event.task_id != protocol_task_id
                or not isinstance(attempt_id, str)
                or not attempt_id
            ):
                raise ValueError("trace source ledger attempt binding is invalid")
            if event.event_type == "EXECUTION_REQUEST_RECORDED":
                if attempt_id in attempts_by_id:
                    request_counts[attempt_id] = request_counts.get(attempt_id, 0) + 1
                continue
            if attempt_id not in attempts_by_id:
                raise ValueError("trace source ledger attempt binding is invalid")
            if attempt_id in commits_by_attempt:
                raise ValueError("duplicate trace source ledger commit")
            commits_by_attempt[attempt_id] = event.event_id
        if any(request_counts.get(attempt_id) != 1 for attempt_id in commits_by_attempt):
            raise ValueError("committed trace delivery request is invalid")
        consumptions_by_attempt: dict[str, Mapping[str, Any]] = {}
        for consumption in task_consumptions:
            attempt_id = consumption.get("current_attempt_id")
            consumption_id = consumption.get("consumption_id")
            if (
                not isinstance(attempt_id, str)
                or not attempt_id
                or not isinstance(consumption_id, str)
                or not consumption_id
                or attempt_id in consumptions_by_attempt
                or attempt_id not in attempts_by_id
            ):
                raise ValueError("trace source consumption/attempt binding mismatch")
            consumptions_by_attempt[attempt_id] = consumption
        if set(consumptions_by_attempt) != set(commits_by_attempt) or any(
            consumption["consumption_id"] != commits_by_attempt[attempt_id]
            for attempt_id, consumption in consumptions_by_attempt.items()
        ):
            raise ValueError("trace source consumption/commit binding mismatch")
        for attempt_id, attempt in attempts_by_id.items():
            if attempt.get("attempt_status") != "provider_error" or attempt_id in commits_by_attempt:
                continue
            if request_counts.get(attempt_id) != 1:
                raise ValueError("uncommitted trace provider_error request is invalid")
            if _closure_nonnegative_int(
                attempt.get("provider_attempt_count"),
                label="provider_error current provider attempt count",
            ) != 0 or task_current_provider_attempt_count != 0:
                raise ValueError("uncommitted trace provider_error dispatched current provider")
        adjunct_path = _closure_relative_file(
            root=checkpoint_root,
            value=checkpoint.get("adjunct_path"),
            label="canonical checkpoint adjunct",
        )
        adjunct_digest = "sha256:" + sha256(adjunct_path.read_bytes()).hexdigest()
        if adjunct_digest != checkpoint.get("adjunct_digest"):
            raise ValueError("canonical checkpoint adjunct digest mismatch")
        try:
            import pickle

            adjunct = pickle.loads(adjunct_path.read_bytes())
        except (OSError, EOFError, pickle.PickleError) as error:
            raise ValueError("canonical checkpoint adjunct is invalid") from error
        producer_facts = (
            adjunct.get("producer_facts")
            if isinstance(adjunct, Mapping)
            else None
        )
        producer_task = (
            producer_facts.get("task")
            if isinstance(producer_facts, Mapping)
            else None
        )
        run_evidence = (
            producer_facts.get("run_evidence")
            if isinstance(producer_facts, Mapping)
            else None
        )
        protocol_runtime = (
            run_evidence.get("protocol_runtime")
            if isinstance(run_evidence, Mapping)
            else None
        )
        ledger_root_clock = (
            producer_facts.get("ledger_root_clock")
            if isinstance(producer_facts, Mapping)
            else None
        )
        if (
                not isinstance(producer_task, Mapping)
                or producer_task.get("condition_id") != condition_id
                or str(producer_task.get("repeat_id")) != repeat_id
                or producer_task.get("task_id") != case_id
                or producer_task.get("protocol_task_id") != protocol_task_id
            or not isinstance(protocol_runtime, Mapping)
            or protocol_runtime.get("run_id") != execution_id
            or protocol_runtime.get("task_id") != protocol_task_id
            or protocol_runtime.get("root_unit_id") != root_unit_id
            or not isinstance(ledger_root_clock, Mapping)
            or ledger_root_clock.get("run_id") != execution_id
            or ledger_root_clock.get("task_id") != protocol_task_id
            or ledger_root_clock.get("root_unit_id") != root_unit_id
        ):
            raise ValueError("canonical checkpoint adjunct execution binding mismatch")
        for field_name in ("inventory_row_digest", "evidence_digest"):
            value = checkpoint.get(field_name)
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError(f"canonical checkpoint {field_name} is invalid")
        checkpoint_inventory.append(
            {
                "preregistered_root_run_id": root_id,
                "inventory_row_digest": checkpoint["inventory_row_digest"],
                "execution_id": execution_id,
                "task_id": checkpoint["task_id"],
                "root_unit_id": checkpoint["root_unit_id"],
                "checkpoint_content_digest": "sha256:"
                + sha256(checkpoint_path.read_bytes()).hexdigest(),
                "ledger_bytes_digest": snapshot.ledger_bytes_digest,
                "ledger_events_digest": snapshot.ledger_events_digest,
                "ledger_event_count": snapshot.event_count,
                "adjunct_digest": adjunct_digest,
            }
        )
    if checkpoint_executions != set(execution_bindings):
        raise ValueError("canonical checkpoint/root execution inventory mismatch")
    if (
        task_provider_attempt_count != current_provider_attempt_count
        or trace_provider_attempt_count != current_provider_attempt_count
    ):
        raise ValueError("trace source/current provider accounting mismatch")
    if current_provider_attempt_count != 0:
        raise ValueError("trace source/current provider domains are mixed")

    return ResultsFirstTraceClosurePreflight(
        condition_count=condition_count,
        root_count=root_count,
        checkpoint_count=len(checkpoint_bodies),
        attempt_count=attempt_count,
        current_provider_attempt_count=current_provider_attempt_count,
        legacy_attempt_row_count=legacy_attempt_row_count,
        positive_provider_attempt_ordinal_count=(
            positive_provider_attempt_ordinal_count
        ),
        source_consumption_count=source_consumption_count,
        checkpoint_inventory_digest=_closure_digest(checkpoint_inventory),
    )


def _results_first_pipeline_request(
    args: argparse.Namespace,
    *,
    profile: object = _RESULTS_FIRST_CLI_PROFILE,
) -> PipelineCommandRequest:
    output_root = Path(args.output_root)
    external_root = getattr(args, "external_bank_root", None)
    serialized_arguments = {
        "command": args.command,
        "selection": args.selection,
        "planning_artifact_root": str(Path(args.planning_artifact_root)),
        "plan_bundle_root": str(Path(args.plan_bundle_root)),
        "external_bank_root": (
            None if external_root is None else str(Path(external_root))
        ),
        "resume": bool(args.resume),
        "plan_only": bool(args.plan_only),
        "allow_provider_calls": bool(args.allow_provider_calls),
        "frozen_closure_snapshot_root": getattr(
            args, "frozen_closure_snapshot_root", None
        ),
        "frozen_prepared_inventory_root": getattr(
            args, "frozen_prepared_inventory_root", None
        ),
        "expected_preflight": getattr(args, "expected_preflight", None),
        "source_rebind_root": getattr(args, "source_rebind_root", None),
        "expected_provider_budget_digest": getattr(
            args,
            "expected_provider_budget_digest",
            None,
        ),
        "paid_reference_binding_root": getattr(
            args, "paid_reference_binding_root", None
        ),
        "paid_launch_key_config": getattr(args, "paid_launch_key_config", None),
        "paid_launch_attestation_root": getattr(
            args, "paid_launch_attestation_root", None
        ),
        "results_first_scope_preparation_root": getattr(
            args, "results_first_scope_preparation_root", None
        ),
        "receipt": getattr(args, "receipt", None),
        "supervised_no_response_closure_root": getattr(
            args, "supervised_no_response_closure_root", None
        ),
    }
    return PipelineCommandRequest(
        command=args.command,
        scope=args.command,
        evidence_class=_EVIDENCE_CLASSES[args.command],
        profile=profile,
        provider_authorization=None,
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments=serialized_arguments,
        plan_bundle_root=Path(args.plan_bundle_root),
    )


def _results_first_metric_merge_audit_request(
    args: argparse.Namespace,
) -> PipelineCommandRequest:
    """该命令不读取 profile、receipt、key 或 transport，只保留三个只读 argv。"""

    output_root = Path(args.output_root).resolve(strict=False)
    audit_output_root = Path(args.audit_output_root).resolve(strict=False)
    return PipelineCommandRequest(
        command="audit-results-first-metric-merge",
        scope="audit-results-first-metric-merge",
        evidence_class=_EVIDENCE_CLASSES["audit-results-first-metric-merge"],
        profile=_RESULTS_FIRST_CLI_PROFILE,
        provider_authorization=None,
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={
            "command": "audit-results-first-metric-merge",
            "selection": args.selection,
            "output_root": str(output_root),
            "audit_output_root": str(audit_output_root),
        },
    )


def _results_first_combined_render_request(
    args: argparse.Namespace,
) -> PipelineCommandRequest:
    """构造 combined renderer 的最小、无 provider 的 request。"""

    output_root = Path(args.output_root).resolve(strict=False)
    metric_merge_audit_root = Path(args.metric_merge_audit_root).resolve(strict=False)
    combined_output_root = Path(args.combined_output_root).resolve(strict=False)
    return PipelineCommandRequest(
        command="render-results-first-combined",
        scope="render-results-first-combined",
        evidence_class=_EVIDENCE_CLASSES["render-results-first-combined"],
        profile=_RESULTS_FIRST_CLI_PROFILE,
        provider_authorization=None,
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={
            "command": "render-results-first-combined",
            "selection": args.selection,
            "output_root": str(output_root),
            "metric_merge_audit_root": str(metric_merge_audit_root),
            "combined_output_root": str(combined_output_root),
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    from tokenshare.experiments.paper_formal_runner import (
        PaperInfrastructureBlockedError,
    )

    parser = _build_argument_parser()
    command_argv = tuple(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(command_argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        if args.command == "audit-results-first-metric-merge":
            request = _results_first_metric_merge_audit_request(args)
            result = _json_result(
                request=request,
                delegated=_default_delegate(request),
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "verified" else 3
        if args.command == "render-results-first-combined":
            request = _results_first_combined_render_request(args)
            result = _json_result(
                request=request,
                delegated=_default_delegate(request),
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "verified" else 3
        if args.command in _RESULTS_FIRST_COMMANDS:
            results_first_profile: object = _RESULTS_FIRST_CLI_PROFILE
            if args.selection in {
                "representative_exp1_exp3_exp5",
                "full_exp1_exp3_exp5",
            }:
                if not isinstance(args.profile, str) or not args.profile:
                    raise ValueError(
                        "Exp4-excluded results-first selection requires a pipeline profile"
                    )
                results_first_profile = _PROFILE_LOADER(args.profile)
            request = _results_first_pipeline_request(
                args,
                profile=results_first_profile,
            )
            result = _json_result(
                request=request,
                delegated=_default_delegate(request),
            )
            print(json.dumps(result, sort_keys=True))
            return (
                0
                if result["status"]
                in {"completed", "completed_with_failures", "ready"}
                else 3
            )
        profile = _PROFILE_LOADER(args.profile)
        provider = args.command in _PROVIDER_SCOPES
        authorization = None
        formal_authority = None
        if provider:
            output_mode = "new_run" if args.new_run else "resume"
            authorization_budget_digest = profile.budget_digest
            plan_bundle_root = getattr(args, "plan_bundle_root", None)
            acquisition_bundle = None
            if args.command == "acquire-bank" and plan_bundle_root is not None:
                acquisition_bundle = _ACQUISITION_BUNDLE_LOADER(plan_bundle_root)
                if acquisition_bundle.authorized_plan_digest != args.plan_digest:
                    raise ValueError("acquisition bundle plan digest mismatch")
                if acquisition_bundle.inventory_digest != args.inventory_digest:
                    raise ValueError("acquisition bundle inventory digest mismatch")
                if acquisition_bundle.profile_digest != profile.profile_digest:
                    raise ValueError("acquisition bundle profile digest mismatch")
                if acquisition_bundle.prompt_admission_profile_digest != (
                    profile.prompt_admission_profile_digest
                ):
                    raise ValueError("acquisition bundle admission digest mismatch")
                authorization_budget_digest = (
                    acquisition_bundle.full_budget.budget_digest
                )
            if args.command in {
                "run-online-checks",
                "run-exp1-online",
                "run-exp5-online",
            }:
                formal_authority = _FORMAL_AUTHORITY_BUILDER(
                    command=args.command,
                    profile=profile,
                    output_root=Path(args.output_root),
                    resume=bool(args.resume),
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                )
                if formal_authority.plan_digest != args.plan_digest:
                    raise ValueError("formal authority plan digest mismatch")
                if formal_authority.inventory_digest != args.inventory_digest:
                    raise ValueError("formal authority inventory digest mismatch")
                authorization_budget_digest = formal_authority.budget_digest
            elif (
                args.command == "run-exp5-capability-smoke"
                and args.results_first_scope_preparation_root is not None
            ):
                formal_authority = _SMOKE_AUTHORITY_BUILDER(
                    profile=profile,
                    output_root=Path(args.output_root),
                    resume=bool(args.resume),
                    results_first_scope_preparation_root=(
                        args.results_first_scope_preparation_root
                    ),
                )
                if formal_authority.plan_digest != args.plan_digest:
                    raise ValueError("capability smoke authority plan digest mismatch")
                if formal_authority.inventory_digest != args.inventory_digest:
                    raise ValueError(
                        "capability smoke authority inventory digest mismatch"
                    )
                authorization_budget_digest = formal_authority.budget_digest
            receipt = _RECEIPT_LOADER(args.receipt)
            from tokenshare.experiments.paper_formal_gate import (
                selected_experiments_for_provider_scope,
            )

            authorization = _RECEIPT_VALIDATOR(
                receipt=receipt,
                requested_scope=_PROVIDER_SCOPES[args.command],
                authorized_plan_digest=args.plan_digest,
                profile_digest=profile.profile_digest,
                budget_digest=authorization_budget_digest,
                inventory_digest=args.inventory_digest,
                prompt_admission_profile_digest=(
                    profile.prompt_admission_profile_digest
                ),
                selected_experiments=selected_experiments_for_provider_scope(
                    _PROVIDER_SCOPES[args.command]
                ),
                output_root=args.output_root,
                output_mode=output_mode,
                action="dispatch",
                allow_provider_calls=args.allow_provider_calls,
                now=_UTC_NOW(),
            )
            if args.command in _FORMAL_GATED_COMMANDS:
                binding = _paid_scope_binding(
                    validation=authorization,
                    scope=_PROVIDER_SCOPES[args.command],
                    authorized_plan_digest=args.plan_digest,
                    profile_digest=profile.profile_digest,
                    budget_digest=authorization_budget_digest,
                    inventory_digest=args.inventory_digest,
                    prompt_admission_profile_digest=(
                        profile.prompt_admission_profile_digest
                    ),
                    output_root=args.output_root,
                )
                formal_authority = _FORMAL_AUTHORITY_BUILDER(
                    command=args.command,
                    profile=profile,
                    output_root=Path(args.output_root),
                    resume=bool(args.resume),
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                    paid_authorization_bindings=(binding,),
                )
                if formal_authority.plan_digest != args.plan_digest:
                    raise ValueError("formal authority plan digest mismatch")
                if formal_authority.inventory_digest != args.inventory_digest:
                    raise ValueError("formal authority inventory digest mismatch")
                if formal_authority.budget_digest != authorization_budget_digest:
                    raise ValueError("formal authority budget digest changed after receipt")
            _validate_prompt_admission(profile)
            _BUDGET_VALIDATOR(provider_writing=True, budget_mode=args.budget_mode)

        external_root = getattr(args, "external_bank_root", None)
        resolver = (
            None
            if external_root is None
            else ExternalBankResolverBinding(
                root=Path(external_root).resolve(strict=False)
            )
        )
        if args.command == "run-trace":
            trace_bindings = []
            acquisition_bundle = _ACQUISITION_BUNDLE_LOADER(args.plan_bundle_root)
            if acquisition_bundle.authorized_plan_digest != args.plan_digest:
                raise ValueError("trace acquisition bundle plan digest mismatch")
            if acquisition_bundle.inventory_digest != args.inventory_digest:
                raise ValueError("trace acquisition bundle inventory digest mismatch")
            if acquisition_bundle.profile_digest != profile.profile_digest:
                raise ValueError("trace acquisition bundle profile digest mismatch")
            if acquisition_bundle.prompt_admission_profile_digest != (
                profile.prompt_admission_profile_digest
            ):
                raise ValueError("trace acquisition bundle admission digest mismatch")

            bank_receipt_path = args.full_bank_acquisition_receipt
            if bank_receipt_path is not None:
                bank_validation = _RECEIPT_VALIDATOR(
                    receipt=_RECEIPT_LOADER(bank_receipt_path),
                    requested_scope="epd027_full_bank_acquisition",
                    authorized_plan_digest=acquisition_bundle.authorized_plan_digest,
                    profile_digest=acquisition_bundle.profile_digest,
                    budget_digest=acquisition_bundle.full_budget.budget_digest,
                    inventory_digest=acquisition_bundle.inventory_digest,
                    prompt_admission_profile_digest=(
                        acquisition_bundle.prompt_admission_profile_digest
                    ),
                    selected_experiments=("exp2", "exp3", "exp4"),
                    output_root=args.external_bank_root,
                    output_mode="resume",
                    action="reconcile_close",
                    allow_provider_calls=False,
                    now=_UTC_NOW(),
                )
                trace_bindings.append(
                    _paid_scope_binding(
                        validation=bank_validation,
                        scope="epd027_full_bank_acquisition",
                        authorized_plan_digest=(
                            acquisition_bundle.authorized_plan_digest
                        ),
                        profile_digest=acquisition_bundle.profile_digest,
                        budget_digest=acquisition_bundle.full_budget.budget_digest,
                        inventory_digest=acquisition_bundle.inventory_digest,
                        prompt_admission_profile_digest=(
                            acquisition_bundle.prompt_admission_profile_digest
                        ),
                        output_root=args.external_bank_root,
                    )
                )

            l3_root = args.l3_online_check_root
            l3_receipt_path = args.l3_online_check_receipt
            if l3_receipt_path is not None and l3_root is None:
                raise ValueError("L3 online-check receipt requires its evidence root")
            if l3_receipt_path is not None:
                l3_authority = _FORMAL_AUTHORITY_BUILDER(
                    command="run-online-checks",
                    profile=profile,
                    output_root=Path(l3_root),
                    resume=True,
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                )
                l3_validation = _RECEIPT_VALIDATOR(
                    receipt=_RECEIPT_LOADER(l3_receipt_path),
                    requested_scope="epd027_l3_capability_and_online_checks",
                    authorized_plan_digest=l3_authority.plan_digest,
                    profile_digest=profile.profile_digest,
                    budget_digest=l3_authority.budget_digest,
                    inventory_digest=l3_authority.inventory_digest,
                    prompt_admission_profile_digest=(
                        profile.prompt_admission_profile_digest
                    ),
                    selected_experiments=("exp2", "exp3"),
                    output_root=l3_root,
                    output_mode="resume",
                    action="reconcile_close",
                    allow_provider_calls=False,
                    now=_UTC_NOW(),
                )
                trace_bindings.append(
                    _paid_scope_binding(
                        validation=l3_validation,
                        scope="epd027_l3_capability_and_online_checks",
                        authorized_plan_digest=l3_authority.plan_digest,
                        profile_digest=profile.profile_digest,
                        budget_digest=l3_authority.budget_digest,
                        inventory_digest=l3_authority.inventory_digest,
                        prompt_admission_profile_digest=(
                            profile.prompt_admission_profile_digest
                        ),
                        output_root=l3_root,
                    )
                )
            formal_authority = _FORMAL_AUTHORITY_BUILDER(
                command=args.command,
                profile=profile,
                output_root=Path(args.output_root),
                resume=False,
                plan_bundle_root=Path(args.plan_bundle_root),
                external_bank_resolver=resolver,
                paid_authorization_bindings=tuple(trace_bindings),
                l3_online_check_root=(None if l3_root is None else Path(l3_root)),
            )
            if formal_authority.plan_digest != args.plan_digest:
                raise ValueError("formal authority plan digest mismatch")
            if formal_authority.inventory_digest != args.inventory_digest:
                raise ValueError("formal authority inventory digest mismatch")
        evidence_class = (
            "online_real_provider"
            if provider
            else _EVIDENCE_CLASSES[args.command]
        )
        serialized_arguments = {
            "command": args.command,
            "profile_digest": profile.profile_digest,
            "budget_digest": profile.budget_digest,
            "plan_digest": getattr(args, "plan_digest", None),
            "inventory_digest": getattr(args, "inventory_digest", None),
            "plan_bundle_root": getattr(args, "plan_bundle_root", None),
            "output_mode": (
                None
                if not provider
                else ("new_run" if args.new_run else "resume")
            ),
            "resume": bool(getattr(args, "resume", False)),
            "supervised_no_response_closure_root": getattr(
                args, "supervised_no_response_closure_root", None
            ),
            "results_first_scope_preparation_root": getattr(
                args, "results_first_scope_preparation_root", None
            ),
            "local_ai_api_config": getattr(args, "local_ai_api_config", None),
        }
        request = PipelineCommandRequest(
            command=args.command,
            scope=_PROVIDER_SCOPES.get(args.command, args.command),
            evidence_class=evidence_class,
            profile=profile,
            provider_authorization=authorization,
            output_root=(
                None
                if not hasattr(args, "output_root")
                else Path(args.output_root)
            ),
            replay_input_root=(
                None
                if not hasattr(args, "replay_input_root")
                else Path(args.replay_input_root)
            ),
            external_bank_resolver=resolver,
            plan_digest=getattr(args, "plan_digest", None),
            inventory_digest=getattr(args, "inventory_digest", None),
            budget_mode=getattr(args, "budget_mode", "bounded"),
            serialized_arguments=serialized_arguments,
            plan_bundle_root=(
                None
                if getattr(args, "plan_bundle_root", None) is None
                else Path(args.plan_bundle_root)
            ),
            _formal_authority=formal_authority,
        )
        result = _json_result(
            request=request,
            delegated=_default_delegate(request),
        )
    except RepresentativeSmokeExecutionError as exc:
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "blocked",
                    "scope": getattr(args, "command", "run-results-first"),
                    "evidence_class": "results_first_execution",
                    "message": str(exc),
                    **exc.to_summary(),
                },
                sort_keys=True,
            )
        )
        return 3
    except PaperInfrastructureBlockedError as exc:
        blocked_summary = dict(exc.to_summary())
        blocked_summary.setdefault("provider_calls", None)
        blocked_summary.setdefault("total_current_provider_calls", None)
        blocked_summary.setdefault(
            "provider_calls_missing_reason",
            "pipeline_boundary_provider_accounting_unavailable",
        )
        blocked_summary.setdefault("total_current_spend", None)
        blocked_summary.setdefault(
            "total_spend_missing_reason",
            "pipeline_boundary_spend_accounting_unavailable",
        )
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "blocked",
                    "scope": _PROVIDER_SCOPES.get(args.command, args.command),
                    "evidence_class": (
                        "online_real_provider"
                        if args.command in _PROVIDER_SCOPES
                        else _EVIDENCE_CLASSES.get(args.command)
                    ),
                    "message": str(exc),
                    **blocked_summary,
                },
                sort_keys=True,
            )
        )
        return 3
    except (OSError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "blocked",
                    "scope": _PROVIDER_SCOPES.get(args.command, args.command),
                    "evidence_class": (
                        "online_real_provider"
                        if args.command in _PROVIDER_SCOPES
                        else _EVIDENCE_CLASSES.get(args.command)
                    ),
                    "provider_calls": None,
                    "total_current_provider_calls": None,
                    "provider_calls_missing_reason": (
                        "pipeline_boundary_provider_accounting_unavailable"
                    ),
                    "total_current_spend": None,
                    "total_spend_missing_reason": (
                        "pipeline_boundary_spend_accounting_unavailable"
                    ),
                    "failure_kind": "pipeline_boundary_rejected",
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result["status"] in {"completed", "completed_with_failures", "ready"}
        else 3
    )


if __name__ == "__main__":
    from tokenshare.experiments.run_paper_pipeline import main as _canonical_main

    raise SystemExit(_canonical_main())


__all__ = [
    "AcquisitionServiceInput",
    "CellLineageAuditServiceInput",
    "ExternalBankResolverBinding",
    "FormalExecutionGateServiceInput",
    "FormalRunGateServiceInput",
    "FormalSuiteServiceInput",
    "FormalSuiteServiceResult",
    "PAPER_PIPELINE_COMMANDS",
    "PipelineCommandRequest",
    "PaperPublicationGateServiceInput",
    "RepresentativeAcquisitionStageResult",
    "RepresentativeCurrentProviderUsage",
    "RepresentativeFullPlanSmokeServiceAuthority",
    "RepresentativeFullPlanSmokeServiceResult",
    "RepresentativeSmokeExecutionError",
    "ReportRenderServiceInput",
    "ResultsFirstExecutionAuthority",
    "ResultsFirstExecutionPolicy",
    "ResultsFirstExecutionResult",
    "ResultsFirstClosureReplaySnapshot",
    "ResultsFirstTraceClosureReplayAuthority",
    "ResultsFirstServiceInput",
    "ResultsFirstTraceClosurePreflight",
    "audit_results_first_trace_closure_source",
    "build_results_first_execution_authority",
    "build_representative_full_plan_smoke_service_authority",
    "execute_results_first_experiments",
    "execute_representative_full_plan_smoke",
    "execute_typed_gate_command",
    "load_results_first_closure_replay_snapshot",
    "main",
    "persist_results_first_closure_replay_snapshot",
    "resume_results_first_trace_closure_from_snapshot",
    "prepare_representative_formal_bundle",
]
