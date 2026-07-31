"""Factorization 插件到本地协议 runtime 的领域桥接。"""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from math import isqrt
from typing import Any, Callable

from tokenshare.core.expansion import ExpansionDecision, SplitStrategyInvocation
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord
from tokenshare.core.models import (
    ArtifactRef,
    ClientRecord,
    JsonObject,
    ProtocolConfig,
    TaskState,
    TaskUnit,
)
from tokenshare.core.registration import RootTaskRegistrationRequest
from tokenshare.core.verification import build_verification_report, digest_json
from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
    ExecutorDescriptor,
    ExecutorStatus,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.local_runtime.contracts import (
    CanonicalUnitContext,
    CompleteAction,
    ExpandAction,
    MergeAction,
    MergeExecutionContext,
    MergeReadinessContext,
    MergeReadinessDecision,
    MergeResolutionAction,
    RootProtocolPlan,
)
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.factorization.descriptor import (
    build_factorization_plugin_descriptor,
)
from tokenshare.plugins.factorization.merge_policy import (
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
from tokenshare.plugins.factorization.prompt_builder import (
    build_factor_search_prompt_package,
)
from tokenshare.plugins.factorization.schemas import (
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    FACTOR_WITNESS_OR_ALL_RANGES_MERGE_POLICY_ID,
    FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
    FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION,
    FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
    FACTOR_INTEGER_SUBJECT_OUTPUT_NAME,
    FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    ROOT_INPUT_SCHEMA_VERSION,
    schema_ref,
)
from tokenshare.plugins.factorization.split_strategy import (
    EXP2_CONTIGUOUS_20WAY_PROFILE_ID,
    FactorizationSplitPlanResult,
    FactorizationSplitStrategyActionResult,
    build_factorization_split_strategy_result,
    resolve_requested_child_count,
)
from tokenshare.plugins.factorization.validator import (
    build_factor_search_instruction,
    verify_factor_integer_subject,
    verify_range_result,
)
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-14T00:00:00Z"
DETERMINISTIC_EXECUTOR_ID = "executor_factorization_runtime"
DETERMINISTIC_EXECUTOR_VERSION = "0.1.0"


class FactorizationRuntimeAdapter:
    """把 factorization 领域计划投影为 runtime 可消费对象。

    实例内缓存 request/ref 与 merge candidate，只支持单次 run 的顺序执行；
    不得跨 run 复用，也不得由并发 worker 共享。
    """

    def __init__(
        self,
        *,
        provider_family: str,
        seed: int | None = None,
        protocol_config: ProtocolConfig | None = None,
        executor_requirements: JsonObject | None = None,
        max_tokens: int = 512,
        timeout_seconds: int = 30,
        created_at: str = NOW,
        lifecycle_clock: Callable[[], str] | None = None,
        clock_policy: str = "fixed",
    ) -> None:
        self.provider_family = provider_family
        self.seed = seed
        self.protocol_config = protocol_config or ProtocolConfig.default(
            config_id="factorization_runtime_preflight",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/factorization_runtime.jsonl",
        )
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._created_at = created_at
        self._lifecycle_clock = lifecycle_clock
        self.clock_policy = clock_policy
        self.descriptor = build_factorization_plugin_descriptor()
        base_executor_descriptor = build_ai_api_executor_descriptor(
            provider_family=provider_family
        )
        supplied_requirements = dict(executor_requirements or {})
        for name, expected in {
            "executor": "ai_api",
            "provider_family": provider_family,
        }.items():
            actual = supplied_requirements.get(name, expected)
            if actual != expected:
                raise ValueError(f"factorization executor requirement mismatch: {name}")
        self.executor_requirements = {
            "executor": "ai_api",
            "provider_family": provider_family,
            **supplied_requirements,
        }
        self.executor_descriptor = replace(
            base_executor_descriptor,
            capabilities={
                **dict(base_executor_descriptor.capabilities),
                **self.executor_requirements,
            },
        )
        self.deterministic_executor_descriptor = ExecutorDescriptor(
            executor_id=DETERMINISTIC_EXECUTOR_ID,
            executor_type="deterministic_local",
            executor_version=DETERMINISTIC_EXECUTOR_VERSION,
            supported_request_schema_versions=["phase3.execution_request.v1"],
            capabilities={
                "executor": ["deterministic_local", "local"],
                "factorization": True,
                "merge": True,
            },
            environment_policy={"runtime": "python", "network_access": False},
            status=ExecutorStatus.AVAILABLE,
            metadata={"runtime": "factorization"},
        )
        self._artifact_store: ArtifactStore | None = None
        self._case: JsonObject | None = None
        self._split_plan: FactorizationSplitPlanResult | None = None
        self._ranges_by_unit_id: dict[str, FactorSearchRangeInput] = {}
        self._range_input_refs_by_request_id: dict[str, ArtifactRef] = {}
        self._merge_candidate_refs: dict[str, Any] = {}
        self._slot_integrity_violation_applied = False

    @property
    def created_at(self) -> str:
        """真实 paper runtime 动态取 UTC；普通插件调用仍使用固定构造时间。"""

        if self._lifecycle_clock is not None:
            return self._lifecycle_clock()
        return self._created_at

    def plan_root(
        self,
        root_input: object,
        *,
        artifact_store: ArtifactStore,
    ) -> RootProtocolPlan:
        case = _case(root_input)
        _validate_case(case)
        requested_child_count = resolve_requested_child_count(case["split_params"])
        split_profile_id = case["split_params"].get("split_profile_id")
        if (
            split_profile_id == EXP2_CONTIGUOUS_20WAY_PROFILE_ID
            and self.protocol_config.max_children_per_unit < requested_child_count
        ):
            self.protocol_config = replace(
                self.protocol_config,
                max_children_per_unit=requested_child_count,
            )
        self._artifact_store = artifact_store
        self._case = case
        self._split_plan = None
        self._ranges_by_unit_id = {}
        self._range_input_refs_by_request_id = {}
        self._merge_candidate_refs = {}
        self._slot_integrity_violation_applied = False
        case_id = str(case["case_id"])
        task_id = f"paper_factorization_{case_id}"
        root = RootInput(
            target_n=str(case["target_n"]),
            requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
            case_label=case_id,
            schema_version=ROOT_INPUT_SCHEMA_VERSION,
        )
        plugin_registry = PluginRegistry()
        plugin_registry.register(self.descriptor)
        executor_registry = ExecutorRegistry()
        executor_registry.register(self.deterministic_executor_descriptor)
        executor_registry.register(self.executor_descriptor)
        return RootProtocolPlan(
            registration_request=RootTaskRegistrationRequest(
                task_id=task_id,
                root_unit_id=f"paper_factor_root_{case_id}",
                root_artifact_id=f"paper_root_input_{case_id}",
                description=f"factorization paper case {case_id}",
                plugin_id=PLUGIN_ID,
                plugin_version=PLUGIN_VERSION,
                split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
                split_strategy_params=dict(case["split_params"]),
                root_input_bytes=_json_bytes(root.to_dict()),
                root_input_media_type="application/json",
                root_input_schema_id="factorization.root_input",
                root_input_schema_version="v1",
                protocol_config=self.protocol_config,
                required_capabilities={
                    "executor": "deterministic_local",
                    "factorization": True,
                },
                plugin_payload={
                    "case_id": case_id,
                    "target_n": str(case["target_n"]),
                    "requested_output": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
                },
                metadata={"paper_factorization": True, "case_id": case_id},
                created_at=self.created_at,
                root_budget=float(case.get("root_budget", 10)),
            ),
            plugin_registry=plugin_registry,
            executor_registry=executor_registry,
            registry_snapshot_id=f"paper_registry_snapshot_{case_id}",
            clients=(
                ClientRecord(
                    client_id=f"worker_factorization_deterministic_{case_id}",
                    executor_type=self.deterministic_executor_descriptor.executor_type,
                    executor_id=self.deterministic_executor_descriptor.executor_id,
                    executor_version=(
                        self.deterministic_executor_descriptor.executor_version
                    ),
                    capabilities={
                        "executor": ["deterministic_local", "local"],
                        "factorization": True,
                        "merge": True,
                    },
                    status="active",
                    stats={},
                    metadata={"runtime": "factorization", "execution": "deterministic"},
                    registered_at=self.created_at,
                ),
                ClientRecord(
                    client_id=f"worker_factorization_ai_{case_id}",
                    executor_type=self.executor_descriptor.executor_type,
                    executor_id=self.executor_descriptor.executor_id,
                    executor_version=self.executor_descriptor.executor_version,
                    capabilities={
                        "executor": ["mock_ai", "ai_api"],
                        "factorization": True,
                        "bounded_factor_search": True,
                    },
                    status="active",
                    stats={},
                    metadata={"runtime": "factorization", "execution": "ai_api"},
                    registered_at=self.created_at,
                ),
            ),
            canonical_action_builder=self._canonical_action,
        )

    def plan_units(
        self,
        root_input: object,
        *,
        artifact_store: ArtifactStore,
    ) -> tuple[TaskUnit, ...]:
        case = _case(root_input)
        split_plan = _preflight_split_plan(
            case,
            artifact_store=artifact_store,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            max_children_per_unit=self.protocol_config.max_children_per_unit,
            created_at=self.created_at,
        )
        ranges_by_key = {
            f"range:{item.coverage_id}:{item.child_index}": item
            for item in split_plan.partition.ranges
        }
        self._artifact_store = artifact_store
        self._case = case
        self._split_plan = split_plan
        self._ranges_by_unit_id = {
            split_plan.child_unit_ids_by_logical_key[key]: range_input
            for key, range_input in ranges_by_key.items()
        }
        units: list[TaskUnit] = []
        for child_spec in split_plan.proposal.child_specs:
            child_key = str(child_spec["child_logical_key"])
            units.append(
                TaskUnit(
                    unit_id=split_plan.child_unit_ids_by_logical_key[child_key],
                    task_id=f"paper_factorization_{case['case_id']}",
                    parent_unit_id=f"paper_factor_root_{case['case_id']}",
                    depth=1,
                    unit_type=str(child_spec["unit_type"]),
                    state=TaskState.READY,
                    input_refs={},
                    canonical_output_refs={},
                    required_capabilities=dict(child_spec["required_capabilities"]),
                    weight=float(child_spec["weight"]),
                    budget_limit=child_spec.get("budget_limit"),
                    deadline=child_spec.get("deadline"),
                    plugin_payload=dict(child_spec["plugin_payload"]),
                    metadata={
                        "child_logical_key": child_key,
                        "source_proposal_id": split_plan.proposal.proposal_header[
                            "proposal_id"
                        ],
                        "source_expansion_decision_id": (
                            split_plan.merge_plan.merge_plan_header[
                                "expansion_decision_id"
                            ]
                        ),
                        "canonical_selection_id": split_plan.proposal.proposal_header[
                            "canonical_selection_id"
                        ],
                        "required_outputs": list(child_spec["required_outputs"]),
                        "output_contract_refs": dict(
                            child_spec["output_contract_refs"]
                        ),
                        "validator_policy_id": child_spec["validator_policy_id"],
                    },
                    created_at=self.created_at,
                    updated_at=self.created_at,
                )
            )
        return tuple(units)

    def build_execution_request(
        self,
        unit: TaskUnit,
        *,
        attempt,
        lease,
    ) -> ExecutionRequest:
        store = self._require_store()
        case = self._require_case()
        request_id = f"request_{_safe(attempt.attempt_id)}"
        is_range = unit.unit_type == FACTOR_SEARCH_RANGE_TASK_TYPE
        executor_descriptor = (
            self.executor_descriptor
            if is_range
            else self.deterministic_executor_descriptor
        )
        input_refs = dict(unit.input_refs)
        instruction_ref = None
        prompt_ref = None
        if is_range:
            range_input = self._ranges_by_unit_id[unit.unit_id]
            range_ref = store.save_json(
                range_input.to_dict(),
                artifact_id=f"range_input_{_safe(unit.unit_id)}",
                artifact_type="FactorSearchRangeInput",
                artifact_schema_id="factorization.factor_search_range_input",
                artifact_schema_version="v1",
                source={"kind": "factorization_runtime", "task_id": unit.task_id},
                metadata={"output_name": "range_input"},
                created_at=self.created_at,
            )
            input_refs = {"range_input": range_ref}
            self._range_input_refs_by_request_id[request_id] = range_ref
            instruction = build_factor_search_instruction(
                request_id=request_id,
                unit_id=unit.unit_id,
                range_input=range_input,
            )
            instruction_ref = store.save_json(
                instruction.to_dict(),
                artifact_id=f"instruction_{_safe(request_id)}",
                artifact_type="ExecutionInstruction",
                artifact_schema_id="factorization.factor_search_instruction",
                artifact_schema_version="v1",
                source={"kind": "factorization_runtime", "task_id": unit.task_id},
                metadata={"output_name": "execution_instruction"},
                created_at=self.created_at,
            )
            prompt = build_factor_search_prompt_package(
                request_id=request_id,
                task_id=unit.task_id,
                unit_id=unit.unit_id,
                range_input=range_input,
                instruction=instruction,
                created_at=self.created_at,
                seed=self.seed,
            )
            prompt_ref = store.save_json(
                prompt.to_dict(),
                artifact_id=f"prompt_{_safe(request_id)}",
                artifact_type="PromptPackage",
                artifact_schema_id="phase3.prompt_package",
                artifact_schema_version="v1",
                source={"kind": "factorization_runtime", "task_id": unit.task_id},
                metadata={"output_name": "prompt_package"},
                created_at=self.created_at,
            )
        return ExecutionRequest(
            request_id=request_id,
            task_id=unit.task_id,
            unit_id=unit.unit_id,
            attempt_id=attempt.attempt_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            plugin=self.descriptor.to_dict(),
            executor=executor_descriptor.to_dict(),
            registry_snapshot_id=f"paper_registry_snapshot_{case['case_id']}",
            allocation_decision={"client_id": attempt.client_id},
            capability_snapshot=(
                {
                    **dict(unit.required_capabilities),
                    **self.executor_requirements,
                    "status": "Available",
                }
                if is_range
                else {
                    **dict(unit.required_capabilities),
                    "executor": "deterministic_local",
                    "status": "Available",
                }
            ),
            task_unit_snapshot=unit.to_dict(),
            input_artifact_refs=input_refs,
            output_contract=self._output_contract(unit),
            hard_requirements=(
                dict(self.executor_requirements)
                if is_range
                else {"executor": "deterministic_local"}
            ),
            soft_hints={
                "temperature": 0.0,
                "lease_deadline_at": lease.expires_at,
                "planned_ai_unit_id": (
                    f"range_{self._ranges_by_unit_id[unit.unit_id].child_index}"
                    if is_range
                    else None
                ),
            },
            environment_ref=self.environment_ref(),
            execution_instruction_ref=instruction_ref,
            prompt_package_ref=prompt_ref,
            limits={
                "timeout_seconds": self.timeout_seconds,
                "max_tokens": self.max_tokens,
            },
            created_at=attempt.started_at or attempt.created_at,
        )

    def verify_submission(
        self,
        submission: ExecutionSubmission,
        *,
        unit: TaskUnit,
    ):
        store = self._require_store()
        candidate_refs = dict(submission.candidate_output_refs)
        verification_input_refs: dict[str, ArtifactRef] = {}
        if unit.parent_unit_id is None:
            ref = candidate_refs[FACTOR_INTEGER_SUBJECT_OUTPUT_NAME]
            subject = FactorIntegerSubject(**_read_json(store, ref))
            root_ref = unit.input_refs["root_input"]
            domain = verify_factor_integer_subject(
                subject,
                root_input_ref=root_ref,
                root_input_body=_read_json(store, root_ref),
            )
            required_names = [FACTOR_INTEGER_SUBJECT_OUTPUT_NAME]
            contract_id = FACTOR_INTEGER_SUBJECT_CONTRACT_ID
            validator_id = "factorization.factor_integer_subject.validator.v1"
        elif unit.unit_type == FACTOR_SEARCH_RANGE_TASK_TYPE:
            ref = candidate_refs["range_result"]
            range_input_ref = self._range_input_refs_by_request_id.get(
                submission.request_id
            )
            if range_input_ref is None:
                raise ValueError(
                    "factorization range verification requires its execution request input"
                )
            range_input = FactorSearchRangeInput(**_read_json(store, range_input_ref))
            verification_input_refs = {"range_input": range_input_ref}
            domain = verify_range_result(
                _read_json(store, ref),
                child_input=range_input,
                no_factor_recheck_max_divisors=(
                    int(range_input.range_end) - int(range_input.range_start) + 1
                ),
            )
            required_names = ["range_result"]
            contract_id = RANGE_RESULT_CONTRACT_ID
            validator_id = RANGE_RESULT_VALIDATOR_POLICY_ID
        else:
            domain = None
            required_names = list(candidate_refs)
            contract_id = FACTORIZATION_MERGE_RESULT_CONTRACT_ID
            validator_id = "factorization.merge_result.validator.v1"
        status = "passed" if domain is None or domain.status == "passed" else "rejected"
        return build_verification_report(
            verification_report_id=f"verification_{_safe(submission.attempt_id)}",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            submission_id=submission.submission_id,
            submission_event_seq=1,
            candidate_output_refs=candidate_refs,
            required_output_names=required_names,
            output_contract_id=contract_id,
            validator_policy_id=validator_id,
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            status=status,
            expected_artifact_hashes={
                name: ref.content_hash for name, ref in candidate_refs.items()
            },
            required_evidence_ref_ids=[],
            available_evidence_ref_ids=[],
            plugin_domain_status=status,
            audit_status="passed",
            verification_environment={"runtime": "factorization_runtime"},
            verifier={
                "verifier_id": validator_id,
                "verifier_version": "v1",
            },
            started_at=submission.submitted_at,
            completed_at=submission.submitted_at,
            metadata=(
                {
                    "plugin_domain_layer": domain.to_phase4_layer_summary(),
                    "verification_input_refs": {
                        name: ref.to_dict()
                        for name, ref in verification_input_refs.items()
                    },
                }
                if domain is not None
                else {}
            ),
        )

    def build_merge(
        self,
        *,
        parent: TaskUnit,
        canonical_children: tuple[TaskUnit, ...],
        slot_integrity_enabled: bool = True,
    ) -> MergeAction:
        store = self._require_store()
        split_plan = self._require_split_plan()
        children = {item.unit_id: item for item in canonical_children}
        selected_slots = [
            slot
            for slot in split_plan.merge_plan.required_slots
            if str(slot["source_child_unit_id"]) in children
        ]
        source_children = [
            children[str(slot["source_child_unit_id"])]
            for slot in selected_slots
        ]
        if (
            not slot_integrity_enabled
            and len(split_plan.merge_plan.required_slots) > 1
        ):
            if len(source_children) > 1:
                source_children = source_children[1:] + source_children[:1]
            else:
                selected_slot_key = str(selected_slots[0]["slot_key"])
                selected_slots = [
                    next(
                        slot
                        for slot in split_plan.merge_plan.required_slots
                        if str(slot["slot_key"]) != selected_slot_key
                    )
                ]
            self._slot_integrity_violation_applied = True
        slots: list[RangeSlotMergeInput] = []
        for slot, child in zip(
            selected_slots,
            source_children,
            strict=True,
        ):
            ref = child.canonical_output_refs["range_result"]
            slots.append(
                RangeSlotMergeInput(
                    slot_key=str(slot["slot_key"]),
                    range_result=RangeResult(**_read_json(store, ref)),
                    canonical_output_digest=ref.content_hash,
                )
            )
        merge_unit_id = f"merge_unit:{split_plan.merge_plan.merge_plan_header['merge_plan_id']}"
        merged = merge_required_range_results(
            merge_plan=split_plan.merge_plan,
            slot_results=slots,
            merge_unit_id=merge_unit_id,
            created_at=self.created_at,
            slot_integrity_enabled=slot_integrity_enabled,
        )
        merge_ref = store.save_json(
            merged.merge_result.to_dict(),
            artifact_id=f"merge_result_{_safe(merge_unit_id)}",
            artifact_type="canonical_output",
            artifact_schema_id="factorization.merge_result",
            artifact_schema_version="v2",
            source={"kind": "factorization_runtime", "task_id": parent.task_id},
            metadata={"output_name": "factorization_result"},
            created_at=self.created_at,
        )
        refs = {"factorization_result": merge_ref}
        if merged.prime_factorization_result is not None:
            prime_ref = self.save_prime_factorization_result(
                merged.prime_factorization_result
            )
            refs[REQUESTED_OUTPUT_PRIME_FACTORIZATION] = prime_ref
        if not merged.expected_output_resolvable:
            raise ValueError("factorization merge did not resolve the parent output")
        self._merge_candidate_refs = refs
        return MergeAction(resolution_builder=self._merge_resolution)

    def planned_ai_unit_id(self, unit: TaskUnit) -> str | None:
        range_input = self._ranges_by_unit_id.get(unit.unit_id)
        if range_input is None:
            return None
        return f"range_{range_input.child_index}"

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
        verified_results = self._verified_canonical_range_results(context)
        witnesses = [
            (unit_id, result)
            for unit_id, result in verified_results.items()
            if result.result_kind == "found_factor"
        ]
        if witnesses:
            witness_unit_id, _result = min(
                witnesses,
                key=lambda item: (
                    int(item[1].found_factor or "0"),
                    item[0],
                ),
            )
            return MergeReadinessDecision(
                status="ready",
                reason="verified_factor_witness_canonical",
                policy_id=FACTOR_WITNESS_OR_ALL_RANGES_MERGE_POLICY_ID,
                policy_version="v2",
                required_child_unit_ids=required,
                selected_child_unit_ids=(witness_unit_id,),
            )
        if (
            len(verified_results) == len(required)
            and all(
                result.result_kind == "no_factor_in_range"
                for result in verified_results.values()
            )
        ):
            return MergeReadinessDecision(
                status="ready",
                reason="all_required_ranges_no_factor_canonical",
                policy_id=FACTOR_WITNESS_OR_ALL_RANGES_MERGE_POLICY_ID,
                policy_version="v2",
                required_child_unit_ids=required,
                selected_child_unit_ids=required,
            )
        required_set = set(required)
        if any(
            child.state == TaskState.FAILED
            for child in context.children
            if child.unit_id in required_set
        ):
            return MergeReadinessDecision(
                status="failed",
                reason="terminal_child_failure_without_factor_witness",
                policy_id=FACTOR_WITNESS_OR_ALL_RANGES_MERGE_POLICY_ID,
                policy_version="v2",
                required_child_unit_ids=required,
            )
        return MergeReadinessDecision(
            status="wait",
            reason="factor_witness_or_complete_coverage_not_ready",
            policy_id=FACTOR_WITNESS_OR_ALL_RANGES_MERGE_POLICY_ID,
            policy_version="v2",
            required_child_unit_ids=required,
        )

    def _verified_canonical_range_results(
        self,
        context: MergeReadinessContext,
    ) -> dict[str, RangeResult]:
        store = self._require_store()
        canonical_by_unit_id = {}
        for event in context.canonical_events:
            selection = event.payload.get("canonical_selection")
            if isinstance(selection, dict) and isinstance(
                selection.get("unit_id"),
                str,
            ):
                canonical_by_unit_id[str(selection["unit_id"])] = selection
        verification_by_id = {
            event.object_id: event.payload.get("verification_report")
            for event in context.verification_events
        }
        results: dict[str, RangeResult] = {}
        for child in context.children:
            if child.state != TaskState.COMPLETED:
                continue
            selection = canonical_by_unit_id.get(child.unit_id)
            if not isinstance(selection, dict):
                continue
            report = verification_by_id.get(
                str(selection.get("selected_verification_report_id"))
            )
            if (
                not isinstance(report, dict)
                or report.get("status") not in {"passed", "accepted"}
                or report.get("eligible_for_canonical") is not True
                or report.get("unit_id") != child.unit_id
            ):
                continue
            ref = child.canonical_output_refs.get("range_result")
            range_input = self._ranges_by_unit_id.get(child.unit_id)
            if ref is None or range_input is None:
                continue
            try:
                result = RangeResult(**_read_json(store, ref))
            except (KeyError, TypeError, ValueError):
                continue
            metadata = report.get("metadata")
            no_verification_ablation = (
                isinstance(metadata, dict)
                and metadata.get("domain_verifier_invoked") is False
            )
            if (
                not no_verification_ablation
                and not verify_range_result(
                    result,
                    child_input=range_input,
                    no_factor_recheck_max_divisors=(
                        int(range_input.range_end)
                        - int(range_input.range_start)
                        + 1
                    ),
                ).accepted
            ):
                continue
            results[child.unit_id] = result
        return results

    def save_prime_factorization_result(self, result: PrimeFactorizationResult):
        case = self._require_case()
        return self._require_store().save_json(
            result.to_dict(),
            artifact_id=f"prime_factorization_{_safe(str(case['case_id']))}",
            artifact_type="canonical_output",
            artifact_schema_id="factorization.prime_factorization_result",
            artifact_schema_version="v1",
            source={"kind": "factorization_runtime", "case_id": case["case_id"]},
            metadata={
                "case_id": case["case_id"],
                "output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
            },
            created_at=self.created_at,
        )

    def environment_ref(self) -> EnvironmentRef:
        return EnvironmentRef(
            environment_id="env_factorization_runtime",
            environment_digest="sha256:env_factorization_runtime",
            runtime="python",
            tool_versions={"factorization_plugin": PLUGIN_VERSION},
            resource_limits={"timeout_seconds": self.timeout_seconds},
            fixture_profile_digest="sha256:factorization_runtime",
            seed=self.seed,
            clock_policy=self.clock_policy,
            created_at=self.created_at,
        )

    @property
    def merge_candidate_refs(self) -> dict[str, Any]:
        return dict(self._merge_candidate_refs)

    @property
    def slot_integrity_violation_applied(self) -> bool:
        return self._slot_integrity_violation_applied

    @property
    def planned_split_plan(self) -> FactorizationSplitPlanResult:
        return self._require_split_plan()

    def range_input_for_unit(self, unit_id: str) -> FactorSearchRangeInput:
        try:
            return self._ranges_by_unit_id[unit_id]
        except KeyError as exc:
            raise ValueError(f"unknown factorization range unit: {unit_id}") from exc

    def normalize_range_submission(
        self,
        submission: ExecutionSubmission,
    ) -> ExecutionSubmission:
        """把 parser candidate 持久化为可供协议 canonical/merge 使用的输出。"""

        candidate = submission.candidate_output_refs.get("range_result")
        if candidate is None or candidate.artifact_type == "canonical_output":
            return submission
        body = _read_json(self._require_store(), candidate)
        canonical_ref = self._require_store().save_json(
            body,
            artifact_id=f"canonical_range_{_safe(submission.submission_id)}",
            artifact_type="canonical_output",
            artifact_schema_id="factorization.range_result",
            artifact_schema_version="v1",
            source={
                "kind": "factorization_runtime",
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
            metadata={"output_name": "range_result"},
            created_at=submission.submitted_at,
        )
        return replace(
            submission,
            candidate_output_refs={"range_result": canonical_ref},
        )

    def build_subject(self, request: ExecutionRequest):
        case = self._require_case()
        root_ref = request.input_artifact_refs["root_input"]
        subject = FactorIntegerSubject(
            subject_id=f"paper_factor_subject_{case['case_id']}",
            task_id=request.task_id,
            unit_id=request.unit_id,
            target_n=str(case["target_n"]),
            source_kind="root_input",
            source_ref=root_ref.to_dict(),
            requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
            created_at=self.created_at,
        )
        ref = self._require_store().save_json(
            subject.to_dict(),
            artifact_id=subject.subject_id,
            artifact_type="canonical_output",
            artifact_schema_id="factorization.factor_integer_subject",
            artifact_schema_version="v1",
            source={"kind": "factorization_runtime", "task_id": request.task_id},
            metadata={"output_name": FACTOR_INTEGER_SUBJECT_OUTPUT_NAME},
            created_at=self.created_at,
        )
        return subject, ref

    def _canonical_action(self, context: CanonicalUnitContext):
        if context.unit.parent_unit_id is not None:
            return self._complete_child_action(context)
        store = self._require_store()
        subject_ref = context.canonical_selection.canonical_output_refs[
            FACTOR_INTEGER_SUBJECT_OUTPUT_NAME
        ]
        subject = FactorIntegerSubject(**_read_json(store, subject_ref))
        case = self._require_case()
        requested = resolve_requested_child_count(case["split_params"])
        split_action = _build_split_action(
            subject=subject,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            requested_child_count=requested,
            max_children_per_unit=self.protocol_config.max_children_per_unit,
            created_at=self.created_at,
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        scope = _scope(subject)
        decision_id = f"expansion_decision:{scope.removeprefix('sha256:')}"
        params_digest = (
            split_action.split_plan.partition.params.params_digest
            if split_action.split_plan is not None
            else canonical_json_digest(
                {
                    "strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
                    "target_n": subject.target_n,
                    "action_policy": "direct_complete_or_candidate_range_partition",
                }
            )
        )
        invocation = SplitStrategyInvocation(
            invocation_id=f"split_invocation:{scope}:attempt:1",
            invocation_attempt_no=1,
            expansion_scope_hash=scope,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            status="succeeded",
            result_action=split_action.split_strategy_result.action,
            result_digest=canonical_json_digest(
                split_action.split_strategy_result.to_dict()
            ),
            started_at=self.created_at,
            completed_at=self.created_at,
        )
        if split_action.split_plan is None:
            prime = split_action.prime_factorization_result
            if prime is None:
                raise ValueError("direct factorization completion requires result")
            prime_ref = self.save_prime_factorization_result(prime)
            return CompleteAction(
                invocation=invocation,
                decision=self._complete_decision(
                    context=context,
                    invocation=invocation,
                    scope=scope,
                    params_digest=params_digest,
                    completed_refs={REQUESTED_OUTPUT_PRIME_FACTORIZATION: prime_ref},
                    completion_kind="direct_small_prime",
                ),
            )
        self._split_plan = split_action.split_plan
        self._ranges_by_unit_id = {
            split_action.split_plan.child_unit_ids_by_logical_key[
                f"range:{item.coverage_id}:{item.child_index}"
            ]: item
            for item in split_action.split_plan.partition.ranges
        }
        split = split_action.split_plan
        decision = ExpansionDecision(
            expansion_decision_id=decision_id,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            expansion_scope_hash=scope,
            action="expand",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            source_invocation_id=invocation.invocation_id,
            proposal_id=split.proposal.proposal_header["proposal_id"],
            proposal_digest=split.proposal.proposal_header["proposal_digest"],
            merge_plan_id=split.merge_plan.merge_plan_header["merge_plan_id"],
            merge_plan_digest=split.merge_plan.merge_plan_header["merge_plan_digest"],
            action_body={
                "expand_evidence": {
                    "proposal_id": split.proposal.proposal_header["proposal_id"],
                    "proposal_digest": split.proposal.proposal_header[
                        "proposal_digest"
                    ],
                    "merge_plan_id": split.merge_plan.merge_plan_header[
                        "merge_plan_id"
                    ],
                    "merge_plan_digest": split.merge_plan.merge_plan_header[
                        "merge_plan_digest"
                    ],
                    "child_count": len(split.proposal.child_specs),
                    "relation_count": len(split.proposal.dependency_edges),
                    "expected_output_count": len(split.proposal.expected_outputs),
                    "required_merge_slot_count": len(split.merge_plan.required_slots),
                }
            },
            decided_at=self.created_at,
        )
        return ExpandAction(
            invocation=invocation,
            decision=decision,
            proposal=split.proposal,
            merge_plan=split.merge_plan,
        )

    def _complete_child_action(self, context: CanonicalUnitContext) -> CompleteAction:
        summary = dict(context.unit.plugin_payload.get("summary", {}))
        scope = canonical_json_digest(
            {"task_id": context.unit.task_id, "unit_id": context.unit.unit_id}
        )
        params_digest = str(summary.get("partition_params_digest", scope))
        invocation = SplitStrategyInvocation(
            invocation_id=f"split_invocation:{scope}:attempt:1",
            invocation_attempt_no=1,
            expansion_scope_hash=scope,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            status="succeeded",
            result_action="complete",
            result_digest=digest_json(context.unit.canonical_output_refs),
            started_at=self.created_at,
            completed_at=self.created_at,
        )
        return CompleteAction(
            invocation=invocation,
            decision=self._complete_decision(
                context=context,
                invocation=invocation,
                scope=scope,
                params_digest=params_digest,
                completed_refs=context.canonical_selection.canonical_output_refs,
                completion_kind="verified_range_result",
            ),
        )

    def _complete_decision(
        self,
        *,
        context,
        invocation,
        scope,
        params_digest,
        completed_refs,
        completion_kind,
    ) -> ExpansionDecision:
        return ExpansionDecision(
            expansion_decision_id=f"expansion_decision:{scope.removeprefix('sha256:')}",
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            expansion_scope_hash=scope,
            action="complete",
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            source_invocation_id=invocation.invocation_id,
            action_body={
                "completion_evidence": {
                    "completion_kind": completion_kind,
                    "validator_policy_id": RANGE_RESULT_VALIDATOR_POLICY_ID,
                    "verification_report_id": (
                        context.canonical_selection.selected_verification_report_id
                    ),
                    "canonical_selection_id": (
                        context.canonical_selection.canonical_selection_id
                    ),
                    "canonical_output_bundle_digest": (
                        context.canonical_selection.canonical_output_bundle_digest
                    ),
                    "completed_output_refs": {
                        name: ref.to_dict() for name, ref in completed_refs.items()
                    },
                    "plugin_completion_summary": completion_kind,
                }
            },
            decided_at=self.created_at,
        )

    def _merge_resolution(
        self,
        context: MergeExecutionContext,
    ) -> MergeResolutionAction:
        canonical = context.canonical_selection
        link = context.merge_task_link
        resolved_at = self.created_at
        record = MergeRecord(
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
            created_at=resolved_at,
        )
        expected = context.expected_output_refs[0]
        output_ref = canonical.canonical_output_refs[
            REQUESTED_OUTPUT_PRIME_FACTORIZATION
        ]
        resolution = ExpectedOutputResolution(
            expected_output_resolution_id=(
                f"expected_output_resolved:{expected.expected_output_id}:"
                f"{record.merge_record_id}"
            ),
            task_id=record.task_id,
            owner_unit_id=record.parent_unit_id,
            expected_output_id=expected.expected_output_id,
            expected_output_name=expected.output_name,
            resolution_source_type="merge_record",
            merge_record_id=record.merge_record_id,
            merge_plan_id=record.merge_plan_id,
            merge_unit_id=record.merge_unit_id,
            merge_canonical_selection_id=record.canonical_selection_id,
            resolved_output_ref=output_ref.to_dict(),
            resolved_output_digest=output_ref.content_hash,
            resolved_at=resolved_at,
        )
        return MergeResolutionAction(
            merge_record=record,
            expected_output_resolutions=(resolution,),
        )

    def _output_contract(self, unit: TaskUnit) -> OutputContract:
        if unit.parent_unit_id is None:
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
        if unit.unit_type == FACTOR_SEARCH_RANGE_TASK_TYPE:
            return OutputContract(
                output_contract_id=RANGE_RESULT_CONTRACT_ID,
                required_outputs=["range_result"],
                output_schema_refs={
                    "range_result": schema_ref(RANGE_RESULT_SCHEMA_VERSION)
                },
                raw_output_policy={"allowed": True, "media_type": "application/json"},
                parsed_output_schema_ref=schema_ref(RANGE_RESULT_SCHEMA_VERSION),
            )
        return OutputContract(
            output_contract_id=FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
            required_outputs=list(self._merge_candidate_refs),
            output_schema_refs={
                "factorization_result": schema_ref(
                    FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION
                ),
                REQUESTED_OUTPUT_PRIME_FACTORIZATION: schema_ref(
                    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION
                ),
            },
            raw_output_policy={"allowed": False, "media_type": "application/json"},
        )

    def _require_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            raise RuntimeError("plan_root must run before factorization execution")
        return self._artifact_store

    def _require_case(self) -> JsonObject:
        if self._case is None:
            raise RuntimeError("plan_root must run before factorization execution")
        return self._case

    def _require_split_plan(self) -> FactorizationSplitPlanResult:
        if self._split_plan is None:
            raise RuntimeError("factorization split plan is not available")
        return self._split_plan


class FactorizationExecutionBridge:
    """只路由执行：root/merge 确定性，range 委托给注入 executor。

    bridge 继承 adapter 的单次 run、顺序执行约束，因为后者维护可变 ref 缓存。
    """

    def __init__(self, *, plugin_runtime: FactorizationRuntimeAdapter, range_executor: Any):
        self._plugin_runtime = plugin_runtime
        self._range_executor = range_executor

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        unit_type = str(request.task_unit_snapshot["unit_type"])
        if unit_type == FACTOR_SEARCH_RANGE_TASK_TYPE:
            return self._plugin_runtime.normalize_range_submission(
                self._range_executor.execute(
                    request,
                    submission_id=submission_id,
                    submitted_at=submitted_at,
                )
            )
        if request.task_unit_snapshot["parent_unit_id"] is None:
            _subject, ref = self._plugin_runtime.build_subject(request)
            refs = {FACTOR_INTEGER_SUBJECT_OUTPUT_NAME: ref}
        else:
            refs = self._plugin_runtime.merge_candidate_refs
        parsed_ref = next(iter(refs.values()))
        descriptor = self._plugin_runtime.deterministic_executor_descriptor
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=descriptor.executor_id,
            executor_version=descriptor.executor_version,
            result_kind="succeeded",
            raw_output_ref=None,
            parsed_output_ref=parsed_ref,
            candidate_output_refs=refs,
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "factorization_runtime"},
            provenance_ref=None,
            usage_summary={"deterministic_local": True, "provider_attempt_count": 0},
            error=None,
            submitted_at=submitted_at,
        )

    def prepare_process_execution(
        self,
        request: ExecutionRequest,
        execution_index: int,
    ) -> None:
        prepare = getattr(self._range_executor, "prepare_process_execution", None)
        if callable(prepare):
            prepare(request, execution_index)

    def export_process_result(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ):
        if str(request.task_unit_snapshot["unit_type"]) != FACTOR_SEARCH_RANGE_TASK_TYPE:
            return None
        export = getattr(self._range_executor, "export_process_result", None)
        return export(request, submission) if callable(export) else None

    def ingest_process_result(self, captured) -> None:
        ingest = getattr(self._range_executor, "ingest_process_result", None)
        if callable(ingest) and captured is not None:
            ingest(captured)


def _preflight_split_plan(
    case: JsonObject,
    *,
    artifact_store: ArtifactStore,
    plugin_descriptor_digest: str,
    max_children_per_unit: int,
    created_at: str,
) -> FactorizationSplitPlanResult:
    root = RootInput(
        target_n=str(case["target_n"]),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        case_label=str(case["case_id"]),
        schema_version=ROOT_INPUT_SCHEMA_VERSION,
    )
    root_ref = artifact_store.save_bytes(
        _json_bytes(root.to_dict()),
        artifact_id=f"paper_root_input_{case['case_id']}",
        artifact_type="root_input",
        media_type="application/json",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="v1",
        source={
            "kind": "client_input",
            "task_id": f"paper_factorization_{case['case_id']}",
        },
        metadata={"paper_factorization": True, "case_id": case["case_id"]},
        created_at=created_at,
    )
    task_id = f"paper_factorization_{case['case_id']}"
    root_unit_id = f"paper_factor_root_{case['case_id']}"
    subject = FactorIntegerSubject(
        subject_id=f"paper_factor_subject_{case['case_id']}",
        task_id=task_id,
        unit_id=root_unit_id,
        target_n=str(case["target_n"]),
        source_kind="root_input",
        source_ref=root_ref.to_dict(),
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=created_at,
    )
    subject_ref = artifact_store.save_json(
        subject.to_dict(),
        artifact_id=subject.subject_id,
        artifact_type="canonical_output",
        artifact_schema_id="factorization.factor_integer_subject",
        artifact_schema_version="v1",
        source={"kind": "factorization_runtime", "task_id": task_id},
        metadata={"output_name": FACTOR_INTEGER_SUBJECT_OUTPUT_NAME},
        created_at=created_at,
    )
    requested_child_count = resolve_requested_child_count(case["split_params"])
    action = _build_split_action(
        subject=subject,
        canonical_selection_id=f"canonical_selection:{task_id}:{root_unit_id}",
        canonical_output_bundle_digest=digest_json(
            {FACTOR_INTEGER_SUBJECT_OUTPUT_NAME: subject_ref}
        ),
        plugin_descriptor_digest=plugin_descriptor_digest,
        requested_child_count=requested_child_count,
        max_children_per_unit=max_children_per_unit,
        created_at=created_at,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )
    if action.split_plan is None:
        raise ValueError("factorization runtime preflight requires an expansion plan")
    return action.split_plan


def _build_split_action(
    *,
    subject: FactorIntegerSubject,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    requested_child_count: int,
    max_children_per_unit: int,
    created_at: str,
    min_divisor: str | int,
    max_divisor: str | int,
) -> FactorizationSplitStrategyActionResult:
    """供 preflight 与协议 canonical action 共用的唯一 split builder。"""

    scope = _scope(subject)
    return build_factorization_split_strategy_result(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=scope,
        expansion_decision_id=f"expansion_decision:{scope.removeprefix('sha256:')}",
        requested_child_count=requested_child_count,
        max_children_per_unit=max_children_per_unit,
        created_at=created_at,
        min_divisor=min_divisor,
        max_divisor=max_divisor,
    )


def _case(root_input: object) -> JsonObject:
    if not isinstance(root_input, dict):
        raise TypeError("factorization runtime root_input must be an object")
    return json.loads(json.dumps(root_input, ensure_ascii=False))


def _validate_case(case: JsonObject) -> None:
    if case.get("schema_version") != "tokenshare.paper_factorization_case.v1":
        raise ValueError("factorization runtime case schema_version mismatch")
    split_params = case.get("split_params")
    if not isinstance(split_params, dict) or split_params.get("strategy_id") != (
        CANDIDATE_RANGE_PARTITION_STRATEGY_ID
    ):
        raise ValueError("factorization runtime requires candidate range partition")
    if str(case.get("candidate_start")) != "2":
        raise ValueError("factorization runtime requires candidate_start=2")
    if int(str(case.get("candidate_end"))) != isqrt(int(str(case["target_n"]))):
        raise ValueError("factorization runtime requires the complete candidate domain")


def _read_json(store: ArtifactStore, ref) -> JsonObject:
    if not store.verify(ref):
        raise ValueError("factorization runtime artifact integrity check failed")
    body = json.loads(store.read_bytes(ref).decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("factorization runtime artifact must be a JSON object")
    return body


def _scope(subject: FactorIntegerSubject) -> str:
    return canonical_json_digest(
        {
            "task_id": subject.task_id,
            "unit_id": subject.unit_id,
            "target_n": subject.target_n,
        }
    )


def _safe(value: str) -> str:
    readable = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in value
    )
    if len(readable) <= 64:
        return readable
    return f"{readable[:48]}_{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _json_bytes(body: JsonObject) -> bytes:
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
