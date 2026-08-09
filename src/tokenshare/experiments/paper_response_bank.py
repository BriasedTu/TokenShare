"""Exp2--4 真实回答库的确定性 semantic-slot inventory 与零引擎预检。"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, wait
import json
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Callable, ClassVar, Mapping, Sequence

from tokenshare.executors.ai_api import (
    PROVIDER_FAILURE_TAXONOMY,
    PreparedDispatchEvidence,
    dispatch_prepared_request_once,
    prepare_ai_api_outbound_request,
)
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequest,
    PROMPT_ADMISSION_PROFILE_DIGEST,
    validate_prepared_request,
)
from tokenshare.executors.response_bank import (
    OBJECT_ROLES,
    ExternalBankObjectLocator,
    ResultsFirstResponseBankManifest,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ResponseBankResolver,
    canonical_inventory_rows,
    canonical_digest,
    initialize_response_bank,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
    terminal_bank_entry_id,
)
from tokenshare.executors.trace_backed import (
    TraceSourceBinding,
    freeze_trace_source_binding,
)
from tokenshare.experiments.paper_budget_ledger import (
    BudgetExceededError,
    PaperBudgetLedger,
    ReservationRequest,
)
from tokenshare.experiments.paper_budget import (
    L3_SMALL_PAID_BUDGET_LIMITS,
    PaperBudgetLimits,
)
from tokenshare.experiments.paper_paid_authorization import (
    PAID_OUTPUT_BINDING_FILENAME,
    PaidAuthorizationValidation,
    output_root_path_digest as task26_output_root_path_digest,
)
from tokenshare.experiments.paper_faults import select_fault_targets
from tokenshare.experiments.paper_exp3_fault_recovery import (
    RATE_FAULT_TARGET_SEED,
    _worker_death_targets,
)
from tokenshare.experiments.paper_resource_accounting import (
    FrozenPricing,
    ProviderUsage,
)
from tokenshare.core.models import (
    Attempt,
    AttemptState,
    Lease,
    LeaseState,
    ProtocolConfig,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    default_lean_paper_environment_manifest,
)
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.storage.artifacts import ArtifactStore


_EXP2_WORKERS = (1, 3, 7, 10, 30, 50)
_EXP2_CASES = (
    ("early", "factor_v2_hard_034", 0),
    ("middle", "factor_v2_hard_122", 9),
    ("late", "factor_v2_hard_063", 1),
    ("no_factor", "factor_v2_hard_161", 44),
)
_EXP4_MODES = {
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_MERGE_GATE",
}

ACQUISITION_PLAN_BUNDLE_SCHEMA_VERSION = (
    "tokenshare.paper_acquisition_plan_bundle.v1"
)
FULL_ACQUISITION_BUDGET_SCHEMA_VERSION = (
    "tokenshare.paper_full_acquisition_budget.v1"
)
ACQUISITION_PLAN_BUNDLE_FILENAME = "acquisition_plan_bundle.v1.json"
IMMUTABLE_CHILD_BANK_DIRNAME = "immutable_response_bank"


@dataclass(frozen=True, kw_only=True)
class SemanticSlotCandidate:
    """一个 condition 对一个已由 adapter 构造的精确出站请求的引用。"""

    experiment_id: str
    condition_id: str
    condition_digest: str
    worker_count: int
    repeat_id: int
    fault_type: str
    ablation_mode: str
    case_id: str
    case_record_digest: str
    planned_ai_unit_id: str
    sample_slot_index: int
    replacement_slot: int
    prompt_profile_digest: str
    prepared_request: PreparedOutboundRequest
    terminal_kind: str | None = None
    precomputed_inventory_entry_id: str | None = None
    prepared_stable_case_id: str | None = None
    replacement_policy_id: str = "formal_complete"
    fault_rate: float = 0.0
    dead_worker_count: int | None = None
    kill_progress_percent: int | None = None


@dataclass(frozen=True, kw_only=True)
class ResponseBankPreflightBlockedRecord:
    schema_version: str
    inventory_entry_id: str
    semantic_slot_key: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class SemanticInventoryPlan:
    schema_version: str
    inventory_digest: str
    rows: tuple[ResponseBankInventoryRow, ...]
    condition_refs: tuple[dict[str, Any], ...]
    exp2_online_condition_refs: tuple[dict[str, Any], ...]
    max_concurrent_roots: int
    expected_slot_count: int
    terminal_provider_failure_count: int
    terminal_success_count: int
    terminal_unacquired_count: int
    observed_early_stop_slot_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class InventoryPreflightResult:
    schema_version: str
    status: str
    blocked_records: tuple[ResponseBankPreflightBlockedRecord, ...]
    protocol_engine_event_count: int
    provider_call_count: int
    coordinator_constructed: bool
    coordinator: Any = None


@dataclass(frozen=True, kw_only=True)
class FormalTraceInventoryPreflightResult:
    """formal runner 在任何协议对象之前消费的纯 completeness 结果。"""

    status: str
    blocked_records: tuple[ResponseBankPreflightBlockedRecord, ...]
    required_inventory_entry_ids: tuple[str, ...]
    available_inventory_entry_ids: tuple[str, ...]
    protocol_engine_event_count: int = 0
    provider_call_count: int = 0
    schema_version: str = "tokenshare.formal_trace_inventory_preflight.v1"


@dataclass(frozen=True, kw_only=True)
class PaperTraceRuntimeContext:
    """单个 root 的 opaque trace runtime 输入；不保存 source 对象正文。"""

    resolver: "ResponseBankResolver"
    bindings: tuple[TraceSourceBinding, ...]
    paid_receipt_claim: Mapping[str, Any] | None = None
    current_provider_call_count: ClassVar[int] = 0

    def __post_init__(self) -> None:
        from tokenshare.executors.response_bank import ResponseBankResolver

        if not isinstance(self.resolver, ResponseBankResolver):
            raise TypeError("trace runtime resolver must be ResponseBankResolver")
        if not self.bindings or any(
            not isinstance(binding, TraceSourceBinding) for binding in self.bindings
        ):
            raise TypeError("trace runtime bindings must be non-empty TraceSourceBinding values")
        if self.paid_receipt_claim is not None:
            if not isinstance(self.paid_receipt_claim, Mapping):
                raise TypeError("trace paid receipt claim must be a mapping or null")
            object.__setattr__(self, "paid_receipt_claim", dict(self.paid_receipt_claim))


@dataclass(frozen=True, kw_only=True)
class PaperTraceCaseBinding:
    condition_id: str
    case_id: str
    case_record_digest: str
    runtime: PaperTraceRuntimeContext
    inventory_entry_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.condition_id or not self.case_id:
            raise ValueError("trace case binding requires condition_id and case_id")
        if (
            not isinstance(self.case_record_digest, str)
            or not self.case_record_digest.startswith("sha256:")
            or len(self.case_record_digest) != 71
        ):
            raise ValueError("trace case binding requires canonical case_record_digest")
        if (
            not self.inventory_entry_ids
            or len(set(self.inventory_entry_ids)) != len(self.inventory_entry_ids)
            or any(not item for item in self.inventory_entry_ids)
        ):
            raise ValueError("trace case inventory entry identities must be non-empty and unique")


@dataclass(frozen=True, kw_only=True)
class PaperFormalTraceContext:
    inventory_plan: SemanticInventoryPlan
    cases: tuple[PaperTraceCaseBinding, ...]

    def __post_init__(self) -> None:
        keys = tuple((item.condition_id, item.case_id) for item in self.cases)
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("formal trace cases must be non-empty and unique")
        if (
            self.inventory_plan.schema_version
            != "tokenshare.response_bank_semantic_inventory_plan.v1"
            or self.inventory_plan.expected_slot_count
            != len(self.inventory_plan.rows)
            or (
                self.inventory_plan.terminal_provider_failure_count
                + self.inventory_plan.terminal_success_count
                + self.inventory_plan.terminal_unacquired_count
            )
            != len(self.inventory_plan.rows)
        ):
            raise ValueError("formal trace inventory plan identity/count mismatch")
        if canonical_digest(
            [
                row.to_dict()
                for row in sorted(
                    self.inventory_plan.rows,
                    key=lambda item: item.inventory_entry_id,
                )
            ]
        ) != self.inventory_plan.inventory_digest:
            raise ValueError("formal trace inventory identity digest mismatch")
        rows_by_id = {
            row.inventory_entry_id: row for row in self.inventory_plan.rows
        }
        if len(rows_by_id) != len(self.inventory_plan.rows):
            raise ValueError("formal trace inventory identities must be unique")
        condition_slots: dict[str, set[str]] = {}
        case_slots: dict[tuple[str, str, str], set[str]] = {}
        for ref in self.inventory_plan.condition_refs:
            condition_id = str(ref["condition_id"])
            if condition_id in condition_slots:
                raise ValueError("formal trace condition identities must be unique")
            condition_slots[condition_id] = set(ref["semantic_slot_keys"])
            refs_for_condition: set[str] = set()
            case_refs = ref.get("case_refs")
            if not isinstance(case_refs, list) or not case_refs:
                raise ValueError("formal trace plan requires canonical case references")
            for case_ref in case_refs:
                case_id = str(case_ref["case_id"])
                case_record_digest = str(case_ref["case_record_digest"])
                identity = (condition_id, case_id, case_record_digest)
                if identity in case_slots:
                    raise ValueError("formal trace case references must be unique")
                slots = set(case_ref["semantic_slot_keys"])
                if not case_id or not slots:
                    raise ValueError("formal trace case reference identity is incomplete")
                case_slots[identity] = slots
                refs_for_condition.update(slots)
            if refs_for_condition != condition_slots[condition_id]:
                raise ValueError("formal trace case references do not cover condition slots")
        covered_by_condition: dict[str, set[str]] = {}
        covered_cases: set[tuple[str, str, str]] = set()
        for item in self.cases:
            expected_slots = condition_slots.get(item.condition_id)
            if expected_slots is None:
                raise ValueError("formal trace condition identity is absent from plan")
            case_identity = (
                item.condition_id,
                item.case_id,
                item.case_record_digest,
            )
            expected_case_slots = case_slots.get(case_identity)
            if expected_case_slots is None:
                raise ValueError("formal trace case record digest identity mismatch")
            covered_cases.add(case_identity)
            resolver_rows = {
                row.inventory_entry_id: row
                for row in item.runtime.resolver.index.inventory_rows
            }
            declared_ids = set(item.inventory_entry_ids)
            for entry_id in declared_ids:
                planned = rows_by_id.get(entry_id)
                canonical = resolver_rows.get(entry_id)
                if (
                    planned is not None
                    and planned.case_record_digest != item.case_record_digest
                ):
                    raise ValueError("formal trace case record digest identity mismatch")
                if (
                    planned is None
                    or canonical is None
                    or planned.to_dict() != canonical.to_dict()
                    or planned.semantic_slot_key not in expected_slots
                    or planned.semantic_slot_key not in expected_case_slots
                ):
                    raise ValueError("formal trace case inventory identity mismatch")
                covered_by_condition.setdefault(item.condition_id, set()).add(
                    planned.semantic_slot_key
                )
            if {
                rows_by_id[entry_id].semantic_slot_key for entry_id in declared_ids
            } != expected_case_slots:
                raise ValueError("formal trace case semantic identity coverage mismatch")
            terminal_by_id = {
                entry.inventory_entry_id: entry
                for entry in item.runtime.resolver.index.entries
            }
            bound_terminal_ids: set[str] = set()
            for binding in item.runtime.bindings:
                if (
                    binding.bank_root_id
                    != item.runtime.resolver.index.manifest.bank_root_id
                    or binding.manifest_digest
                    != item.runtime.resolver.index.manifest.manifest_digest
                ):
                    raise ValueError("formal trace source binding identity mismatch")
                for replacement in binding.replacements:
                    entry = item.runtime.resolver.index.entry(replacement.entry_id)
                    bound_terminal_ids.add(entry.inventory_entry_id)
                    row = resolver_rows.get(entry.inventory_entry_id)
                    if (
                        entry.inventory_entry_id not in declared_ids
                        or terminal_by_id.get(entry.inventory_entry_id) != entry
                        or row is None
                        or row.planned_ai_unit_id != binding.planned_ai_unit_id
                        or row.sample_slot_index != binding.sample_slot_index
                        or row.replacement_slot != replacement.replacement_slot
                        or row.inference_request_digest
                        != replacement.inference_request_digest
                    ):
                        raise ValueError("formal trace terminal identity mismatch")
            if bound_terminal_ids != (set(terminal_by_id) & declared_ids):
                raise ValueError("formal trace terminal binding coverage identity mismatch")
        if any(
            covered_by_condition.get(condition_id, set()) != slots
            for condition_id, slots in condition_slots.items()
        ):
            raise ValueError("formal trace condition semantic identity coverage mismatch")
        if covered_cases != set(case_slots):
            raise ValueError("formal trace case reference coverage mismatch")

    def runtime_for(self, *, condition_id: str, case_id: str) -> PaperTraceRuntimeContext:
        matches = tuple(
            item.runtime
            for item in self.cases
            if item.condition_id == condition_id and item.case_id == case_id
        )
        if len(matches) != 1:
            raise KeyError("formal trace runtime is missing for condition/case")
        return matches[0]

    @property
    def available_inventory_entry_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                entry.inventory_entry_id
                for item in self.cases
                for entry in item.runtime.resolver.index.entries
                if entry.inventory_entry_id in set(item.inventory_entry_ids)
            )
        )


def build_paper_formal_trace_context(
    *,
    inventory_plan: SemanticInventoryPlan,
    resolver: ResponseBankResolver,
) -> PaperFormalTraceContext:
    """从完整 plan 与已验证 external resolver 构造纯进程内 trace authority。"""

    _validate_semantic_inventory_plan(inventory_plan)
    if type(resolver) is not ResponseBankResolver:
        raise TypeError("trace context resolver must be an exact ResponseBankResolver")
    preflight = preflight_formal_trace_inventory(
        required_inventory_entry_ids=tuple(
            row.inventory_entry_id for row in inventory_plan.rows
        ),
        available_inventory_entry_ids=tuple(
            entry.inventory_entry_id for entry in resolver.index.entries
        ),
    )
    if preflight.status != "ready":
        raise ValueError("response bank is incomplete before trace context construction")

    rows_by_slot = {row.semantic_slot_key: row for row in inventory_plan.rows}
    cases: list[PaperTraceCaseBinding] = []
    for condition_ref in inventory_plan.condition_refs:
        condition_id = str(condition_ref["condition_id"])
        for case_ref in condition_ref["case_refs"]:
            rows = tuple(
                rows_by_slot[str(slot_key)]
                for slot_key in case_ref["semantic_slot_keys"]
            )
            grouped: dict[tuple[str, int], list[ResponseBankInventoryRow]] = {}
            for row in rows:
                grouped.setdefault(
                    (row.planned_ai_unit_id, row.sample_slot_index), []
                ).append(row)
            bindings = tuple(
                freeze_trace_source_binding(
                    resolver,
                    planned_ai_unit_id=planned_ai_unit_id,
                    sample_slot_index=sample_slot_index,
                    entry_ids=tuple(
                        row.entry_id
                        for row in sorted(
                            grouped_rows,
                            key=lambda item: item.replacement_slot,
                        )
                    ),
                )
                for (planned_ai_unit_id, sample_slot_index), grouped_rows in grouped.items()
            )
            runtime = PaperTraceRuntimeContext(resolver=resolver, bindings=bindings)
            cases.append(
                PaperTraceCaseBinding(
                    condition_id=condition_id,
                    case_id=str(case_ref["case_id"]),
                    case_record_digest=str(case_ref["case_record_digest"]),
                    runtime=runtime,
                    inventory_entry_ids=tuple(
                        row.inventory_entry_id for row in rows
                    ),
                )
            )
    return PaperFormalTraceContext(
        inventory_plan=inventory_plan,
        cases=tuple(cases),
    )


def preflight_formal_trace_inventory(
    *,
    required_inventory_entry_ids: Sequence[str],
    available_inventory_entry_ids: Sequence[str],
) -> FormalTraceInventoryPreflightResult:
    """只比较 frozen inventory identity；manifest/index/locator 由 resolver 验证。"""

    required = tuple(dict.fromkeys(str(item) for item in required_inventory_entry_ids))
    available = tuple(dict.fromkeys(str(item) for item in available_inventory_entry_ids))
    if any(not item for item in (*required, *available)):
        raise ValueError("inventory entry ids must be non-empty")
    available_set = set(available)
    missing = tuple(item for item in required if item not in available_set)
    records = tuple(
        ResponseBankPreflightBlockedRecord(
            schema_version="tokenshare.response_bank_preflight_blocked.v1",
            inventory_entry_id=entry_id,
            semantic_slot_key="unavailable_before_protocol",
            reason="required semantic slot is missing",
        )
        for entry_id in missing
    )
    return FormalTraceInventoryPreflightResult(
        status="blocked" if records else "ready",
        blocked_records=records,
        required_inventory_entry_ids=required,
        available_inventory_entry_ids=available,
    )


class AcquisitionAuthorizationError(RuntimeError):
    pass


class AcquisitionIdentityError(RuntimeError):
    pass


RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION = (
    "tokenshare.results_first_smoke_acquisition_authorization.v1"
)
RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION = (
    "tokenshare.results_first_smoke_facility_marker.v1"
)
RESULTS_FIRST_ACQUISITION_MARKER_FILENAME = (
    "results_first_smoke_facility_marker.v1.json"
)


@dataclass(frozen=True, kw_only=True)
class ResultsFirstAcquisitionMarker:
    schema_version: str
    authorization_kind: str
    authorization_digest: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    provider_config_digest: str
    output_root_path_digest: str
    marker_digest: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, kw_only=True)
class ResultsFirstAcquisitionAuthorization:
    """用户显式授权的 smoke acquisition facility；不是 paid receipt。"""

    schema_version: str
    authorization_kind: str
    authorization_digest: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    provider_config_digest: str
    output_root_path_digest: str
    selected_experiments: tuple[str, ...]
    marker: ResultsFirstAcquisitionMarker
    output_mode: str
    authorization_state: str
    provider_dispatch_allowed: bool


def establish_results_first_acquisition_authorization(
    *,
    bundle: "AcquisitionPlanBundle",
    output_root: str | Path,
    output_mode: str,
    allow_provider_calls: bool,
) -> ResultsFirstAcquisitionAuthorization:
    """以显式 results-first flag 建立 create-new/resume facility marker。"""

    bundle.validate()
    if output_mode not in {"new_run", "resume"}:
        raise AcquisitionAuthorizationError("invalid acquisition invocation mode")
    if allow_provider_calls is not True:
        raise AcquisitionAuthorizationError(
            "results-first acquisition requires explicit provider-call authorization"
        )
    root = Path(output_root).resolve()
    selected_experiments = (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    )
    identity = {
        "schema_version": RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_kind": "user_authorized_smoke_facility",
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
        "provider_config_digest": bundle.provider_config_digest,
        "output_root_path_digest": output_root_path_digest(root),
        "selected_experiments": list(selected_experiments),
    }
    authorization_digest = canonical_digest(identity)
    marker_body = {
        "schema_version": RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION,
        "authorization_kind": "user_authorized_smoke_facility",
        "authorization_digest": authorization_digest,
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
        "provider_config_digest": bundle.provider_config_digest,
        "output_root_path_digest": output_root_path_digest(root),
    }
    marker = ResultsFirstAcquisitionMarker(
        **marker_body,
        marker_digest=canonical_digest(marker_body),
    )
    marker_path = root / RESULTS_FIRST_ACQUISITION_MARKER_FILENAME
    if output_mode == "new_run":
        root.mkdir(parents=True, exist_ok=False)
        try:
            with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    marker.to_dict(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
        except BaseException:
            if marker_path.exists():
                marker_path.unlink()
            root.rmdir()
            raise
    else:
        try:
            persisted = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AcquisitionAuthorizationError(
                "results-first facility marker is missing or partial"
            ) from exc
        if persisted != marker.to_dict():
            raise AcquisitionAuthorizationError(
                "results-first facility marker identity mismatch"
            )
    return ResultsFirstAcquisitionAuthorization(
        schema_version=RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION,
        authorization_kind="user_authorized_smoke_facility",
        authorized_plan_digest=bundle.authorized_plan_digest,
        profile_digest=bundle.profile_digest,
        budget_digest=bundle.full_budget.budget_digest,
        inventory_digest=bundle.inventory_digest,
        prompt_admission_profile_digest=(
            bundle.prompt_admission_profile_digest
        ),
        provider_config_digest=bundle.provider_config_digest,
        output_root_path_digest=output_root_path_digest(root),
        selected_experiments=selected_experiments,
        authorization_digest=authorization_digest,
        marker=marker,
        output_mode=output_mode,
        authorization_state="user_authorized_smoke_facility",
        provider_dispatch_allowed=True,
    )


@dataclass(frozen=True, kw_only=True)
class PaidAcquisitionContext:
    """历史 evidence fixture 使用的 Task 26 paid context 值对象。"""

    receipt_digest: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    output_root_path_digest: str
    paid_scope_digest: str
    expires_at_epoch: int
    reacquisition_limit: int


@dataclass(frozen=True, kw_only=True)
class FullAcquisitionBudget:
    """由 bundle 内全部 request ceiling 独立推导的 acquisition 硬预算。"""

    schema_version: str
    calls: int
    tokens: int
    cny: Decimal
    deepseek_cumulative_cny: Decimal
    budget_digest: str

    @classmethod
    def create(
        cls, requests: Sequence["AcquisitionRequest"]
    ) -> "FullAcquisitionBudget":
        values = tuple(requests)
        if not values:
            raise ValueError("full acquisition budget requires non-empty requests")
        calls = len(values)
        tokens = sum(item.token_upper_bound for item in values)
        cny = sum((item.cost_upper_bound for item in values), Decimal("0"))
        deepseek_cny = sum(
            (
                item.cost_upper_bound
                for item in values
                if item.provider_family == "deepseek"
            ),
            Decimal("0"),
        )
        provisional = cls(
            schema_version=FULL_ACQUISITION_BUDGET_SCHEMA_VERSION,
            calls=calls,
            tokens=tokens,
            cny=cny,
            deepseek_cumulative_cny=max(deepseek_cny, Decimal("0.000001")),
            budget_digest="",
        )
        result = replace(
            provisional,
            budget_digest=canonical_digest(provisional.digest_preimage()),
        )
        result.validate()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FullAcquisitionBudget":
        expected = {
            "schema_version",
            "calls",
            "tokens",
            "cny",
            "deepseek_cumulative_cny",
            "budget_digest",
        }
        if set(value) != expected:
            raise ValueError("full acquisition budget fields do not match v1 schema")
        result = cls(
            schema_version=str(value["schema_version"]),
            calls=int(value["calls"]),
            tokens=int(value["tokens"]),
            cny=Decimal(str(value["cny"])),
            deepseek_cumulative_cny=Decimal(
                str(value["deepseek_cumulative_cny"])
            ),
            budget_digest=str(value["budget_digest"]),
        )
        result.validate()
        return result

    def digest_preimage(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "calls": self.calls,
            "tokens": self.tokens,
            "cny": str(self.cny),
            "deepseek_cumulative_cny": str(self.deepseek_cumulative_cny),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.digest_preimage(), "budget_digest": self.budget_digest}

    def to_limits(self) -> PaperBudgetLimits:
        self.validate()
        return PaperBudgetLimits(
            calls=self.calls,
            tokens=self.tokens,
            cny=self.cny,
            deepseek_cumulative_cny=self.deepseek_cumulative_cny,
        )

    def validate(self) -> None:
        if self.schema_version != FULL_ACQUISITION_BUDGET_SCHEMA_VERSION:
            raise ValueError("full acquisition budget schema version drift")
        if self.calls < 1 or self.tokens < 1 or self.cny <= 0:
            raise ValueError("full acquisition budget limits must be positive")
        if self.deepseek_cumulative_cny <= 0:
            raise ValueError("full acquisition DeepSeek cumulative limit must be positive")
        expected = canonical_digest(self.digest_preimage())
        if self.budget_digest != expected:
            raise ValueError("full acquisition budget digest mismatch")
        l3 = L3_SMALL_PAID_BUDGET_LIMITS
        if (
            self.calls == l3.calls
            and self.tokens == l3.tokens
            and self.cny == l3.cny
        ):
            raise ValueError("L3 516-call budget cannot authorize full bank acquisition")


@dataclass(frozen=True, kw_only=True)
class AcquisitionPlanBundle:
    """create-only 持久化的 exact prepared requests 与 canonical inventory。"""

    schema_version: str
    bundle_digest: str
    authorized_plan_digest: str
    profile_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    provider_config_digest: str
    semantic_inventory_plan: SemanticInventoryPlan
    inventory_rows: tuple[ResponseBankInventoryRow, ...]
    acquisition_requests: tuple["AcquisitionRequest", ...]
    full_budget: FullAcquisitionBudget

    def to_dict(self) -> dict[str, object]:
        return {
            **self.digest_preimage(),
            "bundle_digest": self.bundle_digest,
        }

    def digest_preimage(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorized_plan_digest": self.authorized_plan_digest,
            "profile_digest": self.profile_digest,
            "inventory_digest": self.inventory_digest,
            "prompt_admission_profile_digest": self.prompt_admission_profile_digest,
            "provider_config_digest": self.provider_config_digest,
            "semantic_inventory_plan": _semantic_inventory_plan_to_dict(
                self.semantic_inventory_plan
            ),
            "inventory_rows": [row.to_dict() for row in self.inventory_rows],
            "acquisition_requests": [
                _acquisition_request_to_dict(item)
                for item in self.acquisition_requests
            ],
            "full_budget": self.full_budget.to_dict(),
        }

    def validate(self) -> None:
        if self.schema_version != ACQUISITION_PLAN_BUNDLE_SCHEMA_VERSION:
            raise ValueError("acquisition plan bundle schema version drift")
        if self.bundle_digest != canonical_digest(self.digest_preimage()):
            raise ValueError("acquisition plan bundle digest mismatch")
        rows = canonical_inventory_rows(self.inventory_rows)
        if rows != self.inventory_rows:
            raise ValueError("acquisition plan bundle inventory is not canonical")
        if response_bank_inventory_digest(rows) != self.inventory_digest:
            raise ValueError("acquisition plan bundle inventory digest mismatch")
        _validate_semantic_inventory_plan(self.semantic_inventory_plan)
        if self.semantic_inventory_plan.rows != rows:
            raise ValueError("semantic plan rows do not match acquisition inventory")
        if self.semantic_inventory_plan.inventory_digest != self.inventory_digest:
            raise ValueError("semantic plan digest does not match acquisition inventory")
        if len(rows) != len(self.acquisition_requests):
            raise ValueError("acquisition plan bundle request cardinality mismatch")
        by_id = {
            item.inventory_row.inventory_entry_id: item
            for item in self.acquisition_requests
        }
        if len(by_id) != len(self.acquisition_requests):
            raise ValueError("acquisition plan bundle has duplicate requests")
        if tuple(item.inventory_row for item in self.acquisition_requests) != rows:
            raise ValueError("acquisition requests are not in canonical inventory order")
        for row, request in zip(rows, self.acquisition_requests, strict=True):
            prepared = validate_prepared_request(request.prepared_request)
            if request.inventory_row != row:
                raise ValueError("acquisition request inventory row mismatch")
            if (
                prepared.inference_request_digest != row.inference_request_digest
                or prepared.body_digest != row.body_digest
                or prepared.provider_config_digest != row.provider_config_digest
            ):
                raise ValueError("acquisition request prepared identity mismatch")
            if prepared.prompt_admission_profile_digest != (
                self.prompt_admission_profile_digest
            ):
                raise ValueError("acquisition request admission digest mismatch")
        provider_digests = tuple(
            sorted({row.provider_config_digest for row in rows})
        )
        expected_provider_digest = (
            provider_digests[0]
            if len(provider_digests) == 1
            else canonical_digest(provider_digests)
        )
        if self.provider_config_digest != expected_provider_digest:
            raise ValueError("acquisition plan provider config digest mismatch")
        self.full_budget.validate()
        if self.full_budget != FullAcquisitionBudget.create(
            self.acquisition_requests
        ):
            raise ValueError("full acquisition budget does not match request ceilings")


@dataclass(frozen=True, kw_only=True)
class AcquisitionRequest:
    inventory_row: ResponseBankInventoryRow
    prepared_request: PreparedOutboundRequest
    provider_family: str
    api_key_env: str
    timeout_seconds: int
    token_upper_bound: int
    cost_upper_bound: Decimal
    frozen_pricing: FrozenPricing
    requested_at: str

    def __post_init__(self) -> None:
        if self.provider_family not in {"siliconflow", "openai", "deepseek"}:
            raise ValueError("unsupported acquisition provider family")
        if not self.api_key_env or self.timeout_seconds < 1:
            raise ValueError("acquisition transport controls are invalid")
        if self.token_upper_bound < 1 or self.cost_upper_bound <= 0:
            raise ValueError("acquisition reservation bounds must be positive")


@dataclass(frozen=True, kw_only=True)
class Matrix8UnifiedAcquisitionPlan:
    """Exp1 统一采集 Exp1--4 regression matrix8 exact prompts 的纯计划。"""

    combined_profile_digest: str
    condition_candidate_count: int
    candidates: tuple[SemanticSlotCandidate, ...]
    semantic_inventory_plan: SemanticInventoryPlan
    acquisition_requests: tuple[AcquisitionRequest, ...]
    case_identity_mappings: tuple[dict[str, str], ...]
    provider_call_count: int = 0
    schema_version: str = "tokenshare.matrix8_unified_acquisition_plan.v1"


def create_acquisition_plan_bundle(
    bundle_root: str | Path,
    *,
    authorized_plan_digest: str,
    profile_digest: str,
    semantic_inventory_plan: SemanticInventoryPlan,
    acquisition_requests: Sequence[AcquisitionRequest],
) -> AcquisitionPlanBundle:
    """以 create-new 方式持久化完整 acquisition authority。"""

    _validate_semantic_inventory_plan(semantic_inventory_plan)
    rows = canonical_inventory_rows(semantic_inventory_plan.rows)
    requests_by_id = {
        item.inventory_row.inventory_entry_id: item
        for item in acquisition_requests
    }
    if len(requests_by_id) != len(tuple(acquisition_requests)):
        raise ValueError("acquisition plan bundle has duplicate requests")
    try:
        requests = tuple(requests_by_id[row.inventory_entry_id] for row in rows)
    except KeyError as exc:
        raise ValueError("acquisition plan bundle is missing an inventory request") from exc
    if len(requests) != len(requests_by_id):
        raise ValueError("acquisition plan bundle has an extra inventory request")
    admission_digests = {
        item.prepared_request.prompt_admission_profile_digest for item in requests
    }
    if len(admission_digests) != 1:
        raise ValueError("acquisition plan bundle admission profile must be unique")
    provider_digests = tuple(sorted({row.provider_config_digest for row in rows}))
    provider_config_digest = (
        provider_digests[0]
        if len(provider_digests) == 1
        else canonical_digest(provider_digests)
    )
    budget = FullAcquisitionBudget.create(requests)
    provisional = AcquisitionPlanBundle(
        schema_version=ACQUISITION_PLAN_BUNDLE_SCHEMA_VERSION,
        bundle_digest="",
        authorized_plan_digest=authorized_plan_digest,
        profile_digest=profile_digest,
        inventory_digest=response_bank_inventory_digest(rows),
        prompt_admission_profile_digest=next(iter(admission_digests)),
        provider_config_digest=provider_config_digest,
        semantic_inventory_plan=semantic_inventory_plan,
        inventory_rows=rows,
        acquisition_requests=requests,
        full_budget=budget,
    )
    bundle = replace(
        provisional,
        bundle_digest=canonical_digest(provisional.digest_preimage()),
    )
    bundle.validate()
    root = Path(bundle_root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    path = root / ACQUISITION_PLAN_BUNDLE_FILENAME
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                bundle.to_dict(),
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
    except BaseException:
        if path.exists():
            path.unlink()
        root.rmdir()
        raise
    return load_acquisition_plan_bundle(root)


def load_acquisition_plan_bundle(
    bundle_root: str | Path,
) -> AcquisitionPlanBundle:
    path = Path(bundle_root).resolve() / ACQUISITION_PLAN_BUNDLE_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("acquisition plan bundle is unreadable") from exc
    if not isinstance(value, Mapping):
        raise ValueError("acquisition plan bundle must be a JSON object")
    expected = {
        "schema_version",
        "bundle_digest",
        "authorized_plan_digest",
        "profile_digest",
        "inventory_digest",
        "prompt_admission_profile_digest",
        "provider_config_digest",
        "semantic_inventory_plan",
        "inventory_rows",
        "acquisition_requests",
        "full_budget",
    }
    if set(value) != expected:
        raise ValueError("acquisition plan bundle fields do not match v1 schema")
    raw_rows = value["inventory_rows"]
    raw_requests = value["acquisition_requests"]
    if not isinstance(raw_rows, list) or not isinstance(raw_requests, list):
        raise ValueError("acquisition plan bundle rows and requests must be lists")
    bundle = AcquisitionPlanBundle(
        schema_version=str(value["schema_version"]),
        bundle_digest=str(value["bundle_digest"]),
        authorized_plan_digest=str(value["authorized_plan_digest"]),
        profile_digest=str(value["profile_digest"]),
        inventory_digest=str(value["inventory_digest"]),
        prompt_admission_profile_digest=str(
            value["prompt_admission_profile_digest"]
        ),
        provider_config_digest=str(value["provider_config_digest"]),
        semantic_inventory_plan=_semantic_inventory_plan_from_dict(
            value["semantic_inventory_plan"]
        ),
        inventory_rows=tuple(ResponseBankInventoryRow.from_dict(item) for item in raw_rows),
        acquisition_requests=tuple(
            _acquisition_request_from_dict(item) for item in raw_requests
        ),
        full_budget=FullAcquisitionBudget.from_dict(value["full_budget"]),
    )
    bundle.validate()
    return bundle


@dataclass(frozen=True, kw_only=True)
class AcquisitionResult:
    status: str
    entry: ResponseBankEntry | None
    lifecycle_order: tuple[str, ...]
    transport_invoked: bool
    failure_kind: str | None
    attempt_id: str | None
    linked_ambiguous_attempt_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AcquisitionBatchResult:
    status: str
    results: tuple[AcquisitionResult, ...]
    missing_inventory_entry_ids: tuple[str, ...]
    ambiguous_inventory_entry_ids: tuple[str, ...]
    blocked_reason: str | None


@dataclass(frozen=True, kw_only=True)
class AcquisitionReconcileReport:
    reconciled_pre_dispatch: tuple[str, ...]
    ambiguous_inventory_entry_ids: tuple[str, ...]
    settled_inventory_entry_ids: tuple[str, ...]
    missing_inventory_entry_ids: tuple[str, ...]


def response_bank_manifest_for_bundle(
    bundle: AcquisitionPlanBundle,
    authorization: PaidAuthorizationValidation,
) -> ResponseBankManifest:
    """在 dispatch 前把 plan/budget/inventory 与 Task26 receipt 绑定为 manifest。"""

    bundle.validate()
    if type(authorization) is not PaidAuthorizationValidation:
        raise AcquisitionAuthorizationError(
            "manifest requires Task26 PaidAuthorizationValidation"
        )
    receipt = authorization.receipt
    expected = {
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
    }
    for name, value in expected.items():
        if getattr(receipt, name) != value:
            raise AcquisitionAuthorizationError(
                f"paid receipt {name} does not match acquisition bundle"
            )
    bank_root_id = canonical_digest(
        {
            "schema_version": "tokenshare.paper_response_bank_root_identity.v1",
            "bundle_digest": bundle.bundle_digest,
            "receipt_digest": receipt.receipt_digest,
        }
    )
    return ResponseBankManifest.create(
        bank_root_id=bank_root_id,
        profile_digest=bundle.profile_digest,
        budget_digest=bundle.full_budget.budget_digest,
        inventory_digest=bundle.inventory_digest,
        provider_config_digest=bundle.provider_config_digest,
        entry_ids=tuple(row.entry_id for row in bundle.inventory_rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(bundle.inventory_rows),
        created_by_paid_receipt_digest=receipt.receipt_digest,
    )


def results_first_response_bank_manifest_for_bundle(
    bundle: AcquisitionPlanBundle,
    authorization: ResultsFirstAcquisitionAuthorization,
) -> ResultsFirstResponseBankManifest:
    """把 smoke bundle 绑定到显式 facility authorization，而非 paid receipt。"""

    bundle.validate()
    if type(authorization) is not ResultsFirstAcquisitionAuthorization:
        raise AcquisitionAuthorizationError(
            "results-first manifest requires facility authorization"
        )
    expected = {
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
        "provider_config_digest": bundle.provider_config_digest,
    }
    for name, value in expected.items():
        if getattr(authorization, name) != value:
            raise AcquisitionAuthorizationError(
                f"facility authorization {name} does not match acquisition bundle"
            )
    bank_root_id = canonical_digest(
        {
            "schema_version": "tokenshare.results_first_response_bank_root_identity.v1",
            "bundle_digest": bundle.bundle_digest,
            "authorization_digest": authorization.authorization_digest,
        }
    )
    return ResultsFirstResponseBankManifest.create(
        bank_root_id=bank_root_id,
        profile_digest=bundle.profile_digest,
        budget_digest=bundle.full_budget.budget_digest,
        inventory_digest=bundle.inventory_digest,
        provider_config_digest=bundle.provider_config_digest,
        entry_ids=tuple(row.entry_id for row in bundle.inventory_rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(bundle.inventory_rows),
        authorization_digest=authorization.authorization_digest,
    )


def finalize_acquisition_child_bank(
    *,
    orchestrator: "ResponseBankAcquisitionOrchestrator",
    bundle: AcquisitionPlanBundle,
    manifest: ResponseBankManifest | ResultsFirstResponseBankManifest,
    batch_result: AcquisitionBatchResult,
) -> ResponseBankResolver | None:
    """complete batch 仅初始化一次 child bank；resume 只重开并复验。"""

    bundle.validate()
    if (
        batch_result.status != "complete"
        or batch_result.missing_inventory_entry_ids
        or batch_result.ambiguous_inventory_entry_ids
    ):
        return None
    if (
        manifest.manifest_digest != orchestrator.manifest_digest
        or manifest.bank_root_id != orchestrator.bank_root_id
        or manifest.inventory_digest != bundle.inventory_digest
    ):
        raise AcquisitionIdentityError("child bank manifest does not match orchestrator")
    child_root = orchestrator.output_root / IMMUTABLE_CHILD_BANK_DIRNAME
    if child_root.exists():
        resolver = ResponseBankResolver.open(child_root)
        if resolver.index.manifest.manifest_digest != manifest.manifest_digest:
            raise AcquisitionIdentityError("resume child bank manifest mismatch")
        return resolver
    entries = orchestrator.terminal_entries()
    if len(entries) != len(bundle.inventory_rows):
        raise AcquisitionIdentityError("complete acquisition is missing terminal entries")
    objects: dict[str, bytes] = {}
    for entry in entries:
        for locator in entry.object_locators:
            payload = orchestrator.read_role_bytes(entry, locator.object_role)
            existing = objects.setdefault(locator.object_digest, payload)
            if existing != payload:
                raise AcquisitionIdentityError("terminal object digest collision")
    initialize_response_bank(
        child_root,
        manifest=manifest,
        inventory_rows=bundle.inventory_rows,
        entries=entries,
        objects=objects,
    )
    return ResponseBankResolver.open(child_root)


def output_root_path_digest(path: str | Path) -> str:
    """兼容导出；实际 identity 只委托 Task26 的 canonical path contract。"""

    return task26_output_root_path_digest(path)


class ResponseBankAcquisitionOrchestrator:
    """只负责 bank acquisition evidence；不运行 TokenShare protocol lifecycle。"""

    def __init__(
        self,
        *,
        output_root: str | Path,
        bank_root_id: str,
        manifest_digest: str,
        inventory_digest: str,
        inventory_rows: Sequence[ResponseBankInventoryRow],
        budget_ledger: PaperBudgetLedger,
        paid_authorization: PaidAuthorizationValidation | None = None,
        facility_authorization: ResultsFirstAcquisitionAuthorization | None = None,
        invocation_mode: str,
        transport: Any,
        secret_resolver: Callable[[str], str],
        now_epoch: int,
        crash_hook: Callable[[str], None] | None = None,
        durability_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.bank_root_id = bank_root_id
        self.manifest_digest = manifest_digest
        self.inventory_digest = inventory_digest
        self.inventory_rows = tuple(inventory_rows)
        self._rows_by_id = {
            row.inventory_entry_id: ResponseBankInventoryRow.from_dict(row.to_dict())
            for row in self.inventory_rows
        }
        if len(self._rows_by_id) != len(self.inventory_rows):
            raise AcquisitionIdentityError("duplicate inventory_entry_id")
        expected_inventory_digest = response_bank_inventory_digest(
            self.inventory_rows
        )
        if expected_inventory_digest != inventory_digest:
            raise AcquisitionIdentityError("inventory digest does not match rows")
        self.budget_ledger = budget_ledger
        self.paid_authorization = paid_authorization
        self.facility_authorization = facility_authorization
        if (type(paid_authorization) is PaidAuthorizationValidation) == (
            type(facility_authorization) is ResultsFirstAcquisitionAuthorization
        ):
            raise AcquisitionAuthorizationError(
                "acquisition requires exactly one explicit authorization"
            )
        if paid_authorization is not None:
            self.authorization_kind = "paid_receipt"
            self.authorization_digest = paid_authorization.receipt.receipt_digest
            self.paid_scope_digest = canonical_digest(
                {
                    "schema_version": "tokenshare.paid_acquisition_scope.v1",
                    "scope": paid_authorization.receipt.scope,
                    "receipt_digest": paid_authorization.receipt.receipt_digest,
                }
            )
        else:
            assert facility_authorization is not None
            self.authorization_kind = facility_authorization.authorization_kind
            self.authorization_digest = facility_authorization.authorization_digest
            self.paid_scope_digest = None
        self.invocation_mode = invocation_mode
        self.transport = transport
        self.secret_resolver = secret_resolver
        self.now_epoch = now_epoch
        self.crash_hook = crash_hook
        self.durability_hook = durability_hook
        self._base_lifecycle: list[str] = []
        self._validate_authorization_context()
        self._base_lifecycle.append(
            "paid_context_validated"
            if paid_authorization is not None
            else "facility_context_validated"
        )
        if invocation_mode not in {"new_run", "resume"}:
            raise AcquisitionAuthorizationError("invalid acquisition invocation mode")
        self._base_lifecycle.append("invocation_mode_validated")
        if invocation_mode == "new_run" and self._is_expired():
            raise AcquisitionAuthorizationError(
                "expired paid context cannot create an acquisition output root"
            )
        self._open_output_binding()
        self._base_lifecycle.append("output_marker_validated")
        self._object_store = ArtifactStore(
            self.output_root, artifact_dir_name="objects"
        )
        self._entry_store = ArtifactStore(
            self.output_root, artifact_dir_name="entries"
        )
        # Provider dispatch 可并发；Windows 上同内容 role 的 durable marker commit 需串行。
        self._artifact_write_lock = Lock()

    def _validate_authorization_context(self) -> None:
        context = self.paid_authorization
        if type(context) is PaidAuthorizationValidation:
            self._validate_paid_context(context)
            return
        facility = self.facility_authorization
        if type(facility) is not ResultsFirstAcquisitionAuthorization:
            raise AcquisitionAuthorizationError(
                "results-first facility authorization is missing"
            )
        required = (
            facility.authorization_digest,
            facility.authorized_plan_digest,
            facility.profile_digest,
            facility.budget_digest,
            facility.inventory_digest,
            facility.prompt_admission_profile_digest,
            facility.provider_config_digest,
            facility.output_root_path_digest,
            facility.marker.marker_digest,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise AcquisitionAuthorizationError(
                "results-first acquisition context is incomplete"
            )
        if facility.authorization_kind != "user_authorized_smoke_facility":
            raise AcquisitionAuthorizationError(
                "results-first acquisition authorization kind mismatch"
            )
        if facility.inventory_digest != self.inventory_digest:
            raise AcquisitionAuthorizationError("facility inventory digest mismatch")
        if (
            facility.prompt_admission_profile_digest
            != PROMPT_ADMISSION_PROFILE_DIGEST
        ):
            raise AcquisitionAuthorizationError(
                "facility prompt admission digest mismatch"
            )
        if facility.output_root_path_digest != output_root_path_digest(self.output_root):
            raise AcquisitionAuthorizationError("facility output root digest mismatch")
        if facility.output_mode != self.invocation_mode:
            raise AcquisitionAuthorizationError(
                "facility authorization output mode mismatch"
            )

    def _validate_paid_context(self, context: PaidAuthorizationValidation) -> None:
        receipt = context.receipt
        required = (
            receipt.receipt_digest,
            receipt.authorized_plan_digest,
            receipt.profile_digest,
            receipt.budget_digest,
            receipt.inventory_digest,
            receipt.prompt_admission_profile_digest,
            receipt.output_root_path_digest,
            context.marker.marker_digest,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise AcquisitionAuthorizationError("paid acquisition context is incomplete")
        if receipt.inventory_digest != self.inventory_digest:
            raise AcquisitionAuthorizationError("paid inventory digest mismatch")
        if receipt.prompt_admission_profile_digest != PROMPT_ADMISSION_PROFILE_DIGEST:
            raise AcquisitionAuthorizationError("paid prompt admission digest mismatch")
        if receipt.output_root_path_digest != output_root_path_digest(self.output_root):
            raise AcquisitionAuthorizationError("paid output root digest mismatch")
        if context.output_mode != self.invocation_mode:
            raise AcquisitionAuthorizationError("paid authorization output mode mismatch")

    def _open_output_binding(self) -> None:
        paid = self.paid_authorization
        if paid is not None:
            marker_path = self.output_root / PAID_OUTPUT_BINDING_FILENAME
            expected_marker = paid.marker.to_dict()
            missing_message = "Task26 output binding marker is missing or partial"
            mismatch_message = "Task26 output binding marker mismatch"
        else:
            facility = self.facility_authorization
            assert facility is not None
            marker_path = self.output_root / RESULTS_FIRST_ACQUISITION_MARKER_FILENAME
            expected_marker = facility.marker.to_dict()
            missing_message = "results-first facility marker is missing or partial"
            mismatch_message = "results-first facility marker mismatch"
        if not self.output_root.is_dir():
            raise AcquisitionAuthorizationError(
                "Task26 paid output root is missing"
                if paid is not None
                else "results-first facility output root is missing"
            )
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AcquisitionAuthorizationError(missing_message) from exc
        if marker != expected_marker:
            raise AcquisitionAuthorizationError(mismatch_message)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        existing = self._load_entry(request.inventory_row.inventory_entry_id)
        if existing is not None:
            self._reconcile_committed_entry(existing)
            return AcquisitionResult(
                status="already_terminal",
                entry=existing,
                lifecycle_order=tuple(self._base_lifecycle),
                transport_invoked=False,
                failure_kind=None,
                attempt_id=existing.acquisition_state_ref,
            )
        self._require_unexpired()
        lifecycle = list(self._base_lifecycle)
        row, prepared = self._validate_acquisition_request(request)
        lifecycle.append("prepared")
        lifecycle.append("consistency_validated")
        lifecycle.append("admitted")
        with self._artifact_write_lock:
            request_ref = self._object_store.save_content_addressed_bytes(
                prepared.body_bytes,
                artifact_type="PreparedOutboundRequest",
                media_type="application/json",
                artifact_schema_id="tokenshare.prepared_outbound_request",
                artifact_schema_version="v1",
                source={"kind": "response_bank_acquisition"},
                metadata={"object_role": "request_body"},
                created_at="content-addressed",
                durability_hook=self.durability_hook,
            )
        lifecycle.append("prepared_artifact_committed")
        reservation_request = self._reservation_request(request)
        decision = self.budget_ledger.reserve(reservation_request)
        if not decision.granted:
            winner = decision.reservation
            if winner.inventory_entry_id != row.inventory_entry_id:
                raise AcquisitionIdentityError(
                    "semantic-slot winner has a different inference request digest"
                )
            if winner.state in {"dispatch_intent", "ambiguous"}:
                if winner.state == "dispatch_intent":
                    self.budget_ledger.mark_ambiguous(
                        self.inventory_digest, row.inventory_entry_id
                    )
                return AcquisitionResult(
                    status="ambiguous",
                    entry=None,
                    lifecycle_order=tuple(lifecycle),
                    transport_invoked=False,
                    failure_kind=None,
                    attempt_id=self._primary_attempt_id(row),
                )
            return AcquisitionResult(
                status=winner.state,
                entry=self._load_entry(row.inventory_entry_id),
                lifecycle_order=tuple(lifecycle),
                transport_invoked=False,
                failure_kind=None,
                attempt_id=self._primary_attempt_id(row),
            )
        lifecycle.append("reserved")
        self._crash("reserved")
        self.budget_ledger.mark_dispatch_intent(
            self.inventory_digest, row.inventory_entry_id
        )
        lifecycle.append("dispatch_intent")
        self._crash("dispatch_intent")
        api_key = self.secret_resolver(request.api_key_env)
        if not isinstance(api_key, str) or not api_key:
            raise AcquisitionAuthorizationError("resolved acquisition secret is missing")
        lifecycle.append("secret_resolved")
        evidence = dispatch_prepared_request_once(
            prepared_request=prepared,
            provider_family=request.provider_family,
            transport=self.transport,
            api_key=api_key,
            timeout_seconds=request.timeout_seconds,
        )
        del api_key
        lifecycle.append("transport_sent")
        self._crash("transport_sent")
        result = self._publish_evidence(
            request=request,
            row=row,
            prepared=prepared,
            request_ref=request_ref,
            evidence=evidence,
            lifecycle=lifecycle,
            attempt_id=self._primary_attempt_id(row),
            linked_ambiguous_attempt_id=None,
            reacquisition_id=None,
        )
        return result

    def reacquire(
        self,
        request: AcquisitionRequest,
        *,
        paid_scope_digest: str,
    ) -> AcquisitionResult:
        existing = self._load_entry(request.inventory_row.inventory_entry_id)
        if existing is not None:
            self._reconcile_committed_entry(existing)
            return AcquisitionResult(
                status="already_terminal",
                entry=existing,
                lifecycle_order=tuple(self._base_lifecycle),
                transport_invoked=False,
                failure_kind=None,
                attempt_id=existing.acquisition_state_ref,
            )
        self._require_unexpired()
        if self.paid_authorization is None:
            raise AcquisitionAuthorizationError(
                "results-first facility does not authorize ambiguous reacquisition"
            )
        if (
            self.paid_authorization.receipt.scope
            != "epd027_full_bank_acquisition"
            or paid_scope_digest != self.paid_scope_digest
        ):
            raise AcquisitionAuthorizationError(
                "reacquisition is outside the validated paid scope"
            )
        row, prepared = self._validate_acquisition_request(request)
        primary = self.budget_ledger.get_reservation(
            self.inventory_digest, row.inventory_entry_id
        )
        if primary.state != "ambiguous":
            raise AcquisitionIdentityError("reacquisition requires ambiguous attempt")
        linked = self._primary_attempt_id(row)
        reacquisition_id = canonical_digest(
            {
                "inventory_digest": self.inventory_digest,
                "inventory_entry_id": row.inventory_entry_id,
                "linked_ambiguous_attempt_id": linked,
                "paid_scope_digest": paid_scope_digest,
                "ordinal": 1,
            }
        )
        request_ref = self._object_store.save_content_addressed_bytes(
            prepared.body_bytes,
            artifact_type="PreparedOutboundRequest",
            media_type="application/json",
            artifact_schema_id="tokenshare.prepared_outbound_request",
            artifact_schema_version="v1",
            source={"kind": "response_bank_acquisition"},
            metadata={"object_role": "request_body"},
            created_at="content-addressed",
            durability_hook=self.durability_hook,
        )
        self.budget_ledger.reserve_reacquisition(
            self._reservation_request(request),
            reacquisition_id=reacquisition_id,
            linked_ambiguous_attempt_id=linked,
            paid_scope_digest=paid_scope_digest,
        )
        self.budget_ledger.mark_reacquisition_dispatch_intent(reacquisition_id)
        api_key = self.secret_resolver(request.api_key_env)
        if not isinstance(api_key, str) or not api_key:
            raise AcquisitionAuthorizationError("resolved acquisition secret is missing")
        evidence = dispatch_prepared_request_once(
            prepared_request=prepared,
            provider_family=request.provider_family,
            transport=self.transport,
            api_key=api_key,
            timeout_seconds=request.timeout_seconds,
        )
        del api_key
        lifecycle = [
            *self._base_lifecycle,
            "prepared",
            "consistency_validated",
            "admitted",
            "prepared_artifact_committed",
            "reserved",
            "dispatch_intent",
            "secret_resolved",
            "transport_sent",
        ]
        return self._publish_evidence(
            request=request,
            row=row,
            prepared=prepared,
            request_ref=request_ref,
            evidence=evidence,
            lifecycle=lifecycle,
            attempt_id=reacquisition_id,
            linked_ambiguous_attempt_id=linked,
            reacquisition_id=reacquisition_id,
        )

    def _publish_evidence(
        self,
        *,
        request: AcquisitionRequest,
        row: ResponseBankInventoryRow,
        prepared: PreparedOutboundRequest,
        request_ref: Any,
        evidence: PreparedDispatchEvidence,
        lifecycle: list[str],
        attempt_id: str,
        linked_ambiguous_attempt_id: str | None,
        reacquisition_id: str | None,
    ) -> AcquisitionResult:
        role_refs: dict[str, Any] = {"request_body": request_ref}
        if evidence.terminal_kind == "success":
            role_refs["raw_output"] = self._save_role(
                "raw_output",
                {
                    "schema_version": "tokenshare.response_bank_raw_output.v1",
                    "raw_response_json": evidence.raw_response_json,
                    "content_text": evidence.content_text,
                    "reasoning_content": evidence.reasoning_content,
                    "provider_response_id": evidence.provider_response_id,
                    "finish_reason": evidence.finish_reason,
                },
                request.requested_at,
            )
        else:
            role_refs["provider_failure"] = self._save_role(
                "provider_failure",
                {
                    "schema_version": "tokenshare.response_bank_provider_failure.v1",
                    "failure_kind": evidence.failure_kind,
                    "http_status": evidence.http_status,
                    "message": evidence.error_message,
                    "raw_response_json": None,
                },
                request.requested_at,
            )
        usage_status = "reported" if evidence.usage is not None else "usage_missing"
        provenance = {
                "schema_version": "tokenshare.response_bank_provenance.v1",
                "provider_family": request.provider_family,
                "entry_id": prepared.entry_id,
                "provider_config_digest": prepared.provider_config_digest,
                "inference_request_digest": prepared.inference_request_digest,
                "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
                "transport_call_count": 1,
                "secret_persisted": False,
        }
        if self.paid_authorization is not None:
            provenance["receipt_digest"] = (
                self.paid_authorization.receipt.receipt_digest
            )
        else:
            provenance.update(
                {
                    "authorization_kind": self.authorization_kind,
                    "authorization_digest": self.authorization_digest,
                }
            )
        role_bodies = {
            "provenance": provenance,
            "usage": {
                "schema_version": "tokenshare.response_bank_usage.v1",
                "usage_status": usage_status,
                "usage": evidence.usage,
            },
            "latency": {
                "schema_version": "tokenshare.response_bank_latency.v1",
                "latency_ms": evidence.latency_ms,
                "timing_source": "provider_transport_observed",
            },
            "pricing": {
                "schema_version": "tokenshare.response_bank_pricing.v1",
                "currency": request.frozen_pricing.currency,
                "input_per_million_tokens": str(
                    request.frozen_pricing.input_per_million_tokens
                ),
                "output_per_million_tokens": str(
                    request.frozen_pricing.output_per_million_tokens
                ),
                "token_upper_bound": request.token_upper_bound,
                "cost_upper_bound": str(request.cost_upper_bound),
            },
            "acquisition_attempt": {
                "schema_version": "tokenshare.response_bank_acquisition_attempt.v1",
                "attempt_id": attempt_id,
                "linked_ambiguous_attempt_id": linked_ambiguous_attempt_id,
                "requested_at": request.requested_at,
                "terminal_kind": evidence.terminal_kind,
                "failure_kind": evidence.failure_kind,
            },
            "model_record": {
                "schema_version": "tokenshare.response_bank_model_record.v1",
                "configured_model": prepared.configured_model,
                "requested_model": str(prepared.body_obj.get("model")),
                "resolved_model": evidence.resolved_model,
                "response_model_status": evidence.response_model_status,
            },
        }
        for role, body in role_bodies.items():
            role_refs[role] = self._save_role(role, body, request.requested_at)
        lifecycle.append("terminal_objects_committed")
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=self.bank_root_id,
                manifest_digest=self.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=role_refs[role].content_hash,
            )
            for role in sorted(role_refs)
        )
        entry = ResponseBankEntry(
            inventory_digest=self.inventory_digest,
            inventory_entry_id=row.inventory_entry_id,
            semantic_slot_key=row.semantic_slot_key,
            inference_request_digest=row.inference_request_digest,
            entry_id=row.entry_id,
            sample_slot_index=row.sample_slot_index,
            replacement_slot=row.replacement_slot,
            terminal_kind=evidence.terminal_kind,
            object_locators=locators,
            acquisition_state_ref=attempt_id,
        )
        with self._artifact_write_lock:
            entry_ref = self._entry_store.save_json(
                entry.to_dict(),
                artifact_id=self._entry_artifact_id(row.inventory_entry_id),
                artifact_type="ResponseBankEntry",
                artifact_schema_id="tokenshare.response_bank_entry",
                artifact_schema_version="v1",
                source={"kind": "response_bank_acquisition"},
                metadata={"inventory_entry_id": row.inventory_entry_id},
                created_at=request.requested_at,
            )
        lifecycle.append("terminal_entry_committed")
        self._crash("terminal_entry_committed")
        if reacquisition_id is None:
            self.budget_ledger.publish_terminal(
                self.inventory_digest,
                row.inventory_entry_id,
                terminal_ref=entry_ref.content_hash,
                terminal_kind=evidence.terminal_kind,
            )
        else:
            self.budget_ledger.publish_reacquisition_terminal(
                reacquisition_id,
                terminal_ref=entry_ref.content_hash,
                terminal_kind=evidence.terminal_kind,
            )
        lifecycle.append("terminal_published")
        self._crash("terminal_published")
        usage = _provider_usage(evidence.usage)
        if reacquisition_id is None:
            self.budget_ledger.reconcile_terminal(
                self.inventory_digest, row.inventory_entry_id, usage=usage
            )
        else:
            self.budget_ledger.reconcile_reacquisition(
                reacquisition_id, usage=usage
            )
        lifecycle.append("settled")
        return AcquisitionResult(
            status="settled",
            entry=entry,
            lifecycle_order=tuple(lifecycle),
            transport_invoked=True,
            failure_kind=evidence.failure_kind,
            attempt_id=attempt_id,
            linked_ambiguous_attempt_id=linked_ambiguous_attempt_id,
        )

    def acquire_all(
        self,
        requests: Sequence[AcquisitionRequest],
        *,
        max_in_flight: int = 1,
    ) -> AcquisitionBatchResult:
        if (
            isinstance(max_in_flight, bool)
            or not isinstance(max_in_flight, int)
            or not 1 <= max_in_flight <= 10
        ):
            raise ValueError("max_in_flight must be an integer between 1 and 10")
        initial_reconcile = self.reconcile()
        # 本次 resume 只关闭 pre-dispatch crash；不能在同一 invocation 立刻重发。
        skip_after_safe_release = set(initial_reconcile.reconciled_pre_dispatch)
        pending = tuple(
            (index, request)
            for index, request in enumerate(requests)
            if request.inventory_row.inventory_entry_id
            not in skip_after_safe_release
        )
        blocked_reason: str | None = None
        if max_in_flight == 1:
            # paid/default 路径保持原有同线程、逐请求行为。
            serial_results: list[AcquisitionResult] = []
            for _, request in pending:
                try:
                    serial_results.append(self.acquire(request))
                except (BudgetExceededError, AcquisitionAuthorizationError) as exc:
                    blocked_reason = str(exc)
                    break
            results = tuple(serial_results)
        else:
            results_by_index: dict[int, AcquisitionResult] = {}
            unexpected_error: BaseException | None = None
            offset = 0
            with ThreadPoolExecutor(max_workers=max_in_flight) as executor:
                while offset < len(pending):
                    wave = pending[offset : offset + max_in_flight]
                    futures = {
                        executor.submit(self.acquire, request): index
                        for index, request in wave
                    }
                    # 一个 wave 全部归档后才补新请求，确保 provider failure 能阻止新提交。
                    wait(tuple(futures))
                    stop_submitting = False
                    for future, index in sorted(
                        futures.items(), key=lambda item: item[1]
                    ):
                        try:
                            result = future.result()
                        except (
                            BudgetExceededError,
                            AcquisitionAuthorizationError,
                        ) as exc:
                            if blocked_reason is None:
                                blocked_reason = str(exc)
                            stop_submitting = True
                        except BaseException as exc:
                            if unexpected_error is None:
                                unexpected_error = exc
                            stop_submitting = True
                        else:
                            results_by_index[index] = result
                            if result.failure_kind is not None or (
                                result.entry is not None
                                and result.entry.terminal_kind == "provider_failure"
                            ):
                                stop_submitting = True
                    offset += len(wave)
                    if stop_submitting:
                        break
            if unexpected_error is not None:
                raise unexpected_error
            results = tuple(
                results_by_index[index] for index in sorted(results_by_index)
            )
        report = self.reconcile()
        status = "complete"
        if blocked_reason is not None:
            status = "blocked"
        elif (
            report.missing_inventory_entry_ids
            or report.ambiguous_inventory_entry_ids
        ):
            status = "incomplete"
        return AcquisitionBatchResult(
            status=status,
            results=results,
            missing_inventory_entry_ids=report.missing_inventory_entry_ids,
            ambiguous_inventory_entry_ids=report.ambiguous_inventory_entry_ids,
            blocked_reason=blocked_reason,
        )

    def reconcile(self) -> AcquisitionReconcileReport:
        released: list[str] = []
        for record in self.budget_ledger.list_reservations():
            entry = self._load_entry(record.inventory_entry_id)
            if record.state == "reserved":
                if self.budget_ledger.release_reserved(
                    record.inventory_digest, record.inventory_entry_id
                ):
                    released.append(record.inventory_entry_id)
            elif record.state == "dispatch_intent":
                if entry is None:
                    self.budget_ledger.mark_ambiguous(
                        record.inventory_digest, record.inventory_entry_id
                    )
                else:
                    self._reconcile_committed_entry(entry)
            elif record.state in {"ambiguous", "terminal_published", "settled"}:
                if entry is not None:
                    self._reconcile_committed_entry(entry)
        for record in self.budget_ledger.list_reacquisitions():
            entry = self._load_entry(record.inventory_entry_id)
            if record.state == "dispatch_intent" and entry is None:
                self.budget_ledger.mark_reacquisition_ambiguous(
                    record.reacquisition_id
                )
            elif entry is not None and record.state in {
                "dispatch_intent",
                "ambiguous",
                "terminal_published",
                "settled",
            }:
                self._reconcile_committed_entry(entry)
        current = self.budget_ledger.list_reservations()
        ambiguous = tuple(
            record.inventory_entry_id
            for record in current
            if record.state == "ambiguous"
        )
        settled_ids = {
            record.inventory_entry_id
            for record in current
            if record.state == "settled"
        }
        settled_ids.update(
            record.inventory_entry_id
            for record in self.budget_ledger.list_reacquisitions()
            if record.state == "settled"
        )
        missing = tuple(
            row.inventory_entry_id
            for row in self.inventory_rows
            if self._load_entry(row.inventory_entry_id) is None
        )
        return AcquisitionReconcileReport(
            reconciled_pre_dispatch=tuple(released),
            ambiguous_inventory_entry_ids=ambiguous,
            settled_inventory_entry_ids=tuple(
                row.inventory_entry_id
                for row in self.inventory_rows
                if row.inventory_entry_id in settled_ids
            ),
            missing_inventory_entry_ids=missing,
        )

    def _reconcile_committed_entry(self, entry: ResponseBankEntry) -> None:
        usage = _provider_usage(self.read_role_json(entry, "usage").get("usage"))
        reacquisitions = {
            record.reacquisition_id: record
            for record in self.budget_ledger.list_reacquisitions()
        }
        if entry.acquisition_state_ref in reacquisitions:
            record = reacquisitions[entry.acquisition_state_ref]
            entry_ref = self._entry_store.load_artifact_ref(
                self._entry_artifact_id(entry.inventory_entry_id)
            )
            if record.state in {"dispatch_intent", "ambiguous"}:
                self.budget_ledger.publish_reacquisition_terminal(
                    record.reacquisition_id,
                    terminal_ref=entry_ref.content_hash,
                    terminal_kind=entry.terminal_kind,
                )
            self.budget_ledger.reconcile_reacquisition(
                record.reacquisition_id, usage=usage
            )
            return
        record = self.budget_ledger.get_reservation(
            entry.inventory_digest, entry.inventory_entry_id
        )
        entry_ref = self._entry_store.load_artifact_ref(
            self._entry_artifact_id(entry.inventory_entry_id)
        )
        if record.state in {"dispatch_intent", "ambiguous"}:
            self.budget_ledger.publish_terminal(
                entry.inventory_digest,
                entry.inventory_entry_id,
                terminal_ref=entry_ref.content_hash,
                terminal_kind=entry.terminal_kind,
            )
        self.budget_ledger.reconcile_terminal(
            entry.inventory_digest, entry.inventory_entry_id, usage=usage
        )

    def _validate_acquisition_request(
        self, request: AcquisitionRequest
    ) -> tuple[ResponseBankInventoryRow, PreparedOutboundRequest]:
        row = ResponseBankInventoryRow.from_dict(request.inventory_row.to_dict())
        registered = self._rows_by_id.get(row.inventory_entry_id)
        if registered is None or registered != row:
            raise AcquisitionIdentityError("acquisition row is not preregistered")
        prepared = validate_prepared_request(request.prepared_request)
        expected = {
            "body_digest": prepared.body_digest,
            "inference_request_digest": prepared.inference_request_digest,
            "provider_config_digest": prepared.provider_config_digest,
            "prompt_admission_profile_digest": (
                prepared.prompt_admission_profile_digest
            ),
            "planned_ai_unit_id": prepared.planned_ai_unit_id,
            "sample_slot_index": prepared.sample_slot_index,
            "replacement_slot": prepared.replacement_slot,
            "plugin_version": prepared.plugin_version,
        }
        for field_name, value in expected.items():
            if getattr(row, field_name) != value:
                raise AcquisitionIdentityError(
                    f"prepared request {field_name} does not match inventory row"
                )
        if row.entry_id != terminal_bank_entry_id(
            semantic_slot_key=row.semantic_slot_key,
            inference_request_digest=prepared.inference_request_digest,
        ):
            raise AcquisitionIdentityError(
                "terminal bank entry identity does not match inventory row"
            )
        return row, prepared

    def _reservation_request(self, request: AcquisitionRequest) -> ReservationRequest:
        row = request.inventory_row
        return ReservationRequest(
            inventory_digest=self.inventory_digest,
            inventory_entry_id=row.inventory_entry_id,
            semantic_slot_key=row.semantic_slot_key,
            inference_request_digest=row.inference_request_digest,
            prompt_admission_profile_digest=row.prompt_admission_profile_digest,
            token_upper_bound=request.token_upper_bound,
            cost_upper_bound=request.cost_upper_bound,
            provider_family=request.provider_family,
            frozen_pricing=request.frozen_pricing,
        )

    def _save_role(self, role: str, body: Mapping[str, Any], created_at: str):
        encoded = json.dumps(
            dict(body), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with self._artifact_write_lock:
            return self._object_store.save_content_addressed_bytes(
                encoded,
                artifact_type=f"ResponseBank{role.title().replace('_', '')}",
                media_type="application/json",
                artifact_schema_id=f"tokenshare.response_bank_{role}",
                artifact_schema_version="v1",
                source={"kind": "response_bank_acquisition"},
                metadata={"object_role": role},
                created_at="content-addressed",
            )

    def _load_entry(self, inventory_entry_id: str) -> ResponseBankEntry | None:
        artifact_id = self._entry_artifact_id(inventory_entry_id)
        try:
            ref = self._entry_store.load_artifact_ref(artifact_id)
            body = json.loads(self._entry_store.read_bytes(ref).decode("utf-8"))
            entry = ResponseBankEntry.from_dict(body)
        except FileNotFoundError:
            return None
        if entry.inventory_entry_id != inventory_entry_id:
            raise AcquisitionIdentityError("terminal entry inventory identity mismatch")
        return entry

    @staticmethod
    def _entry_artifact_id(inventory_entry_id: str) -> str:
        return f"{inventory_entry_id.removeprefix('sha256:')}.v1.json"

    def read_role_json(self, entry: ResponseBankEntry, role: str) -> dict[str, Any]:
        return json.loads(self.read_role_bytes(entry, role).decode("utf-8"))

    def read_role_bytes(self, entry: ResponseBankEntry, role: str) -> bytes:
        locator = next(
            (item for item in entry.object_locators if item.object_role == role),
            None,
        )
        if locator is None:
            raise KeyError(f"entry role not found: {role}")
        ref = self._object_store.load_artifact_ref(
            locator.object_digest.removeprefix("sha256:")
        )
        if ref.content_hash != locator.object_digest:
            raise AcquisitionIdentityError("entry locator digest mismatch")
        return self._object_store.read_bytes(ref)

    def terminal_entries(self) -> tuple[ResponseBankEntry, ...]:
        entries = tuple(
            self._load_entry(row.inventory_entry_id) for row in self.inventory_rows
        )
        if any(entry is None for entry in entries):
            raise AcquisitionIdentityError("terminal entries are incomplete")
        return tuple(entry for entry in entries if entry is not None)

    def _primary_attempt_id(self, row: ResponseBankInventoryRow) -> str:
        return canonical_digest(
            {
                "inventory_digest": self.inventory_digest,
                "inventory_entry_id": row.inventory_entry_id,
                "attempt_kind": "primary",
            }
        )

    def _require_unexpired(self) -> None:
        authorization = self.paid_authorization or self.facility_authorization
        assert authorization is not None
        if self._is_expired() or not authorization.provider_dispatch_allowed:
            raise AcquisitionAuthorizationError(
                "expired paid context cannot reserve or dispatch"
                if self.paid_authorization is not None
                else "results-first facility cannot reserve or dispatch"
            )

    def _is_expired(self) -> bool:
        if self.paid_authorization is None:
            return False
        expires_at = datetime.fromisoformat(
            self.paid_authorization.receipt.expires_at.replace("Z", "+00:00")
        )
        return self.now_epoch >= int(expires_at.timestamp())

    def _crash(self, stage: str) -> None:
        if self.crash_hook is not None:
            self.crash_hook(stage)


def _provider_usage(value: Any) -> ProviderUsage | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = value.get("prompt_tokens")
    output_tokens = value.get("completion_tokens")
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        return None
    return ProviderUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def replacement_slots_for(
    *, experiment_id: str, fault_type: str, ablation_mode: str
) -> tuple[int, ...]:
    """返回冻结的 acquisition replacement 深度（含初始 slot 0）。"""

    if experiment_id == "exp3_real_ai_fault_recovery":
        return tuple(range(5 if fault_type == "worker_death" else 3))
    if experiment_id == "exp4_real_ai_protocol_ablation":
        if ablation_mode not in _EXP4_MODES:
            raise ValueError("Experiment 4 ablation mode drift")
        return (0, 1)
    return (0,)


def regression_smoke_sparse_replacement_slots(
    *,
    experiment_id: str,
    fault_type: str,
    fault_rate: float,
    ablation_mode: str,
    planned_ai_unit_ids: Sequence[str],
    dead_worker_count: int | None = None,
    kill_progress_percent: int | None = None,
) -> dict[str, tuple[int, ...]]:
    """按正式 target policy 为 regression smoke 只冻结可能消费的 replacement。"""

    unit_ids = tuple(str(unit_id) for unit_id in planned_ai_unit_ids)
    if (
        not unit_ids
        or len(set(unit_ids)) != len(unit_ids)
        or any(not unit_id for unit_id in unit_ids)
    ):
        raise ValueError("regression smoke planned AI units must be non-empty and unique")
    slots = {unit_id: (0,) for unit_id in unit_ids}
    if experiment_id == "exp3_real_ai_fault_recovery":
        if fault_type == "worker_death":
            if dead_worker_count is None or kill_progress_percent is None:
                raise ValueError("worker death sparse policy requires frozen targets")
            targets = _worker_death_targets(
                unit_ids,
                dead_worker_count=dead_worker_count,
                kill_progress_percent=kill_progress_percent,
            )
        else:
            targets = select_fault_targets(
                unit_ids,
                fault_rate=fault_rate,
                seed=RATE_FAULT_TARGET_SEED,
            )
            if not targets:
                raise ValueError("rate-fault smoke must freeze at least one target")
        replacement_slots = replacement_slots_for(
            experiment_id=experiment_id,
            fault_type=fault_type,
            ablation_mode=ablation_mode,
        )
        for unit_id in targets:
            slots[unit_id] = replacement_slots
        return slots
    if experiment_id == "exp4_real_ai_protocol_ablation":
        if ablation_mode not in _EXP4_MODES:
            raise ValueError("Experiment 4 ablation mode drift")
        if ablation_mode != "NO_REQUEUE":
            replacement_slots = replacement_slots_for(
                experiment_id=experiment_id,
                fault_type=fault_type,
                ablation_mode=ablation_mode,
            )
            return {unit_id: replacement_slots for unit_id in unit_ids}
    return slots


def build_semantic_inventory(
    candidates: Sequence[SemanticSlotCandidate],
    *,
    exp2_online_conditions: Sequence[Mapping[str, Any] | Any] = (),
    max_concurrent_roots: int = 1,
    observed_early_stop_slot_keys: Sequence[str] = (),
) -> SemanticInventoryPlan:
    """仅规划并校验完整 inventory；不创建协议对象或发起 provider 调用。"""

    if max_concurrent_roots != 1:
        raise ValueError("max_concurrent_roots must remain 1")
    _validate_replacement_completeness(candidates)
    online_refs = _exp2_online_refs(exp2_online_conditions)
    rows_by_slot: dict[str, ResponseBankInventoryRow] = {}
    request_identity_by_slot: dict[str, tuple[str, str]] = {}
    terminal_kind_by_slot: dict[str, str | None] = {}
    sample_by_repeat: dict[tuple[Any, ...], int] = {}
    repeat_by_sample: dict[tuple[Any, ...], int] = {}
    condition_refs: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        prepared = validate_prepared_request(candidate.prepared_request)
        _validate_candidate_matches_prepared(candidate, prepared)
        repeat_context = _repeat_sample_context(candidate, prepared)
        repeat_key = (*repeat_context, candidate.repeat_id)
        if (
            repeat_key in sample_by_repeat
            and sample_by_repeat[repeat_key] != candidate.sample_slot_index
        ):
            raise ValueError("same repeat cannot map to multiple sample slots")
        sample_by_repeat[repeat_key] = candidate.sample_slot_index
        sample_key = (*repeat_context, candidate.sample_slot_index)
        if (
            sample_key in repeat_by_sample
            and repeat_by_sample[sample_key] != candidate.repeat_id
        ):
            raise ValueError("different repeats cannot share the same sample slot")
        repeat_by_sample[sample_key] = candidate.repeat_id
        slot_key = semantic_slot_key(
            case_record_digest=candidate.case_record_digest,
            planned_ai_unit_id=candidate.planned_ai_unit_id,
            sample_slot_index=candidate.sample_slot_index,
            replacement_slot=candidate.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest=candidate.prompt_profile_digest,
            prompt_admission_profile_digest=(
                prepared.prompt_admission_profile_digest
            ),
            plugin_version=prepared.plugin_version,
        )
        request_identity = (prepared.body_digest, prepared.inference_request_digest)
        previous_identity = request_identity_by_slot.setdefault(
            slot_key, request_identity
        )
        if previous_identity != request_identity:
            raise ValueError(
                "one semantic slot cannot preregister two inference/body digests"
            )

        provisional = ResponseBankInventoryRow(
            inventory_entry_id="",
            semantic_slot_key=slot_key,
            case_record_digest=candidate.case_record_digest,
            planned_ai_unit_id=candidate.planned_ai_unit_id,
            sample_slot_index=candidate.sample_slot_index,
            replacement_slot=candidate.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest=candidate.prompt_profile_digest,
            prompt_admission_profile_digest=(
                prepared.prompt_admission_profile_digest
            ),
            plugin_version=prepared.plugin_version,
            entry_id=terminal_bank_entry_id(
                semantic_slot_key=slot_key,
                inference_request_digest=prepared.inference_request_digest,
            ),
            body_digest=prepared.body_digest,
            inference_request_digest=prepared.inference_request_digest,
        )
        computed_id = inventory_entry_id(provisional)
        if (
            candidate.precomputed_inventory_entry_id is not None
            and candidate.precomputed_inventory_entry_id != computed_id
        ):
            raise ValueError("precomputed inventory_entry_id mismatch")
        row = ResponseBankInventoryRow.from_dict(
            replace(provisional, inventory_entry_id=computed_id).to_dict()
        )
        previous_row = rows_by_slot.setdefault(slot_key, row)
        if previous_row != row:
            raise ValueError("one semantic slot maps to non-identical inventory rows")
        if (
            slot_key in terminal_kind_by_slot
            and terminal_kind_by_slot[slot_key] != candidate.terminal_kind
        ):
            raise ValueError("conflicting terminal_kind for semantic slot")
        terminal_kind_by_slot[slot_key] = candidate.terminal_kind
        _append_condition_ref(condition_refs, candidate, slot_key)

    rows = canonical_inventory_rows(tuple(rows_by_slot.values()))
    terminal_values = tuple(terminal_kind_by_slot[row.semantic_slot_key] for row in rows)
    return SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=response_bank_inventory_digest(rows),
        rows=rows,
        condition_refs=tuple(condition_refs.values()),
        exp2_online_condition_refs=online_refs,
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=terminal_values.count("provider_failure"),
        terminal_success_count=terminal_values.count("success"),
        terminal_unacquired_count=terminal_values.count(None),
        # early-stop 是消费期观测，只记录而绝不参与 expected inventory 裁剪。
        observed_early_stop_slot_keys=tuple(observed_early_stop_slot_keys),
    )


def build_matrix8_unified_acquisition_plan(
    *,
    execution_plans: Sequence[Any],
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_config: AIAPIExecutorConfig,
    entry_id: str,
    planning_artifact_root: str | Path,
    requested_at: str,
    token_upper_bound: int,
    cost_upper_bound: Decimal,
    frozen_pricing: FrozenPricing,
) -> Matrix8UnifiedAcquisitionPlan:
    """从四份 canonical matrix8 execution plan 冻结 Exp1 统一采集计划。"""

    expected_experiments = (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    )
    plans = tuple(execution_plans)
    if tuple(
        tuple(getattr(plan, "experiment_ids", ())) for plan in plans
    ) != tuple((experiment_id,) for experiment_id in expected_experiments):
        raise ValueError("unified acquisition requires ordered Exp1--4 matrix8 plans")
    if any(
        getattr(plan, "schema_version", None)
        != "tokenshare.paper_smoke_execution_plan.v1"
        or len(tuple(getattr(plan, "items", ()))) != 8
        or getattr(plan, "catalog_id", None) != catalog_manifest.catalog_id
        or getattr(plan, "catalog_version", None) != catalog_manifest.catalog_version
        or getattr(plan, "catalog_digest", None) != catalog_manifest.catalog_digest
        for plan in plans
    ):
        raise ValueError("unified acquisition requires canonical matrix8 smoke plans")
    root_case_ids = tuple(item.case_id for item in plans[0].items)
    if len(set(root_case_ids)) != 8 or any(
        {item.case_id for item in plan.items} != set(root_case_ids)
        for plan in plans[1:]
    ):
        raise ValueError("unified acquisition matrix8 root case set drift")
    if not isinstance(ai_api_config, AIAPIExecutorConfig):
        raise TypeError("ai_api_config must be AIAPIExecutorConfig")
    entries = tuple(
        entry
        for entry in ai_api_config.entries
        if entry.entry_id == entry_id and entry.enabled
    )
    if (
        ai_api_config.provider_family != "deepseek"
        or entry_id != "deepseek_v4_pro_exp1_baseline"
        or len(entries) != 1
        or entries[0].model != "deepseek-v4-pro"
        or int(ai_api_config.defaults.get("timeout_seconds", 0)) != 600
        or int(ai_api_config.defaults.get("max_tokens", 0)) != 300000
        or entries[0].request_overrides.get("reasoning_effort") != "high"
        or entries[0].request_overrides.get("thinking") != {"type": "enabled"}
    ):
        raise ValueError("unified acquisition provider identity drift")
    entry = entries[0]
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    try:
        root_cases = {case_id: cases_by_id[case_id] for case_id in root_case_ids}
    except KeyError as exc:
        raise ValueError("unified acquisition root is absent from catalog") from exc
    factor_case_ids = {
        str(case["case_id"]) for case in catalog_manifest.factorization_cases
    }
    lean_case_ids = {
        str(case["case_id"])
        for case in (
            catalog_manifest.lean_cases + catalog_manifest.lean_lemma_graph_cases
        )
    }
    if len(set(root_case_ids) & factor_case_ids) != 4:
        raise ValueError("unified acquisition requires four Factor roots")
    if len(set(root_case_ids) & lean_case_ids) != 4:
        raise ValueError("unified acquisition requires four Lean roots")

    protocol_config = replace(
        ProtocolConfig.default(
            config_id="matrix8_unified_acquisition_planning",
            artifact_store_uri="file://planning-artifacts",
            event_log_uri="file://planning-events.jsonl",
        ),
        max_children_per_unit=64,
    )
    artifact_root = Path(planning_artifact_root).resolve()
    candidates: list[SemanticSlotCandidate] = []
    case_mappings: dict[tuple[str, str, str], dict[str, str]] = {}
    for execution_plan in plans:
        if len(tuple(execution_plan.dispatch_plans)) != 1:
            raise ValueError("matrix8 execution plan must have one dispatch plan")
        dispatch_plan = execution_plan.dispatch_plans[0]
        if (
            dispatch_plan.experiment_id != execution_plan.experiment_ids[0]
            or dispatch_plan.paper_eligible_possible
            or dispatch_plan.provider_calls_made != 0
        ):
            raise ValueError("matrix8 dispatch plan classification drift")
        for item in execution_plan.items:
            condition, selection = dispatch_plan.bound_condition(item.condition_id)
            if (
                condition.condition_digest != item.condition_digest
                or selection.selection_id != item.selection_id
                or selection.selection_digest != item.selection_digest
                or item.case_id not in selection.ordered_case_ids
            ):
                raise ValueError("matrix8 condition/selection identity drift")
            root_case = root_cases[item.case_id]
            planned_case = _case_with_acquisition_split_profile(
                root_case,
                selection=selection,
            )
            store = ArtifactStore(
                artifact_root
                / _safe_planning_part(condition.condition_id)
                / _safe_planning_part(item.case_id)
            )
            adapter: FactorizationRuntimeAdapter | LeanRuntimeAdapter
            if item.case_id in factor_case_ids:
                adapter = FactorizationRuntimeAdapter(
                    provider_family=ai_api_config.provider_family,
                    seed=condition.seed,
                    protocol_config=protocol_config,
                    max_tokens=int(ai_api_config.defaults["max_tokens"]),
                    timeout_seconds=int(ai_api_config.defaults["timeout_seconds"]),
                )
            else:
                adapter = LeanRuntimeAdapter(
                    provider_family=ai_api_config.provider_family,
                    environment_manifest=default_lean_paper_environment_manifest(),
                    seed=condition.seed,
                    protocol_config=protocol_config,
                    max_tokens=int(ai_api_config.defaults["max_tokens"]),
                    timeout_seconds=int(ai_api_config.defaults["timeout_seconds"]),
                )
            units = adapter.plan_units(planned_case, artifact_store=store)
            ai_units = tuple(
                (unit, planned_ai_unit_id)
                for unit in units
                if (
                    planned_ai_unit_id := adapter.planned_ai_unit_id(unit)
                ) is not None
            )
            if not ai_units:
                raise ValueError("matrix8 root has no planned AI units")
            selector = dict(item.condition_selector)
            slot_policy_id = (
                "regression_smoke_sparse_replacements.v1"
                if item.experiment_id
                in {
                    "exp3_real_ai_fault_recovery",
                    "exp4_real_ai_protocol_ablation",
                }
                else "formal_complete"
            )
            slots_by_unit = regression_smoke_sparse_replacement_slots(
                experiment_id=item.experiment_id,
                fault_type=str(condition.fault_type),
                fault_rate=float(condition.fault_rate),
                ablation_mode=str(condition.ablation_mode),
                planned_ai_unit_ids=tuple(unit_id for _, unit_id in ai_units),
                dead_worker_count=_optional_selector_int(
                    selector, "dead_worker_count"
                ),
                kill_progress_percent=_optional_selector_int(
                    selector, "kill_progress_percent"
                ),
            )
            for unit, planned_ai_unit_id in ai_units:
                attempt, lease = _planning_attempt_and_lease(
                    case_id=item.case_id,
                    unit_id=unit.unit_id,
                    task_id=unit.task_id,
                )
                if isinstance(adapter, LeanRuntimeAdapter):
                    base_request = adapter.build_planning_execution_request(
                        unit,
                        attempt=attempt,
                        lease=lease,
                    )
                else:
                    base_request = adapter.build_execution_request(
                        unit,
                        attempt=attempt,
                        lease=lease,
                    )
                if base_request.prompt_package_ref is None:
                    raise ValueError("planned AI request is missing prompt package")
                prompt = json.loads(store.read_bytes(base_request.prompt_package_ref))
                case_record_digest = canonical_digest(root_case)
                for replacement_slot in slots_by_unit[planned_ai_unit_id]:
                    request = replace(
                        base_request,
                        soft_hints={
                            **dict(base_request.soft_hints or {}),
                            "paper_condition_id": condition.condition_id,
                            "sample_slot_index": item.repeat_id,
                            "replacement_slot": replacement_slot,
                        },
                    )
                    prepared = prepare_ai_api_outbound_request(
                        config=ai_api_config,
                        request=request,
                        prompt=prompt,
                        entry=entry,
                    ).prepared_request
                    if prepared.planned_ai_unit_id != planned_ai_unit_id:
                        raise ValueError("planned AI unit identity drift")
                    mapping_key = (
                        item.case_id,
                        prepared.case_id,
                        prepared.plugin_id,
                    )
                    case_mappings[mapping_key] = {
                        "root_case_id": item.case_id,
                        "prepared_stable_case_id": prepared.case_id,
                        "plugin_id": prepared.plugin_id,
                    }
                    candidates.append(
                        SemanticSlotCandidate(
                            experiment_id=item.experiment_id,
                            condition_id=condition.condition_id,
                            condition_digest=condition.condition_digest,
                            worker_count=condition.worker_count,
                            repeat_id=item.repeat_id,
                            fault_type=str(condition.fault_type),
                            ablation_mode=str(condition.ablation_mode),
                            case_id=item.case_id,
                            case_record_digest=case_record_digest,
                            planned_ai_unit_id=planned_ai_unit_id,
                            sample_slot_index=item.repeat_id,
                            replacement_slot=replacement_slot,
                            prompt_profile_digest=canonical_digest(
                                {
                                    "body_digest": prepared.body_digest,
                                    "prompt_profile_id": prepared.prompt_profile_id,
                                    "prompt_serialization_schema": (
                                        prepared.prompt_serialization_schema
                                    ),
                                }
                            ),
                            prepared_request=prepared,
                            prepared_stable_case_id=prepared.case_id,
                            replacement_policy_id=slot_policy_id,
                            fault_rate=float(condition.fault_rate),
                            dead_worker_count=_optional_selector_int(
                                selector, "dead_worker_count"
                            ),
                            kill_progress_percent=_optional_selector_int(
                                selector, "kill_progress_percent"
                            ),
                        )
                    )
    semantic_plan = build_semantic_inventory(tuple(candidates))
    prepared_by_inference: dict[str, PreparedOutboundRequest] = {}
    for candidate in candidates:
        prepared = candidate.prepared_request
        previous = prepared_by_inference.setdefault(
            prepared.inference_request_digest,
            prepared,
        )
        if previous != prepared:
            raise ValueError("one inference digest maps to multiple requests")
    requests = tuple(
        AcquisitionRequest(
            inventory_row=row,
            prepared_request=prepared_by_inference[row.inference_request_digest],
            provider_family=ai_api_config.provider_family,
            api_key_env=entry.api_key_env,
            timeout_seconds=int(ai_api_config.defaults["timeout_seconds"]),
            token_upper_bound=token_upper_bound,
            cost_upper_bound=cost_upper_bound,
            frozen_pricing=frozen_pricing,
            requested_at=requested_at,
        )
        for row in semantic_plan.rows
    )
    return Matrix8UnifiedAcquisitionPlan(
        combined_profile_digest=canonical_digest(
            tuple(plan.profile_digest for plan in plans)
        ),
        condition_candidate_count=len(candidates),
        candidates=tuple(candidates),
        semantic_inventory_plan=semantic_plan,
        acquisition_requests=requests,
        case_identity_mappings=tuple(
            case_mappings[key] for key in sorted(case_mappings)
        ),
    )


def _case_with_acquisition_split_profile(
    case: Mapping[str, Any],
    *,
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


def _planning_attempt_and_lease(
    *,
    case_id: str,
    unit_id: str,
    task_id: str,
) -> tuple[Attempt, Lease]:
    identity = canonical_digest(
        {
            "case_id": case_id,
            "unit_id": unit_id,
            "attempt_ordinal": 0,
        }
    ).removeprefix("sha256:")
    attempt_id = f"matrix8_acquisition_{identity}"
    lease_id = f"lease_{identity}"
    attempt = Attempt(
        attempt_id=attempt_id,
        task_id=task_id,
        unit_id=unit_id,
        lease_id=lease_id,
        client_id="matrix8_acquisition_planner",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at="2026-07-14T00:00:00Z",
        started_at="2026-07-14T00:00:00Z",
    )
    return attempt, Lease(
        lease_id=lease_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        client_id=attempt.client_id,
        state=LeaseState.ACTIVE,
        fencing_token=f"fence_{identity}",
        issued_at="2026-07-14T00:00:00Z",
        expires_at="2026-07-14T00:10:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="execution",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )


def _optional_selector_int(selector: Mapping[str, Any], field_name: str) -> int | None:
    value = selector.get(field_name)
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"matrix8 selector {field_name} must be an integer")
    return value


def _safe_planning_part(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value
    )


def preflight_inventory_before_coordinator(
    *,
    plan: SemanticInventoryPlan,
    available_inventory_entry_ids: Sequence[str],
    coordinator_factory: Callable[[], Any],
) -> InventoryPreflightResult:
    """在 coordinator 构造前逐 slot 检查 inventory 完整性。"""

    completeness = preflight_formal_trace_inventory(
        required_inventory_entry_ids=tuple(
            row.inventory_entry_id for row in plan.rows
        ),
        available_inventory_entry_ids=available_inventory_entry_ids,
    )
    available = set(completeness.available_inventory_entry_ids)
    missing_rows = tuple(
        row for row in plan.rows if row.inventory_entry_id not in available
    )
    if completeness.status == "blocked":
        records = tuple(
            ResponseBankPreflightBlockedRecord(
                schema_version="tokenshare.response_bank_preflight_blocked.v1",
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                reason="required semantic slot is missing",
            )
            for row in missing_rows
        )
        return InventoryPreflightResult(
            schema_version="tokenshare.response_bank_inventory_preflight.v1",
            status="blocked",
            blocked_records=records,
            protocol_engine_event_count=0,
            provider_call_count=0,
            coordinator_constructed=False,
        )
    coordinator = coordinator_factory()
    return InventoryPreflightResult(
        schema_version="tokenshare.response_bank_inventory_preflight.v1",
        status="ready",
        blocked_records=(),
        protocol_engine_event_count=0,
        provider_call_count=0,
        coordinator_constructed=True,
        coordinator=coordinator,
    )


def _append_condition_ref(
    refs: dict[str, dict[str, Any]],
    candidate: SemanticSlotCandidate,
    slot_key: str,
) -> None:
    condition_ref: dict[str, Any] = {
        "condition_id": candidate.condition_id,
        "condition_digest": candidate.condition_digest,
        "experiment_id": candidate.experiment_id,
        "worker_count": candidate.worker_count,
        "repeat_id": candidate.repeat_id,
        "fault_type": candidate.fault_type,
        "ablation_mode": candidate.ablation_mode,
        "semantic_slot_keys": [],
        "case_refs": [],
    }
    if candidate.replacement_policy_id == (
        "regression_smoke_sparse_replacements.v1"
    ):
        condition_ref.update(
            {
                "replacement_policy_id": candidate.replacement_policy_id,
                "fault_rate": candidate.fault_rate,
                "dead_worker_count": candidate.dead_worker_count,
                "kill_progress_percent": candidate.kill_progress_percent,
            }
        )
    value = refs.setdefault(candidate.condition_id, condition_ref)
    identity_fields = set(condition_ref) - {"semantic_slot_keys", "case_refs"}
    if set(value) != set(condition_ref) or any(
        value.get(name) != condition_ref[name] for name in identity_fields
    ):
        raise ValueError("condition replacement policy drift")
    if slot_key not in value["semantic_slot_keys"]:
        value["semantic_slot_keys"].append(slot_key)
    matching_case_refs = [
        case_ref
        for case_ref in value["case_refs"]
        if case_ref["case_id"] == candidate.case_id
    ]
    if matching_case_refs:
        case_ref = matching_case_refs[0]
        if case_ref["case_record_digest"] != candidate.case_record_digest:
            raise ValueError("one formal case_id cannot map to multiple record digests")
    else:
        case_ref = {
            "case_id": candidate.case_id,
            "case_record_digest": candidate.case_record_digest,
            "semantic_slot_keys": [],
        }
        value["case_refs"].append(case_ref)
    if slot_key not in case_ref["semantic_slot_keys"]:
        case_ref["semantic_slot_keys"].append(slot_key)


def _validate_candidate_matches_prepared(
    candidate: SemanticSlotCandidate, prepared: PreparedOutboundRequest
) -> None:
    expected = (
        (
            "case_id",
            candidate.prepared_stable_case_id or candidate.case_id,
            prepared.case_id,
        ),
        ("planned_ai_unit_id", candidate.planned_ai_unit_id, prepared.planned_ai_unit_id),
        ("sample_slot_index", candidate.sample_slot_index, prepared.sample_slot_index),
        ("replacement_slot", candidate.replacement_slot, prepared.replacement_slot),
    )
    for field_name, candidate_value, prepared_value in expected:
        if candidate_value != prepared_value:
            raise ValueError(f"candidate {field_name} does not match prepared request")
    if candidate.terminal_kind not in {None, "success", "provider_failure"}:
        raise ValueError("unsupported terminal kind")


def _repeat_sample_context(
    candidate: SemanticSlotCandidate,
    prepared: PreparedOutboundRequest,
) -> tuple[Any, ...]:
    # repeat/sample 与 replacement 独立；映射必须跨所有 replacement slot 成立。
    return (
        candidate.case_record_digest,
        candidate.planned_ai_unit_id,
        prepared.provider_config_digest,
        candidate.prompt_profile_digest,
        prepared.prompt_admission_profile_digest,
        prepared.plugin_version,
    )


def _validate_replacement_completeness(
    candidates: Sequence[SemanticSlotCandidate],
) -> None:
    sparse_policy = "regression_smoke_sparse_replacements.v1"
    slots_by_unit: dict[tuple[str, str, str, int], set[int]] = {}
    policy_by_unit: dict[tuple[str, str, str, int], tuple[object, ...]] = {}
    sparse_groups: dict[tuple[str, str, int], list[SemanticSlotCandidate]] = {}
    for candidate in candidates:
        key = (
            candidate.condition_id,
            candidate.case_record_digest,
            candidate.planned_ai_unit_id,
            candidate.sample_slot_index,
        )
        slots_by_unit.setdefault(key, set()).add(candidate.replacement_slot)
        policy = (
            candidate.replacement_policy_id,
            candidate.experiment_id,
            candidate.fault_type,
            candidate.ablation_mode,
            candidate.fault_rate,
            candidate.dead_worker_count,
            candidate.kill_progress_percent,
        )
        previous = policy_by_unit.setdefault(key, policy)
        if previous != policy:
            raise ValueError("condition replacement policy drift")
        if candidate.replacement_policy_id == sparse_policy:
            group_key = (
                candidate.condition_id,
                candidate.case_record_digest,
                candidate.sample_slot_index,
            )
            sparse_groups.setdefault(group_key, []).append(candidate)
        elif candidate.replacement_policy_id != "formal_complete":
            raise ValueError("unsupported replacement completeness policy")
    for key, actual_slots in slots_by_unit.items():
        (
            replacement_policy_id,
            experiment_id,
            fault_type,
            ablation_mode,
            _fault_rate,
            _dead_worker_count,
            _kill_progress_percent,
        ) = policy_by_unit[key]
        if replacement_policy_id == sparse_policy:
            continue
        expected_slots = set(
            replacement_slots_for(
                experiment_id=str(experiment_id),
                fault_type=str(fault_type),
                ablation_mode=str(ablation_mode),
            )
        )
        if actual_slots != expected_slots:
            raise ValueError(
                "incomplete replacement slots for preregistered semantic unit"
            )
    for group_candidates in sparse_groups.values():
        representative = group_candidates[0]
        policy_values = {
            (
                candidate.experiment_id,
                candidate.fault_type,
                candidate.fault_rate,
                candidate.ablation_mode,
                candidate.dead_worker_count,
                candidate.kill_progress_percent,
            )
            for candidate in group_candidates
        }
        if len(policy_values) != 1:
            raise ValueError("condition replacement policy drift")
        unit_ids = tuple(
            dict.fromkeys(
                candidate.planned_ai_unit_id for candidate in group_candidates
            )
        )
        expected_by_unit = regression_smoke_sparse_replacement_slots(
            experiment_id=representative.experiment_id,
            fault_type=representative.fault_type,
            fault_rate=representative.fault_rate,
            ablation_mode=representative.ablation_mode,
            planned_ai_unit_ids=unit_ids,
            dead_worker_count=representative.dead_worker_count,
            kill_progress_percent=representative.kill_progress_percent,
        )
        for unit_id, expected_slots in expected_by_unit.items():
            key = (
                representative.condition_id,
                representative.case_record_digest,
                unit_id,
                representative.sample_slot_index,
            )
            if slots_by_unit.get(key) != set(expected_slots):
                raise ValueError(
                    "incomplete replacement slots for preregistered semantic unit"
                )


def _exp2_online_refs(
    conditions: Sequence[Mapping[str, Any] | Any],
) -> tuple[dict[str, Any], ...]:
    if not conditions:
        return ()
    refs = tuple(_object_dict(item) for item in conditions)
    expected_axes = tuple(
        (worker, position, case_id, selection_index)
        for worker in _EXP2_WORKERS
        for position, case_id, selection_index in _EXP2_CASES
    )
    actual = tuple(
        (
            int(item["worker_count"]),
            str(item["case_position"]),
            str(item["case_id"]),
            int(item["active_selection_index"]),
        )
        for item in refs
    )
    if actual != expected_axes or len(refs) != 24:
        raise ValueError("Experiment 2 frozen 24-condition sequence drift")
    for item, (worker, position, case_id, selection_index) in zip(
        refs, expected_axes, strict=True
    ):
        expected_body = {
            "condition_id": f"epd027_exp2_online_w{worker}_{position}_r0",
            "experiment_id": "exp2_online_concurrency_check",
            "evidence_class": "online_real_provider",
            "worker_count": worker,
            "case_position": position,
            "case_id": case_id,
            "paper_difficulty": "hard",
            "active_selection_index": selection_index,
            "repeat_id": 0,
            "split_profile_id": "factorization.exp2_contiguous_20way.v1",
            "planned_first_ai_units": 20,
            "provider_calls_upper": 20,
            "max_concurrent_roots": 1,
        }
        body = {key: value for key, value in item.items() if key != "condition_digest"}
        if item.get("condition_digest") != canonical_digest(body):
            raise ValueError("Experiment 2 condition digest drift")
        if body != expected_body:
            raise ValueError("Experiment 2 frozen condition axes drift")
    return refs


def _semantic_inventory_plan_to_dict(
    plan: SemanticInventoryPlan,
) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "inventory_digest": plan.inventory_digest,
        "rows": [row.to_dict() for row in plan.rows],
        "condition_refs": [dict(item) for item in plan.condition_refs],
        "exp2_online_condition_refs": [
            dict(item) for item in plan.exp2_online_condition_refs
        ],
        "max_concurrent_roots": plan.max_concurrent_roots,
        "expected_slot_count": plan.expected_slot_count,
        "terminal_provider_failure_count": plan.terminal_provider_failure_count,
        "terminal_success_count": plan.terminal_success_count,
        "terminal_unacquired_count": plan.terminal_unacquired_count,
        "observed_early_stop_slot_keys": list(plan.observed_early_stop_slot_keys),
    }


def _semantic_inventory_plan_from_dict(value: Any) -> SemanticInventoryPlan:
    if not isinstance(value, Mapping):
        raise ValueError("semantic inventory plan must be a JSON object")
    expected = {
        "schema_version",
        "inventory_digest",
        "rows",
        "condition_refs",
        "exp2_online_condition_refs",
        "max_concurrent_roots",
        "expected_slot_count",
        "terminal_provider_failure_count",
        "terminal_success_count",
        "terminal_unacquired_count",
        "observed_early_stop_slot_keys",
    }
    if set(value) != expected:
        raise ValueError("semantic inventory plan fields do not match v1 schema")
    raw_rows = value["rows"]
    raw_refs = value["condition_refs"]
    raw_online_refs = value["exp2_online_condition_refs"]
    raw_early_stop = value["observed_early_stop_slot_keys"]
    if not all(
        isinstance(item, list)
        for item in (raw_rows, raw_refs, raw_online_refs, raw_early_stop)
    ):
        raise ValueError("semantic inventory plan collections must be lists")
    if any(not isinstance(item, Mapping) for item in (*raw_refs, *raw_online_refs)):
        raise ValueError("semantic inventory plan refs must be JSON objects")
    plan = SemanticInventoryPlan(
        schema_version=str(value["schema_version"]),
        inventory_digest=str(value["inventory_digest"]),
        rows=tuple(ResponseBankInventoryRow.from_dict(item) for item in raw_rows),
        condition_refs=tuple(dict(item) for item in raw_refs),
        exp2_online_condition_refs=tuple(dict(item) for item in raw_online_refs),
        max_concurrent_roots=int(value["max_concurrent_roots"]),
        expected_slot_count=int(value["expected_slot_count"]),
        terminal_provider_failure_count=int(value["terminal_provider_failure_count"]),
        terminal_success_count=int(value["terminal_success_count"]),
        terminal_unacquired_count=int(value["terminal_unacquired_count"]),
        observed_early_stop_slot_keys=tuple(str(item) for item in raw_early_stop),
    )
    _validate_semantic_inventory_plan(plan)
    return plan


def _validate_semantic_inventory_plan(plan: SemanticInventoryPlan) -> None:
    if type(plan) is not SemanticInventoryPlan:
        raise TypeError("semantic_inventory_plan must be an exact SemanticInventoryPlan")
    if plan.schema_version != "tokenshare.response_bank_semantic_inventory_plan.v1":
        raise ValueError("semantic inventory plan schema version drift")
    rows = canonical_inventory_rows(plan.rows)
    if rows != plan.rows:
        raise ValueError("semantic inventory plan rows are not canonical")
    if response_bank_inventory_digest(rows) != plan.inventory_digest:
        raise ValueError("semantic inventory plan digest mismatch")
    if plan.max_concurrent_roots != 1:
        raise ValueError("semantic inventory plan max_concurrent_roots must remain 1")
    if plan.expected_slot_count != len(rows):
        raise ValueError("semantic inventory plan slot count mismatch")
    terminal_counts = (
        plan.terminal_provider_failure_count,
        plan.terminal_success_count,
        plan.terminal_unacquired_count,
    )
    if any(type(item) is not int or item < 0 for item in terminal_counts):
        raise ValueError("semantic inventory plan terminal counts are invalid")
    if sum(terminal_counts) != len(rows):
        raise ValueError("semantic inventory plan terminal counts do not cover inventory")

    rows_by_slot = {row.semantic_slot_key: row for row in rows}
    if len(rows_by_slot) != len(rows):
        raise ValueError("semantic inventory plan has duplicate semantic slots")
    observed = set(plan.observed_early_stop_slot_keys)
    if len(observed) != len(plan.observed_early_stop_slot_keys):
        raise ValueError("semantic inventory plan early-stop slots are duplicated")
    if not observed <= set(rows_by_slot):
        raise ValueError("semantic inventory plan early-stop slot is not registered")

    condition_ids: set[str] = set()
    covered_slots: set[str] = set()
    expected_ref_fields = {
        "condition_id",
        "condition_digest",
        "experiment_id",
        "worker_count",
        "repeat_id",
        "fault_type",
        "ablation_mode",
        "semantic_slot_keys",
        "case_refs",
    }
    sparse_ref_fields = {
        "replacement_policy_id",
        "fault_rate",
        "dead_worker_count",
        "kill_progress_percent",
    }
    for raw_ref in plan.condition_refs:
        if not isinstance(raw_ref, Mapping):
            raise ValueError("semantic inventory condition ref schema drift")
        raw_fields = set(raw_ref)
        sparse_policy = raw_fields == expected_ref_fields | sparse_ref_fields
        if raw_fields != expected_ref_fields and not sparse_policy:
            raise ValueError("semantic inventory condition ref schema drift")
        if sparse_policy and (
            raw_ref["replacement_policy_id"]
            != "regression_smoke_sparse_replacements.v1"
            or raw_ref["experiment_id"]
            not in {
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            }
        ):
            raise ValueError("semantic inventory replacement policy marker is invalid")
        condition_id = raw_ref["condition_id"]
        if not isinstance(condition_id, str) or not condition_id:
            raise ValueError("semantic inventory condition id is invalid")
        if condition_id in condition_ids:
            raise ValueError("semantic inventory condition ids are duplicated")
        condition_ids.add(condition_id)
        slot_keys = raw_ref["semantic_slot_keys"]
        case_refs = raw_ref["case_refs"]
        if not isinstance(slot_keys, list) or not slot_keys:
            raise ValueError("semantic inventory condition slots are invalid")
        if len(set(slot_keys)) != len(slot_keys) or not set(slot_keys) <= set(rows_by_slot):
            raise ValueError("semantic inventory condition slots are invalid")
        if not isinstance(case_refs, list) or not case_refs:
            raise ValueError("semantic inventory case refs are invalid")
        case_slots: set[str] = set()
        unit_slots: dict[tuple[str, str, int], set[int]] = {}
        for case_ref in case_refs:
            if not isinstance(case_ref, Mapping) or set(case_ref) != {
                "case_id",
                "case_record_digest",
                "semantic_slot_keys",
            }:
                raise ValueError("semantic inventory case ref schema drift")
            ref_slots = case_ref["semantic_slot_keys"]
            if not isinstance(ref_slots, list) or not ref_slots:
                raise ValueError("semantic inventory case slots are invalid")
            case_unit_ids: list[str] = []
            for slot_key in ref_slots:
                row = rows_by_slot.get(slot_key)
                if row is None or row.case_record_digest != case_ref["case_record_digest"]:
                    raise ValueError("semantic inventory case row binding mismatch")
                case_slots.add(slot_key)
                if row.planned_ai_unit_id not in case_unit_ids:
                    case_unit_ids.append(row.planned_ai_unit_id)
                unit_slots.setdefault(
                    (
                        row.case_record_digest,
                        row.planned_ai_unit_id,
                        row.sample_slot_index,
                    ),
                    set(),
                ).add(row.replacement_slot)
            if sparse_policy:
                expected_by_unit = regression_smoke_sparse_replacement_slots(
                    experiment_id=str(raw_ref["experiment_id"]),
                    fault_type=str(raw_ref["fault_type"]),
                    fault_rate=float(raw_ref["fault_rate"]),
                    ablation_mode=str(raw_ref["ablation_mode"]),
                    planned_ai_unit_ids=tuple(case_unit_ids),
                    dead_worker_count=(
                        None
                        if raw_ref["dead_worker_count"] is None
                        else int(raw_ref["dead_worker_count"])
                    ),
                    kill_progress_percent=(
                        None
                        if raw_ref["kill_progress_percent"] is None
                        else int(raw_ref["kill_progress_percent"])
                    ),
                )
                case_record_digest = str(case_ref["case_record_digest"])
                for unit_id, expected_slots in expected_by_unit.items():
                    key = (
                        case_record_digest,
                        unit_id,
                        int(raw_ref["repeat_id"]),
                    )
                    if unit_slots.get(key) != set(expected_slots):
                        raise ValueError(
                            "semantic inventory replacement policy is incomplete"
                        )
        if case_slots != set(slot_keys):
            raise ValueError("semantic inventory case refs do not cover condition")
        if not sparse_policy:
            expected_replacements = set(
                replacement_slots_for(
                    experiment_id=str(raw_ref["experiment_id"]),
                    fault_type=str(raw_ref["fault_type"]),
                    ablation_mode=str(raw_ref["ablation_mode"]),
                )
            )
            if any(actual != expected_replacements for actual in unit_slots.values()):
                raise ValueError("semantic inventory replacement policy is incomplete")
        covered_slots.update(slot_keys)
    if covered_slots != set(rows_by_slot):
        raise ValueError("semantic inventory condition refs do not cover inventory")
    _exp2_online_refs(plan.exp2_online_condition_refs)


def _acquisition_request_to_dict(
    request: AcquisitionRequest,
) -> dict[str, object]:
    prepared = validate_prepared_request(request.prepared_request)
    prepared_body = asdict(prepared)
    prepared_body["body_bytes_base64"] = base64.b64encode(
        prepared_body.pop("body_bytes")
    ).decode("ascii")
    return {
        "inventory_row": request.inventory_row.to_dict(),
        "prepared_request": prepared_body,
        "provider_family": request.provider_family,
        "api_key_env": request.api_key_env,
        "timeout_seconds": request.timeout_seconds,
        "token_upper_bound": request.token_upper_bound,
        "cost_upper_bound": str(request.cost_upper_bound),
        "frozen_pricing": {
            "currency": request.frozen_pricing.currency,
            "input_per_million_tokens": str(
                request.frozen_pricing.input_per_million_tokens
            ),
            "output_per_million_tokens": str(
                request.frozen_pricing.output_per_million_tokens
            ),
        },
        "requested_at": request.requested_at,
    }


def _acquisition_request_from_dict(value: Any) -> AcquisitionRequest:
    if not isinstance(value, Mapping):
        raise ValueError("acquisition request must be a JSON object")
    expected = {
        "inventory_row",
        "prepared_request",
        "provider_family",
        "api_key_env",
        "timeout_seconds",
        "token_upper_bound",
        "cost_upper_bound",
        "frozen_pricing",
        "requested_at",
    }
    if set(value) != expected:
        raise ValueError("acquisition request fields do not match v1 schema")
    raw_prepared = value["prepared_request"]
    if not isinstance(raw_prepared, Mapping):
        raise ValueError("prepared request must be a JSON object")
    prepared_fields = set(PreparedOutboundRequest.__dataclass_fields__)
    if set(raw_prepared) != (prepared_fields - {"body_bytes"}) | {
        "body_bytes_base64"
    }:
        raise ValueError("prepared request fields do not match v1 schema")
    prepared_body = dict(raw_prepared)
    try:
        encoded = prepared_body.pop("body_bytes_base64")
        if not isinstance(encoded, str):
            raise ValueError("prepared request body bytes must be base64 text")
        prepared_body["body_bytes"] = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("prepared request body bytes are invalid") from exc
    prepared = validate_prepared_request(PreparedOutboundRequest(**prepared_body))
    raw_pricing = value["frozen_pricing"]
    if not isinstance(raw_pricing, Mapping) or set(raw_pricing) != {
        "currency",
        "input_per_million_tokens",
        "output_per_million_tokens",
    }:
        raise ValueError("frozen pricing fields do not match v1 schema")
    return AcquisitionRequest(
        inventory_row=ResponseBankInventoryRow.from_dict(value["inventory_row"]),
        prepared_request=prepared,
        provider_family=str(value["provider_family"]),
        api_key_env=str(value["api_key_env"]),
        timeout_seconds=int(value["timeout_seconds"]),
        token_upper_bound=int(value["token_upper_bound"]),
        cost_upper_bound=Decimal(str(value["cost_upper_bound"])),
        frozen_pricing=FrozenPricing(
            currency=str(raw_pricing["currency"]),
            input_per_million_tokens=Decimal(
                str(raw_pricing["input_per_million_tokens"])
            ),
            output_per_million_tokens=Decimal(
                str(raw_pricing["output_per_million_tokens"])
            ),
        ),
        requested_at=str(value["requested_at"]),
    )


def _object_dict(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("condition reference must be a mapping or dataclass")


__all__ = [
    "ACQUISITION_PLAN_BUNDLE_FILENAME",
    "ACQUISITION_PLAN_BUNDLE_SCHEMA_VERSION",
    "FULL_ACQUISITION_BUDGET_SCHEMA_VERSION",
    "IMMUTABLE_CHILD_BANK_DIRNAME",
    "PROVIDER_FAILURE_TAXONOMY",
    "RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION",
    "RESULTS_FIRST_ACQUISITION_MARKER_FILENAME",
    "RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION",
    "AcquisitionPlanBundle",
    "AcquisitionAuthorizationError",
    "AcquisitionBatchResult",
    "AcquisitionIdentityError",
    "AcquisitionReconcileReport",
    "AcquisitionRequest",
    "AcquisitionResult",
    "FullAcquisitionBudget",
    "InventoryPreflightResult",
    "FormalTraceInventoryPreflightResult",
    "Matrix8UnifiedAcquisitionPlan",
    "PaperTraceRuntimeContext",
    "PaperTraceCaseBinding",
    "PaperFormalTraceContext",
    "PaidAcquisitionContext",
    "ResponseBankPreflightBlockedRecord",
    "ResponseBankAcquisitionOrchestrator",
    "ResultsFirstAcquisitionAuthorization",
    "ResultsFirstAcquisitionMarker",
    "SemanticInventoryPlan",
    "SemanticSlotCandidate",
    "build_semantic_inventory",
    "build_matrix8_unified_acquisition_plan",
    "create_acquisition_plan_bundle",
    "establish_results_first_acquisition_authorization",
    "finalize_acquisition_child_bank",
    "load_acquisition_plan_bundle",
    "output_root_path_digest",
    "preflight_inventory_before_coordinator",
    "preflight_formal_trace_inventory",
    "regression_smoke_sparse_replacement_slots",
    "replacement_slots_for",
    "response_bank_manifest_for_bundle",
    "results_first_response_bank_manifest_for_bundle",
]
