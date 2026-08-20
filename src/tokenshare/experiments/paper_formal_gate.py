"""EPD-027 formal execution 与 paper publication 的纯 typed gate policy。

本模块只消费已经由正式 producer 建立的 typed authority。它不读取 secret/env，
不签发 receipt、不建立 output marker，也不执行协议、replay 或 provider transport。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from tokenshare.executors.response_bank import OBJECT_ROLES, ResponseBankManifest
from tokenshare.experiments.paper_exp2_metrics import (
    EXP2_WORKER_COUNTS,
    Exp2OnlineHydratedRoot,
    Exp2TraceHydratedRoot,
    build_exp2_online_observations,
    build_exp2_trace_observations,
)
from tokenshare.experiments.paper_formal_report import FormalReportResult
from tokenshare.experiments.paper_metric_contract import PaperMetricContract
from tokenshare.experiments.paper_models import (
    VersionedPaperEvidenceEligibilityReport,
)
from tokenshare.experiments.paper_paid_authorization import (
    MARKER_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    PaidAuthorizationValidation,
)
from tokenshare.experiments.paper_response_bank import (
    FormalTraceInventoryPreflightResult,
)
from tokenshare.experiments.paper_traceability import (
    L4_ARTIFACT_ROOT,
    PaperTraceabilityReplayResult,
    ProtectedReplayInputRoot,
)


_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FORMAL_EXPERIMENTS = frozenset({"exp1", "exp2", "exp3", "exp4", "exp5"})
_FACILITY_EXPERIMENTS = frozenset({"exp5_capability"})
_BANK_EXPERIMENTS = frozenset({"exp2", "exp3", "exp4"})
_SELECTED_ROUTE_SPECS = {
    "exp1": (("online", "online_real_provider", ("exp1_feasibility",)),),
    "exp2": (
        ("trace-main", "real_model_trace_protocol_run", ("exp2_trace_scalability",)),
        ("online-support", "online_real_provider", ("exp2_online_concurrency",)),
    ),
    "exp3": (
        ("trace-main", "real_model_trace_protocol_run", ("exp3_trace_robustness",)),
        ("online-support", "online_real_provider", ("exp3_online_recovery",)),
    ),
    "exp4": (("trace", "real_model_trace_protocol_run", ("exp4_ablation",)),),
    "exp5": (
        ("online", "online_real_provider", ("exp5_quality", "exp5_resources")),
    ),
}
_AUTHORIZATION_SCOPE_ORDER = (
    "exp1_full_online",
    "epd027_full_bank_acquisition",
    "epd027_l3_capability_and_online_checks",
    "exp5_capability_smoke",
    "exp5_full_online",
)
_MARKER_BINDING_FIELDS = (
    "receipt_digest",
    "authorized_plan_digest",
    "profile_digest",
    "budget_digest",
    "inventory_digest",
    "prompt_admission_profile_digest",
    "output_root_path_digest",
)


def _canonical_digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def _require_digest(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical sha256 digest")


@dataclass(frozen=True, kw_only=True)
class PaperGateAuthorityDigests:
    profile_digest: str
    contract_digest: str
    plan_digest: str
    inventory_digest: str

    def __post_init__(self) -> None:
        for name in (
            "profile_digest",
            "contract_digest",
            "plan_digest",
            "inventory_digest",
        ):
            _require_digest(getattr(self, name), name)

    @property
    def authority_digest(self) -> str:
        return _canonical_digest(
            {
                "profile_digest": self.profile_digest,
                "contract_digest": self.contract_digest,
                "plan_digest": self.plan_digest,
                "inventory_digest": self.inventory_digest,
            }
        )


@dataclass(frozen=True, kw_only=True)
class PaperGateLevelAttestation:
    level: str
    status: str
    classification: str
    authority_digest: str

    def __post_init__(self) -> None:
        if self.level not in {"L1", "L2", "L3", "L4"}:
            raise ValueError("paper gate attestation level is unsupported")
        if self.status not in {"passed", "blocked", "facility_gate_verified"}:
            raise ValueError("paper gate attestation status is unsupported")
        if self.classification not in {"formal", "facility"}:
            raise ValueError("paper gate attestation classification is unsupported")
        _require_digest(self.authority_digest, "authority_digest")


@dataclass(frozen=True, kw_only=True)
class PaperGateSelectionEnvelope:
    selected_experiments: tuple[str, ...]
    classification: str
    authority: PaperGateAuthorityDigests
    authorization_commitment: PaidAuthorizationAuthorityCommitment | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authority, PaperGateAuthorityDigests):
            raise TypeError("paper gate selection requires typed authority digests")
        if not self.selected_experiments:
            raise ValueError("paper gate selection cannot be empty")
        if len(set(self.selected_experiments)) != len(self.selected_experiments):
            raise ValueError("paper gate selection cannot contain duplicates")
        if self.classification == "formal":
            allowed = _FORMAL_EXPERIMENTS
        elif self.classification == "facility":
            allowed = _FACILITY_EXPERIMENTS
        else:
            raise ValueError("paper gate selection classification is unsupported")
        if any(value not in allowed for value in self.selected_experiments):
            raise ValueError("paper gate selection/classification mismatch")
        if self.authorization_commitment is not None and not isinstance(
            self.authorization_commitment, PaidAuthorizationAuthorityCommitment
        ):
            raise TypeError("paper gate selection authority commitment must be typed")


@dataclass(frozen=True, kw_only=True)
class PaidAuthorizationScopeAuthority:
    """Task26 validator 使用的每个 paid scope 独立实际 authority。"""

    scope: str
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    selected_experiments: tuple[str, ...]
    output_root_path_digest: str

    def __post_init__(self) -> None:
        if self.scope not in _AUTHORIZATION_SCOPE_ORDER:
            raise ValueError("paid authorization scope authority is unsupported")
        if not self.selected_experiments:
            raise ValueError("paid authorization scope selection cannot be empty")
        for name in (
            "authorized_plan_digest",
            "profile_digest",
            "budget_digest",
            "inventory_digest",
            "prompt_admission_profile_digest",
            "output_root_path_digest",
        ):
            _require_digest(getattr(self, name), name)

    @property
    def authority_digest(self) -> str:
        return _canonical_digest(
            {
                "scope": self.scope,
                "authorized_plan_digest": self.authorized_plan_digest,
                "profile_digest": self.profile_digest,
                "budget_digest": self.budget_digest,
                "inventory_digest": self.inventory_digest,
                "prompt_admission_profile_digest": (
                    self.prompt_admission_profile_digest
                ),
                "selected_experiments": list(self.selected_experiments),
                "output_root_path_digest": self.output_root_path_digest,
            }
        )


@dataclass(frozen=True, kw_only=True)
class PaidAuthorizationAuthorityCommitment:
    """Selection 对全部 paid scope authority 的共享、排序 commitment。"""

    scope_authority_digests: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        values = tuple(self.scope_authority_digests)
        if not values or values != tuple(sorted(values)):
            raise ValueError("paid authority commitment must be non-empty and sorted")
        scopes = tuple(scope for scope, _digest in values)
        if len(set(scopes)) != len(scopes) or any(
            scope not in _AUTHORIZATION_SCOPE_ORDER for scope in scopes
        ):
            raise ValueError("paid authority commitment scope inventory is invalid")
        for _scope, authority_digest in values:
            _require_digest(authority_digest, "scope_authority_digest")

    @property
    def commitment_digest(self) -> str:
        return _canonical_digest(
            {scope: authority_digest for scope, authority_digest in self.scope_authority_digests}
        )


def build_paid_authorization_authority_commitment(
    bindings: Sequence[SelectedPaidAuthorizationBinding],
) -> PaidAuthorizationAuthorityCommitment:
    values = tuple(bindings)
    if not values or any(
        not isinstance(value, SelectedPaidAuthorizationBinding) for value in values
    ):
        raise TypeError("paid authority commitment requires typed bindings")
    by_scope = {value.authority.scope: value.authority.authority_digest for value in values}
    if len(by_scope) != len(values):
        raise ValueError("paid authority commitment contains duplicate scopes")
    return PaidAuthorizationAuthorityCommitment(
        scope_authority_digests=tuple(sorted(by_scope.items()))
    )


@dataclass(frozen=True, kw_only=True)
class SelectedPaidAuthorizationBinding:
    authority: PaidAuthorizationScopeAuthority
    validation: PaidAuthorizationValidation
    authority_commitment_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authority, PaidAuthorizationScopeAuthority):
            raise TypeError("paid authorization binding requires typed scope authority")
        if not isinstance(self.validation, PaidAuthorizationValidation):
            raise TypeError("paid authorization binding requires Task26 validation")
        if self.authority_commitment_digest is not None:
            _require_digest(
                self.authority_commitment_digest,
                "authority_commitment_digest",
            )


@dataclass(frozen=True, kw_only=True)
class CompleteFullBankPrerequisite:
    plan_digest: str
    inventory_digest: str
    manifest: ResponseBankManifest
    preflight: FormalTraceInventoryPreflightResult


@dataclass(frozen=True, kw_only=True)
class PaperGatePrerequisiteEnvelope:
    l1_attestation: PaperGateLevelAttestation | None
    l2_attestation: PaperGateLevelAttestation | None
    paid_authorizations: tuple[SelectedPaidAuthorizationBinding, ...] = ()
    full_bank: CompleteFullBankPrerequisite | None = None


@dataclass(frozen=True, kw_only=True)
class SelectedTerminalEvidence:
    experiment_id: str
    terminal_status: str
    eligibility: VersionedPaperEvidenceEligibilityReport
    formal_report: FormalReportResult | None
    routes: tuple[SelectedEvidenceRoute, ...] = ()


def selected_formal_metrics_audit_digest(value: Mapping[str, object]) -> str:
    return _canonical_digest(value)


@dataclass(frozen=True, kw_only=True)
class SelectedFormalMetricsAudit:
    descriptor_digest: str
    metrics_digest: str
    source_index_digest: str
    selected_table_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    observation_digests: tuple[str, ...]
    observation_table_ids: tuple[str, ...]
    evidence_classes: tuple[str, ...]
    publish_blocked_observation_ids: tuple[str, ...]
    direct_provenance_refs_digest: str
    output_refs_digest: str
    output_refs: tuple[Mapping[str, str], ...]
    non_regression: bool
    selected_audit_digest: str

    def __post_init__(self) -> None:
        for name in (
            "descriptor_digest",
            "metrics_digest",
            "source_index_digest",
            "direct_provenance_refs_digest",
            "output_refs_digest",
            "selected_audit_digest",
        ):
            _require_digest(getattr(self, name), name)
        if type(self.non_regression) is not bool:
            raise ValueError("selected formal audit non_regression must be bool")

    def digest_body(self) -> dict[str, object]:
        return {
            "descriptor_digest": self.descriptor_digest,
            "metrics_digest": self.metrics_digest,
            "source_index_digest": self.source_index_digest,
            "selected_table_ids": self.selected_table_ids,
            "observation_ids": self.observation_ids,
            "observation_digests": self.observation_digests,
            "observation_table_ids": self.observation_table_ids,
            "evidence_classes": self.evidence_classes,
            "publish_blocked_observation_ids": self.publish_blocked_observation_ids,
            "direct_provenance_refs_digest": self.direct_provenance_refs_digest,
            "output_refs_digest": self.output_refs_digest,
            "output_refs": self.output_refs,
            "non_regression": self.non_regression,
        }


@dataclass(frozen=True, kw_only=True)
class SelectedEvidenceRoute:
    route_id: str
    evidence_class: str
    table_ids: tuple[str, ...]
    eligibility_reports: tuple[VersionedPaperEvidenceEligibilityReport, ...]
    selected_audits: tuple[SelectedFormalMetricsAudit, ...]


@dataclass(frozen=True, kw_only=True)
class Exp2PostBankPublicationInputs:
    """由 protected replay closure 恢复的 Exp2 projector typed inputs。"""

    contract: PaperMetricContract
    trace_roots: tuple[Exp2TraceHydratedRoot, ...]
    online_roots: tuple[Exp2OnlineHydratedRoot, ...]
    contract_digest: str
    profile_digest: str
    authority_digest: str
    trace_replay_descriptor_digest: str
    online_replay_descriptor_digest: str
    worker_levels_complete: bool
    _producer_validated: bool = False

    def __post_init__(self) -> None:
        if not self._producer_validated:
            raise ValueError(
                "Exp2 publication inputs require official protected replay factory"
            )
        if not isinstance(self.contract, PaperMetricContract):
            raise TypeError("Exp2 publication contract must be PaperMetricContract")
        if any(
            not isinstance(value, Exp2TraceHydratedRoot)
            for value in self.trace_roots
        ) or any(
            not isinstance(value, Exp2OnlineHydratedRoot)
            for value in self.online_roots
        ):
            raise TypeError("Exp2 publication closure has invalid hydrated roots")
        for name in (
            "contract_digest",
            "profile_digest",
            "authority_digest",
            "trace_replay_descriptor_digest",
            "online_replay_descriptor_digest",
        ):
            _require_digest(getattr(self, name), name)
        if self.worker_levels_complete is not True:
            raise ValueError("Exp2 protected replay closure is not six-level complete")


def _exp2_worker_levels(roots: Sequence[object]) -> frozenset[int]:
    try:
        return frozenset(
            int(value.direct_result.condition_axes["worker_count"])
            for value in roots
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Exp2 protected replay worker identity is invalid") from exc


def load_exp2_post_bank_publication_inputs(
    *,
    trace_replay_input_root: str | Path,
    online_replay_input_root: str | Path,
    contract: PaperMetricContract,
    authority: PaperGateAuthorityDigests,
) -> Exp2PostBankPublicationInputs:
    """从 trace 与 L3 两份 official protected closure 恢复 Exp2 projector。"""

    if not isinstance(contract, PaperMetricContract):
        raise TypeError("Exp2 publication contract must be PaperMetricContract")
    if not isinstance(authority, PaperGateAuthorityDigests):
        raise TypeError("Exp2 publication inputs require typed gate authority")
    if contract.contract_digest != authority.contract_digest:
        raise ValueError("Exp2 publication contract digest mismatch")
    if contract.pipeline_profile_digest != authority.profile_digest:
        raise ValueError("Exp2 publication profile digest mismatch")

    # 动态导入避免把 legacy runner/CLI 路径引入 gate 的顶层 import graph。
    from tokenshare.experiments.paper_formal_runner import (
        load_paper_traceability_replay_input_root,
        load_paper_traceability_replay_inputs,
    )

    trace_protected = load_paper_traceability_replay_input_root(
        trace_replay_input_root
    )
    online_protected = load_paper_traceability_replay_input_root(
        online_replay_input_root
    )
    if not isinstance(trace_protected, ProtectedReplayInputRoot) or not isinstance(
        online_protected, ProtectedReplayInputRoot
    ):
        raise TypeError("Exp2 publication requires ProtectedReplayInputRoot")
    trace_loaded = load_paper_traceability_replay_inputs(trace_replay_input_root)
    online_loaded = load_paper_traceability_replay_inputs(online_replay_input_root)
    trace_direct = getattr(trace_loaded, "direct", None)
    online_direct = getattr(online_loaded, "direct", None)
    if not isinstance(trace_direct, Mapping) or not isinstance(
        online_direct, Mapping
    ):
        raise ValueError("protected replay direct closure is missing")
    trace_roots = tuple(trace_direct.get("exp2_trace_scalability", ()))
    online_roots = tuple(online_direct.get("exp2_online_concurrency", ()))
    if any(
        not isinstance(value, Exp2TraceHydratedRoot) for value in trace_roots
    ) or any(
        not isinstance(value, Exp2OnlineHydratedRoot) for value in online_roots
    ):
        raise TypeError("protected replay Exp2 roots have invalid types")
    expected_workers = frozenset(EXP2_WORKER_COUNTS)
    complete = (
        bool(trace_roots)
        and bool(online_roots)
        and _exp2_worker_levels(trace_roots) == expected_workers
        and _exp2_worker_levels(online_roots) == expected_workers
    )
    if not complete:
        raise ValueError("protected replay Exp2 six-level closure is incomplete")
    return Exp2PostBankPublicationInputs(
        contract=contract,
        trace_roots=trace_roots,
        online_roots=online_roots,
        contract_digest=contract.contract_digest,
        profile_digest=contract.pipeline_profile_digest,
        authority_digest=authority.authority_digest,
        trace_replay_descriptor_digest=trace_protected.descriptor_digest,
        online_replay_descriptor_digest=online_protected.descriptor_digest,
        worker_levels_complete=True,
        _producer_validated=True,
    )


@dataclass(frozen=True, kw_only=True)
class PaperGateTerminalEnvelope:
    experiment_evidence: tuple[SelectedTerminalEvidence, ...]
    l3_attestation: PaperGateLevelAttestation | None
    l4_attestation: PaperGateLevelAttestation | None
    replay_results: tuple[PaperTraceabilityReplayResult, ...]
    exp2_post_bank_inputs: Exp2PostBankPublicationInputs | None = None
    l1_attestation: PaperGateLevelAttestation | None = None
    l2_attestation: PaperGateLevelAttestation | None = None
    paid_authorizations: tuple[SelectedPaidAuthorizationBinding, ...] = ()
    full_bank: CompleteFullBankPrerequisite | None = None


@dataclass(frozen=True, kw_only=True)
class PaperGateDecision:
    stage: str
    status: str
    classification: str
    blocked_reasons: tuple[str, ...]
    provider_calls: int = 0
    facility_gate_verified: bool = False

    @property
    def paper_eligible(self) -> bool:
        return (
            self.stage == "paper_publication_gate"
            and self.status == "ready"
            and self.classification == "formal"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status,
            "classification": self.classification,
            "blocked_reasons": list(self.blocked_reasons),
            "provider_calls": self.provider_calls,
            "facility_gate_verified": self.facility_gate_verified,
            "paper_eligible": self.paper_eligible,
        }


def selected_experiments_for_provider_scope(scope: str) -> tuple[str, ...]:
    """返回 receipt 中应绑定的实验身份；scope 名本身不是实验身份。"""

    mapping = {
        "epd027_full_bank_acquisition": ("exp2", "exp3", "exp4"),
        "epd027_l3_capability_and_online_checks": ("exp2", "exp3"),
        "exp1_full_online": ("exp1",),
        "exp5_capability_smoke": ("exp5_capability",),
        "exp5_full_online": ("exp5",),
        "results_first_representative_exp1_exp3_exp5": ("exp1", "exp5"),
        "results_first_full_exp1_exp3_exp5": ("exp1", "exp5"),
    }
    try:
        return mapping[scope]
    except KeyError as exc:
        raise ValueError("unsupported provider authorization scope") from exc


def _required_scope_selections(
    selected_experiments: Sequence[str],
    *,
    publication: bool = False,
) -> dict[str, tuple[str, ...]]:
    selected = tuple(selected_experiments)
    requested: dict[str, tuple[str, ...]] = {}
    if "exp1" in selected:
        requested["exp1_full_online"] = ("exp1",)
    if any(value in _BANK_EXPERIMENTS for value in selected):
        # full-bank acquisition 是不可拆分 scope；selected main run 仍只要求这一份 receipt。
        requested["epd027_full_bank_acquisition"] = (
            "exp2",
            "exp3",
            "exp4",
        )
    if publication and any(value in {"exp2", "exp3"} for value in selected):
        requested["epd027_l3_capability_and_online_checks"] = (
            "exp2",
            "exp3",
        )
    if "exp5_capability" in selected:
        requested["exp5_capability_smoke"] = ("exp5_capability",)
    if "exp5" in selected:
        requested["exp5_full_online"] = ("exp5",)
    return {
        scope: requested[scope]
        for scope in _AUTHORIZATION_SCOPE_ORDER
        if scope in requested
    }


def _attestation_reasons(
    attestation: PaperGateLevelAttestation | None,
    *,
    level: str,
    authority: PaperGateAuthorityDigests,
    classification_reason_prefix: str = "publication",
) -> list[str]:
    prefix = level.lower()
    if attestation is None:
        return [f"missing_{prefix}_attestation"]
    reasons: list[str] = []
    if not isinstance(attestation, PaperGateLevelAttestation):
        return [f"invalid_{prefix}_attestation"]
    if attestation.level != level:
        reasons.append(f"{prefix}_attestation_level_mismatch")
    if attestation.status != "passed":
        reasons.append(f"{prefix}_attestation_not_passed")
    if attestation.classification != "formal":
        reasons.append(
            f"{classification_reason_prefix}_requires_formal_{prefix}"
        )
    if attestation.authority_digest != authority.authority_digest:
        reasons.append(f"{prefix}_authority_digest_mismatch")
    return reasons


def _authorization_reasons(
    selection: PaperGateSelectionEnvelope,
    bindings: Sequence[SelectedPaidAuthorizationBinding],
    *,
    dispatch_scopes: frozenset[str] = frozenset(),
    publication: bool = False,
) -> tuple[list[str], dict[str, PaidAuthorizationValidation]]:
    required = _required_scope_selections(
        selection.selected_experiments, publication=publication
    )
    by_scope: dict[str, list[SelectedPaidAuthorizationBinding]] = {}
    for binding in bindings:
        if not isinstance(binding, SelectedPaidAuthorizationBinding):
            continue
        by_scope.setdefault(binding.authority.scope, []).append(binding)

    reasons: list[str] = []
    accepted: dict[str, PaidAuthorizationValidation] = {}
    commitment = selection.authorization_commitment
    committed_by_scope: dict[str, str] = {}
    if not isinstance(commitment, PaidAuthorizationAuthorityCommitment):
        reasons.append("missing_paid_authority_commitment")
    else:
        committed_by_scope = dict(commitment.scope_authority_digests)
        if not set(required) <= set(committed_by_scope):
            reasons.append("paid_authority_commitment_scope_mismatch")
    for scope, expected_selection in required.items():
        candidates = by_scope.get(scope, [])
        if not candidates:
            reasons.append(f"missing_paid_authorization:{scope}")
            continue
        if len(candidates) != 1:
            reasons.append(f"duplicate_paid_authorization:{scope}")
            continue
        binding = candidates[0]
        authority = binding.authority
        validation = binding.validation
        receipt = validation.receipt
        marker = validation.marker
        scope_reasons: list[str] = []
        if isinstance(commitment, PaidAuthorizationAuthorityCommitment):
            if binding.authority_commitment_digest != commitment.commitment_digest:
                scope_reasons.append(
                    f"paid_authority_shared_commitment_mismatch:{scope}"
                )
            receipt_authority_digest = _canonical_digest(
                {
                    "scope": receipt.scope,
                    "authorized_plan_digest": receipt.authorized_plan_digest,
                    "profile_digest": receipt.profile_digest,
                    "budget_digest": receipt.budget_digest,
                    "inventory_digest": receipt.inventory_digest,
                    "prompt_admission_profile_digest": (
                        receipt.prompt_admission_profile_digest
                    ),
                    "selected_experiments": list(receipt.selected_experiments),
                    "output_root_path_digest": receipt.output_root_path_digest,
                }
            )
            if (
                committed_by_scope.get(scope) != authority.authority_digest
                or committed_by_scope.get(scope) != receipt_authority_digest
            ):
                scope_reasons.append(
                    f"paid_authority_commitment_receipt_mismatch:{scope}"
                )
        if receipt.scope != scope:
            scope_reasons.append(f"paid_authorization_scope_mismatch:{scope}")
        if receipt.schema_version != RECEIPT_SCHEMA_VERSION:
            scope_reasons.append(f"paid_receipt_schema_mismatch:{scope}")
        if marker.schema_version != MARKER_SCHEMA_VERSION:
            scope_reasons.append(f"paid_output_binding_schema_mismatch:{scope}")
        if validation.authorization_state not in {
            "authorized",
            "dispatch_authorized",
            "reconcile_close_only",
        }:
            scope_reasons.append(f"paid_authorization_state_invalid:{scope}")
        if scope in dispatch_scopes and (
            not validation.provider_dispatch_allowed
            or validation.authorization_state
            not in {"authorized", "dispatch_authorized"}
        ):
            scope_reasons.append(f"paid_authorization_not_dispatchable:{scope}")
        for receipt_name, authority_name, reason_name in (
            ("profile_digest", "profile_digest", "profile_digest"),
            ("authorized_plan_digest", "authorized_plan_digest", "plan_digest"),
            ("budget_digest", "budget_digest", "budget_digest"),
            ("inventory_digest", "inventory_digest", "inventory_digest"),
            (
                "prompt_admission_profile_digest",
                "prompt_admission_profile_digest",
                "prompt_admission_profile_digest",
            ),
            (
                "output_root_path_digest",
                "output_root_path_digest",
                "output_root_path_digest",
            ),
        ):
            if getattr(receipt, receipt_name) != getattr(authority, authority_name):
                scope_reasons.append(
                    f"paid_authorization_{reason_name}_mismatch:{scope}"
                )
        if (
            tuple(authority.selected_experiments) != expected_selection
            or tuple(receipt.selected_experiments) != expected_selection
        ):
            scope_reasons.append(f"paid_authorization_selection_mismatch:{scope}")
        if any(
            getattr(marker, name, None) != getattr(receipt, name, None)
            for name in _MARKER_BINDING_FIELDS
        ):
            scope_reasons.append(f"paid_output_binding_mismatch:{scope}")
        for name in (
            "receipt_digest",
            "budget_digest",
            "prompt_admission_profile_digest",
            "output_root_path_digest",
        ):
            try:
                _require_digest(getattr(receipt, name), name)
            except ValueError:
                scope_reasons.append(f"paid_authorization_{name}_invalid:{scope}")
        reasons.extend(scope_reasons)
        if not scope_reasons:
            accepted[scope] = validation
    return reasons, accepted


def _full_bank_reasons(
    selection: PaperGateSelectionEnvelope,
    full_bank: CompleteFullBankPrerequisite | None,
    accepted_authorizations: Mapping[str, PaidAuthorizationValidation],
) -> list[str]:
    if not any(value in _BANK_EXPERIMENTS for value in selection.selected_experiments):
        return []
    if not isinstance(full_bank, CompleteFullBankPrerequisite):
        return ["missing_complete_full_bank"]
    if not isinstance(full_bank.manifest, ResponseBankManifest) or not isinstance(
        full_bank.preflight, FormalTraceInventoryPreflightResult
    ):
        return ["invalid_complete_full_bank"]

    reasons: list[str] = []
    manifest = full_bank.manifest
    preflight = full_bank.preflight
    if full_bank.plan_digest != selection.authority.plan_digest:
        reasons.append("full_bank_plan_digest_mismatch")
    if full_bank.inventory_digest != selection.authority.inventory_digest:
        reasons.append("full_bank_inventory_digest_mismatch")
    if manifest.profile_digest != selection.authority.profile_digest:
        reasons.append("full_bank_profile_digest_mismatch")
    if manifest.inventory_digest != selection.authority.inventory_digest:
        reasons.append("full_bank_manifest_inventory_digest_mismatch")
    try:
        ResponseBankManifest.from_dict(manifest.to_dict())
    except (TypeError, ValueError):
        reasons.append("full_bank_manifest_digest_mismatch")
    manifest_ids = tuple(manifest.entry_ids)
    if (
        not manifest_ids
        or manifest.terminal_entry_count != len(manifest_ids)
        or len(set(manifest_ids)) != len(manifest_ids)
        or tuple(manifest.object_role_schema) != tuple(OBJECT_ROLES)
    ):
        reasons.append("full_bank_manifest_incomplete")
    if (
        preflight.status not in {"ready", "complete"}
        or preflight.blocked_records
        or not preflight.required_inventory_entry_ids
        or set(preflight.required_inventory_entry_ids)
        != set(preflight.available_inventory_entry_ids)
        or preflight.provider_call_count != 0
        or preflight.protocol_engine_event_count != 0
    ):
        reasons.append("full_bank_preflight_incomplete")
    authorization = accepted_authorizations.get("epd027_full_bank_acquisition")
    if authorization is not None and (
        manifest.created_by_paid_receipt_digest != authorization.receipt.receipt_digest
        or manifest.budget_digest != authorization.receipt.budget_digest
    ):
        reasons.append("full_bank_paid_authorization_binding_mismatch")
    return reasons


def formal_execution_gate(
    selected_experiments: PaperGateSelectionEnvelope,
    prerequisites: PaperGatePrerequisiteEnvelope,
) -> PaperGateDecision:
    """只检查 selected run 启动前已经可得的 prerequisite authority。"""

    if not isinstance(selected_experiments, PaperGateSelectionEnvelope):
        raise TypeError("formal execution gate requires a typed selection envelope")
    if not isinstance(prerequisites, PaperGatePrerequisiteEnvelope):
        raise TypeError("formal execution gate requires typed prerequisites")
    reasons = [
        *_attestation_reasons(
            prerequisites.l1_attestation,
            level="L1",
            authority=selected_experiments.authority,
            classification_reason_prefix="formal_execution",
        ),
        *_attestation_reasons(
            prerequisites.l2_attestation,
            level="L2",
            authority=selected_experiments.authority,
            classification_reason_prefix="formal_execution",
        ),
    ]
    authorization_reasons, accepted = _authorization_reasons(
        selected_experiments,
        prerequisites.paid_authorizations,
        dispatch_scopes=frozenset(
            {
                "exp1_full_online",
                "exp5_capability_smoke",
                "exp5_full_online",
            }
        ),
    )
    reasons.extend(authorization_reasons)
    reasons.extend(
        _full_bank_reasons(selected_experiments, prerequisites.full_bank, accepted)
    )
    facility_verified = selected_experiments.classification == "facility" and not reasons
    return PaperGateDecision(
        stage="formal_execution_gate",
        status="ready" if not reasons else "blocked",
        classification=selected_experiments.classification,
        blocked_reasons=tuple(reasons),
        facility_gate_verified=facility_verified,
    )


def _terminal_evidence_reasons(
    selection: PaperGateSelectionEnvelope,
    evidence: Sequence[SelectedTerminalEvidence],
) -> list[str]:
    by_experiment: dict[str, list[SelectedTerminalEvidence]] = {}
    for item in evidence:
        if isinstance(item, SelectedTerminalEvidence):
            by_experiment.setdefault(item.experiment_id, []).append(item)
    reasons: list[str] = []
    for experiment in selection.selected_experiments:
        candidates = by_experiment.get(experiment, [])
        if not candidates:
            reasons.append(f"selected_terminal_evidence_missing:{experiment}")
            continue
        if len(candidates) != 1:
            reasons.append(f"selected_terminal_evidence_duplicate:{experiment}")
            continue
        item = candidates[0]
        if item.terminal_status not in {"completed", "completed_with_failures"}:
            reasons.append(f"selected_terminal_evidence_not_completed:{experiment}")
        route_candidates: dict[str, list[SelectedEvidenceRoute]] = {}
        for route in item.routes:
            if isinstance(route, SelectedEvidenceRoute):
                route_candidates.setdefault(route.route_id, []).append(route)
        for route_id, expected_class, table_ids in _SELECTED_ROUTE_SPECS[experiment]:
            routes = route_candidates.get(route_id, [])
            if not routes:
                reasons.append(f"selected_route_missing:{experiment}:{route_id}")
                continue
            if len(routes) != 1:
                reasons.append(f"selected_route_duplicate:{experiment}:{route_id}")
                continue
            route = routes[0]
            if route.evidence_class != expected_class or route.table_ids != table_ids:
                reasons.append(f"selected_route_identity_mismatch:{experiment}:{route_id}")
                continue
            expected_source = (
                "current_real_provider"
                if expected_class == "online_real_provider"
                else "approved_real_full_acquisition"
            )
            reports = tuple(route.eligibility_reports)
            if not reports:
                reasons.append(f"selected_route_eligibility_missing:{experiment}:{route_id}")
            for report in reports:
                if not isinstance(report, VersionedPaperEvidenceEligibilityReport):
                    reasons.append(f"selected_route_ineligible:{experiment}:{route_id}")
                    break
                manifest_valid = (
                    report.source_manifest_complete
                    if expected_class == "real_model_trace_protocol_run"
                    else report.source_manifest_complete is False
                )
                if (
                    not report.paper_eligible
                    or report.ineligibility_reasons
                    or report.evidence_class != expected_class
                    or report.source_classification != expected_source
                    or not report.direct_evidence_complete
                    or not report.identity_consistent
                    or not manifest_valid
                ):
                    reasons.append(f"selected_route_ineligible:{experiment}:{route_id}")
                    break
            reasons.extend(
                _selected_audit_reasons(
                    experiment=experiment,
                    route_id=route_id,
                    expected_class=expected_class,
                    expected_table_ids=table_ids,
                    audits=route.selected_audits,
                )
            )
        if set(selection.selected_experiments) == _FORMAL_EXPERIMENTS:
            report = item.formal_report
            if not isinstance(report, FormalReportResult):
                reasons.append(f"formal_report_missing:{experiment}")
            elif (
                not report.formal_paper_table_generated
                or not report.report_ref
                or not report.renderer_manifest_ref
                or not report.cell_lineage_ref
                or not report.tables_digest
                or not report.cell_lineage_digest
                or not report.observations_digest
            ):
                reasons.append(f"formal_report_artifact_incomplete:{experiment}")
    if set(selection.selected_experiments) == _FORMAL_EXPERIMENTS:
        selected_reports = tuple(
            item.formal_report
            for experiment in selection.selected_experiments
            for item in by_experiment.get(experiment, ())
            if isinstance(item, SelectedTerminalEvidence)
            and isinstance(item.formal_report, FormalReportResult)
        )
        if len(selected_reports) != len(_FORMAL_EXPERIMENTS) or any(
            not report.paper_eligible
            or report.regression_only
            or not report.formal_paper_table_generated
            for report in selected_reports
        ):
            reasons.append("formal_aggregate_report_ineligible")
    return reasons


def _selected_audit_reasons(
    *,
    experiment: str,
    route_id: str,
    expected_class: str,
    expected_table_ids: tuple[str, ...],
    audits: Sequence[SelectedFormalMetricsAudit],
) -> list[str]:
    prefix = f"{experiment}:{route_id}"
    values = tuple(audits)
    if len(values) != 2 or any(
        not isinstance(value, SelectedFormalMetricsAudit) for value in values
    ):
        return [f"selected_audit_pair_missing:{prefix}"]
    reasons: list[str] = []
    for audit in values:
        observation_count = len(audit.observation_ids)
        normalized_refs = tuple(
            sorted(
                (
                    {
                        "path": str(ref.get("path", "")),
                        "content_hash": str(ref.get("content_hash", "")),
                    }
                    for ref in audit.output_refs
                ),
                key=lambda ref: ref["path"],
            )
        )
        if (
            audit.selected_table_ids != expected_table_ids
            or observation_count == 0
            or audit.observation_ids != tuple(sorted(audit.observation_ids))
            or len(set(audit.observation_ids)) != observation_count
            or len(audit.observation_digests) != observation_count
            or len(audit.observation_table_ids) != observation_count
            or len(audit.evidence_classes) != observation_count
            or set(audit.observation_table_ids) != set(expected_table_ids)
            or any(value != expected_class for value in audit.evidence_classes)
            or audit.publish_blocked_observation_ids
            or not audit.non_regression
            or not audit.output_refs
            or any(
                not ref["path"]
                or _DIGEST_PATTERN.fullmatch(ref["content_hash"]) is None
                for ref in normalized_refs
            )
            or audit.output_refs_digest
            != _canonical_digest({"output_refs": list(normalized_refs)})
            or audit.selected_audit_digest
            != selected_formal_metrics_audit_digest(audit.digest_body())
        ):
            reasons.append(f"selected_audit_invalid:{prefix}")
    if values[0] != values[1]:
        reasons.append(f"selected_audit_determinism_mismatch:{prefix}")
    return reasons


def _formal_cell_audit_reasons(
    selection: PaperGateSelectionEnvelope,
    terminal: PaperGateTerminalEnvelope,
) -> list[str]:
    if set(selection.selected_experiments) != _FORMAL_EXPERIMENTS:
        return []
    replays = tuple(terminal.replay_results)
    if len(replays) < 2:
        return ["missing_formal_cell_audit"]
    reasons: list[str] = []
    if len({str(value.output_root) for value in replays}) != len(replays):
        reasons.append("formal_cell_audit_independent_replays_required")
    digests: list[tuple[str, str, str]] = []
    for replay in replays:
        if not isinstance(replay, PaperTraceabilityReplayResult):
            reasons.append("formal_cell_audit_invalid")
            continue
        if replay.audit_level != L4_ARTIFACT_ROOT:
            reasons.append("formal_cell_audit_requires_l4")
        if not isinstance(replay.report, FormalReportResult):
            reasons.append("formal_cell_audit_regression_only")
        elif not replay.report.formal_paper_table_generated:
            reasons.append("formal_cell_audit_report_incomplete")
        elif set(selection.selected_experiments) == _FORMAL_EXPERIMENTS and (
            not replay.report.paper_eligible or replay.report.regression_only
        ):
            reasons.append("formal_aggregate_report_ineligible")
        if replay.provider_calls != 0:
            reasons.append("formal_cell_audit_provider_calls_nonzero")
        if replay.source_write_count != 0 or replay.online_fill_count != 0:
            reasons.append("formal_cell_audit_mutated_sources")
        digests.append(
            (
                replay.observations_digest,
                replay.tables_digest,
                replay.cell_lineage_digest,
            )
        )
    if digests and len(set(digests)) != 1:
        reasons.append("formal_cell_audit_determinism_mismatch")
    if digests:
        expected = digests[0]
        for item in terminal.experiment_evidence:
            if (
                isinstance(item, SelectedTerminalEvidence)
                and item.experiment_id in selection.selected_experiments
                and isinstance(item.formal_report, FormalReportResult)
            ):
                report = item.formal_report
                if (
                    report.observations_digest,
                    report.tables_digest,
                    report.cell_lineage_digest,
                ) != expected:
                    reasons.append(
                        f"formal_cell_audit_report_digest_mismatch:{item.experiment_id}"
                    )
    return list(dict.fromkeys(reasons))


def _exp2_post_bank_reasons(
    selection: PaperGateSelectionEnvelope,
    inputs: Exp2PostBankPublicationInputs | None,
) -> list[str]:
    if "exp2" not in selection.selected_experiments:
        return []
    if not isinstance(inputs, Exp2PostBankPublicationInputs):
        return ["missing_exp2_post_bank_inputs"]
    if (
        inputs.contract_digest != selection.authority.contract_digest
        or inputs.profile_digest != selection.authority.profile_digest
        or inputs.authority_digest != selection.authority.authority_digest
        or inputs.contract.contract_digest != inputs.contract_digest
        or inputs.contract.pipeline_profile_digest != inputs.profile_digest
    ):
        return ["exp2_post_bank_authority_binding_mismatch"]
    if not inputs.worker_levels_complete:
        return ["exp2_post_bank_worker_levels_incomplete"]
    try:
        trace_projection = build_exp2_trace_observations(
            inputs.trace_roots, inputs.contract
        )
        online_projection = build_exp2_online_observations(
            inputs.online_roots,
            inputs.contract,
            complete_main_trace=trace_projection,
            main_trace_complete=inputs.worker_levels_complete,
        )
        check = online_projection.post_bank_check
    except (AttributeError, TypeError, ValueError):
        return ["exp2_post_bank_projection_blocked"]
    reasons: list[str] = []
    if check.status != "evaluated_post_bank":
        reasons.append("exp2_post_bank_not_evaluated")
    intersections = dict(check.same_case_intersection_count_by_worker)
    expected_compared_workers = set(EXP2_WORKER_COUNTS) - {1}
    if set(intersections) != expected_compared_workers:
        reasons.append("exp2_post_bank_worker_levels_incomplete")
    for worker, count in sorted(intersections.items()):
        if count < 3:
            reasons.append(f"exp2_post_bank_intersection_below_3:{worker}")
    if check.severe_worker_counts or check.severe_reasons_by_worker:
        reasons.append("exp2_post_bank_severe_divergence")
    return reasons


def paper_publication_gate(
    selected_experiments: PaperGateSelectionEnvelope,
    terminal_evidence: PaperGateTerminalEnvelope,
) -> PaperGateDecision:
    """在 terminal outputs 后独立决定 selected formal evidence 是否可发布。"""

    if not isinstance(selected_experiments, PaperGateSelectionEnvelope):
        raise TypeError("paper publication gate requires a typed selection envelope")
    if not isinstance(terminal_evidence, PaperGateTerminalEnvelope):
        raise TypeError("paper publication gate requires typed terminal evidence")
    reasons = _terminal_evidence_reasons(
        selected_experiments, terminal_evidence.experiment_evidence
    )
    if selected_experiments.classification != "formal":
        reasons.append("publication_requires_formal_selection")
    for level, attestation in (
        ("L1", terminal_evidence.l1_attestation),
        ("L2", terminal_evidence.l2_attestation),
        ("L3", terminal_evidence.l3_attestation),
        ("L4", terminal_evidence.l4_attestation),
    ):
        reasons.extend(
            _attestation_reasons(
                attestation, level=level, authority=selected_experiments.authority
            )
        )
    authorization_reasons, accepted = _authorization_reasons(
        selected_experiments,
        terminal_evidence.paid_authorizations,
        publication=True,
    )
    reasons.extend(authorization_reasons)
    reasons.extend(
        _full_bank_reasons(
            selected_experiments, terminal_evidence.full_bank, accepted
        )
    )
    reasons.extend(_formal_cell_audit_reasons(selected_experiments, terminal_evidence))
    reasons.extend(
        _exp2_post_bank_reasons(
            selected_experiments, terminal_evidence.exp2_post_bank_inputs
        )
    )
    return PaperGateDecision(
        stage="paper_publication_gate",
        status="ready" if not reasons else "blocked",
        classification=selected_experiments.classification,
        blocked_reasons=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "CompleteFullBankPrerequisite",
    "Exp2PostBankPublicationInputs",
    "PaperGateAuthorityDigests",
    "PaperGateDecision",
    "PaperGateLevelAttestation",
    "PaidAuthorizationAuthorityCommitment",
    "PaidAuthorizationScopeAuthority",
    "PaperGatePrerequisiteEnvelope",
    "PaperGateSelectionEnvelope",
    "PaperGateTerminalEnvelope",
    "SelectedEvidenceRoute",
    "SelectedFormalMetricsAudit",
    "SelectedPaidAuthorizationBinding",
    "SelectedTerminalEvidence",
    "build_paid_authorization_authority_commitment",
    "formal_execution_gate",
    "load_exp2_post_bank_publication_inputs",
    "paper_publication_gate",
    "selected_formal_metrics_audit_digest",
    "selected_experiments_for_provider_scope",
]
