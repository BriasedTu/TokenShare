"""Fixture helpers for the pinned Lean proof project and protocol E2E flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from tokenshare.core.contribution import ContributionCoordinator, ContributionRecord
from tokenshare.core.expansion import ExpectedOutputRef, ExpansionDecision, SplitStrategyInvocation
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord, digest_json
from tokenshare.core.merge_coordinator import BatchView, MergeCoordinator, MergeTaskCreationFlowResult
from tokenshare.core.models import (
    ArtifactRef,
    Attempt,
    ClientRecord,
    JsonObject,
    Lease,
    ProtocolConfig,
    TaskState,
    TaskUnit,
)
from tokenshare.core.registration import RootTaskRegistrar, RootTaskRegistrationRequest
from tokenshare.core.state_machines import transition_lease
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import build_verification_report
from tokenshare.executors.contracts import (
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerReport,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.child_proof import LeanChildProofResult, check_lean_child_proof
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.merge_policy import (
    LeanProofMergeInput,
    LeanProofMergeResult,
    merge_lean_child_proofs,
)
from tokenshare.plugins.lean_proof.models import LeanFixtureManifest, canonical_json_digest
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
    THEOREM_PAYLOAD_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
    schema_ref,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperReport,
    LeanSplitHelperRequest,
    LeanSplitPlanResult,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.plugins.lean_proof.validator import LeanValidationResult, verify_lean_checker_report
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.protocol_engine import (
    CanonicalBindingFlowResult,
    CompleteDecisionFlowResult,
    ExecutionRequestFlowResult,
    ExecutionSubmissionFlowResult,
    ExpandDecisionFlowResult,
    MergeResolutionFlowResult,
    ParentCompletionFlowResult,
    ProtocolEngine,
    RegistrySnapshotFlowResult,
    SchedulingFlowResult,
    SettlementFlowResult,
    VerificationFlowResult,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


NOW = "2026-06-29T00:00:00Z"
TASK_ID = "task_lean_proof_fixture"
ROOT_UNIT_ID = "unit_lean_theorem_root"
REGISTRY_SNAPSHOT_ID = "registry_snapshot_lean_proof"
EXECUTOR_ID = "executor_lean_checker_fixture"
EXECUTOR_VERSION = "0.1.0"
SETTLEMENT_POLICY_ID = "sandbox_equal_weight_v1"


@dataclass(frozen=True)
class LeanFixtureSubmission:
    request: ExecutionRequestFlowResult
    submission: ExecutionSubmissionFlowResult


@dataclass(frozen=True)
class LeanChildFixtureResult:
    child_unit: TaskUnit
    child_payload_ref: ArtifactRef
    proof_candidate_ref: ArtifactRef
    scheduled: SchedulingFlowResult
    submission: LeanFixtureSubmission
    verification: VerificationFlowResult
    canonical: CanonicalBindingFlowResult
    child_result: LeanChildProofResult

    @property
    def accepted(self) -> bool:
        return self.child_result.accepted

    @property
    def merge_ready(self) -> bool:
        return self.child_result.merge_ready


@dataclass(frozen=True)
class LeanDirectProofFixtureFlowResult:
    engine: ProtocolEngine
    ledger: EventLedger
    store: ArtifactStore
    graph: TaskGraph
    environment_manifest: LeanEnvironmentManifest
    theorem_payload: LeanTheoremPayload
    theorem_payload_ref: ArtifactRef
    proof_candidate_ref: ArtifactRef
    checker_report: LeanCheckerReport
    canonical_proof_ref: ArtifactRef | None
    root_submission: LeanFixtureSubmission
    verification: VerificationFlowResult
    root_canonical: CanonicalBindingFlowResult | None
    split_invocation_event: LedgerEvent | None
    complete_result: CompleteDecisionFlowResult | None
    settlement: SettlementFlowResult | None


@dataclass(frozen=True)
class LeanDecompositionFixtureFlowResult:
    engine: ProtocolEngine
    ledger: EventLedger
    store: ArtifactStore
    graph: TaskGraph
    environment_manifest: LeanEnvironmentManifest
    theorem_payload: LeanTheoremPayload
    theorem_payload_ref: ArtifactRef
    root_canonical: CanonicalBindingFlowResult
    split_report: LeanSplitHelperReport
    split_plan: LeanSplitPlanResult
    split_invocation_event: LedgerEvent
    expand_result: ExpandDecisionFlowResult | None
    child_results: tuple[LeanChildFixtureResult, ...]
    child_canonicals: tuple[CanonicalBindingFlowResult, ...]
    merge_task_creation: MergeTaskCreationFlowResult | None
    merge_policy_result: LeanProofMergeResult | None
    merge_submission: LeanFixtureSubmission | None
    merge_canonical: CanonicalBindingFlowResult | None
    merge_record: MergeRecord | None
    resolution: ExpectedOutputResolution | None
    merge_resolution: MergeResolutionFlowResult | None
    expand_contribution: ContributionRecord | None
    merge_contribution: ContributionRecord | None
    parent_completion: ParentCompletionFlowResult | None
    settlement: SettlementFlowResult | None


@dataclass(frozen=True)
class LeanUnsupportedDecompositionFixtureFlowResult:
    engine: ProtocolEngine
    ledger: EventLedger
    store: ArtifactStore
    environment_manifest: LeanEnvironmentManifest
    theorem_payload: LeanTheoremPayload
    theorem_payload_ref: ArtifactRef
    root_canonical: CanonicalBindingFlowResult
    split_report: LeanSplitHelperReport
    split_invocation_event: LedgerEvent
    unsupported_reason: str
    expand_result: None
    complete_result: None
    settlement: None


def default_lean_fixture_project_path() -> Path:
    return Path(__file__).resolve().parents[4] / "fixtures" / "lean_proof_project"


def build_lean_fixture_manifest(
    *,
    project_root: Path | None = None,
) -> LeanFixtureManifest:
    root = (project_root or default_lean_fixture_project_path()).resolve()
    helper_sources = _helper_source_digests(root)
    return LeanFixtureManifest(
        project_root=str(root),
        toolchain_file="lean-toolchain",
        toolchain_file_digest=_file_digest(root / "lean-toolchain"),
        lakefile=_lakefile_name(root),
        lakefile_digest=_file_digest(root / _lakefile_name(root)),
        helper_sources=helper_sources,
        helper_sources_digest=canonical_json_digest(helper_sources),
        fixture_cases=_fixture_cases(),
    )


def run_lean_direct_proof_fixture_flow(
    root_path: str | Path,
    *,
    root_budget: int = 10,
    proof_kind: str = "valid",
) -> LeanDirectProofFixtureFlowResult:
    """Run a direct Lean proof through Phase 3-5 lifecycle events."""

    context = _new_context(root_path)
    theorem_payload = _direct_theorem_payload(proof_kind=proof_kind)
    prepared = _prepare_direct_root(
        context,
        theorem_payload=theorem_payload,
        root_budget=root_budget,
        proof_kind=proof_kind,
    )
    proof_source = "by\n  rfl" if proof_kind == "valid" else "by\n  exact False.elim (by contradiction)"
    proof_candidate_ref = _save_proof_candidate(
        context.store,
        artifact_id=f"proof_candidate_direct_{proof_kind}",
        theorem_payload=theorem_payload,
        proof_source=proof_source,
    )
    checker_report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"lean_checker_request:direct:{proof_kind}",
            theorem_payload_ref=prepared.theorem_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            environment_ref=build_lean_environment_ref(context.environment_manifest),
            checker_mode=LeanCheckerMode.DIRECT_PROOF,
            timeout_seconds=int(theorem_payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(theorem_payload.resource_limits["max_output_bytes"]),
            created_at=NOW,
        ),
        artifact_store=context.store,
        environment_manifest=context.environment_manifest,
    )
    canonical_proof_ref = _canonical_proof_ref_from_checker(
        context.store,
        checker_report=checker_report,
        artifact_id=f"canonical_direct_proof_{proof_kind}",
    )
    root_submission = _record_submission(
        context=context,
        scheduled=prepared.root_scheduled,
        output_contract=_proof_output_contract(),
        candidate_output_refs=(
            {PROOF_ARTIFACT_OUTPUT_NAME: canonical_proof_ref}
            if canonical_proof_ref is not None
            else {}
        ),
        request_id=f"request_lean_direct_{proof_kind}",
        submission_id=f"submission_lean_direct_{proof_kind}",
        parsed_output_ref=proof_candidate_ref,
        raw_output_ref=None,
        log_ref=checker_report.report_ref,
        result_kind="succeeded" if checker_report.status == LeanCheckerStatus.ACCEPTED else "invalid_output",
        hard_requirements={"executor": "deterministic_local_lean_checker", "lean_checker": True},
    )
    _release_lease(
        engine=context.engine,
        lease=prepared.root_scheduled.lease,
        reason="lean_direct_checker_completed",
    )
    validation = verify_lean_checker_report(checker_report)
    verification = _record_lean_verification(
        context=context,
        attempt=_require_submitted_attempt(root_submission),
        submission=root_submission.submission.submission,
        candidate_output_refs=(
            {PROOF_ARTIFACT_OUTPUT_NAME: canonical_proof_ref}
            if canonical_proof_ref is not None
            else {}
        ),
        required_output_names=[PROOF_ARTIFACT_OUTPUT_NAME],
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        validation=validation,
        verification_report_id=f"verification_lean_direct_{proof_kind}",
    )

    if checker_report.status != LeanCheckerStatus.ACCEPTED:
        return LeanDirectProofFixtureFlowResult(
            engine=context.engine,
            ledger=context.ledger,
            store=context.store,
            graph=prepared.graph,
            environment_manifest=context.environment_manifest,
            theorem_payload=theorem_payload,
            theorem_payload_ref=prepared.theorem_payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            checker_report=checker_report,
            canonical_proof_ref=None,
            root_submission=root_submission,
            verification=verification,
            root_canonical=None,
            split_invocation_event=None,
            complete_result=None,
            settlement=None,
        )

    root_canonical = context.engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        verification_events=[verification.event],
        attempts_by_id={_require_attempt(verification).attempt_id: _require_attempt(verification)},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_lean_direct_root_canonical",
    )
    invocation = _record_split_invocation(
        context=context,
        unit_id=ROOT_UNIT_ID,
        canonical=root_canonical,
        action="complete",
        result_digest=canonical_json_digest(
            {
                "action": "complete",
                "checker_report_id": checker_report.report_id,
                "proof_digest": checker_report.proof_digest,
            }
        ),
        expansion_scope_hash=_expansion_scope_hash(prepared.theorem_payload_ref),
    )
    complete_result = _record_complete_decision(
        context=context,
        root_unit=prepared.root_scheduled.task_unit,
        root_canonical=root_canonical,
        verification=verification,
        split_invocation_event=invocation.event,
        expansion_scope_hash=_expansion_scope_hash(prepared.theorem_payload_ref),
        completion_summary="direct Lean proof accepted by pinned checker",
    )
    contribution = ContributionCoordinator(
        event_ledger=context.engine._event_ledger
    ).record_canonical_contributions(
        task_id=TASK_ID,
        completion_batches=[_batch(complete_result.events)],
        expansion_batches=[],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_lean_direct_contribution",
    )[0].contribution
    settlement = context.engine.record_root_settlement(
        task_id=TASK_ID,
        root_unit_id=ROOT_UNIT_ID,
        root_completion_event_seq=complete_result.events[1].event_seq,
        eligible_contributions=[contribution],
        root_budget=root_budget,
        settlement_policy_id=SETTLEMENT_POLICY_ID,
        now=NOW,
        correlation_id="corr_lean_direct_settlement",
    )
    return LeanDirectProofFixtureFlowResult(
        engine=context.engine,
        ledger=context.ledger,
        store=context.store,
        graph=TaskGraph(
            task_id=TASK_ID,
            units={ROOT_UNIT_ID: complete_result.task_unit},
            relations=[],
            canonical_outputs_by_unit_id={
                ROOT_UNIT_ID: root_canonical.canonical_selection.canonical_output_refs
            },
            protocol_config=context.config,
        ),
        environment_manifest=context.environment_manifest,
        theorem_payload=theorem_payload,
        theorem_payload_ref=prepared.theorem_payload_ref,
        proof_candidate_ref=proof_candidate_ref,
        checker_report=checker_report,
        canonical_proof_ref=canonical_proof_ref,
        root_submission=root_submission,
        verification=verification,
        root_canonical=root_canonical,
        split_invocation_event=invocation.event,
        complete_result=complete_result,
        settlement=settlement,
    )


def run_lean_decomposition_fixture_flow(
    root_path: str | Path,
    *,
    root_budget: int = 10,
    stop_after_canonical_child_count: int | None = None,
) -> LeanDecompositionFixtureFlowResult:
    """Run Lean split -> child proof -> verified merge through Phase 3-5 events."""

    context = _new_context(root_path)
    theorem_payload = _decomposition_theorem_payload()
    prepared = _prepare_root_theorem(context, theorem_payload=theorem_payload, root_budget=root_budget)
    split_report = run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id="lean_split_request:decomposition_flow",
            theorem_payload_ref=prepared.theorem_payload_ref,
            environment_ref=build_lean_environment_ref(context.environment_manifest),
            timeout_seconds=int(theorem_payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(theorem_payload.resource_limits["max_output_bytes"]),
            created_at=NOW,
        ),
        artifact_store=context.store,
        environment_manifest=context.environment_manifest,
    )
    if split_report.certificate is None:
        raise ValueError("decomposition fixture requires a Lean split certificate")
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=context.store,
        task_id=TASK_ID,
        parent_unit_id=ROOT_UNIT_ID,
        canonical_selection_id=prepared.root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            prepared.root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=_expansion_scope_hash(prepared.theorem_payload_ref),
        expansion_decision_id=_expansion_decision_id(prepared.theorem_payload_ref),
        created_at=NOW,
    )
    split_invocation = _record_split_invocation(
        context=context,
        unit_id=ROOT_UNIT_ID,
        canonical=prepared.root_canonical,
        action="expand",
        result_digest=canonical_json_digest(split_plan.to_dict()),
        expansion_scope_hash=_expansion_scope_hash(prepared.theorem_payload_ref),
        params_digest=split_report.certificate.certificate_digest,
    )
    decision = _expand_decision(
        root_canonical=prepared.root_canonical,
        split_plan=split_plan,
        split_invocation_event=split_invocation.event,
        theorem_payload_ref=prepared.theorem_payload_ref,
    )
    expand_result = context.engine.record_expand_decision(
        decision=decision,
        proposal=split_plan.proposal,
        merge_plan=split_plan.merge_plan,
        parent_unit=prepared.root_scheduled.task_unit,
        graph=prepared.graph,
        correlation_id="corr_lean_expand",
        causation_event_id=split_invocation.event.event_id,
    )
    child_limit = (
        len(expand_result.child_units)
        if stop_after_canonical_child_count is None
        else stop_after_canonical_child_count
    )
    child_results: list[LeanChildFixtureResult] = []
    graph_for_children = expand_result.task_graph
    for index, child_unit in enumerate(expand_result.child_units[:child_limit]):
        child_results.append(
            _execute_child_proof(
                context=context,
                graph=graph_for_children,
                split_plan=split_plan,
                child_unit=child_unit,
                index=index,
            )
        )
        graph_for_children = _replace_graph_unit_with_canonical(
            graph_for_children,
            child_results[-1].canonical,
        )

    merge_task_creation = _create_merge_task(
        context=context,
        graph=expand_result.task_graph,
        expand_result=expand_result,
        child_results=child_results,
    )
    if merge_task_creation is None:
        return LeanDecompositionFixtureFlowResult(
            engine=context.engine,
            ledger=context.ledger,
            store=context.store,
            graph=expand_result.task_graph,
            environment_manifest=context.environment_manifest,
            theorem_payload=theorem_payload,
            theorem_payload_ref=prepared.theorem_payload_ref,
            root_canonical=prepared.root_canonical,
            split_report=split_report,
            split_plan=split_plan,
            split_invocation_event=split_invocation.event,
            expand_result=expand_result,
            child_results=tuple(child_results),
            child_canonicals=tuple(child.canonical for child in child_results),
            merge_task_creation=None,
            merge_policy_result=None,
            merge_submission=None,
            merge_canonical=None,
            merge_record=None,
            resolution=None,
            merge_resolution=None,
            expand_contribution=None,
            merge_contribution=None,
            parent_completion=None,
            settlement=None,
        )

    merge_policy_result = merge_lean_child_proofs(
        merge_plan=split_plan.merge_plan,
        split_certificate=split_plan.certificate,
        parent_theorem_payload_ref=prepared.theorem_payload_ref,
        child_proofs=_merge_inputs(merge_task_creation, child_results),
        artifact_store=context.store,
        environment_manifest=context.environment_manifest,
        merge_unit_id=merge_task_creation.merge_task_unit.unit_id,
        request_id="lean_merge_checker:decomposition_flow",
        created_at=NOW,
    )
    if not merge_policy_result.accepted or merge_policy_result.root_proof_artifact_ref is None:
        raise ValueError("Lean decomposition fixture expected accepted merge proof")
    merge_canonical_proof_ref = _canonical_proof_ref_from_checker(
        context.store,
        checker_report=merge_policy_result.root_checker_report,
        artifact_id="canonical_lean_merge_root_proof",
    )
    if merge_canonical_proof_ref is None:
        raise ValueError("accepted Lean merge checker report missing canonical proof ref")
    merge_scheduled = _schedule_unit(
        context=context,
        graph=_graph_with_merge_unit(expand_result.task_graph, merge_task_creation.merge_task_unit),
        unit=merge_task_creation.merge_task_unit,
        attempt_id="attempt_lean_merge",
        lease_id="lease_lean_merge",
        decision_id="decision_lean_merge",
        fencing_token="token_lean_merge",
    )
    merge_submission = _record_submission(
        context=context,
        scheduled=merge_scheduled,
        output_contract=_proof_output_contract(),
        candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: merge_canonical_proof_ref},
        request_id="request_lean_merge",
        submission_id="submission_lean_merge",
        parsed_output_ref=merge_policy_result.merge_result_ref,
        raw_output_ref=None,
        log_ref=merge_policy_result.root_checker_report.report_ref,
        result_kind="succeeded",
        hard_requirements={"executor": "deterministic_local_lean_checker", "lean_merge": True},
    )
    _release_lease(
        engine=context.engine,
        lease=merge_scheduled.lease,
        reason="lean_merge_checker_completed",
    )
    merge_verification = _record_lean_verification(
        context=context,
        attempt=_require_submitted_attempt(merge_submission),
        submission=merge_submission.submission.submission,
        candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: merge_canonical_proof_ref},
        required_output_names=[PROOF_ARTIFACT_OUTPUT_NAME],
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        validation=verify_lean_checker_report(merge_policy_result.root_checker_report),
        verification_report_id="verification_lean_merge",
    )
    merge_canonical = context.engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=merge_task_creation.merge_task_unit.unit_id,
        verification_events=[merge_verification.event],
        attempts_by_id={_require_attempt(merge_verification).attempt_id: _require_attempt(merge_verification)},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_lean_merge_canonical",
    )
    merge_record = _merge_record(
        merge_creation=merge_task_creation,
        merge_plan=split_plan.merge_plan,
        merge_canonical=merge_canonical,
    )
    resolution = _expected_output_resolution(
        expected_output_refs=expand_result.expected_output_refs,
        merge_record=merge_record,
        resolved_output_ref=merge_canonical_proof_ref,
    )
    merge_resolution = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_lean_merge_resolution",
        causation_event_id=merge_canonical.event.event_id,
    )
    contribution_results = ContributionCoordinator(
        event_ledger=context.engine._event_ledger
    ).record_canonical_contributions(
        task_id=TASK_ID,
        completion_batches=[],
        expansion_batches=[_batch(expand_result.events)],
        merge_resolution_batches=[_batch(merge_resolution.events)],
        now=NOW,
        correlation_id="corr_lean_decomposition_contributions",
    )
    expand_contribution = contribution_results[0].contribution
    merge_contribution = contribution_results[1].contribution
    parent_completion = context.engine.record_parent_completion(
        owner_unit=prepared.root_scheduled.task_unit,
        expected_output_refs=list(expand_result.expected_output_refs),
        expected_output_resolutions=[resolution],
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_lean_parent_completion",
        causation_event_id=merge_resolution.events[-1].event_id,
    )
    settlement = context.engine.record_root_settlement(
        task_id=TASK_ID,
        root_unit_id=ROOT_UNIT_ID,
        root_completion_event_seq=parent_completion.events[0].event_seq,
        eligible_contributions=[
            parent_completion.expand_contributions[0],
            merge_contribution,
        ],
        root_budget=root_budget,
        settlement_policy_id=SETTLEMENT_POLICY_ID,
        now=NOW,
        correlation_id="corr_lean_decomposition_settlement",
    )
    return LeanDecompositionFixtureFlowResult(
        engine=context.engine,
        ledger=context.ledger,
        store=context.store,
        graph=expand_result.task_graph,
        environment_manifest=context.environment_manifest,
        theorem_payload=theorem_payload,
        theorem_payload_ref=prepared.theorem_payload_ref,
        root_canonical=prepared.root_canonical,
        split_report=split_report,
        split_plan=split_plan,
        split_invocation_event=split_invocation.event,
        expand_result=expand_result,
        child_results=tuple(child_results),
        child_canonicals=tuple(child.canonical for child in child_results),
        merge_task_creation=merge_task_creation,
        merge_policy_result=merge_policy_result,
        merge_submission=merge_submission,
        merge_canonical=merge_canonical,
        merge_record=merge_record,
        resolution=resolution,
        merge_resolution=merge_resolution,
        expand_contribution=expand_contribution,
        merge_contribution=merge_contribution,
        parent_completion=parent_completion,
        settlement=settlement,
    )


def run_lean_unsupported_decomposition_fixture_flow(
    root_path: str | Path,
) -> LeanUnsupportedDecompositionFixtureFlowResult:
    """Run only the canonical theorem payload and unsupported split audit path."""

    context = _new_context(root_path)
    theorem_payload = _unsupported_theorem_payload()
    prepared = _prepare_root_theorem(context, theorem_payload=theorem_payload, root_budget=10)
    split_report = run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id="lean_split_request:unsupported_flow",
            theorem_payload_ref=prepared.theorem_payload_ref,
            environment_ref=build_lean_environment_ref(context.environment_manifest),
            timeout_seconds=int(theorem_payload.resource_limits["timeout_seconds"]),
            max_output_bytes=int(theorem_payload.resource_limits["max_output_bytes"]),
            created_at=NOW,
        ),
        artifact_store=context.store,
        environment_manifest=context.environment_manifest,
    )
    if split_report.certificate is None:
        raise ValueError("unsupported fixture requires split certificate")
    invocation = _record_split_invocation(
        context=context,
        unit_id=ROOT_UNIT_ID,
        canonical=prepared.root_canonical,
        action=None,
        result_digest=None,
        expansion_scope_hash=_expansion_scope_hash(prepared.theorem_payload_ref),
        params_digest=split_report.certificate.certificate_digest,
        status="invalid_result",
        error_kind="unsupported_decomposition",
        error_summary=split_report.certificate.unsupported_reason
        or "unsupported_decomposition",
    )
    return LeanUnsupportedDecompositionFixtureFlowResult(
        engine=context.engine,
        ledger=context.ledger,
        store=context.store,
        environment_manifest=context.environment_manifest,
        theorem_payload=theorem_payload,
        theorem_payload_ref=prepared.theorem_payload_ref,
        root_canonical=prepared.root_canonical,
        split_report=split_report,
        split_invocation_event=invocation.event,
        unsupported_reason=split_report.certificate.unsupported_reason or "",
        expand_result=None,
        complete_result=None,
        settlement=None,
    )


def _fixture_cases() -> dict[str, JsonObject]:
    return {
        "lean_direct_proof": {
            "case_id": "lean_direct_proof",
            "theorem_name": "one_eq_one",
            "capabilities": ["direct_proof"],
            "expected_status": "accepted",
            "fixture_file": "TokenShare/Fixtures/Direct.lean",
        },
        "lean_decomposition_merge": {
            "case_id": "lean_decomposition_merge",
            "theorem_name": "and_swap",
            "capabilities": ["decomposition", "child_proof", "merge_proof"],
            "expected_status": "accepted",
            "fixture_file": "TokenShare/Fixtures/Decomposition.lean",
        },
        "lean_unsupported_decomposition": {
            "case_id": "lean_unsupported_decomposition",
            "theorem_name": "unsupported_goal_shape",
            "capabilities": ["unsupported_decomposition"],
            "expected_status": "unsupported_decomposition",
            "fixture_file": "TokenShare/Fixtures/Unsupported.lean",
        },
        "lean_invalid_proof": {
            "case_id": "lean_invalid_proof",
            "theorem_name": "invalid_proof",
            "capabilities": ["direct_proof"],
            "expected_status": "rejected",
            "fixture_file": "TokenShare/Fixtures/Invalid.lean",
        },
    }


def _helper_source_digests(project_root: Path) -> dict[str, str]:
    helper_dir = project_root / "TokenShare"
    if not helper_dir.is_dir():
        raise FileNotFoundError(f"missing Lean helper directory: {helper_dir}")
    digests: dict[str, str] = {}
    for path in sorted(helper_dir.rglob("*.lean")):
        relative = path.relative_to(project_root).as_posix()
        digests[relative] = _file_digest(path)
    required = {
        "TokenShare/Helper.lean",
        "TokenShare/SplitRules.lean",
        "TokenShare/Merge.lean",
    }
    missing = sorted(required.difference(digests))
    if missing:
        raise FileNotFoundError(f"missing Lean helper sources: {', '.join(missing)}")
    return digests


def _lakefile_name(project_root: Path) -> str:
    if (project_root / "lakefile.lean").is_file():
        return "lakefile.lean"
    if (project_root / "lakefile.toml").is_file():
        return "lakefile.toml"
    raise FileNotFoundError(f"missing lakefile in {project_root}")


def _file_digest(path: Path) -> str:
    data = path.read_bytes()
    return f"sha256:{sha256(data).hexdigest()}"


@dataclass(frozen=True)
class _FixtureContext:
    root_path: Path
    store: ArtifactStore
    ledger: EventLedger
    config: ProtocolConfig
    engine: ProtocolEngine
    registry: RegistrySnapshotFlowResult
    environment_manifest: LeanEnvironmentManifest


@dataclass(frozen=True)
class _PreparedRoot:
    theorem_payload_ref: ArtifactRef
    root_scheduled: SchedulingFlowResult
    root_submission: LeanFixtureSubmission
    root_verification: VerificationFlowResult
    root_canonical: CanonicalBindingFlowResult
    graph: TaskGraph


@dataclass(frozen=True)
class _PreparedDirectRoot:
    theorem_payload_ref: ArtifactRef
    root_scheduled: SchedulingFlowResult
    graph: TaskGraph


def _new_context(root_path: str | Path) -> _FixtureContext:
    root = Path(root_path)
    store = ArtifactStore(root)
    ledger = EventLedger(root / "events" / f"{TASK_ID}.jsonl")
    config = ProtocolConfig.default(
        config_id="config_lean_proof_fixture",
        artifact_store_uri="file://artifacts",
        event_log_uri=f"file://events/{TASK_ID}.jsonl",
        metadata={"fixture": "lean_proof"},
    )
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config, artifact_store=store)
    environment_manifest = _environment_manifest()
    return _FixtureContext(
        root_path=root,
        store=store,
        ledger=ledger,
        config=config,
        engine=engine,
        registry=_record_registry(engine),
        environment_manifest=environment_manifest,
    )


def _environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    return LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=NOW,
    )


def _record_registry(engine: ProtocolEngine) -> RegistrySnapshotFlowResult:
    plugin_registry = PluginRegistry()
    executor_registry = ExecutorRegistry()
    plugin_registry.register(build_lean_proof_plugin_descriptor())
    executor_registry.register(
        ExecutorDescriptor(
            executor_id=EXECUTOR_ID,
            executor_type="deterministic_local_lean_checker",
            executor_version=EXECUTOR_VERSION,
            supported_request_schema_versions=["phase3.execution_request.v1"],
            capabilities={
                "executor": "deterministic_local_lean_checker",
                "lean_checker": True,
                "lean_helper": True,
                "lean_merge": True,
            },
            environment_policy={"runtime": "lean", "network_access": False},
            status=ExecutorStatus.AVAILABLE,
            metadata={"fixture": "lean_proof"},
        )
    )
    return engine.record_registry_snapshot(
        task_id=TASK_ID,
        registry_snapshot_id=REGISTRY_SNAPSHOT_ID,
        plugin_registry=plugin_registry,
        executor_registry=executor_registry,
        now=NOW,
        correlation_id="corr_lean_registry",
    )


def _prepare_root_theorem(
    context: _FixtureContext,
    *,
    theorem_payload: LeanTheoremPayload,
    root_budget: int,
) -> _PreparedRoot:
    theorem_payload_ref = _save_json_artifact(
        context.store,
        theorem_payload.to_dict(),
        artifact_id="lean_theorem_payload_root",
        artifact_type="canonical_output",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        output_name=THEOREM_PAYLOAD_OUTPUT_NAME,
    )
    root_registration = _register_root_task(
        context=context,
        root_input_ref=theorem_payload_ref,
        root_budget=root_budget,
    )
    root_unit_ready = _replace_unit(
        root_registration.root_unit,
        unit_type="lean_theorem",
        input_refs={"root_input": theorem_payload_ref},
        required_capabilities={
            "executor": "deterministic_local_lean_checker",
            "lean_checker": True,
        },
        plugin_payload={
            "schema_version": "lean_proof.root_plugin_payload.v1",
            "required_outputs": [PROOF_ARTIFACT_OUTPUT_NAME],
            "requested_outputs": [PROOF_ARTIFACT_OUTPUT_NAME],
            "theorem_payload_digest": theorem_payload.payload_digest,
        },
        metadata={"fixture": "lean_proof", "theorem_name": theorem_payload.theorem_name},
    )
    root_graph = TaskGraph(
        task_id=TASK_ID,
        units={ROOT_UNIT_ID: root_unit_ready},
        relations=[],
        canonical_outputs_by_unit_id={},
        protocol_config=context.config,
    )
    root_scheduled = _schedule_unit(
        context=context,
        graph=root_graph,
        unit=root_unit_ready,
        attempt_id="attempt_lean_root_payload",
        lease_id="lease_lean_root_payload",
        decision_id="decision_lean_root_payload",
        fencing_token="token_lean_root_payload",
    )
    root_submission = _record_submission(
        context=context,
        scheduled=root_scheduled,
        output_contract=_theorem_payload_output_contract(),
        candidate_output_refs={THEOREM_PAYLOAD_OUTPUT_NAME: theorem_payload_ref},
        request_id="request_lean_root_payload",
        submission_id="submission_lean_root_payload",
        parsed_output_ref=theorem_payload_ref,
        raw_output_ref=None,
        log_ref=None,
        result_kind="succeeded",
        hard_requirements={"executor": "deterministic_local_lean_checker"},
    )
    _release_lease(
        engine=context.engine,
        lease=root_scheduled.lease,
        reason="lean_theorem_payload_recorded",
    )
    verification = _record_theorem_payload_verification(
        context=context,
        attempt=_require_submitted_attempt(root_submission),
        submission=root_submission.submission.submission,
        theorem_payload_ref=theorem_payload_ref,
    )
    root_canonical = context.engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        verification_events=[verification.event],
        attempts_by_id={_require_attempt(verification).attempt_id: _require_attempt(verification)},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_lean_root_payload_canonical",
    )
    graph = TaskGraph(
        task_id=TASK_ID,
        units={ROOT_UNIT_ID: root_scheduled.task_unit},
        relations=[],
        canonical_outputs_by_unit_id={
            ROOT_UNIT_ID: root_canonical.canonical_selection.canonical_output_refs
        },
        protocol_config=context.config,
    )
    return _PreparedRoot(
        theorem_payload_ref=theorem_payload_ref,
        root_scheduled=root_scheduled,
        root_submission=root_submission,
        root_verification=verification,
        root_canonical=root_canonical,
        graph=graph,
    )


def _prepare_direct_root(
    context: _FixtureContext,
    *,
    theorem_payload: LeanTheoremPayload,
    root_budget: int,
    proof_kind: str,
) -> _PreparedDirectRoot:
    theorem_payload_ref = _save_json_artifact(
        context.store,
        theorem_payload.to_dict(),
        artifact_id=f"lean_theorem_payload_direct_{proof_kind}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        output_name="lean_theorem_payload",
    )
    root_registration = _register_root_task(
        context=context,
        root_input_ref=theorem_payload_ref,
        root_budget=root_budget,
    )
    root_unit_ready = _replace_unit(
        root_registration.root_unit,
        unit_type="lean_theorem",
        input_refs={"theorem_payload": theorem_payload_ref},
        required_capabilities={
            "executor": "deterministic_local_lean_checker",
            "lean_checker": True,
        },
        plugin_payload={
            "schema_version": "lean_proof.root_plugin_payload.v1",
            "required_outputs": [PROOF_ARTIFACT_OUTPUT_NAME],
            "requested_outputs": [PROOF_ARTIFACT_OUTPUT_NAME],
            "theorem_payload_digest": theorem_payload.payload_digest,
            "direct_proof": True,
        },
        metadata={
            "fixture": "lean_proof",
            "theorem_name": theorem_payload.theorem_name,
            "proof_kind": proof_kind,
        },
    )
    root_graph = TaskGraph(
        task_id=TASK_ID,
        units={ROOT_UNIT_ID: root_unit_ready},
        relations=[],
        canonical_outputs_by_unit_id={},
        protocol_config=context.config,
    )
    root_scheduled = _schedule_unit(
        context=context,
        graph=root_graph,
        unit=root_unit_ready,
        attempt_id=f"attempt_lean_direct_{proof_kind}",
        lease_id=f"lease_lean_direct_{proof_kind}",
        decision_id=f"decision_lean_direct_{proof_kind}",
        fencing_token=f"token_lean_direct_{proof_kind}",
    )
    return _PreparedDirectRoot(
        theorem_payload_ref=theorem_payload_ref,
        root_scheduled=root_scheduled,
        graph=root_graph,
    )


def _register_root_task(
    *,
    context: _FixtureContext,
    root_input_ref: ArtifactRef,
    root_budget: int,
):
    registrar = RootTaskRegistrar(
        artifact_store=context.store,
        event_ledger=context.ledger,
    )
    return registrar.register_root_task(
        RootTaskRegistrationRequest(
            task_id=TASK_ID,
            root_unit_id=ROOT_UNIT_ID,
            root_artifact_id="lean_root_input_marker",
            description="Lean proof fixture",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            split_strategy_params={},
            root_input_bytes=_json_bytes(
                {
                    "schema_version": "lean_proof.root_input.v1",
                    "theorem_payload_ref": root_input_ref.to_dict(),
                    "requested_output": PROOF_ARTIFACT_OUTPUT_NAME,
                }
            ),
            root_input_media_type="application/json",
            root_input_schema_id="lean_proof.root_input",
            root_input_schema_version="v1",
            protocol_config=context.config,
            required_capabilities={
                "executor": "deterministic_local_lean_checker",
                "lean_checker": True,
            },
            plugin_payload={"required_outputs": [PROOF_ARTIFACT_OUTPUT_NAME]},
            metadata={"fixture": "lean_proof"},
            created_at=NOW,
            root_budget=float(root_budget),
        )
    )


def _record_submission(
    *,
    context: _FixtureContext,
    scheduled: SchedulingFlowResult,
    output_contract: OutputContract,
    candidate_output_refs: dict[str, ArtifactRef],
    request_id: str,
    submission_id: str,
    parsed_output_ref: ArtifactRef | None,
    raw_output_ref: ArtifactRef | None,
    log_ref: ArtifactRef | None,
    result_kind: str,
    hard_requirements: JsonObject,
) -> LeanFixtureSubmission:
    environment_ref = build_lean_environment_ref(context.environment_manifest)
    request = ExecutionRequest(
        request_id=request_id,
        task_id=TASK_ID,
        unit_id=scheduled.task_unit.unit_id,
        attempt_id=scheduled.attempt.attempt_id,
        lease_id=scheduled.lease.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        plugin=context.registry.snapshot.plugin_entries[0],
        executor=context.registry.snapshot.executor_entries[0],
        registry_snapshot_id=context.registry.snapshot.registry_snapshot_id,
        allocation_decision={
            "decision_id": f"allocation_{request_id}",
            "selected_executor_id": EXECUTOR_ID,
            "eligible_executor_ids": [EXECUTOR_ID],
            "rejected_executor_reasons": {},
            "tie_break": ["executor_id"],
        },
        capability_snapshot={
            "executor": "deterministic_local_lean_checker",
            "status": "Available",
            "lean_checker": True,
        },
        task_unit_snapshot=scheduled.task_unit.to_dict(),
        input_artifact_refs=scheduled.task_unit.input_refs,
        output_contract=output_contract,
        hard_requirements=hard_requirements,
        soft_hints={"fixture": "lean_proof"},
        environment_ref=environment_ref,
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={"timeout_seconds": 30},
        created_at=NOW,
    )
    request_flow = context.engine.record_execution_request(
        request=request,
        correlation_id=f"corr_{request_id}",
        causation_event_id=scheduled.events[-1].event_id,
    )
    submission = ExecutionSubmission(
        submission_id=submission_id,
        request_id=request_id,
        task_id=TASK_ID,
        unit_id=scheduled.task_unit.unit_id,
        attempt_id=scheduled.attempt.attempt_id,
        lease_id=scheduled.lease.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        executor_id=EXECUTOR_ID,
        executor_version=EXECUTOR_VERSION,
        result_kind=result_kind,
        raw_output_ref=raw_output_ref,
        parsed_output_ref=parsed_output_ref,
        candidate_output_refs=candidate_output_refs,
        parse_failure_ref=None,
        log_ref=log_ref,
        environment_ref=environment_ref,
        environment_summary={
            "runtime": "lean",
            "network_access": False,
            "environment_digest": environment_ref.environment_digest,
        },
        provenance_ref=None,
        usage_summary={"deterministic_fixture": True},
        error=None if result_kind == "succeeded" else {"kind": result_kind},
        submitted_at=NOW,
    )
    submission_flow = context.engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id=f"corr_{submission_id}",
        causation_event_id=request_flow.event.event_id,
    )
    return LeanFixtureSubmission(request=request_flow, submission=submission_flow)


def _record_theorem_payload_verification(
    *,
    context: _FixtureContext,
    attempt: Attempt,
    submission: ExecutionSubmission,
    theorem_payload_ref: ArtifactRef,
) -> VerificationFlowResult:
    layer = {
        "status": "passed",
        "reason_code": "lean_theorem_payload_structured",
        "summary": "structured Lean theorem payload persisted as canonical split input",
        "details": {"real_checker_evidence": False},
        "evidence_refs": [theorem_payload_ref.artifact_id],
        "checked_at": NOW,
    }
    report = build_verification_report(
        verification_report_id="verification_lean_root_payload",
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=submission.submission_id,
        submission_event_seq=_submission_event_seq(context.engine, submission.submission_id),
        candidate_output_refs={THEOREM_PAYLOAD_OUTPUT_NAME: theorem_payload_ref},
        required_output_names=[THEOREM_PAYLOAD_OUTPUT_NAME],
        output_contract_id="lean_proof.root_theorem.contract.v1",
        validator_policy_id="lean_proof.theorem_payload.validator.v1",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        status="passed",
        expected_artifact_hashes={THEOREM_PAYLOAD_OUTPUT_NAME: theorem_payload_ref.content_hash},
        required_evidence_ref_ids=[theorem_payload_ref.artifact_id],
        available_evidence_ref_ids=[theorem_payload_ref.artifact_id],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={
            "runtime": "lean_fixture",
            "environment_digest": context.environment_manifest.environment_digest,
        },
        verifier={"verifier_id": "lean_payload_fixture_verifier", "verifier_version": "1"},
        started_at=NOW,
        completed_at=NOW,
        metadata={"plugin_domain_layer": layer},
    )
    return context.engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id="corr_verification_lean_root_payload",
    )


def _record_lean_verification(
    *,
    context: _FixtureContext,
    attempt: Attempt,
    submission: ExecutionSubmission,
    candidate_output_refs: dict[str, ArtifactRef],
    required_output_names: list[str],
    output_contract_id: str,
    validation: LeanValidationResult,
    verification_report_id: str,
) -> VerificationFlowResult:
    evidence_ids = _validation_evidence_ids(validation)
    report = build_verification_report(
        verification_report_id=verification_report_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=submission.submission_id,
        submission_event_seq=_submission_event_seq(context.engine, submission.submission_id),
        candidate_output_refs=candidate_output_refs,
        required_output_names=required_output_names,
        output_contract_id=output_contract_id,
        validator_policy_id=CHECKER_VALIDATOR_POLICY_ID,
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        status="passed" if validation.accepted else "rejected",
        expected_artifact_hashes={
            name: ref.content_hash for name, ref in candidate_output_refs.items()
        },
        required_evidence_ref_ids=evidence_ids,
        available_evidence_ref_ids=evidence_ids,
        plugin_domain_status="passed" if validation.accepted else "rejected",
        audit_status="passed",
        verification_environment={
            "runtime": "lean",
            "environment_digest": context.environment_manifest.environment_digest,
        },
        verifier={"verifier_id": "lean_checker_fixture_verifier", "verifier_version": "1"},
        started_at=NOW,
        completed_at=NOW,
        metadata={"plugin_domain_layer": validation.to_phase4_layer_summary()},
    )
    return context.engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id=f"corr_{verification_report_id}",
    )


def _record_split_invocation(
    *,
    context: _FixtureContext,
    unit_id: str,
    canonical: CanonicalBindingFlowResult,
    action: str | None,
    result_digest: str | None,
    expansion_scope_hash: str,
    params_digest: str | None = None,
    status: str = "succeeded",
    error_kind: str | None = None,
    error_summary: str | None = None,
):
    invocation = SplitStrategyInvocation(
        invocation_id=(
            f"lean_split_invocation:{_stable_id_component(expansion_scope_hash)}:"
            f"{action or status}"
        ),
        invocation_attempt_no=1,
        expansion_scope_hash=expansion_scope_hash,
        task_id=TASK_ID,
        unit_id=unit_id,
        canonical_selection_id=canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            canonical.canonical_selection.canonical_output_bundle_digest
        ),
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        split_strategy_params_digest=params_digest
        or canonical_json_digest({"strategy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID}),
        status=status,
        result_action=action,
        result_digest=result_digest,
        error_kind=error_kind,
        error_summary=error_summary,
        started_at=NOW,
        completed_at=NOW,
    )
    return context.engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id=f"corr_lean_split_invocation_{action or status}",
        causation_event_id=canonical.event.event_id,
    )


def _record_complete_decision(
    *,
    context: _FixtureContext,
    root_unit: TaskUnit,
    root_canonical: CanonicalBindingFlowResult,
    verification: VerificationFlowResult,
    split_invocation_event: LedgerEvent,
    expansion_scope_hash: str,
    completion_summary: str,
) -> CompleteDecisionFlowResult:
    decision = ExpansionDecision(
        expansion_decision_id=_expansion_decision_id_from_scope(expansion_scope_hash),
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        expansion_scope_hash=expansion_scope_hash,
        action="complete",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        split_strategy_params_digest=split_invocation_event.payload[
            "split_strategy_params_digest"
        ],
        source_invocation_id=split_invocation_event.object_id,
        action_body={
            "completion_evidence": {
                "completion_kind": "lean_direct_proof",
                "validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
                "verification_report_id": verification.report.verification_report_id,
                "canonical_selection_id": (
                    root_canonical.canonical_selection.canonical_selection_id
                ),
                "canonical_output_bundle_digest": (
                    root_canonical.canonical_selection.canonical_output_bundle_digest
                ),
                "completed_output_refs": {
                    name: ref.to_dict()
                    for name, ref in root_canonical.canonical_selection.canonical_output_refs.items()
                },
                "plugin_completion_summary": completion_summary,
            }
        },
        decided_at=NOW,
    )
    return context.engine.record_complete_decision(
        decision=decision,
        task_unit=root_unit,
        correlation_id="corr_lean_complete_decision",
        causation_event_id=split_invocation_event.event_id,
    )


def _expand_decision(
    *,
    root_canonical: CanonicalBindingFlowResult,
    split_plan: LeanSplitPlanResult,
    split_invocation_event: LedgerEvent,
    theorem_payload_ref: ArtifactRef,
) -> ExpansionDecision:
    return ExpansionDecision(
        expansion_decision_id=_expansion_decision_id(theorem_payload_ref),
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        expansion_scope_hash=_expansion_scope_hash(theorem_payload_ref),
        action="expand",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        split_strategy_params_digest=split_plan.certificate.certificate_digest,
        source_invocation_id=split_invocation_event.object_id,
        proposal_id=split_plan.proposal.proposal_header["proposal_id"],
        proposal_digest=split_plan.proposal.proposal_header["proposal_digest"],
        merge_plan_id=split_plan.merge_plan.merge_plan_header["merge_plan_id"],
        merge_plan_digest=split_plan.merge_plan.merge_plan_header["merge_plan_digest"],
        action_body={
            "expand_evidence": {
                "proposal_id": split_plan.proposal.proposal_header["proposal_id"],
                "proposal_digest": split_plan.proposal.proposal_header["proposal_digest"],
                "merge_plan_id": split_plan.merge_plan.merge_plan_header["merge_plan_id"],
                "merge_plan_digest": split_plan.merge_plan.merge_plan_header[
                    "merge_plan_digest"
                ],
                "child_count": len(split_plan.proposal.child_specs),
                "relation_count": len(split_plan.proposal.dependency_edges),
                "expected_output_count": len(split_plan.proposal.expected_outputs),
                "required_merge_slot_count": len(split_plan.merge_plan.required_slots),
            }
        },
        decided_at=NOW,
    )


def _execute_child_proof(
    *,
    context: _FixtureContext,
    graph: TaskGraph,
    split_plan: LeanSplitPlanResult,
    child_unit: TaskUnit,
    index: int,
) -> LeanChildFixtureResult:
    child_key = child_unit.metadata["child_logical_key"]
    child_payload_ref = split_plan.child_payload_refs_by_logical_key[child_key]
    child_payload = LeanTheoremPayload.from_dict(
        json.loads(context.store.read_bytes(child_payload_ref).decode("utf-8"))
    )
    proof_candidate_ref = _save_proof_candidate(
        context.store,
        artifact_id=f"proof_candidate_{_stable_id_component(child_key)}",
        theorem_payload=child_payload,
        proof_source=_child_proof_source(child_key),
    )
    child_result = check_lean_child_proof(
        child_logical_key=child_key,
        split_certificate=split_plan.certificate,
        child_payload_ref=child_payload_ref,
        proof_candidate_ref=proof_candidate_ref,
        artifact_store=context.store,
        environment_manifest=context.environment_manifest,
        request_id=f"lean_child_checker:{child_key}",
        created_at=NOW,
    )
    if child_result.checker_report is None:
        raise ValueError("child fixture expected checker report")
    canonical_ref = _canonical_proof_ref_from_checker(
        context.store,
        checker_report=child_result.checker_report,
        artifact_id=f"canonical_child_proof_{index}",
    )
    if canonical_ref is None:
        raise ValueError("child fixture expected accepted canonical proof")
    scheduled = _schedule_unit(
        context=context,
        graph=graph,
        unit=child_unit,
        attempt_id=f"attempt_lean_child_{index}",
        lease_id=f"lease_lean_child_{index}",
        decision_id=f"decision_lean_child_{index}",
        fencing_token=f"token_lean_child_{index}",
    )
    submission = _record_submission(
        context=context,
        scheduled=scheduled,
        output_contract=_proof_output_contract(),
        candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: canonical_ref},
        request_id=f"request_lean_child_{index}",
        submission_id=f"submission_lean_child_{index}",
        parsed_output_ref=proof_candidate_ref,
        raw_output_ref=None,
        log_ref=child_result.checker_report.report_ref,
        result_kind="succeeded",
        hard_requirements={"executor": "deterministic_local_lean_checker", "lean_checker": True},
    )
    _release_lease(
        engine=context.engine,
        lease=scheduled.lease,
        reason="lean_child_checker_completed",
    )
    verification = _record_lean_verification(
        context=context,
        attempt=_require_submitted_attempt(submission),
        submission=submission.submission.submission,
        candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: canonical_ref},
        required_output_names=[PROOF_ARTIFACT_OUTPUT_NAME],
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        validation=verify_lean_checker_report(child_result.checker_report),
        verification_report_id=f"verification_lean_child_{index}",
    )
    canonical = context.engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=child_unit.unit_id,
        verification_events=[verification.event],
        attempts_by_id={_require_attempt(verification).attempt_id: _require_attempt(verification)},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id=f"corr_lean_child_{index}_canonical",
    )
    return LeanChildFixtureResult(
        child_unit=child_unit,
        child_payload_ref=child_payload_ref,
        proof_candidate_ref=proof_candidate_ref,
        scheduled=scheduled,
        submission=submission,
        verification=verification,
        canonical=canonical,
        child_result=child_result,
    )


def _create_merge_task(
    *,
    context: _FixtureContext,
    graph: TaskGraph,
    expand_result: ExpandDecisionFlowResult,
    child_results: list[LeanChildFixtureResult],
) -> MergeTaskCreationFlowResult | None:
    coordinator = MergeCoordinator(
        event_ledger=context.engine._event_ledger,
        artifact_store=context.store,
        protocol_config=context.config,
    )
    try:
        results = coordinator.create_ready_merge_tasks(
            task_id=TASK_ID,
            graph=graph,
            merge_plan_events=[
                event
                for event in expand_result.events
                if event.event_type == EventType.MERGE_PLAN_RECORDED
            ],
            expansion_batches=[_batch(expand_result.events)],
            canonical_events=[child.canonical.event for child in child_results],
            now=NOW,
            coordinator_id="lean_fixture_merge_coordinator",
            correlation_id="corr_lean_merge_creation",
        )
    except ValueError as exc:
        if "required slots" in str(exc):
            return None
        raise
    return results[0] if results else None


def _merge_inputs(
    merge_creation: MergeTaskCreationFlowResult,
    child_results: list[LeanChildFixtureResult],
) -> list[LeanProofMergeInput]:
    by_unit_id = {child.canonical.canonical_selection.unit_id: child for child in child_results}
    inputs: list[LeanProofMergeInput] = []
    for binding in merge_creation.merge_task_link.required_slot_bindings:
        inputs.append(
            LeanProofMergeInput(
                slot_key=binding.slot_key,
                child_proof=by_unit_id[binding.source_child_unit_id].child_result,
            )
        )
    return inputs


def _merge_record(
    *,
    merge_creation: MergeTaskCreationFlowResult,
    merge_plan,
    merge_canonical: CanonicalBindingFlowResult,
) -> MergeRecord:
    canonical_selection = merge_canonical.canonical_selection
    link = merge_creation.merge_task_link
    return MergeRecord(
        merge_record_id=(
            f"merge_record:{link.merge_plan_id}:"
            f"{link.merge_unit_id}:{canonical_selection.canonical_selection_id}"
        ),
        task_id=link.task_id,
        parent_unit_id=link.parent_unit_id,
        merge_plan_id=link.merge_plan_id,
        merge_unit_id=link.merge_unit_id,
        merge_task_link_id=link.merge_task_link_id,
        merge_input_bundle_ref=link.merge_input_bundle_ref,
        merge_input_bundle_digest=link.merge_input_bundle_digest,
        required_slot_bindings_digest=link.required_slot_bindings_digest,
        merge_policy_id=link.merge_policy_id,
        merge_policy_version=link.merge_policy_version,
        merge_policy_descriptor_digest=link.merge_policy_descriptor_digest,
        merge_policy_params_digest=merge_plan.merge_policy_ref["merge_policy_params_digest"],
        canonical_selection_id=canonical_selection.canonical_selection_id,
        canonical_event_seq=merge_canonical.event.event_seq,
        selected_verification_report_id=canonical_selection.selected_verification_report_id,
        selected_verification_event_seq=canonical_selection.selected_verification_event_seq,
        selected_submission_id=canonical_selection.selected_submission_id,
        selected_submission_event_seq=canonical_selection.selected_submission_event_seq,
        selected_attempt_id=canonical_selection.selected_attempt_id,
        merge_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        merge_output_refs={
            name: ref.to_dict()
            for name, ref in canonical_selection.canonical_output_refs.items()
        },
        parent_output_mapping_digest=digest_json(merge_plan.parent_output_mapping),
        created_at=NOW,
    )


def _expected_output_resolution(
    *,
    expected_output_refs: Iterable[ExpectedOutputRef],
    merge_record: MergeRecord,
    resolved_output_ref: ArtifactRef,
) -> ExpectedOutputResolution:
    refs = list(expected_output_refs)
    if len(refs) != 1:
        raise ValueError("Lean fixture expects one parent expected output")
    expected_ref = refs[0]
    return ExpectedOutputResolution(
        expected_output_resolution_id=(
            f"expected_output_resolved:{expected_ref.expected_output_id}:"
            f"{merge_record.merge_record_id}"
        ),
        task_id=merge_record.task_id,
        owner_unit_id=merge_record.parent_unit_id,
        expected_output_id=expected_ref.expected_output_id,
        expected_output_name=expected_ref.output_name,
        resolution_source_type="merge_record",
        merge_record_id=merge_record.merge_record_id,
        merge_plan_id=merge_record.merge_plan_id,
        merge_unit_id=merge_record.merge_unit_id,
        merge_canonical_selection_id=merge_record.canonical_selection_id,
        resolved_output_ref=resolved_output_ref.to_dict(),
        resolved_output_digest=resolved_output_ref.content_hash,
        resolved_at=NOW,
    )


def _canonical_proof_ref_from_checker(
    store: ArtifactStore,
    *,
    checker_report: LeanCheckerReport,
    artifact_id: str,
) -> ArtifactRef | None:
    if checker_report.status != LeanCheckerStatus.ACCEPTED:
        return None
    if checker_report.proof_artifact_ref is None:
        raise ValueError("accepted Lean checker report missing proof artifact")
    proof_source = store.read_bytes(checker_report.proof_artifact_ref)
    return store.save_bytes(
        proof_source,
        artifact_id=artifact_id,
        artifact_type="canonical_output",
        media_type="text/x-lean",
        artifact_schema_id="lean_proof.proof_artifact",
        artifact_schema_version="v1",
        source={"kind": "lean_fixture", "checker_report_id": checker_report.report_id},
        metadata={
            "output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            "proof_digest": checker_report.proof_digest,
            "checker_report_ref": (
                checker_report.report_ref.to_dict()
                if checker_report.report_ref is not None
                else None
            ),
        },
        created_at=NOW,
    )


def _save_proof_candidate(
    store: ArtifactStore,
    *,
    artifact_id: str,
    theorem_payload: LeanTheoremPayload,
    proof_source: str,
) -> ArtifactRef:
    return store.save_json(
        {
            "schema_version": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
            "proof_candidate_id": f"proof_candidate:{artifact_id}",
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": proof_source,
            "created_at": NOW,
        },
        artifact_id=artifact_id,
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "lean_fixture"},
        metadata={"theorem_name": theorem_payload.theorem_name},
        created_at=NOW,
    )


def _theorem_payload_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id="lean_proof.root_theorem.contract.v1",
        required_outputs=[THEOREM_PAYLOAD_OUTPUT_NAME],
        output_schema_refs={
            THEOREM_PAYLOAD_OUTPUT_NAME: schema_ref(LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION)
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
    )


def _proof_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        required_outputs=[PROOF_ARTIFACT_OUTPUT_NAME],
        output_schema_refs={
            PROOF_ARTIFACT_OUTPUT_NAME: schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION)
        },
        raw_output_policy={"allowed": True, "authoritative": False, "media_type": "text/plain"},
        parsed_output_schema_ref=schema_ref(LEAN_PROOF_CANDIDATE_SCHEMA_VERSION),
    )


def _schedule_unit(
    *,
    context: _FixtureContext,
    graph: TaskGraph,
    unit: TaskUnit,
    attempt_id: str,
    lease_id: str,
    decision_id: str,
    fencing_token: str,
) -> SchedulingFlowResult:
    scheduling_graph = graph
    if graph.units.get(unit.unit_id) != unit:
        scheduling_graph = TaskGraph(
            task_id=graph.task_id,
            units={**graph.units, unit.unit_id: unit},
            relations=graph.relations,
            canonical_outputs_by_unit_id=graph.canonical_outputs_by_unit_id,
            protocol_config=graph.protocol_config,
        )
    return context.engine.schedule_ready_unit(
        graph=scheduling_graph,
        clients=[
            _client(
                client_id=f"client_{attempt_id}",
                capabilities={
                    "executor": [
                        "deterministic_local_lean_checker",
                        "deterministic_local_lean_helper",
                        "local",
                    ],
                    "lean_checker": True,
                    "lean_helper": True,
                    "lean_merge": True,
                },
            )
        ],
        now=NOW,
        correlation_id=f"corr_schedule_{attempt_id}",
        decision_id=decision_id,
        lease_id=lease_id,
        attempt_id=attempt_id,
        fencing_token=fencing_token,
    )


def _client(client_id: str, capabilities: JsonObject) -> ClientRecord:
    return ClientRecord(
        client_id=client_id,
        executor_type="deterministic_local_lean_checker",
        executor_id=EXECUTOR_ID,
        executor_version=EXECUTOR_VERSION,
        capabilities=capabilities,
        status="active",
        stats={},
        metadata={"fixture": "lean_proof"},
        registered_at=NOW,
    )


def _release_lease(*, engine: ProtocolEngine, lease: Lease, reason: str) -> None:
    released = transition_lease(
        lease,
        new_state="Released",
        changed_at=NOW,
        reason=reason,
    )
    engine._event_ledger.append(
        event_type=EventType.LEASE_STATE_CHANGED,
        object_type="Lease",
        object_id=released.lease_id,
        task_id=released.task_id,
        actor={"kind": "lean_fixture"},
        correlation_id=f"corr_release_{released.lease_id}",
        idempotency_key=f"lease:terminal:{released.lease_id}:Released",
        payload={
            "old_state": "Active",
            "new_state": "Released",
            "lease": released.to_dict(),
            "reason": reason,
            "correlation_id": f"corr_release_{released.lease_id}",
        },
        occurred_at=NOW,
    )


def _batch(events: Iterable[LedgerEvent]) -> BatchView:
    event_tuple = tuple(events)
    return BatchView(batch_id=event_tuple[0].batch_id or "", events=event_tuple)


def _submission_event_seq(engine: ProtocolEngine, submission_id: str) -> int:
    for event in reversed(engine._event_ledger.read_all()):
        if (
            event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
            and event.object_id == submission_id
        ):
            return event.event_seq
    raise ValueError(f"missing execution submission event: {submission_id}")


def _require_submitted_attempt(submission: LeanFixtureSubmission) -> Attempt:
    if submission.submission.attempt is None:
        raise ValueError("fixture submission did not advance attempt")
    return submission.submission.attempt


def _require_attempt(verification: VerificationFlowResult) -> Attempt:
    if verification.attempt is None:
        raise ValueError("fixture verification did not advance attempt")
    return verification.attempt


def _validation_evidence_ids(validation: LeanValidationResult) -> list[str]:
    evidence = validation.layer_summary.get("evidence_refs", [])
    if not isinstance(evidence, list):
        return []
    return [str(item) for item in evidence]


def _save_json_artifact(
    store: ArtifactStore,
    body: JsonObject,
    *,
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
    artifact_schema_version: str,
    output_name: str,
) -> ArtifactRef:
    return store.save_json(
        body,
        artifact_id=_stable_id_component(artifact_id),
        artifact_type=artifact_type,
        artifact_schema_id=artifact_schema_id,
        artifact_schema_version=artifact_schema_version,
        source={"kind": "lean_fixture"},
        metadata={"output_name": output_name, "task_id": TASK_ID},
        created_at=NOW,
    )


def _replace_unit(unit: TaskUnit, **updates) -> TaskUnit:
    data = unit.to_dict()
    data.pop("schema_version", None)
    data.update(updates)
    if isinstance(data["state"], str):
        data["state"] = TaskState(data["state"])
    data["input_refs"] = {
        key: ArtifactRef.from_dict(value) if isinstance(value, dict) else value
        for key, value in data["input_refs"].items()
    }
    data["canonical_output_refs"] = {
        key: ArtifactRef.from_dict(value) if isinstance(value, dict) else value
        for key, value in data["canonical_output_refs"].items()
    }
    return TaskUnit(**data)


def _replace_graph_unit_with_canonical(
    graph: TaskGraph,
    canonical: CanonicalBindingFlowResult,
) -> TaskGraph:
    unit = graph.units[canonical.canonical_selection.unit_id]
    updated = _replace_unit(
        unit,
        state=TaskState.PROCESSING,
        canonical_output_refs=canonical.canonical_selection.canonical_output_refs,
        updated_at=NOW,
    )
    canonical_outputs = dict(graph.canonical_outputs_by_unit_id)
    canonical_outputs[updated.unit_id] = canonical.canonical_selection.canonical_output_refs
    return TaskGraph(
        task_id=graph.task_id,
        units={**graph.units, updated.unit_id: updated},
        relations=graph.relations,
        canonical_outputs_by_unit_id=canonical_outputs,
        protocol_config=graph.protocol_config,
    )


def _graph_with_merge_unit(graph: TaskGraph, merge_unit: TaskUnit) -> TaskGraph:
    return TaskGraph(
        task_id=graph.task_id,
        units={**graph.units, merge_unit.unit_id: merge_unit},
        relations=graph.relations,
        canonical_outputs_by_unit_id=graph.canonical_outputs_by_unit_id,
        protocol_config=graph.protocol_config,
    )


def _direct_theorem_payload(*, proof_kind: str) -> LeanTheoremPayload:
    del proof_kind
    return LeanTheoremPayload(
        theorem_id="lean_theorem:one_eq_one",
        theorem_name="one_eq_one",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="",
        statement_source="1 = 1",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={"project": "tokenshare_lean", "module": "TokenShareGenerated.Direct"},
        decomposition_policy={
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["leaf_close"],
            "max_depth": 0,
            "max_children": 0,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )


def _decomposition_theorem_payload() -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id="lean_theorem:and_intro_fixture",
        theorem_name="and_intro_fixture",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="(P Q : Prop) (hP : P) (hQ : Q)",
        statement_source="P ∧ Q",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={"project": "tokenshare_lean", "module": "TokenShareGenerated.Split"},
        decomposition_policy={
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["conjunction", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )


def _unsupported_theorem_payload() -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id="lean_theorem:unsupported_goal_shape",
        theorem_name="unsupported_goal_shape",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="(n : Nat)",
        statement_source="Nat.succ n = 0",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={"project": "tokenshare_lean", "module": "TokenShareGenerated.Split"},
        decomposition_policy={
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["conjunction", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )


def _child_proof_source(child_logical_key: str) -> str:
    if child_logical_key == "child:left":
        return "by\n  exact hP"
    if child_logical_key == "child:right":
        return "by\n  exact hQ"
    if child_logical_key == "child:forward":
        return "by\n  exact hpq"
    if child_logical_key == "child:backward":
        return "by\n  exact hqp"
    raise ValueError(f"unsupported fixture child proof: {child_logical_key}")


def _expansion_scope_hash(theorem_payload_ref: ArtifactRef) -> str:
    return canonical_json_digest(
        {
            "task_id": TASK_ID,
            "unit_id": ROOT_UNIT_ID,
            "theorem_payload_digest": theorem_payload_ref.content_hash,
        }
    )


def _expansion_decision_id(theorem_payload_ref: ArtifactRef) -> str:
    return _expansion_decision_id_from_scope(_expansion_scope_hash(theorem_payload_ref))


def _expansion_decision_id_from_scope(scope_hash: str) -> str:
    return f"expansion_decision:{_stable_id_component(scope_hash)}"


def _stable_id_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_" for character in value
    )


def _json_bytes(data: JsonObject) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
