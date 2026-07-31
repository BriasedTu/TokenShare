"""Lean proof 插件到本地协议 coordinator 的领域桥接。"""

from __future__ import annotations

import json
from dataclasses import replace
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
from tokenshare.plugins.lean_proof.checker import (
    LeanChecker,
    LeanCheckerMode,
    LeanCheckerReport,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.child_proof import (
    LeanChildProofResult,
    check_lean_child_proof,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixed_plan import (
    LeanFixedDecompositionPlan,
    build_fixed_plan_certificate,
)
from tokenshare.plugins.lean_proof.merge_policy import (
    LeanLemmaGraphMergeResult,
    LeanLemmaGraphProofInput,
    LeanProofMergeInput,
    LeanProofMergeResult,
    merge_lean_child_proofs,
    merge_lean_lemma_graph_proofs,
)
from tokenshare.plugins.lean_proof.models import (
    LeanLemmaGraphCertificate,
    LeanSplitCertificate,
    LeanTheoremPayload,
    canonical_json_digest,
)
from tokenshare.plugins.lean_proof.prompt_builder import (
    PROOF_CANDIDATE_OUTPUT_NAME,
    build_lean_proof_candidate_prompt_package,
)
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_MERGE_RESULT_SCHEMA_VERSION,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
    LEAN_PROOF_SUBGOAL_TASK_TYPE,
    LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
    MERGE_RESULT_CONTRACT_ID,
    MERGE_RESULT_OUTPUT_NAME,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
    THEOREM_PAYLOAD_CONTRACT_ID,
    THEOREM_PAYLOAD_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
    schema_ref,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperReport,
    LeanSplitHelperRequest,
    LeanSplitHelperStatus,
    LeanSplitPlanResult,
    build_lean_split_plan,
    run_lean_split_helper,
)
from tokenshare.plugins.lean_proof.validator import verify_lean_checker_report
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-22T00:00:00Z"
DETERMINISTIC_EXECUTOR_ID = "executor_lean_runtime"
DETERMINISTIC_EXECUTOR_VERSION = "0.1.0"
LEAN_V2_SCHEMA_VERSION = "tokenshare.paper_lean_lemma_graph_case.v1"


class LeanRuntimeSplitBlocked(ValueError):
    """插件拆分没有产生可执行 certificate，供兼容投影返回 blocked。"""

    def __init__(self, split_report: LeanSplitHelperReport) -> None:
        super().__init__("Lean runtime requires a supported plugin split certificate")
        self.split_report = split_report


class LeanRuntimeAdapter:
    """把固定 Lean 拆分、checker 与 merge 投影为 coordinator 行为。

    实例缓存 request/checker/merge 事实，只支持单次 run 的顺序执行。
    """

    def __init__(
        self,
        *,
        provider_family: str,
        environment_manifest: LeanEnvironmentManifest,
        checker: LeanChecker = check_lean_proof,
        seed: int | None = None,
        protocol_config: ProtocolConfig | None = None,
        executor_requirements: JsonObject | None = None,
        simple_split_report: LeanSplitHelperReport | None = None,
        max_tokens: int = 1024,
        timeout_seconds: int = 30,
        created_at: str = NOW,
        lifecycle_clock: Callable[[], str] | None = None,
    ) -> None:
        self.provider_family = provider_family
        self.environment_manifest = environment_manifest
        self.checker = checker
        self.seed = seed
        self.protocol_config = protocol_config or ProtocolConfig.default(
            config_id="lean_runtime_preflight",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/lean_runtime.jsonl",
        )
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self._created_at = created_at
        self._lifecycle_clock = lifecycle_clock
        self.simple_split_report = simple_split_report
        self.descriptor = build_lean_proof_plugin_descriptor()
        supplied = dict(executor_requirements or {})
        for name, expected in {
            "executor": "ai_api",
            "provider_family": provider_family,
        }.items():
            if supplied.get(name, expected) != expected:
                raise ValueError(f"Lean executor requirement mismatch: {name}")
        self.executor_requirements = {
            "executor": "ai_api",
            "provider_family": provider_family,
            **supplied,
        }

        base_ai_descriptor = build_ai_api_executor_descriptor(
            provider_family=provider_family
        )
        self.executor_descriptor = replace(
            base_ai_descriptor,
            capabilities={
                **dict(base_ai_descriptor.capabilities),
                **self.executor_requirements,
                "lean_proof_candidate": True,
            },
        )
        self.deterministic_executor_descriptor = ExecutorDescriptor(
            executor_id=DETERMINISTIC_EXECUTOR_ID,
            executor_type="deterministic_local",
            executor_version=DETERMINISTIC_EXECUTOR_VERSION,
            supported_request_schema_versions=["phase3.execution_request.v1"],
            capabilities={
                "executor": ["deterministic_local", "local"],
                "lean_proof": True,
                "merge": True,
            },
            environment_policy={"runtime": "lean", "network_access": False},
            status=ExecutorStatus.AVAILABLE,
            metadata={"runtime": "lean_proof"},
        )
        self._artifact_store: ArtifactStore | None = None
        self._case: JsonObject | None = None
        self._parent_payload_ref: ArtifactRef | None = None
        self._split_plan: LeanSplitPlanResult | None = None
        self._checker_reports_by_request_id: dict[str, LeanCheckerReport] = {}
        self._proof_inputs_by_logical_key: dict[
            str, LeanChildProofResult | LeanLemmaGraphProofInput
        ] = {}
        self._canonical_proof_refs_by_logical_key: dict[str, ArtifactRef] = {}
        self._merge_candidate_refs: dict[str, ArtifactRef] = {}
        self._merge_result: LeanProofMergeResult | LeanLemmaGraphMergeResult | None = None
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
        self._reset(case=case, artifact_store=artifact_store)
        case_id = str(case["case_id"])
        task_id = f"paper_lean_{case_id}"
        plugin_registry = PluginRegistry()
        plugin_registry.register(self.descriptor)
        executor_registry = ExecutorRegistry()
        executor_registry.register(self.deterministic_executor_descriptor)
        executor_registry.register(self.executor_descriptor)
        return RootProtocolPlan(
            registration_request=RootTaskRegistrationRequest(
                task_id=task_id,
                root_unit_id=f"paper_lean_root_{case_id}",
                root_artifact_id=f"paper_lean_root_input_{case_id}",
                description=f"Lean paper case {case_id}",
                plugin_id=PLUGIN_ID,
                plugin_version=PLUGIN_VERSION,
                split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
                split_strategy_params={
                    "decomposition_authority": "plugin_fixed_or_helper_certificate",
                    "case_id": case_id,
                },
                root_input_bytes=_json_bytes(case),
                root_input_media_type="application/json",
                root_input_schema_id="tokenshare.paper_lean_case",
                root_input_schema_version="v2" if _is_fixed_case(case) else "v1",
                protocol_config=self.protocol_config,
                required_capabilities={
                    "executor": "deterministic_local",
                    "lean_proof": True,
                },
                plugin_payload={
                    "case_id": case_id,
                    "requested_output": PROOF_ARTIFACT_OUTPUT_NAME,
                    "fixed_decomposition": _is_fixed_case(case),
                    "ai_may_decide_decomposition": False,
                },
                metadata={"paper_lean": True, "case_id": case_id},
                created_at=self.created_at,
                root_budget=float(case.get("root_budget", 10)),
            ),
            plugin_registry=plugin_registry,
            executor_registry=executor_registry,
            registry_snapshot_id=f"paper_lean_registry_snapshot_{case_id}",
            clients=self._clients(case_id),
            canonical_action_builder=self._canonical_action,
        )

    def plan_units(
        self,
        root_input: object,
        *,
        artifact_store: ArtifactStore,
    ) -> tuple[TaskUnit, ...]:
        case = _case(root_input)
        _validate_case(case)
        self._reset(case=case, artifact_store=artifact_store)
        case_id = str(case["case_id"])
        task_id = f"paper_lean_{case_id}"
        parent_unit_id = f"paper_lean_root_{case_id}"
        parent_ref = self._save_parent_payload(parent_unit_id=parent_unit_id)
        split_plan = self._build_split_plan(
            parent_payload_ref=parent_ref,
            task_id=task_id,
            parent_unit_id=parent_unit_id,
            canonical_selection_id=(
                f"canonical_selection:{task_id}:{parent_unit_id}"
            ),
            canonical_output_bundle_digest=digest_json(
                {THEOREM_PAYLOAD_OUTPUT_NAME: parent_ref}
            ),
        )
        self._install_split_plan(split_plan)
        units: list[TaskUnit] = []
        for child_spec in split_plan.proposal.child_specs:
            child_key = str(child_spec["child_logical_key"])
            units.append(
                TaskUnit(
                    unit_id=split_plan.child_unit_ids_by_logical_key[child_key],
                    task_id=task_id,
                    parent_unit_id=parent_unit_id,
                    depth=1,
                    unit_type=str(child_spec["unit_type"]),
                    state=TaskState.READY,
                    input_refs={
                        name: ArtifactRef.from_dict(binding["artifact_ref"])
                        for name, binding in child_spec["input_bindings"].items()
                        if binding.get("kind") == "artifact_ref"
                    },
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

    def build_execution_request(self, unit: TaskUnit, *, attempt, lease) -> ExecutionRequest:
        store = self._require_store()
        case = self._require_case()
        request_id = f"request_{_safe(attempt.attempt_id)}"
        is_proof_unit = unit.unit_type in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }
        executor = (
            self.executor_descriptor
            if is_proof_unit
            else self.deterministic_executor_descriptor
        )
        input_refs = dict(unit.input_refs)
        prompt_ref = None
        soft_hints: JsonObject = {
            "temperature": 0.0,
            "lease_deadline_at": lease.expires_at,
        }
        if is_proof_unit:
            logical_key = str(unit.metadata["child_logical_key"])
            payload_name = (
                "lemma_theorem_payload"
                if unit.unit_type == LEAN_PROOF_LEMMA_NODE_TASK_TYPE
                else "child_theorem_payload"
            )
            payload_ref = input_refs[payload_name]
            payload = LeanTheoremPayload.from_dict(_read_json(store, payload_ref))
            dependency_keys = self._dependency_sources(logical_key)
            for source_key in dependency_keys:
                try:
                    input_refs[f"dependency:{source_key}"] = (
                        self._canonical_proof_refs_by_logical_key[source_key]
                    )
                except KeyError as exc:
                    raise RuntimeError(
                        f"Lean dependency proof is not canonical: {source_key}"
                    ) from exc
            prompt = build_lean_proof_candidate_prompt_package(
                request_id=request_id,
                task_id=unit.task_id,
                unit_id=unit.unit_id,
                theorem_payload=payload,
                created_at=self.created_at,
                seed=self.seed,
            )
            prompt_ref = store.save_json(
                prompt.to_dict(),
                artifact_id=f"lean_prompt_{_safe(request_id)}",
                artifact_type="PromptPackage",
                artifact_schema_id="phase3.prompt_package",
                artifact_schema_version="v1",
                source={"kind": "lean_runtime", "task_id": unit.task_id},
                metadata={"logical_key": logical_key},
                created_at=self.created_at,
            )
            soft_hints.update(
                {
                    "planned_ai_unit_id": self._planned_ai_unit_id(logical_key),
                    "lemma_node_id": logical_key,
                    "dependency_path": self._dependency_path(logical_key),
                    "ai_may_decide_decomposition": False,
                }
            )
        return ExecutionRequest(
            request_id=request_id,
            task_id=unit.task_id,
            unit_id=unit.unit_id,
            attempt_id=attempt.attempt_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            plugin=self.descriptor.to_dict(),
            executor=executor.to_dict(),
            registry_snapshot_id=f"paper_lean_registry_snapshot_{case['case_id']}",
            allocation_decision={"client_id": attempt.client_id},
            capability_snapshot=(
                {**dict(unit.required_capabilities), **self.executor_requirements}
                if is_proof_unit
                else {**dict(unit.required_capabilities), "executor": "deterministic_local"}
            ),
            task_unit_snapshot=unit.to_dict(),
            input_artifact_refs=input_refs,
            output_contract=self._output_contract(unit),
            hard_requirements=(
                dict(self.executor_requirements)
                if is_proof_unit
                else {"executor": "deterministic_local"}
            ),
            soft_hints=soft_hints,
            environment_ref=build_lean_environment_ref(self.environment_manifest),
            execution_instruction_ref=None,
            prompt_package_ref=prompt_ref,
            limits={
                "timeout_seconds": self.timeout_seconds,
                "max_tokens": self.max_tokens,
            },
            created_at=attempt.started_at or attempt.created_at,
        )

    def normalize_proof_submission(
        self,
        submission: ExecutionSubmission,
        *,
        request: ExecutionRequest,
    ) -> ExecutionSubmission:
        """运行 checker，并且只把 checker 接受的 proof artifact 提升为候选输出。"""

        if submission.result_kind != "succeeded":
            return submission
        proof_candidate_ref = submission.candidate_output_refs.get(
            PROOF_CANDIDATE_OUTPUT_NAME
        )
        if proof_candidate_ref is None:
            return submission
        unit_type = str(request.task_unit_snapshot["unit_type"])
        logical_key = str(request.task_unit_snapshot["metadata"]["child_logical_key"])
        if unit_type == LEAN_PROOF_LEMMA_NODE_TASK_TYPE:
            result = self._check_lemma_node_candidate(
                logical_key=logical_key,
                proof_candidate_ref=proof_candidate_ref,
                request=request,
            )
            report = result.checker_report
            if report.status == LeanCheckerStatus.ACCEPTED:
                self._proof_inputs_by_logical_key[logical_key] = result
        elif unit_type == LEAN_PROOF_SUBGOAL_TASK_TYPE:
            split_plan = self._require_split_plan()
            certificate = split_plan.certificate
            if not isinstance(certificate, LeanSplitCertificate):
                raise TypeError("Lean subgoal requires a simple split certificate")
            child_payload_ref = request.input_artifact_refs["child_theorem_payload"]
            child_result = check_lean_child_proof(
                child_logical_key=logical_key,
                split_certificate=certificate,
                child_payload_ref=child_payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                artifact_store=self._require_store(),
                environment_manifest=self.environment_manifest,
                request_id=f"checker_{_safe(request.request_id)}",
                created_at=submission.submitted_at,
                checker=self.checker,
            )
            report = child_result.checker_report
            if report is None:
                return submission
            if child_result.merge_ready:
                self._proof_inputs_by_logical_key[logical_key] = child_result
        else:
            raise ValueError("normalize_proof_submission requires a Lean proof unit")
        self._checker_reports_by_request_id[submission.request_id] = report
        canonical_ref = report.proof_artifact_ref
        if report.status != LeanCheckerStatus.ACCEPTED or canonical_ref is None:
            return replace(
                submission,
                candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: proof_candidate_ref},
            )
        canonical_ref = self._require_store().save_bytes(
            self._require_store().read_bytes(canonical_ref),
            artifact_id=f"canonical_lean_proof_{_safe(submission.submission_id)}",
            artifact_type="canonical_output",
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.proof_artifact",
            artifact_schema_version="v1",
            source={
                "kind": "lean_runtime_checker_promotion",
                "checker_report_ref": (
                    report.report_ref.to_dict()
                    if report.report_ref is not None
                    else None
                ),
                "checker_proof_artifact_ref": report.proof_artifact_ref.to_dict(),
                "proof_candidate_ref": proof_candidate_ref.to_dict(),
            },
            metadata={
                "output_name": PROOF_ARTIFACT_OUTPUT_NAME,
                "checker_status": report.status.value,
                "proof_digest": report.proof_digest,
            },
            created_at=submission.submitted_at,
        )
        self._canonical_proof_refs_by_logical_key[logical_key] = canonical_ref
        return replace(
            submission,
            candidate_output_refs={PROOF_ARTIFACT_OUTPUT_NAME: canonical_ref},
        )

    def verify_submission(
        self,
        submission: ExecutionSubmission,
        *,
        unit: TaskUnit,
    ):
        if unit.parent_unit_id is None:
            return self._verification_report(
                submission=submission,
                required_output_names=[THEOREM_PAYLOAD_OUTPUT_NAME],
                output_contract_id=THEOREM_PAYLOAD_CONTRACT_ID,
                status="passed",
                layer_summary={
                    "status": "passed",
                    "reason_code": "lean_theorem_payload_bound",
                    "summary": "root theorem payload is plugin-constructed",
                    "details": {"ai_decomposition_authority": False},
                    "evidence_refs": [],
                    "checked_at": self.created_at,
                },
            )
        if unit.unit_type in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }:
            report = self._checker_reports_by_request_id.get(submission.request_id)
            if report is None:
                return self._verification_report(
                    submission=submission,
                    required_output_names=[PROOF_ARTIFACT_OUTPUT_NAME],
                    output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
                    status="rejected",
                    layer_summary={
                        "status": "rejected",
                        "reason_code": "missing_lean_checker_report",
                        "summary": "proof candidate did not produce checker evidence",
                        "details": {},
                        "evidence_refs": [],
                        "checked_at": self.created_at,
                    },
                )
            validation = verify_lean_checker_report(report)
            return self._verification_report(
                submission=submission,
                required_output_names=[PROOF_ARTIFACT_OUTPUT_NAME],
                output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
                status="passed" if validation.accepted else "rejected",
                layer_summary=validation.to_phase4_layer_summary(),
                checker_report=report,
            )
        report = self._merge_checker_report()
        validation = verify_lean_checker_report(report)
        return self._verification_report(
            submission=submission,
            required_output_names=list(self._merge_candidate_refs),
            output_contract_id=MERGE_RESULT_CONTRACT_ID,
            status="passed" if validation.accepted else "rejected",
            layer_summary=validation.to_phase4_layer_summary(),
            checker_report=report,
        )

    def build_merge(
        self,
        *,
        parent: TaskUnit,
        canonical_children: tuple[TaskUnit, ...],
        slot_integrity_enabled: bool = True,
    ) -> MergeAction:
        split_plan = self._require_split_plan()
        parent_ref = self._require_parent_payload_ref()
        expected_keys = {
            str(child.metadata["child_logical_key"])
            for child in canonical_children
        }
        if expected_keys != set(self._proof_inputs_by_logical_key):
            raise ValueError("Lean merge requires checker-accepted input for every child")
        merge_unit_id = (
            f"merge_unit:{split_plan.merge_plan.merge_plan_header['merge_plan_id']}"
        )
        request_id = f"lean_merge_checker_{_safe(parent.task_id)}"
        certificate = split_plan.certificate
        if isinstance(certificate, LeanLemmaGraphCertificate):
            required_slots = list(split_plan.merge_plan.required_slots)
            source_inputs = [
                self._proof_inputs_by_logical_key[
                    str(slot["source_child_logical_key"])
                ]
                for slot in required_slots
            ]
            if not all(
                isinstance(item, LeanLemmaGraphProofInput)
                for item in source_inputs
            ):
                raise TypeError("Lean lemma graph merge inputs are invalid")
            if not slot_integrity_enabled and len(source_inputs) > 1:
                source_inputs = source_inputs[1:] + source_inputs[:1]
                self._slot_integrity_violation_applied = True
            node_inputs = [
                replace(
                    proof,
                    node_id=str(slot["source_child_logical_key"]),
                    slot_key=str(slot["slot_key"]),
                )
                for slot, proof in zip(
                    required_slots,
                    source_inputs,
                    strict=True,
                )
            ]
            result = merge_lean_lemma_graph_proofs(
                merge_plan=split_plan.merge_plan,
                lemma_graph_certificate=certificate,
                parent_theorem_payload_ref=parent_ref,
                node_proofs=list(node_inputs),
                artifact_store=self._require_store(),
                environment_manifest=self.environment_manifest,
                merge_unit_id=merge_unit_id,
                request_id=request_id,
                created_at=self.created_at,
                checker=self.checker,
                slot_integrity_enabled=slot_integrity_enabled,
            )
        else:
            required_slots = list(split_plan.merge_plan.required_slots)
            source_proofs = [
                self._proof_inputs_by_logical_key[
                    str(slot["source_child_logical_key"])
                ]
                for slot in required_slots
            ]
            if not all(
                isinstance(proof, LeanChildProofResult)
                for proof in source_proofs
            ):
                raise TypeError("Lean simple merge inputs are invalid")
            if not slot_integrity_enabled and len(source_proofs) > 1:
                source_proofs = source_proofs[1:] + source_proofs[:1]
                self._slot_integrity_violation_applied = True
            child_inputs: list[LeanProofMergeInput] = []
            for slot, proof in zip(
                required_slots,
                source_proofs,
                strict=True,
            ):
                expected_key = str(slot["source_child_logical_key"])
                child_inputs.append(
                    LeanProofMergeInput(
                        slot_key=str(slot["slot_key"]),
                        child_proof=replace(
                            proof,
                            child_logical_key=expected_key,
                        ),
                    )
                )
            result = merge_lean_child_proofs(
                merge_plan=split_plan.merge_plan,
                split_certificate=certificate,
                parent_theorem_payload_ref=parent_ref,
                child_proofs=child_inputs,
                artifact_store=self._require_store(),
                environment_manifest=self.environment_manifest,
                merge_unit_id=merge_unit_id,
                request_id=request_id,
                created_at=self.created_at,
                checker=self.checker,
                slot_integrity_enabled=slot_integrity_enabled,
            )
        self._merge_result = result
        refs: dict[str, ArtifactRef] = {}
        if result.root_proof_artifact_ref is not None:
            refs[PROOF_ARTIFACT_OUTPUT_NAME] = self._promote_merge_output(
                result.root_proof_artifact_ref,
                output_name=PROOF_ARTIFACT_OUTPUT_NAME,
                artifact_schema_id="lean_proof.proof_artifact",
                artifact_schema_version="v1",
                media_type="text/x-lean",
            )
        else:
            refs[PROOF_ARTIFACT_OUTPUT_NAME] = (
                result.root_proof_candidate_ref
                if isinstance(result, LeanLemmaGraphMergeResult)
                else result.merge_proof_candidate_ref
            )
        if result.merge_result_ref is not None:
            refs[MERGE_RESULT_OUTPUT_NAME] = self._promote_merge_output(
                result.merge_result_ref,
                output_name=MERGE_RESULT_OUTPUT_NAME,
                artifact_schema_id="lean_proof.merge_result",
                artifact_schema_version="v1",
                media_type="application/json",
            )
        self._merge_candidate_refs = refs
        return MergeAction(resolution_builder=self._merge_resolution)

    def build_root_payload(self, request: ExecutionRequest) -> ArtifactRef:
        del request
        return self._save_parent_payload(
            parent_unit_id=f"paper_lean_root_{self._require_case()['case_id']}"
        )

    @property
    def planned_split_plan(self) -> LeanSplitPlanResult:
        return self._require_split_plan()

    @property
    def merge_candidate_refs(self) -> dict[str, ArtifactRef]:
        return dict(self._merge_candidate_refs)

    @property
    def slot_integrity_violation_applied(self) -> bool:
        return self._slot_integrity_violation_applied

    @property
    def merge_result(self) -> LeanProofMergeResult | LeanLemmaGraphMergeResult:
        if self._merge_result is None:
            raise RuntimeError("Lean merge result is not available")
        return self._merge_result

    def checker_report_for_request(self, request_id: str) -> LeanCheckerReport | None:
        return self._checker_reports_by_request_id.get(request_id)

    def proof_input_for_logical_key(
        self,
        logical_key: str,
    ) -> LeanChildProofResult | LeanLemmaGraphProofInput | None:
        return self._proof_inputs_by_logical_key.get(logical_key)

    def export_process_proof_state(
        self,
        request: ExecutionRequest,
    ) -> JsonObject:
        """导出 proof 子进程产生、父 coordinator 后续校验所需的最小状态。"""

        logical_key = str(
            request.task_unit_snapshot["metadata"]["child_logical_key"]
        )
        return {
            "request_id": request.request_id,
            "logical_key": logical_key,
            "checker_report": self._checker_reports_by_request_id.get(
                request.request_id
            ),
            "proof_input": self._proof_inputs_by_logical_key.get(logical_key),
            "canonical_proof_ref": (
                self._canonical_proof_refs_by_logical_key.get(logical_key)
            ),
        }

    def ingest_process_proof_state(self, state: JsonObject) -> None:
        """把子进程 checker/proof 结果恢复到父进程插件实例。"""

        request_id = str(state["request_id"])
        logical_key = str(state["logical_key"])
        checker_report = state.get("checker_report")
        if checker_report is not None:
            self._checker_reports_by_request_id[request_id] = checker_report
        proof_input = state.get("proof_input")
        if proof_input is not None:
            self._proof_inputs_by_logical_key[logical_key] = proof_input
        canonical_ref = state.get("canonical_proof_ref")
        if canonical_ref is not None:
            self._canonical_proof_refs_by_logical_key[logical_key] = (
                canonical_ref
            )

    def _canonical_action(self, context: CanonicalUnitContext):
        if context.unit.parent_unit_id is not None:
            return self._complete_child_action(context)
        parent_ref = context.canonical_selection.canonical_output_refs[
            THEOREM_PAYLOAD_OUTPUT_NAME
        ]
        self._parent_payload_ref = parent_ref
        split_plan = self._build_split_plan(
            parent_payload_ref=parent_ref,
            task_id=context.unit.task_id,
            parent_unit_id=context.unit.unit_id,
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
        )
        self._install_split_plan(split_plan)
        scope = str(split_plan.proposal.proposal_header["expansion_scope_hash"])
        params_digest = str(split_plan.certificate.certificate_digest)
        invocation = self._invocation(
            context=context,
            scope=scope,
            params_digest=params_digest,
            result_action="expand",
            result_digest=split_plan.proposal.proposal_header["proposal_digest"],
        )
        decision = ExpansionDecision(
            expansion_decision_id=str(
                split_plan.merge_plan.merge_plan_header["expansion_decision_id"]
            ),
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
            split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            source_invocation_id=invocation.invocation_id,
            proposal_id=split_plan.proposal.proposal_header["proposal_id"],
            proposal_digest=split_plan.proposal.proposal_header["proposal_digest"],
            merge_plan_id=split_plan.merge_plan.merge_plan_header["merge_plan_id"],
            merge_plan_digest=split_plan.merge_plan.merge_plan_header[
                "merge_plan_digest"
            ],
            action_body={
                "expand_evidence": {
                    "proposal_id": split_plan.proposal.proposal_header[
                        "proposal_id"
                    ],
                    "proposal_digest": split_plan.proposal.proposal_header[
                        "proposal_digest"
                    ],
                    "merge_plan_id": split_plan.merge_plan.merge_plan_header[
                        "merge_plan_id"
                    ],
                    "merge_plan_digest": split_plan.merge_plan.merge_plan_header[
                        "merge_plan_digest"
                    ],
                    "child_count": len(split_plan.proposal.child_specs),
                    "relation_count": len(
                        split_plan.proposal.dependency_edges
                    ),
                    "expected_output_count": len(
                        split_plan.proposal.expected_outputs
                    ),
                    "required_merge_slot_count": len(
                        split_plan.merge_plan.required_slots
                    ),
                }
            },
            decided_at=self.created_at,
        )
        return ExpandAction(
            invocation=invocation,
            decision=decision,
            proposal=split_plan.proposal,
            merge_plan=split_plan.merge_plan,
        )

    def _complete_child_action(self, context: CanonicalUnitContext) -> CompleteAction:
        scope = canonical_json_digest(
            {"task_id": context.unit.task_id, "unit_id": context.unit.unit_id}
        )
        params_digest = str(self._require_split_plan().certificate.certificate_digest)
        invocation = self._invocation(
            context=context,
            scope=scope,
            params_digest=params_digest,
            result_action="complete",
            result_digest=digest_json(context.unit.canonical_output_refs),
        )
        return CompleteAction(
            invocation=invocation,
            decision=ExpansionDecision(
                expansion_decision_id=f"expansion_decision:{scope.removeprefix('sha256:')}",
                task_id=context.unit.task_id,
                unit_id=context.unit.unit_id,
                canonical_selection_id=(
                    context.canonical_selection.canonical_selection_id
                ),
                canonical_output_bundle_digest=(
                    context.canonical_selection.canonical_output_bundle_digest
                ),
                expansion_scope_hash=scope,
                action="complete",
                plugin_id=PLUGIN_ID,
                plugin_version=PLUGIN_VERSION,
                plugin_descriptor_digest=self.descriptor.descriptor_digest,
                split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
                split_strategy_params_digest=params_digest,
                source_invocation_id=invocation.invocation_id,
                action_body={
                    "completion_evidence": {
                        "completion_kind": "checker_accepted_lean_proof",
                        "validator_policy_id": CHECKER_VALIDATOR_POLICY_ID,
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
                            name: ref.to_dict()
                            for name, ref in (
                                context.canonical_selection.canonical_output_refs.items()
                            )
                        },
                        "plugin_completion_summary": "checker_accepted_lean_proof",
                    }
                },
                decided_at=self.created_at,
            ),
        )

    def _invocation(
        self,
        *,
        context: CanonicalUnitContext,
        scope: str,
        params_digest: str,
        result_action: str,
        result_digest: str,
    ) -> SplitStrategyInvocation:
        return SplitStrategyInvocation(
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
            split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            split_strategy_params_digest=params_digest,
            status="succeeded",
            result_action=result_action,
            result_digest=result_digest,
            started_at=self.created_at,
            completed_at=self.created_at,
        )

    def _build_split_plan(
        self,
        *,
        parent_payload_ref: ArtifactRef,
        task_id: str,
        parent_unit_id: str,
        canonical_selection_id: str,
        canonical_output_bundle_digest: str,
    ) -> LeanSplitPlanResult:
        case = self._require_case()
        case_id = str(case["case_id"])
        if _is_fixed_case(case):
            fixed_plan = LeanFixedDecompositionPlan.from_catalog_case(case)
            certificate = build_fixed_plan_certificate(
                plan=fixed_plan,
                parent_theorem_payload_ref=parent_payload_ref,
                environment_manifest=self.environment_manifest,
            )
            certificate_ref = self._require_store().save_json(
                certificate.to_dict(),
                artifact_id=f"fixed_plan_certificate_{_safe(case_id)}",
                artifact_type="LeanLemmaGraphCertificate",
                artifact_schema_id="lean_proof.lemma_graph_certificate",
                artifact_schema_version="v2",
                source={"kind": "lean_fixed_plan", "case_id": case_id},
                metadata={
                    "root_node_id": certificate.root_node_id,
                    "rule_id": certificate.rule_id,
                },
                created_at=self.created_at,
            )
            split_report = LeanSplitHelperReport(
                report_id=f"lean_fixed_plan_report:{_safe(case_id)}",
                request_id=f"lean_fixed_plan_request:{_safe(case_id)}",
                status=LeanSplitHelperStatus.SUCCEEDED,
                exit_code=0,
                generated_source_ref=None,
                helper_stdout_ref=None,
                helper_stderr_ref=None,
                certificate_ref=certificate_ref,
                report_ref=None,
                certificate=certificate,
                diagnostics={
                    "source": "preregistered_fixed_plan",
                    "plan_digest": fixed_plan.plan_digest,
                },
                environment_ref=build_lean_environment_ref(
                    self.environment_manifest
                ),
                command_summary={"backend": "lean_fixed_plan_plugin"},
                duration_ms=0,
                helper_stdout_excerpt="",
                helper_stderr_excerpt="",
            )
        else:
            split_report = self.simple_split_report
            if split_report is None:
                payload = LeanTheoremPayload.from_dict(
                    _read_json(self._require_store(), parent_payload_ref)
                )
                split_report = run_lean_split_helper(
                    LeanSplitHelperRequest(
                        request_id=f"lean_split_request:{_safe(case_id)}",
                        theorem_payload_ref=parent_payload_ref,
                        environment_ref=build_lean_environment_ref(
                            self.environment_manifest
                        ),
                        timeout_seconds=int(payload.resource_limits["timeout_seconds"]),
                        max_output_bytes=int(payload.resource_limits["max_output_bytes"]),
                        created_at=self.created_at,
                    ),
                    artifact_store=self._require_store(),
                    environment_manifest=self.environment_manifest,
                )
        if split_report.certificate is None:
            raise LeanRuntimeSplitBlocked(split_report)
        if isinstance(split_report.certificate, LeanSplitCertificate) and (
            split_report.certificate.split_kind == "unsupported"
        ):
            raise LeanRuntimeSplitBlocked(split_report)
        scope = canonical_json_digest(
            {
                "task_id": task_id,
                "parent_unit_id": parent_unit_id,
                "parent_payload_digest": parent_payload_ref.content_hash,
                "certificate_digest": split_report.certificate.certificate_digest,
            }
        )
        return build_lean_split_plan(
            split_report=split_report,
            artifact_store=self._require_store(),
            task_id=task_id,
            parent_unit_id=parent_unit_id,
            canonical_selection_id=canonical_selection_id,
            canonical_output_bundle_digest=canonical_output_bundle_digest,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            expansion_scope_hash=scope,
            expansion_decision_id=(
                f"lean_expansion_decision_{scope.removeprefix('sha256:')}"
            ),
            created_at=self.created_at,
            proof_executor_requirements=self.executor_requirements,
        )

    def _check_lemma_node_candidate(
        self,
        *,
        logical_key: str,
        proof_candidate_ref: ArtifactRef,
        request: ExecutionRequest,
    ) -> LeanLemmaGraphProofInput:
        split_plan = self._require_split_plan()
        certificate = split_plan.certificate
        if not isinstance(certificate, LeanLemmaGraphCertificate):
            raise TypeError("Lean lemma node requires lemma graph certificate")
        node = certificate.lemma_nodes_by_id[logical_key]
        payload_ref = request.input_artifact_refs["lemma_theorem_payload"]
        payload = LeanTheoremPayload.from_dict(
            _read_json(self._require_store(), payload_ref)
        )
        proof_body = _read_json(self._require_store(), proof_candidate_ref)
        if proof_body.get("theorem_payload_digest") != payload.payload_digest:
            raise ValueError("proof candidate theorem payload digest mismatch")
        report = self.checker(
            LeanCheckerRequest(
                request_id=f"checker_{_safe(request.request_id)}",
                theorem_payload_ref=payload_ref,
                proof_candidate_ref=proof_candidate_ref,
                environment_ref=build_lean_environment_ref(
                    self.environment_manifest
                ),
                checker_mode=LeanCheckerMode.CHILD_PROOF,
                timeout_seconds=int(payload.resource_limits["timeout_seconds"]),
                max_output_bytes=int(payload.resource_limits["max_output_bytes"]),
                created_at=self.created_at,
            ),
            artifact_store=self._require_store(),
            environment_manifest=self.environment_manifest,
        )
        return LeanLemmaGraphProofInput(
            node_id=logical_key,
            slot_key=f"{logical_key}:{PROOF_ARTIFACT_OUTPUT_NAME}",
            node_payload_ref=payload_ref,
            proof_candidate_ref=proof_candidate_ref,
            checker_report=report,
            context_digest=str(node["context_digest"]),
            theorem_payload_digest=payload.payload_digest or "",
        )

    def _verification_report(
        self,
        *,
        submission: ExecutionSubmission,
        required_output_names: list[str],
        output_contract_id: str,
        status: str,
        layer_summary: JsonObject,
        checker_report: LeanCheckerReport | None = None,
    ):
        evidence_ids: list[str] = []
        if checker_report is not None:
            evidence_ids = [
                ref.artifact_id
                for ref in (
                    checker_report.stdout_ref,
                    checker_report.stderr_ref,
                    checker_report.generated_source_ref,
                    checker_report.report_ref,
                    checker_report.proof_artifact_ref,
                )
                if ref is not None
            ]
        return build_verification_report(
            verification_report_id=f"verification_{_safe(submission.attempt_id)}",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            submission_id=submission.submission_id,
            submission_event_seq=1,
            candidate_output_refs=dict(submission.candidate_output_refs),
            required_output_names=required_output_names,
            output_contract_id=output_contract_id,
            validator_policy_id=CHECKER_VALIDATOR_POLICY_ID,
            plugin_id=PLUGIN_ID,
            plugin_version=PLUGIN_VERSION,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            status=status,
            expected_artifact_hashes={
                name: ref.content_hash
                for name, ref in submission.candidate_output_refs.items()
            },
            required_evidence_ref_ids=evidence_ids,
            available_evidence_ref_ids=evidence_ids,
            plugin_domain_status=status,
            audit_status="passed",
            verification_environment={
                "runtime": "lean",
                "environment_digest": self.environment_manifest.environment_digest,
            },
            verifier={
                "verifier_id": CHECKER_VALIDATOR_POLICY_ID,
                "verifier_version": "v1",
            },
            started_at=submission.submitted_at,
            completed_at=submission.submitted_at,
            metadata={
                "plugin_domain_layer": layer_summary,
                "checker_report_ref": (
                    checker_report.report_ref.to_dict()
                    if checker_report is not None
                    and checker_report.report_ref is not None
                    else None
                ),
            },
        )

    def _merge_resolution(
        self,
        context: MergeExecutionContext,
    ) -> MergeResolutionAction:
        canonical = context.canonical_selection
        link = context.merge_task_link
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
            selected_verification_report_id=(
                canonical.selected_verification_report_id
            ),
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
            created_at=self.created_at,
        )
        expected = context.expected_output_refs[0]
        proof_ref = canonical.canonical_output_refs[PROOF_ARTIFACT_OUTPUT_NAME]
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
            resolved_output_ref=proof_ref.to_dict(),
            resolved_output_digest=proof_ref.content_hash,
            resolved_at=self.created_at,
        )
        return MergeResolutionAction(
            merge_record=record,
            expected_output_resolutions=(resolution,),
        )

    def _output_contract(self, unit: TaskUnit) -> OutputContract:
        if unit.parent_unit_id is None:
            return OutputContract(
                output_contract_id=THEOREM_PAYLOAD_CONTRACT_ID,
                required_outputs=[THEOREM_PAYLOAD_OUTPUT_NAME],
                output_schema_refs={
                    THEOREM_PAYLOAD_OUTPUT_NAME: schema_ref(
                        LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION
                    )
                },
                raw_output_policy={"allowed": False, "media_type": "application/json"},
            )
        if unit.unit_type in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }:
            return OutputContract(
                output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
                required_outputs=[PROOF_ARTIFACT_OUTPUT_NAME],
                output_schema_refs={
                    PROOF_ARTIFACT_OUTPUT_NAME: schema_ref(
                        LEAN_PROOF_ARTIFACT_SCHEMA_VERSION
                    )
                },
                raw_output_policy={
                    "allowed": True,
                    "authoritative": False,
                    "media_type": "text/plain",
                },
                parsed_output_schema_ref=schema_ref(
                    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION
                ),
            )
        refs = list(self._merge_candidate_refs)
        return OutputContract(
            output_contract_id=MERGE_RESULT_CONTRACT_ID,
            required_outputs=refs,
            output_schema_refs={
                PROOF_ARTIFACT_OUTPUT_NAME: schema_ref(
                    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION
                ),
                MERGE_RESULT_OUTPUT_NAME: schema_ref(
                    LEAN_MERGE_RESULT_SCHEMA_VERSION
                ),
            },
            raw_output_policy={"allowed": False, "media_type": "application/json"},
        )

    def _save_parent_payload(self, *, parent_unit_id: str) -> ArtifactRef:
        if self._parent_payload_ref is not None:
            return self._parent_payload_ref
        case = self._require_case()
        payload = (
            LeanFixedDecompositionPlan.from_catalog_case(case).parent_theorem_payload()
            if _is_fixed_case(case)
            else _simple_payload_from_case(case)
        )
        self._parent_payload_ref = self._require_store().save_json(
            payload.to_dict(),
            artifact_id=f"lean_parent_payload_{_safe(str(case['case_id']))}",
            artifact_type="canonical_output",
            artifact_schema_id="lean_proof.theorem_payload",
            artifact_schema_version="v1",
            source={"kind": "lean_runtime", "unit_id": parent_unit_id},
            metadata={
                "case_id": case["case_id"],
                "output_name": THEOREM_PAYLOAD_OUTPUT_NAME,
            },
            created_at=self.created_at,
        )
        return self._parent_payload_ref

    def _promote_merge_output(
        self,
        source_ref: ArtifactRef,
        *,
        output_name: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        media_type: str,
    ) -> ArtifactRef:
        case_id = str(self._require_case()["case_id"])
        return self._require_store().save_bytes(
            self._require_store().read_bytes(source_ref),
            artifact_id=f"canonical_lean_merge_{_safe(case_id)}_{_safe(output_name)}",
            artifact_type="canonical_output",
            media_type=media_type,
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source={
                "kind": "lean_runtime_merge_promotion",
                "source_ref": source_ref.to_dict(),
            },
            metadata={"case_id": case_id, "output_name": output_name},
            created_at=self.created_at,
        )

    def _clients(self, case_id: str) -> tuple[ClientRecord, ClientRecord]:
        return (
            ClientRecord(
                client_id=f"worker_lean_deterministic_{case_id}",
                executor_type=self.deterministic_executor_descriptor.executor_type,
                executor_id=self.deterministic_executor_descriptor.executor_id,
                executor_version=self.deterministic_executor_descriptor.executor_version,
                capabilities={
                    "executor": ["deterministic_local", "local"],
                    "lean_proof": True,
                    "merge": True,
                },
                status="active",
                stats={},
                metadata={"runtime": "lean_proof", "execution": "deterministic"},
                registered_at=self.created_at,
            ),
            ClientRecord(
                client_id=f"worker_lean_ai_{case_id}",
                executor_type=self.executor_descriptor.executor_type,
                executor_id=self.executor_descriptor.executor_id,
                executor_version=self.executor_descriptor.executor_version,
                capabilities={
                    **self.executor_requirements,
                    "lean_proof_candidate": True,
                },
                status="active",
                stats={},
                metadata={"runtime": "lean_proof", "execution": "ai_api"},
                registered_at=self.created_at,
            ),
        )

    def _dependency_sources(self, logical_key: str) -> list[str]:
        certificate = self._require_split_plan().certificate
        if not isinstance(certificate, LeanLemmaGraphCertificate):
            return []
        return [
            str(edge["source_node_id"])
            for edge in certificate.dependency_edges
            if str(edge["target_node_id"]) == logical_key
        ]

    def _dependency_path(self, logical_key: str) -> list[str]:
        certificate = self._require_split_plan().certificate
        if not isinstance(certificate, LeanLemmaGraphCertificate):
            return []
        ordered: list[str] = []

        def visit(node_id: str) -> None:
            for source in self._dependency_sources(node_id):
                visit(source)
                if source not in ordered:
                    ordered.append(source)

        visit(logical_key)
        if logical_key not in ordered:
            ordered.append(logical_key)
        return ordered

    def planned_ai_unit_id(self, unit: TaskUnit) -> str | None:
        logical_key = unit.metadata.get("child_logical_key")
        if not isinstance(logical_key, str) or unit.unit_type == "merge":
            return None
        return self._planned_ai_unit_id(logical_key)

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
        required_set = set(required)
        required_children = tuple(
            child
            for child in context.children
            if child.unit_id in required_set
        )
        if all(child.state == TaskState.COMPLETED for child in required_children):
            return MergeReadinessDecision(
                status="ready",
                reason="all_required_slots_canonical",
                policy_id="lean_proof.all_required_children.v1",
                policy_version="v1",
                required_child_unit_ids=required,
                selected_child_unit_ids=required,
            )
        if any(child.state == TaskState.FAILED for child in required_children):
            return MergeReadinessDecision(
                status="failed",
                reason="terminal_child_failure",
                policy_id="lean_proof.all_required_children.v1",
                policy_version="v1",
                required_child_unit_ids=required,
            )
        return MergeReadinessDecision(
            status="wait",
            reason="required_children_not_terminal",
            policy_id="lean_proof.all_required_children.v1",
            policy_version="v1",
            required_child_unit_ids=required,
        )

    def _planned_ai_unit_id(self, logical_key: str) -> str:
        certificate = self._require_split_plan().certificate
        if isinstance(certificate, LeanLemmaGraphCertificate):
            return logical_key
        for index, child in enumerate(certificate.child_goals):
            if str(child["child_logical_key"]) == logical_key:
                return f"child_{index}"
        raise ValueError("Lean split certificate is missing the planned child")

    def _install_split_plan(self, split_plan: LeanSplitPlanResult) -> None:
        self._split_plan = split_plan

    def _reset(self, *, case: JsonObject, artifact_store: ArtifactStore) -> None:
        self._artifact_store = artifact_store
        self._case = case
        self._parent_payload_ref = None
        self._split_plan = None
        self._checker_reports_by_request_id = {}
        self._proof_inputs_by_logical_key = {}
        self._canonical_proof_refs_by_logical_key = {}
        self._merge_candidate_refs = {}
        self._merge_result = None
        self._slot_integrity_violation_applied = False

    def _merge_checker_report(self) -> LeanCheckerReport:
        return self.merge_result.root_checker_report

    def _require_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            raise RuntimeError("plan_root must run before Lean execution")
        return self._artifact_store

    def _require_case(self) -> JsonObject:
        if self._case is None:
            raise RuntimeError("plan_root must run before Lean execution")
        return self._case

    def _require_parent_payload_ref(self) -> ArtifactRef:
        if self._parent_payload_ref is None:
            raise RuntimeError("Lean parent payload is not available")
        return self._parent_payload_ref

    def _require_split_plan(self) -> LeanSplitPlanResult:
        if self._split_plan is None:
            raise RuntimeError("Lean split plan is not available")
        return self._split_plan


