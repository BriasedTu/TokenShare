"""Phase 6 factorization prime / semiprime fixture flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tokenshare.core.contribution import (
    ContributionCoordinator,
    ContributionRecord,
)
from tokenshare.core.expansion import (
    ExpansionDecision,
    ExpectedOutputRef,
    SplitStrategyInvocation,
)
from tokenshare.core.merge import (
    ExpectedOutputResolution,
    MergeRecord,
    digest_json,
)
from tokenshare.core.merge_coordinator import BatchView, MergeCoordinator, MergeTaskCreationFlowResult
from tokenshare.core.models import (
    ArtifactRef,
    Attempt,
    AttemptState,
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
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.merge_policy import (
    FactorizationMergePolicyResult,
    RangeSlotMergeInput,
    merge_required_range_results,
)
from tokenshare.plugins.factorization.models import (
    FactorIntegerSubject,
    FactorSearchRangeInput,
    PrimeFactorizationResult,
    RangeResult,
    RootInput,
    canonical_json_digest,
)
from tokenshare.plugins.factorization.schemas import (
    FACTOR_INTEGER_SUBJECT_OUTPUT_NAME,
    FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
    FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
    FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION,
    FACTORIZATION_MERGE_TASK_TYPE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    ROOT_INPUT_SCHEMA_VERSION,
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    schema_ref,
)
from tokenshare.plugins.factorization.prompt_builder import build_factor_search_prompt_package
from tokenshare.plugins.factorization.split_strategy import build_factorization_split_plan
from tokenshare.plugins.factorization.split_strategy import (
    FactorizationSplitStrategyActionResult,
    build_factorization_split_strategy_result,
)
from tokenshare.plugins.factorization.validator import (
    RangeVerificationResult,
    build_factor_search_instruction,
    verify_factor_integer_subject,
    verify_range_result,
)
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


NOW = "2026-06-28T00:00:00Z"
TASK_ID = "task_factorization_fixture"
ROOT_UNIT_ID = "unit_factor_integer_root"
REGISTRY_SNAPSHOT_ID = "registry_snapshot_factorization"
EXECUTOR_ID = "executor_factorization_fixture"
EXECUTOR_VERSION = "0.1.0"
SETTLEMENT_POLICY_ID = "sandbox_equal_weight_v1"


@dataclass(frozen=True)
class FixtureSubmission:
    request: ExecutionRequestFlowResult
    submission: ExecutionSubmissionFlowResult


@dataclass(frozen=True)
class FixtureRangeExecution:
    range_input: FactorSearchRangeInput
    range_result: RangeResult
    unit: TaskUnit
    scheduled: SchedulingFlowResult
    request: ExecutionRequestFlowResult
    submission: ExecutionSubmissionFlowResult
    verification: VerificationFlowResult
    canonical: CanonicalBindingFlowResult
    executor_observation: dict[str, int]
    range_output_ref: ArtifactRef


@dataclass(frozen=True)
class FactorizationFixtureFlowResult:
    engine: ProtocolEngine
    ledger: EventLedger
    store: ArtifactStore
    graph: TaskGraph
    root_unit: TaskUnit
    subject: FactorIntegerSubject
    subject_ref: ArtifactRef
    root_submission: FixtureSubmission
    root_verification: VerificationFlowResult
    root_canonical: CanonicalBindingFlowResult
    split_invocation_event: LedgerEvent
    complete_result: CompleteDecisionFlowResult | None
    expand_result: ExpandDecisionFlowResult | None
    range_executions: tuple[FixtureRangeExecution, ...]
    range_results: tuple[FixtureRangeExecution, ...]
    range_verifications: tuple[VerificationFlowResult, ...]
    range_canonical_events: tuple[LedgerEvent, ...]
    executor_observations: tuple[dict[str, int], ...]
    merge_task_creations: tuple[MergeTaskCreationFlowResult, ...]
    merge_policy_result: FactorizationMergePolicyResult | None
    merge_result_ref: ArtifactRef | None
    prime_factorization_result: PrimeFactorizationResult | None
    prime_factorization_ref: ArtifactRef | None
    merge_submission: FixtureSubmission | None
    merge_canonical: CanonicalBindingFlowResult | None
    merge_record: MergeRecord | None
    resolution: ExpectedOutputResolution | None
    merge_resolution: MergeResolutionFlowResult | None
    expand_contribution: ContributionRecord | None
    merge_contribution: ContributionRecord | None
    parent_completion: ParentCompletionFlowResult | None
    settlement: SettlementFlowResult | None


def run_factorization_fixture_flow(
    root_path: str | Path,
    *,
    target_n: int | str,
    requested_child_count: int,
    root_budget: int = 10,
    stop_after_canonical_range_count: int | None = None,
) -> FactorizationFixtureFlowResult:
    """Run a local factorization fixture through Phase 3-5 protocol events.

    The fixture is deterministic and intentionally conservative: every range is
    executed and canonicalized before merge creation is attempted. Passing
    ``stop_after_canonical_range_count`` exercises the all-required readiness
    gate without creating synthetic merge facts.
    """

    target_text = _target_text(target_n)
    store = ArtifactStore(root_path)
    ledger = EventLedger(Path(root_path) / "events" / f"{TASK_ID}.jsonl")
    config = ProtocolConfig.default(
        config_id="config_factorization_fixture",
        artifact_store_uri="file://artifacts",
        event_log_uri=f"file://events/{TASK_ID}.jsonl",
        metadata={"fixture": "factorization"},
    )
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config, artifact_store=store)
    registry = _record_registry(engine)
    root_registration = _register_root_task(
        store=store,
        ledger=ledger,
        config=config,
        target_n=target_text,
        root_budget=root_budget,
    )
    root_unit_ready = _replace_unit(
        root_registration.root_unit,
        unit_type="factor_integer",
        required_capabilities={"executor": "deterministic_local", "factorization": True},
        plugin_payload={"requested_output": REQUESTED_OUTPUT_PRIME_FACTORIZATION},
    )
    root_graph = TaskGraph(
        task_id=TASK_ID,
        units={root_unit_ready.unit_id: root_unit_ready},
        relations=[],
        protocol_config=config,
    )
    root_scheduled = _schedule_unit(
        engine=engine,
        graph=root_graph,
        unit=root_unit_ready,
        attempt_id="attempt_factor_integer_root",
        lease_id="lease_factor_integer_root",
        decision_id="decision_factor_integer_root",
        fencing_token="token_factor_integer_root",
    )
    subject, subject_ref = _execute_factor_integer_subject(
        store=store,
        root_input_ref=root_registration.root_input_ref,
        unit=root_scheduled.task_unit,
        target_n=target_text,
    )
    root_submission = _record_submission(
        engine=engine,
        registry=registry,
        scheduled=root_scheduled,
        output_contract=_subject_output_contract(),
        candidate_output_refs={FACTOR_INTEGER_SUBJECT_OUTPUT_NAME: subject_ref},
        request_id="request_factor_integer_root",
        submission_id="submission_factor_integer_root",
        hard_requirements={"executor": "deterministic_local"},
        execution_instruction_ref=None,
        prompt_package_ref=None,
        raw_output_ref=None,
        parsed_output_ref=subject_ref,
    )
    _release_lease(
        engine=engine,
        lease=root_scheduled.lease,
        reason="fixture_submission_recorded",
    )
    root_verification_result = verify_factor_integer_subject(
        subject,
        root_input_ref=root_registration.root_input_ref,
        root_input_body=_root_input_body(store, root_registration.root_input_ref),
    )
    root_verification = _record_verification(
        engine=engine,
        attempt=_require_submitted_attempt(root_submission),
        submission=root_submission.submission.submission,
        candidate_output_refs={FACTOR_INTEGER_SUBJECT_OUTPUT_NAME: subject_ref},
        required_output_names=[FACTOR_INTEGER_SUBJECT_OUTPUT_NAME],
        output_contract_id=FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
        validator_policy_id="factorization.factor_integer_subject.validator.v1",
        plugin_domain_layer=root_verification_result,
        verification_report_id="verification_factor_integer_root",
    )
    root_canonical = engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=root_scheduled.task_unit.unit_id,
        verification_events=[root_verification.event],
        attempts_by_id={_require_attempt(root_verification).attempt_id: _require_attempt(root_verification)},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_factorization_root_canonical",
    )

    direct_params_digest = canonical_json_digest(
        {
            "strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            "target_n": target_text,
            "action_policy": "direct_complete_or_candidate_range_partition",
        }
    )
    split_action = build_factorization_split_strategy_result(
        subject=subject,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=_expansion_scope_hash(subject=subject),
        expansion_decision_id=_expansion_decision_id(subject=subject),
        requested_child_count=requested_child_count,
        max_children_per_unit=config.max_children_per_unit,
        created_at=NOW,
    )
    split_params_digest = (
        split_action.split_plan.partition.params.params_digest
        if split_action.split_plan is not None
        else direct_params_digest
    )
    invocation = SplitStrategyInvocation(
        invocation_id=f"split_invocation:{_expansion_scope_hash(subject=subject)}:attempt:1",
        invocation_attempt_no=1,
        expansion_scope_hash=_expansion_scope_hash(subject=subject),
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        split_strategy_params_digest=split_params_digest,
        status="succeeded",
        result_action=split_action.split_strategy_result.action,
        result_digest=canonical_json_digest(split_action.split_strategy_result.to_dict()),
        started_at=NOW,
        completed_at=NOW,
    )
    split_invocation = engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id="corr_factorization_split_invocation",
        causation_event_id=root_canonical.event.event_id,
    )
    if split_action.split_strategy_result.action == "complete":
        if split_action.prime_factorization_result is None:
            raise ValueError("complete split action requires prime factorization result")
        direct_complete = _record_direct_complete(
            engine=engine,
            store=store,
            root_scheduled=root_scheduled,
            root_canonical=root_canonical,
            root_verification=root_verification,
            subject=subject,
            subject_ref=subject_ref,
            root_submission=root_submission,
            split_invocation_event=split_invocation.event,
            split_action=split_action,
            split_strategy_params_digest=split_params_digest,
            root_budget=root_budget,
        )
        return direct_complete

    split_result = split_action.split_plan
    if split_result is None:
        raise ValueError("expand split action requires split plan")
    decision = ExpansionDecision(
        expansion_decision_id=_expansion_decision_id(subject=subject),
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        expansion_scope_hash=_expansion_scope_hash(subject=subject),
        action="expand",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        split_strategy_params_digest=split_params_digest,
        source_invocation_id=invocation.invocation_id,
        proposal_id=split_result.proposal.proposal_header["proposal_id"],
        proposal_digest=split_result.proposal.proposal_header["proposal_digest"],
        merge_plan_id=split_result.merge_plan.merge_plan_header["merge_plan_id"],
        merge_plan_digest=split_result.merge_plan.merge_plan_header["merge_plan_digest"],
        action_body={
            "expand_evidence": {
                "proposal_id": split_result.proposal.proposal_header["proposal_id"],
                "proposal_digest": split_result.proposal.proposal_header["proposal_digest"],
                "merge_plan_id": split_result.merge_plan.merge_plan_header["merge_plan_id"],
                "merge_plan_digest": split_result.merge_plan.merge_plan_header["merge_plan_digest"],
                "child_count": len(split_result.proposal.child_specs),
                "relation_count": len(split_result.proposal.dependency_edges),
                "expected_output_count": len(split_result.proposal.expected_outputs),
                "required_merge_slot_count": len(split_result.merge_plan.required_slots),
            }
        },
        decided_at=NOW,
    )
    root_unit_processing = root_scheduled.task_unit
    canonical_graph = TaskGraph(
        task_id=TASK_ID,
        units={root_unit_processing.unit_id: root_unit_processing},
        relations=[],
        canonical_outputs_by_unit_id={
            ROOT_UNIT_ID: root_canonical.canonical_selection.canonical_output_refs
        },
        protocol_config=config,
    )
    expand_result = engine.record_expand_decision(
        decision=decision,
        proposal=split_result.proposal,
        merge_plan=split_result.merge_plan,
        parent_unit=root_unit_processing,
        graph=canonical_graph,
        correlation_id="corr_factorization_expand",
        causation_event_id=split_invocation.event.event_id,
    )
    graph_after_expand = expand_result.task_graph
    range_inputs_by_child_key = _range_inputs_by_child_key(split_result.proposal.child_specs)
    range_limit = (
        len(expand_result.child_units)
        if stop_after_canonical_range_count is None
        else stop_after_canonical_range_count
    )
    range_executions: list[FixtureRangeExecution] = []
    graph_for_scheduling = graph_after_expand
    for index, child_unit in enumerate(expand_result.child_units[:range_limit]):
        range_executions.append(
            _execute_range_child(
                engine=engine,
                store=store,
                registry=registry,
                graph=graph_for_scheduling,
                unit=child_unit,
                range_input=range_inputs_by_child_key[child_unit.metadata["child_logical_key"]],
                index=index,
            )
        )
        graph_for_scheduling = _replace_graph_unit(
            graph_for_scheduling,
            range_executions[-1].canonical.attempt,
            TaskState.PROCESSING,
        )

    merge_task_creations = _create_merge_tasks(
        engine=engine,
        store=store,
        graph=graph_after_expand,
        expand_result=expand_result,
        range_executions=range_executions,
    )
    if not merge_task_creations:
        return FactorizationFixtureFlowResult(
            engine=engine,
            ledger=ledger,
            store=store,
            graph=graph_after_expand,
            root_unit=root_scheduled.task_unit,
            subject=subject,
            subject_ref=subject_ref,
            root_submission=root_submission,
            root_verification=root_verification,
            root_canonical=root_canonical,
            split_invocation_event=split_invocation.event,
            complete_result=None,
            expand_result=expand_result,
            range_executions=tuple(range_executions),
            range_results=tuple(range_executions),
            range_verifications=tuple(item.verification for item in range_executions),
            range_canonical_events=tuple(item.canonical.event for item in range_executions),
            executor_observations=tuple(item.executor_observation for item in range_executions),
            merge_task_creations=(),
            merge_policy_result=None,
            merge_result_ref=None,
            prime_factorization_result=None,
            prime_factorization_ref=None,
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

    merge_creation = merge_task_creations[0]
    merge_policy_result = merge_required_range_results(
        merge_plan=split_result.merge_plan,
        slot_results=_slot_merge_inputs(merge_creation, range_executions),
        merge_unit_id=merge_creation.merge_task_unit.unit_id,
        created_at=NOW,
    )
    merge_result_ref = _save_json_artifact(
        store,
        merge_policy_result.merge_result.to_dict(),
        artifact_id=merge_policy_result.merge_result.merge_result_id,
        artifact_type="canonical_output",
        artifact_schema_id="factorization.merge_result",
        artifact_schema_version="v1",
        output_name="factorization_result",
    )
    prime_factorization_ref = None
    if merge_policy_result.prime_factorization_result is not None:
        prime_factorization_ref = _save_json_artifact(
            store,
            merge_policy_result.prime_factorization_result.to_dict(),
            artifact_id=merge_policy_result.prime_factorization_result.result_id,
            artifact_type="canonical_output",
            artifact_schema_id="factorization.prime_factorization_result",
            artifact_schema_version="v1",
            output_name=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        )
    merge_scheduled = _schedule_unit(
        engine=engine,
        graph=_graph_with_merge_unit(graph_for_scheduling, merge_creation.merge_task_unit),
        unit=merge_creation.merge_task_unit,
        attempt_id="attempt_factorization_merge",
        lease_id="lease_factorization_merge",
        decision_id="decision_factorization_merge",
        fencing_token="token_factorization_merge",
    )
    merge_candidate_refs = {
        "factorization_result": merge_result_ref,
        **(
            {REQUESTED_OUTPUT_PRIME_FACTORIZATION: prime_factorization_ref}
            if prime_factorization_ref is not None
            else {}
        ),
    }
    merge_required_output_names = list(merge_candidate_refs)
    merge_submission = _record_submission(
        engine=engine,
        registry=registry,
        scheduled=merge_scheduled,
        output_contract=_merge_output_contract(required_outputs=merge_required_output_names),
        candidate_output_refs=merge_candidate_refs,
        request_id="request_factorization_merge",
        submission_id="submission_factorization_merge",
        hard_requirements={"executor": "deterministic_local"},
        execution_instruction_ref=None,
        prompt_package_ref=None,
        raw_output_ref=None,
        parsed_output_ref=merge_result_ref,
    )
    _release_lease(
        engine=engine,
        lease=merge_scheduled.lease,
        reason="fixture_submission_recorded",
    )
    merge_verification = _record_verification(
        engine=engine,
        attempt=_require_submitted_attempt(merge_submission),
        submission=merge_submission.submission.submission,
        candidate_output_refs=merge_candidate_refs,
        required_output_names=merge_required_output_names,
        output_contract_id=FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
        validator_policy_id="factorization.merge_result.validator.v1",
        plugin_domain_layer=_accepted_layer(
            "factorization_merge_policy_passed",
            "all required range results merged by factorization policy",
        ),
        verification_report_id="verification_factorization_merge",
    )
    merge_canonical = engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=merge_creation.merge_task_unit.unit_id,
        verification_events=[merge_verification.event],
        attempts_by_id={
            _require_attempt(merge_verification).attempt_id: _require_attempt(merge_verification)
        },
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_factorization_merge_canonical",
    )
    merge_record = _merge_record(
        merge_creation=merge_creation,
        merge_plan=split_result.merge_plan,
        merge_canonical=merge_canonical,
    )
    if not merge_policy_result.expected_output_resolvable:
        return FactorizationFixtureFlowResult(
            engine=engine,
            ledger=ledger,
            store=store,
            graph=graph_after_expand,
            root_unit=root_scheduled.task_unit,
            subject=subject,
            subject_ref=subject_ref,
            root_submission=root_submission,
            root_verification=root_verification,
            root_canonical=root_canonical,
            split_invocation_event=split_invocation.event,
            complete_result=None,
            expand_result=expand_result,
            range_executions=tuple(range_executions),
            range_results=tuple(range_executions),
            range_verifications=tuple(item.verification for item in range_executions),
            range_canonical_events=tuple(item.canonical.event for item in range_executions),
            executor_observations=tuple(item.executor_observation for item in range_executions),
            merge_task_creations=tuple(merge_task_creations),
            merge_policy_result=merge_policy_result,
            merge_result_ref=merge_result_ref,
            prime_factorization_result=None,
            prime_factorization_ref=None,
            merge_submission=merge_submission,
            merge_canonical=merge_canonical,
            merge_record=merge_record,
            resolution=None,
            merge_resolution=None,
            expand_contribution=None,
            merge_contribution=None,
            parent_completion=None,
            settlement=None,
        )
    resolution = _expected_output_resolution(
        expected_output_refs=expand_result.expected_output_refs,
        merge_record=merge_record,
        resolved_output_ref=_require_ref(prime_factorization_ref),
    )
    merge_resolution = engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_factorization_merge_resolution",
    )
    contribution_coordinator = ContributionCoordinator(event_ledger=ledger)
    expand_contribution = contribution_coordinator.record_canonical_contributions(
        task_id=TASK_ID,
        completion_batches=[],
        expansion_batches=[_batch(expand_result.events)],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_factorization_expand_contribution",
    )[0].contribution
    merge_contribution = contribution_coordinator.record_canonical_contributions(
        task_id=TASK_ID,
        completion_batches=[],
        expansion_batches=[],
        merge_resolution_batches=[_batch(merge_resolution.events)],
        now=NOW,
        correlation_id="corr_factorization_merge_contribution",
    )[0].contribution
    parent_completion = engine.record_parent_completion(
        owner_unit=root_scheduled.task_unit,
        expected_output_refs=list(expand_result.expected_output_refs),
        expected_output_resolutions=[resolution],
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_factorization_parent_completion",
    )
    root_completion_event_seq = parent_completion.events[0].event_seq
    settlement = engine.record_root_settlement(
        task_id=TASK_ID,
        root_unit_id=ROOT_UNIT_ID,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=[
            merge_contribution,
            parent_completion.expand_contributions[0],
        ],
        root_budget=root_budget,
        settlement_policy_id=SETTLEMENT_POLICY_ID,
        now=NOW,
        correlation_id="corr_factorization_root_settlement",
    )

    return FactorizationFixtureFlowResult(
        engine=engine,
        ledger=ledger,
        store=store,
        graph=graph_after_expand,
        root_unit=root_scheduled.task_unit,
        subject=subject,
        subject_ref=subject_ref,
        root_submission=root_submission,
        root_verification=root_verification,
        root_canonical=root_canonical,
        split_invocation_event=split_invocation.event,
        complete_result=None,
        expand_result=expand_result,
        range_executions=tuple(range_executions),
        range_results=tuple(range_executions),
        range_verifications=tuple(item.verification for item in range_executions),
        range_canonical_events=tuple(item.canonical.event for item in range_executions),
        executor_observations=tuple(item.executor_observation for item in range_executions),
        merge_task_creations=tuple(merge_task_creations),
        merge_policy_result=merge_policy_result,
        merge_result_ref=merge_result_ref,
        prime_factorization_result=merge_policy_result.prime_factorization_result,
        prime_factorization_ref=prime_factorization_ref,
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


def _record_registry(engine: ProtocolEngine) -> RegistrySnapshotFlowResult:
    plugin_registry = PluginRegistry()
    executor_registry = ExecutorRegistry()
    plugin_registry.register(build_factorization_plugin_descriptor())
    executor_registry.register(
        ExecutorDescriptor(
            executor_id=EXECUTOR_ID,
            executor_type="deterministic_local",
            executor_version=EXECUTOR_VERSION,
            supported_request_schema_versions=["phase3.execution_request.v1"],
            capabilities={
                "executor": "deterministic_local",
                "factorization": True,
                "bounded_factor_search": True,
            },
            environment_policy={"runtime": "python", "network_access": False},
            status=ExecutorStatus.AVAILABLE,
            metadata={"fixture": "factorization"},
        )
    )
    return engine.record_registry_snapshot(
        task_id=TASK_ID,
        registry_snapshot_id=REGISTRY_SNAPSHOT_ID,
        plugin_registry=plugin_registry,
        executor_registry=executor_registry,
        now=NOW,
        correlation_id="corr_factorization_registry",
    )


def _register_root_task(
    *,
    store: ArtifactStore,
    ledger: EventLedger,
    config: ProtocolConfig,
    target_n: str,
    root_budget: int,
):
    root_input = RootInput(
        target_n=target_n,
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label=f"factorization_fixture_{target_n}",
    )
    registrar = RootTaskRegistrar(artifact_store=store, event_ledger=ledger)
    return registrar.register_root_task(
        RootTaskRegistrationRequest(
            task_id=TASK_ID,
            root_unit_id=ROOT_UNIT_ID,
            root_artifact_id=f"root_input_factorization_{target_n}",
            description=f"factorization fixture for {target_n}",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            split_strategy_params={},
            root_input_bytes=_json_bytes(root_input.to_dict()),
            root_input_media_type="application/json",
            root_input_schema_id="factorization.root_input",
            root_input_schema_version="v1",
            protocol_config=config,
            required_capabilities={"executor": "deterministic_local", "factorization": True},
            plugin_payload={"requested_output": REQUESTED_OUTPUT_PRIME_FACTORIZATION},
            metadata={"fixture": "factorization", "target_n": target_n},
            created_at=NOW,
            root_budget=float(root_budget),
        )
    )


def _execute_factor_integer_subject(
    *,
    store: ArtifactStore,
    root_input_ref: ArtifactRef,
    unit: TaskUnit,
    target_n: str,
) -> tuple[FactorIntegerSubject, ArtifactRef]:
    subject = FactorIntegerSubject(
        subject_id=f"factor_integer_subject:{TASK_ID}:{unit.unit_id}",
        task_id=TASK_ID,
        unit_id=unit.unit_id,
        target_n=target_n,
        source_kind="root_input",
        source_ref=root_input_ref.to_dict(),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )
    ref = _save_json_artifact(
        store,
        subject.to_dict(),
        artifact_id=subject.subject_id,
        artifact_type="canonical_output",
        artifact_schema_id="factorization.factor_integer_subject",
        artifact_schema_version="v1",
        output_name="factor_integer_subject",
    )
    return subject, ref


def _execute_range_child(
    *,
    engine: ProtocolEngine,
    store: ArtifactStore,
    registry: RegistrySnapshotFlowResult,
    graph: TaskGraph,
    unit: TaskUnit,
    range_input: FactorSearchRangeInput,
    index: int,
) -> FixtureRangeExecution:
    scheduled = _schedule_unit(
        engine=engine,
        graph=graph,
        unit=unit,
        attempt_id=f"attempt_factor_range_{index}",
        lease_id=f"lease_factor_range_{index}",
        decision_id=f"decision_factor_range_{index}",
        fencing_token=f"token_factor_range_{index}",
    )
    instruction = build_factor_search_instruction(
        request_id=f"request_factor_range_{index}",
        unit_id=scheduled.task_unit.unit_id,
        range_input=range_input,
    )
    instruction_ref = _save_json_artifact(
        store,
        instruction.to_dict(),
        artifact_id=instruction.instruction_id,
        artifact_type="ExecutionInstruction",
        artifact_schema_id="factorization.factor_search_instruction",
        artifact_schema_version="v1",
        output_name="execution_instruction",
    )
    prompt_package = build_factor_search_prompt_package(
        request_id=f"request_factor_range_{index}",
        task_id=TASK_ID,
        unit_id=scheduled.task_unit.unit_id,
        range_input=range_input,
        instruction=instruction,
        created_at=NOW,
    )
    prompt_package_ref = _save_json_artifact(
        store,
        prompt_package.to_dict(),
        artifact_id=prompt_package.prompt_package_id,
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        output_name="prompt_package",
    )
    range_result, observation = _bounded_range_executor(range_input=range_input)
    raw_output_ref = _save_bytes_artifact(
        store,
        json.dumps(range_result.to_dict(), ensure_ascii=False).encode("utf-8"),
        artifact_id=f"raw_output_factor_range_{index}",
        artifact_type="RawExecutorOutput",
        artifact_schema_id="phase3.raw_output",
        artifact_schema_version="v1",
        media_type="application/json",
        output_name="raw_output",
    )
    parsed_output_ref = _save_json_artifact(
        store,
        range_result.to_dict(),
        artifact_id=range_result.range_result_id,
        artifact_type="canonical_output",
        artifact_schema_id="factorization.range_result",
        artifact_schema_version="v1",
        output_name="range_result",
    )
    submission = _record_submission(
        engine=engine,
        registry=registry,
        scheduled=scheduled,
        output_contract=_range_output_contract(),
        candidate_output_refs={"range_result": parsed_output_ref},
        request_id=f"request_factor_range_{index}",
        submission_id=f"submission_factor_range_{index}",
        hard_requirements={"executor": "deterministic_local", "bounded_factor_search": True},
        execution_instruction_ref=instruction_ref,
        prompt_package_ref=prompt_package_ref,
        raw_output_ref=raw_output_ref,
        parsed_output_ref=parsed_output_ref,
    )
    _release_lease(
        engine=engine,
        lease=scheduled.lease,
        reason="fixture_submission_recorded",
    )
    verification = verify_range_result(range_result, child_input=range_input)
    verification_flow = _record_verification(
        engine=engine,
        attempt=_require_submitted_attempt(submission),
        submission=submission.submission.submission,
        candidate_output_refs={"range_result": parsed_output_ref},
        required_output_names=["range_result"],
        output_contract_id=RANGE_RESULT_CONTRACT_ID,
        validator_policy_id=RANGE_RESULT_VALIDATOR_POLICY_ID,
        plugin_domain_layer=verification,
        verification_report_id=f"verification_factor_range_{index}",
    )
    canonical = engine.bind_canonical_outputs(
        task_id=TASK_ID,
        unit_id=unit.unit_id,
        verification_events=[verification_flow.event],
        attempts_by_id={
            _require_attempt(verification_flow).attempt_id: _require_attempt(verification_flow)
        },
        policy="first_verified_bundle",
        now=NOW,
        correlation_id=f"corr_factor_range_{index}_canonical",
    )
    return FixtureRangeExecution(
        range_input=range_input,
        range_result=range_result,
        unit=scheduled.task_unit,
        scheduled=scheduled,
        request=submission.request,
        submission=submission.submission,
        verification=verification_flow,
        canonical=canonical,
        executor_observation=observation,
        range_output_ref=parsed_output_ref,
    )


def _record_submission(
    *,
    engine: ProtocolEngine,
    registry: RegistrySnapshotFlowResult,
    scheduled: SchedulingFlowResult,
    output_contract: OutputContract,
    candidate_output_refs: dict[str, ArtifactRef],
    request_id: str,
    submission_id: str,
    hard_requirements: dict[str, object],
    execution_instruction_ref: ArtifactRef | None,
    prompt_package_ref: ArtifactRef | None,
    raw_output_ref: ArtifactRef | None,
    parsed_output_ref: ArtifactRef | None,
) -> FixtureSubmission:
    request = ExecutionRequest(
        request_id=request_id,
        task_id=TASK_ID,
        unit_id=scheduled.task_unit.unit_id,
        attempt_id=scheduled.attempt.attempt_id,
        lease_id=scheduled.lease.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        plugin=registry.snapshot.plugin_entries[0],
        executor=registry.snapshot.executor_entries[0],
        registry_snapshot_id=registry.snapshot.registry_snapshot_id,
        allocation_decision={
            "decision_id": f"allocation_{request_id}",
            "selected_executor_id": EXECUTOR_ID,
            "eligible_executor_ids": [EXECUTOR_ID],
            "rejected_executor_reasons": {},
            "tie_break": ["executor_id"],
        },
        capability_snapshot={
            "executor": "deterministic_local",
            "status": "Available",
            "bounded_factor_search": True,
        },
        task_unit_snapshot=scheduled.task_unit.to_dict(),
        input_artifact_refs=scheduled.task_unit.input_refs,
        output_contract=output_contract,
        hard_requirements=hard_requirements,
        soft_hints={"fixture": "factorization"},
        environment_ref=_environment_ref(),
        execution_instruction_ref=execution_instruction_ref,
        prompt_package_ref=prompt_package_ref,
        limits={"timeout_seconds": 30},
        created_at=NOW,
    )
    request_flow = engine.record_execution_request(
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
        result_kind="succeeded",
        raw_output_ref=raw_output_ref,
        parsed_output_ref=parsed_output_ref,
        candidate_output_refs=candidate_output_refs,
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=_environment_ref(),
        environment_summary={"runtime": "python", "network_access": False},
        provenance_ref=None,
        usage_summary={"deterministic_fixture": True},
        error=None,
        submitted_at=NOW,
    )
    submission_flow = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id=f"corr_{submission_id}",
        causation_event_id=request_flow.event.event_id,
    )
    return FixtureSubmission(request=request_flow, submission=submission_flow)


def _record_verification(
    *,
    engine: ProtocolEngine,
    attempt: Attempt,
    submission: ExecutionSubmission,
    candidate_output_refs: dict[str, ArtifactRef],
    required_output_names: list[str],
    output_contract_id: str,
    validator_policy_id: str,
    plugin_domain_layer: RangeVerificationResult | dict[str, object],
    verification_report_id: str,
) -> VerificationFlowResult:
    if isinstance(plugin_domain_layer, RangeVerificationResult):
        plugin_layer = plugin_domain_layer.to_phase4_layer_summary()
        status = plugin_domain_layer.status
    else:
        plugin_layer = dict(plugin_domain_layer)
        status = str(plugin_layer["status"])
    report = build_verification_report(
        verification_report_id=verification_report_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=submission.submission_id,
        submission_event_seq=_submission_event_seq(engine, submission.submission_id),
        candidate_output_refs=candidate_output_refs,
        required_output_names=required_output_names,
        output_contract_id=output_contract_id,
        validator_policy_id=validator_policy_id,
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        status="passed" if status == "passed" else "rejected",
        expected_artifact_hashes={
            name: ref.content_hash for name, ref in candidate_output_refs.items()
        },
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status=status,
        audit_status="passed",
        verification_environment={"runtime": "pytest", "fixture": "factorization"},
        verifier={"verifier_id": "factorization_fixture_verifier", "verifier_version": "1"},
        started_at=NOW,
        completed_at=NOW,
        metadata={"plugin_domain_layer": plugin_layer},
    )
    return engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id=f"corr_{verification_report_id}",
    )


def _schedule_unit(
    *,
    engine: ProtocolEngine,
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
    return engine.schedule_ready_unit(
        graph=scheduling_graph,
        clients=[
            _client(
                client_id=f"client_{attempt_id}",
                capabilities={
                    "executor": ["deterministic_local", "mock_ai", "local"],
                    "factorization": True,
                    "bounded_factor_search": True,
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
        actor={"kind": "factorization_fixture"},
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


def _bounded_range_executor(
    *,
    range_input: FactorSearchRangeInput,
) -> tuple[RangeResult, dict[str, int]]:
    target = int(range_input.target_n)
    range_start = int(range_input.range_start)
    range_end = int(range_input.range_end)
    checked_count = 0
    for divisor in range(range_start, range_end + 1):
        checked_count += 1
        if target % divisor == 0:
            result = RangeResult(
                range_result_id=f"range_result:{range_input.coverage_id}:{range_input.child_index}",
                result_kind=RANGE_RESULT_FOUND_FACTOR,
                target_n=range_input.target_n,
                range_start=range_input.range_start,
                range_end=range_input.range_end,
                coverage_id=range_input.coverage_id,
                child_index=range_input.child_index,
                partition_params_digest=range_input.partition_params_digest,
                found_factor=str(divisor),
                cofactor=str(target // divisor),
                checked_divisor_count=checked_count,
                executor_summary={
                    "executor": "deterministic_local",
                    "bounded_range_only": True,
                    "checked_start": range_start,
                    "checked_end": divisor,
                },
                created_at=NOW,
            )
            return result, {
                "range_start": range_start,
                "range_end": range_end,
                "checked_start": range_start,
                "checked_end": divisor,
            }
    result = RangeResult(
        range_result_id=f"range_result:{range_input.coverage_id}:{range_input.child_index}",
        result_kind=RANGE_RESULT_NO_FACTOR,
        target_n=range_input.target_n,
        range_start=range_input.range_start,
        range_end=range_input.range_end,
        coverage_id=range_input.coverage_id,
        child_index=range_input.child_index,
        partition_params_digest=range_input.partition_params_digest,
        found_factor=None,
        cofactor=None,
        checked_divisor_count=checked_count,
        executor_summary={
            "executor": "deterministic_local",
            "bounded_range_only": True,
            "checked_start": range_start,
            "checked_end": range_end,
        },
        created_at=NOW,
    )
    return result, {
        "range_start": range_start,
        "range_end": range_end,
        "checked_start": range_start,
        "checked_end": range_end,
    }


def _create_merge_tasks(
    *,
    engine: ProtocolEngine,
    store: ArtifactStore,
    graph: TaskGraph,
    expand_result: ExpandDecisionFlowResult,
    range_executions: list[FixtureRangeExecution],
) -> tuple[MergeTaskCreationFlowResult, ...]:
    coordinator = MergeCoordinator(
        event_ledger=engine._event_ledger,
        artifact_store=store,
        protocol_config=engine._protocol_config,
    )
    try:
        return tuple(
            coordinator.create_ready_merge_tasks(
                task_id=TASK_ID,
                graph=graph,
                merge_plan_events=[
                    event
                    for event in expand_result.events
                    if event.event_type == EventType.MERGE_PLAN_RECORDED
                ],
                expansion_batches=[_batch(expand_result.events)],
                canonical_events=[item.canonical.event for item in range_executions],
                now=NOW,
                coordinator_id="factorization_fixture_merge_coordinator",
                correlation_id="corr_factorization_merge_creation",
            )
        )
    except ValueError as exc:
        if "required slots" in str(exc):
            return ()
        raise


def _slot_merge_inputs(
    merge_creation: MergeTaskCreationFlowResult,
    range_executions: list[FixtureRangeExecution],
) -> list[RangeSlotMergeInput]:
    range_results_by_unit_id = {
        item.canonical.canonical_selection.unit_id: item for item in range_executions
    }
    inputs: list[RangeSlotMergeInput] = []
    for binding in merge_creation.merge_task_link.required_slot_bindings:
        execution = range_results_by_unit_id[binding.source_child_unit_id]
        inputs.append(
            RangeSlotMergeInput(
                slot_key=binding.slot_key,
                range_result=execution.range_result,
                canonical_output_digest=binding.canonical_output_digest,
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
        raise ValueError("factorization fixture expects one parent expected output")
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


def _record_direct_complete(
    *,
    engine: ProtocolEngine,
    store: ArtifactStore,
    root_scheduled: SchedulingFlowResult,
    root_canonical: CanonicalBindingFlowResult,
    root_verification: VerificationFlowResult,
    subject: FactorIntegerSubject,
    subject_ref: ArtifactRef,
    root_submission: FixtureSubmission,
    split_invocation_event: LedgerEvent,
    split_action: FactorizationSplitStrategyActionResult,
    split_strategy_params_digest: str,
    root_budget: int,
) -> FactorizationFixtureFlowResult:
    prime_result = split_action.prime_factorization_result
    if prime_result is None:
        raise ValueError("direct complete requires prime factorization result")
    prime_ref = _save_json_artifact(
        store,
        prime_result.to_dict(),
        artifact_id=prime_result.result_id,
        artifact_type="canonical_output",
        artifact_schema_id="factorization.prime_factorization_result",
        artifact_schema_version="v1",
        output_name=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    )
    decision = ExpansionDecision(
        expansion_decision_id=_expansion_decision_id(subject=subject),
        task_id=TASK_ID,
        unit_id=ROOT_UNIT_ID,
        canonical_selection_id=root_canonical.canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=(
            root_canonical.canonical_selection.canonical_output_bundle_digest
        ),
        expansion_scope_hash=_expansion_scope_hash(subject=subject),
        action="complete",
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        split_strategy_params_digest=split_strategy_params_digest,
        source_invocation_id=split_invocation_event.object_id,
        action_body={
            "completion_evidence": {
                "completion_kind": "direct_small_prime",
                "validator_policy_id": "factorization.factor_integer_subject.validator.v1",
                "verification_report_id": root_verification.report.verification_report_id,
                "canonical_selection_id": (
                    root_canonical.canonical_selection.canonical_selection_id
                ),
                "canonical_output_bundle_digest": (
                    root_canonical.canonical_selection.canonical_output_bundle_digest
                ),
                "completed_output_refs": {
                    **{
                        name: ref.to_dict()
                        for name, ref in root_canonical.canonical_selection.canonical_output_refs.items()
                    },
                    REQUESTED_OUTPUT_PRIME_FACTORIZATION: prime_ref.to_dict(),
                },
                "plugin_completion_summary": (
                    "target_n has empty candidate domain and is a direct small prime"
                ),
            },
            "plugin_direct_output_ref": prime_ref.to_dict(),
            "split_strategy_result": split_action.split_strategy_result.to_dict(),
        },
        decided_at=NOW,
    )
    complete_result = engine.record_complete_decision(
        decision=decision,
        task_unit=root_scheduled.task_unit,
        correlation_id="corr_factorization_direct_complete",
        causation_event_id=split_invocation_event.event_id,
    )
    contribution = ContributionCoordinator(event_ledger=engine._event_ledger).record_canonical_contributions(
        task_id=TASK_ID,
        completion_batches=[_batch(complete_result.events)],
        expansion_batches=[],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_factorization_direct_complete_contribution",
    )[0].contribution
    settlement = engine.record_root_settlement(
        task_id=TASK_ID,
        root_unit_id=ROOT_UNIT_ID,
        root_completion_event_seq=complete_result.events[1].event_seq,
        eligible_contributions=[contribution],
        root_budget=root_budget,
        settlement_policy_id=SETTLEMENT_POLICY_ID,
        now=NOW,
        correlation_id="corr_factorization_direct_complete_settlement",
    )
    graph = TaskGraph(
        task_id=TASK_ID,
        units={ROOT_UNIT_ID: complete_result.task_unit},
        relations=[],
        canonical_outputs_by_unit_id={
            ROOT_UNIT_ID: root_canonical.canonical_selection.canonical_output_refs
        },
        protocol_config=engine._protocol_config,
    )
    return FactorizationFixtureFlowResult(
        engine=engine,
        ledger=engine._event_ledger,
        store=store,
        graph=graph,
        root_unit=complete_result.task_unit,
        subject=subject,
        subject_ref=subject_ref,
        root_submission=root_submission,
        root_verification=root_verification,
        root_canonical=root_canonical,
        split_invocation_event=split_invocation_event,
        complete_result=complete_result,
        expand_result=None,
        range_executions=(),
        range_results=(),
        range_verifications=(),
        range_canonical_events=(),
        executor_observations=(),
        merge_task_creations=(),
        merge_policy_result=None,
        merge_result_ref=None,
        prime_factorization_result=prime_result,
        prime_factorization_ref=prime_ref,
        merge_submission=None,
        merge_canonical=None,
        merge_record=None,
        resolution=None,
        merge_resolution=None,
        expand_contribution=None,
        merge_contribution=None,
        parent_completion=complete_result,
        settlement=settlement,
    )


def _subject_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
        required_outputs=[FACTOR_INTEGER_SUBJECT_OUTPUT_NAME],
        output_schema_refs={
            FACTOR_INTEGER_SUBJECT_OUTPUT_NAME: schema_ref(
                FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION
            )
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
    )


def _range_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=RANGE_RESULT_CONTRACT_ID,
        required_outputs=["range_result"],
        output_schema_refs={"range_result": schema_ref(RANGE_RESULT_SCHEMA_VERSION)},
        raw_output_policy={"allowed": True, "media_type": "application/json"},
        parsed_output_schema_ref=schema_ref(RANGE_RESULT_SCHEMA_VERSION),
    )


def _merge_output_contract(*, required_outputs: list[str] | None = None) -> OutputContract:
    return OutputContract(
        output_contract_id=FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
        required_outputs=required_outputs or [
            "factorization_result",
            REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        ],
        output_schema_refs={
            "factorization_result": schema_ref(FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION),
            REQUESTED_OUTPUT_PRIME_FACTORIZATION: schema_ref(
                PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION
            ),
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
        parsed_output_schema_ref=schema_ref(FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION),
    )


def _root_input_body(store: ArtifactStore, root_input_ref: ArtifactRef) -> JsonObject:
    return json.loads(store.read_bytes(root_input_ref).decode("utf-8"))


def _accepted_layer(reason_code: str, summary: str) -> dict[str, object]:
    return {
        "status": "passed",
        "reason_code": reason_code,
        "summary": summary,
        "details": {},
        "evidence_refs": [],
        "checked_at": NOW,
    }


def _save_json_artifact(
    store: ArtifactStore,
    body: dict,
    *,
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
    artifact_schema_version: str,
    output_name: str,
) -> ArtifactRef:
    return store.save_json(
        body,
        artifact_id=_artifact_id_component(artifact_id),
        artifact_type=artifact_type,
        artifact_schema_id=artifact_schema_id,
        artifact_schema_version=artifact_schema_version,
        source={"kind": "factorization_fixture"},
        metadata={"output_name": output_name, "task_id": TASK_ID},
        created_at=NOW,
    )


def _save_bytes_artifact(
    store: ArtifactStore,
    data: bytes,
    *,
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
    artifact_schema_version: str,
    media_type: str,
    output_name: str,
) -> ArtifactRef:
    return store.save_bytes(
        data,
        artifact_id=_artifact_id_component(artifact_id),
        artifact_type=artifact_type,
        media_type=media_type,
        artifact_schema_id=artifact_schema_id,
        artifact_schema_version=artifact_schema_version,
        source={"kind": "factorization_fixture"},
        metadata={"output_name": output_name, "task_id": TASK_ID},
        created_at=NOW,
    )


def _range_inputs_by_child_key(child_specs: list[dict]) -> dict[str, FactorSearchRangeInput]:
    by_key: dict[str, FactorSearchRangeInput] = {}
    for child_spec in child_specs:
        body = child_spec["input_bindings"]["range_input"]["body"]
        by_key[child_spec["child_logical_key"]] = FactorSearchRangeInput(**body)
    return by_key


def _batch(events: Iterable[LedgerEvent]) -> BatchView:
    event_tuple = tuple(events)
    return BatchView(batch_id=event_tuple[0].batch_id or "", events=event_tuple)


def _environment_ref() -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_factorization_fixture",
        environment_digest="sha256:env_factorization_fixture",
        runtime="python",
        tool_versions={"factorization_fixture": "0.1.0"},
        resource_limits={"timeout_seconds": 30},
        fixture_profile_digest="sha256:factorization_fixture_profile",
        seed=None,
        clock_policy="fixed",
        created_at=NOW,
    )


def _client(client_id: str, capabilities: dict[str, object]):
    from tokenshare.core.models import ClientRecord

    return ClientRecord(
        client_id=client_id,
        executor_type="deterministic_local",
        executor_id=EXECUTOR_ID,
        executor_version=EXECUTOR_VERSION,
        capabilities=capabilities,
        status="active",
        stats={},
        metadata={"fixture": "factorization"},
        registered_at=NOW,
    )


def _submission_event_seq(engine: ProtocolEngine, submission_id: str) -> int:
    for event in reversed(engine._event_ledger.read_all()):
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED and event.object_id == submission_id:
            return event.event_seq
    raise ValueError(f"missing execution submission event: {submission_id}")


def _require_submitted_attempt(submission: FixtureSubmission) -> Attempt:
    if submission.submission.attempt is None:
        raise ValueError("fixture submission did not advance attempt")
    return submission.submission.attempt


def _require_attempt(verification: VerificationFlowResult) -> Attempt:
    if verification.attempt is None:
        raise ValueError("fixture verification did not advance attempt")
    return verification.attempt


def _require_ref(ref: ArtifactRef | None) -> ArtifactRef:
    if ref is None:
        raise ValueError("expected resolvable prime factorization artifact")
    return ref


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


def _replace_graph_unit(
    graph: TaskGraph,
    attempt: Attempt | None,
    state: TaskState,
) -> TaskGraph:
    if attempt is None:
        return graph
    unit = graph.units[attempt.unit_id]
    canonical_output_refs = dict(unit.canonical_output_refs)
    if attempt.candidate_output_refs is not None:
        canonical_output_refs.update(attempt.candidate_output_refs)
    updated = _replace_unit(
        unit,
        state=state,
        canonical_output_refs=canonical_output_refs,
        updated_at=NOW,
    )
    canonical_outputs_by_unit_id = dict(graph.canonical_outputs_by_unit_id)
    if canonical_output_refs:
        canonical_outputs_by_unit_id[unit.unit_id] = canonical_output_refs
    return TaskGraph(
        task_id=graph.task_id,
        units={**graph.units, unit.unit_id: updated},
        relations=graph.relations,
        canonical_outputs_by_unit_id=canonical_outputs_by_unit_id,
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


def _expansion_scope_hash(*, subject: FactorIntegerSubject) -> str:
    return canonical_json_digest(
        {"task_id": subject.task_id, "unit_id": subject.unit_id, "target_n": subject.target_n}
    )


def _expansion_decision_id(*, subject: FactorIntegerSubject) -> str:
    return f"expansion_decision:{_stable_id_component(_expansion_scope_hash(subject=subject))}"


def _target_text(target_n: int | str) -> str:
    if isinstance(target_n, bool):
        raise TypeError("target_n must be an integer or decimal string")
    if isinstance(target_n, int):
        return str(target_n)
    if isinstance(target_n, str) and target_n.isdecimal():
        return target_n
    raise ValueError("target_n must be an integer or decimal string")


def _artifact_id_component(value: str) -> str:
    return _stable_id_component(value.replace("sha256:", "sha256_"))


def _stable_id_component(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)


def _json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
