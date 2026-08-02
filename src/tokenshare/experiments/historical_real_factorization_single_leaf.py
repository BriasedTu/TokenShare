"""历史真实 direct-factor payload 的单叶协议回归适配器。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from tokenshare.core.expansion import (
    DecompositionProposal,
    ExpansionDecision,
    MergePlan,
    SplitStrategyInvocation,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
)
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord
from tokenshare.core.models import ArtifactRef, ClientRecord, ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.registration import RootTaskRegistrationRequest
from tokenshare.core.verification import build_verification_report, digest_json
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.experiments.factorization_500_ai import (
    DIRECT_ANSWER_SCHEMA_VERSION,
    DIRECT_OUTPUT_NAME,
    DIRECT_PARSER_ID,
    evaluate_direct_factorization_answer,
    parse_direct_factorization_ai_output,
)
from tokenshare.experiments.paper_historical_fixture import (
    CLASSIFICATION,
    FACILITY_SEMANTICS,
    FIXTURE_ID,
    HistoricalRealFixture,
    load_historical_real_fixture,
    source_tree_digest,
)
from tokenshare.experiments.paper_direct_results import (
    PaperDirectProjection,
    PaperDirectRootResult,
    build_canonical_direct_evidence,
    project_paper_direct_results,
)
from tokenshare.experiments.paper_models import (
    CanonicalDirectRootEvidence,
    ExternalBankObjectLocator,
    PaperDirectRootInventoryRow,
    PreregisteredRootInventoryManifest,
)
from tokenshare.local_runtime import (
    CanonicalUnitContext,
    CompleteAction,
    ExpandAction,
    MergeAction,
    MergeExecutionContext,
    MergeReadinessContext,
    MergeReadinessDecision,
    MergeResolutionAction,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ProtocolRunResult,
    RootProtocolPlan,
    SequentialWorkerBackend,
)
from tokenshare.local_runtime.projection import project_protocol_run
from tokenshare.plugins.contracts import (
    OutputContract,
    PluginDescriptor,
    SplitStrategyContract,
)
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


NOW = "2026-07-02T00:00:00Z"
PLUGIN_ID = "factorization"
PLUGIN_VERSION = "0.1.0"
SINGLE_LEAF_STRATEGY_ID = "factorization.historical_real_single_leaf.v1"
SINGLE_LEAF_VALIDATOR_ID = "factorization.direct_factorization_answer.validator.v1"
SINGLE_LEAF_MERGE_POLICY_ID = "factorization.explicit_single_leaf_merge.v1"
ROOT_MARKER_OUTPUT = "historical_root_marker"
ROOT_MARKER_CONTRACT_ID = "factorization.historical_root_marker.contract.v1"
DIRECT_OUTPUT_CONTRACT_ID = "factorization.direct_factorization_answer.contract.v1"
EXECUTOR_ID = "executor_historical_real_fixture_local"
EXECUTOR_VERSION = "0.1.0"
EXECUTOR_TYPE = "deterministic_local"
TASK_ID = "task_historical_real_factorization_4733749"
ROOT_UNIT_ID = "unit_historical_real_factorization_4733749"
CHILD_LOGICAL_KEY = "historical_direct_factor_leaf"
SOURCE_ID_20260731 = "exp34-smoke-20260731-041442"

_DIRECT_SCHEMA_REF = {
    "artifact_schema_id": "factorization.direct_factorization_answer",
    "artifact_schema_version": "v1",
}
_ROOT_SCHEMA_REF = {
    "artifact_schema_id": "factorization.historical_root_marker",
    "artifact_schema_version": "v1",
}
_SINGLE_LEAF_PARAMS = {"mode": "deterministic_single_leaf", "child_count": 1}
_SINGLE_LEAF_PARAMS_DIGEST = digest_json(_SINGLE_LEAF_PARAMS)


@dataclass(frozen=True)
class HistoricalSingleLeafRunResult:
    protocol_result: ProtocolRunResult
    event_types: tuple[str, ...]
    verification: dict[str, bool]
    canonical_evidence: CanonicalDirectRootEvidence
    direct_projection: PaperDirectProjection
    direct_result: PaperDirectRootResult
    metric_observation: dict[str, Any]
    regression_table: tuple[dict[str, Any], ...]
    classification: str = CLASSIFICATION
    paper_eligible: bool = False
    provider_call_count: int = 0
    network_call_count: int = 0
    semantics: str = FACILITY_SEMANTICS
    formal_range_semantics: bool = False

    def require_evidence_class(self, evidence_class: str) -> str:
        if evidence_class != CLASSIFICATION:
            raise ValueError(
                "historical fixture is regression-only and cannot become trace paper evidence"
            )
        return evidence_class


class _EvidenceBoundArtifactStore(ArtifactStore):
    """为 typed replay boundary 补齐所有协议 artifact 的执行身份。"""

    def save_bytes(self, data: bytes, **kwargs: Any) -> ArtifactRef:
        artifact_id = str(kwargs["artifact_id"])
        try:
            existing = self.load_artifact_ref(artifact_id)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            content_hash = f"sha256:{sha256(data).hexdigest()}"
            if existing.content_hash != content_hash or existing.size_bytes != len(data):
                raise ValueError(
                    f"artifact_id already exists with different content: {artifact_id}"
                )
            return existing
        source = dict(kwargs["source"])
        source.setdefault("role", "protocol_runtime_artifact")
        source.setdefault("task_id", TASK_ID)
        source.setdefault("execution_id", "historical_real_factorization_single_leaf_v1")
        kwargs["source"] = source
        return super().save_bytes(data, **kwargs)


class HistoricalSingleLeafRuntimeAdapter:
    """把冻结 direct payload 送入正常 coordinator，而不引入 range 语义。"""

    def __init__(
        self,
        *,
        fixture: HistoricalRealFixture,
        protocol_config: ProtocolConfig,
        created_at: str = NOW,
    ) -> None:
        self.fixture = fixture
        self.protocol_config = protocol_config
        self.created_at = created_at
        self.descriptor = _plugin_descriptor()
        self.executor_descriptor = _executor_descriptor()
        self._store: ArtifactStore | None = None
        self._merge_candidate_refs: dict[str, Any] = {}

    def plan_root(
        self,
        root_input: object,
        *,
        artifact_store: ArtifactStore,
    ) -> RootProtocolPlan:
        _validate_root_input(root_input, self.fixture)
        self._store = artifact_store
        self._merge_candidate_refs = {}
        plugin_registry = PluginRegistry()
        plugin_registry.register(self.descriptor)
        executor_registry = ExecutorRegistry()
        executor_registry.register(self.executor_descriptor)
        return RootProtocolPlan(
            registration_request=RootTaskRegistrationRequest(
                task_id=TASK_ID,
                root_unit_id=ROOT_UNIT_ID,
                root_artifact_id="historical_real_factorization_root_input",
                description="historical real direct factorization single-leaf facility path",
                plugin_id=PLUGIN_ID,
                plugin_version=PLUGIN_VERSION,
                split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
                split_strategy_params=dict(_SINGLE_LEAF_PARAMS),
                root_input_bytes=_canonical_json_bytes(root_input),
                root_input_media_type="application/json",
                root_input_schema_id="factorization.historical_single_leaf_root",
                root_input_schema_version="v1",
                protocol_config=self.protocol_config,
                required_capabilities={
                    "executor": "historical_fixture_local",
                    "factorization": True,
                },
                plugin_payload={
                    "fixture_id": FIXTURE_ID,
                    "target_n": self.fixture.per_number_result["target_n"],
                    "requested_output": DIRECT_OUTPUT_NAME,
                    "required_outputs": [DIRECT_OUTPUT_NAME],
                    "classification": CLASSIFICATION,
                    "paper_eligible": False,
                    "semantics": FACILITY_SEMANTICS,
                },
                metadata={
                    "historical_real_fixture": True,
                    "classification": CLASSIFICATION,
                    "paper_eligible": False,
                },
                created_at=self.created_at,
                root_budget=2.0,
            ),
            plugin_registry=plugin_registry,
            executor_registry=executor_registry,
            registry_snapshot_id="registry_historical_real_factorization_single_leaf_v1",
            clients=(
                ClientRecord(
                    client_id="worker_historical_fixture_local",
                    executor_type=EXECUTOR_TYPE,
                    executor_id=EXECUTOR_ID,
                    executor_version=EXECUTOR_VERSION,
                    capabilities={
                        "executor": ["historical_fixture_local", "local"],
                        "factorization": True,
                        "merge": True,
                    },
                    status="active",
                    stats={},
                    metadata={"network_access": False, "provider_calls": 0},
                    registered_at=self.created_at,
                ),
            ),
            canonical_action_builder=self._canonical_action,
            settlement_policy_id="sandbox_equal_weight_v1",
        )

    def build_execution_request(self, unit: TaskUnit, *, attempt, lease) -> ExecutionRequest:
        contract = _root_output_contract() if unit.parent_unit_id is None else _direct_output_contract()
        return ExecutionRequest(
            request_id=f"request_{_safe(attempt.attempt_id)}",
            task_id=unit.task_id,
            unit_id=unit.unit_id,
            attempt_id=attempt.attempt_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            plugin=self.descriptor.to_dict(),
            executor=self.executor_descriptor.to_dict(),
            registry_snapshot_id="registry_historical_real_factorization_single_leaf_v1",
            allocation_decision={"client_id": attempt.client_id},
            capability_snapshot=dict(unit.required_capabilities),
            task_unit_snapshot=unit.to_dict(),
            input_artifact_refs=dict(unit.input_refs),
            output_contract=contract,
            hard_requirements={"executor": "historical_fixture_local"},
            soft_hints={"network_access": False, "provider_calls": 0},
            environment_ref=_environment_ref(self.fixture),
            execution_instruction_ref=None,
            prompt_package_ref=None,
            limits={"timeout_seconds": 30},
            created_at=attempt.started_at or attempt.created_at,
        )

    def verify_submission(
        self,
        submission: ExecutionSubmission,
        *,
        unit: TaskUnit,
    ):
        store = self._require_store()
        if unit.parent_unit_id is None:
            required_outputs = [ROOT_MARKER_OUTPUT]
            marker = _read_json_ref(
                store,
                submission.candidate_output_refs[ROOT_MARKER_OUTPUT],
            )
            passed = (
                marker.get("fixture_id") == FIXTURE_ID
                and marker.get("target_n") == self.fixture.per_number_result["target_n"]
            )
            validator_id = "factorization.historical_root_marker.validator.v1"
            evaluation: dict[str, Any] = {"root_marker_valid": passed}
            contract_id = ROOT_MARKER_CONTRACT_ID
        else:
            required_outputs = [DIRECT_OUTPUT_NAME]
            answer = _read_json_ref(
                store,
                submission.candidate_output_refs[DIRECT_OUTPUT_NAME],
            )
            evaluation = evaluate_direct_factorization_answer(
                answer,
                target_n=str(self.fixture.per_number_result["target_n"]),
                oracle_prime_factors=list(
                    self.fixture.per_number_result["oracle_prime_factors"]
                ),
            )
            passed = evaluation.get("final_correctness") is True
            validator_id = SINGLE_LEAF_VALIDATOR_ID
            contract_id = DIRECT_OUTPUT_CONTRACT_ID
        status = "passed" if passed else "rejected"
        return build_verification_report(
            verification_report_id=f"verification_{_safe(submission.attempt_id)}",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            submission_id=submission.submission_id,
            submission_event_seq=1,
            candidate_output_refs=submission.candidate_output_refs,
            required_output_names=required_outputs,
            output_contract_id=contract_id,
            validator_policy_id=validator_id,
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            status=status,
            expected_artifact_hashes={
                name: ref.content_hash
                for name, ref in submission.candidate_output_refs.items()
            },
            required_evidence_ref_ids=[],
            available_evidence_ref_ids=[],
            plugin_domain_status=status,
            audit_status="passed",
            verification_environment={
                "runtime": "python",
                "network_access": False,
                "fixture_id": FIXTURE_ID,
            },
            verifier={"verifier_id": validator_id, "verifier_version": "v1"},
            started_at=submission.submitted_at,
            completed_at=submission.submitted_at,
            metadata={"domain_evaluation": evaluation},
        )

    def build_merge(
        self,
        *,
        parent: TaskUnit,
        canonical_children: tuple[TaskUnit, ...],
        slot_integrity_enabled: bool = True,
    ) -> MergeAction:
        if not slot_integrity_enabled:
            raise ValueError("historical single-leaf merge requires slot integrity")
        if parent.unit_id != ROOT_UNIT_ID or len(canonical_children) != 1:
            raise ValueError("historical adapter requires exactly one canonical leaf")
        child = canonical_children[0]
        if child.unit_type != "historical_direct_factor_leaf":
            raise ValueError("historical adapter rejects range child semantics")
        source_ref = child.canonical_output_refs.get(DIRECT_OUTPUT_NAME)
        if source_ref is None:
            raise ValueError("historical single-leaf merge requires canonical direct output")
        answer = _read_json_ref(self._require_store(), source_ref)
        merged_ref = self._require_store().save_json(
            answer,
            artifact_id="historical_explicit_single_leaf_merge_output",
            artifact_type="CandidateOutput",
            artifact_schema_id="factorization.direct_factorization_answer",
            artifact_schema_version="v1",
            source={
                "kind": "explicit_single_leaf_merge",
                "source_child_unit_id": child.unit_id,
                "source_content_hash": source_ref.content_hash,
            },
            metadata={
                "output_name": DIRECT_OUTPUT_NAME,
                "semantics": FACILITY_SEMANTICS,
            },
            created_at=self.created_at,
        )
        self._merge_candidate_refs = {DIRECT_OUTPUT_NAME: merged_ref}
        return MergeAction(resolution_builder=self._merge_resolution)

    def normalize_submission(
        self,
        submission: ExecutionSubmission,
    ) -> ExecutionSubmission:
        """保留 parser/candidate 后，形成可由 verifier 与 canonical binding 消费的 wrapper。"""

        output_name, candidate = next(iter(submission.candidate_output_refs.items()))
        if candidate.artifact_type == "canonical_output":
            return submission
        body = _read_json_ref(self._require_store(), candidate)
        is_merge_final = candidate.artifact_id == "historical_explicit_single_leaf_merge_output"
        canonical_ref = self._require_store().save_json(
            body,
            artifact_id=f"historical_canonical_{_safe(submission.submission_id)}",
            artifact_type="canonical_output",
            artifact_schema_id=candidate.artifact_schema_id,
            artifact_schema_version="v1",
            source={
                "kind": "historical_single_leaf_canonical_wrapper",
                "role": "final_result" if is_merge_final else "protocol_runtime_artifact",
                "task_id": TASK_ID,
                "execution_id": "historical_real_factorization_single_leaf_v1",
                "candidate_output_ref": candidate.to_dict(),
                "parsed_output_ref": (
                    submission.parsed_output_ref.to_dict()
                    if submission.parsed_output_ref is not None
                    else None
                ),
                "raw_output_ref": (
                    submission.raw_output_ref.to_dict()
                    if submission.raw_output_ref is not None
                    else None
                ),
            },
            metadata={"output_name": output_name},
            created_at=submission.submitted_at,
        )
        return replace(
            submission,
            candidate_output_refs={output_name: canonical_ref},
        )

    def planned_ai_unit_id(self, unit: TaskUnit) -> str | None:
        if unit.unit_type == "historical_direct_factor_leaf":
            return "historical_real_factorization_4733749"
        return None

    def evaluate_merge_readiness(
        self,
        context: MergeReadinessContext,
    ) -> MergeReadinessDecision:
        required = tuple(
            dict.fromkeys(
                str(slot["source_child_unit_id"])
                for slot in context.merge_plan.required_slots
            )
        )
        canonical = {
            child.unit_id
            for child in context.children
            if DIRECT_OUTPUT_NAME in child.canonical_output_refs
        }
        if set(required).issubset(canonical):
            return MergeReadinessDecision(
                status="ready",
                reason="single_verified_direct_leaf_canonical",
                policy_id=SINGLE_LEAF_MERGE_POLICY_ID,
                policy_version="v1",
                required_child_unit_ids=required,
                selected_child_unit_ids=required,
            )
        if any(
            child.state == TaskState.FAILED
            for child in context.children
            if child.unit_id in set(required)
        ):
            return MergeReadinessDecision(
                status="failed",
                reason="single_direct_leaf_failed",
                policy_id=SINGLE_LEAF_MERGE_POLICY_ID,
                policy_version="v1",
                required_child_unit_ids=required,
            )
        return MergeReadinessDecision(
            status="wait",
            reason="single_direct_leaf_not_canonical",
            policy_id=SINGLE_LEAF_MERGE_POLICY_ID,
            policy_version="v1",
            required_child_unit_ids=required,
        )

    @property
    def merge_candidate_refs(self) -> dict[str, Any]:
        if not self._merge_candidate_refs:
            raise ValueError("explicit single-leaf merge output is not prepared")
        return dict(self._merge_candidate_refs)

    def _canonical_action(self, context: CanonicalUnitContext):
        if context.unit.parent_unit_id is None:
            return self._expand_action(context)
        return self._complete_action(context)

    def _expand_action(self, context: CanonicalUnitContext) -> ExpandAction:
        canonical = context.canonical_selection
        scope = digest_json(
            {"fixture_id": FIXTURE_ID, "unit_id": context.unit.unit_id, "mode": "single_leaf"}
        )
        invocation = SplitStrategyInvocation(
            invocation_id=f"split_invocation:{scope.removeprefix('sha256:')}:attempt:1",
            invocation_attempt_no=1,
            expansion_scope_hash=scope,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
            split_strategy_params_digest=_SINGLE_LEAF_PARAMS_DIGEST,
            status="succeeded",
            result_action="expand",
            result_digest=digest_json(
                {"action": "expand", "child_count": 1, "fixture_id": FIXTURE_ID}
            ),
            started_at=self.created_at,
            completed_at=self.created_at,
        )
        proposal = self._proposal(
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            expansion_scope_hash=scope,
        )
        proposal_digest = digest_decomposition_proposal_body(proposal)
        proposal_id = f"decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
        proposal.proposal_header["proposal_id"] = proposal_id
        proposal.proposal_header["proposal_digest"] = proposal_digest
        child_unit_id = _child_unit_id(
            parent_unit_id=context.unit.unit_id,
            proposal_digest=proposal_digest,
        )
        decision_id = f"expansion_decision:{scope.removeprefix('sha256:')}"
        merge_plan = self._merge_plan(
            canonical_selection_id=canonical.canonical_selection_id,
            proposal_id=proposal_id,
            decision_id=decision_id,
            child_unit_id=child_unit_id,
        )
        merge_plan_digest = digest_merge_plan_body(merge_plan)
        merge_plan_id = f"merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
        merge_plan.merge_plan_header["merge_plan_id"] = merge_plan_id
        merge_plan.merge_plan_header["merge_plan_digest"] = merge_plan_digest
        decision = ExpansionDecision(
            expansion_decision_id=decision_id,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            expansion_scope_hash=scope,
            action="expand",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
            split_strategy_params_digest=_SINGLE_LEAF_PARAMS_DIGEST,
            source_invocation_id=invocation.invocation_id,
            proposal_id=proposal_id,
            proposal_digest=proposal_digest,
            merge_plan_id=merge_plan_id,
            merge_plan_digest=merge_plan_digest,
            action_body={
                "expand_evidence": {
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal_digest,
                    "merge_plan_id": merge_plan_id,
                    "merge_plan_digest": merge_plan_digest,
                    "child_count": 1,
                    "relation_count": 0,
                    "expected_output_count": 1,
                    "required_merge_slot_count": 1,
                }
            },
            decided_at=self.created_at,
        )
        return ExpandAction(
            invocation=invocation,
            decision=decision,
            proposal=proposal,
            merge_plan=merge_plan,
        )

    def _complete_action(self, context: CanonicalUnitContext) -> CompleteAction:
        canonical = context.canonical_selection
        scope = digest_json(
            {"task_id": context.unit.task_id, "unit_id": context.unit.unit_id}
        )
        invocation = SplitStrategyInvocation(
            invocation_id=f"split_invocation:{scope.removeprefix('sha256:')}:attempt:1",
            invocation_attempt_no=1,
            expansion_scope_hash=scope,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
            split_strategy_params_digest=_SINGLE_LEAF_PARAMS_DIGEST,
            status="succeeded",
            result_action="complete",
            result_digest=digest_json(
                {"action": "complete", "unit_id": context.unit.unit_id}
            ),
            started_at=canonical.bound_at,
            completed_at=canonical.bound_at,
        )
        decision = ExpansionDecision(
            expansion_decision_id=f"expansion_decision:{scope.removeprefix('sha256:')}",
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            expansion_scope_hash=scope,
            action="complete",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
            split_strategy_params_digest=_SINGLE_LEAF_PARAMS_DIGEST,
            source_invocation_id=invocation.invocation_id,
            action_body={
                "completion_evidence": {
                    "completion_kind": "verified_historical_direct_factor_leaf",
                    "validator_policy_id": SINGLE_LEAF_VALIDATOR_ID,
                    "verification_report_id": canonical.selected_verification_report_id,
                    "canonical_selection_id": canonical.canonical_selection_id,
                    "canonical_output_bundle_digest": canonical.canonical_output_bundle_digest,
                    "completed_output_refs": {
                        name: ref.to_dict()
                        for name, ref in canonical.canonical_output_refs.items()
                    },
                    "plugin_completion_summary": FACILITY_SEMANTICS,
                }
            },
            decided_at=canonical.bound_at,
        )
        return CompleteAction(invocation=invocation, decision=decision)

    def _proposal(
        self,
        *,
        canonical_selection_id: str,
        canonical_output_bundle_digest: str,
        expansion_scope_hash: str,
    ) -> DecompositionProposal:
        return DecompositionProposal(
            proposal_header={
                "proposal_id": "pending",
                "proposal_schema_version": "phase4.decomposition_proposal.v1",
                "task_id": TASK_ID,
                "parent_unit_id": ROOT_UNIT_ID,
                "canonical_selection_id": canonical_selection_id,
                "canonical_output_bundle_digest": canonical_output_bundle_digest,
                "plugin_id": PLUGIN_ID,
                "plugin_version": PLUGIN_VERSION,
                "plugin_descriptor_digest": self.descriptor.descriptor_digest,
                "split_strategy_id": SINGLE_LEAF_STRATEGY_ID,
                "split_strategy_params_digest": _SINGLE_LEAF_PARAMS_DIGEST,
                "expansion_scope_hash": expansion_scope_hash,
                "proposal_digest": "pending",
                "created_at": self.created_at,
            },
            child_specs=[
                {
                    "child_logical_key": CHILD_LOGICAL_KEY,
                    "unit_type": "historical_direct_factor_leaf",
                    "input_bindings": {},
                    "required_outputs": [DIRECT_OUTPUT_NAME],
                    "output_contract_refs": {DIRECT_OUTPUT_NAME: dict(_DIRECT_SCHEMA_REF)},
                    "validator_policy_id": SINGLE_LEAF_VALIDATOR_ID,
                    "budget_limit": None,
                    "deadline": None,
                    "weight": 1.0,
                    "required_capabilities": {
                        "executor": "historical_fixture_local",
                        "factorization": True,
                    },
                    "plugin_payload": {
                        "fixture_id": FIXTURE_ID,
                        "target_n": self.fixture.per_number_result["target_n"],
                        "classification": CLASSIFICATION,
                        "paper_eligible": False,
                    },
                    "promotion_guard_ref": None,
                }
            ],
            dependency_edges=[],
            expected_outputs=[
                {
                    "output_name": DIRECT_OUTPUT_NAME,
                    "schema_ref": dict(_DIRECT_SCHEMA_REF),
                    "resolution_kind": "merge_plan_output",
                    "child_key": None,
                    "child_output_name": None,
                    "merge_slot_id": "slot_historical_direct_factor",
                    "required": True,
                }
            ],
            merge_slots=[
                {
                    "slot_id": "slot_historical_direct_factor",
                    "child_key": CHILD_LOGICAL_KEY,
                    "child_output_name": DIRECT_OUTPUT_NAME,
                    "schema_ref": dict(_DIRECT_SCHEMA_REF),
                    "required": True,
                    "missing_policy": "block_merge",
                }
            ],
            promotion_guard_evidence={
                "typed_io_checked": True,
                "independently_schedulable_checked": True,
                "validator_policy_checked": True,
                "output_contract_checked": True,
                "no_freeform_thought_checked": True,
                "max_depth_checked": True,
                "max_children_checked": True,
                "evidence_ref": None,
            },
        )

    def _merge_plan(
        self,
        *,
        canonical_selection_id: str,
        proposal_id: str,
        decision_id: str,
        child_unit_id: str,
    ) -> MergePlan:
        schema_digest = digest_json(_DIRECT_SCHEMA_REF)
        payload_body = {
            "mode": "explicit_single_leaf",
            "semantics": FACILITY_SEMANTICS,
        }
        return MergePlan(
            merge_plan_header={
                "merge_plan_id": "pending",
                "merge_plan_schema_version": "phase4.merge_plan.v1",
                "task_id": TASK_ID,
                "parent_unit_id": ROOT_UNIT_ID,
                "canonical_selection_id": canonical_selection_id,
                "decomposition_proposal_id": proposal_id,
                "expansion_decision_id": decision_id,
                "created_by_plugin_id": PLUGIN_ID,
                "created_by_plugin_version": PLUGIN_VERSION,
                "merge_plan_digest": "pending",
                "created_at": self.created_at,
            },
            merge_policy_ref={
                "plugin_id": PLUGIN_ID,
                "plugin_version": PLUGIN_VERSION,
                "merge_policy_id": SINGLE_LEAF_MERGE_POLICY_ID,
                "merge_policy_version": "v1",
                "merge_policy_descriptor_digest": self.descriptor.descriptor_digest,
                "merge_policy_params_digest": digest_json(
                    {"mode": "explicit_single_leaf"}
                ),
            },
            required_slots=[
                {
                    "slot_key": "slot_historical_direct_factor",
                    "source_child_logical_key": CHILD_LOGICAL_KEY,
                    "source_child_unit_id": child_unit_id,
                    "source_output_name": DIRECT_OUTPUT_NAME,
                    "output_schema_ref": dict(_DIRECT_SCHEMA_REF),
                    "output_schema_digest": schema_digest,
                    "required": True,
                    "missing_policy": "block_merge",
                }
            ],
            parent_output_mapping=[
                {
                    "parent_output_name": DIRECT_OUTPUT_NAME,
                    "resolution_kind": "merge_plan_output",
                    "merge_slot_keys": ["slot_historical_direct_factor"],
                    "result_schema_ref": dict(_DIRECT_SCHEMA_REF),
                    "result_schema_digest": schema_digest,
                }
            ],
            hash_recording_requirements={
                "record_child_canonical_output_digest": True,
                "record_slot_source_artifact_digest": True,
                "record_merge_input_bundle_digest": True,
            },
            merge_validation_requirements={
                "all_required_slots_canonical": True,
                "slot_schema_check_required": True,
                "merged_output_schema_check_required": True,
                "plugin_merge_validator_policy_id": (
                    "factorization.explicit_single_leaf_merge.validator.v1"
                ),
            },
            plugin_payload={
                "plugin_defined_schema_ref": {
                    "schema": "factorization.historical_single_leaf_merge.v1"
                },
                "plugin_defined_body_digest": digest_json(payload_body),
                "plugin_defined_body": payload_body,
            },
        )

    def _merge_resolution(
        self,
        context: MergeExecutionContext,
    ) -> MergeResolutionAction:
        canonical = context.canonical_selection
        link = context.merge_task_link
        merge_record = MergeRecord(
            merge_record_id=(
                f"merge_record:{link.merge_plan_id}:"
                f"{link.merge_unit_id}:{canonical.canonical_selection_id}"
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
            merge_policy_params_digest=context.merge_plan.merge_policy_ref[
                "merge_policy_params_digest"
            ],
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_event_seq=context.canonical_event.event_seq,
            selected_verification_report_id=canonical.selected_verification_report_id,
            selected_verification_event_seq=canonical.selected_verification_event_seq,
            selected_submission_id=canonical.selected_submission_id,
            selected_submission_event_seq=canonical.selected_submission_event_seq,
            selected_attempt_id=canonical.selected_attempt_id,
            merge_output_bundle_digest=canonical.canonical_output_bundle_digest,
            merge_output_refs={
                name: ref.to_dict()
                for name, ref in canonical.canonical_output_refs.items()
            },
            parent_output_mapping_digest=digest_json(
                context.merge_plan.parent_output_mapping
            ),
            created_at=canonical.bound_at,
        )
        expected = context.expected_output_refs[0]
        output_ref = canonical.canonical_output_refs[DIRECT_OUTPUT_NAME]
        resolution = ExpectedOutputResolution(
            expected_output_resolution_id=(
                f"expected_output_resolved:{expected.expected_output_id}:"
                f"{merge_record.merge_record_id}"
            ),
            task_id=merge_record.task_id,
            owner_unit_id=merge_record.parent_unit_id,
            expected_output_id=expected.expected_output_id,
            expected_output_name=expected.output_name,
            resolution_source_type="merge_record",
            merge_record_id=merge_record.merge_record_id,
            merge_plan_id=merge_record.merge_plan_id,
            merge_unit_id=merge_record.merge_unit_id,
            merge_canonical_selection_id=merge_record.canonical_selection_id,
            resolved_output_ref=output_ref.to_dict(),
            resolved_output_digest=output_ref.content_hash,
            resolved_at=canonical.bound_at,
        )
        return MergeResolutionAction(
            merge_record=merge_record,
            expected_output_resolutions=(resolution,),
        )

    def _require_store(self) -> ArtifactStore:
        if self._store is None:
            raise RuntimeError("historical adapter has not planned a root")
        return self._store


class HistoricalSingleLeafExecutionBridge:
    """只读消费 tracked payload；不会访问 provider 或网络。"""

    def __init__(
        self,
        *,
        plugin_runtime: HistoricalSingleLeafRuntimeAdapter,
        artifact_store: ArtifactStore,
    ) -> None:
        self._runtime = plugin_runtime
        self._store = artifact_store
        self.provider_call_count = 0
        self.network_call_count = 0

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        unit_type = str(request.task_unit_snapshot["unit_type"])
        if request.task_unit_snapshot["parent_unit_id"] is None:
            refs, raw_ref, parsed_ref, provenance_ref, usage = self._root_submission(
                submitted_at=submitted_at
            )
        elif unit_type == "historical_direct_factor_leaf":
            refs, raw_ref, parsed_ref, provenance_ref, usage = self._leaf_submission(
                request=request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
        else:
            refs = self._runtime.merge_candidate_refs
            raw_ref = None
            parsed_ref = None
            provenance_ref = None
            usage = {"provider_attempt_count": 0, "network_call_count": 0}
        submission = ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=EXECUTOR_ID,
            executor_version=EXECUTOR_VERSION,
            result_kind="succeeded",
            raw_output_ref=raw_ref,
            parsed_output_ref=parsed_ref,
            candidate_output_refs=refs,
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={
                "runtime": "historical_fixture_local",
                "network_access": False,
                "provider_calls": 0,
                "classification": CLASSIFICATION,
            },
            provenance_ref=provenance_ref,
            usage_summary=usage,
            error=None,
            submitted_at=submitted_at,
        )
        return self._runtime.normalize_submission(submission)

    def _root_submission(self, *, submitted_at: str):
        marker = {
            "schema_version": "factorization.historical_root_marker.v1",
            "fixture_id": FIXTURE_ID,
            "target_n": self._runtime.fixture.per_number_result["target_n"],
            "classification": CLASSIFICATION,
            "paper_eligible": False,
        }
        parsed_ref = self._store.save_json(
            marker,
            artifact_id="historical_real_parsed_root_marker",
            artifact_type="ParsedModelOutput",
            artifact_schema_id="factorization.historical_root_marker",
            artifact_schema_version="v1",
            source={"kind": "historical_fixture_root_parser", "role": "root_parser_result"},
            metadata={"output_name": ROOT_MARKER_OUTPUT},
            created_at=submitted_at,
        )
        candidate_ref = self._store.save_json(
            marker,
            artifact_id="historical_real_candidate_root_marker",
            artifact_type="CandidateOutput",
            artifact_schema_id="factorization.historical_root_marker",
            artifact_schema_version="v1",
            source={
                "kind": "historical_fixture_root_parser",
                "parsed_output_ref": parsed_ref.to_dict(),
            },
            metadata={"output_name": ROOT_MARKER_OUTPUT},
            created_at=submitted_at,
        )
        return (
            {ROOT_MARKER_OUTPUT: candidate_ref},
            None,
            parsed_ref,
            None,
            {"provider_attempt_count": 0, "network_call_count": 0},
        )

    def _leaf_submission(
        self,
        *,
        request: ExecutionRequest,
        submission_id: str,
        submitted_at: str,
    ):
        fixture = self._runtime.fixture
        raw_ref = self._store.save_bytes(
            fixture.raw_payload_bytes,
            artifact_id="historical_real_raw_direct_factor_payload",
            artifact_type="RawModelOutput",
            media_type="application/json",
            artifact_schema_id="factorization.direct_factorization_raw_payload",
            artifact_schema_version="v1",
            source={
                "kind": "tracked_historical_real_fixture",
                "fixture_id": FIXTURE_ID,
                "source_object_digest": fixture.source_digests["raw_model_output"],
            },
            metadata={
                "unmodified_payload": True,
                "classification": CLASSIFICATION,
            },
            created_at=submitted_at,
        )
        parsed = parse_direct_factorization_ai_output(
            fixture.raw_payload_bytes.decode("utf-8"),
            target_n=str(fixture.per_number_result["target_n"]),
            raw_output_ref_summary=raw_ref.to_dict(),
            created_at=submitted_at,
        )
        if not parsed.succeeded or parsed.parsed_artifact_body is None:
            raise ValueError("historical direct factor payload failed the current parser")
        parsed_ref = self._store.save_json(
            parsed.parsed_artifact_body,
            artifact_id="historical_real_parsed_direct_factor_answer",
            artifact_type="ParsedModelOutput",
            artifact_schema_id="factorization.direct_factorization_answer",
            artifact_schema_version="v1",
            source={
                "kind": "current_direct_factor_parser",
                "role": "parser_result",
                "task_id": TASK_ID,
                "execution_id": "historical_real_factorization_single_leaf_v1",
                "raw_output_ref": raw_ref.to_dict(),
                "parser_id": DIRECT_PARSER_ID,
            },
            metadata={"output_name": DIRECT_OUTPUT_NAME},
            created_at=submitted_at,
        )
        candidate_body = parsed.candidate_output_artifact_bodies[DIRECT_OUTPUT_NAME]
        candidate_ref = self._store.save_json(
            candidate_body,
            artifact_id="historical_real_candidate_direct_factor_answer",
            artifact_type="CandidateOutput",
            artifact_schema_id="factorization.direct_factorization_answer",
            artifact_schema_version="v1",
            source={
                "kind": "current_direct_factor_parser",
                "raw_output_ref": raw_ref.to_dict(),
                "parsed_output_ref": parsed_ref.to_dict(),
            },
            metadata={"output_name": DIRECT_OUTPUT_NAME},
            created_at=submitted_at,
        )
        provenance_ref = self._store.save_json(
            {
                **dict(fixture.provenance),
                "current_provider_call_count": 0,
                "current_network_call_count": 0,
                "fixture_id": FIXTURE_ID,
            },
            artifact_id="historical_real_source_provenance",
            artifact_type="HistoricalSourceProvenance",
            artifact_schema_id="tokenshare.historical_source_provenance",
            artifact_schema_version="v1",
            source={
                "kind": "tracked_historical_real_fixture",
                "source_object_digest": fixture.source_digests["provenance"],
            },
            metadata={"classification": CLASSIFICATION},
            created_at=submitted_at,
        )
        self._store.save_json(
            dict(fixture.usage),
            artifact_id="historical_real_source_usage",
            artifact_type="HistoricalSourceUsage",
            artifact_schema_id="tokenshare.historical_source_usage",
            artifact_schema_version="v1",
            source={"kind": "tracked_historical_real_fixture"},
            metadata={"current_provider_call_count": 0},
            created_at=submitted_at,
        )
        return (
            {DIRECT_OUTPUT_NAME: candidate_ref},
            raw_ref,
            parsed_ref,
            provenance_ref,
            {
                **dict(fixture.usage),
                "provider_attempt_count": 0,
                "source_provider_attempt_count": 1,
                "network_call_count": 0,
                "submission_id": submission_id,
                "request_id": request.request_id,
            },
        )


def run_historical_real_factorization_single_leaf(
    *,
    fixture_root: str | Path,
    output_root: str | Path,
    formal_range_catalog: Mapping[str, Any] | None = None,
    condition: str | None = None,
) -> HistoricalSingleLeafRunResult:
    """从 tracked fixture 运行 L2 facility path；拒绝 range/catalog 条件。"""

    if formal_range_catalog is not None or condition is not None:
        raise ValueError("historical single-leaf adapter rejects formal range semantics")
    fixture = load_historical_real_fixture(fixture_root)
    if fixture.manifest.get("marker") != FIXTURE_ID:
        raise ValueError("historical single-leaf fixture marker mismatch")
    root = Path(output_root)
    store = _EvidenceBoundArtifactStore(root)
    ledger = EventLedger(root / "events" / "historical_single_leaf.jsonl")
    config = ProtocolConfig.default(
        config_id="historical_real_factorization_single_leaf_v1",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/historical_single_leaf.jsonl",
        metadata={
            "classification": CLASSIFICATION,
            "paper_eligible": False,
            "semantics": FACILITY_SEMANTICS,
        },
    )
    runtime = HistoricalSingleLeafRuntimeAdapter(
        fixture=fixture,
        protocol_config=config,
    )
    bridge = HistoricalSingleLeafExecutionBridge(
        plugin_runtime=runtime,
        artifact_store=store,
    )
    clock = _Clock()
    coordinator = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    )
    protocol_result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="historical_real_factorization_single_leaf_v1",
            root_input={
                "schema_version": "factorization.historical_single_leaf_root.v1",
                "fixture_id": FIXTURE_ID,
                "target_n": fixture.per_number_result["target_n"],
                "classification": CLASSIFICATION,
                "paper_eligible": False,
                "semantics": FACILITY_SEMANTICS,
            },
            plugin_runtime=runtime,
            worker_backend=SequentialWorkerBackend(
                executor=bridge,
                submitted_at=clock,
            ),
        )
    )
    if protocol_result.status != "completed":
        raise RuntimeError("historical single-leaf normal protocol run did not complete")
    _persist_projection_evidence(fixture=fixture, store=store, ledger=ledger)
    projected = reproject_historical_real_factorization_single_leaf(
        fixture_root=fixture_root,
        output_root=root,
    )
    if projected.provider_call_count or projected.network_call_count:
        raise RuntimeError("historical replay unexpectedly recorded current transport")
    return projected


def reproject_historical_real_factorization_single_leaf(
    *,
    fixture_root: str | Path,
    output_root: str | Path,
) -> HistoricalSingleLeafRunResult:
    """只从持久化 ledger/artifact 重投影 typed direct result；任何缺口均失败。"""

    fixture = load_historical_real_fixture(fixture_root)
    root = Path(output_root)
    store = ArtifactStore(root)
    ledger = EventLedger(root / "events" / "historical_single_leaf.jsonl")
    runtime_result = project_protocol_run(
        run_id="historical_real_factorization_single_leaf_v1",
        task_id=TASK_ID,
        root_unit_id=ROOT_UNIT_ID,
        event_ledger=ledger,
        artifact_store=store,
    )
    if runtime_result.status != "completed":
        raise ValueError("persisted historical protocol run is not completed")
    events = ledger.read_all()
    final_ref = _final_result_ref(events, store)
    parser_refs = _refs_for_roles(store, {"parser_result"})
    verifier_refs = _refs_for_roles(
        store,
        {"independent_verdict", "verification_report"},
    )
    trace_refs = _refs_for_roles(store, {"trace_resource_book"})
    if len(trace_refs) != 1:
        raise ValueError("historical trace resource book is missing or ambiguous")
    inventory, condition_manifest, catalog_manifest = _direct_inventory()
    locators = _source_locators(fixture)
    evidence = build_canonical_direct_evidence(
        inventory_row=inventory.rows[0],
        execution_id=runtime_result.run_id,
        event_ledger=ledger,
        artifact_store=store,
        runtime_result=runtime_result,
        final_result_ref=final_ref,
        parser_refs=parser_refs,
        verifier_checker_refs=verifier_refs,
        source_bank_object_locators=locators,
        trace_resource_book_ref=trace_refs[0],
    )
    projection = project_paper_direct_results(
        root_inventory_manifest=inventory,
        condition_manifests=(condition_manifest,),
        catalog_manifests=(catalog_manifest,),
        canonical_runtime_evidence=(evidence,),
    )
    direct = projection.rows[0]
    report_ref = next(
        ref for ref in verifier_refs if ref.source.get("role") == "verification_report"
    )
    report = _read_json_ref(store, report_ref)
    evaluation = report.get("domain_evaluation")
    if not isinstance(evaluation, Mapping):
        raise ValueError("persisted verifier domain evaluation is missing")
    verification = {
        "product_check_passed": evaluation.get("product_check_passed") is True,
        "primality_check_passed": evaluation.get("primality_check_passed") is True,
        "oracle_match": evaluation.get("oracle_match") is True,
    }
    direct_body = direct.to_dict()
    metric_observation = {
        "schema_version": "tokenshare.historical_facility_metric_observation.v2",
        "metric_id": "historical_single_leaf_independently_verified_correct",
        "value": direct.independently_verified_correct,
        "source_direct_result_digest": digest_json(direct_body),
        "classification": CLASSIFICATION,
        "paper_eligible": False,
    }
    binding = direct.execution_binding
    if binding is None:
        raise ValueError("typed direct result has no execution binding")
    regression_row = {
        "fixture_id": FIXTURE_ID,
        "facility_success": direct.independently_verified_correct,
        "provider_call_count": 0,
        "network_call_count": 0,
        "classification": CLASSIFICATION,
        "paper_eligible": False,
        "semantics": FACILITY_SEMANTICS,
        "execution_binding_digest": binding.binding_digest,
        "direct_result_digest": digest_json(direct_body),
        "direct_result": direct_body,
    }
    return HistoricalSingleLeafRunResult(
        protocol_result=runtime_result,
        event_types=tuple(event.event_type.value for event in events),
        verification=verification,
        canonical_evidence=evidence,
        direct_projection=projection,
        direct_result=direct,
        metric_observation=metric_observation,
        regression_table=(regression_row,),
    )


def _persist_projection_evidence(
    *,
    fixture: HistoricalRealFixture,
    store: ArtifactStore,
    ledger: EventLedger,
) -> None:
    events = ledger.read_all()
    final_ref = _final_result_ref(events, store)
    verification_events = []
    for event in events:
        if event.event_type.value != "VERIFICATION_RECORDED":
            continue
        report = event.payload.get("verification_report")
        if not isinstance(report, Mapping):
            continue
        refs = report.get("candidate_output_refs")
        if (
            report.get("status") in {"passed", "accepted"}
            and report.get("eligible_for_canonical") is True
            and isinstance(refs, Mapping)
            and any(_same_ref(value, final_ref) for value in refs.values())
        ):
            verification_events.append(event)
    if len(verification_events) != 1:
        raise ValueError("final result requires one official accepted verification event")
    canonical_events = []
    for event in events:
        if event.event_type.value != "CANONICAL_OUTPUTS_BOUND":
            continue
        selection = event.payload.get("canonical_selection")
        refs = selection.get("canonical_output_refs") if isinstance(selection, Mapping) else None
        if isinstance(refs, Mapping) and any(
            _same_ref(value, final_ref) for value in refs.values()
        ):
            canonical_events.append(event)
    if len(canonical_events) != 1:
        raise ValueError("final result requires one official canonical event")
    verification_event = verification_events[0]
    canonical_event = canonical_events[0]
    if verification_event.event_seq >= canonical_event.event_seq:
        raise ValueError("final verification must precede canonical binding")
    answer = _read_json_ref(store, final_ref)
    evaluation = evaluate_direct_factorization_answer(
        answer,
        target_n=str(fixture.per_number_result["target_n"]),
        oracle_prime_factors=list(fixture.per_number_result["oracle_prime_factors"]),
    )
    correct = evaluation.get("final_correctness") is True
    source = {
        "task_id": TASK_ID,
        "execution_id": "historical_real_factorization_single_leaf_v1",
    }
    store.save_json(
        {
            "schema_version": "tokenshare.paper_direct_correctness_verdict.v1",
            "execution_id": "historical_real_factorization_single_leaf_v1",
            "task_id": TASK_ID,
            "root_unit_id": ROOT_UNIT_ID,
            "final_artifact_id": final_ref.artifact_id,
            "final_content_hash": final_ref.content_hash,
            "final_size_bytes": final_ref.size_bytes,
            "verdict_kind": "independent_verifier",
            "correct": correct,
        },
        artifact_id="historical_real_independent_final_verdict",
        artifact_type="PaperDirectEvidence",
        artifact_schema_id="tokenshare.paper_direct_correctness_verdict",
        artifact_schema_version="v1",
        source={**source, "role": "independent_verdict"},
        metadata={},
        created_at=NOW,
    )
    store.save_json(
        {
            "schema_version": "tokenshare.historical_verification_evidence.v1",
            "domain_evaluation": evaluation,
            "verification_event_seq": verification_event.event_seq,
            "verification_event_hash": verification_event.event_hash,
            "canonical_event_seq": canonical_event.event_seq,
            "canonical_event_hash": canonical_event.event_hash,
            "final_result_ref": final_ref.to_dict(),
        },
        artifact_id="historical_real_verification_evidence",
        artifact_type="PaperDirectEvidence",
        artifact_schema_id="tokenshare.historical_verification_evidence",
        artifact_schema_version="v1",
        source={**source, "role": "verification_report"},
        metadata={},
        created_at=NOW,
    )
    store.save_json(
        {
            "schema_version": "tokenshare.historical_trace_resource_book.v1",
            "fixture_id": FIXTURE_ID,
            "source_digests": fixture.source_digests,
        },
        artifact_id="historical_real_trace_resource_book",
        artifact_type="PaperDirectEvidence",
        artifact_schema_id="tokenshare.historical_trace_resource_book",
        artifact_schema_version="v1",
        source={**source, "role": "trace_resource_book"},
        metadata={},
        created_at=NOW,
    )
    if not correct:
        raise ValueError("independent final verifier rejected historical result")


def _same_ref(value: object, expected: ArtifactRef) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("artifact_id") == expected.artifact_id
        and value.get("content_hash") == expected.content_hash
        and value.get("size_bytes") == expected.size_bytes
    )


def _final_result_ref(events: list[Any], store: ArtifactStore) -> ArtifactRef:
    matches: list[ArtifactRef] = []
    for event in events:
        if event.event_type.value != "MERGE_RECORDED":
            continue
        if event.payload.get("parent_unit_id") != ROOT_UNIT_ID:
            continue
        refs = event.payload.get("merge_output_refs")
        if isinstance(refs, Mapping) and DIRECT_OUTPUT_NAME in refs:
            matches.append(ArtifactRef.from_dict(refs[DIRECT_OUTPUT_NAME]))
    if len(matches) != 1:
        raise ValueError("historical run requires one unique merged final result")
    persisted = store.load_artifact_ref(matches[0].artifact_id)
    if persisted.to_dict() != matches[0].to_dict():
        raise ValueError("merged final artifact manifest binding mismatch")
    return persisted


def _refs_for_roles(store: ArtifactStore, roles: set[str]) -> tuple[ArtifactRef, ...]:
    refs = []
    for path in sorted(store.artifact_dir.glob("*.manifest.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        ref = ArtifactRef.from_dict(value)
        if ref.source.get("role") in roles:
            refs.append(ref)
    found = {str(ref.source.get("role")) for ref in refs}
    if found != roles or len(refs) != len(roles):
        raise ValueError("required persisted artifact evidence role is missing or ambiguous")
    return tuple(refs)


def _source_locators(
    fixture: HistoricalRealFixture,
) -> tuple[ExternalBankObjectLocator, ...]:
    manifest_digest = digest_json(fixture.manifest)
    digests = {
        "raw_output": fixture.source_digests["raw_model_output"],
        "provenance": fixture.source_digests["provenance"],
        "model_record": fixture.source_digests["batch_report"],
    }
    return tuple(
        ExternalBankObjectLocator(
            bank_root_id=FIXTURE_ID,
            manifest_digest=manifest_digest,
            entry_id=str(fixture.provenance["entry_id"]),
            object_role=role,
            object_digest=object_digest,
        )
        for role, object_digest in digests.items()
    )


def _direct_inventory() -> tuple[
    PreregisteredRootInventoryManifest,
    dict[str, Any],
    dict[str, Any],
]:
    axes = {
        "domain": "factorization",
        "difficulty": "hard",
        "topic_family": None,
        "worker_count": 1,
        "sample_slot_index": 0,
        "fault_condition": None,
        "death_condition": None,
        "ablation_mode": None,
        "model_endpoint_id": None,
    }
    condition_record_body = {
        "schema_version": "tokenshare.preregistered_condition_record.v1",
        "condition_id": "historical_single_leaf_regression",
        "condition_axes": axes,
        "condition_axes_digest": digest_json(axes),
    }
    condition_record = {
        **condition_record_body,
        "condition_record_digest": digest_json(condition_record_body),
    }
    condition_body = {
        "schema_version": "tokenshare.preregistered_condition_manifest.v1",
        "records": [condition_record],
    }
    condition_manifest = {
        **condition_body,
        "condition_manifest_digest": digest_json(condition_body),
    }
    case_axes = {"factor_position_quantile": "early", "position_stratum": "front"}
    case_body = {
        "schema_version": "tokenshare.preregistered_case_record.v1",
        "case_id": FIXTURE_ID,
        "domain": "factorization",
        "difficulty": "hard",
        "case_axes_digest": digest_json(case_axes),
        **case_axes,
    }
    case_record = {**case_body, "case_record_digest": digest_json(case_body)}
    catalog_body = {
        "schema_version": "tokenshare.preregistered_case_catalog_manifest.v1",
        "records": [case_record],
    }
    catalog_manifest = {**catalog_body, "catalog_digest": digest_json(catalog_body)}
    condition_ref = {
        "schema_version": "tokenshare.preregistered_condition_ref.v1",
        "condition_manifest_digest": condition_manifest["condition_manifest_digest"],
        "condition_record_digest": condition_record["condition_record_digest"],
        "condition_axes_digest": condition_record["condition_axes_digest"],
    }
    case_ref = {
        "schema_version": "tokenshare.preregistered_case_ref.v1",
        "catalog_digest": catalog_manifest["catalog_digest"],
        "case_record_digest": case_record["case_record_digest"],
        "case_axes_digest": case_record["case_axes_digest"],
        **case_axes,
    }
    row_values = {
        "inventory_id": "historical_single_leaf_regression_inventory",
        "preregistered_root_run_id": "historical_single_leaf_regression_root",
        "experiment_id": "historical_facility_regression",
        "condition_id": "historical_single_leaf_regression",
        "preregistered_condition_ref": condition_ref,
        "condition_axes": axes,
        "case_id": FIXTURE_ID,
        "preregistered_case_ref": case_ref,
        "repeat_id": 0,
        "evidence_class": CLASSIFICATION,
    }
    row = PaperDirectRootInventoryRow(
        **row_values,
        inventory_row_digest=digest_json(
            {
                "schema_version": "tokenshare.paper_direct_root_inventory_row.v2",
                **row_values,
            }
        ),
    )
    inventory_body = {
        "schema_version": "tokenshare.preregistered_root_inventory_manifest.v1",
        "inventory_id": row.inventory_id,
        "root_count": 1,
        "rows": [row.to_dict()],
    }
    inventory = PreregisteredRootInventoryManifest(
        inventory_id=row.inventory_id,
        rows=(row,),
        root_count=1,
        inventory_digest=digest_json(inventory_body),
    )
    return inventory, condition_manifest, catalog_manifest


def classify_20260731_exp34_negative(source_root: str | Path) -> dict[str, Any]:
    """只读标记 2026-07-31 Exp3/4 source；它永远不是正向 fixture。"""

    root = Path(source_root)
    if root.name != SOURCE_ID_20260731:
        raise ValueError("unexpected 2026-07-31 Exp3/4 negative source identity")
    before = source_tree_digest(root)
    after = source_tree_digest(root)
    if before != after:
        raise RuntimeError("negative historical source changed during read-only classification")
    return {
        "source_id": SOURCE_ID_20260731,
        "classification": "negative_only_expected_fail",
        "paper_eligible": False,
        "tree_digest_before": before,
        "tree_digest_after": after,
    }


def _plugin_descriptor() -> PluginDescriptor:
    root_contract = _root_output_contract()
    direct_contract = _direct_output_contract()
    return PluginDescriptor(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        supported_task_types=[
            "root",
            "historical_direct_factor_leaf",
            "merge",
        ],
        input_contract={
            "root_input": {
                "artifact_schema_id": "factorization.historical_single_leaf_root",
                "artifact_schema_version": "v1",
            }
        },
        output_contracts={
            ROOT_MARKER_OUTPUT: root_contract,
            DIRECT_OUTPUT_NAME: direct_contract,
        },
        execution_contracts={
            "historical_fixture_local": {
                "hard_requirements": {"executor": "historical_fixture_local"},
                "network_access": False,
                "provider_calls": 0,
            }
        },
        split_strategies={
            SINGLE_LEAF_STRATEGY_ID: SplitStrategyContract(
                split_strategy_id=SINGLE_LEAF_STRATEGY_ID,
                params_schema_ref={
                    "artifact_schema_id": "factorization.historical_single_leaf_params",
                    "artifact_schema_version": "v1",
                },
                allowed_unit_types=["historical_direct_factor_leaf"],
                child_input_port_schema_refs={},
                child_output_contract_refs={
                    "historical_direct_factor_leaf": {
                        "output_contract_id": DIRECT_OUTPUT_CONTRACT_ID
                    }
                },
                validator_policy_id=SINGLE_LEAF_VALIDATOR_ID,
                merge_policy_id=SINGLE_LEAF_MERGE_POLICY_ID,
                durable_subgoal_policy={
                    "only_promote_unit_types": ["historical_direct_factor_leaf"],
                    "executor_may_define_task_graph": False,
                    "formal_range_semantics": False,
                },
                candidate_artifact_policy={
                    "required_structured_output": DIRECT_OUTPUT_NAME,
                    "required_schema_version": DIRECT_ANSWER_SCHEMA_VERSION,
                    "raw_text_authoritative": False,
                    "executor_may_define_task_graph": False,
                },
                max_children_per_expansion=1,
            )
        },
        validator_policy_id=SINGLE_LEAF_VALIDATOR_ID,
        merge_policy_id=SINGLE_LEAF_MERGE_POLICY_ID,
        metadata={
            "classification": CLASSIFICATION,
            "paper_eligible": False,
            "semantics": FACILITY_SEMANTICS,
            "exclusive_task_types": ["historical_direct_factor_leaf"],
        },
    )


def _executor_descriptor() -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_id=EXECUTOR_ID,
        executor_type=EXECUTOR_TYPE,
        executor_version=EXECUTOR_VERSION,
        supported_request_schema_versions=[
            "phase3.execution_request.v1",
            "phase3.execution_request.v2",
        ],
        capabilities={
            "executor": ["historical_fixture_local", "local"],
            "factorization": True,
            "merge": True,
        },
        environment_policy={"runtime": "python", "network_access": False},
        status=ExecutorStatus.AVAILABLE,
        metadata={"provider_calls": 0, "classification": CLASSIFICATION},
    )


def _root_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=ROOT_MARKER_CONTRACT_ID,
        required_outputs=[ROOT_MARKER_OUTPUT],
        output_schema_refs={ROOT_MARKER_OUTPUT: dict(_ROOT_SCHEMA_REF)},
        raw_output_policy={"allowed": False, "media_type": "application/json"},
        parsed_output_schema_ref=dict(_ROOT_SCHEMA_REF),
    )


def _direct_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=DIRECT_OUTPUT_CONTRACT_ID,
        required_outputs=[DIRECT_OUTPUT_NAME],
        output_schema_refs={DIRECT_OUTPUT_NAME: dict(_DIRECT_SCHEMA_REF)},
        raw_output_policy={
            "allowed": True,
            "authoritative": False,
            "media_type": "application/json",
        },
        parsed_output_schema_ref=dict(_DIRECT_SCHEMA_REF),
    )


def _environment_ref(fixture: HistoricalRealFixture) -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_historical_real_factorization_single_leaf",
        environment_digest=digest_json(
            {
                "fixture_id": FIXTURE_ID,
                "classification": CLASSIFICATION,
                "network_access": False,
            }
        ),
        runtime="python",
        tool_versions={"adapter": "v1", "parser": DIRECT_PARSER_ID},
        resource_limits={"timeout_seconds": 30, "network_access": False},
        fixture_profile_digest=digest_json(fixture.manifest),
        seed=0,
        clock_policy="fixed",
        created_at=NOW,
    )


def _validate_root_input(value: object, fixture: HistoricalRealFixture) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("historical single-leaf root input must be an object")
    if value.get("fixture_id") != FIXTURE_ID:
        raise ValueError("historical single-leaf fixture identity mismatch")
    if value.get("target_n") != fixture.per_number_result["target_n"]:
        raise ValueError("historical single-leaf target mismatch")
    if value.get("classification") != CLASSIFICATION or value.get("paper_eligible") is not False:
        raise ValueError("historical single-leaf evidence classification mismatch")
    if value.get("semantics") != FACILITY_SEMANTICS:
        raise ValueError("historical single-leaf facility semantics mismatch")


def _child_unit_id(*, parent_unit_id: str, proposal_digest: str) -> str:
    return (
        f"unit_{_safe(parent_unit_id)}_"
        f"{_safe(proposal_digest.removeprefix('sha256:'))}_{_safe(CHILD_LOGICAL_KEY)}"
    )


def _read_json_ref(store: ArtifactStore, ref) -> dict[str, Any]:
    value = json.loads(store.read_bytes(ref).decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("historical factorization artifact must be a JSON object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe(value: object) -> str:
    text = str(value)
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in text)


class _Clock:
    def __init__(self) -> None:
        self._value = datetime(2026, 7, 2, tzinfo=UTC)

    def __call__(self) -> str:
        value = self._value
        self._value += timedelta(seconds=1)
        return value.isoformat().replace("+00:00", "Z")