class LeanExecutionBridge:
    """root/merge 确定性执行，proof unit 委托给注入的 candidate executor。"""

    def __init__(
        self,
        *,
        plugin_runtime: LeanRuntimeAdapter,
        proof_candidate_executor: Any,
    ) -> None:
        self._plugin_runtime = plugin_runtime
        self._proof_candidate_executor = proof_candidate_executor

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        unit_type = str(request.task_unit_snapshot["unit_type"])
        if unit_type in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }:
            submission = self._proof_candidate_executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
            return self._plugin_runtime.normalize_proof_submission(
                submission,
                request=request,
            )
        if request.task_unit_snapshot["parent_unit_id"] is None:
            refs = {
                THEOREM_PAYLOAD_OUTPUT_NAME: self._plugin_runtime.build_root_payload(
                    request
                )
            }
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
            environment_summary={"runtime": "lean_runtime"},
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
        prepare = getattr(
            self._proof_candidate_executor,
            "prepare_process_execution",
            None,
        )
        if callable(prepare):
            prepare(request, execution_index)

    def export_process_result(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ):
        unit_type = str(request.task_unit_snapshot["unit_type"])
        if unit_type not in {
            LEAN_PROOF_SUBGOAL_TASK_TYPE,
            LEAN_PROOF_LEMMA_NODE_TASK_TYPE,
        }:
            return None
        export = getattr(
            self._proof_candidate_executor,
            "export_process_result",
            None,
        )
        return {
            "captured": (
                export(request, submission) if callable(export) else None
            ),
            "plugin_state": self._plugin_runtime.export_process_proof_state(
                request
            ),
        }

    def ingest_process_result(self, process_result) -> None:
        if process_result is None:
            return
        captured = process_result.get("captured")
        plugin_state = process_result.get("plugin_state")
        ingest = getattr(
            self._proof_candidate_executor,
            "ingest_process_result",
            None,
        )
        if callable(ingest) and captured is not None:
            ingest(captured)
        if plugin_state is not None:
            self._plugin_runtime.ingest_process_proof_state(plugin_state)


