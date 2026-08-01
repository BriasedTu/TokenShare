"""Exp2--4 真实回答库的确定性 semantic-slot inventory 与零引擎预检。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    ExternalBankObjectLocator,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    canonical_digest,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.experiments.paper_budget_ledger import (
    BudgetExceededError,
    PaperBudgetLedger,
    ReservationRequest,
)
from tokenshare.experiments.paper_resource_accounting import (
    FrozenPricing,
    ProviderUsage,
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


class AcquisitionAuthorizationError(RuntimeError):
    pass


class AcquisitionIdentityError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class PaidAcquisitionContext:
    """Task 26 receipt 之前由调用方提供的已验证 paid context。"""

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


def output_root_path_digest(path: str | Path) -> str:
    return canonical_digest(str(Path(path).resolve()))


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
        paid_context: PaidAcquisitionContext,
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
        expected_inventory_digest = canonical_digest(
            [
                row.to_dict()
                for row in sorted(
                    self.inventory_rows,
                    key=lambda item: (item.semantic_slot_key, item.inventory_entry_id),
                )
            ]
        )
        if expected_inventory_digest != inventory_digest:
            raise AcquisitionIdentityError("inventory digest does not match rows")
        self.budget_ledger = budget_ledger
        self.paid_context = paid_context
        self.invocation_mode = invocation_mode
        self.transport = transport
        self.secret_resolver = secret_resolver
        self.now_epoch = now_epoch
        self.crash_hook = crash_hook
        self.durability_hook = durability_hook
        self._base_lifecycle: list[str] = []
        self._validate_paid_context()
        self._base_lifecycle.append("paid_context_validated")
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

    def _validate_paid_context(self) -> None:
        context = self.paid_context
        required = (
            context.receipt_digest,
            context.authorized_plan_digest,
            context.profile_digest,
            context.budget_digest,
            context.inventory_digest,
            context.prompt_admission_profile_digest,
            context.output_root_path_digest,
            context.paid_scope_digest,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise AcquisitionAuthorizationError("paid acquisition context is incomplete")
        if context.inventory_digest != self.inventory_digest:
            raise AcquisitionAuthorizationError("paid inventory digest mismatch")
        if context.prompt_admission_profile_digest != PROMPT_ADMISSION_PROFILE_DIGEST:
            raise AcquisitionAuthorizationError("paid prompt admission digest mismatch")
        if context.output_root_path_digest != output_root_path_digest(self.output_root):
            raise AcquisitionAuthorizationError("paid output root digest mismatch")
        if context.reacquisition_limit not in {0, 1}:
            raise AcquisitionAuthorizationError("paid reacquisition scope drift")

    def _binding_body(self) -> dict[str, str]:
        preimage = {
            "receipt_digest": self.paid_context.receipt_digest,
            "authorized_plan_digest": self.paid_context.authorized_plan_digest,
            "profile_digest": self.paid_context.profile_digest,
            "budget_digest": self.paid_context.budget_digest,
            "inventory_digest": self.paid_context.inventory_digest,
            "prompt_admission_profile_digest": (
                self.paid_context.prompt_admission_profile_digest
            ),
            "output_root_path_digest": self.paid_context.output_root_path_digest,
        }
        return {**preimage, "marker_digest": canonical_digest(preimage)}

    def _open_output_binding(self) -> None:
        marker_name = "paid_output_binding.v1.json"
        if self.invocation_mode == "new_run":
            try:
                self.output_root.mkdir(parents=True, exist_ok=False)
            except FileExistsError as exc:
                raise AcquisitionAuthorizationError(
                    "new_run requires a nonexistent canonical output root"
                ) from exc
            store = ArtifactStore(self.output_root, artifact_dir_name=".")
            store.save_json(
                self._binding_body(),
                artifact_id=marker_name,
                artifact_type="PaidOutputBinding",
                artifact_schema_id="tokenshare.paid_output_binding",
                artifact_schema_version="v1",
                source={"kind": "validated_paid_acquisition_context"},
                metadata={},
                created_at="paid-context",
            )
            return
        if not self.output_root.is_dir():
            raise AcquisitionAuthorizationError("resume output root is missing")
        store = ArtifactStore(self.output_root, artifact_dir_name=".")
        try:
            marker_ref = store.load_artifact_ref(marker_name)
            marker = json.loads(store.read_bytes(marker_ref).decode("utf-8"))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            raise AcquisitionAuthorizationError(
                "resume output binding marker is missing or partial"
            ) from exc
        if marker != self._binding_body():
            raise AcquisitionAuthorizationError("resume output binding marker mismatch")

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
        if (
            self.paid_context.reacquisition_limit != 1
            or paid_scope_digest != self.paid_context.paid_scope_digest
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
        role_bodies = {
            "provenance": {
                "schema_version": "tokenshare.response_bank_provenance.v1",
                "provider_family": request.provider_family,
                "entry_id": prepared.entry_id,
                "provider_config_digest": prepared.provider_config_digest,
                "inference_request_digest": prepared.inference_request_digest,
                "normalized_absolute_endpoint": prepared.normalized_absolute_endpoint,
                "transport_call_count": 1,
                "secret_persisted": False,
                "receipt_digest": self.paid_context.receipt_digest,
            },
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
        self, requests: Sequence[AcquisitionRequest]
    ) -> AcquisitionBatchResult:
        initial_reconcile = self.reconcile()
        # 本次 resume 只关闭 pre-dispatch crash；不能在同一 invocation 立刻重发。
        skip_after_safe_release = set(initial_reconcile.reconciled_pre_dispatch)
        results: list[AcquisitionResult] = []
        blocked_reason: str | None = None
        for request in requests:
            if request.inventory_row.inventory_entry_id in skip_after_safe_release:
                continue
            try:
                results.append(self.acquire(request))
            except BudgetExceededError as exc:
                blocked_reason = str(exc)
                break
            except AcquisitionAuthorizationError as exc:
                blocked_reason = str(exc)
                break
        report = self.reconcile()
        return AcquisitionBatchResult(
            status="blocked" if blocked_reason is not None else "complete",
            results=tuple(results),
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
            "entry_id": prepared.entry_id,
            "plugin_version": prepared.plugin_version,
        }
        for field_name, value in expected.items():
            if getattr(row, field_name) != value:
                raise AcquisitionIdentityError(
                    f"prepared request {field_name} does not match inventory row"
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
        return json.loads(self._object_store.read_bytes(ref).decode("utf-8"))

    def _primary_attempt_id(self, row: ResponseBankInventoryRow) -> str:
        return canonical_digest(
            {
                "inventory_digest": self.inventory_digest,
                "inventory_entry_id": row.inventory_entry_id,
                "attempt_kind": "primary",
            }
        )

    def _require_unexpired(self) -> None:
        if self._is_expired():
            raise AcquisitionAuthorizationError(
                "expired paid context cannot reserve or dispatch"
            )

    def _is_expired(self) -> bool:
        return self.now_epoch >= self.paid_context.expires_at_epoch

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
            entry_id=prepared.entry_id,
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

    rows = tuple(sorted(rows_by_slot.values(), key=lambda item: item.semantic_slot_key))
    terminal_values = tuple(terminal_kind_by_slot[row.semantic_slot_key] for row in rows)
    return SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=canonical_digest([row.to_dict() for row in rows]),
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


def preflight_inventory_before_coordinator(
    *,
    plan: SemanticInventoryPlan,
    available_inventory_entry_ids: Sequence[str],
    coordinator_factory: Callable[[], Any],
) -> InventoryPreflightResult:
    """在 coordinator 构造前逐 slot 检查 inventory 完整性。"""

    available = set(available_inventory_entry_ids)
    missing_rows = tuple(
        row for row in plan.rows if row.inventory_entry_id not in available
    )
    if missing_rows:
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
    value = refs.setdefault(
        candidate.condition_id,
        {
            "condition_id": candidate.condition_id,
            "condition_digest": candidate.condition_digest,
            "experiment_id": candidate.experiment_id,
            "worker_count": candidate.worker_count,
            "repeat_id": candidate.repeat_id,
            "fault_type": candidate.fault_type,
            "ablation_mode": candidate.ablation_mode,
            "semantic_slot_keys": [],
        },
    )
    if slot_key not in value["semantic_slot_keys"]:
        value["semantic_slot_keys"].append(slot_key)


def _validate_candidate_matches_prepared(
    candidate: SemanticSlotCandidate, prepared: PreparedOutboundRequest
) -> None:
    expected = (
        ("case_id", candidate.case_id, prepared.case_id),
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
    policy_by_unit: dict[tuple[str, str, str, int], tuple[str, str, str]] = {}
    for candidate in candidates:
        key = (
            candidate.condition_id,
            candidate.case_record_digest,
            candidate.planned_ai_unit_id,
            candidate.sample_slot_index,
        )
        slots_by_unit.setdefault(key, set()).add(candidate.replacement_slot)
        policy = (
            candidate.experiment_id,
            candidate.fault_type,
            candidate.ablation_mode,
        )
        previous = policy_by_unit.setdefault(key, policy)
        if previous != policy:
            raise ValueError("condition replacement policy drift")
    for key, actual_slots in slots_by_unit.items():
        experiment_id, fault_type, ablation_mode = policy_by_unit[key]
        expected_slots = set(
            replacement_slots_for(
                experiment_id=experiment_id,
                fault_type=fault_type,
                ablation_mode=ablation_mode,
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


def _object_dict(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("condition reference must be a mapping or dataclass")


__all__ = [
    "PROVIDER_FAILURE_TAXONOMY",
    "AcquisitionAuthorizationError",
    "AcquisitionBatchResult",
    "AcquisitionIdentityError",
    "AcquisitionReconcileReport",
    "AcquisitionRequest",
    "AcquisitionResult",
    "InventoryPreflightResult",
    "PaidAcquisitionContext",
    "ResponseBankPreflightBlockedRecord",
    "ResponseBankAcquisitionOrchestrator",
    "SemanticInventoryPlan",
    "SemanticSlotCandidate",
    "build_semantic_inventory",
    "output_root_path_digest",
    "preflight_inventory_before_coordinator",
    "replacement_slots_for",
]
