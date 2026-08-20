"""Exp2--4 真实回答库的确定性 semantic-slot inventory 与零引擎预检。"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, wait
import json
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Callable, ClassVar, Mapping, Sequence

from tokenshare.executors.ai_api import (
    PROVIDER_FAILURE_TAXONOMY,
    PreparedDispatchEvidence,
    dispatch_prepared_request_once,
)
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
from tokenshare.experiments.paper_budget import PaperBudgetLimits
from tokenshare.experiments.paper_paid_authorization import (
    PAID_OUTPUT_BINDING_FILENAME,
    PaidAuthorizationValidation,
    output_root_path_digest as task26_output_root_path_digest,
)
from tokenshare.experiments.paper_resource_accounting import (
    FrozenPricing,
    ProviderUsage,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
)
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
    "tokenshare.paper_acquisition_plan_bundle.v2"
)
FULL_ACQUISITION_BUDGET_SCHEMA_VERSION = (
    "tokenshare.paper_full_acquisition_budget.v1"
)
ACQUISITION_PLAN_BUNDLE_FILENAME = "acquisition_plan_bundle.v2.json"
IMMUTABLE_CHILD_BANK_DIRNAME = "immutable_response_bank"
SUPERVISED_NO_RESPONSE_AUTHORITY_FILENAME = (
    "supervised_provider_no_response_closure_authority.v1.json"
)
SUPERVISED_NO_RESPONSE_AUTHORITY_REF_FILENAME = (
    "supervised_provider_no_response_closure_authority_ref.v1.json"
)
SUPERVISED_NO_RESPONSE_RECEIPT_FILENAME = (
    "supervised_provider_no_response_closure_receipt.v1.json"
)
SUPERVISED_NO_RESPONSE_RECEIPT_REF_FILENAME = (
    "supervised_provider_no_response_closure_receipt_ref.v1.json"
)


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
    formal_authority: bool = False
    selection_id: str | None = None
    selection_digest: str | None = None
    seed: int | None = None
    split_profile_id: str | None = None
    split_profile_digest: str | None = None
    source_snapshot_digest: str | None = None
    coverage_digest: str | None = None
    model_endpoint_identity_digest: str | None = None
    request_controls_digest: str | None = None


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
    inventory_rows: tuple[ResponseBankInventoryRow, ...]
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
        if not self.inventory_rows or any(
            not isinstance(row, ResponseBankInventoryRow)
            for row in self.inventory_rows
        ):
            raise TypeError(
                "trace runtime inventory rows must be non-empty canonical rows"
            )
        inventory_entry_ids = tuple(
            row.inventory_entry_id for row in self.inventory_rows
        )
        if len(set(inventory_entry_ids)) != len(inventory_entry_ids):
            raise ValueError("trace runtime inventory row identities must be unique")
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
            runtime = PaperTraceRuntimeContext(
                resolver=resolver,
                bindings=bindings,
                inventory_rows=rows,
            )
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
    "tokenshare.results_first_acquisition_authorization.v1"
)
RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION = (
    "tokenshare.results_first_facility_marker.v1"
)
RESULTS_FIRST_ACQUISITION_MARKER_FILENAME = (
    "results_first_facility_marker.v1.json"
)
LEGACY_RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION = (
    "tokenshare.results_first_smoke_acquisition_authorization.v1"
)
LEGACY_RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION = (
    "tokenshare.results_first_smoke_facility_marker.v1"
)
LEGACY_RESULTS_FIRST_ACQUISITION_MARKER_FILENAME = (
    "results_first_smoke_facility_marker.v1.json"
)
RESULTS_FIRST_AUTHORIZATION_KIND = "user_authorized_results_first"
LEGACY_RESULTS_FIRST_AUTHORIZATION_KIND = "user_authorized_smoke_facility"


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
    """用户显式授权的 results-first acquisition；不是 paid receipt。"""

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
    marker_path = root / RESULTS_FIRST_ACQUISITION_MARKER_FILENAME
    legacy_marker_path = (
        root / LEGACY_RESULTS_FIRST_ACQUISITION_MARKER_FILENAME
    )
    if output_mode == "resume":
        current_exists = marker_path.exists()
        legacy_exists = legacy_marker_path.exists()
        if current_exists and legacy_exists:
            raise AcquisitionAuthorizationError(
                "both current and legacy results-first facility markers exist"
            )
        if current_exists:
            authorization_schema = (
                RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION
            )
            marker_schema = RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION
            authorization_kind = RESULTS_FIRST_AUTHORIZATION_KIND
        elif legacy_exists:
            authorization_schema = (
                LEGACY_RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION
            )
            marker_schema = (
                LEGACY_RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION
            )
            authorization_kind = LEGACY_RESULTS_FIRST_AUTHORIZATION_KIND
            marker_path = legacy_marker_path
        else:
            raise AcquisitionAuthorizationError(
                "results-first facility marker is missing or partial"
            )
    else:
        authorization_schema = (
            RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION
        )
        marker_schema = RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION
        authorization_kind = RESULTS_FIRST_AUTHORIZATION_KIND
    selected_experiments = (
        (
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        )
        if authorization_kind == LEGACY_RESULTS_FIRST_AUTHORIZATION_KIND
        else ("exp1_real_ai_feasibility",)
    )
    identity = {
        "schema_version": authorization_schema,
        "authorization_kind": authorization_kind,
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
        "schema_version": marker_schema,
        "authorization_kind": authorization_kind,
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
        schema_version=authorization_schema,
        authorization_kind=authorization_kind,
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
        authorization_state=authorization_kind,
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


@dataclass(frozen=True, kw_only=True)
class AcquisitionPlanBundle:
    """create-only 持久化的 exact prepared requests 与 canonical inventory。"""

    schema_version: str
    bundle_digest: str
    authorized_plan_digest: str
    profile_digest: str
    source_snapshot_digest: str | None
    source_prepared_inventory_digest: str | None
    coverage_digest: str | None
    representative_plan_digest: str | None
    inventory_digest: str
    prompt_admission_profile_digest: str
    provider_config_digest: str
    semantic_inventory_plan: SemanticInventoryPlan
    inventory_rows: tuple[ResponseBankInventoryRow, ...]
    acquisition_requests: tuple["AcquisitionRequest", ...]
    max_acquisition_concurrency: int
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
            "source_snapshot_digest": self.source_snapshot_digest,
            "source_prepared_inventory_digest": (
                self.source_prepared_inventory_digest
            ),
            "coverage_digest": self.coverage_digest,
            "representative_plan_digest": self.representative_plan_digest,
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
            "max_acquisition_concurrency": self.max_acquisition_concurrency,
            "full_budget": self.full_budget.to_dict(),
        }

    def validate(self) -> None:
        if self.schema_version != ACQUISITION_PLAN_BUNDLE_SCHEMA_VERSION:
            raise ValueError("acquisition plan bundle schema version drift")
        if self.bundle_digest != canonical_digest(self.digest_preimage()):
            raise ValueError("acquisition plan bundle digest mismatch")
        lineage = (
            self.source_snapshot_digest,
            self.source_prepared_inventory_digest,
            self.coverage_digest,
            self.representative_plan_digest,
        )
        if any(value is None for value in lineage) and not all(
            value is None for value in lineage
        ):
            raise ValueError("acquisition plan bundle lineage is partial")
        if all(value is not None for value in lineage):
            if any(
                not isinstance(value, str) or not value.startswith("sha256:")
                for value in lineage
            ):
                raise ValueError("acquisition plan bundle lineage digest is invalid")
            if (
                self.authorized_plan_digest != self.source_snapshot_digest
                or self.profile_digest != self.coverage_digest
            ):
                raise ValueError("acquisition plan bundle formal lineage authority drift")
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
        if type(self.max_acquisition_concurrency) is not int or not (
            1 <= self.max_acquisition_concurrency <= 10
        ):
            raise ValueError("acquisition plan concurrency must be within 1..10")
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
class RepresentativeUnifiedAcquisitionPlan:
    """从 full prepared inventory 派生的 Exp1--4 exact acquisition 计划。"""

    source_snapshot_digest: str
    source_prepared_inventory_digest: str
    coverage_digest: str
    condition_candidate_count: int
    unique_acquisition_request_count: int
    candidates: tuple[SemanticSlotCandidate, ...]
    semantic_inventory_plan: SemanticInventoryPlan
    acquisition_requests: tuple[AcquisitionRequest, ...]
    max_acquisition_concurrency: int
    provider_call_count: int = 0
    schema_version: str = "tokenshare.representative_unified_acquisition_plan.v1"

    @property
    def profile_digest(self) -> str:
        return self.coverage_digest

    @property
    def plan_digest(self) -> str:
        return canonical_digest(
            {
                "schema_version": self.schema_version,
                "source_snapshot_digest": self.source_snapshot_digest,
                "source_prepared_inventory_digest": (
                    self.source_prepared_inventory_digest
                ),
                "coverage_digest": self.coverage_digest,
                "condition_candidate_count": self.condition_candidate_count,
                "unique_acquisition_request_count": (
                    self.unique_acquisition_request_count
                ),
                "semantic_inventory_plan": _semantic_inventory_plan_to_dict(
                    self.semantic_inventory_plan
                ),
                "acquisition_requests": [
                    _acquisition_request_to_dict(item)
                    for item in self.acquisition_requests
                ],
                "max_acquisition_concurrency": self.max_acquisition_concurrency,
                "provider_call_count": self.provider_call_count,
            }
        )

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.representative_unified_acquisition_plan.v1":
            raise ValueError("representative acquisition plan schema drift")
        if self.provider_call_count != 0:
            raise ValueError("representative acquisition planning must not call providers")
        if not 1 <= self.max_acquisition_concurrency <= 10:
            raise ValueError("representative acquisition concurrency must be within 1..10")
        if self.condition_candidate_count != len(self.candidates):
            raise ValueError("representative acquisition candidate count mismatch")
        if self.unique_acquisition_request_count != len(self.acquisition_requests):
            raise ValueError("representative acquisition request count mismatch")
        if self.unique_acquisition_request_count != len(self.semantic_inventory_plan.rows):
            raise ValueError("representative acquisition inventory count mismatch")
        for value, field_name in (
            (self.source_snapshot_digest, "source_snapshot_digest"),
            (self.source_prepared_inventory_digest, "source_prepared_inventory_digest"),
            (self.coverage_digest, "coverage_digest"),
        ):
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError(f"representative acquisition {field_name} is invalid")
        request_rows = tuple(item.inventory_row for item in self.acquisition_requests)
        if request_rows != self.semantic_inventory_plan.rows:
            raise ValueError("representative acquisition request order mismatch")
        if not self.candidates or any(
            not candidate.formal_authority
            or candidate.source_snapshot_digest != self.source_snapshot_digest
            or candidate.coverage_digest != self.coverage_digest
            for candidate in self.candidates
        ):
            raise ValueError("representative acquisition candidates lack formal authority")
        if build_semantic_inventory(self.candidates) != self.semantic_inventory_plan:
            raise ValueError("representative acquisition candidate mapping drift")


@dataclass(frozen=True, kw_only=True)
class FullCurrentAcquisitionPricingAuthority:
    """Full Exp1 的当前价格与冻结 source provenance 的显式双重绑定。"""

    selection_kind: str
    provider_config_id: str
    source_provider_config_digest: str
    source_execution_identity_digest: str
    current_provider_config_digest: str
    current_execution_identity_digest: str
    pricing_by_entry: Mapping[str, FrozenPricing]
    current_pricing_authority_digest: str
    full_budget_approval_authority_digest: str
    approved_exp1_budget_digest: str
    provider_calls_made: int = 0
    schema_version: str = (
        "tokenshare.full_current_acquisition_pricing_authority.v1"
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != "tokenshare.full_current_acquisition_pricing_authority.v1"
            or self.selection_kind != "full_exp1_exp3_exp5"
            or self.provider_calls_made != 0
            or not self.provider_config_id
            or not self.pricing_by_entry
            or any(
                not isinstance(entry_id, str)
                or not entry_id
                or type(pricing) is not FrozenPricing
                for entry_id, pricing in self.pricing_by_entry.items()
            )
        ):
            raise ValueError("Full current acquisition pricing authority is invalid")
        for digest in (
            self.source_provider_config_digest,
            self.source_execution_identity_digest,
            self.current_provider_config_digest,
            self.current_execution_identity_digest,
            self.current_pricing_authority_digest,
            self.full_budget_approval_authority_digest,
            self.approved_exp1_budget_digest,
        ):
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise ValueError("Full current acquisition pricing digest is invalid")

    def applies_to(self, *, record: Any) -> bool:
        return (
            getattr(record, "provider_config_id", None) == self.provider_config_id
        )

    def resolve_pricing(
        self,
        *,
        record: Any,
        source_config: Any,
    ) -> FrozenPricing:
        if not self.applies_to(record=record):
            raise ValueError("Full current acquisition pricing authority target drift")
        if (
            self.source_execution_identity_digest
            != self.current_execution_identity_digest
        ):
            raise ValueError("Full current pricing execution identity drift")
        if getattr(record, "source_provider_config_digest", None) != (
            self.source_provider_config_digest
        ):
            raise ValueError("Full current acquisition source provenance drift")
        config_digest = getattr(source_config, "config_digest", None)
        if config_digest not in {
            self.source_provider_config_digest,
            self.current_provider_config_digest,
        }:
            raise ValueError("Full current acquisition source provenance drift")
        if (
            self.source_execution_identity_digest
            != self.current_execution_identity_digest
        ):
            raise ValueError("Full current pricing execution identity drift")
        if config_digest == self.current_provider_config_digest:
            from tokenshare.experiments.run_paper_experiments import (
                _pricing_refresh_execution_identity,
            )

            if (
                _pricing_refresh_execution_identity(source_config)
                != self.current_execution_identity_digest
            ):
                raise ValueError("Full current pricing execution identity drift")
        entry_id = getattr(record, "model_entry_id", None)
        pricing = self.pricing_by_entry.get(entry_id)
        if type(pricing) is not FrozenPricing:
            raise ValueError("Full current acquisition pricing entry drift")
        return pricing


@dataclass(frozen=True, kw_only=True)
class ResultsFirstAcquisitionPlan:
    """仅冻结 selection lineage；真实 acquisition 前才物化 exact requests。"""

    full_snapshot: Any
    full_prepared_inventory: Any
    full_budget: Any
    coverage: Any
    execution_budget_projection: Any
    catalog_manifest: PaperInputCatalogManifest
    ai_api_configs: Mapping[str, Any]
    planning_artifact_root: Path
    api_key_env_by_provider_family: Mapping[str, str]
    frozen_pricing_by_provider_family: Mapping[str, FrozenPricing]
    requested_at: str
    max_acquisition_concurrency: int = 10
    full_current_pricing_authority: FullCurrentAcquisitionPricingAuthority | None = None
    source_ai_api_configs: Mapping[str, Any] | None = None
    current_ai_api_configs: Mapping[str, Any] | None = None
    provider_call_count: int = 0
    schema_version: str = "tokenshare.results_first_acquisition_plan.v1"

    def __post_init__(self) -> None:
        from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
        from tokenshare.experiments.paper_formal_plan import (
            FormalExecutionCoverage,
            FormalPlanSnapshot,
            FormalPreparedRequestInventory,
        )
        from tokenshare.experiments.paper_models import PaperBudgetResult

        if (
            type(self.full_snapshot) is not FormalPlanSnapshot
            or type(self.full_prepared_inventory) is not FormalPreparedRequestInventory
            or type(self.full_budget) is not PaperBudgetResult
            or type(self.coverage) is not FormalExecutionCoverage
            or type(self.execution_budget_projection)
            is not PaperExecutionBudgetProjection
        ):
            raise TypeError("results-first acquisition plan object type drift")
        if (
            self.schema_version != "tokenshare.results_first_acquisition_plan.v1"
            or self.provider_call_count != 0
            or self.coverage.source_snapshot is not self.full_snapshot
            or self.execution_budget_projection.source_snapshot is not self.full_snapshot
            or self.execution_budget_projection.source_budget is not self.full_budget
            or self.execution_budget_projection.coverage is not self.coverage
            or self.full_prepared_inventory.source_snapshot_digest
            != self.full_snapshot.snapshot_digest
        ):
            raise ValueError("results-first acquisition plan lineage drift")
        if type(self.max_acquisition_concurrency) is not int or not (
            1 <= self.max_acquisition_concurrency <= 10
        ):
            raise ValueError("results-first acquisition concurrency must be within 1..10")
        if not self.requested_at:
            raise ValueError("results-first acquisition requested_at is missing")
        if self.full_current_pricing_authority is not None and (
            type(self.full_current_pricing_authority)
            is not FullCurrentAcquisitionPricingAuthority
            or self.coverage.selection_kind not in {"full", "full_exp1_exp3_exp5"}
        ):
            raise ValueError("results-first current pricing authority scope drift")
        if self.source_ai_api_configs is not None and not isinstance(
            self.source_ai_api_configs, Mapping
        ):
            raise TypeError("results-first source provider config map is invalid")
        if self.current_ai_api_configs is not None and not isinstance(
            self.current_ai_api_configs, Mapping
        ):
            raise TypeError("results-first current provider config map is invalid")
        object.__setattr__(self, "planning_artifact_root", Path(self.planning_artifact_root))

    @property
    def source_snapshot_digest(self) -> str:
        return self.full_snapshot.snapshot_digest

    @property
    def source_prepared_inventory_digest(self) -> str:
        return self.full_prepared_inventory.inventory_digest

    @property
    def coverage_digest(self) -> str:
        return self.coverage.coverage_digest

    @property
    def projection_digest(self) -> str:
        return self.execution_budget_projection.projection_digest

    @property
    def plan_digest(self) -> str:
        return canonical_digest(
            {
                "schema_version": self.schema_version,
                "source_snapshot_digest": self.source_snapshot_digest,
                "source_prepared_inventory_digest": (
                    self.source_prepared_inventory_digest
                ),
                "source_budget_digest": self.full_budget.budget_digest,
                "coverage_digest": self.coverage_digest,
                "execution_budget_projection_digest": self.projection_digest,
                "selection_kind": self.coverage.selection_kind,
                "condition_count": self.coverage.condition_count,
                "root_run_count": self.coverage.root_run_count,
                "first_attempt_ai_unit_count": (
                    self.coverage.selected_first_attempt_ai_unit_count
                ),
                "full_current_pricing_authority_digest": (
                    self.full_current_pricing_authority.current_pricing_authority_digest
                    if self.full_current_pricing_authority is not None
                    else None
                ),
                "full_budget_approval_authority_digest": (
                    self.full_current_pricing_authority.full_budget_approval_authority_digest
                    if self.full_current_pricing_authority is not None
                    else None
                ),
                "max_acquisition_concurrency": self.max_acquisition_concurrency,
                "provider_call_count": self.provider_call_count,
            }
        )


@dataclass(frozen=True, kw_only=True)
class ResultsFirstReplaySemanticAuthority:
    """Provider-zero Exp1--4 target semantics; distinct from paid inventory."""

    candidates: tuple[SemanticSlotCandidate, ...]
    semantic_inventory_plan: SemanticInventoryPlan
    source_snapshot_digest: str
    coverage_digest: str
    provider_call_count: int = 0
    schema_version: str = "tokenshare.results_first_replay_semantic_authority.v1"

    @classmethod
    def create(
        cls,
        *,
        candidates: Sequence[SemanticSlotCandidate],
        source_snapshot_digest: str,
        coverage_digest: str,
    ) -> "ResultsFirstReplaySemanticAuthority":
        values = tuple(candidates)
        return cls(
            candidates=values,
            semantic_inventory_plan=_build_replay_semantic_inventory(values),
            source_snapshot_digest=source_snapshot_digest,
            coverage_digest=coverage_digest,
        )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != "tokenshare.results_first_replay_semantic_authority.v1"
            or self.provider_call_count != 0
            or not self.candidates
            or _build_replay_semantic_inventory(self.candidates)
            != self.semantic_inventory_plan
        ):
            raise ValueError("results-first replay semantic authority drift")
        if {
            candidate.experiment_id for candidate in self.candidates
        } - {
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        }:
            raise ValueError("replay semantic authority includes an online experiment")
        for value in (self.source_snapshot_digest, self.coverage_digest):
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError("replay semantic authority digest is invalid")


@dataclass(frozen=True, kw_only=True)
class ResultsFirstAcquisitionAuthority:
    """一次 full freeze 与一个显式 typed selection 的零调用 authority。"""

    full_snapshot: Any
    full_prepared_inventory: Any
    full_budget: Any
    coverage: Any
    execution_budget_projection: Any
    plan: ResultsFirstAcquisitionPlan
    validation_digest: str
    provider_calls_made: int = 0
    schema_version: str = "tokenshare.results_first_acquisition_authority.v1"

    @classmethod
    def create(
        cls,
        *,
        full_snapshot: Any,
        full_prepared_inventory: Any,
        full_budget: Any,
        coverage: Any,
        execution_budget_projection: Any,
        plan: ResultsFirstAcquisitionPlan,
    ) -> "ResultsFirstAcquisitionAuthority":
        validation_digest = canonical_digest(
            {
                "schema_version": "tokenshare.results_first_acquisition_authority.v1",
                "source_snapshot_digest": full_snapshot.snapshot_digest,
                "source_prepared_inventory_digest": (
                    full_prepared_inventory.inventory_digest
                ),
                "source_budget_digest": full_budget.budget_digest,
                "coverage_digest": coverage.coverage_digest,
                "execution_budget_projection_digest": (
                    execution_budget_projection.projection_digest
                ),
                "results_first_plan_digest": plan.plan_digest,
                "provider_calls_made": 0,
            }
        )
        return cls(
            full_snapshot=full_snapshot,
            full_prepared_inventory=full_prepared_inventory,
            full_budget=full_budget,
            coverage=coverage,
            execution_budget_projection=execution_budget_projection,
            plan=plan,
            validation_digest=validation_digest,
        )

    def validation_preimage(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_snapshot_digest": self.full_snapshot.snapshot_digest,
            "source_prepared_inventory_digest": (
                self.full_prepared_inventory.inventory_digest
            ),
            "source_budget_digest": self.full_budget.budget_digest,
            "coverage_digest": self.coverage.coverage_digest,
            "execution_budget_projection_digest": (
                self.execution_budget_projection.projection_digest
            ),
            "results_first_plan_digest": self.plan.plan_digest,
            "provider_calls_made": self.provider_calls_made,
        }

    def __post_init__(self) -> None:
        from tokenshare.experiments.paper_formal_plan import (
            FormalPlanSnapshot,
            FormalPreparedRequestInventory,
            FormalExecutionCoverage,
        )
        from tokenshare.experiments.paper_budget import (
            PaperExecutionBudgetProjection,
        )
        from tokenshare.experiments.paper_models import PaperBudgetResult

        if (
            type(self.full_snapshot) is not FormalPlanSnapshot
            or type(self.full_prepared_inventory) is not FormalPreparedRequestInventory
            or type(self.full_budget) is not PaperBudgetResult
            or type(self.coverage) is not FormalExecutionCoverage
            or type(self.execution_budget_projection)
            is not PaperExecutionBudgetProjection
            or type(self.plan) is not ResultsFirstAcquisitionPlan
        ):
            raise TypeError("results-first acquisition authority object type drift")
        if (
            self.schema_version
            != "tokenshare.results_first_acquisition_authority.v1"
            or self.provider_calls_made != 0
            or self.full_snapshot.provider_calls_made != 0
            or self.full_prepared_inventory.provider_calls_made != 0
            or self.coverage.provider_calls_made != 0
            or self.execution_budget_projection.provider_calls_made != 0
            or self.plan.provider_call_count != 0
            or self.coverage.source_snapshot is not self.full_snapshot
            or self.execution_budget_projection.source_snapshot is not self.full_snapshot
            or self.execution_budget_projection.source_budget is not self.full_budget
            or self.execution_budget_projection.coverage is not self.coverage
            or self.full_prepared_inventory.source_snapshot_digest
            != self.full_snapshot.snapshot_digest
            or self.coverage.source_snapshot_digest
            != self.full_snapshot.snapshot_digest
            or self.plan.source_snapshot_digest != self.full_snapshot.snapshot_digest
            or self.plan.source_prepared_inventory_digest
            != self.full_prepared_inventory.inventory_digest
            or self.plan.coverage_digest != self.coverage.coverage_digest
        ):
            raise ValueError("results-first acquisition authority lineage drift")
        if self.validation_digest != canonical_digest(self.validation_preimage()):
            raise ValueError("results-first acquisition authority digest mismatch")

    @property
    def snapshot(self) -> Any:
        """旧调用方兼容；对象仍是同一个 full snapshot。"""

        return self.full_snapshot

    @property
    def prepared_inventory(self) -> Any:
        """旧调用方兼容；对象仍是同一个 full prepared inventory。"""

        return self.full_prepared_inventory


# 历史名称仅是同一 concrete type 的兼容 alias。
RepresentativeAcquisitionAuthority = ResultsFirstAcquisitionAuthority


_VALIDATED_REPRESENTATIVE_AUTHORITY_SEAL = object()


@dataclass(frozen=True, kw_only=True)
class _ValidatedRepresentativeAuthority:
    snapshot: Any
    prepared_inventory: Any
    coverage: Any
    seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.seal is not _VALIDATED_REPRESENTATIVE_AUTHORITY_SEAL:
            raise TypeError("validated authority token is forged")
        _validate_bound_representative_authority_objects(
            snapshot=self.snapshot,
            prepared_inventory=self.prepared_inventory,
            coverage=self.coverage,
        )


def create_acquisition_plan_bundle(
    bundle_root: str | Path,
    *,
    authorized_plan_digest: str,
    profile_digest: str,
    semantic_inventory_plan: SemanticInventoryPlan,
    acquisition_requests: Sequence[AcquisitionRequest],
    max_acquisition_concurrency: int = 1,
) -> AcquisitionPlanBundle:
    """持久化非 formal fixture/generic acquisition authority。"""

    return _create_acquisition_plan_bundle(
        bundle_root,
        authorized_plan_digest=authorized_plan_digest,
        profile_digest=profile_digest,
        semantic_inventory_plan=semantic_inventory_plan,
        acquisition_requests=acquisition_requests,
        max_acquisition_concurrency=max_acquisition_concurrency,
        source_snapshot_digest=None,
        source_prepared_inventory_digest=None,
        coverage_digest=None,
        representative_plan_digest=None,
    )


def create_representative_acquisition_plan_bundle(
    bundle_root: str | Path,
    *,
    plan: RepresentativeUnifiedAcquisitionPlan,
) -> AcquisitionPlanBundle:
    """仅从完整 representative plan 对象持久化 formal lineage。"""

    if type(plan) is not RepresentativeUnifiedAcquisitionPlan:
        raise TypeError("plan must be a RepresentativeUnifiedAcquisitionPlan")
    bundle = _create_acquisition_plan_bundle(
        bundle_root,
        authorized_plan_digest=plan.source_snapshot_digest,
        profile_digest=plan.coverage_digest,
        semantic_inventory_plan=plan.semantic_inventory_plan,
        acquisition_requests=plan.acquisition_requests,
        max_acquisition_concurrency=plan.max_acquisition_concurrency,
        source_snapshot_digest=plan.source_snapshot_digest,
        source_prepared_inventory_digest=plan.source_prepared_inventory_digest,
        coverage_digest=plan.coverage_digest,
        representative_plan_digest=plan.plan_digest,
    )
    validate_representative_acquisition_plan_bundle(bundle=bundle, plan=plan)
    return bundle


def _create_acquisition_plan_bundle(
    bundle_root: str | Path,
    *,
    authorized_plan_digest: str,
    profile_digest: str,
    semantic_inventory_plan: SemanticInventoryPlan,
    acquisition_requests: Sequence[AcquisitionRequest],
    max_acquisition_concurrency: int,
    source_snapshot_digest: str | None,
    source_prepared_inventory_digest: str | None,
    coverage_digest: str | None,
    representative_plan_digest: str | None,
) -> AcquisitionPlanBundle:
    """以 create-new 方式持久化 exact prepared requests。"""

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
        source_snapshot_digest=source_snapshot_digest,
        source_prepared_inventory_digest=source_prepared_inventory_digest,
        coverage_digest=coverage_digest,
        representative_plan_digest=representative_plan_digest,
        inventory_digest=response_bank_inventory_digest(rows),
        prompt_admission_profile_digest=next(iter(admission_digests)),
        provider_config_digest=provider_config_digest,
        semantic_inventory_plan=semantic_inventory_plan,
        inventory_rows=rows,
        acquisition_requests=requests,
        max_acquisition_concurrency=max_acquisition_concurrency,
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
    return _load_acquisition_plan_bundle_raw(root)


def load_acquisition_plan_bundle(
    bundle_root: str | Path,
) -> AcquisitionPlanBundle:
    """加载 generic bundle；formal lineage 必须经 fresh-plan loader。"""

    bundle = _load_acquisition_plan_bundle_raw(bundle_root)
    if any(
        value is not None
        for value in (
            bundle.source_snapshot_digest,
            bundle.source_prepared_inventory_digest,
            bundle.coverage_digest,
            bundle.representative_plan_digest,
        )
    ):
        raise ValueError(
            "formal acquisition bundle requires fresh-plan validation"
        )
    return bundle


def _load_acquisition_plan_bundle_raw(
    bundle_root: str | Path,
) -> AcquisitionPlanBundle:
    """只反序列化并验证 bundle 内部自洽性，不授予 dispatch authority。"""

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
        "source_snapshot_digest",
        "source_prepared_inventory_digest",
        "coverage_digest",
        "representative_plan_digest",
        "inventory_digest",
        "prompt_admission_profile_digest",
        "provider_config_digest",
        "semantic_inventory_plan",
        "inventory_rows",
        "acquisition_requests",
        "max_acquisition_concurrency",
        "full_budget",
    }
    if set(value) != expected:
        raise ValueError("acquisition plan bundle fields do not match v2 schema")
    raw_rows = value["inventory_rows"]
    raw_requests = value["acquisition_requests"]
    if not isinstance(raw_rows, list) or not isinstance(raw_requests, list):
        raise ValueError("acquisition plan bundle rows and requests must be lists")
    bundle = AcquisitionPlanBundle(
        schema_version=str(value["schema_version"]),
        bundle_digest=str(value["bundle_digest"]),
        authorized_plan_digest=str(value["authorized_plan_digest"]),
        profile_digest=str(value["profile_digest"]),
        source_snapshot_digest=_optional_digest_value(
            value["source_snapshot_digest"],
            "source_snapshot_digest",
        ),
        source_prepared_inventory_digest=_optional_digest_value(
            value["source_prepared_inventory_digest"],
            "source_prepared_inventory_digest",
        ),
        coverage_digest=_optional_digest_value(
            value["coverage_digest"],
            "coverage_digest",
        ),
        representative_plan_digest=_optional_digest_value(
            value["representative_plan_digest"],
            "representative_plan_digest",
        ),
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
        max_acquisition_concurrency=int(value["max_acquisition_concurrency"]),
        full_budget=FullAcquisitionBudget.from_dict(value["full_budget"]),
    )
    bundle.validate()
    return bundle


def load_representative_acquisition_plan_bundle(
    bundle_root: str | Path,
    *,
    plan: RepresentativeUnifiedAcquisitionPlan,
) -> AcquisitionPlanBundle:
    """加载 bundle 并与 fresh representative plan 做 exact lineage 对账。"""

    bundle = _load_acquisition_plan_bundle_raw(bundle_root)
    validate_representative_acquisition_plan_bundle(bundle=bundle, plan=plan)
    return bundle


def validate_representative_acquisition_plan_bundle(
    *,
    bundle: AcquisitionPlanBundle,
    plan: RepresentativeUnifiedAcquisitionPlan,
) -> None:
    if type(bundle) is not AcquisitionPlanBundle:
        raise TypeError("bundle must be an AcquisitionPlanBundle")
    if type(plan) is not RepresentativeUnifiedAcquisitionPlan:
        raise TypeError("plan must be a RepresentativeUnifiedAcquisitionPlan")
    bundle.validate()
    expected_lineage = (
        plan.source_snapshot_digest,
        plan.source_prepared_inventory_digest,
        plan.coverage_digest,
        plan.plan_digest,
    )
    if (
        (
            bundle.source_snapshot_digest,
            bundle.source_prepared_inventory_digest,
            bundle.coverage_digest,
            bundle.representative_plan_digest,
        )
        != expected_lineage
        or bundle.authorized_plan_digest != plan.source_snapshot_digest
        or bundle.profile_digest != plan.coverage_digest
        or bundle.semantic_inventory_plan != plan.semantic_inventory_plan
        or bundle.acquisition_requests != plan.acquisition_requests
        or bundle.max_acquisition_concurrency != plan.max_acquisition_concurrency
    ):
        raise ValueError("representative acquisition lineage does not match fresh plan")


def _optional_digest_value(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"acquisition plan bundle {field_name} is invalid")
    return value


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


@dataclass(frozen=True, kw_only=True)
class SupervisedNoResponseClosureAuthority:
    """已停止 provider 进程后，唯一允许零网络闭合的 typed authority。"""

    schema_version: str
    inventory_digest: str
    bank_root_id: str
    manifest_digest: str
    budget_digest: str
    authorization_digest: str
    acquisition_output_root_digest: str
    target_inventory_entry_ids: tuple[str, ...]
    target_bindings: tuple[dict[str, object], ...]
    stop_evidence: dict[str, object]
    stop_evidence_digest: str
    provider_calls_made: int
    authority_digest: str

    def digest_preimage(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "inventory_digest": self.inventory_digest,
            "bank_root_id": self.bank_root_id,
            "manifest_digest": self.manifest_digest,
            "budget_digest": self.budget_digest,
            "authorization_digest": self.authorization_digest,
            "acquisition_output_root_digest": self.acquisition_output_root_digest,
            "target_inventory_entry_ids": list(self.target_inventory_entry_ids),
            "target_bindings": [dict(item) for item in self.target_bindings],
            "stop_evidence": dict(self.stop_evidence),
            "stop_evidence_digest": self.stop_evidence_digest,
            "provider_calls_made": self.provider_calls_made,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.digest_preimage(), "authority_digest": self.authority_digest}


@dataclass(frozen=True, kw_only=True)
class SupervisedNoResponseClosureReceipt:
    schema_version: str
    authority_digest: str
    closed_inventory_entry_ids: tuple[str, ...]
    terminal_entry_digests: tuple[str, ...]
    provider_calls_made: int
    receipt_digest: str

    def digest_preimage(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authority_digest": self.authority_digest,
            "closed_inventory_entry_ids": list(self.closed_inventory_entry_ids),
            "terminal_entry_digests": list(self.terminal_entry_digests),
            "provider_calls_made": self.provider_calls_made,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.digest_preimage(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True, kw_only=True)
class ExistingSettledAttempt:
    """只读绑定一个已有 settled provider attempt 的不可变证据。"""

    attempt_id: str
    attempt_index: int
    source_inventory_entry_id: str
    source_entry_id: str
    source_sample_slot_index: int
    source_replacement_slot: int
    request_identity_digest: str
    request_artifact_ref: str
    request_artifact_digest: str
    response_artifact_ref: str
    response_artifact_digest: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    api_latency_ms: int | None
    cost_cny: Decimal | None
    provider_family: str
    model_id: str
    provider_config_digest: str
    terminal_kind: str
    failure_kind: str | None
    terminal_selected: bool
    retry_reason: str | None
    requeue_reason: str | None
    ledger_ref: str
    ledger_record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.attempt_index) is not int
            or self.attempt_index < 0
            or type(self.source_sample_slot_index) is not int
            or self.source_sample_slot_index < 0
            or type(self.source_replacement_slot) is not int
            or self.source_replacement_slot != self.attempt_index
        ):
            raise ValueError("existing attempt index identity drifted")
        required = (
            self.attempt_id,
            self.source_inventory_entry_id,
            self.source_entry_id,
            self.request_artifact_ref,
            self.response_artifact_ref,
            self.provider_family,
            self.model_id,
            self.ledger_ref,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise ValueError("existing attempt identity is incomplete")
        digests = (
            self.request_identity_digest,
            self.request_artifact_digest,
            self.response_artifact_digest,
            self.provider_config_digest,
            self.ledger_record_digest,
        )
        if any(
            not isinstance(value, str) or not value.startswith("sha256:")
            for value in digests
        ):
            raise ValueError("existing attempt digest identity is invalid")
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if all(value is None for value in token_values):
            if self.cost_cny is not None:
                raise ValueError("missing attempt usage cannot have derived cost")
        elif (
            any(type(value) is not int or value < 0 for value in token_values)
            or self.total_tokens != self.input_tokens + self.output_tokens
            or self.cost_cny is None
            or self.cost_cny < 0
        ):
            raise ValueError("existing attempt usage/cost identity drifted")
        if (
            self.api_latency_ms is not None
            and (type(self.api_latency_ms) is not int or self.api_latency_ms < 0)
        ):
            raise ValueError("existing attempt latency is invalid")
        if self.terminal_kind not in {"success", "provider_failure"}:
            raise ValueError("existing attempt terminal kind is invalid")
        if type(self.terminal_selected) is not bool:
            raise ValueError("existing attempt terminal selection is invalid")
        for reason in (self.failure_kind, self.retry_reason, self.requeue_reason):
            if reason is not None and (not isinstance(reason, str) or not reason):
                raise ValueError("existing attempt reason is invalid")

    def to_dict(self) -> dict[str, object]:
        body = asdict(self)
        body["cost_cny"] = None if self.cost_cny is None else str(self.cost_cny)
        return body


def load_ordered_existing_settled_attempts(
    *,
    resolver: ResponseBankResolver,
    budget_ledger: PaperBudgetLedger,
    case_record_digest: str,
    planned_ai_unit_id: str,
    sample_slot_index: int,
) -> tuple[ExistingSettledAttempt, ...]:
    """从已有 bank/ledger 加载一个 Exp1 logical unit 的全部真实 attempts。"""

    if type(resolver) is not ResponseBankResolver:
        raise TypeError("existing attempts require an exact response bank resolver")
    if type(budget_ledger) is not PaperBudgetLedger:
        raise TypeError("existing attempts require an exact paper budget ledger")
    if (
        not isinstance(case_record_digest, str)
        or not case_record_digest.startswith("sha256:")
        or not isinstance(planned_ai_unit_id, str)
        or not planned_ai_unit_id
        or type(sample_slot_index) is not int
        or sample_slot_index < 0
    ):
        raise ValueError("existing attempt logical unit identity is invalid")
    manifest = resolver.index.manifest
    rows = tuple(
        row
        for row in resolver.index.inventory_rows
        if row.case_record_digest == case_record_digest
        and row.planned_ai_unit_id == planned_ai_unit_id
        and row.sample_slot_index == sample_slot_index
    )
    if not rows:
        raise AcquisitionIdentityError("existing attempt logical unit is missing")
    logical_request_identities = {
        (
            row.body_digest,
            row.provider_config_digest,
            row.prompt_profile_digest,
            row.prompt_admission_profile_digest,
            row.plugin_version,
        )
        for row in rows
    }
    if len(logical_request_identities) != 1:
        raise AcquisitionIdentityError("existing attempt request identity drifted")

    entries_by_inventory = {
        entry.inventory_entry_id: entry for entry in resolver.index.entries
    }
    reservations = tuple(
        record
        for record in budget_ledger.list_reservations()
        if record.inventory_digest == manifest.inventory_digest
    )
    reservations_by_inventory = {
        record.inventory_entry_id: record for record in reservations
    }
    if len(reservations_by_inventory) != len(reservations):
        raise AcquisitionIdentityError("existing attempt ledger identity is ambiguous")
    target_inventory_ids = {row.inventory_entry_id for row in rows}
    if target_inventory_ids - set(reservations_by_inventory):
        raise AcquisitionIdentityError("existing attempt ledger coverage is incomplete")
    target_reacquisitions = tuple(
        record
        for record in budget_ledger.list_reacquisitions()
        if record.inventory_digest == manifest.inventory_digest
        and record.inventory_entry_id in target_inventory_ids
    )
    if target_reacquisitions:
        raise AcquisitionIdentityError(
            "existing attempt reacquisition/ambiguity requires manual closure"
        )

    candidates: list[ExistingSettledAttempt] = []
    for row in rows:
        entry = entries_by_inventory.get(row.inventory_entry_id)
        record = reservations_by_inventory[row.inventory_entry_id]
        if entry is None:
            raise AcquisitionIdentityError("existing attempt response entry is missing")
        terminal_entry_digest = canonical_digest(entry.to_dict())
        if (
            record.state != "settled"
            or record.semantic_slot_key != row.semantic_slot_key
            or record.terminal_ref != terminal_entry_digest
            or record.terminal_kind != entry.terminal_kind
            or entry.inventory_digest != manifest.inventory_digest
        ):
            raise AcquisitionIdentityError("existing attempt ledger terminal drifted")
        role_bodies = {
            role: _read_existing_attempt_role(resolver, entry, role)
            for role in (
                "acquisition_attempt",
                "usage",
                "latency",
                "pricing",
                "provenance",
                "model_record",
            )
        }
        acquisition = role_bodies["acquisition_attempt"]
        _validate_existing_acquisition_role(acquisition)
        encoded_attempt_index = acquisition.get(
            "attempt_index", row.replacement_slot
        )
        expected_attempt_id = canonical_digest(
            {
                "inventory_digest": manifest.inventory_digest,
                "inventory_entry_id": row.inventory_entry_id,
                "attempt_kind": "primary",
            }
        )
        attempt_id = acquisition.get("attempt_id")
        if (
            type(encoded_attempt_index) is not int
            or encoded_attempt_index < 0
            or encoded_attempt_index != row.replacement_slot
            or attempt_id != expected_attempt_id
            or entry.acquisition_state_ref != attempt_id
            or acquisition.get("linked_ambiguous_attempt_id") is not None
            or acquisition.get("terminal_kind") != entry.terminal_kind
        ):
            raise AcquisitionIdentityError("existing attempt acquisition identity drifted")
        input_tokens, output_tokens, total_tokens = _existing_attempt_usage(
            role_bodies["usage"]
        )
        latency_ms = _existing_attempt_latency(role_bodies["latency"])
        cost_cny = _existing_attempt_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            pricing=role_bodies["pricing"],
        )
        if input_tokens is None:
            if record.usage_missing is not True:
                raise AcquisitionIdentityError(
                    "existing attempt missing usage ledger drifted"
                )
        elif (
            record.usage_missing is not False
            or record.charged_tokens != total_tokens
            or record.cost_estimate != cost_cny
        ):
            raise AcquisitionIdentityError(
                "existing attempt ledger resource accounting drifted"
            )
        provenance = role_bodies["provenance"]
        provider_family = provenance.get("provider_family")
        if (
            not isinstance(provider_family, str)
            or not provider_family
            or provider_family != record.provider_family
            or provenance.get("provider_config_digest")
            != row.provider_config_digest
            or provenance.get("inference_request_digest")
            != row.inference_request_digest
            or provenance.get("transport_call_count") != 1
            or provenance.get("secret_persisted") is not False
        ):
            raise AcquisitionIdentityError("existing attempt provider identity drifted")
        model_id = role_bodies["model_record"].get("configured_model")
        if not isinstance(model_id, str) or not model_id:
            raise AcquisitionIdentityError("existing attempt model identity drifted")
        request_locator = _existing_attempt_locator(entry, "request_body")
        response_role = (
            "raw_output" if entry.terminal_kind == "success" else "provider_failure"
        )
        response_locator = _existing_attempt_locator(entry, response_role)
        # locator identity 本身不足以证明 bytes 尚未漂移；两个原始 artifacts
        # 都必须通过 resolver 的流式 digest 校验后才可进入 ordered authority。
        resolver.read_verified(request_locator)
        resolver.read_verified(response_locator)
        ledger_body = _existing_attempt_ledger_body(record)
        candidates.append(
            ExistingSettledAttempt(
                attempt_id=attempt_id,
                attempt_index=encoded_attempt_index,
                source_inventory_entry_id=row.inventory_entry_id,
                source_entry_id=entry.entry_id,
                source_sample_slot_index=row.sample_slot_index,
                source_replacement_slot=row.replacement_slot,
                request_identity_digest=row.inference_request_digest,
                request_artifact_ref=(
                    f"response-bank:{manifest.bank_root_id}:{entry.entry_id}:request_body"
                ),
                request_artifact_digest=request_locator.object_digest,
                response_artifact_ref=(
                    f"response-bank:{manifest.bank_root_id}:{entry.entry_id}:"
                    f"{response_role}"
                ),
                response_artifact_digest=response_locator.object_digest,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                api_latency_ms=latency_ms,
                cost_cny=cost_cny,
                provider_family=provider_family,
                model_id=model_id,
                provider_config_digest=row.provider_config_digest,
                terminal_kind=entry.terminal_kind,
                failure_kind=_optional_existing_attempt_reason(
                    acquisition, "failure_kind"
                ),
                terminal_selected=False,
                retry_reason=_optional_existing_attempt_reason(
                    acquisition, "retry_reason"
                ),
                requeue_reason=_optional_existing_attempt_reason(
                    acquisition, "requeue_reason"
                ),
                ledger_ref=(
                    "paper_budget_ledger.reservation:"
                    f"{manifest.inventory_digest}:{row.inventory_entry_id}"
                ),
                ledger_record_digest=canonical_digest(ledger_body),
            )
        )
    ordered = tuple(sorted(candidates, key=lambda attempt: attempt.attempt_index))
    if tuple(attempt.attempt_index for attempt in ordered) != tuple(
        range(len(ordered))
    ) or len({attempt.attempt_id for attempt in ordered}) != len(ordered):
        raise AcquisitionIdentityError("existing attempt order/index drifted")
    provider_models = {
        (attempt.provider_family, attempt.model_id) for attempt in ordered
    }
    if len(provider_models) != 1:
        raise AcquisitionIdentityError("existing attempt provider/model sequence drifted")
    return tuple(
        replace(attempt, terminal_selected=index == len(ordered) - 1)
        for index, attempt in enumerate(ordered)
    )


def _read_existing_attempt_role(
    resolver: ResponseBankResolver,
    entry: ResponseBankEntry,
    role: str,
) -> dict[str, Any]:
    locator = _existing_attempt_locator(entry, role)
    try:
        value = json.loads(resolver.read_verified(locator).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionIdentityError(
            f"existing attempt {role} artifact is invalid"
        ) from exc
    if not isinstance(value, Mapping):
        raise AcquisitionIdentityError(
            f"existing attempt {role} artifact is not an object"
        )
    return dict(value)


def _existing_attempt_locator(
    entry: ResponseBankEntry,
    role: str,
) -> ExternalBankObjectLocator:
    matches = tuple(
        locator for locator in entry.object_locators if locator.object_role == role
    )
    if len(matches) != 1:
        raise AcquisitionIdentityError(
            f"existing attempt {role} locator identity drifted"
        )
    return matches[0]


def _validate_existing_acquisition_role(value: Mapping[str, Any]) -> None:
    base_fields = {
        "schema_version",
        "attempt_id",
        "linked_ambiguous_attempt_id",
        "requested_at",
        "terminal_kind",
        "failure_kind",
    }
    optional_fields = {"attempt_index", "retry_reason", "requeue_reason"}
    if (
        value.get("schema_version")
        != "tokenshare.response_bank_acquisition_attempt.v1"
        or not base_fields <= set(value)
        or set(value) - base_fields - optional_fields
    ):
        raise AcquisitionIdentityError("existing acquisition attempt schema drifted")


def _optional_existing_attempt_reason(
    value: Mapping[str, Any],
    field_name: str,
) -> str | None:
    reason = value.get(field_name)
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise AcquisitionIdentityError(
            f"existing attempt {field_name} identity drifted"
        )
    return reason


def _existing_attempt_usage(
    value: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None]:
    if value.get("schema_version") != "tokenshare.response_bank_usage.v1":
        raise AcquisitionIdentityError("existing attempt usage schema drifted")
    status = value.get("usage_status")
    usage = value.get("usage")
    if status == "usage_missing" and usage is None:
        return None, None, None
    if status != "reported" or not isinstance(usage, Mapping):
        raise AcquisitionIdentityError("existing attempt usage status drifted")
    result = (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    if (
        any(type(token_count) is not int or token_count < 0 for token_count in result)
        or result[2] != result[0] + result[1]
    ):
        raise AcquisitionIdentityError("existing attempt token accounting drifted")
    return result


def _existing_attempt_latency(value: Mapping[str, Any]) -> int | None:
    schema = value.get("schema_version")
    if schema == "tokenshare.response_bank_latency.v2":
        evidence_kind = value.get("evidence_kind")
        common = {
            "schema_version",
            "evidence_kind",
            "latency_ms",
            "latency_missing",
            "timing_source",
        }
        evidence_fields = (
            {"hard_deadline_evidence_digest", "observed_wall_clock_ms"}
            if evidence_kind == "hard_deadline_child"
            else {
                "observed_inflight_lower_bound_ms",
                "unchanged_observation_window_ms",
                "stop_evidence_digest",
            }
            if evidence_kind == "supervised_stopped_attempt"
            else set()
        )
        if not evidence_fields or set(value) != common | evidence_fields:
            raise AcquisitionIdentityError(
                "existing attempt latency v2 fields drifted"
            )
    elif schema != "tokenshare.response_bank_latency.v1":
        raise AcquisitionIdentityError("existing attempt latency schema drifted")
    latency_ms = value.get("latency_ms")
    if latency_ms is None:
        if schema == "tokenshare.response_bank_latency.v2" and value.get(
            "latency_missing"
        ) is not True:
            raise AcquisitionIdentityError(
                "existing attempt latency missingness drifted"
            )
        return None
    if type(latency_ms) is not int or latency_ms < 0:
        raise AcquisitionIdentityError("existing attempt latency drifted")
    return latency_ms


def _existing_attempt_cost(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: Mapping[str, Any],
) -> Decimal | None:
    if pricing.get("schema_version") != "tokenshare.response_bank_pricing.v1":
        raise AcquisitionIdentityError("existing attempt pricing schema drifted")
    if input_tokens is None or output_tokens is None:
        return None
    try:
        input_rate = Decimal(str(pricing["input_per_million_tokens"]))
        output_rate = Decimal(str(pricing["output_per_million_tokens"]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise AcquisitionIdentityError("existing attempt pricing identity drifted") from exc
    if pricing.get("currency") != "CNY" or input_rate < 0 or output_rate < 0:
        raise AcquisitionIdentityError("existing attempt pricing identity drifted")
    return (
        Decimal(input_tokens) * input_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)


def _existing_attempt_ledger_body(record: Any) -> dict[str, object]:
    return {
        "schema_version": "tokenshare.paper_budget_existing_attempt_record.v1",
        "record_kind": "reservation",
        "inventory_digest": record.inventory_digest,
        "inventory_entry_id": record.inventory_entry_id,
        "semantic_slot_key": record.semantic_slot_key,
        "state": record.state,
        "token_upper_bound": record.token_upper_bound,
        "cost_upper_bound": str(record.cost_upper_bound),
        "provider_family": record.provider_family,
        "terminal_ref": record.terminal_ref,
        "terminal_kind": record.terminal_kind,
        "charged_tokens": record.charged_tokens,
        "cost_estimate": (
            None if record.cost_estimate is None else str(record.cost_estimate)
        ),
        "usage_missing": record.usage_missing,
        "revision": record.revision,
    }


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
    """把 results-first bundle 绑定到显式 authorization，而非 paid receipt。"""

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
        authorization_kind=authorization.authorization_kind,
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
        accepted_authorities = {
            RESULTS_FIRST_AUTHORIZATION_KIND: (
                RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION,
                RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION,
            ),
            LEGACY_RESULTS_FIRST_AUTHORIZATION_KIND: (
                LEGACY_RESULTS_FIRST_ACQUISITION_AUTHORIZATION_SCHEMA_VERSION,
                LEGACY_RESULTS_FIRST_ACQUISITION_MARKER_SCHEMA_VERSION,
            ),
        }
        expected_schemas = accepted_authorities.get(facility.authorization_kind)
        if expected_schemas is None:
            raise AcquisitionAuthorizationError(
                "results-first acquisition authorization kind mismatch"
            )
        if (
            facility.schema_version,
            facility.marker.schema_version,
        ) != expected_schemas or facility.authorization_state != (
            facility.authorization_kind
        ):
            raise AcquisitionAuthorizationError(
                "results-first acquisition authorization schema mismatch"
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
        api_key = self.secret_resolver(request.api_key_env)
        if not isinstance(api_key, str) or not api_key:
            raise AcquisitionAuthorizationError("resolved acquisition secret is missing")
        lifecycle.append("secret_resolved")
        dispatch_intent_marked = False

        def mark_dispatch_intent_after_transport_ready() -> None:
            nonlocal dispatch_intent_marked
            if dispatch_intent_marked:
                raise AcquisitionIdentityError(
                    "provider transport-start callback repeated"
                )
            self.budget_ledger.mark_dispatch_intent(
                self.inventory_digest, row.inventory_entry_id
            )
            dispatch_intent_marked = True
            lifecycle.append("dispatch_intent")
            self._crash("dispatch_intent")

        try:
            evidence = dispatch_prepared_request_once(
                prepared_request=prepared,
                provider_family=request.provider_family,
                transport=self.transport,
                api_key=api_key,
                timeout_seconds=request.timeout_seconds,
                on_transport_start=mark_dispatch_intent_after_transport_ready,
            )
        finally:
            del api_key
        if evidence.transport_call_count == 0:
            if dispatch_intent_marked:
                raise AcquisitionIdentityError(
                    "pre-dispatch failure followed a dispatch intent"
                )
            deadline_proof = _validated_pre_dispatch_failure_evidence(
                evidence,
                expected_hard_total_seconds=request.timeout_seconds,
            )
            intent_path, intent_body, intent_encoded = (
                self._persist_pre_dispatch_release_intent(
                    request=request,
                    row=row,
                    prepared=prepared,
                    reservation_revision=decision.reservation.revision,
                    evidence=evidence,
                    hard_deadline_evidence=deadline_proof,
                )
            )
            lifecycle.append("pre_dispatch_release_intent_committed")
            self._crash("pre_dispatch_release_intent_committed")
            if not self.budget_ledger.release_reserved(
                self.inventory_digest, row.inventory_entry_id
            ):
                raise AcquisitionIdentityError(
                    "pre-dispatch reservation could not be safely released"
                )
            lifecycle.append("pre_dispatch_reservation_released")
            self._crash("pre_dispatch_reservation_released")
            self._commit_pre_dispatch_release_intent(
                intent_path=intent_path,
                intent_body=intent_body,
                intent_encoded=intent_encoded,
            )
            lifecycle.append("pre_dispatch_release_audited")
            return AcquisitionResult(
                status="pre_dispatch_failure",
                entry=None,
                lifecycle_order=tuple(lifecycle),
                transport_invoked=False,
                failure_kind=evidence.failure_kind,
                attempt_id=None,
            )
        if not dispatch_intent_marked:
            raise AcquisitionIdentityError(
                "provider call evidence is missing dispatch intent"
            )
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

    def _persist_pre_dispatch_release_intent(
        self,
        *,
        request: AcquisitionRequest,
        row: ResponseBankInventoryRow,
        prepared: PreparedOutboundRequest,
        reservation_revision: int,
        evidence: PreparedDispatchEvidence,
        hard_deadline_evidence: Mapping[str, object],
    ) -> tuple[Path, dict[str, object], bytes]:
        preimage: dict[str, object] = {
            "schema_version": "tokenshare.pre_dispatch_release_intent.v1",
            "inventory_digest": self.inventory_digest,
            "inventory_entry_id": row.inventory_entry_id,
            "inventory_row_digest": canonical_digest(row.to_dict()),
            "inference_request_digest": prepared.inference_request_digest,
            "body_digest": prepared.body_digest,
            "provider_family": request.provider_family,
            "provider_config_digest": prepared.provider_config_digest,
            "timeout_seconds": request.timeout_seconds,
            "authorization_digest": self.authorization_digest,
            "reservation_revision_before_release": reservation_revision,
            "failure_kind": evidence.failure_kind,
            "hard_deadline_evidence": dict(hard_deadline_evidence),
            "hard_deadline_evidence_digest": canonical_digest(
                dict(hard_deadline_evidence)
            ),
            "provider_calls_made": 0,
            "terminal_published": False,
            "settled": False,
            "ledger_action": "safe_release_reserved_intent",
        }
        intent_digest = canonical_digest(preimage)
        body = {**preimage, "intent_digest": intent_digest}
        encoded = _canonical_json_file_bytes(body)
        audit_root = self.output_root / "pre_dispatch_release_audit"
        audit_path = audit_root / (
            f"{row.inventory_entry_id.removeprefix('sha256:')}.intent.v1.json"
        )
        with self._artifact_write_lock:
            audit_root.mkdir(parents=False, exist_ok=True)
            if audit_path.exists():
                if audit_path.read_bytes() != encoded:
                    raise AcquisitionIdentityError(
                        "pre-dispatch release intent identity drift"
                    )
            else:
                _write_new_bytes(audit_path, encoded)
        return audit_path, body, encoded

    def _commit_pre_dispatch_release_intent(
        self,
        *,
        intent_path: Path,
        intent_body: Mapping[str, object],
        intent_encoded: bytes,
    ) -> None:
        ref_path = intent_path.with_name(
            intent_path.name.replace(".intent.v1.json", ".commit_ref.v1.json")
        )
        ref_body = {
            "schema_version": "tokenshare.pre_dispatch_release_commit_ref.v1",
            "intent_filename": intent_path.name,
            "intent_digest": intent_body["intent_digest"],
            "intent_file_sha256": f"sha256:{sha256(intent_encoded).hexdigest()}",
            "intent_file_size": len(intent_encoded),
            "inventory_entry_id": intent_body["inventory_entry_id"],
            "ledger_absent_after_release": True,
            "provider_calls_made": 0,
        }
        encoded = _canonical_json_file_bytes(ref_body)
        with self._artifact_write_lock:
            if ref_path.exists():
                if ref_path.read_bytes() != encoded:
                    raise AcquisitionIdentityError(
                        "pre-dispatch release commit ref identity drift"
                    )
                return
            _write_new_bytes(ref_path, encoded)

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
        deadline_proof = _validated_hard_deadline_role_evidence(
            evidence,
            expected_hard_total_seconds=request.timeout_seconds,
        )
        deadline_digest = (
            None if deadline_proof is None else canonical_digest(deadline_proof)
        )
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
            failure_body: dict[str, object] = {
                "schema_version": (
                    "tokenshare.response_bank_provider_failure.v2"
                    if deadline_proof is not None
                    else "tokenshare.response_bank_provider_failure.v1"
                ),
                "failure_kind": evidence.failure_kind,
                "http_status": evidence.http_status,
                "message": evidence.error_message,
                "raw_response_json": None,
                "transport_call_count": evidence.transport_call_count,
            }
            if deadline_proof is not None:
                failure_body["evidence_kind"] = "hard_deadline_child"
                failure_body["hard_deadline_evidence"] = deadline_proof
            role_refs["provider_failure"] = self._save_role(
                "provider_failure",
                failure_body,
                request.requested_at,
            )
        usage_status = "reported" if evidence.usage is not None else "usage_missing"
        provenance = {
                "schema_version": (
                    "tokenshare.response_bank_provenance.v2"
                    if deadline_proof is not None
                    else "tokenshare.response_bank_provenance.v1"
                ),
                "provider_family": request.provider_family,
                "entry_id": prepared.entry_id,
                "provider_config_digest": prepared.provider_config_digest,
                "inference_request_digest": prepared.inference_request_digest,
                "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
                "transport_call_count": evidence.transport_call_count,
                "secret_persisted": False,
        }
        if deadline_proof is not None:
            provenance.update(
                {
                    "evidence_kind": "hard_deadline_child",
                    "hard_deadline_evidence_digest": deadline_digest,
                    "network_start_acknowledged": deadline_proof[
                        "network_start_acknowledged"
                    ],
                    "result_observed_before_deadline": deadline_proof[
                        "result_observed_before_deadline"
                    ],
                    "late_result_rejected": deadline_proof[
                        "late_result_rejected"
                    ],
                    "post_reap_late_result_absent": deadline_proof[
                        "post_reap_late_result_absent"
                    ],
                    "child_reaped": deadline_proof["child_reaped"],
                    "parent_only_evidence_consumer": deadline_proof[
                        "parent_only_evidence_consumer"
                    ],
                    "ephemeral_files_secret_free": deadline_proof[
                        "ephemeral_files_secret_free"
                    ],
                }
            )
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
        latency_body: dict[str, object] = {
            "schema_version": (
                "tokenshare.response_bank_latency.v2"
                if deadline_proof is not None
                else "tokenshare.response_bank_latency.v1"
            ),
            "latency_ms": evidence.latency_ms,
            "latency_missing": evidence.latency_ms is None,
            "timing_source": evidence.latency_timing_source,
        }
        if deadline_proof is not None:
            latency_body.update(
                {
                    "evidence_kind": "hard_deadline_child",
                    "hard_deadline_evidence_digest": deadline_digest,
                    "observed_wall_clock_ms": deadline_proof[
                        "observed_wall_clock_ms"
                    ],
                }
            )
        role_bodies = {
            "provenance": provenance,
            "usage": {
                "schema_version": "tokenshare.response_bank_usage.v1",
                "usage_status": usage_status,
                "usage": evidence.usage,
            },
            "latency": latency_body,
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
            transport_invoked=(evidence.transport_call_count == 1),
            failure_kind=evidence.failure_kind,
            attempt_id=attempt_id,
            linked_ambiguous_attempt_id=linked_ambiguous_attempt_id,
        )

    def _publish_supervised_no_response(
        self,
        *,
        request: AcquisitionRequest,
        authority: SupervisedNoResponseClosureAuthority,
        crash_hook: Callable[[str], None] | None,
    ) -> tuple[ResponseBankEntry, str]:
        """只提交已授权的无响应终态；这里绝不解析 secret 或调用 transport。"""

        row, prepared = self._validate_acquisition_request(request)
        binding = next(
            (
                item
                for item in authority.target_bindings
                if item["inventory_entry_id"] == row.inventory_entry_id
            ),
            None,
        )
        if binding is None:
            raise AcquisitionIdentityError(
                "supervised closure request is outside authority"
            )
        self._validate_supervised_no_response_binding(
            request=request,
            row=row,
            prepared=prepared,
            binding=binding,
        )
        existing = self._load_entry(row.inventory_entry_id)
        if existing is not None:
            if existing.terminal_kind != "provider_failure":
                raise AcquisitionIdentityError(
                    "supervised closure conflicts with existing terminal"
                )
            failure = self.read_role_json(existing, "provider_failure")
            if (
                failure.get("failure_kind") != "no_response"
                or failure.get("closure_authority_digest")
                != authority.authority_digest
            ):
                raise AcquisitionIdentityError(
                    "supervised closure terminal identity mismatch"
                )
            self._validate_supervised_no_response_terminal(
                request=request,
                authority=authority,
                binding=binding,
                entry=existing,
                require_settled=False,
            )
            self._reconcile_committed_entry(existing)
            existing_ref = self._entry_store.load_artifact_ref(
                self._entry_artifact_id(row.inventory_entry_id)
            )
            return existing, existing_ref.content_hash

        record = self.budget_ledger.get_reservation(
            self.inventory_digest, row.inventory_entry_id
        )
        if (
            record.state != binding["ledger_state"]
            or record.revision != binding["ledger_revision"]
            or record.terminal_ref is not None
        ):
            raise AcquisitionIdentityError(
                "supervised closure ledger state or revision drift"
            )
        request_ref = self._object_store.load_artifact_ref(
            prepared.body_digest.removeprefix("sha256:")
        )
        if request_ref.content_hash != prepared.body_digest:
            raise AcquisitionIdentityError(
                "supervised closure request artifact digest drift"
            )
        role_refs: dict[str, Any] = {"request_body": request_ref}
        role_bodies = self._supervised_no_response_role_bodies(
            request=request,
            row=row,
            prepared=prepared,
            authority=authority,
        )
        for role, body in role_bodies.items():
            role_refs[role] = self._save_role(role, body, request.requested_at)
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
            terminal_kind="provider_failure",
            object_locators=locators,
            acquisition_state_ref=self._primary_attempt_id(row),
        )
        with self._artifact_write_lock:
            entry_ref = self._entry_store.save_json(
                entry.to_dict(),
                artifact_id=self._entry_artifact_id(row.inventory_entry_id),
                artifact_type="ResponseBankEntry",
                artifact_schema_id="tokenshare.response_bank_entry",
                artifact_schema_version="v1",
                source={"kind": "supervised_no_response_closure"},
                metadata={"inventory_entry_id": row.inventory_entry_id},
                created_at=request.requested_at,
            )
        if crash_hook is not None:
            crash_hook("supervised_no_response_terminal_entry_committed")
        self.budget_ledger.publish_terminal(
            self.inventory_digest,
            row.inventory_entry_id,
            terminal_ref=entry_ref.content_hash,
            terminal_kind="provider_failure",
        )
        self.budget_ledger.reconcile_terminal(
            self.inventory_digest,
            row.inventory_entry_id,
            usage=None,
        )
        return entry, entry_ref.content_hash

    def _preflight_supervised_no_response(
        self,
        *,
        request: AcquisitionRequest,
        authority: SupervisedNoResponseClosureAuthority,
    ) -> None:
        """在任何 target 发布前只读校验当前 request/binding/ledger/artifact。"""

        row, prepared = self._validate_acquisition_request(request)
        binding = next(
            (
                item
                for item in authority.target_bindings
                if item["inventory_entry_id"] == row.inventory_entry_id
            ),
            None,
        )
        if binding is None:
            raise AcquisitionIdentityError(
                "supervised closure request is outside authority"
            )
        self._validate_supervised_no_response_binding(
            request=request,
            row=row,
            prepared=prepared,
            binding=binding,
        )
        existing = self._load_entry(row.inventory_entry_id)
        if existing is not None:
            self._validate_supervised_no_response_terminal(
                request=request,
                authority=authority,
                binding=binding,
                entry=existing,
                require_settled=False,
            )
            return
        record = self.budget_ledger.get_reservation(
            self.inventory_digest, row.inventory_entry_id
        )
        if (
            record.state != binding["ledger_state"]
            or record.revision != binding["ledger_revision"]
            or record.terminal_ref is not None
        ):
            raise AcquisitionIdentityError(
                "supervised closure ledger state or revision drift"
            )
        request_ref = self._object_store.load_artifact_ref(
            prepared.body_digest.removeprefix("sha256:")
        )
        if (
            request_ref.content_hash != prepared.body_digest
            or self._object_store.read_bytes(request_ref) != prepared.body_bytes
        ):
            raise AcquisitionIdentityError(
                "supervised closure request artifact digest drift"
            )

    def _supervised_no_response_role_bodies(
        self,
        *,
        request: AcquisitionRequest,
        row: ResponseBankInventoryRow,
        prepared: PreparedOutboundRequest,
        authority: SupervisedNoResponseClosureAuthority,
    ) -> dict[str, Mapping[str, object]]:
        provenance: dict[str, object] = {
            "schema_version": "tokenshare.response_bank_provenance.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "provider_family": request.provider_family,
            "entry_id": prepared.entry_id,
            "provider_config_digest": prepared.provider_config_digest,
            "inference_request_digest": prepared.inference_request_digest,
            "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
            "transport_call_count": 1,
            "closure_provider_call_count": 0,
            "closure_authority_digest": authority.authority_digest,
            "stop_evidence_digest": authority.stop_evidence_digest,
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
        return {
            "provider_failure": {
                "schema_version": "tokenshare.response_bank_provider_failure.v2",
                "evidence_kind": "supervised_stopped_attempt",
                "failure_kind": "no_response",
                "http_status": None,
                "message": (
                    "provider returned no terminal response before supervised stop"
                ),
                "raw_response_json": None,
                "transport_call_count": 1,
                "closure_provider_call_count": 0,
                "closure_authority_digest": authority.authority_digest,
                "stop_evidence_digest": authority.stop_evidence_digest,
            },
            "provenance": provenance,
            "usage": {
                "schema_version": "tokenshare.response_bank_usage.v1",
                "usage_status": "usage_missing",
                "usage": None,
            },
            "latency": {
                "schema_version": "tokenshare.response_bank_latency.v2",
                "evidence_kind": "supervised_stopped_attempt",
                "latency_ms": None,
                "latency_missing": True,
                "timing_source": "supervised_no_terminal_response",
                "observed_inflight_lower_bound_ms": authority.stop_evidence[
                    "observed_inflight_lower_bound_ms"
                ],
                "unchanged_observation_window_ms": authority.stop_evidence[
                    "unchanged_observation_window_ms"
                ],
                "stop_evidence_digest": authority.stop_evidence_digest,
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
                "attempt_id": self._primary_attempt_id(row),
                "linked_ambiguous_attempt_id": None,
                "requested_at": request.requested_at,
                "terminal_kind": "provider_failure",
                "failure_kind": "no_response",
            },
            "model_record": {
                "schema_version": "tokenshare.response_bank_model_record.v1",
                "configured_model": prepared.configured_model,
                "requested_model": str(prepared.body_obj.get("model")),
                "resolved_model": None,
                "response_model_status": "unavailable_provider_failure",
            },
        }

    def _validate_supervised_no_response_binding(
        self,
        *,
        request: AcquisitionRequest,
        row: ResponseBankInventoryRow,
        prepared: PreparedOutboundRequest,
        binding: Mapping[str, object],
    ) -> None:
        expected_fields = {
            "inventory_entry_id",
            "inventory_row_digest",
            "entry_id",
            "inference_request_digest",
            "request_body_digest",
            "provider_config_digest",
            "primary_attempt_id",
            "ledger_state",
            "ledger_revision",
            "reacquisition_count",
            "token_upper_bound",
            "cost_upper_bound",
        }
        static_expected: dict[str, object] = {
            "inventory_entry_id": row.inventory_entry_id,
            "inventory_row_digest": canonical_digest(row.to_dict()),
            "entry_id": row.entry_id,
            "inference_request_digest": prepared.inference_request_digest,
            "request_body_digest": prepared.body_digest,
            "provider_config_digest": prepared.provider_config_digest,
            "primary_attempt_id": self._primary_attempt_id(row),
            "reacquisition_count": 0,
            "token_upper_bound": request.token_upper_bound,
            "cost_upper_bound": str(request.cost_upper_bound),
        }
        if (
            set(binding) != expected_fields
            or any(binding.get(key) != value for key, value in static_expected.items())
            or binding.get("ledger_state") not in {"dispatch_intent", "ambiguous"}
            or type(binding.get("ledger_revision")) is not int
            or int(binding["ledger_revision"]) < 1
        ):
            raise AcquisitionIdentityError(
                "supervised closure target binding identity drift"
            )
        reacquisition_count = sum(
            1
            for item in self.budget_ledger.list_reacquisitions()
            if item.inventory_entry_id == row.inventory_entry_id
        )
        if reacquisition_count != 0:
            raise AcquisitionIdentityError(
                "supervised closure target reacquisition drift"
            )

    def _validate_supervised_no_response_terminal(
        self,
        *,
        request: AcquisitionRequest,
        authority: SupervisedNoResponseClosureAuthority,
        binding: Mapping[str, object],
        entry: ResponseBankEntry,
        require_settled: bool,
    ) -> str:
        row, prepared = self._validate_acquisition_request(request)
        self._validate_supervised_no_response_binding(
            request=request,
            row=row,
            prepared=prepared,
            binding=binding,
        )
        if (
            entry.inventory_digest != self.inventory_digest
            or entry.inventory_entry_id != row.inventory_entry_id
            or entry.inference_request_digest != row.inference_request_digest
            or entry.entry_id != row.entry_id
            or entry.terminal_kind != "provider_failure"
            or entry.acquisition_state_ref != self._primary_attempt_id(row)
            or self.read_role_bytes(entry, "request_body") != prepared.body_bytes
        ):
            raise AcquisitionIdentityError(
                "supervised closure terminal entry identity drift"
            )
        expected_roles = self._supervised_no_response_role_bodies(
            request=request,
            row=row,
            prepared=prepared,
            authority=authority,
        )
        for role, expected_body in expected_roles.items():
            if self.read_role_json(entry, role) != expected_body:
                raise AcquisitionIdentityError(
                    f"supervised closure terminal {role} identity drift"
                )
        entry_ref = self._entry_store.load_artifact_ref(
            self._entry_artifact_id(row.inventory_entry_id)
        )
        record = self.budget_ledger.get_reservation(
            self.inventory_digest, row.inventory_entry_id
        )
        if require_settled or record.state == "settled":
            if (
                record.state != "settled"
                or record.terminal_ref != entry_ref.content_hash
                or record.terminal_kind != "provider_failure"
                or record.usage_missing is not True
                or record.charged_tokens != request.token_upper_bound
                or record.cost_estimate != request.cost_upper_bound
            ):
                raise AcquisitionIdentityError(
                    "supervised closure settled ledger accounting drift"
                )
        elif record.state == "terminal_published":
            if (
                record.terminal_ref != entry_ref.content_hash
                or record.terminal_kind != "provider_failure"
            ):
                raise AcquisitionIdentityError(
                    "supervised closure terminal ledger reference drift"
                )
        elif (
            record.state != str(binding["ledger_state"])
            or record.revision != binding["ledger_revision"]
            or record.terminal_ref is not None
        ):
            raise AcquisitionIdentityError(
                "supervised closure terminal ledger transition drift"
            )
        return entry_ref.content_hash

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
                    result = self.acquire(request)
                    serial_results.append(result)
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
                    # 一个 wave 全部归档后才补新请求；terminal provider failure
                    # 属于该 slot 的不可变结果，不得阻塞其他独立 slots。
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
        released: list[str] = list(self._reconcile_pre_dispatch_release_intents())
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

    def _reconcile_pre_dispatch_release_intents(self) -> tuple[str, ...]:
        audit_root = self.output_root / "pre_dispatch_release_audit"
        if not audit_root.is_dir():
            return ()
        intent_paths = tuple(sorted(audit_root.glob("*.intent.v1.json")))
        for ref_path in audit_root.glob("*.commit_ref.v1.json"):
            expected_intent = ref_path.with_name(
                ref_path.name.replace(".commit_ref.v1.json", ".intent.v1.json")
            )
            if expected_intent not in intent_paths:
                raise AcquisitionIdentityError(
                    "pre-dispatch release publication is ref-only"
                )
        reconciled: list[str] = []
        reservations = {
            record.inventory_entry_id: record
            for record in self.budget_ledger.list_reservations()
        }
        expected_fields = {
            "schema_version",
            "inventory_digest",
            "inventory_entry_id",
            "inventory_row_digest",
            "inference_request_digest",
            "body_digest",
            "provider_family",
            "provider_config_digest",
            "timeout_seconds",
            "authorization_digest",
            "reservation_revision_before_release",
            "failure_kind",
            "hard_deadline_evidence",
            "hard_deadline_evidence_digest",
            "provider_calls_made",
            "terminal_published",
            "settled",
            "ledger_action",
            "intent_digest",
        }
        for intent_path in intent_paths:
            try:
                intent_encoded = intent_path.read_bytes()
                body = json.loads(intent_encoded.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AcquisitionIdentityError(
                    "pre-dispatch release intent is unreadable"
                ) from exc
            if (
                not isinstance(body, Mapping)
                or set(body) != expected_fields
                or body["schema_version"]
                != "tokenshare.pre_dispatch_release_intent.v1"
                or body["inventory_digest"] != self.inventory_digest
                or body["authorization_digest"] != self.authorization_digest
                or body["provider_calls_made"] != 0
                or type(body["provider_calls_made"]) is not int
                or body["terminal_published"] is not False
                or body["settled"] is not False
                or body["ledger_action"] != "safe_release_reserved_intent"
                or body["intent_digest"]
                != canonical_digest(
                    {key: value for key, value in body.items() if key != "intent_digest"}
                )
            ):
                raise AcquisitionIdentityError(
                    "pre-dispatch release intent identity drift"
                )
            inventory_entry_id = str(body["inventory_entry_id"])
            row = self._rows_by_id.get(inventory_entry_id)
            if (
                row is None
                or body["inventory_row_digest"] != canonical_digest(row.to_dict())
                or body["inference_request_digest"] != row.inference_request_digest
                or body["body_digest"] != row.body_digest
                or body["provider_config_digest"] != row.provider_config_digest
            ):
                raise AcquisitionIdentityError(
                    "pre-dispatch release intent request identity drift"
                )
            proof = body["hard_deadline_evidence"]
            evidence = PreparedDispatchEvidence(
                terminal_kind="provider_failure",
                failure_kind="executor_error",
                raw_response_json=None,
                content_text=None,
                reasoning_content=None,
                provider_response_id=None,
                finish_reason=None,
                usage=None,
                latency_ms=None,
                http_status=None,
                resolved_model=None,
                response_model_status="unavailable_provider_failure",
                error_message="pre-dispatch provider child bootstrap failure",
                transport_call_count=0,
                latency_timing_source="unavailable_pre_dispatch",
                hard_deadline_evidence=proof,
            )
            validated_proof = _validated_pre_dispatch_failure_evidence(
                evidence,
                expected_hard_total_seconds=int(body["timeout_seconds"]),
            )
            if body["hard_deadline_evidence_digest"] != canonical_digest(
                validated_proof
            ):
                raise AcquisitionIdentityError(
                    "pre-dispatch release intent proof digest drift"
                )
            record = reservations.get(inventory_entry_id)
            if record is not None:
                if (
                    record.state != "reserved"
                    or record.revision != body["reservation_revision_before_release"]
                    or not self.budget_ledger.release_reserved(
                        self.inventory_digest, inventory_entry_id
                    )
                ):
                    raise AcquisitionIdentityError(
                        "pre-dispatch release intent ledger drift"
                    )
                reservations.pop(inventory_entry_id, None)
            self._commit_pre_dispatch_release_intent(
                intent_path=intent_path,
                intent_body=body,
                intent_encoded=intent_encoded,
            )
            reconciled.append(inventory_entry_id)
        return tuple(reconciled)

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
                artifact_schema_version=(
                    "v2"
                    if str(body.get("schema_version", "")).endswith(".v2")
                    else "v1"
                ),
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


def persist_supervised_no_response_closure_authority(
    *,
    closure_root: str | Path,
    orchestrator: ResponseBankAcquisitionOrchestrator,
    requests: Sequence[AcquisitionRequest],
    target_inventory_entry_ids: Sequence[str],
    stop_evidence: Mapping[str, object],
) -> SupervisedNoResponseClosureAuthority:
    """把 supervisor stop 与 exact dispatch ledger 绑定为 write-once authority。"""

    if type(orchestrator) is not ResponseBankAcquisitionOrchestrator:
        raise TypeError("supervised closure orchestrator type mismatch")
    target_ids = tuple(target_inventory_entry_ids)
    normalized_requests = tuple(requests)
    if not target_ids or len(set(target_ids)) != len(target_ids):
        raise AcquisitionIdentityError(
            "supervised closure target identities are empty or duplicate"
        )
    request_by_id: dict[str, AcquisitionRequest] = {}
    for request in normalized_requests:
        row, _prepared = orchestrator._validate_acquisition_request(request)
        if row.inventory_entry_id in request_by_id:
            raise AcquisitionIdentityError("supervised closure request is duplicate")
        request_by_id[row.inventory_entry_id] = request
    if tuple(request_by_id) != target_ids:
        raise AcquisitionIdentityError(
            "supervised closure request order or target identity mismatch"
        )
    stop_body = _validate_supervised_no_response_stop_evidence(
        stop_evidence,
        target_inventory_entry_ids=target_ids,
    )
    observations = {
        str(item["inventory_entry_id"]): item
        for item in stop_body["target_ledger_observations"]
    }
    reacquisition_counts: dict[str, int] = {entry_id: 0 for entry_id in target_ids}
    for reacquisition in orchestrator.budget_ledger.list_reacquisitions():
        if reacquisition.inventory_entry_id in reacquisition_counts:
            reacquisition_counts[reacquisition.inventory_entry_id] += 1
    target_bindings: list[dict[str, object]] = []
    for inventory_entry_id in target_ids:
        request = request_by_id[inventory_entry_id]
        row, prepared = orchestrator._validate_acquisition_request(request)
        record = orchestrator.budget_ledger.get_reservation(
            orchestrator.inventory_digest, inventory_entry_id
        )
        observation = observations.get(inventory_entry_id)
        if observation is None:
            raise AcquisitionIdentityError(
                "supervised closure stop evidence target is missing"
            )
        if (
            record.state not in {"dispatch_intent", "ambiguous"}
            or record.state != observation["state"]
            or record.revision != observation["revision"]
            or record.terminal_ref is not None
            or observation["terminal_entry_observed"] is not False
            or reacquisition_counts[inventory_entry_id]
            != observation["reacquisition_count"]
            or reacquisition_counts[inventory_entry_id] != 0
            or orchestrator._load_entry(inventory_entry_id) is not None
        ):
            raise AcquisitionIdentityError(
                "supervised closure ledger revision, terminal, or reacquisition drift"
            )
        target_bindings.append(
            {
                "inventory_entry_id": inventory_entry_id,
                "inventory_row_digest": canonical_digest(row.to_dict()),
                "entry_id": row.entry_id,
                "inference_request_digest": prepared.inference_request_digest,
                "request_body_digest": prepared.body_digest,
                "provider_config_digest": prepared.provider_config_digest,
                "primary_attempt_id": orchestrator._primary_attempt_id(row),
                "ledger_state": record.state,
                "ledger_revision": record.revision,
                "reacquisition_count": 0,
                "token_upper_bound": request.token_upper_bound,
                "cost_upper_bound": str(request.cost_upper_bound),
            }
        )
    budget_digest = (
        orchestrator.paid_authorization.receipt.budget_digest
        if orchestrator.paid_authorization is not None
        else orchestrator.facility_authorization.budget_digest
    )
    provisional = SupervisedNoResponseClosureAuthority(
        schema_version=(
            "tokenshare.supervised_provider_no_response_closure_authority.v1"
        ),
        inventory_digest=orchestrator.inventory_digest,
        bank_root_id=orchestrator.bank_root_id,
        manifest_digest=orchestrator.manifest_digest,
        budget_digest=budget_digest,
        authorization_digest=orchestrator.authorization_digest,
        acquisition_output_root_digest=output_root_path_digest(
            orchestrator.output_root
        ),
        target_inventory_entry_ids=target_ids,
        target_bindings=tuple(target_bindings),
        stop_evidence=stop_body,
        stop_evidence_digest=str(stop_body["stop_evidence_digest"]),
        provider_calls_made=0,
        authority_digest="",
    )
    authority = replace(
        provisional,
        authority_digest=canonical_digest(provisional.digest_preimage()),
    )
    root = Path(closure_root).resolve()
    authority_path = root / SUPERVISED_NO_RESPONSE_AUTHORITY_FILENAME
    ref_path = root / SUPERVISED_NO_RESPONSE_AUTHORITY_REF_FILENAME
    if root.exists():
        loaded = _load_supervised_no_response_closure_authority(root)
        if loaded != authority:
            raise AcquisitionIdentityError(
                "supervised closure authority write-once identity drift"
            )
        return loaded
    root.mkdir(parents=True, exist_ok=False)
    encoded = _canonical_json_file_bytes(authority.to_dict())
    _write_new_bytes(authority_path, encoded)
    _write_new_bytes(
        ref_path,
        _canonical_json_file_bytes(
            {
                "schema_version": (
                    "tokenshare.supervised_provider_no_response_closure_authority_ref.v1"
                ),
                "authority_filename": SUPERVISED_NO_RESPONSE_AUTHORITY_FILENAME,
                "authority_digest": authority.authority_digest,
                "authority_file_sha256": f"sha256:{sha256(encoded).hexdigest()}",
                "authority_file_size": len(encoded),
                "provider_calls_made": 0,
            }
        ),
    )
    return authority


def apply_supervised_no_response_closure(
    *,
    closure_root: str | Path,
    orchestrator: ResponseBankAcquisitionOrchestrator,
    requests: Sequence[AcquisitionRequest],
    crash_hook: Callable[[str], None] | None = None,
) -> SupervisedNoResponseClosureReceipt:
    """消费 exact authority，零网络发布 provider_failure 并幂等结算。"""

    root = Path(closure_root).resolve()
    authority = _load_supervised_no_response_closure_authority(root)
    current_budget_digest = (
        orchestrator.paid_authorization.receipt.budget_digest
        if orchestrator.paid_authorization is not None
        else orchestrator.facility_authorization.budget_digest
    )
    if (
        authority.inventory_digest != orchestrator.inventory_digest
        or authority.bank_root_id != orchestrator.bank_root_id
        or authority.manifest_digest != orchestrator.manifest_digest
        or authority.authorization_digest != orchestrator.authorization_digest
        or authority.budget_digest != current_budget_digest
        or authority.acquisition_output_root_digest
        != output_root_path_digest(orchestrator.output_root)
    ):
        raise AcquisitionIdentityError(
            "supervised closure authority does not bind acquisition context"
        )
    normalized_requests = tuple(requests)
    request_ids = tuple(
        request.inventory_row.inventory_entry_id for request in normalized_requests
    )
    if request_ids != authority.target_inventory_entry_ids:
        raise AcquisitionIdentityError(
            "supervised closure requests do not match authority"
        )
    # 所有 target 必须先整体通过只读校验；禁止 target-1 已 settle 后才
    # 发现 target-2 身份漂移。
    for request in normalized_requests:
        orchestrator._preflight_supervised_no_response(
            request=request,
            authority=authority,
        )
    receipt_path = root / SUPERVISED_NO_RESPONSE_RECEIPT_FILENAME
    receipt_ref_path = root / SUPERVISED_NO_RESPONSE_RECEIPT_REF_FILENAME
    if receipt_ref_path.exists():
        receipt = _load_supervised_no_response_closure_receipt(root, authority)
        _revalidate_supervised_no_response_closure_receipt(
            orchestrator=orchestrator,
            authority=authority,
            receipt=receipt,
            requests=normalized_requests,
        )
        return receipt
    if receipt_path.exists():
        receipt, encoded = _read_supervised_no_response_closure_receipt_body(
            receipt_path, authority
        )
        _revalidate_supervised_no_response_closure_receipt(
            orchestrator=orchestrator,
            authority=authority,
            receipt=receipt,
            requests=normalized_requests,
        )
        _write_new_bytes(
            receipt_ref_path,
            _canonical_json_file_bytes(
                _supervised_no_response_receipt_ref_body(
                    receipt=receipt,
                    encoded=encoded,
                )
            ),
        )
        return receipt
    terminal_digests: list[str] = []
    for request in normalized_requests:
        _entry, entry_digest = orchestrator._publish_supervised_no_response(
            request=request,
            authority=authority,
            crash_hook=crash_hook,
        )
        terminal_digests.append(entry_digest)
    provisional = SupervisedNoResponseClosureReceipt(
        schema_version=(
            "tokenshare.supervised_provider_no_response_closure_receipt.v1"
        ),
        authority_digest=authority.authority_digest,
        closed_inventory_entry_ids=authority.target_inventory_entry_ids,
        terminal_entry_digests=tuple(terminal_digests),
        provider_calls_made=0,
        receipt_digest="",
    )
    receipt = replace(
        provisional,
        receipt_digest=canonical_digest(provisional.digest_preimage()),
    )
    _revalidate_supervised_no_response_closure_receipt(
        orchestrator=orchestrator,
        authority=authority,
        receipt=receipt,
        requests=normalized_requests,
    )
    encoded = _canonical_json_file_bytes(receipt.to_dict())
    _write_new_bytes(receipt_path, encoded)
    if crash_hook is not None:
        crash_hook("supervised_no_response_closure_receipt_committed")
    _write_new_bytes(
        receipt_ref_path,
        _canonical_json_file_bytes(
            _supervised_no_response_receipt_ref_body(
                receipt=receipt,
                encoded=encoded,
            )
        ),
    )
    return receipt


def _revalidate_supervised_no_response_closure_receipt(
    *,
    orchestrator: ResponseBankAcquisitionOrchestrator,
    authority: SupervisedNoResponseClosureAuthority,
    receipt: SupervisedNoResponseClosureReceipt,
    requests: Sequence[AcquisitionRequest],
) -> None:
    terminal_digests: list[str] = []
    if len(requests) != len(authority.target_bindings):
        raise AcquisitionIdentityError(
            "supervised closure receipt request count drift"
        )
    for request, binding in zip(
        requests, authority.target_bindings, strict=True
    ):
        entry = orchestrator._load_entry(
            request.inventory_row.inventory_entry_id
        )
        if entry is None:
            raise AcquisitionIdentityError(
                "supervised closure receipt terminal entry is missing"
            )
        terminal_digests.append(
            orchestrator._validate_supervised_no_response_terminal(
                request=request,
                authority=authority,
                binding=binding,
                entry=entry,
                require_settled=True,
            )
        )
    if tuple(terminal_digests) != receipt.terminal_entry_digests:
        raise AcquisitionIdentityError(
            "supervised closure receipt terminal digest order drift"
        )


def _validate_supervised_no_response_stop_evidence(
    value: Mapping[str, object],
    *,
    target_inventory_entry_ids: tuple[str, ...],
) -> dict[str, object]:
    body = dict(value)
    expected_fields = {
        "schema_version",
        "supervisor_session_id",
        "worker_pid",
        "target_inventory_entry_ids",
        "target_ledger_observations",
        "process_dead",
        "provider_terminal_observed",
        "terminal_entry_observed",
        "transport_connection_observed",
        "observation_check_count",
        "unchanged_observation_window_ms",
        "observed_inflight_lower_bound_ms",
        "stopped_at",
        "stop_evidence_digest",
    }
    if set(body) != expected_fields:
        raise AcquisitionIdentityError(
            "supervised closure stop evidence fields mismatch"
        )
    if body["schema_version"] != (
        "tokenshare.supervised_provider_no_response_stop_evidence.v1"
    ):
        raise AcquisitionIdentityError(
            "supervised closure stop evidence schema mismatch"
        )
    if (
        body["target_inventory_entry_ids"] != list(target_inventory_entry_ids)
        or type(body["process_dead"]) is not bool
        or body["process_dead"] is not True
        or type(body["provider_terminal_observed"]) is not bool
        or body["provider_terminal_observed"] is not False
        or type(body["terminal_entry_observed"]) is not bool
        or body["terminal_entry_observed"] is not False
        or type(body["worker_pid"]) is not int
        or body["worker_pid"] < 1
        or type(body["observation_check_count"]) is not int
        or body["observation_check_count"] < 2
        or type(body["unchanged_observation_window_ms"]) is not int
        or body["unchanged_observation_window_ms"] < 120_000
        or type(body["observed_inflight_lower_bound_ms"]) is not int
        or body["observed_inflight_lower_bound_ms"] < 720_000
        or type(body["transport_connection_observed"]) is not bool
        or body["transport_connection_observed"] is not True
    ):
        raise AcquisitionIdentityError(
            "supervised closure stop evidence is not terminal-safe"
        )
    raw_observations = body["target_ledger_observations"]
    if not isinstance(raw_observations, list) or len(raw_observations) != len(
        target_inventory_entry_ids
    ):
        raise AcquisitionIdentityError(
            "supervised closure target ledger observations mismatch"
        )
    observation_fields = {
        "inventory_entry_id",
        "state",
        "revision",
        "terminal_entry_observed",
        "reacquisition_count",
    }
    for expected_id, observation in zip(
        target_inventory_entry_ids, raw_observations, strict=True
    ):
        if (
            not isinstance(observation, Mapping)
            or set(observation) != observation_fields
            or observation["inventory_entry_id"] != expected_id
            or observation["state"] not in {"dispatch_intent", "ambiguous"}
            or type(observation["revision"]) is not int
            or observation["revision"] < 1
            or observation["terminal_entry_observed"] is not False
            or type(observation["reacquisition_count"]) is not int
            or observation["reacquisition_count"] != 0
        ):
            raise AcquisitionIdentityError(
                "supervised closure target ledger observation is invalid"
            )
    digest_preimage = {
        key: item for key, item in body.items() if key != "stop_evidence_digest"
    }
    if body["stop_evidence_digest"] != canonical_digest(digest_preimage):
        raise AcquisitionIdentityError(
            "supervised closure stop evidence digest mismatch"
        )
    return body


def _load_supervised_no_response_closure_authority(
    root: Path,
) -> SupervisedNoResponseClosureAuthority:
    path = root / SUPERVISED_NO_RESPONSE_AUTHORITY_FILENAME
    ref_path = root / SUPERVISED_NO_RESPONSE_AUTHORITY_REF_FILENAME
    try:
        encoded = path.read_bytes()
        body = json.loads(encoded.decode("utf-8"))
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionIdentityError(
            "supervised closure authority publication is missing or partial"
        ) from exc
    expected = set(SupervisedNoResponseClosureAuthority.__dataclass_fields__)
    if not isinstance(body, Mapping) or set(body) != expected:
        raise AcquisitionIdentityError("supervised closure authority fields mismatch")
    authority = SupervisedNoResponseClosureAuthority(
        schema_version=str(body["schema_version"]),
        inventory_digest=str(body["inventory_digest"]),
        bank_root_id=str(body["bank_root_id"]),
        manifest_digest=str(body["manifest_digest"]),
        budget_digest=str(body["budget_digest"]),
        authorization_digest=str(body["authorization_digest"]),
        acquisition_output_root_digest=str(body["acquisition_output_root_digest"]),
        target_inventory_entry_ids=tuple(body["target_inventory_entry_ids"]),
        target_bindings=tuple(dict(item) for item in body["target_bindings"]),
        stop_evidence=dict(body["stop_evidence"]),
        stop_evidence_digest=str(body["stop_evidence_digest"]),
        provider_calls_made=body["provider_calls_made"],
        authority_digest=str(body["authority_digest"]),
    )
    if (
        authority.schema_version
        != "tokenshare.supervised_provider_no_response_closure_authority.v1"
        or type(authority.provider_calls_made) is not int
        or authority.provider_calls_made != 0
        or authority.authority_digest
        != canonical_digest(authority.digest_preimage())
    ):
        raise AcquisitionIdentityError("supervised closure authority digest mismatch")
    expected_ref = {
        "schema_version": (
            "tokenshare.supervised_provider_no_response_closure_authority_ref.v1"
        ),
        "authority_filename": SUPERVISED_NO_RESPONSE_AUTHORITY_FILENAME,
        "authority_digest": authority.authority_digest,
        "authority_file_sha256": f"sha256:{sha256(encoded).hexdigest()}",
        "authority_file_size": len(encoded),
        "provider_calls_made": 0,
    }
    if ref != expected_ref:
        raise AcquisitionIdentityError("supervised closure authority ref mismatch")
    _validate_supervised_no_response_stop_evidence(
        authority.stop_evidence,
        target_inventory_entry_ids=authority.target_inventory_entry_ids,
    )
    if authority.stop_evidence_digest != authority.stop_evidence[
        "stop_evidence_digest"
    ]:
        raise AcquisitionIdentityError(
            "supervised closure stop evidence authority binding mismatch"
        )
    return authority


def load_supervised_no_response_closure_authority(
    closure_root: str | Path,
) -> SupervisedNoResponseClosureAuthority:
    """只读加载已提交的 closure authority，供 resume 精确选取 target。"""

    return _load_supervised_no_response_closure_authority(
        Path(closure_root).resolve()
    )


def _load_supervised_no_response_closure_receipt(
    root: Path,
    authority: SupervisedNoResponseClosureAuthority,
) -> SupervisedNoResponseClosureReceipt:
    path = root / SUPERVISED_NO_RESPONSE_RECEIPT_FILENAME
    ref_path = root / SUPERVISED_NO_RESPONSE_RECEIPT_REF_FILENAME
    receipt, encoded = _read_supervised_no_response_closure_receipt_body(
        path, authority
    )
    try:
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionIdentityError(
            "supervised closure receipt publication is missing or partial"
        ) from exc
    expected_ref = _supervised_no_response_receipt_ref_body(
        receipt=receipt,
        encoded=encoded,
    )
    if ref != expected_ref:
        raise AcquisitionIdentityError("supervised closure receipt ref mismatch")
    return receipt


def _read_supervised_no_response_closure_receipt_body(
    path: Path,
    authority: SupervisedNoResponseClosureAuthority,
) -> tuple[SupervisedNoResponseClosureReceipt, bytes]:
    try:
        encoded = path.read_bytes()
        body = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionIdentityError(
            "supervised closure receipt body is missing or invalid"
        ) from exc
    expected = set(SupervisedNoResponseClosureReceipt.__dataclass_fields__)
    if not isinstance(body, Mapping) or set(body) != expected:
        raise AcquisitionIdentityError("supervised closure receipt fields mismatch")
    receipt = SupervisedNoResponseClosureReceipt(
        schema_version=str(body["schema_version"]),
        authority_digest=str(body["authority_digest"]),
        closed_inventory_entry_ids=tuple(body["closed_inventory_entry_ids"]),
        terminal_entry_digests=tuple(body["terminal_entry_digests"]),
        provider_calls_made=body["provider_calls_made"],
        receipt_digest=str(body["receipt_digest"]),
    )
    if (
        receipt.schema_version
        != "tokenshare.supervised_provider_no_response_closure_receipt.v1"
        or receipt.authority_digest != authority.authority_digest
        or receipt.closed_inventory_entry_ids
        != authority.target_inventory_entry_ids
        or type(receipt.provider_calls_made) is not int
        or receipt.provider_calls_made != 0
        or receipt.receipt_digest != canonical_digest(receipt.digest_preimage())
    ):
        raise AcquisitionIdentityError("supervised closure receipt digest mismatch")
    return receipt, encoded


def _supervised_no_response_receipt_ref_body(
    *,
    receipt: SupervisedNoResponseClosureReceipt,
    encoded: bytes,
) -> dict[str, object]:
    return {
        "schema_version": (
            "tokenshare.supervised_provider_no_response_closure_receipt_ref.v1"
        ),
        "receipt_filename": SUPERVISED_NO_RESPONSE_RECEIPT_FILENAME,
        "receipt_digest": receipt.receipt_digest,
        "receipt_file_sha256": f"sha256:{sha256(encoded).hexdigest()}",
        "receipt_file_size": len(encoded),
        "authority_digest": receipt.authority_digest,
        "provider_calls_made": 0,
    }


def _canonical_json_file_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def _write_new_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()


def _validated_hard_deadline_role_evidence(
    evidence: PreparedDispatchEvidence,
    *,
    expected_hard_total_seconds: int,
) -> dict[str, object] | None:
    raw = evidence.hard_deadline_evidence
    if raw is None:
        if evidence.failure_kind == "no_response":
            raise AcquisitionIdentityError(
                "live no_response requires hard-deadline quiescence evidence"
            )
        return None
    if not isinstance(raw, Mapping):
        raise AcquisitionIdentityError("hard-deadline evidence must be a mapping")
    proof = dict(raw)
    required = {
        "schema_version",
        "deadline_enforced",
        "hard_total_seconds",
        "observed_wall_clock_ms",
        "child_pid",
        "child_exit_code",
        "terminate_attempted",
        "kill_attempted",
        "child_reaped",
        "network_start_acknowledged",
        "result_observed_before_deadline",
        "child_completed_before_parent_deadline",
        "result_commit_count",
        "accepted_result_commit_count",
        "late_result_rejected",
        "post_reap_late_result_absent",
        "parent_only_evidence_consumer",
        "ephemeral_files_secret_free",
        "request_file_sha256",
    }
    if not required.issubset(proof) or proof["schema_version"] != (
        "tokenshare.ai_api_hard_deadline_quiescence.v1"
    ):
        raise AcquisitionIdentityError("hard-deadline evidence schema mismatch")
    bool_fields = (
        "deadline_enforced",
        "terminate_attempted",
        "kill_attempted",
        "child_reaped",
        "network_start_acknowledged",
        "result_observed_before_deadline",
        "child_completed_before_parent_deadline",
        "late_result_rejected",
        "post_reap_late_result_absent",
        "parent_only_evidence_consumer",
        "ephemeral_files_secret_free",
    )
    int_fields = (
        "observed_wall_clock_ms",
        "child_pid",
        "child_exit_code",
        "result_commit_count",
        "accepted_result_commit_count",
    )
    hard_total_seconds = proof["hard_total_seconds"]
    hard_total_decimal = (
        Decimal(str(hard_total_seconds))
        if type(hard_total_seconds) in {int, float}
        else Decimal("NaN")
    )
    if (
        any(type(proof[field_name]) is not bool for field_name in bool_fields)
        or any(type(proof[field_name]) is not int for field_name in int_fields)
        or not hard_total_decimal.is_finite()
        or hard_total_decimal <= 0
        or hard_total_decimal != Decimal(expected_hard_total_seconds)
        or type(evidence.transport_call_count) is not int
        or evidence.transport_call_count not in {0, 1}
        or not isinstance(proof["request_file_sha256"], str)
        or not str(proof["request_file_sha256"]).startswith("sha256:")
        or proof["deadline_enforced"] is not True
        or proof["child_reaped"] is not True
        or proof["post_reap_late_result_absent"] is not True
        or proof["parent_only_evidence_consumer"] is not True
        or proof["ephemeral_files_secret_free"] is not True
    ):
        raise AcquisitionIdentityError("hard-deadline evidence is not quiescent")
    if evidence.failure_kind == "no_response":
        if (
            evidence.transport_call_count != 1
            or evidence.usage is not None
            or evidence.latency_ms is not None
            or evidence.latency_timing_source != "unknown_no_response"
            or proof["network_start_acknowledged"] is not True
            or proof["result_observed_before_deadline"] is not False
            or proof["accepted_result_commit_count"] != 0
        ):
            raise AcquisitionIdentityError(
                "live no_response hard-deadline accounting mismatch"
            )
    if evidence.terminal_kind == "success":
        if (
            evidence.transport_call_count != 1
            or proof["network_start_acknowledged"] is not True
            or proof["result_observed_before_deadline"] is not True
            or proof["child_completed_before_parent_deadline"] is not True
            or proof["accepted_result_commit_count"] != 1
            or proof["late_result_rejected"] is not False
        ):
            raise AcquisitionIdentityError(
                "on-time success hard-deadline evidence is inconsistent"
            )
    return proof


def _validated_pre_dispatch_failure_evidence(
    evidence: PreparedDispatchEvidence,
    *,
    expected_hard_total_seconds: int,
) -> dict[str, object]:
    proof = _validated_hard_deadline_role_evidence(
        evidence,
        expected_hard_total_seconds=expected_hard_total_seconds,
    )
    if (
        proof is None
        or evidence.terminal_kind != "provider_failure"
        or evidence.failure_kind != "executor_error"
        or evidence.transport_call_count != 0
        or evidence.usage is not None
        or evidence.latency_ms is not None
        or evidence.http_status is not None
        or evidence.resolved_model is not None
        or evidence.response_model_status != "unavailable_provider_failure"
        or evidence.latency_timing_source != "unavailable_pre_dispatch"
        or proof["network_start_acknowledged"] is not False
    ):
        raise AcquisitionIdentityError(
            "pre-dispatch failure evidence has provider accounting drift"
        )
    return proof


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
    condition_slot_keys: dict[str, set[str]] = {}
    case_refs_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    case_slot_keys_by_key: dict[tuple[str, str], set[str]] = {}

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
        _append_condition_ref(
            condition_refs,
            candidate,
            slot_key,
            condition_slot_keys=condition_slot_keys,
            case_refs_by_key=case_refs_by_key,
            case_slot_keys_by_key=case_slot_keys_by_key,
        )

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


def _full_current_pricing_authority_from_mapping(
    value: Mapping[str, object],
    *,
    selection_kind: str,
) -> FullCurrentAcquisitionPricingAuthority:
    """将 A 中的 JSON pricing projection 收窄为 typed Full authority。"""

    raw_pricing = value.get("pricing_by_entry")
    if not isinstance(raw_pricing, Mapping):
        raise ValueError("Full current pricing authority pricing map is invalid")
    pricing_by_entry: dict[str, FrozenPricing] = {}
    try:
        for entry_id, body in raw_pricing.items():
            if not isinstance(entry_id, str) or not isinstance(body, Mapping):
                raise ValueError("Full current pricing authority pricing map is invalid")
            pricing_by_entry[entry_id] = FrozenPricing(
                currency=str(body["currency"]),
                input_per_million_tokens=Decimal(
                    str(
                        body["input_per_million_tokens"]
                        if "input_per_million_tokens" in body
                        else body["uncached_input_per_million_tokens"]
                    )
                ),
                output_per_million_tokens=Decimal(str(body["output_per_million_tokens"])),
            )
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("Full current pricing authority pricing map is invalid") from exc
    required = (
        "provider_config_id",
        "source_provider_config_digest",
        "source_execution_identity_digest",
        "provider_config_digest",
        "execution_identity_digest",
        "authority_digest",
    )
    if any(not isinstance(value.get(key), str) for key in required):
        raise ValueError("Full current pricing authority binding is incomplete")
    return FullCurrentAcquisitionPricingAuthority(
        selection_kind=selection_kind,
        provider_config_id=str(value["provider_config_id"]),
        source_provider_config_digest=str(value["source_provider_config_digest"]),
        source_execution_identity_digest=str(value["source_execution_identity_digest"]),
        current_provider_config_digest=str(value["provider_config_digest"]),
        current_execution_identity_digest=str(value["execution_identity_digest"]),
        pricing_by_entry=pricing_by_entry,
        current_pricing_authority_digest=str(value["authority_digest"]),
        full_budget_approval_authority_digest=str(
            value.get("full_budget_approval_authority_digest", "sha256:" + "0" * 64)
        ),
        approved_exp1_budget_digest=str(
            value.get("approved_exp1_budget_digest", "sha256:" + "0" * 64)
        ),
    )


def prepare_results_first_acquisition_authority(
    *,
    full_snapshot: Any,
    full_prepared_inventory: Any,
    full_budget: Any,
    coverage: Any,
    execution_budget_projection: Any,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
    api_key_env_by_provider_family: Mapping[str, str],
    frozen_pricing_by_provider_family: Mapping[str, FrozenPricing],
    requested_at: str,
    max_acquisition_concurrency: int = 10,
    full_current_pricing_authority: FullCurrentAcquisitionPricingAuthority | None = None,
    # 兼容 Full authority 构造器的显式双 map 命名：formal/materializer 只消费
    # frozen source map，current map 仅由 typed pricing authority 描述。
    source_ai_api_configs: Mapping[str, Any] | None = None,
    current_pricing_authority: object | None = None,
    current_ai_api_configs: Mapping[str, Any] | None = None,
) -> ResultsFirstAcquisitionAuthority:
    """从调用方已选定的 typed coverage/projection 建立 acquisition authority。"""

    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_formal_plan import (
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
    )
    from tokenshare.experiments.paper_models import PaperBudgetResult

    if (
        type(full_snapshot) is not FormalPlanSnapshot
        or type(full_prepared_inventory) is not FormalPreparedRequestInventory
        or type(full_budget) is not PaperBudgetResult
        or type(coverage) is not FormalExecutionCoverage
        or type(execution_budget_projection) is not PaperExecutionBudgetProjection
    ):
        raise TypeError("results-first acquisition requires typed full authority")
    if (
        coverage.source_snapshot is not full_snapshot
        or execution_budget_projection.source_snapshot is not full_snapshot
        or execution_budget_projection.source_budget is not full_budget
        or execution_budget_projection.coverage is not coverage
    ):
        raise ValueError("results-first acquisition selection lineage drift")
    _validate_bound_representative_authority_objects(
        snapshot=full_snapshot,
        prepared_inventory=full_prepared_inventory,
        coverage=coverage,
    )
    if source_ai_api_configs is not None:
        # current config map remains available to formal-plan identity checks;
        # source map is retained separately for frozen provenance validation.
        source_ai_api_configs = dict(source_ai_api_configs)
    if current_pricing_authority is not None:
        if full_current_pricing_authority is not None:
            raise ValueError("duplicate Full current pricing authority")
        if isinstance(current_pricing_authority, Mapping):
            # Mapping 是本地 A projection 的输入格式；物化时仍会再次验证
            # source/current execution identity 和 pricing entry。
            full_current_pricing_authority = _full_current_pricing_authority_from_mapping(
                current_pricing_authority,
                selection_kind=coverage.selection_kind,
            )
        else:
            full_current_pricing_authority = current_pricing_authority
    plan = ResultsFirstAcquisitionPlan(
        full_snapshot=full_snapshot,
        full_prepared_inventory=full_prepared_inventory,
        full_budget=full_budget,
        coverage=coverage,
        execution_budget_projection=execution_budget_projection,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=Path(planning_artifact_root),
        api_key_env_by_provider_family=dict(api_key_env_by_provider_family),
        frozen_pricing_by_provider_family=dict(frozen_pricing_by_provider_family),
        requested_at=requested_at,
        max_acquisition_concurrency=max_acquisition_concurrency,
        full_current_pricing_authority=full_current_pricing_authority,
        source_ai_api_configs=source_ai_api_configs,
        current_ai_api_configs=current_ai_api_configs,
    )
    return ResultsFirstAcquisitionAuthority.create(
        full_snapshot=full_snapshot,
        full_prepared_inventory=full_prepared_inventory,
        full_budget=full_budget,
        coverage=coverage,
        execution_budget_projection=execution_budget_projection,
        plan=plan,
    )


def prepare_representative_acquisition_authority(
    *,
    dispatch_plans: Sequence[Any],
    catalog_manifest: PaperInputCatalogManifest,
    budget: Any,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
    api_key_env_by_provider_family: Mapping[str, str],
    frozen_pricing_by_provider_family: Mapping[str, FrozenPricing],
    requested_at: str,
    repeat_ids: Sequence[int] = (0,),
    max_acquisition_concurrency: int = 10,
) -> RepresentativeAcquisitionAuthority:
    """一次 full freeze 生成 canonical snapshot/inventory/coverage/plan。"""

    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_representative_coverage,
        freeze_paper_formal_plan_snapshot,
        freeze_paper_formal_prepared_request_inventory,
    )
    from tokenshare.experiments.paper_budget import project_paper_execution_budget

    artifact_root = Path(planning_artifact_root)
    plans = tuple(dispatch_plans)
    formal_suite_root = _formal_dispatch_suite_root(plans)
    snapshot = freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=formal_suite_root,
    )
    prepared_inventory = freeze_paper_formal_prepared_request_inventory(
        snapshot=snapshot,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=artifact_root / "full-prepared-inventory-freeze",
    )
    coverage = derive_paper_formal_representative_coverage(
        snapshot=snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        repeat_ids=repeat_ids,
    )
    projection = project_paper_execution_budget(
        snapshot=snapshot,
        budget=budget,
        coverage=coverage,
    )
    return prepare_results_first_acquisition_authority(
        full_snapshot=snapshot,
        full_prepared_inventory=prepared_inventory,
        full_budget=budget,
        coverage=coverage,
        execution_budget_projection=projection,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=artifact_root / "representative-slots",
        api_key_env_by_provider_family=api_key_env_by_provider_family,
        frozen_pricing_by_provider_family=frozen_pricing_by_provider_family,
        requested_at=requested_at,
        max_acquisition_concurrency=max_acquisition_concurrency,
    )


def build_results_first_unified_acquisition_plan(
    **keyword_arguments: Any,
) -> ResultsFirstAcquisitionPlan:
    """中性 plan API；selection 必须由调用方以 coverage/projection 明确传入。"""

    return prepare_results_first_acquisition_authority(
        **keyword_arguments
    ).plan


def materialize_results_first_unified_acquisition_plan(
    *,
    plan: ResultsFirstAcquisitionPlan,
) -> RepresentativeUnifiedAcquisitionPlan:
    """唯一 eager materializer；full/filtered 只由 plan.coverage 区分。"""

    if type(plan) is not ResultsFirstAcquisitionPlan:
        raise TypeError("typed results-first acquisition plan is required")
    plan.__post_init__()
    validated_authority = _mint_validated_representative_authority(
        snapshot=plan.full_snapshot,
        prepared_inventory=plan.full_prepared_inventory,
        coverage=plan.coverage,
    )
    return _build_representative_unified_acquisition_plan_from_validated_authority(
        validated_authority=validated_authority,
        catalog_manifest=plan.catalog_manifest,
        ai_api_configs=(
            plan.current_ai_api_configs
            if plan.current_ai_api_configs is not None
            else plan.ai_api_configs
        ),
        planning_artifact_root=plan.planning_artifact_root,
        api_key_env_by_provider_family=plan.api_key_env_by_provider_family,
        frozen_pricing_by_provider_family=plan.frozen_pricing_by_provider_family,
        requested_at=plan.requested_at,
        max_acquisition_concurrency=plan.max_acquisition_concurrency,
        full_current_pricing_authority=plan.full_current_pricing_authority,
        source_ai_api_configs=(
            plan.source_ai_api_configs
            if plan.source_ai_api_configs is not None
            else (
                plan.full_snapshot.ai_api_configs
                if plan.full_current_pricing_authority is not None
                else None
            )
        ),
        current_ai_api_configs=plan.current_ai_api_configs,
    )


def materialize_results_first_acquisition_bundle(
    *,
    plan: ResultsFirstAcquisitionPlan,
    bundle_root: str | Path,
    resume: bool,
) -> AcquisitionPlanBundle:
    """实际 acquisition 前按同一 lazy plan 物化或验证持久化 bundle。"""

    materialized = materialize_results_first_unified_acquisition_plan(plan=plan)
    if resume:
        return load_representative_acquisition_plan_bundle(
            bundle_root,
            plan=materialized,
        )
    return create_representative_acquisition_plan_bundle(
        bundle_root,
        plan=materialized,
    )


def build_representative_unified_acquisition_plan(
    *,
    snapshot: Any,
    prepared_inventory: Any,
    coverage: Any,
    dispatch_plans: Sequence[Any],
    catalog_manifest: PaperInputCatalogManifest,
    budget: Any,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
    api_key_env_by_provider_family: Mapping[str, str],
    frozen_pricing_by_provider_family: Mapping[str, FrozenPricing],
    requested_at: str,
    max_acquisition_concurrency: int = 10,
) -> RepresentativeUnifiedAcquisitionPlan:
    """重建并验证 canonical full authority 后抽取 representative slots。"""

    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_exp4_excluded_coverage,
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
        derive_paper_formal_representative_coverage,
        freeze_paper_formal_plan_snapshot,
        validate_formal_prepared_request_inventory,
    )

    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("snapshot must be a FormalPlanSnapshot")
    if type(prepared_inventory) is not FormalPreparedRequestInventory:
        raise TypeError("prepared_inventory must be a FormalPreparedRequestInventory")
    if type(coverage) is not FormalExecutionCoverage:
        raise TypeError("coverage must be a FormalExecutionCoverage")
    plans = tuple(dispatch_plans)
    artifact_root = Path(planning_artifact_root)
    formal_suite_root = _formal_dispatch_suite_root(plans)
    canonical_snapshot = freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=formal_suite_root,
    )
    _validate_exact_formal_snapshot_authority(
        supplied=snapshot,
        canonical=canonical_snapshot,
    )
    repeat_ids = tuple(
        dict.fromkeys(condition.repeat_id for condition in coverage.conditions)
    )
    if coverage.selection_kind == "full_exp1_exp3_exp5":
        canonical_coverage = derive_paper_formal_exp4_excluded_coverage(
            snapshot=canonical_snapshot,
            dispatch_plans=plans,
            catalog_manifest=catalog_manifest,
            selection="full",
        )
    elif coverage.selection_kind == "representative_exp1_exp3_exp5":
        canonical_coverage = derive_paper_formal_exp4_excluded_coverage(
            snapshot=canonical_snapshot,
            dispatch_plans=plans,
            catalog_manifest=catalog_manifest,
            selection="representative",
        )
    else:
        canonical_coverage = derive_paper_formal_representative_coverage(
            snapshot=canonical_snapshot,
            dispatch_plans=plans,
            catalog_manifest=catalog_manifest,
            repeat_ids=repeat_ids,
        )
    _validate_exact_formal_coverage_authority(
        supplied=coverage,
        supplied_snapshot=snapshot,
        canonical=canonical_coverage,
        canonical_snapshot=canonical_snapshot,
    )
    validate_formal_prepared_request_inventory(
        inventory=prepared_inventory,
        snapshot=canonical_snapshot,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=artifact_root / "full-prepared-inventory-validation",
    )
    validated_authority = _mint_validated_representative_authority(
        snapshot=canonical_snapshot,
        prepared_inventory=prepared_inventory,
        coverage=canonical_coverage,
    )
    return _build_representative_unified_acquisition_plan_from_validated_authority(
        validated_authority=validated_authority,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=artifact_root / "representative-slots",
        api_key_env_by_provider_family=api_key_env_by_provider_family,
        frozen_pricing_by_provider_family=frozen_pricing_by_provider_family,
        requested_at=requested_at,
        max_acquisition_concurrency=max_acquisition_concurrency,
    )


def _formal_dispatch_suite_root(dispatch_plans: Sequence[Any]) -> Path:
    """返回正式 runner authority 的共同 suite root，而非 planning artifact root。"""

    plans = tuple(dispatch_plans)
    if not plans:
        raise ValueError("representative acquisition requires formal dispatch plans")
    roots = {
        Path(getattr(plan, "output_root", "")).resolve(strict=False).parent
        for plan in plans
    }
    if len(roots) != 1:
        raise ValueError("formal dispatch plans do not share one suite root")
    return next(iter(roots))


def _mint_validated_representative_authority(
    *,
    snapshot: Any,
    prepared_inventory: Any,
    coverage: Any,
) -> _ValidatedRepresentativeAuthority:
    return _ValidatedRepresentativeAuthority(
        snapshot=snapshot,
        prepared_inventory=prepared_inventory,
        coverage=coverage,
        seal=_VALIDATED_REPRESENTATIVE_AUTHORITY_SEAL,
    )


def _validate_bound_representative_authority_objects(
    *,
    snapshot: Any,
    prepared_inventory: Any,
    coverage: Any,
) -> None:
    from tokenshare.experiments.paper_formal_plan import (
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
    )

    if (
        type(snapshot) is not FormalPlanSnapshot
        or type(prepared_inventory) is not FormalPreparedRequestInventory
        or type(coverage) is not FormalExecutionCoverage
    ):
        raise TypeError("validated authority token object type drift")
    expected_keys = {
        (root.condition.condition_id, root.case_id, planned_ai_unit_id)
        for root in snapshot.roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    }
    actual_keys = {
        (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        )
        for record in prepared_inventory.records
    }
    if (
        snapshot.provider_calls_made != 0
        or prepared_inventory.provider_calls_made != 0
        or coverage.provider_calls_made != 0
        or coverage.source_snapshot is not snapshot
        or prepared_inventory.source_snapshot_digest != snapshot.snapshot_digest
        or coverage.source_snapshot_digest != snapshot.snapshot_digest
        or prepared_inventory.record_count != len(prepared_inventory.records)
        or prepared_inventory.record_count != len(expected_keys)
        or actual_keys != expected_keys
    ):
        raise ValueError("validated authority token lineage drift")


def _validate_exact_formal_snapshot_authority(*, supplied: Any, canonical: Any) -> None:
    if supplied.to_dict() != canonical.to_dict():
        raise ValueError("representative acquisition canonical full snapshot drift")
    for supplied_condition, canonical_condition in zip(
        supplied.conditions,
        canonical.conditions,
        strict=True,
    ):
        if (
            supplied_condition != canonical_condition
            or supplied_condition.condition is not canonical_condition.condition
            or supplied_condition.binding is not canonical_condition.binding
        ):
            raise ValueError("representative acquisition canonical condition authority drift")
    for supplied_root, canonical_root in zip(
        supplied.roots,
        canonical.roots,
        strict=True,
    ):
        if (
            supplied_root != canonical_root
            or supplied_root.condition is not canonical_root.condition
            or supplied_root.binding is not canonical_root.binding
        ):
            raise ValueError("representative acquisition canonical root authority drift")


def _validate_exact_formal_coverage_authority(
    *,
    supplied: Any,
    supplied_snapshot: Any,
    canonical: Any,
    canonical_snapshot: Any,
) -> None:
    if supplied.source_snapshot is not supplied_snapshot:
        raise ValueError("representative acquisition supplied coverage snapshot drift")
    if supplied.to_dict() != canonical.to_dict():
        raise ValueError("representative acquisition canonical coverage drift")
    supplied_root_ids = {id(root) for root in supplied_snapshot.roots}
    canonical_root_ids = {id(root) for root in canonical_snapshot.roots}
    for supplied_root, canonical_root in zip(
        supplied.roots,
        canonical.roots,
        strict=True,
    ):
        if (
            id(supplied_root) not in supplied_root_ids
            or id(canonical_root) not in canonical_root_ids
            or supplied_root != canonical_root
            or supplied_root.condition is not canonical_root.condition
            or supplied_root.binding is not canonical_root.binding
        ):
            raise ValueError("representative acquisition canonical coverage root drift")


def _build_formal_semantic_candidates_for_roots(
    *,
    roots: Sequence[Any],
    records_by_key: Mapping[tuple[str, str, str], Any],
    snapshot: Any,
    coverage: Any,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> tuple[SemanticSlotCandidate, ...]:
    """Pure provider-zero candidate projection shared by acquisition and replay."""

    from tokenshare.executors.ai_api_request_identity import (
        PreparedOutboundRequestFactory,
    )

    candidates: list[SemanticSlotCandidate] = []
    del catalog_manifest, ai_api_configs, planning_artifact_root
    for root in roots:
        records = tuple(
            records_by_key[
                (root.condition.condition_id, root.case_id, planned_ai_unit_id)
            ]
            for planned_ai_unit_id in root.planned_ai_unit_ids
        )
        slots = replacement_slots_for(
            experiment_id=root.condition.experiment_id,
            fault_type=str(root.condition.fault_type),
            ablation_mode=str(root.condition.ablation_mode),
        )
        prepared_values: list[PreparedOutboundRequest] = []
        for record in records:
            base = validate_prepared_request(record.prepared_request)
            for replacement_slot in slots:
                prepared_values.append(
                    base
                    if replacement_slot == 0
                    else PreparedOutboundRequestFactory.prepare(
                        body_obj=dict(base.body_obj),
                        base_url=base.normalized_absolute_endpoint,
                        endpoint="",
                        provider_config_digest=base.provider_config_digest,
                        entry_id=base.entry_id,
                        configured_model=base.configured_model,
                        effective_controls_digest=(
                            base.effective_controls_digest
                        ),
                        plugin_id=base.plugin_id,
                        plugin_version=base.plugin_version,
                        prompt_profile_id=base.prompt_profile_id,
                        prompt_serialization_schema=(
                            base.prompt_serialization_schema
                        ),
                        body_serialization_schema=(
                            base.body_serialization_schema
                        ),
                        case_id=base.case_id,
                        planned_ai_unit_id=base.planned_ai_unit_id,
                        sample_slot_index=base.sample_slot_index,
                        replacement_slot=replacement_slot,
                    )
                )
        record_by_unit = {
            record.planned_ai_unit_id: record for record in records
        }
        for prepared in prepared_values:
            record = record_by_unit[prepared.planned_ai_unit_id]
            candidates.append(
                SemanticSlotCandidate(
                    experiment_id=root.condition.experiment_id,
                    condition_id=root.condition.condition_id,
                    condition_digest=root.condition_digest,
                    worker_count=root.condition.worker_count,
                    repeat_id=root.repeat_id,
                    fault_type=str(root.condition.fault_type),
                    ablation_mode=str(root.condition.ablation_mode),
                    case_id=root.case_id,
                    case_record_digest=root.case_record_digest,
                    planned_ai_unit_id=prepared.planned_ai_unit_id,
                    sample_slot_index=prepared.sample_slot_index,
                    replacement_slot=prepared.replacement_slot,
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
                    replacement_policy_id="formal_complete",
                    formal_authority=True,
                    selection_id=root.binding.selection.selection_id,
                    selection_digest=root.selection_digest,
                    seed=root.seed,
                    split_profile_id=root.split_profile_id,
                    split_profile_digest=root.split_profile_digest,
                    source_snapshot_digest=snapshot.snapshot_digest,
                    coverage_digest=coverage.coverage_digest,
                    model_endpoint_identity_digest=(
                        root.endpoint_controls.model_endpoint_identity_digest
                    ),
                    request_controls_digest=(
                        root.endpoint_controls.request_controls_digest
                    ),
                )
            )
    return tuple(candidates)


def _pricing_from_source_entry(entry: Any) -> FrozenPricing:
    pricing_body = getattr(entry, "pricing", None)
    if not isinstance(pricing_body, Mapping):
        raise ValueError("representative acquisition pricing source is invalid")
    try:
        return FrozenPricing(
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
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("representative acquisition pricing source is invalid") from exc


def _resolve_acquisition_pricing(
    *,
    record: Any,
    source_config: Any,
    current_config: Any,
    supplied_pricing: FrozenPricing,
    full_current_pricing_authority: FullCurrentAcquisitionPricingAuthority | None,
) -> FrozenPricing:
    """在 source provenance 不变的前提下选择 representative frozen 或 Full A 价格。"""

    source_entries = tuple(
        entry
        for entry in getattr(source_config, "entries", ())
        if getattr(entry, "enabled", None) is True
        and getattr(entry, "entry_id", None)
        == getattr(record, "model_entry_id", None)
    )
    source_config_digest = getattr(source_config, "config_digest", None)
    authority_allows_current_source = (
        full_current_pricing_authority is not None
        and full_current_pricing_authority.applies_to(record=record)
        and source_config_digest
        == full_current_pricing_authority.current_provider_config_digest
    )
    if (
        (
            source_config_digest
            != getattr(record, "source_provider_config_digest", None)
            and not authority_allows_current_source
        )
        or len(source_entries) != 1
    ):
        raise ValueError("representative acquisition provider authority drift")
    if full_current_pricing_authority is not None:
        if type(full_current_pricing_authority) is not FullCurrentAcquisitionPricingAuthority:
            raise TypeError("Full current acquisition pricing authority type drift")
        if full_current_pricing_authority.applies_to(record=record):
            if (
                getattr(current_config, "config_digest", None)
                != full_current_pricing_authority.current_provider_config_digest
            ):
                raise ValueError("Full current pricing authority drift")
            from tokenshare.experiments.run_paper_experiments import (
                _pricing_refresh_execution_identity,
            )

            if (
                _pricing_refresh_execution_identity(current_config)
                != full_current_pricing_authority.current_execution_identity_digest
            ):
                raise ValueError("Full current pricing execution identity drift")
            entry_id = getattr(record, "model_entry_id", None)
            current_entries = tuple(
                entry
                for entry in getattr(current_config, "entries", ())
                if getattr(entry, "enabled", None) is True
                and getattr(entry, "entry_id", None) == entry_id
            )
            if len(current_entries) != 1:
                raise ValueError("Full current pricing authority entry drift")
            if _pricing_from_source_entry(current_entries[0]) != (
                full_current_pricing_authority.pricing_by_entry.get(entry_id)
            ):
                raise ValueError("Full current pricing authority drift")
            return full_current_pricing_authority.resolve_pricing(
                record=record,
                source_config=source_config,
            )
    expected_pricing = _pricing_from_source_entry(source_entries[0])
    if supplied_pricing != expected_pricing:
        raise ValueError("representative acquisition frozen pricing drift")
    return expected_pricing


def _build_representative_unified_acquisition_plan_from_validated_authority(
    *,
    validated_authority: _ValidatedRepresentativeAuthority,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
    api_key_env_by_provider_family: Mapping[str, str],
    frozen_pricing_by_provider_family: Mapping[str, FrozenPricing],
    requested_at: str,
    max_acquisition_concurrency: int = 10,
    full_current_pricing_authority: FullCurrentAcquisitionPricingAuthority | None = None,
    source_ai_api_configs: Mapping[str, Any] | None = None,
    current_ai_api_configs: Mapping[str, Any] | None = None,
) -> RepresentativeUnifiedAcquisitionPlan:
    """已验证 canonical authority 的 compact semantic/dedupe 实现。"""

    from tokenshare.experiments.paper_exp1_trace_reuse import (
        select_exp1_source_roots,
    )

    if (
        type(validated_authority) is not _ValidatedRepresentativeAuthority
        or validated_authority.seal is not _VALIDATED_REPRESENTATIVE_AUTHORITY_SEAL
    ):
        raise TypeError("validated authority token is required")
    snapshot = validated_authority.snapshot
    prepared_inventory = validated_authority.prepared_inventory
    coverage = validated_authority.coverage
    source_config_map = dict(source_ai_api_configs or {})
    current_config_map = dict(current_ai_api_configs or ai_api_configs)
    if type(max_acquisition_concurrency) is not int or not (
        1 <= max_acquisition_concurrency <= 10
    ):
        raise ValueError("representative acquisition concurrency must be within 1..10")

    roots_by_key = {
        (root.condition.condition_id, root.case_id): root for root in snapshot.roots
    }
    if len(roots_by_key) != len(snapshot.roots):
        raise ValueError("representative acquisition snapshot root identity is duplicate")
    records_by_key: dict[tuple[str, str, str], Any] = {}
    for record in prepared_inventory.records:
        key = (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        )
        root = roots_by_key.get(key[:2])
        if key in records_by_key or root is None:
            raise ValueError("representative acquisition prepared record coverage drift")
        expected_slots = replacement_slots_for(
            experiment_id=root.condition.experiment_id,
            fault_type=str(root.condition.fault_type),
            ablation_mode=str(root.condition.ablation_mode),
        )
        prepared = validate_prepared_request(record.prepared_request)
        if (
            record.condition is not root.condition
            or record.binding is not root.binding
            or record.case_record_digest != root.case_record_digest
            or record.planned_ai_unit_id not in root.planned_ai_unit_ids
            or record.sample_slot_index != root.repeat_id
            or record.base_replacement_slot != 0
            or record.replacement_slot_ids != expected_slots
            or record.replacement_policy_id != "formal_attempt_budget.v1"
            or record.provider_calls_made != 0
            or prepared.planned_ai_unit_id != record.planned_ai_unit_id
            or prepared.sample_slot_index != root.repeat_id
            or prepared.replacement_slot != 0
            or prepared.provider_config_digest
            != record.prepared_execution_config_digest
            or record.prompt_profile_digest
            != canonical_digest(
                {
                    "body_digest": prepared.body_digest,
                    "prompt_profile_id": prepared.prompt_profile_id,
                    "prompt_serialization_schema": prepared.prompt_serialization_schema,
                }
            )
        ):
            raise ValueError("representative acquisition prepared record identity drift")
        # _body() 还会重算 provider_request_identity 与 nested prepared identity。
        record.request_identity_digest
        records_by_key[key] = record
    expected_keys = {
        (root.condition.condition_id, root.case_id, planned_ai_unit_id)
        for root in snapshot.roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    }
    if set(records_by_key) != expected_keys:
        raise ValueError("representative acquisition full prepared inventory is partial")
    if prepared_inventory.unique_inference_request_count != len(
        {record.inference_request_digest for record in prepared_inventory.records}
    ):
        raise ValueError("representative acquisition prepared unique count drift")

    acquisition_roots = select_exp1_source_roots(
        full_roots=snapshot.roots,
        selected_roots=coverage.roots,
    )
    if not acquisition_roots:
        raise ValueError("representative acquisition selected no Exp1 roots")
    candidates = _build_formal_semantic_candidates_for_roots(
        roots=acquisition_roots,
        records_by_key=records_by_key,
        snapshot=snapshot,
        coverage=coverage,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=planning_artifact_root,
    )
    semantic_plan = build_semantic_inventory(candidates)
    prepared_by_inference: dict[str, PreparedOutboundRequest] = {}
    record_by_inference: dict[str, Any] = {}
    for candidate in candidates:
        prepared = candidate.prepared_request
        existing = prepared_by_inference.setdefault(
            prepared.inference_request_digest,
            prepared,
        )
        if existing != prepared:
            raise ValueError("one inference digest maps to multiple exact requests")
        record_by_inference.setdefault(
            prepared.inference_request_digest,
            records_by_key[
                (
                    candidate.condition_id,
                    candidate.case_id,
                    candidate.planned_ai_unit_id,
                )
            ],
        )
    requests: list[AcquisitionRequest] = []
    million = Decimal("1000000")
    for row in semantic_plan.rows:
        prepared = prepared_by_inference[row.inference_request_digest]
        record = record_by_inference[row.inference_request_digest]
        try:
            api_key_env = api_key_env_by_provider_family[record.provider_family]
            pricing = frozen_pricing_by_provider_family[record.provider_family]
        except KeyError as exc:
            raise ValueError("representative acquisition transport authority is missing") from exc
        source_config = source_config_map.get(record.provider_config_id)
        if source_config is None:
            source_config = ai_api_configs.get(record.provider_config_id)
        current_config = current_config_map.get(record.provider_config_id)
        if current_config is None:
            current_config = source_config
        source_entries = tuple(
            entry
            for entry in getattr(source_config, "entries", ())
            if entry.enabled and entry.entry_id == record.model_entry_id
        )
        source_config_digest = getattr(source_config, "config_digest", None)
        authority_allows_current_source = (
            full_current_pricing_authority is not None
            and full_current_pricing_authority.applies_to(record=record)
            and source_config_digest
            == full_current_pricing_authority.current_provider_config_digest
        )
        if (
            (
                source_config_digest != record.source_provider_config_digest
                and not authority_allows_current_source
            )
            or len(source_entries) != 1
            or source_entries[0].api_key_env != api_key_env
        ):
            raise ValueError("representative acquisition provider authority drift")
        pricing = _resolve_acquisition_pricing(
            record=record,
            source_config=source_config,
            current_config=current_config,
            supplied_pricing=pricing,
            full_current_pricing_authority=full_current_pricing_authority,
        )
        token_upper_bound = (
            prepared.estimated_prompt_tokens + record.request_max_tokens
        )
        cost_upper_bound = (
            Decimal(prepared.estimated_prompt_tokens)
            * pricing.input_per_million_tokens
            + Decimal(record.request_max_tokens)
            * pricing.output_per_million_tokens
        ) / million
        requests.append(
            AcquisitionRequest(
                inventory_row=row,
                prepared_request=prepared,
                provider_family=record.provider_family,
                api_key_env=api_key_env,
                timeout_seconds=record.request_timeout_seconds,
                token_upper_bound=token_upper_bound,
                cost_upper_bound=cost_upper_bound,
                frozen_pricing=pricing,
                requested_at=requested_at,
            )
        )
    return RepresentativeUnifiedAcquisitionPlan(
        source_snapshot_digest=snapshot.snapshot_digest,
        source_prepared_inventory_digest=prepared_inventory.inventory_digest,
        coverage_digest=coverage.coverage_digest,
        condition_candidate_count=len(candidates),
        unique_acquisition_request_count=len(requests),
        candidates=tuple(candidates),
        semantic_inventory_plan=semantic_plan,
        acquisition_requests=tuple(requests),
        max_acquisition_concurrency=max_acquisition_concurrency,
    )


def _build_replay_semantic_inventory(
    candidates: Sequence[SemanticSlotCandidate],
) -> SemanticInventoryPlan:
    """Build a logical runner preflight plan without cross-condition slot aliasing."""

    values = tuple(candidates)
    if not values:
        raise ValueError("replay semantic candidates are missing")
    by_condition: dict[str, list[SemanticSlotCandidate]] = {}
    for candidate in values:
        by_condition.setdefault(candidate.condition_id, []).append(candidate)
    logical_rows: list[ResponseBankInventoryRow] = []
    condition_refs: list[dict[str, Any]] = []
    failure_count = success_count = unacquired_count = 0
    for condition_id in sorted(by_condition):
        condition_plan = build_semantic_inventory(
            tuple(by_condition[condition_id])
        )
        condition_ref = condition_plan.condition_refs[0]
        experiment_id = str(condition_ref["experiment_id"])
        slot_mapping: dict[str, str] = {}
        for row in condition_plan.rows:
            logical_unit_id = (
                f"replay:{experiment_id}:{condition_id}:"
                f"{row.planned_ai_unit_id}"
            )
            logical_slot = semantic_slot_key(
                case_record_digest=row.case_record_digest,
                planned_ai_unit_id=logical_unit_id,
                sample_slot_index=row.sample_slot_index,
                replacement_slot=row.replacement_slot,
                provider_config_digest=row.provider_config_digest,
                prompt_profile_digest=row.prompt_profile_digest,
                prompt_admission_profile_digest=(
                    row.prompt_admission_profile_digest
                ),
                plugin_version=row.plugin_version,
            )
            provisional = replace(
                row,
                inventory_entry_id="",
                semantic_slot_key=logical_slot,
                planned_ai_unit_id=logical_unit_id,
                entry_id=terminal_bank_entry_id(
                    semantic_slot_key=logical_slot,
                    inference_request_digest=row.inference_request_digest,
                ),
            )
            logical = replace(
                provisional,
                inventory_entry_id=inventory_entry_id(provisional),
            )
            logical_rows.append(logical)
            slot_mapping[row.semantic_slot_key] = logical_slot
        rewritten = dict(condition_ref)
        rewritten["semantic_slot_keys"] = [
            slot_mapping[str(slot)]
            for slot in condition_ref["semantic_slot_keys"]
        ]
        rewritten["case_refs"] = [
            {
                **dict(case_ref),
                "semantic_slot_keys": [
                    slot_mapping[str(slot)]
                    for slot in case_ref["semantic_slot_keys"]
                ],
            }
            for case_ref in condition_ref["case_refs"]
        ]
        condition_refs.append(rewritten)
        failure_count += condition_plan.terminal_provider_failure_count
        success_count += condition_plan.terminal_success_count
        unacquired_count += condition_plan.terminal_unacquired_count
    rows = canonical_inventory_rows(tuple(logical_rows))
    plan = SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=response_bank_inventory_digest(rows),
        rows=rows,
        condition_refs=tuple(condition_refs),
        exp2_online_condition_refs=(),
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=failure_count,
        terminal_success_count=success_count,
        terminal_unacquired_count=unacquired_count,
    )
    _validate_semantic_inventory_plan(plan)
    return plan


def build_results_first_replay_semantic_plan(
    *,
    snapshot: Any,
    prepared_inventory: Any,
    coverage: Any,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> ResultsFirstReplaySemanticAuthority:
    """Build the Exp1--4 target plan without creating provider authority."""

    from tokenshare.experiments.paper_formal_plan import (
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
    )

    if (
        type(snapshot) is not FormalPlanSnapshot
        or type(prepared_inventory) is not FormalPreparedRequestInventory
        or type(coverage) is not FormalExecutionCoverage
    ):
        raise TypeError("typed formal replay authority is required")
    if (
        coverage.source_snapshot is not snapshot
        or coverage.source_snapshot_digest != snapshot.snapshot_digest
        or prepared_inventory.source_snapshot_digest != snapshot.snapshot_digest
        or prepared_inventory.provider_calls_made != 0
        or coverage.provider_calls_made != 0
        or prepared_inventory.record_count != len(prepared_inventory.records)
    ):
        raise ValueError("formal replay authority lineage drift")
    records_by_key: dict[tuple[str, str, str], Any] = {}
    for record in prepared_inventory.records:
        key = (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        )
        if key in records_by_key:
            raise ValueError("formal replay prepared identity is duplicate")
        records_by_key[key] = record
    expected_keys = {
        (root.condition.condition_id, root.case_id, planned_ai_unit_id)
        for root in snapshot.roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    }
    if set(records_by_key) != expected_keys:
        raise ValueError("formal replay prepared inventory is incomplete")
    trace_experiment_ids = {
        condition.experiment_id
        for condition in coverage.conditions
        if condition.experiment_id != "exp5_real_ai_model_endpoint_comparison"
    }
    if not trace_experiment_ids or not trace_experiment_ids.issubset(
        {
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        }
    ):
        raise ValueError("formal replay coverage experiment set is invalid")
    target_roots = tuple(
        root
        for root in coverage.roots
        if root.condition.experiment_id in trace_experiment_ids
    )
    if not target_roots:
        raise ValueError("formal replay selected no trace roots")
    candidates = _build_formal_semantic_candidates_for_roots(
        roots=target_roots,
        records_by_key=records_by_key,
        snapshot=snapshot,
        coverage=coverage,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=planning_artifact_root,
    )
    semantic_plan = _build_replay_semantic_inventory(candidates)
    if int(getattr(semantic_plan, "provider_call_count", 0)) != 0:
        raise ValueError("formal replay planning called a provider")
    return ResultsFirstReplaySemanticAuthority(
        candidates=candidates,
        semantic_inventory_plan=semantic_plan,
        source_snapshot_digest=snapshot.snapshot_digest,
        coverage_digest=coverage.coverage_digest,
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
    *,
    condition_slot_keys: dict[str, set[str]],
    case_refs_by_key: dict[tuple[str, str], dict[str, Any]],
    case_slot_keys_by_key: dict[tuple[str, str], set[str]],
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
    if candidate.formal_authority:
        formal_values = {
            "selection_id": candidate.selection_id,
            "selection_digest": candidate.selection_digest,
            "seed": candidate.seed,
            "source_snapshot_digest": candidate.source_snapshot_digest,
            "coverage_digest": candidate.coverage_digest,
            "model_endpoint_identity_digest": (
                candidate.model_endpoint_identity_digest
            ),
            "request_controls_digest": candidate.request_controls_digest,
        }
        required_strings = {
            key: value
            for key, value in formal_values.items()
            if key != "seed"
        }
        if (
            type(candidate.seed) is not int
            or any(not isinstance(value, str) or not value for value in required_strings.values())
            or candidate.split_profile_id is not None
            and not isinstance(candidate.split_profile_id, str)
            or not isinstance(candidate.split_profile_digest, str)
            or not candidate.split_profile_digest
        ):
            raise ValueError("formal semantic candidate authority is incomplete")
        condition_ref.update(formal_values)
    value = refs.setdefault(candidate.condition_id, condition_ref)
    identity_fields = set(condition_ref) - {"semantic_slot_keys", "case_refs"}
    if set(value) != set(condition_ref) or any(
        value.get(name) != condition_ref[name] for name in identity_fields
    ):
        raise ValueError("condition replacement policy drift")
    known_condition_slots = condition_slot_keys.setdefault(
        candidate.condition_id,
        set(),
    )
    if slot_key not in known_condition_slots:
        known_condition_slots.add(slot_key)
        value["semantic_slot_keys"].append(slot_key)
    case_key = (candidate.condition_id, candidate.case_id)
    candidate_case_ref: dict[str, Any] = {
        "case_id": candidate.case_id,
        "case_record_digest": candidate.case_record_digest,
        "semantic_slot_keys": [],
    }
    if candidate.formal_authority:
        # split profile 是 case/root identity，不是跨 case 共用的 condition identity。
        candidate_case_ref.update(
            {
                "split_profile_id": candidate.split_profile_id,
                "split_profile_digest": candidate.split_profile_digest,
            }
        )
    case_ref = case_refs_by_key.get(case_key)
    if case_ref is not None:
        identity_fields = set(candidate_case_ref) - {"semantic_slot_keys"}
        if set(case_ref) != set(candidate_case_ref) or any(
            case_ref.get(name) != candidate_case_ref[name]
            for name in identity_fields
        ):
            if (
                case_ref.get("case_record_digest") == candidate.case_record_digest
                and (
                    case_ref.get("split_profile_id") != candidate.split_profile_id
                    or case_ref.get("split_profile_digest")
                    != candidate.split_profile_digest
                )
            ):
                raise ValueError("case split profile authority drift")
            raise ValueError("one formal case_id cannot map to multiple record digests")
    else:
        case_ref = candidate_case_ref
        value["case_refs"].append(case_ref)
        case_refs_by_key[case_key] = case_ref
    case_slot_keys = case_slot_keys_by_key.setdefault(case_key, set())
    if slot_key not in case_slot_keys:
        case_slot_keys.add(slot_key)
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
    slots_by_unit: dict[tuple[str, str, str, int], set[int]] = {}
    policy_by_unit: dict[tuple[str, str, str, int], tuple[object, ...]] = {}
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
        )
        previous = policy_by_unit.setdefault(key, policy)
        if previous != policy:
            raise ValueError("condition replacement policy drift")
        if candidate.replacement_policy_id != "formal_complete":
            raise ValueError("unsupported replacement completeness policy")
    for key, actual_slots in slots_by_unit.items():
        (
            replacement_policy_id,
            experiment_id,
            fault_type,
            ablation_mode,
        ) = policy_by_unit[key]
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
    formal_condition_ref_fields = {
        "selection_id",
        "selection_digest",
        "seed",
        "source_snapshot_digest",
        "coverage_digest",
        "model_endpoint_identity_digest",
        "request_controls_digest",
    }
    split_profile_fields = {
        "split_profile_id",
        "split_profile_digest",
    }
    legacy_formal_condition_ref_fields = (
        formal_condition_ref_fields | split_profile_fields
    )
    base_case_ref_fields = {
        "case_id",
        "case_record_digest",
        "semantic_slot_keys",
    }
    formal_case_ref_fields = base_case_ref_fields | split_profile_fields
    for raw_ref in plan.condition_refs:
        if not isinstance(raw_ref, Mapping):
            raise ValueError("semantic inventory condition ref schema drift")
        raw_fields = set(raw_ref)
        current_formal_policy = (
            raw_fields == expected_ref_fields | formal_condition_ref_fields
        )
        legacy_formal_policy = (
            raw_fields == expected_ref_fields | legacy_formal_condition_ref_fields
        )
        formal_policy = current_formal_policy or legacy_formal_policy
        if raw_fields != expected_ref_fields and not formal_policy:
            raise ValueError("semantic inventory condition ref schema drift")
        if formal_policy:
            if (
                type(raw_ref["seed"]) is not int
                or any(
                    not isinstance(raw_ref[field_name], str)
                    or not raw_ref[field_name]
                    for field_name in formal_condition_ref_fields
                    if field_name != "seed"
                )
            ):
                raise ValueError("semantic inventory formal authority is invalid")
        if legacy_formal_policy and (
            raw_ref["split_profile_id"] is not None
            and not isinstance(raw_ref["split_profile_id"], str)
            or not isinstance(raw_ref["split_profile_digest"], str)
            or not raw_ref["split_profile_digest"]
        ):
            raise ValueError("semantic inventory formal authority is invalid")
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
            expected_case_fields = (
                formal_case_ref_fields
                if current_formal_policy
                else base_case_ref_fields
            )
            if (
                not isinstance(case_ref, Mapping)
                or set(case_ref) != expected_case_fields
            ):
                raise ValueError("semantic inventory case ref schema drift")
            if current_formal_policy and (
                case_ref["split_profile_id"] is not None
                and not isinstance(case_ref["split_profile_id"], str)
                or not isinstance(case_ref["split_profile_digest"], str)
                or not case_ref["split_profile_digest"]
            ):
                raise ValueError("semantic inventory case split authority is invalid")
            ref_slots = case_ref["semantic_slot_keys"]
            if not isinstance(ref_slots, list) or not ref_slots:
                raise ValueError("semantic inventory case slots are invalid")
            for slot_key in ref_slots:
                row = rows_by_slot.get(slot_key)
                if row is None or row.case_record_digest != case_ref["case_record_digest"]:
                    raise ValueError("semantic inventory case row binding mismatch")
                case_slots.add(slot_key)
                unit_slots.setdefault(
                    (
                        row.case_record_digest,
                        row.planned_ai_unit_id,
                        row.sample_slot_index,
                    ),
                    set(),
                ).add(row.replacement_slot)
        if case_slots != set(slot_keys):
            raise ValueError("semantic inventory case refs do not cover condition")
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
    "ExistingSettledAttempt",
    "FullCurrentAcquisitionPricingAuthority",
    "FullAcquisitionBudget",
    "InventoryPreflightResult",
    "FormalTraceInventoryPreflightResult",
    "RepresentativeUnifiedAcquisitionPlan",
    "ResultsFirstAcquisitionPlan",
    "ResultsFirstAcquisitionAuthority",
    "RepresentativeAcquisitionAuthority",
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
    "SupervisedNoResponseClosureAuthority",
    "SupervisedNoResponseClosureReceipt",
    "apply_supervised_no_response_closure",
    "build_semantic_inventory",
    "build_representative_unified_acquisition_plan",
    "build_results_first_unified_acquisition_plan",
    "create_acquisition_plan_bundle",
    "create_representative_acquisition_plan_bundle",
    "establish_results_first_acquisition_authorization",
    "finalize_acquisition_child_bank",
    "load_acquisition_plan_bundle",
    "load_ordered_existing_settled_attempts",
    "load_supervised_no_response_closure_authority",
    "load_representative_acquisition_plan_bundle",
    "materialize_results_first_acquisition_bundle",
    "materialize_results_first_unified_acquisition_plan",
    "output_root_path_digest",
    "preflight_inventory_before_coordinator",
    "preflight_formal_trace_inventory",
    "persist_supervised_no_response_closure_authority",
    "prepare_representative_acquisition_authority",
    "prepare_results_first_acquisition_authority",
    "replacement_slots_for",
    "response_bank_manifest_for_bundle",
    "results_first_response_bank_manifest_for_bundle",
    "validate_representative_acquisition_plan_bundle",
]