def _case(root_input: object) -> JsonObject:
    if not isinstance(root_input, dict):
        raise TypeError("Lean runtime root_input must be a catalog case object")
    return dict(root_input)


def _validate_case(case: JsonObject) -> None:
    for field in ("case_id", "schema_version"):
        if field not in case:
            raise ValueError(f"Lean runtime case missing field: {field}")
    if _is_fixed_case(case):
        plan = LeanFixedDecompositionPlan.from_catalog_case(case)
        if plan.preflight_status == "structured_blocked":
            raise ValueError(
                "structured-blocked Lean fixed plan cannot enter checker-success runtime"
            )
    else:
        if "theorem_payload" not in case:
            raise ValueError("simple Lean runtime case requires theorem_payload")
        if case.get("expected_child_count") is None:
            raise ValueError("simple Lean runtime case requires expected_child_count")


def _is_fixed_case(case: JsonObject) -> bool:
    return case.get("schema_version") == LEAN_V2_SCHEMA_VERSION


def _simple_payload_from_case(case: JsonObject) -> LeanTheoremPayload:
    payload = dict(case["theorem_payload"])
    return LeanTheoremPayload(
        theorem_id=f"lean_theorem:{case['case_id']}",
        theorem_name=str(payload["theorem_name"]),
        imports=list(payload.get("imports", ["Init"])),
        namespace=payload.get("namespace", "TokenSharePaperCatalog"),
        open_namespaces=list(payload.get("open_namespaces", [])),
        options=dict(payload.get("options", {})),
        parameters_source=str(payload.get("parameters_source", "")),
        statement_source=str(payload["statement_source"]),
        theorem_source=payload.get("theorem_source"),
        proof_candidate_ref=None,
        library_context=dict(
            payload.get(
                "library_context",
                {
                    "project": "tokenshare_lean",
                    "module": "TokenSharePaperCatalog",
                    "case_id": case["case_id"],
                },
            )
        ),
        decomposition_policy=dict(
            payload.get(
                "decomposition_policy",
                {
                    "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
                    "allowed_rules": ["conjunction", "iff", "intro"],
                    "max_depth": 1,
                    "max_children": int(case["expected_child_count"]),
                    "unsupported_policy": "return_unsupported",
                },
            )
        ),
        resource_limits=dict(
            payload.get(
                "resource_limits",
                {"timeout_seconds": 30, "max_output_bytes": 65536},
            )
        ),
    )


def _read_json(store: ArtifactStore, ref: ArtifactRef) -> JsonObject:
    body = json.loads(store.read_bytes(ref).decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("Lean runtime artifact must contain a JSON object")
    return body


def _safe(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in value
    )
    return normalized or "lean_runtime"


def _json_bytes(body: JsonObject) -> bytes:
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
