"""Slim V2 Experiment 2--4 的窄场景接线。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from math import ceil
import json
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from tokenshare.core.models import ProtocolConfig
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.local_runtime import (
    GateDirective,
    ParsedCandidateDirective,
    ProcessWorkerBackend,
    ProtocolMechanismPolicy,
    SequentialWorkerBackend,
    ThreadWorkerBackend,
    WorkerBatchOutcome,
    WorkerTerminationPolicy,
)
from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
from tokenshare.local_runtime.contracts import RecoveryMergeContext
from tokenshare.local_runtime.logical_scheduler import LogicalSourceLatencyScheduler
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanRuntimeAdapter,
)
from tokenshare.plugins.contracts import IncompleteMergeInputError
from tokenshare.storage.artifacts import ArtifactStore

from .execution import FixedTraceSubmissionAdapter, SlimLeanExecutionBridge
from .profiles import ChallengePlanV1
from .schema import AttemptResultV1, PrematureMergeObservationV2, RootInventoryV1
from .storage import RunStore


PERTURBATION_SEED = 20260820
PERTURBATION_VERSION = "slim_v2.exp3_perturbation.v1"

EXP4_MODE_DISABLED_MECHANISMS = {
    "FULL": (),
    "NO_VERIFICATION": ("verification",),
    "NO_PARSER_POLICY": ("parser_policy",),
    "NO_REQUEUE": ("requeue",),
    "NO_MERGE_GATE": ("merge_gate",),
    "NO_VERIFICATION__NO_PARSER_POLICY": ("verification", "parser_policy"),
    "NO_VERIFICATION__NO_REQUEUE": ("verification", "requeue"),
    "NO_VERIFICATION__NO_MERGE_GATE": ("verification", "merge_gate"),
    "NO_PARSER_POLICY__NO_REQUEUE": ("parser_policy", "requeue"),
    "NO_PARSER_POLICY__NO_MERGE_GATE": ("parser_policy", "merge_gate"),
    "NO_REQUEUE__NO_MERGE_GATE": ("requeue", "merge_gate"),
}

_FAULT_TYPES = {
    "false_positive",
    "false_negative",
    "no_return",
    "late_submission",
    "executor_error",
}
_CHALLENGE_FAMILIES = {
    "INVALID_PARSED_CANDIDATE",
    "PARSER_REQUIRED_CANONICAL_JSON",
    "RECOVERABLE_NO_RETURN",
    "REQUIRED_CHILD_DELAY",
}


@dataclass(frozen=True, slots=True)
class ResolvedChallengePlanV1:
    challenge_plan_id: str
    case_id: str
    repeat_id: int
    challenge_family: str
    target_planned_ai_unit_ids: tuple[str, ...]
    attempt_rule: str
    injection_boundary: str


@dataclass(frozen=True, slots=True)
class ScenarioV1:
    inventory: RootInventoryV1
    submission_adapter: FixedTraceSubmissionAdapter
    worker_backend: object
    logical_scheduler: LogicalSourceLatencyScheduler
    mechanism_policy: ProtocolMechanismPolicy
    hooks: object
    backend_kind: str
    challenge_plan: ResolvedChallengePlanV1 | None
    fault_target_planned_ai_unit_ids: tuple[str, ...]
    auxiliary_reference: bool
    challenge_controller: object | None = None
    route_observer: object | None = None

    @property
    def provider_call_count(self) -> int:
        return sum(
            attempt.provider_call_made is True
            for attempt in self.submission_adapter.attempts
        )


class _FactorDeathProgressProcessBackend(ProcessWorkerBackend):
    """只调整Factor death批次的真实Process结果消费顺序。"""

    def __init__(
        self,
        *,
        progress_target_planned_ai_unit_id: str,
        progress_target_index: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._progress_target_planned_ai_unit_id = (
            progress_target_planned_ai_unit_id
        )
        self._progress_target_index = progress_target_index

    def execute_batch(
        self,
        requests: Iterable[ExecutionRequest],
    ) -> tuple[WorkerBatchOutcome, ...]:
        original = tuple(requests)
        targets = [
            request
            for request in original
            if (request.soft_hints or {}).get("planned_ai_unit_id")
            == self._progress_target_planned_ai_unit_id
        ]
        if len(targets) > 1:
            raise ValueError("Factor death batch repeats its progress target")
        ordered = original
        if targets:
            target = targets[0]
            remaining = [request for request in original if request is not target]
            insert_at = min(self._progress_target_index, len(remaining))
            remaining.insert(insert_at, target)
            ordered = tuple(remaining)
        outcomes = super().execute_batch(ordered)
        by_attempt_id = {
            outcome.request.attempt_id: outcome for outcome in outcomes
        }
        if len(by_attempt_id) != len(outcomes):
            raise ValueError("Factor death batch returned duplicate attempt outcomes")
        return tuple(by_attempt_id[request.attempt_id] for request in original)


class ScenarioHooks:
    """只持有fault/challenge plan与实际到达边界的普通记录。"""

    def __init__(
        self,
        *,
        inventory: RootInventoryV1,
        artifact_store: ArtifactStore,
        fault_targets: tuple[str, ...],
        challenge_plan: ResolvedChallengePlanV1 | None,
    ) -> None:
        self._experiment_id = str(inventory.experiment_id)
        self._case_id = str(inventory.case_id)
        self._repeat_id = int(inventory.repeat_id)
        self._domain = str(inventory.domain)
        self._fault_type = inventory.fault_type
        self._fault_targets = frozenset(fault_targets)
        self._challenge_plan = challenge_plan
        self._artifact_store = artifact_store
        self._attempt_context: dict[str, tuple[str, int]] = {}
        self._request_ids_by_planned: dict[str, str] = {}
        self.injection_records: list[dict[str, Any]] = []
        self.boundary_records: list[dict[str, Any]] = []
        self.recovery_records: list[dict[str, Any]] = []
        self.merge_records: list[dict[str, Any]] = []

    def _identity(self, request: ExecutionRequest) -> tuple[str, int]:
        planned = (request.soft_hints or {}).get("planned_ai_unit_id")
        if not isinstance(planned, str) or not planned:
            raise ValueError("scenario request lacks planned_ai_unit_id")
        return planned, request.attempt_ordinal

    def _fault_applies(self, planned: str, ordinal: int) -> bool:
        return (
            self._experiment_id == "exp3"
            and self._fault_type in _FAULT_TYPES
            and planned in self._fault_targets
            and ordinal == 0
        )

    def _challenge_applies(self, planned: str, ordinal: int) -> bool:
        plan = self._challenge_plan
        if plan is None or planned not in plan.target_planned_ai_unit_ids:
            return False
        return plan.attempt_rule == "every_attempt" or ordinal == 0

    def _record_injection(
        self,
        *,
        request: ExecutionRequest,
        family: str,
        boundary: str,
        independently_wrong: bool | None = None,
        source_semantics_preserved: bool | None = None,
        candidate_content_before: Mapping[str, Any] | None = None,
        candidate_content_after: Mapping[str, Any] | None = None,
        candidate_output_refs_before: Mapping[str, str] | None = None,
        candidate_output_refs_after: Mapping[str, str] | None = None,
    ) -> None:
        planned, ordinal = self._identity(request)
        identity = (request.attempt_id, family, boundary)
        if any(
            (item["attempt_id"], item["kind"], item["boundary"]) == identity
            for item in self.injection_records
        ):
            return
        self.injection_records.append(
            {
                "attempt_id": request.attempt_id,
                "planned_ai_unit_id": planned,
                "attempt_ordinal": ordinal,
                "kind": family,
                "boundary": boundary,
                "independently_wrong": independently_wrong,
                "source_semantics_preserved": source_semantics_preserved,
                "candidate_content_before": (
                    dict(candidate_content_before)
                    if candidate_content_before is not None
                    else None
                ),
                "candidate_content_after": (
                    dict(candidate_content_after)
                    if candidate_content_after is not None
                    else None
                ),
                "candidate_output_refs_before": (
                    dict(candidate_output_refs_before)
                    if candidate_output_refs_before is not None
                    else None
                ),
                "candidate_output_refs_after": (
                    dict(candidate_output_refs_after)
                    if candidate_output_refs_after is not None
                    else None
                ),
                "opportunity": True,
                "injected": True,
            }
        )

    def transform_content(self, request: ExecutionRequest, content: str) -> str:
        planned, ordinal = self._identity(request)
        plan = self._challenge_plan
        if (
            plan is None
            or plan.challenge_family != "PARSER_REQUIRED_CANONICAL_JSON"
            or not self._challenge_applies(planned, ordinal)
        ):
            return content
        try:
            canonical = json.dumps(
                json.loads(content),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except json.JSONDecodeError as exc:
            raise ValueError("parser-required challenge source is not valid JSON") from exc
        self._record_injection(
            request=request,
            family=plan.challenge_family,
            boundary="before_plugin_parser",
            source_semantics_preserved=True,
        )
        return canonical

    def transform_submission(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ) -> ExecutionSubmission:
        planned, ordinal = self._identity(request)
        self._attempt_context[request.attempt_id] = (planned, ordinal)
        self._request_ids_by_planned[planned] = request.request_id
        if self._fault_applies(planned, ordinal) and self._fault_type in {
            "no_return",
            "late_submission",
            "executor_error",
        }:
            assert self._fault_type is not None
            self._record_injection(
                request=request,
                family=self._fault_type,
                boundary="after_source_usage",
            )
            return replace(submission, result_kind=self._fault_type)
        plan = self._challenge_plan
        if (
            plan is not None
            and plan.challenge_family
            in {"RECOVERABLE_NO_RETURN", "REQUIRED_CHILD_DELAY"}
            and self._challenge_applies(planned, ordinal)
        ):
            self._record_injection(
                request=request,
                family=plan.challenge_family,
                boundary="after_source_usage",
                source_semantics_preserved=None,
            )
            return replace(submission, result_kind="no_return")
        if (
            self._domain == "lean"
            and self._fault_type in {"false_positive", "false_negative"}
            and self._fault_applies(planned, ordinal)
            and submission.parsed_output_ref is not None
            and submission.candidate_output_refs
        ):
            directive = self.after_parsed_candidate_persisted(
                SimpleNamespace(
                    attempt_id=request.attempt_id,
                    candidate_output_refs=dict(submission.candidate_output_refs),
                    submitted_at=submission.submitted_at,
                    raw_output_ref=submission.raw_output_ref,
                )
            )
            if isinstance(directive, ParsedCandidateDirective):
                return replace(
                    submission,
                    candidate_output_refs=dict(
                        directive.replacement_candidate_output_refs
                    ),
                )
        return submission

    def transform_attempt(
        self,
        request: ExecutionRequest,
        attempt: AttemptResultV1,
        submission: ExecutionSubmission,
    ) -> AttemptResultV1:
        result_kind = submission.result_kind
        if (
            self._fault_type in {"false_positive", "false_negative"}
            and self._fault_applies(*self._identity(request))
        ):
            result_kind = "fault_injected"
        updated = replace(attempt, result_kind=result_kind)
        if self._experiment_id != "exp3":
            return updated
        token_delta, network_delta = _perturbations(
            case_id=self._case_id,
            planned_ai_unit_id=str(attempt.planned_ai_unit_id),
            repeat_id=self._repeat_id,
            attempt_ordinal=int(attempt.attempt_ordinal),
        )
        generated = (
            max(0, round(attempt.source_completion_tokens * (1 + token_delta)))
            if attempt.source_completion_tokens is not None
            else None
        )
        simulated_tokens = (
            max(1, int(attempt.source_prompt_tokens) + generated)
            if generated is not None and attempt.source_prompt_tokens is not None
            else max(1, round(attempt.source_total_tokens * (1 + token_delta)))
            if attempt.source_total_tokens is not None
            else None
        )
        simulated_latency = (
            max(
                1,
                round(
                    attempt.source_latency_ms
                    * (1 + 0.8 * token_delta + 0.2 * network_delta)
                ),
            )
            if attempt.source_latency_ms is not None
            else None
        )
        values = {
            "simulated_total_tokens": simulated_tokens,
            "simulated_latency_ms": simulated_latency,
            "token_perturbation_factor": token_delta,
            "network_perturbation_factor": network_delta,
            "perturbation_seed": PERTURBATION_SEED,
            "perturbation_version": PERTURBATION_VERSION,
        }
        return replace(
            updated,
            **values,
            missing_reason={
                key: value
                for key, value in updated.missing_reason.items()
                if key.removeprefix("attempts[].") not in {
                    name for name, item in values.items() if item is not None
                }
            },
        )

    def after_raw_output_persisted(self, context: object) -> None:
        return None

    def after_parsed_candidate_persisted(
        self,
        context: object,
    ) -> ParsedCandidateDirective | None:
        attempt_id = getattr(context, "attempt_id", None)
        if not isinstance(attempt_id, str) or attempt_id not in self._attempt_context:
            return None
        planned, ordinal = self._attempt_context[attempt_id]
        refs = getattr(context, "candidate_output_refs", None)
        submitted_at = getattr(context, "submitted_at", None)
        if not isinstance(refs, Mapping) or not isinstance(submitted_at, str):
            raise ValueError("parsed-candidate hook context is incomplete")
        if (
            self._challenge_plan is not None
            and self._challenge_plan.challenge_family
            == "PARSER_REQUIRED_CANONICAL_JSON"
            and self._challenge_applies(planned, ordinal)
        ):
            raw_ref = getattr(context, "raw_output_ref", None)
            raw_artifact_id = getattr(raw_ref, "artifact_id", None)
            exposed = bool(raw_artifact_id) and bool(refs) and all(
                getattr(ref, "artifact_id", None) == raw_artifact_id
                for ref in refs.values()
            )
            self.boundary_records.append(
                {
                    "mechanism": "parser_policy",
                    "attempt_id": attempt_id,
                    "planned_ai_unit_id": planned,
                    "attempt_ordinal": ordinal,
                    "raw_only_exposed": exposed,
                }
            )
            return None
        family = None
        independently_wrong = None
        if (
            self._fault_type in {"false_positive", "false_negative"}
            and self._fault_applies(planned, ordinal)
        ):
            family = str(self._fault_type)
            independently_wrong = self._fault_type == "false_positive"
        elif (
            self._challenge_plan is not None
            and self._challenge_plan.challenge_family == "INVALID_PARSED_CANDIDATE"
            and self._challenge_applies(planned, ordinal)
        ):
            family = self._challenge_plan.challenge_family
            independently_wrong = True
        if family is None:
            return None
        if any(
            item.get("attempt_id") == attempt_id
            and item.get("kind") == family
            and item.get("boundary") == "after_parser_before_verification"
            for item in self.injection_records
        ):
            return None
        replacements = dict(refs)
        before_content: dict[str, Any] = {}
        after_content: dict[str, Any] = {}
        before_refs = {
            str(output_name): str(ref.artifact_id)
            for output_name, ref in refs.items()
        }
        for output_name, ref in refs.items():
            body = json.loads(self._artifact_store.read_bytes(ref).decode("utf-8"))
            before_content[str(output_name)] = json.loads(json.dumps(body))
            if family == "false_negative":
                after_content[str(output_name)] = json.loads(json.dumps(body))
                continue
            candidate_body = body
            if isinstance(body.get("content_text"), str):
                candidate_body = json.loads(body["content_text"])
            if "target_n" in candidate_body:
                candidate_body["target_n"] = str(
                    int(candidate_body["target_n"]) + 1
                )
            elif "proof_source" in candidate_body:
                candidate_body["proof_source"] = (
                    "by\n  exact nonexistent_slim_v2_identifier"
                )
            else:
                raise ValueError("candidate hook reached an unsupported domain body")
            if candidate_body is not body:
                mutated_content = json.dumps(candidate_body, ensure_ascii=False)
                body["content_text"] = mutated_content
                choices = body.get("provider_response", {}).get("choices", [])
                if choices and isinstance(choices[0], Mapping):
                    message = choices[0].get("message")
                    if isinstance(message, dict):
                        message["content"] = mutated_content
            replacements[str(output_name)] = self._artifact_store.save_json(
                body,
                artifact_id=f"slim_scenario_{_safe(attempt_id)}_{_safe(str(output_name))}",
                artifact_type=ref.artifact_type,
                artifact_schema_id=ref.artifact_schema_id,
                artifact_schema_version=ref.artifact_schema_version,
                source={"kind": "slim_v2_scenario_candidate"},
                metadata={"attempt_id": attempt_id},
                created_at=submitted_at,
            )
            after_content[str(output_name)] = json.loads(json.dumps(body))
        request = _request_view(attempt_id, planned, ordinal)
        self._record_injection(
            request=request,
            family=family,
            boundary="after_parser_before_verification",
            independently_wrong=independently_wrong,
            source_semantics_preserved=(
                False if family == "INVALID_PARSED_CANDIDATE" else None
            ),
            candidate_content_before=before_content,
            candidate_content_after=after_content,
            candidate_output_refs_before=before_refs,
            candidate_output_refs_after={
                str(output_name): str(ref.artifact_id)
                for output_name, ref in replacements.items()
            },
        )
        return ParsedCandidateDirective(
            replacement_candidate_output_refs=replacements,
        )

    def before_parser(self, context: object) -> None:
        attempt = getattr(context, "attempt", None)
        if attempt is not None:
            self.boundary_records.append(
                {"mechanism": "parser_policy", "attempt_id": attempt.attempt_id}
            )
        return None

    def before_verification(self, context: object) -> GateDirective | None:
        attempt = getattr(context, "attempt", None)
        if attempt is not None:
            self.boundary_records.append(
                {"mechanism": "verification", "attempt_id": attempt.attempt_id}
            )
        submission = getattr(context, "submission", None)
        if not isinstance(submission, ExecutionSubmission):
            return None
        attempt_id = submission.attempt_id
        identity = self._attempt_context.get(attempt_id)
        if identity is None or not (
            self._fault_type == "false_negative"
            and self._fault_applies(*identity)
        ):
            return None
        planned, _ordinal = identity
        if self._domain == "factorization":
            alternatives = sorted(
                request_id
                for candidate_planned, request_id in self._request_ids_by_planned.items()
                if candidate_planned != planned
            )
            if not alternatives:
                raise ValueError(
                    "false_negative factorization requires an alternate planned request context"
                )
            forced_request_id = alternatives[0]
        else:
            forced_request_id = f"slim_v2_false_negative_{_safe(attempt_id)}"
        for record in self.injection_records:
            if record.get("attempt_id") == attempt_id and record.get("kind") == "false_negative":
                record["verification_request_id_before"] = submission.request_id
                record["verification_request_id_after"] = forced_request_id
                break
        return GateDirective(
            replacement=replace(submission, request_id=forced_request_id),
        )

    def before_requeue(self, context: object) -> None:
        attempt = getattr(context, "attempt", None)
        if attempt is not None:
            self.boundary_records.append(
                {"mechanism": "requeue", "attempt_id": attempt.attempt_id}
            )
            self.recovery_records.append(
                {
                    "original_attempt_id": attempt.attempt_id,
                    "trigger": getattr(context, "trigger", None),
                    "retry_allowed": bool(
                        getattr(context, "decision", {}).get("retry_allowed")
                    ),
                }
            )
        return None

    def before_merge(self, context: object) -> GateDirective:
        required = tuple(getattr(context, "required_child_unit_ids", ()))
        canonical = tuple(
            child.unit_id for child in getattr(context, "canonical_children", ())
        )
        gate_satisfied = bool(getattr(context, "gate_satisfied", False))
        self.boundary_records.append(
            {"mechanism": "merge_gate", "gate_satisfied": gate_satisfied}
        )
        self.merge_records.append(
            {
                "gate_satisfied": gate_satisfied,
                "required_child_unit_ids": required,
                "canonical_child_unit_ids": canonical,
                "missing_child_unit_ids": tuple(
                    item for item in required if item not in set(canonical)
                ),
            }
        )
        return GateDirective(bypass=True)

    def on_unit_progress(self, context: object) -> None:
        return None

    def export_process_result(self, request: ExecutionRequest) -> dict[str, Any]:
        attempt_id = request.attempt_id
        return {
            "attempt_context": self._attempt_context.get(attempt_id),
            "injections": [
                dict(item)
                for item in self.injection_records
                if item["attempt_id"] == attempt_id
            ],
        }

    def ingest_process_result(self, value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        context = value.get("attempt_context")
        injections = value.get("injections", ())
        for item in injections:
            if item not in self.injection_records:
                self.injection_records.append(dict(item))
        if (
            isinstance(context, tuple)
            and len(context) == 2
            and injections
        ):
            self._attempt_context[str(injections[0]["attempt_id"])] = context


class ModeBlindChallengeController:
    """只持有普通 challenge plan；构造和动作均看不到 mode/policy。"""

    def __init__(
        self,
        *,
        challenge_plan: ResolvedChallengePlanV1,
        domain: str,
        artifact_store: ArtifactStore,
    ) -> None:
        self._challenge_plan = challenge_plan
        self._domain = domain
        self._artifact_store = artifact_store
        self.injection_records: list[dict[str, Any]] = []

    def _identity(self, request: ExecutionRequest) -> tuple[str, int]:
        planned = (request.soft_hints or {}).get("planned_ai_unit_id")
        if not isinstance(planned, str) or not planned:
            raise ValueError("challenge request lacks planned_ai_unit_id")
        return planned, request.attempt_ordinal

    def _applies(self, request: ExecutionRequest) -> bool:
        planned, ordinal = self._identity(request)
        return (
            planned in self._challenge_plan.target_planned_ai_unit_ids
            and (
                self._challenge_plan.attempt_rule == "every_attempt"
                or ordinal == 0
            )
        )

    def _record(
        self,
        *,
        request: ExecutionRequest,
        boundary: str,
        independently_wrong: bool | None = None,
        source_semantics_preserved: bool | None = None,
        candidate_content_before: Mapping[str, Any] | None = None,
        candidate_content_after: Mapping[str, Any] | None = None,
        candidate_output_refs_before: Mapping[str, str] | None = None,
        candidate_output_refs_after: Mapping[str, str] | None = None,
        opportunity: bool = True,
        injected: bool = True,
    ) -> None:
        planned, ordinal = self._identity(request)
        identity = (request.attempt_id, boundary)
        if any(
            (item["attempt_id"], item["boundary"]) == identity
            for item in self.injection_records
        ):
            return
        self.injection_records.append(
            {
                "attempt_id": request.attempt_id,
                "planned_ai_unit_id": planned,
                "attempt_ordinal": ordinal,
                "kind": self._challenge_plan.challenge_family,
                "boundary": boundary,
                "independently_wrong": independently_wrong,
                "source_semantics_preserved": source_semantics_preserved,
                "candidate_content_before": (
                    dict(candidate_content_before)
                    if candidate_content_before is not None
                    else None
                ),
                "candidate_content_after": (
                    dict(candidate_content_after)
                    if candidate_content_after is not None
                    else None
                ),
                "candidate_output_refs_before": (
                    dict(candidate_output_refs_before)
                    if candidate_output_refs_before is not None
                    else None
                ),
                "candidate_output_refs_after": (
                    dict(candidate_output_refs_after)
                    if candidate_output_refs_after is not None
                    else None
                ),
                "opportunity": opportunity,
                "injected": injected,
            }
        )

    def _attach_actual_record(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ) -> ExecutionSubmission:
        records = [
            dict(item)
            for item in self.injection_records
            if item["attempt_id"] == request.attempt_id
        ]
        if not records:
            return submission
        return replace(
            submission,
            environment_summary={
                **dict(submission.environment_summary or {}),
                "slim_challenge_observations": records,
            },
        )

    def transform_content(self, request: ExecutionRequest, content: str) -> str:
        if (
            self._challenge_plan.challenge_family
            != "PARSER_REQUIRED_CANONICAL_JSON"
            or not self._applies(request)
        ):
            return content
        try:
            canonical = json.dumps(
                json.loads(content),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except json.JSONDecodeError as exc:
            raise ValueError("parser-required challenge source is not valid JSON") from exc
        self._record(
            request=request,
            boundary="before_plugin_parser",
            source_semantics_preserved=True,
        )
        return canonical

    def transform_submission(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
    ) -> ExecutionSubmission:
        if not self._applies(request):
            return submission
        family = self._challenge_plan.challenge_family
        if family in {"RECOVERABLE_NO_RETURN", "REQUIRED_CHILD_DELAY"}:
            self._record(
                request=request,
                boundary="after_source_usage",
                source_semantics_preserved=None,
            )
            return self._attach_actual_record(
                request,
                replace(submission, result_kind="no_return"),
            )
        if family != "INVALID_PARSED_CANDIDATE":
            return self._attach_actual_record(request, submission)
        if submission.parsed_output_ref is None or not submission.candidate_output_refs:
            self._record(
                request=request,
                boundary=self._challenge_plan.injection_boundary,
                source_semantics_preserved=None,
                opportunity=False,
                injected=False,
            )
            return self._attach_actual_record(request, submission)
        replacements: dict[str, Any] = {}
        before_content: dict[str, Any] = {}
        after_content: dict[str, Any] = {}
        before_refs: dict[str, str] = {}
        for output_name, ref in submission.candidate_output_refs.items():
            body = json.loads(self._artifact_store.read_bytes(ref).decode("utf-8"))
            before_content[output_name] = json.loads(json.dumps(body))
            before_refs[output_name] = str(ref.artifact_id)
            if "target_n" in body:
                body["target_n"] = str(int(body["target_n"]) + 1)
            elif "proof_source" in body:
                body["proof_source"] = "by\n  exact nonexistent_slim_v2_identifier"
            else:
                raise ValueError("invalid-candidate challenge reached unsupported body")
            replacements[output_name] = self._artifact_store.save_json(
                body,
                artifact_id=(
                    f"slim_challenge_{_safe(request.attempt_id)}_"
                    f"{_safe(output_name)}"
                ),
                artifact_type=ref.artifact_type,
                artifact_schema_id=ref.artifact_schema_id,
                artifact_schema_version=ref.artifact_schema_version,
                source={"kind": "slim_v2_mode_blind_challenge"},
                metadata={"attempt_id": request.attempt_id},
                created_at=submission.submitted_at,
            )
            after_content[output_name] = json.loads(json.dumps(body))
        self._record(
            request=request,
            boundary="after_parser_before_verification",
            independently_wrong=True,
            source_semantics_preserved=False,
            candidate_content_before=before_content,
            candidate_content_after=after_content,
            candidate_output_refs_before=before_refs,
            candidate_output_refs_after={
                name: str(ref.artifact_id) for name, ref in replacements.items()
            },
        )
        return self._attach_actual_record(
            request,
            replace(submission, candidate_output_refs=replacements),
        )

    def transform_attempt(
        self,
        request: ExecutionRequest,
        attempt: AttemptResultV1,
        submission: ExecutionSubmission,
    ) -> AttemptResultV1:
        return replace(attempt, result_kind=submission.result_kind)

    def export_process_result(self, request: ExecutionRequest) -> dict[str, Any]:
        return {
            "injections": [
                dict(item)
                for item in self.injection_records
                if item["attempt_id"] == request.attempt_id
            ]
        }

    def ingest_process_result(self, value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        for item in value.get("injections", ()):
            if item not in self.injection_records:
                self.injection_records.append(dict(item))


class Exp4StructuralRouteObserver:
    """读取冻结route flags并只记录实际到达/动作；不持有challenge plan。"""

    def __init__(
        self,
        *,
        mechanism_policy: ProtocolMechanismPolicy,
        plugin_runtime: object,
    ) -> None:
        self._mechanism_policy = mechanism_policy
        self._plugin_runtime = plugin_runtime
        self.parser_records: list[dict[str, Any]] = []
        self.verification_records: list[dict[str, Any]] = []
        self.recovery_records: list[dict[str, Any]] = []
        self.merge_records: list[dict[str, Any]] = []
        self.premature_merge_observations: list[PrematureMergeObservationV2] = []

    @staticmethod
    def _planned(request: ExecutionRequest) -> str:
        planned = (request.soft_hints or {}).get("planned_ai_unit_id")
        if not isinstance(planned, str) or not planned:
            raise ValueError("structural route request lacks planned_ai_unit_id")
        return planned

    def select_parser_route(self, request: ExecutionRequest, raw_ref: object) -> str:
        route = (
            "PARSE"
            if self._mechanism_policy.parser_policy_enabled
            else "RAW_PASSTHROUGH"
        )
        self.parser_records.append(
            {
                "attempt_id": request.attempt_id,
                "planned_ai_unit_id": self._planned(request),
                "attempt_ordinal": request.attempt_ordinal,
                "route": route,
                "raw_artifact_persisted": raw_ref is not None,
                "domain_parser_call_count": 0,
                "parse_result": None,
                "raw_only_exposed": None,
            }
        )
        return route

    def record_parser_result(
        self,
        request: ExecutionRequest,
        submission: ExecutionSubmission,
        parse_result: str | None,
    ) -> dict[str, Any] | None:
        matches = [
            item
            for item in self.parser_records
            if item["attempt_id"] == request.attempt_id
        ]
        if not matches and parse_result is None and submission.result_kind != "succeeded":
            return None
        if len(matches) != 1:
            raise ValueError("parser route lacks a unique actual decision")
        record = matches[0]
        record["parse_result"] = parse_result
        record["domain_parser_call_count"] = (
            0 if parse_result == "bypassed" else 1
        )
        record["raw_only_exposed"] = bool(
            parse_result == "bypassed"
            and submission.raw_output_ref is not None
            and submission.parsed_output_ref is None
            and not submission.candidate_output_refs
        )
        return dict(record)

    def record_lean_verification_route(
        self,
        request: ExecutionRequest,
        *,
        domain_child_checker_invoked: bool,
        normalize_invoked: bool,
    ) -> dict[str, Any]:
        record = {
                "attempt_id": request.attempt_id,
                "planned_ai_unit_id": self._planned(request),
                "domain_child_checker_call_count": int(domain_child_checker_invoked),
                "normalize_proof_submission_call_count": int(normalize_invoked),
            }
        self.verification_records.append(record)
        return dict(record)

    def after_raw_output_persisted(self, context: object) -> None:
        return None

    def after_parsed_candidate_persisted(self, context: object) -> None:
        return None

    def before_parser(self, context: object) -> None:
        return None

    def before_verification(self, context: object) -> None:
        attempt = getattr(context, "attempt", None)
        if attempt is not None and not any(
            item.get("attempt_id") == attempt.attempt_id
            for item in self.verification_records
        ):
            self.verification_records.append(
                {
                    "attempt_id": attempt.attempt_id,
                    "planned_ai_unit_id": None,
                    "domain_child_checker_call_count": None,
                    "normalize_proof_submission_call_count": None,
                }
            )
        return None

    def before_requeue(self, context: object) -> None:
        decision = getattr(context, "decision", {})
        retry_allowed = decision.get("retry_allowed") is True
        attempt = getattr(context, "attempt", None)
        self.recovery_records.append(
            {
                "recovery_attempt_id": getattr(attempt, "attempt_id", None),
                "retry_allowed": retry_allowed,
                "route_status": (
                    "applied"
                    if retry_allowed
                    and not self._mechanism_policy.replacement_attempts_allowed
                    else "not_reached"
                ),
                "stuck_due_to_no_requeue": bool(
                    retry_allowed
                    and not self._mechanism_policy.replacement_attempts_allowed
                ),
            }
        )
        return None

    def before_recovery_merge(
        self,
        context: RecoveryMergeContext,
    ) -> GateDirective | None:
        canonical_ids = tuple(
            child.unit_id for child in context.canonical_children
        )
        missing_ids = tuple(
            unit_id
            for unit_id in context.required_child_unit_ids
            if unit_id not in set(canonical_ids)
        )
        record = {
            "boundary": "recovery_premerge",
            "route_status": "not_reached",
            "recovery_attempt_id": context.recovered_attempt_id,
            "gate_satisfied": context.gate_satisfied,
            "required_child_unit_ids": tuple(context.required_child_unit_ids),
            "canonical_child_unit_ids": canonical_ids,
            "missing_required_slot_ids": missing_ids,
            "plugin_merge_attempted": False,
            "plugin_outcome": "not_attempted",
            "plugin_error_kind": None,
            "root_checker_reached": False,
            "root_check_passed": None,
        }
        self.merge_records.append(record)
        if context.gate_satisfied or self._mechanism_policy.merge_gate_enabled:
            return None
        plugin_outcome = "candidate_produced"
        plugin_error_kind = None
        root_checker_reached = False
        root_check_passed = None
        try:
            self._plugin_runtime.build_merge(
                parent=context.parent,
                canonical_children=context.canonical_children,
                slot_integrity_enabled=True,
            )
            try:
                merge_result = self._plugin_runtime.merge_result
            except (AttributeError, RuntimeError):
                merge_result = None
            checker_report = getattr(merge_result, "root_checker_report", None)
            if checker_report is not None:
                root_checker_reached = True
                status = getattr(checker_report, "status", None)
                root_check_passed = getattr(status, "value", status) == "accepted"
        except IncompleteMergeInputError as exc:
            plugin_outcome = "rejected_incomplete_input"
            plugin_error_kind = type(exc).__name__
        observation = PrematureMergeObservationV2(
            recovery_attempt_id=context.recovered_attempt_id,
            gate_satisfied=False,
            required_child_unit_ids=tuple(context.required_child_unit_ids),
            canonical_child_unit_ids=canonical_ids,
            missing_required_slot_ids=missing_ids,
            plugin_merge_attempted=True,
            plugin_outcome=plugin_outcome,
            plugin_error_kind=plugin_error_kind,
            root_checker_reached=root_checker_reached,
            root_check_passed=root_check_passed,
            final_result_present=False,
            failure_stage=(
                "plugin_merge"
                if plugin_outcome == "rejected_incomplete_input"
                else "root_checker"
                if root_checker_reached and root_check_passed is False
                else "no_final"
            ),
        )
        observation.validate()
        self.premature_merge_observations.append(observation)
        record.update(
            {
                "route_status": "applied",
                "plugin_merge_attempted": True,
                "plugin_outcome": plugin_outcome,
                "plugin_error_kind": plugin_error_kind,
                "root_checker_reached": root_checker_reached,
                "root_check_passed": root_check_passed,
            }
        )
        if not self._mechanism_policy.replacement_attempts_allowed:
            self.recovery_records.append(
                {
                    "recovery_attempt_id": context.recovered_attempt_id,
                    "retry_allowed": context.recovery_decision.get("retry_allowed")
                    is True,
                    "route_status": "preempted_by_merge_first",
                    "stuck_due_to_no_requeue": False,
                }
            )
        return GateDirective(stop=True)

    def before_merge(self, context: object) -> None:
        canonical = tuple(
            child.unit_id for child in getattr(context, "canonical_children", ())
        )
        required = tuple(getattr(context, "required_child_unit_ids", ()))
        self.merge_records.append(
            {
                "boundary": "normal_merge",
                "gate_satisfied": bool(getattr(context, "gate_satisfied", False)),
                "required_child_unit_ids": required,
                "canonical_child_unit_ids": canonical,
                "missing_required_slot_ids": tuple(
                    item for item in required if item not in set(canonical)
                ),
                "plugin_merge_attempted": False,
                "plugin_outcome": "not_attempted",
                "plugin_error_kind": None,
                "root_checker_reached": False,
                "root_check_passed": None,
            }
        )
        return None

    def on_unit_progress(self, context: object) -> None:
        return None


def build_challenge_plan(
    *,
    inventory: RootInventoryV1,
    root_input: Mapping[str, Any],
    source_store: RunStore,
    plan: ChallengePlanV1,
) -> ResolvedChallengePlanV1:
    """只从结构化inventory、普通plan和source语义解析目标。"""

    inventory.validate()
    if inventory.experiment_id != "exp4":
        raise ValueError("challenge plans are only valid for Exp4")
    if (
        plan.challenge_plan_id != inventory.challenge_plan_id
        or plan.case_id != inventory.case_id
        or plan.repeat_id != inventory.repeat_id
        or plan.challenge_family not in _CHALLENGE_FAMILIES
    ):
        raise ValueError("challenge plan identity differs from root inventory")
    planned = tuple(sorted(inventory.planned_ai_unit_ids))
    if not planned:
        raise ValueError("challenge plan requires planned AI units")
    if plan.target_rule == "stable_first_planned_unit":
        targets = planned[:1]
    elif plan.target_rule in {"last_required_range", "last_required_terminal_slot"}:
        targets = planned[-1:]
    elif plan.target_rule == "all_true_divisor_ranges":
        target_n = int(root_input["target_n"])
        selected = []
        for unit_id in planned:
            trace = source_store.read_trace(str(inventory.case_id), 0, unit_id)
            if trace.candidate_start is None or trace.candidate_end is None:
                raise ValueError("divisor challenge requires Factorization source ranges")
            if any(
                target_n % candidate == 0
                for candidate in range(trace.candidate_start, trace.candidate_end + 1)
            ):
                selected.append(unit_id)
        targets = tuple(selected) or planned[-1:]
    else:
        raise ValueError(f"unsupported challenge target rule {plan.target_rule!r}")
    boundary = {
        "PARSER_REQUIRED_CANONICAL_JSON": "before_plugin_parser",
        "INVALID_PARSED_CANDIDATE": "after_parser_before_verification",
        "RECOVERABLE_NO_RETURN": "after_source_usage",
        "REQUIRED_CHILD_DELAY": "after_source_usage",
    }[plan.challenge_family]
    return ResolvedChallengePlanV1(
        challenge_plan_id=plan.challenge_plan_id,
        case_id=plan.case_id,
        repeat_id=plan.repeat_id,
        challenge_family=plan.challenge_family,
        target_planned_ai_unit_ids=targets,
        attempt_rule=plan.attempt_rule,
        injection_boundary=boundary,
    )


def build_exp3_reference(inventory: RootInventoryV1) -> RootInventoryV1:
    """从同case/repeat论文root生成不进入论文分母的无fault辅助reference。"""

    inventory.validate()
    if inventory.experiment_id != "exp3":
        raise ValueError("Exp3 reference requires an Exp3 inventory root")
    stratum = inventory.topic_family or inventory.difficulty
    reference = replace(
        inventory,
        condition_id=(
            f"exp3-reference|domain={inventory.domain}|stratum={stratum}"
            f"|repeat={inventory.repeat_id}"
        ),
        fault_type=None,
        fault_rate=None,
        dead_worker_count=None,
        kill_progress_target_ratio=None,
    )
    reference.validate()
    return reference


def build_scenario(
    *,
    inventory: RootInventoryV1,
    root_input: Mapping[str, Any],
    source_store: RunStore,
    artifact_store: ArtifactStore,
    plugin_runtime: object,
    protocol_config: ProtocolConfig,
    submitted_at: Callable[[], str],
    challenge_plan: ChallengePlanV1 | None = None,
    process_timeout_seconds: float = 30.0,
) -> ScenarioV1:
    """把一个结构化Exp2--4 root翻译为现有scheduler/hooks/policy/backend。"""

    inventory.validate()
    if inventory.experiment_id not in {"exp2", "exp3", "exp4"}:
        raise ValueError("scenario roots must belong to Exp2, Exp3, or Exp4")
    if str(root_input.get("case_id")) != inventory.case_id:
        raise ValueError("root input case_id differs from inventory")
    expected_retries = 1 if inventory.experiment_id == "exp4" else 2
    if protocol_config.max_retries != expected_retries:
        raise ValueError(
            f"{inventory.experiment_id} requires max_retries={expected_retries}"
        )
    _validate_source_inventory(inventory, source_store)
    resolved_challenge = None
    if inventory.experiment_id == "exp4":
        if challenge_plan is None:
            raise ValueError("Exp4 root requires its preregistered challenge plan")
        resolved_challenge = build_challenge_plan(
            inventory=inventory,
            root_input=root_input,
            source_store=source_store,
            plan=challenge_plan,
        )
    elif challenge_plan is not None:
        raise ValueError("non-Exp4 root cannot carry a challenge plan")
    disabled = EXP4_MODE_DISABLED_MECHANISMS.get(inventory.mode, ())
    if (
        inventory.experiment_id == "exp4"
        and tuple(inventory.disabled_mechanisms) != disabled
    ):
        raise ValueError("Exp4 structured disabled_mechanisms differ from mode mapping")
    policy = ProtocolMechanismPolicy(
        verification_enabled="verification" not in disabled,
        parser_policy_enabled="parser_policy" not in disabled,
        replacement_attempts_allowed="requeue" not in disabled,
        merge_gate_enabled="merge_gate" not in disabled,
        slot_integrity_enabled=True,
    )
    fault_targets = _fault_targets(inventory)
    if resolved_challenge is not None:
        challenge_controller: object | None = ModeBlindChallengeController(
            challenge_plan=resolved_challenge,
            domain=str(inventory.domain),
            artifact_store=artifact_store,
        )
        route_observer: object | None = Exp4StructuralRouteObserver(
            mechanism_policy=policy,
            plugin_runtime=plugin_runtime,
        )
        hooks = route_observer
    else:
        challenge_controller = ScenarioHooks(
            inventory=inventory,
            artifact_store=artifact_store,
            fault_targets=fault_targets,
            challenge_plan=None,
        )
        route_observer = None
        hooks = challenge_controller
    fixed = FixedTraceSubmissionAdapter(
        source_store=source_store,
        artifact_store=artifact_store,
        case_id=str(inventory.case_id),
        domain=str(inventory.domain),
        provider_entry_id=str(inventory.provider_entry_id),
        configured_model=str(inventory.configured_model),
        experiment_id=str(inventory.experiment_id),
        scenario_controller=challenge_controller,
        structural_route_observer=route_observer,
    )
    if isinstance(plugin_runtime, FactorizationRuntimeAdapter):
        executor = FactorizationExecutionBridge(
            plugin_runtime=plugin_runtime,
            range_executor=fixed,
        )
    elif isinstance(plugin_runtime, LeanRuntimeAdapter):
        executor = SlimLeanExecutionBridge(
            plugin_runtime=plugin_runtime,
            proof_candidate_executor=fixed,
            verification_enabled=policy.verification_enabled,
            structural_route_observer=route_observer,
        )
    else:
        raise TypeError("scenario requires a Factorization or Lean runtime adapter")
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)

    def completion_schedule(
        request: ExecutionRequest,
        submission: ExecutionSubmission | None,
        failure_kind: str | None,
    ) -> WorkerCompletionSchedule:
        planned = (request.soft_hints or {}).get("planned_ai_unit_id")
        latency = 0
        if isinstance(planned, str):
            matching = [
                item for item in fixed.attempts if item.attempt_id == request.attempt_id
            ]
            if matching:
                attempt = matching[-1]
                latency = (
                    attempt.simulated_latency_ms
                    if inventory.experiment_id == "exp3"
                    else attempt.source_latency_ms
                ) or 0
            elif submission is not None and isinstance(submission.usage_summary, Mapping):
                usage = submission.usage_summary
                latency = int(
                    usage.get("simulated_latency_ms")
                    or usage.get("source_latency_ms")
                    or 0
                )
        return WorkerCompletionSchedule(
            source_latency_ms=latency,
            attempt_ordinal=request.attempt_ordinal,
        )

    if inventory.dead_worker_count is not None:
        ratio = inventory.kill_progress_target_ratio
        if ratio not in {0.25, 0.5, 0.75}:
            raise ValueError("worker death requires a frozen progress ratio")
        ordered_targets = tuple(sorted(inventory.planned_ai_unit_ids))
        target_index = min(
            len(ordered_targets) - 1,
            max(0, ceil(ratio * len(ordered_targets)) - 1),
        )
        death_target = ordered_targets[target_index]
        if inventory.domain == "factorization":
            target_n = int(root_input["target_n"])
            witness_targets: list[str] = []
            for planned_ai_unit_id in ordered_targets:
                trace = source_store.read_trace(
                    str(inventory.case_id), 0, planned_ai_unit_id
                )
                if trace.candidate_start is None or trace.candidate_end is None:
                    raise ValueError("worker death range lacks structured bounds")
                if any(
                    target_n % candidate == 0
                    for candidate in range(
                        trace.candidate_start, trace.candidate_end + 1
                    )
                ):
                    witness_targets.append(planned_ai_unit_id)
            if witness_targets:
                death_target = witness_targets[0]
        termination = WorkerTerminationPolicy(
            target_planned_ai_unit_ids=(death_target,),
            kill_point=f"progress_{int(ratio * 100)}",
            termination_count_target=inventory.dead_worker_count,
            total_planned_ai_unit_count=len(inventory.planned_ai_unit_ids),
            process_timeout_seconds=process_timeout_seconds,
        )
        backend_args = {
            "executor": executor,
            "capacity": int(inventory.worker_count),
            "submitted_at": submitted_at,
            "termination_policy": termination,
            "completion_schedule": completion_schedule,
        }
        backend = (
            _FactorDeathProgressProcessBackend(
                progress_target_planned_ai_unit_id=death_target,
                progress_target_index=target_index,
                **backend_args,
            )
            if inventory.domain == "factorization"
            else ProcessWorkerBackend(**backend_args)
        )
        backend_kind = "process"
    elif inventory.worker_count == 1:
        backend = SequentialWorkerBackend(
            executor=executor,
            submitted_at=submitted_at,
            completion_schedule=completion_schedule,
        )
        backend_kind = "sequential"
    else:
        backend = ThreadWorkerBackend(
            executor=executor,
            capacity=int(inventory.worker_count),
            submitted_at=submitted_at,
            completion_schedule=completion_schedule,
        )
        backend_kind = "thread"
    return ScenarioV1(
        inventory=inventory,
        submission_adapter=fixed,
        worker_backend=backend,
        logical_scheduler=scheduler,
        mechanism_policy=policy,
        hooks=hooks,
        backend_kind=backend_kind,
        challenge_plan=resolved_challenge,
        fault_target_planned_ai_unit_ids=fault_targets,
        auxiliary_reference=(
            inventory.experiment_id == "exp3"
            and inventory.fault_type is None
            and inventory.fault_rate is None
            and inventory.dead_worker_count is None
            and inventory.kill_progress_target_ratio is None
        ),
        challenge_controller=challenge_controller,
        route_observer=route_observer,
    )


def _validate_source_inventory(inventory: RootInventoryV1, store: RunStore) -> None:
    seen = set()
    for planned in inventory.planned_ai_unit_ids:
        trace = store.read_trace(str(inventory.case_id), 0, planned)
        identity = (trace.case_id, trace.source_repeat_id, trace.planned_ai_unit_id)
        if identity in seen:
            raise ValueError("fixed trace source contains a duplicate key")
        seen.add(identity)
        if (
            identity != (inventory.case_id, 0, planned)
            or trace.domain != inventory.domain
            or trace.provider_family != "deepseek"
            or trace.provider_entry_id != inventory.provider_entry_id
            or trace.configured_model != inventory.configured_model
            or trace.requested_model != inventory.configured_model
            or trace.resolved_model != inventory.configured_model
            or not trace.attempts
        ):
            raise ValueError("fixed trace source identity differs from inventory")


def _fault_targets(inventory: RootInventoryV1) -> tuple[str, ...]:
    if inventory.experiment_id != "exp3" or inventory.fault_type is None:
        return ()
    if inventory.fault_type not in _FAULT_TYPES or inventory.fault_rate is None:
        raise ValueError("rate-fault root has incomplete structured fields")
    ordered = tuple(sorted(inventory.planned_ai_unit_ids))
    count = max(1, ceil(float(inventory.fault_rate) * len(ordered)))
    count = min(count, len(ordered))
    return tuple(
        ordered[(index * len(ordered)) // count]
        for index in range(count)
    )


def _perturbations(
    *,
    case_id: str,
    planned_ai_unit_id: str,
    repeat_id: int,
    attempt_ordinal: int,
) -> tuple[float, float]:
    identity = json.dumps(
        {
            "attempt_ordinal": attempt_ordinal,
            "case_id": case_id,
            "experiment_repeat_id": repeat_id,
            "planned_ai_unit_id": planned_ai_unit_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = sha256(f"{PERTURBATION_SEED}:".encode("ascii") + identity).digest()
    token_integer = int.from_bytes(digest[:8], "big")
    network_integer = int.from_bytes(digest[8:16], "big")
    scale = 0.2 / (2**64 - 1)
    return token_integer * scale - 0.1, network_integer * scale - 0.1


def _request_view(attempt_id: str, planned: str, ordinal: int) -> Any:
    class _RequestView:
        pass

    value = _RequestView()
    value.attempt_id = attempt_id
    value.attempt_ordinal = ordinal
    value.soft_hints = {"planned_ai_unit_id": planned}
    return value


def _safe(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


__all__ = [
    "EXP4_MODE_DISABLED_MECHANISMS",
    "ResolvedChallengePlanV1",
    "ScenarioV1",
    "build_challenge_plan",
    "build_exp3_reference",
    "build_scenario",
]
