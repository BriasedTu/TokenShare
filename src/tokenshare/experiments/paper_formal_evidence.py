"""正式论文实验的独立、可恢复 evidence 文件存储。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENTITY_NAMES = (
    "suite",
    "dispatch",
    "budget",
    "catalog",
    "identity",
    "request_limits",
    "hard_limits",
)
_RUN_FILES = (
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)
_SUCCESS_STATUSES = {"accepted", "completed", "success", "succeeded"}
_TERMINAL_CHECKPOINT_STATUSES = _SUCCESS_STATUSES | {
    "blocked",
    "budget_exhausted",
    "failed",
    "ineligible",
    "partial",
    "timeout",
}
_SUITE_RUNTIME_FIELDS = frozenset(
    {
        "status",
        "formal",
        "pilot_only",
        "execution_scope",
        "capturing",
        "regression_only",
        "paper_eligible",
        "ineligibility_reasons",
        "baseline_policy",
        "suite_identity",
    }
)
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_LOCK_FILE_NAME = ".formal-evidence.lock"


class SharedEvidenceError(ValueError):
    """带类型的 shared evidence 失败，避免消费端解析异常文案。"""

    def __init__(self, message: str, *, evidence_integrity: str) -> None:
        super().__init__(message)
        if evidence_integrity not in {"missing", "invalid", "corrupt"}:
            raise ValueError("invalid shared evidence integrity classification")
        self.evidence_integrity = evidence_integrity
_PLAN_ONLY_ROOT_FILES = frozenset(
    {
        "lean_3x3_matrix.json",
        "model_endpoint_cohort_plan.json",
        "model_policy_plan.json",
        "paper_dispatch_plans.json",
        "run_budget.json",
        "suite_manifest.json",
    }
)
_CURRENT_KEYS = {
    "schema_version",
    "generation_id",
    "generation_manifest_digest",
}
_GENERATION_MANIFEST_KEYS = {"schema_version", "generation_id", "files"}
_RUN_MANIFEST_KEYS = {
    "schema_version",
    "generation_id",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "task_ids",
    "completed_task_ids",
    "status",
}


def _execution_classification(
    suite_body: Any,
    *,
    capturing: bool,
) -> dict[str, Any]:
    """从冻结 suite identity 派生 evidence flags；缺省保持正式行为。"""

    declared = (
        suite_body.get("execution_classification")
        if isinstance(suite_body, dict)
        else None
    )
    if declared is None:
        return {
            "formal": True,
            "pilot_only": False,
            "regression_only": bool(capturing),
            "paper_eligible": True,
            "execution_scope": "formal_matrix",
            "ineligibility_reasons": [],
        }
    if not isinstance(declared, dict):
        raise ValueError("execution_classification must be an object")
    required = {
        "formal",
        "pilot_only",
        "regression_only",
        "paper_eligible",
        "execution_scope",
        "ineligibility_reasons",
    }
    optional = {"baseline_policy"}
    if not required <= set(declared) or set(declared) - required > optional:
        raise ValueError("execution_classification fields are invalid")
    for field_name in ("formal", "pilot_only", "regression_only", "paper_eligible"):
        if not isinstance(declared[field_name], bool):
            raise ValueError(f"execution_classification {field_name} must be a bool")
    reasons = declared["ineligibility_reasons"]
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("execution_classification reasons are invalid")
    if (
        declared["formal"] is not False
        or declared["pilot_only"] is not True
        or declared["regression_only"] is not True
        or declared["paper_eligible"] is not False
        or declared["execution_scope"] != "smoke_suite"
        or not {"smoke_suite", "pilot_only"}.issubset(reasons)
    ):
        raise ValueError("unsupported non-formal execution classification")
    result = {
        **declared,
        "ineligibility_reasons": list(reasons),
    }
    baseline_policy = declared.get("baseline_policy")
    if baseline_policy is not None and baseline_policy not in {
        "required_by_formal_plan",
        "omitted_for_smoke_regression",
    }:
        raise ValueError("execution_classification baseline_policy is invalid")
    return result


@dataclass(frozen=True)
class LoadedFormalEvidence:
    """通过完整性校验后可用于 resume/replay 的最小索引。"""

    completed_task_ids: tuple[str, ...]
    completed_task_ids_by_experiment: dict[str, tuple[str, ...]]
    completed_task_keys: tuple[tuple[str, str, str, str], ...]


class FormalEvidenceStore:
    """只负责正式实验 evidence 的持久化与确定性校验。"""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root).resolve(strict=False)
        self._lock = _lock_for_root(self.output_root)
        self._conditions = self._load_condition_index()
        self._expected_task_counts = self._load_expected_task_counts()

    @classmethod
    def initialize(
        cls,
        *,
        output_root: str | Path,
        suite: Any,
        dispatch: Any,
        budget: Any,
        catalog: Any,
        identity: Any,
        request_limits: Any,
        hard_limits: Any,
        capturing: bool,
    ) -> "FormalEvidenceStore":
        """冻结 suite identity，并原子写入正式执行所需的根 manifests。"""

        root = Path(output_root).resolve(strict=False)
        if not isinstance(capturing, bool):
            raise ValueError("capturing must be a boolean")
        bodies = {
            "suite": _json_value(suite),
            "dispatch": _json_value(dispatch),
            "budget": _json_value(budget),
            "catalog": _json_value(catalog),
            "identity": _json_value(identity),
            "request_limits": _json_value(request_limits),
            "hard_limits": _json_value(hard_limits),
        }
        if root.exists() and any(root.iterdir()):
            _validate_promotable_plan_only_root(root, bodies=bodies)
        root.mkdir(parents=True, exist_ok=True)
        if not isinstance(bodies["suite"], dict):
            raise ValueError("suite body must be a JSON object")
        conditions_by_experiment = _conditions_by_experiment(bodies["dispatch"])
        experiment_ids = tuple(conditions_by_experiment)
        declared_ids = bodies["suite"].get("experiment_ids")
        if declared_ids is not None and tuple(declared_ids) != experiment_ids:
            raise ValueError("suite and dispatch experiment identity mismatch")

        identity_components = {
            name: {"body": body, "digest": _digest_json(body)}
            for name, body in bodies.items()
        }
        classification = _execution_classification(
            bodies["suite"],
            capturing=capturing,
        )
        suite_manifest = dict(bodies["suite"])
        suite_manifest.update(
            {
                "status": "running",
                "formal": classification["formal"],
                "pilot_only": classification["pilot_only"],
                "execution_scope": classification["execution_scope"],
                "capturing": bool(capturing),
                "regression_only": classification["regression_only"],
                "paper_eligible": False,
                "ineligibility_reasons": classification[
                    "ineligibility_reasons"
                ],
                "baseline_policy": classification.get(
                    "baseline_policy",
                    "required_by_formal_plan",
                ),
                "suite_identity": identity_components,
            }
        )
        _atomic_write_json(root / "suite_manifest.json", suite_manifest)
        _atomic_write_json(root / "run_budget.json", bodies["budget"])
        _atomic_write_json(root / "input_catalog_manifest.json", bodies["catalog"])
        _atomic_write_json(root / "paper_dispatch_plans.json", bodies["dispatch"])
        all_conditions = [
            condition
            for conditions in conditions_by_experiment.values()
            for condition in conditions
        ]
        _atomic_write_jsonl(root / "conditions.jsonl", all_conditions)
        dispatch_plans = {
            plan["experiment_id"]: plan for plan in _dispatch_plans(bodies["dispatch"])
        }
        for experiment_id, conditions in conditions_by_experiment.items():
            experiment_root = root / "experiments" / experiment_id
            _atomic_write_json(
                experiment_root / "experiment_manifest.json",
                {
                    "schema_version": "tokenshare.paper_experiment_evidence.v1",
                    "suite_id": suite_manifest.get("suite_id"),
                    "experiment_id": experiment_id,
                    "condition_ids": [item["condition_id"] for item in conditions],
                    "status": "running",
                    "formal": classification["formal"],
                    "pilot_only": classification["pilot_only"],
                    "execution_scope": classification["execution_scope"],
                    "capturing": bool(capturing),
                    "regression_only": classification["regression_only"],
                    "paper_eligible": False,
                    "ineligibility_reasons": classification[
                        "ineligibility_reasons"
                    ],
                    "baseline_policy": classification.get(
                        "baseline_policy",
                        "required_by_formal_plan",
                    ),
                    "dispatch_plan_digest": _digest_json(
                        dispatch_plans[experiment_id]
                    ),
                },
            )
        store = cls(root)
        store._refresh_evidence_manifest()
        return store

    def checkpoint_root(
        self,
        *,
        experiment_id: str,
        condition: Any,
        repeat_id: int | str,
        task: Any,
        attempts: Sequence[Any],
        faults: Sequence[Any],
        events: Sequence[Any],
        artifact_refs: Sequence[Any],
    ) -> None:
        """合并一个 root task checkpoint；已成功 task 永不被后写覆盖。"""

        experiment_id = _safe_id(experiment_id, "experiment_id")
        repeat_name = _safe_id(str(repeat_id), "repeat_id")
        condition_body = _require_object(condition, "condition")
        condition_id = _safe_id(condition_body.get("condition_id"), "condition_id")
        expected_condition = self._conditions.get((experiment_id, condition_id))
        if expected_condition is None:
            raise ValueError("condition does not belong to experiment")
        if _canonical_bytes(condition_body) != _canonical_bytes(expected_condition):
            raise ValueError("condition experiment identity mismatch")
        _validate_context(
            condition_body,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            label="condition",
        )
        task_body = _require_object(task, "task")
        task_id = _safe_id(task_body.get("task_id"), "task_id")
        _validate_context(
            task_body,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=task_id,
            label="task",
        )
        attempt_bodies = [_require_object(item, "attempt") for item in attempts]
        event_bodies = [_require_object(item, "event") for item in events]
        fault_bodies = [_require_object(item, "fault") for item in faults]
        artifact_bodies = [_require_object(item, "artifact ref") for item in artifact_refs]
        if not attempt_bodies:
            raise ValueError("checkpoint requires attempt evidence")
        if not event_bodies:
            raise ValueError("checkpoint requires event evidence")
        for label, records in (
            ("attempt", attempt_bodies),
            ("event", event_bodies),
            ("fault", fault_bodies),
        ):
            for record in records:
                _validate_context(
                    record,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    label=label,
                )

        run_root = (
            self.output_root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / repeat_name
        )
        if _is_success(task_body) and not artifact_bodies:
            raise ValueError("completed task requires artifact evidence")
        with self._lock:
            with _exclusive_output_root_lock(self.output_root):
                self._publish_checkpoint_generation(
                    run_root=run_root,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    task_body=task_body,
                    attempt_bodies=attempt_bodies,
                    fault_bodies=fault_bodies,
                    event_bodies=event_bodies,
                    artifact_bodies=artifact_bodies,
                )

    def build_shared_root_reference(
        self,
        *,
        source_experiment_id: str,
        case_id: str,
        source_repeat_id: int,
        expected_condition_identity: Mapping[str, Any],
        expected_source_versions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """校验并冻结一个已持久化 root，供后续实验零调用引用。"""

        source_experiment_id = _safe_id(
            source_experiment_id,
            "source_experiment_id",
        )
        case_id = _safe_id(case_id, "case_id")
        if isinstance(source_repeat_id, bool) or not isinstance(
            source_repeat_id,
            int,
        ):
            raise SharedEvidenceError(
                "source repeat identity is invalid",
                evidence_integrity="invalid",
            )
        if not isinstance(expected_condition_identity, Mapping):
            raise SharedEvidenceError(
                "expected source condition identity must be an object",
                evidence_integrity="invalid",
            )
        if expected_source_versions is not None and not isinstance(
            expected_source_versions,
            Mapping,
        ):
            raise SharedEvidenceError(
                "expected source version identity must be an object",
                evidence_integrity="invalid",
            )

        suite_manifest = _read_json(self.output_root / "suite_manifest.json")
        suite_identity = suite_manifest.get("suite_identity")
        if not isinstance(suite_identity, dict):
            raise SharedEvidenceError(
                "source suite identity evidence is missing",
                evidence_integrity="missing",
            )
        frozen_request_limits = _frozen_identity_body(
            suite_identity,
            "request_limits",
        )
        frozen_catalog = _frozen_identity_body(suite_identity, "catalog")
        runs_root = (
            self.output_root
            / "experiments"
            / source_experiment_id
            / "runs"
        )
        if not runs_root.is_dir():
            raise SharedEvidenceError(
                "shared source evidence is missing",
                evidence_integrity="missing",
            )

        matches: list[dict[str, Any]] = []
        for condition_root in sorted(
            path for path in runs_root.iterdir() if path.is_dir()
        ):
            run_root = condition_root / str(source_repeat_id)
            if not run_root.is_dir():
                continue
            try:
                tasks = self._validate_run(
                    run_root,
                    experiment_id=source_experiment_id,
                )
            except (ValueError, json.JSONDecodeError) as error:
                raise SharedEvidenceError(
                    "shared source checkpoint integrity validation failed",
                    evidence_integrity="corrupt",
                ) from error
            for task in tasks:
                task_case_id = task.get("case_id", task.get("task_id"))
                if task_case_id != case_id:
                    continue
                matches.append(
                    {
                        "run_root": run_root,
                        "task": task,
                    }
                )
        if not matches:
            raise SharedEvidenceError(
                "shared source case evidence is missing",
                evidence_integrity="missing",
            )
        if len(matches) != 1:
            raise SharedEvidenceError(
                "shared source case identity is ambiguous",
                evidence_integrity="invalid",
            )

        match = matches[0]
        run_root = match["run_root"]
        task = match["task"]
        condition_id = run_root.parent.name
        condition = self._conditions.get((source_experiment_id, condition_id))
        if condition is None:
            raise SharedEvidenceError(
                "shared source condition identity is missing",
                evidence_integrity="missing",
            )
        for field_name, expected_value in expected_condition_identity.items():
            if field_name == "request_limits":
                actual_value = frozen_request_limits
            elif field_name == "catalog_digest":
                actual_value = condition.get(
                    field_name,
                    frozen_catalog.get(field_name),
                )
            else:
                actual_value = condition.get(field_name)
            if _canonical_bytes(actual_value) != _canonical_bytes(expected_value):
                raise SharedEvidenceError(
                    f"shared source condition identity mismatch: {field_name}",
                    evidence_integrity="invalid",
                )

        if not _is_terminal_checkpoint(task):
            raise SharedEvidenceError(
                "shared source evidence is not terminal",
                evidence_integrity="invalid",
            )
        generation_root = self._current_generation_root(run_root, required=True)
        assert generation_root is not None
        current = _read_json(run_root / "CURRENT.json")
        run_manifest = _read_json(generation_root / "run_manifest.json")
        attempts = [
            item
            for item in _read_jsonl(
                generation_root / "per_attempt_results.jsonl"
            )
            if item.get("task_id") == task.get("task_id")
        ]
        events = [
            item
            for item in _read_jsonl(
                generation_root / "events" / "event_log.jsonl"
            )
            if item.get("task_id") == task.get("task_id")
        ]
        faults = [
            item
            for item in _read_jsonl(
                generation_root / "fault_injections.jsonl"
            )
            if item.get("task_id") == task.get("task_id")
        ]
        artifacts = [
            item
            for item in _read_jsonl(
                generation_root / "artifacts" / "artifact_index.jsonl"
            )
            if item.get("task_id") == task.get("task_id")
        ]
        if not attempts or not events:
            raise SharedEvidenceError(
                "shared source evidence is incomplete",
                evidence_integrity="missing",
            )

        source_usage = _shared_source_usage(task=task, attempts=attempts)
        source_root_status = str(task.get("root_status"))
        comparison_eligible = _is_success(task) and bool(
            source_usage["usage_complete"]
        )
        source_record_refs = {
            "source_task_ref": _generation_record_ref(
                self.output_root,
                generation_root,
                "per_task_results.jsonl",
                task,
            ),
            "source_attempt_refs": [
                _generation_record_ref(
                    self.output_root,
                    generation_root,
                    "per_attempt_results.jsonl",
                    item,
                )
                for item in attempts
            ],
            "source_event_refs": [
                _generation_record_ref(
                    self.output_root,
                    generation_root,
                    "events/event_log.jsonl",
                    item,
                )
                for item in events
            ],
            "source_fault_refs": [
                _generation_record_ref(
                    self.output_root,
                    generation_root,
                    "fault_injections.jsonl",
                    item,
                )
                for item in faults
            ],
            "source_artifact_refs": artifacts,
        }
        source_versions = task.get("execution_version_identity")
        _validate_execution_version_identity(source_versions)
        assert isinstance(source_versions, Mapping)
        source_versions = dict(source_versions)
        runtime_generation_identity = task.get("runtime_generation_identity")
        if (
            not isinstance(runtime_generation_identity, Mapping)
            or source_versions.get("runtime_generation_schema_version")
            != runtime_generation_identity.get("schema_version")
            or source_versions.get("runtime_generation_identity_digest")
            != _digest_json(runtime_generation_identity)
        ):
            raise SharedEvidenceError(
                "shared source runtime generation identity mismatch",
                evidence_integrity="invalid",
            )
        if expected_source_versions is not None:
            for field_name, expected_value in expected_source_versions.items():
                if _canonical_bytes(source_versions.get(field_name)) != _canonical_bytes(
                    expected_value
                ):
                    raise SharedEvidenceError(
                        f"shared source version identity mismatch: {field_name}",
                        evidence_integrity="invalid",
                    )
        reference_core: dict[str, Any] = {
            "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
            "source_suite_id": suite_manifest.get("suite_id"),
            "source_run_id": f"{condition_id}/{source_repeat_id}",
            "source_generation_id": generation_root.name,
            "source_generation_manifest_digest": current[
                "generation_manifest_digest"
            ],
            "source_experiment_id": source_experiment_id,
            "source_condition_id": condition_id,
            "source_condition_digest": _digest_json(condition),
            "source_condition": condition,
            "source_case_id": case_id,
            "source_task_id": str(task["task_id"]),
            "source_repeat_id": source_repeat_id,
            "source_seed": condition.get("seed"),
            "source_worker_count": condition.get("worker_count"),
            "source_root_status": source_root_status,
            "source_provider_config_id": condition.get("provider_config_id"),
            "source_model_entry_id": condition.get("model_entry_id"),
            "source_provider_family": condition.get("provider_family"),
            "source_provider_model_id": condition.get("provider_model_id"),
            "source_reasoning_profile_id": condition.get("reasoning_profile_id"),
            "source_request_limits": frozen_request_limits,
            "source_catalog_identity": frozen_catalog,
            "source_versions": source_versions,
            "source_run_manifest_hash": _digest_json(run_manifest),
            "source_task_record_hash": _digest_json(task),
            **source_record_refs,
            "source_usage": source_usage,
            "evidence_integrity": "complete",
            "baseline_comparison_eligible": comparison_eligible,
            "baseline_unavailable_reason": (
                None
                if comparison_eligible
                else (
                    "source_exp1_failed_experimental"
                    if not _is_success(task)
                    else "source_exp1_usage_incomplete"
                )
            ),
            "provider_calls_made": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_estimate": 0.0,
        }
        source_hash = _digest_json(reference_core)
        return {
            **reference_core,
            "source_hash": source_hash,
            "source_reference_id": (
                "shared_exp1_" + source_hash.removeprefix("sha256:")[:24]
            ),
        }

    def _publish_checkpoint_generation(
        self,
        *,
        run_root: Path,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        task_id: str,
        task_body: dict[str, Any],
        attempt_bodies: list[dict[str, Any]],
        fault_bodies: list[dict[str, Any]],
        event_bodies: list[dict[str, Any]],
        artifact_bodies: list[dict[str, Any]],
    ) -> None:
        current_root = self._current_generation_root(run_root, required=False)
        prior_tasks = _generation_jsonl(current_root, "per_task_results.jsonl")
        prior_attempts = _generation_jsonl(
            current_root, "per_attempt_results.jsonl"
        )
        prior_faults = _generation_jsonl(current_root, "fault_injections.jsonl")
        prior_events = _generation_jsonl(current_root, "events/event_log.jsonl")
        prior_artifacts = _generation_jsonl(
            current_root, "artifacts/artifact_index.jsonl"
        )
        prior_by_id = {str(item.get("task_id")): item for item in prior_tasks}
        prior = prior_by_id.get(task_id)
        self._validate_artifact_refs(
            artifact_bodies,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=task_id,
            run_root=run_root,
        )
        if prior is not None and _is_success(prior):
            prior_refs = [
                item for item in prior_artifacts if item.get("task_id") == task_id
            ]
            if _canonical_bytes(prior_refs) != _canonical_bytes(artifact_bodies):
                raise ValueError("completed task artifact evidence is immutable")
            return

        prior_by_id[task_id] = task_body
        merged_tasks = list(prior_by_id.values())
        merged_attempts = _merge_records(
            prior_attempts,
            attempt_bodies,
            key="attempt_id",
        )
        merged_faults = _merge_records(
            prior_faults,
            fault_bodies,
            key="fault_injection_id",
        )
        merged_events = _merge_records(
            prior_events,
            event_bodies,
            key="event_id",
        )
        merged_artifacts = _merge_records(
            prior_artifacts,
            artifact_bodies,
            key="path",
        )
        completed_task_ids = [
            str(item["task_id"]) for item in merged_tasks if _is_success(item)
        ]
        generation_id = uuid.uuid4().hex
        generation_root = run_root / ".generations" / generation_id
        run_manifest = {
            "schema_version": "tokenshare.paper_run_evidence.v1",
            "generation_id": generation_id,
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_ids": [str(item["task_id"]) for item in merged_tasks],
            "completed_task_ids": completed_task_ids,
            "status": _derived_run_status(
                merged_tasks,
                expected_task_count=self._expected_task_counts.get(
                    (experiment_id, condition_id),
                    len(merged_tasks),
                ),
            ),
        }
        _atomic_write_json(generation_root / "run_manifest.json", run_manifest)
        _atomic_write_jsonl(
            generation_root / "per_task_results.jsonl", merged_tasks
        )
        _atomic_write_jsonl(
            generation_root / "per_attempt_results.jsonl", merged_attempts
        )
        _atomic_write_jsonl(
            generation_root / "fault_injections.jsonl", merged_faults
        )
        _atomic_write_jsonl(
            generation_root / "events" / "event_log.jsonl", merged_events
        )
        _atomic_write_jsonl(
            generation_root / "artifacts" / "artifact_index.jsonl",
            merged_artifacts,
        )
        generation_manifest = {
            "schema_version": "tokenshare.paper_checkpoint_generation.v1",
            "generation_id": generation_id,
            "files": [
                _file_evidence(generation_root, generation_root / relative_path)
                for relative_path in _RUN_FILES
            ],
        }
        _atomic_write_json(
            generation_root / "generation_manifest.json", generation_manifest
        )
        _atomic_write_json(
            run_root / "CURRENT.json",
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation_id,
                "generation_manifest_digest": _digest_json(generation_manifest),
            },
        )
        generations_root = run_root / ".generations"
        for candidate in generations_root.iterdir():
            if candidate.is_dir() and candidate != generation_root:
                shutil.rmtree(candidate)
        self._refresh_evidence_manifest()

    @classmethod
    def load(
        cls,
        *,
        output_root: str | Path,
        expected_suite: Any,
        expected_dispatch: Any,
        expected_budget: Any,
        expected_catalog: Any,
        expected_identity: Any,
        expected_request_limits: Any,
        expected_hard_limits: Any,
    ) -> LoadedFormalEvidence:
        """验证 suite identity 和全部 checkpoint evidence 后返回完成索引。"""

        store = cls(output_root)
        suite_path = store.output_root / "suite_manifest.json"
        if not suite_path.is_file():
            raise ValueError("required evidence file is missing: suite_manifest.json")
        suite_manifest = _read_json(suite_path)
        frozen = suite_manifest.get("suite_identity")
        if not isinstance(frozen, dict):
            raise ValueError("suite identity evidence is missing")
        expected = {
            "suite": expected_suite,
            "dispatch": expected_dispatch,
            "budget": expected_budget,
            "catalog": expected_catalog,
            "identity": expected_identity,
            "request_limits": expected_request_limits,
            "hard_limits": expected_hard_limits,
        }
        for name in _IDENTITY_NAMES:
            body = _json_value(expected[name])
            component = frozen.get(name)
            if (
                not isinstance(component, dict)
                or component.get("digest") != _digest_json(body)
                or _canonical_bytes(component.get("body")) != _canonical_bytes(body)
            ):
                raise ValueError(f"suite identity drift: {name}")

        actual_root_bodies = {
            "budget": _read_json_value(store.output_root / "run_budget.json"),
            "catalog": _read_json_value(
                store.output_root / "input_catalog_manifest.json"
            ),
            "dispatch": _read_json_value(
                store.output_root / "paper_dispatch_plans.json"
            ),
        }
        for name, actual_body in actual_root_bodies.items():
            if _canonical_bytes(actual_body) != _canonical_bytes(frozen[name]["body"]):
                raise ValueError(f"suite identity file mismatch: {name}")
        frozen_conditions = [
            condition
            for conditions in _conditions_by_experiment(frozen["dispatch"]["body"]).values()
            for condition in conditions
        ]
        actual_conditions = _read_jsonl(store.output_root / "conditions.jsonl")
        if _canonical_bytes(actual_conditions) != _canonical_bytes(frozen_conditions):
            raise ValueError("suite identity file mismatch: conditions")

        store._validate_evidence_manifest()
        store._validate_suite_and_experiment_manifests(
            suite_manifest=suite_manifest,
            frozen_suite=frozen["suite"]["body"],
            frozen_dispatch=frozen["dispatch"]["body"],
        )
        completed_by_experiment: dict[str, set[str]] = {}
        completed_task_keys: set[tuple[str, str, str, str]] = set()
        for experiment_id in _conditions_by_experiment(frozen["dispatch"]["body"]):
            experiment_root = store.output_root / "experiments" / experiment_id
            completed = completed_by_experiment.setdefault(experiment_id, set())
            runs_root = experiment_root / "runs"
            run_roots = (
                sorted(
                    repeat_root
                    for condition_root in runs_root.iterdir()
                    if condition_root.is_dir()
                    for repeat_root in condition_root.iterdir()
                    if repeat_root.is_dir()
                )
                if runs_root.is_dir()
                else []
            )
            for run_root in run_roots:
                records = store._validate_run(
                    run_root,
                    experiment_id=experiment_id,
                )
                completed.update(
                    str(item["task_id"]) for item in records if _is_success(item)
                )
                completed_task_keys.update(
                    (
                        experiment_id,
                        run_root.parent.name,
                        run_root.name,
                        str(item["task_id"]),
                    )
                    for item in records
                    # checkpoint 已冻结的负面结果同样是终态；resume 不得把它
                    # 当作新的 provider retry 再次执行。
                    if _is_terminal_checkpoint(item)
                )
        normalized = {
            experiment_id: tuple(sorted(task_ids))
            for experiment_id, task_ids in completed_by_experiment.items()
            if task_ids
        }
        all_completed = tuple(
            sorted({task_id for task_ids in normalized.values() for task_id in task_ids})
        )
        return LoadedFormalEvidence(
            completed_task_ids=all_completed,
            completed_task_ids_by_experiment=normalized,
            completed_task_keys=tuple(sorted(completed_task_keys)),
        )

    def _validate_suite_and_experiment_manifests(
        self,
        *,
        suite_manifest: dict[str, Any],
        frozen_suite: Any,
        frozen_dispatch: Any,
    ) -> None:
        if not isinstance(frozen_suite, dict):
            raise ValueError("frozen suite manifest identity must be an object")
        actual_frozen_fields = {
            field_name: value
            for field_name, value in suite_manifest.items()
            if field_name not in _SUITE_RUNTIME_FIELDS
        }
        if _canonical_bytes(actual_frozen_fields) != _canonical_bytes(frozen_suite):
            raise ValueError("suite manifest frozen identity mismatch")
        capturing = suite_manifest.get("capturing")
        if not isinstance(capturing, bool):
            raise ValueError("suite manifest capturing identity is invalid")
        classification = _execution_classification(
            frozen_suite,
            capturing=capturing,
        )
        required_suite_flags = {
            "formal": classification["formal"],
            "pilot_only": classification["pilot_only"],
            "execution_scope": classification["execution_scope"],
            "regression_only": classification["regression_only"],
            "ineligibility_reasons": classification["ineligibility_reasons"],
            "baseline_policy": classification.get(
                "baseline_policy",
                "required_by_formal_plan",
            ),
        }
        for field_name, expected_value in required_suite_flags.items():
            if suite_manifest.get(field_name) != expected_value:
                raise ValueError(f"suite manifest {field_name} identity mismatch")
        if not isinstance(suite_manifest.get("paper_eligible"), bool):
            raise ValueError("suite manifest paper eligibility is invalid")
        if (
            capturing
            or classification["paper_eligible"] is False
        ) and suite_manifest.get("paper_eligible") is True:
            raise ValueError("classified suite cannot be paper eligible")

        plans = _dispatch_plans(frozen_dispatch)
        plan_by_experiment = {plan["experiment_id"]: plan for plan in plans}
        conditions_by_experiment = _conditions_by_experiment(frozen_dispatch)
        declared_ids = set(conditions_by_experiment)
        experiments_root = self.output_root / "experiments"
        actual_ids = {
            path.name for path in experiments_root.iterdir() if path.is_dir()
        } if experiments_root.is_dir() else set()
        if actual_ids != declared_ids:
            extra = actual_ids - declared_ids
            if extra:
                raise ValueError("undeclared experiment root exists")
            raise ValueError("declared experiment manifest root is missing")
        for experiment_id, conditions in conditions_by_experiment.items():
            manifest_path = (
                experiments_root / experiment_id / "experiment_manifest.json"
            )
            if not manifest_path.is_file():
                raise ValueError("declared experiment manifest is missing")
            manifest = _read_json(manifest_path)
            expected = {
                "schema_version": "tokenshare.paper_experiment_evidence.v1",
                "suite_id": suite_manifest.get("suite_id"),
                "experiment_id": experiment_id,
                "condition_ids": [item["condition_id"] for item in conditions],
                "formal": classification["formal"],
                "pilot_only": classification["pilot_only"],
                "execution_scope": classification["execution_scope"],
                "capturing": capturing,
                "regression_only": classification["regression_only"],
                "ineligibility_reasons": classification[
                    "ineligibility_reasons"
                ],
                "baseline_policy": classification.get(
                    "baseline_policy",
                    "required_by_formal_plan",
                ),
                "dispatch_plan_digest": _digest_json(
                    plan_by_experiment[experiment_id]
                ),
            }
            for field_name, expected_value in expected.items():
                if manifest.get(field_name) != expected_value:
                    raise ValueError(
                        f"experiment manifest {field_name} identity mismatch"
                    )
            if manifest.get("status") not in {
                "planned",
                "running",
                "completed",
                "completed_with_failures",
                "blocked",
                "incomplete",
                "budget_exhausted",
                "failed",
            }:
                raise ValueError("experiment manifest status is invalid")
            if not isinstance(manifest.get("paper_eligible"), bool):
                raise ValueError("experiment manifest paper eligibility is invalid")
            if (
                capturing
                or classification["paper_eligible"] is False
            ) and manifest.get("paper_eligible") is True:
                raise ValueError("classified experiment cannot be paper eligible")

    def _current_generation_root(
        self,
        run_root: Path,
        *,
        required: bool,
    ) -> Path | None:
        pointer_path = run_root / "CURRENT.json"
        generations_root = run_root / ".generations"
        if not pointer_path.is_file():
            if required:
                raise ValueError("checkpoint generation has no commit marker")
            return None
        pointer = _read_json(pointer_path)
        _require_exact_keys(pointer, _CURRENT_KEYS, "checkpoint CURRENT")
        if (
            pointer.get("schema_version")
            != "tokenshare.paper_checkpoint_current.v1"
        ):
            raise ValueError("checkpoint CURRENT schema version mismatch")
        _require_string(pointer, "generation_id", "checkpoint CURRENT")
        _require_digest(
            pointer,
            "generation_manifest_digest",
            "checkpoint CURRENT",
        )
        generation_id = _safe_id(pointer.get("generation_id"), "generation_id")
        generation_root = generations_root / generation_id
        if not generation_root.is_dir():
            raise ValueError("checkpoint CURRENT generation is missing")
        manifest_path = generation_root / "generation_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("checkpoint generation manifest is missing")
        generation_manifest = _read_json(manifest_path)
        _require_exact_keys(
            generation_manifest,
            _GENERATION_MANIFEST_KEYS,
            "checkpoint generation manifest",
        )
        if (
            generation_manifest.get("schema_version")
            != "tokenshare.paper_checkpoint_generation.v1"
        ):
            raise ValueError("checkpoint generation schema version mismatch")
        _require_string(
            generation_manifest,
            "generation_id",
            "checkpoint generation manifest",
        )
        if not isinstance(generation_manifest.get("files"), list):
            raise ValueError("checkpoint generation manifest files type is invalid")
        if generation_manifest.get("generation_id") != generation_id:
            raise ValueError("checkpoint generation identity mismatch")
        if pointer.get("generation_manifest_digest") != _digest_json(
            generation_manifest
        ):
            raise ValueError("checkpoint commit marker digest mismatch")
        entries = generation_manifest.get("files")
        assert isinstance(entries, list)
        expected_paths = set(_RUN_FILES)
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid checkpoint generation evidence")
            relative_path = entry["path"]
            if relative_path in seen:
                raise ValueError("duplicate checkpoint generation evidence")
            seen.add(relative_path)
            path = (generation_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, generation_root) or not path.is_file():
                raise ValueError("checkpoint generation evidence is missing")
            if _file_evidence(generation_root, path) != entry:
                raise ValueError("checkpoint generation evidence integrity mismatch")
        if seen != expected_paths:
            raise ValueError("checkpoint generation required evidence mismatch")
        actual_paths = {
            path.relative_to(generation_root).as_posix()
            for path in generation_root.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        }
        if actual_paths != expected_paths | {"generation_manifest.json"}:
            raise ValueError("checkpoint generation contains incomplete evidence")
        return generation_root

    def _load_condition_index(self) -> dict[tuple[str, str], dict[str, Any]]:
        path = self.output_root / "conditions.jsonl"
        if not path.is_file():
            return {}
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for condition in _read_jsonl(path):
            experiment_id = _safe_id(condition.get("experiment_id"), "experiment_id")
            condition_id = _safe_id(condition.get("condition_id"), "condition_id")
            key = (experiment_id, condition_id)
            if key in result:
                raise ValueError("duplicate condition evidence")
            result[key] = condition
        return result

    def _load_expected_task_counts(self) -> dict[tuple[str, str], int]:
        suite_path = self.output_root / "suite_manifest.json"
        if not suite_path.is_file():
            return {}
        suite = _read_json(suite_path)
        identity = suite.get("suite_identity")
        if not isinstance(identity, dict):
            return {}
        dispatch_component = identity.get("dispatch")
        if not isinstance(dispatch_component, dict):
            return {}
        dispatch = dispatch_component.get("body")
        suite_component = identity.get("suite")
        suite_body = (
            suite_component.get("body")
            if isinstance(suite_component, dict)
            else None
        )
        root_case_filter = (
            suite_body.get("root_case_filter", {})
            if isinstance(suite_body, dict)
            else {}
        )
        if not isinstance(root_case_filter, dict):
            raise ValueError("suite root_case_filter must be an object")
        result: dict[tuple[str, str], int] = {}
        for plan in _dispatch_plans(dispatch):
            experiment_id = str(plan["experiment_id"])
            conditions = plan.get("conditions", [])
            selections = plan.get("selections", [])
            if not isinstance(conditions, list) or not isinstance(selections, list):
                continue
            if len(conditions) != len(selections):
                continue
            for condition, selection in zip(conditions, selections, strict=True):
                ordered = selection.get("ordered_case_ids", [])
                if isinstance(ordered, list):
                    condition_id = str(condition["condition_id"])
                    filtered = root_case_filter.get(condition_id, ordered)
                    if not isinstance(filtered, list):
                        raise ValueError("suite root_case_filter entry must be a list")
                    result[(experiment_id, condition_id)] = len(filtered)
        return result

    def _validate_artifact_refs(
        self,
        refs: Sequence[dict[str, Any]],
        *,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        task_id: str,
        run_root: Path,
    ) -> None:
        artifact_root = (run_root / "artifacts").resolve(strict=False)
        for ref in refs:
            expected_identity = {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_id,
            }
            for field_name, expected_value in expected_identity.items():
                if field_name not in ref or ref[field_name] != expected_value:
                    raise ValueError(
                        f"artifact ref {field_name} does not match run identity"
                    )
            relative_path = ref.get("path")
            digest = ref.get("content_hash")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("artifact ref requires path evidence")
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise ValueError("artifact ref requires content_hash evidence")
            path = (self.output_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, artifact_root) or path == artifact_root:
                raise ValueError("artifact ref crosses run artifact path isolation")
            if not path.is_file() or _digest_bytes(path.read_bytes()) != digest:
                raise ValueError("artifact evidence is missing or hash mismatched")

    def _refresh_evidence_manifest(self) -> None:
        entries = []
        for path in sorted(self.output_root.rglob("*")):
            relative_path = (
                path.relative_to(self.output_root).as_posix()
                if path.is_file()
                else ""
            )
            if (
                not path.is_file()
                or path.name == "evidence_manifest.json"
                or path.name == _LOCK_FILE_NAME
                or path.name.endswith(".tmp")
                or _is_noncurrent_generation_path(
                    self.output_root,
                    relative_path,
                )
            ):
                continue
            entries.append(_file_evidence(self.output_root, path))
        _atomic_write_json(
            self.output_root / "evidence_manifest.json",
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v1",
                "files": entries,
            },
        )

    def repair_stale_compatibility_manifest(self) -> dict[str, Any] | None:
        """仅修复与 canonical evidence 逐字节一致的未索引兼容镜像。"""

        try:
            self._validate_evidence_manifest()
            return None
        except ValueError as error:
            if str(error) != "evidence manifest does not exactly index stored files":
                raise

        manifest_path = self.output_root / "evidence_manifest.json"
        manifest = _read_json(manifest_path)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen = {
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and Path(str(entry["path"])).name != _LOCK_FILE_NAME
            and not _is_noncurrent_generation_path(
                self.output_root,
                str(entry["path"]),
            )
        }
        actual = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if seen.difference(actual):
            raise ValueError("stale evidence manifest references missing files")
        unindexed = sorted(actual.difference(seen))
        if not unindexed:
            raise ValueError("stale evidence manifest has no repairable files")

        mirror_pairs = []
        for relative_path in unindexed:
            parts = Path(relative_path).parts
            if not parts or parts[0] in {"experiments", "repairs"}:
                raise ValueError(
                    "unindexed evidence is not a compatibility projection"
                )
            compatibility_path = self.output_root / relative_path
            canonical_path = self.output_root / "experiments" / relative_path
            if (
                not canonical_path.is_file()
                or compatibility_path.read_bytes() != canonical_path.read_bytes()
            ):
                raise ValueError(
                    "unindexed compatibility evidence differs from canonical evidence"
                )
            mirror_pairs.append(
                {
                    "compatibility": _file_evidence(
                        self.output_root,
                        compatibility_path,
                    ),
                    "canonical": _file_evidence(
                        self.output_root,
                        canonical_path,
                    ),
                }
            )

        original_bytes = manifest_path.read_bytes()
        original_digest = _digest_bytes(original_bytes)
        repair_suffix = original_digest.removeprefix("sha256:")[:16]
        repair_root = self.output_root / "repairs"
        original_copy = (
            repair_root
            / f"pre_resume_evidence_manifest_{repair_suffix}.json"
        )
        if original_copy.is_file():
            if original_copy.read_bytes() != original_bytes:
                raise ValueError("preserved evidence manifest identity mismatch")
        else:
            original_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest_path, original_copy)
        repair_body = {
            "schema_version": "tokenshare.paper_compatibility_manifest_repair.v1",
            "repair_kind": "stale_compatibility_projection_index",
            "original_manifest_ref": _file_evidence(
                self.output_root,
                original_copy,
            ),
            "unindexed_compatibility_file_count": len(mirror_pairs),
            "verified_mirror_pairs": mirror_pairs,
            "paper_eligible": False,
        }
        repair_path = (
            repair_root
            / f"compatibility_manifest_repair_{repair_suffix}.json"
        )
        if repair_path.is_file():
            if _read_json(repair_path) != repair_body:
                raise ValueError("compatibility manifest repair identity mismatch")
        else:
            _atomic_write_json(repair_path, repair_body)
        self._refresh_evidence_manifest()
        self._validate_evidence_manifest()
        return repair_body

    def archive_uncheckpointed_adapter_runs(self) -> dict[str, Any] | None:
        """归档设施异常留下、尚未进入 canonical checkpoint 的 adapter 现场。"""

        suite = _read_json(self.output_root / "suite_manifest.json")
        identity = suite.get("suite_identity")
        dispatch_component = (
            identity.get("dispatch") if isinstance(identity, dict) else None
        )
        dispatch = (
            dispatch_component.get("body")
            if isinstance(dispatch_component, dict)
            else None
        )
        if not isinstance(dispatch, dict):
            raise ValueError("suite identity dispatch evidence is missing")

        orphan_roots: list[Path] = []
        for experiment_id, conditions in _conditions_by_experiment(dispatch).items():
            for condition in conditions:
                condition_id = str(condition["condition_id"])
                repeat_id = _safe_id(str(condition.get("repeat_id", 0)), "repeat_id")
                compatibility_root = (
                    self.output_root
                    / experiment_id
                    / "runs"
                    / condition_id
                )
                canonical_current = (
                    self.output_root
                    / "experiments"
                    / experiment_id
                    / "runs"
                    / condition_id
                    / repeat_id
                    / "CURRENT.json"
                )
                if (
                    compatibility_root.is_dir()
                    and not canonical_current.is_file()
                    and any(path.is_file() for path in compatibility_root.rglob("*"))
                ):
                    orphan_roots.append(compatibility_root)
        if not orphan_roots:
            return None

        manifest_path = self.output_root / "evidence_manifest.json"
        manifest = _read_json(manifest_path)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen = {
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and Path(str(entry["path"])).name != _LOCK_FILE_NAME
            and not _is_noncurrent_generation_path(
                self.output_root,
                str(entry["path"]),
            )
        }
        actual = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if seen.difference(actual):
            raise ValueError("stale evidence manifest references missing files")
        orphan_paths = {
            path.relative_to(self.output_root).as_posix()
            for root in orphan_roots
            for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        }
        if actual.difference(seen) != orphan_paths:
            raise ValueError(
                "unindexed evidence is not exactly uncheckpointed adapter evidence"
            )

        inventory = [
            {
                "source_path": relative_path,
                "content_sha256": _digest_bytes(
                    (self.output_root / relative_path).read_bytes()
                ),
                "size": (self.output_root / relative_path).stat().st_size,
            }
            for relative_path in sorted(orphan_paths)
        ]
        repair_suffix = _digest_json(inventory).removeprefix("sha256:")[:16]
        repair_id = f"facility_orphan_{repair_suffix}"
        repair_root = (
            self.output_root / "repairs" / "facility_orphans" / repair_id
        )
        if repair_root.exists():
            raise ValueError("facility orphan archive identity already exists")
        original_copy = repair_root / "pre_archive_evidence_manifest.json"
        original_copy.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(manifest_path, original_copy)

        archived_files = []
        for orphan_root in sorted(orphan_roots):
            source_relative = orphan_root.relative_to(self.output_root)
            destination = repair_root / "original" / source_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(orphan_root), str(destination))
            for archived_path in sorted(destination.rglob("*")):
                if not archived_path.is_file() or archived_path.name.endswith(".tmp"):
                    continue
                original_relative = (
                    source_relative / archived_path.relative_to(destination)
                ).as_posix()
                archived_files.append(
                    {
                        "source_path": original_relative,
                        "archived_ref": _file_evidence(
                            self.output_root,
                            archived_path,
                        ),
                    }
                )

        repair_body = {
            "schema_version": "tokenshare.paper_facility_orphan_archive.v1",
            "repair_id": repair_id,
            "repair_kind": "uncheckpointed_adapter_evidence_archive",
            "original_manifest_ref": _file_evidence(
                self.output_root,
                original_copy,
            ),
            "archived_condition_count": len(orphan_roots),
            "archived_files": archived_files,
            "paper_eligible": False,
        }
        repair_body["repair_digest"] = _digest_json(repair_body)
        _atomic_write_json(repair_root / "repair.json", repair_body)
        self._refresh_evidence_manifest()
        self._validate_evidence_manifest()
        return repair_body

    def _validate_evidence_manifest(self) -> None:
        required = (
            "suite_manifest.json",
            "run_budget.json",
            "input_catalog_manifest.json",
            "paper_dispatch_plans.json",
            "conditions.jsonl",
            "evidence_manifest.json",
        )
        for relative_path in required:
            if not (self.output_root / relative_path).is_file():
                raise ValueError(f"required evidence file is missing: {relative_path}")
        manifest = _read_json(self.output_root / "evidence_manifest.json")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid evidence manifest entry")
            relative_path = entry["path"]
            if (
                Path(relative_path).name == _LOCK_FILE_NAME
                or _is_noncurrent_generation_path(
                    self.output_root,
                    relative_path,
                )
            ):
                continue
            if relative_path in seen:
                raise ValueError("duplicate evidence manifest path")
            seen.add(relative_path)
            path = (self.output_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, self.output_root) or not path.is_file():
                raise ValueError(f"evidence file is missing: {relative_path}")
            if _file_evidence(self.output_root, path) != entry:
                raise ValueError(f"evidence file integrity mismatch: {relative_path}")
        if not set(required[:-1]) <= seen:
            raise ValueError("required evidence files are absent from manifest")
        actual_paths = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if actual_paths != seen:
            raise ValueError("evidence manifest does not exactly index stored files")

    def _validate_run(
        self,
        run_root: Path,
        *,
        experiment_id: str,
    ) -> list[dict[str, Any]]:
        generation_root = self._current_generation_root(run_root, required=True)
        assert generation_root is not None
        run_manifest = _read_json(generation_root / "run_manifest.json")
        _require_exact_keys(run_manifest, _RUN_MANIFEST_KEYS, "run manifest")
        if (
            run_manifest.get("schema_version")
            != "tokenshare.paper_run_evidence.v1"
        ):
            raise ValueError("run manifest schema version mismatch")
        for field_name in ("generation_id", "experiment_id", "condition_id", "status"):
            _require_string(run_manifest, field_name, "run manifest")
        repeat_value = run_manifest.get("repeat_id")
        if isinstance(repeat_value, bool) or not isinstance(repeat_value, (int, str)):
            raise ValueError("run manifest repeat_id type is invalid")
        for field_name in ("task_ids", "completed_task_ids"):
            value = run_manifest.get(field_name)
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"run manifest {field_name} type is invalid")
        condition_id = run_root.parent.name
        repeat_id = run_manifest.get("repeat_id")
        if run_manifest.get("experiment_id") != experiment_id:
            raise ValueError("run evidence crosses experiment identity")
        if run_manifest.get("condition_id") != condition_id:
            raise ValueError("run evidence crosses condition identity")
        if (experiment_id, condition_id) not in self._conditions:
            raise ValueError("run condition does not belong to experiment")
        if str(repeat_id) != run_root.name:
            raise ValueError("run manifest repeat identity mismatch")
        if run_manifest.get("generation_id") != generation_root.name:
            raise ValueError("run manifest generation identity mismatch")
        tasks = _read_jsonl(generation_root / "per_task_results.jsonl")
        attempts = _read_jsonl(generation_root / "per_attempt_results.jsonl")
        events = _read_jsonl(generation_root / "events" / "event_log.jsonl")
        faults = _read_jsonl(generation_root / "fault_injections.jsonl")
        artifacts = _read_jsonl(
            generation_root / "artifacts" / "artifact_index.jsonl"
        )
        if tasks and not attempts:
            raise ValueError("attempt evidence is missing")
        if tasks and not events:
            raise ValueError("event evidence is missing")
        task_ids = {str(item.get("task_id")) for item in tasks}
        if len(task_ids) != len(tasks) or "None" in task_ids:
            raise ValueError("task evidence identity is invalid")
        for label, records in (
            ("task", tasks),
            ("attempt", attempts),
            ("event", events),
            ("fault", faults),
        ):
            for record in records:
                task_id = _safe_id(record.get("task_id"), "task_id")
                _validate_context(
                    record,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    label=label,
                )
                if label in {"attempt", "event"} and str(task_id) not in task_ids:
                    raise ValueError(f"{label} evidence references an unknown task")
                if label == "fault" and str(task_id) not in task_ids:
                    raise ValueError("fault evidence references an unknown task")
        expected_task_ids = [str(item["task_id"]) for item in tasks]
        expected_completed_ids = [
            str(item["task_id"]) for item in tasks if _is_success(item)
        ]
        expected_status = _derived_run_status(
            tasks,
            expected_task_count=self._expected_task_counts.get(
                (experiment_id, condition_id),
                len(tasks),
            ),
        )
        if run_manifest.get("task_ids") != expected_task_ids:
            raise ValueError("run manifest task_ids do not match task records")
        if run_manifest.get("completed_task_ids") != expected_completed_ids:
            raise ValueError(
                "run manifest completed_task_ids do not match task records"
            )
        if run_manifest.get("status") != expected_status:
            raise ValueError("run manifest status does not match task records")
        artifact_task_ids = {str(item.get("task_id")) for item in artifacts}
        for task in tasks:
            if _is_success(task) and str(task["task_id"]) not in artifact_task_ids:
                raise ValueError("completed task artifact evidence is missing")
        for artifact in artifacts:
            artifact_task_id = _safe_id(artifact.get("task_id"), "task_id")
            if artifact_task_id not in task_ids:
                raise ValueError("artifact evidence references an unknown task")
            self._validate_artifact_refs(
                [artifact],
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=repeat_id,
                task_id=artifact_task_id,
                run_root=run_root,
            )
        return tasks


def _frozen_identity_body(
    suite_identity: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    component = suite_identity.get(name)
    if not isinstance(component, Mapping) or not isinstance(
        component.get("body"),
        Mapping,
    ):
        raise ValueError(f"source suite identity is missing: {name}")
    body = dict(component["body"])
    if component.get("digest") != _digest_json(body):
        raise ValueError(f"source suite identity digest mismatch: {name}")
    return body


def _generation_record_ref(
    suite_root: Path,
    generation_root: Path,
    relative_path: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "path": (generation_root / relative_path).relative_to(
            suite_root
        ).as_posix(),
        "record_hash": _digest_json(record),
    }


def _conditions_by_experiment(dispatch: Any) -> dict[str, list[dict[str, Any]]]:
    plans = _dispatch_plans(dispatch)
    result: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        experiment_id = plan["experiment_id"]
        if experiment_id in result:
            raise ValueError("duplicate experiment dispatch plan")
        raw_conditions = plan.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError("dispatch plan requires conditions")
        conditions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_condition in raw_conditions:
            condition = _require_object(raw_condition, "condition")
            condition_id = _safe_id(condition.get("condition_id"), "condition_id")
            if condition.get("experiment_id") != experiment_id:
                raise ValueError("condition experiment identity mismatch")
            if condition_id in seen:
                raise ValueError("duplicate condition_id in experiment")
            seen.add(condition_id)
            conditions.append(condition)
        result[experiment_id] = conditions
    return result


def _dispatch_plans(dispatch: Any) -> list[dict[str, Any]]:
    if isinstance(dispatch, list):
        plans = dispatch
    elif isinstance(dispatch, dict):
        plans = dispatch.get("plans", dispatch.get("dispatch_plans"))
    else:
        raise ValueError("dispatch body must contain JSON plans")
    if not isinstance(plans, list):
        raise ValueError("dispatch body requires plans")
    result: list[dict[str, Any]] = []
    for raw_plan in plans:
        plan = _require_object(raw_plan, "dispatch plan")
        experiment_id = _safe_id(plan.get("experiment_id"), "experiment_id")
        plan["experiment_id"] = experiment_id
        result.append(plan)
    return result


def _validate_promotable_plan_only_root(
    root: Path,
    *,
    bodies: Mapping[str, Any],
) -> None:
    entries = tuple(root.iterdir())
    if any(not entry.is_file() for entry in entries):
        raise ValueError("formal evidence output_root contains non-plan entries")
    names = {entry.name for entry in entries}
    required = {
        "lean_3x3_matrix.json",
        "paper_dispatch_plans.json",
        "run_budget.json",
        "suite_manifest.json",
    }
    if not required <= names or not names <= _PLAN_ONLY_ROOT_FILES:
        raise ValueError("formal evidence output_root contains non-plan entries")

    suite = _read_json(root / "suite_manifest.json")
    budget = _read_json(root / "run_budget.json")
    dispatch = _read_json(root / "paper_dispatch_plans.json")
    matrix = _read_json(root / "lean_3x3_matrix.json")
    expected_suite = _require_object(bodies.get("suite"), "suite")
    expected_budget = _require_object(bodies.get("budget"), "budget")
    expected_dispatch = _require_object(bodies.get("dispatch"), "dispatch")
    expected_catalog = _require_object(bodies.get("catalog"), "catalog")

    if suite.get("suite_id") != "paper_v1_plan" or suite.get("status") != "planned":
        raise ValueError("formal evidence output_root is not a plan-only suite")
    expected_experiment_ids = expected_suite.get("experiment_ids")
    if suite.get("experiment_ids") != expected_experiment_ids:
        raise ValueError("plan-only suite experiment identity drift")
    for field_name in (
        "provider_attempt_count",
        "total_tokens",
        "total_cost_estimate",
    ):
        if suite.get(field_name) != 0:
            raise ValueError("plan-only suite contains provider usage")

    expected_budget_digest = expected_budget.get("budget_digest")
    if (
        not isinstance(expected_budget_digest, str)
        or budget.get("budget_digest") != expected_budget_digest
    ):
        raise ValueError("plan-only budget identity drift")
    quota = budget.get("quota_preflight")
    if not isinstance(quota, Mapping) or quota.get("provider_calls_made") != 0:
        raise ValueError("plan-only budget contains provider calls")
    suite_budget = suite.get("budget_ref")
    if (
        not isinstance(suite_budget, Mapping)
        or suite_budget.get("budget_digest") != expected_budget_digest
    ):
        raise ValueError("plan-only suite budget identity drift")

    if dispatch.get("provider_calls_made") != 0:
        raise ValueError("plan-only dispatch contains provider calls")
    if _canonical_bytes(dispatch.get("plans")) != _canonical_bytes(
        expected_dispatch.get("plans")
    ):
        raise ValueError("plan-only dispatch identity drift")
    if matrix.get("provider_calls_made") != 0:
        raise ValueError("plan-only Lean matrix contains provider calls")
    if matrix.get("catalog_digest") != expected_catalog.get("catalog_digest"):
        raise ValueError("plan-only catalog identity drift")

    for optional_name in (
        "model_endpoint_cohort_plan.json",
        "model_policy_plan.json",
    ):
        if optional_name in names:
            optional_body = _read_json(root / optional_name)
            if optional_body.get("provider_calls_made") != 0:
                raise ValueError("plan-only model preflight contains provider calls")


def _validate_context(
    record: Mapping[str, Any],
    *,
    experiment_id: str,
    condition_id: str,
    repeat_id: int | str,
    label: str,
    task_id: str | None = None,
) -> None:
    expected = {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": repeat_id,
    }
    if task_id is not None:
        expected["task_id"] = task_id
    for field_name, expected_value in expected.items():
        if field_name not in record:
            raise ValueError(f"{label} evidence requires {field_name} identity")
        if record[field_name] != expected_value:
            raise ValueError(f"{label} evidence crosses experiment/run identity")


def _merge_records(
    existing: Sequence[dict[str, Any]],
    incoming: Sequence[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    merged = list(existing)
    positions: dict[str, int] = {}
    for index, record in enumerate(merged):
        value = record.get(key)
        if value is not None:
            positions[str(value)] = index
    for record in incoming:
        value = record.get(key)
        if value is None:
            if record not in merged:
                merged.append(record)
            continue
        text = str(value)
        if text in positions:
            if _canonical_bytes(merged[positions[text]]) != _canonical_bytes(record):
                raise ValueError(f"conflicting checkpoint evidence for {key}={text}")
        else:
            positions[text] = len(merged)
            merged.append(record)
    return merged


def _is_success(task: Mapping[str, Any]) -> bool:
    status = task.get("root_status", task.get("status"))
    return isinstance(status, str) and status.lower() in _SUCCESS_STATUSES


def _shared_source_usage(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provider_attempts = [
        item
        for item in attempts
        if (
            isinstance(item.get("provider_attempt_count"), int)
            and not isinstance(item.get("provider_attempt_count"), bool)
            and int(item["provider_attempt_count"]) > 0
        )
        or isinstance(item.get("model_execution_record_ref"), Mapping)
    ]
    expected_count = task.get("provider_attempt_count")
    count_complete = (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count > 0
        and expected_count == len(provider_attempts)
    )
    complete_attempts: list[Mapping[str, Any]] = []
    currencies: set[str] = set()
    cost_statuses: set[str] = set()
    for attempt in provider_attempts:
        token_values = [
            attempt.get(field_name)
            for field_name in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        ]
        cost_value = attempt.get("cost_estimate")
        currency = attempt.get("cost_estimate_currency")
        cost_status = attempt.get("cost_estimate_status")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in token_values
            )
            or isinstance(cost_value, bool)
            or not isinstance(cost_value, (int, float))
            or float(cost_value) < 0
            or not isinstance(currency, str)
            or not currency
            or cost_status != "estimated"
            or attempt.get("usage_missing") is True
        ):
            continue
        complete_attempts.append(attempt)
        currencies.add(currency)
        cost_statuses.add(cost_status)
    usage_complete = (
        count_complete
        and len(complete_attempts) == len(provider_attempts)
        and len(currencies) == 1
        and cost_statuses == {"estimated"}
    )
    missing_count = max(
        int(expected_count) if isinstance(expected_count, int) else 0,
        len(provider_attempts),
    ) - len(complete_attempts)
    return {
        "provider_attempt_count": len(provider_attempts),
        "expected_provider_attempt_count": (
            int(expected_count)
            if isinstance(expected_count, int) and not isinstance(expected_count, bool)
            else None
        ),
        "prompt_tokens": (
            sum(int(item["prompt_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "completion_tokens": (
            sum(int(item["completion_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "total_tokens": (
            sum(int(item["total_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "cost_estimate": (
            round(
                sum(float(item["cost_estimate"]) for item in complete_attempts),
                12,
            )
            if usage_complete
            else None
        ),
        "usage_complete": usage_complete,
        "usage_missing_provider_attempt_count": missing_count,
        "cost_estimate_status": "estimated" if usage_complete else "usage_missing",
        "cost_estimate_currency": next(iter(currencies)) if usage_complete else None,
    }


def _validate_execution_version_identity(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise SharedEvidenceError(
            "shared source execution version identity is missing",
            evidence_integrity="missing",
        )
    required_strings = (
        "schema_version",
        "plugin_version",
        "parser_version",
        "verifier_version",
        "executor_version",
        "prompt_version",
        "runtime_generation_schema_version",
    )
    if value.get("schema_version") != "tokenshare.paper_execution_version_identity.v1":
        raise SharedEvidenceError(
            "shared source execution version identity schema is invalid",
            evidence_integrity="invalid",
        )
    for field_name in required_strings:
        field_value = value.get(field_name)
        if not isinstance(field_value, str) or not field_value:
            raise SharedEvidenceError(
                f"shared source execution version identity is missing: {field_name}",
                evidence_integrity="missing",
            )
    for field_name in (
        "split_profile_digest",
        "runtime_generation_identity_digest",
    ):
        field_value = value.get(field_name)
        if (
            not isinstance(field_value, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", field_value)
        ):
            raise SharedEvidenceError(
                f"shared source execution version digest is invalid: {field_name}",
                evidence_integrity="invalid",
            )


def _is_terminal_checkpoint(task: Mapping[str, Any]) -> bool:
    status = task.get("root_status", task.get("status"))
    return (
        isinstance(status, str)
        and status.lower() in _TERMINAL_CHECKPOINT_STATUSES
    )


def _derived_run_status(
    tasks: Sequence[Mapping[str, Any]],
    *,
    expected_task_count: int,
) -> str:
    if len(tasks) < expected_task_count:
        return "running"
    statuses = {
        str(task.get("root_status", task.get("status", "failed"))).lower()
        for task in tasks
    }
    if tasks and all(_is_success(task) for task in tasks):
        return "completed"
    if "budget_exhausted" in statuses:
        return "budget_exhausted"
    if statuses == {"blocked"}:
        return "blocked"
    return "completed_with_failures"


def _file_evidence(root: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    relative_path = path.relative_to(root).as_posix()
    records: list[Any] | None = None
    is_artifact_payload = "/artifacts/" in f"/{relative_path}" and not relative_path.endswith(
        "artifact_index.jsonl"
    )
    if not is_artifact_payload and path.suffix == ".json":
        records = [json.loads(content.decode("utf-8"))]
    elif path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    return {
        "path": relative_path,
        "size": len(content),
        "content_sha256": _digest_bytes(content),
        "record_count": None if records is None else len(records),
        "records_digest": None if records is None else _digest_json(records),
    }


def _atomic_write_json(path: Path, body: Any) -> None:
    _atomic_write(path, _canonical_bytes(body) + b"\n")


def _atomic_write_jsonl(path: Path, records: Sequence[Any]) -> None:
    content = b"".join(_canonical_bytes(record) + b"\n" for record in records)
    _atomic_write(path, content)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    body = _read_json_value(path)
    if not isinstance(body, dict):
        raise ValueError(f"JSON evidence must be an object: {path.name}")
    return body


def _read_json_value(path: Path) -> Any:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON evidence: {path.name}") from exc
    return body


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSONL evidence: {path.name}") from exc
    if any(not isinstance(item, dict) for item in records):
        raise ValueError(f"JSONL evidence requires object records: {path.name}")
    return records


def _read_jsonl_if_present(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl(path) if path.is_file() else []


def _generation_jsonl(
    generation_root: Path | None,
    relative_path: str,
) -> list[dict[str, Any]]:
    if generation_root is None:
        return []
    return _read_jsonl(generation_root / relative_path)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    body = _json_value(value)
    if not isinstance(body, dict):
        raise ValueError(f"{label} must be a JSON object")
    return body


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"value is not JSON serializable: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _safe_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a path-safe identifier")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _lock_for_root(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_output_root_lock(root: Path, *, timeout_seconds: float = 30.0):
    lock_path = root / _LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        acquired = False
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "timed out acquiring formal evidence output-root lock"
                    ) from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_exact_keys(
    body: Mapping[str, Any],
    expected_keys: set[str],
    label: str,
) -> None:
    if set(body) != expected_keys:
        raise ValueError(f"{label} keys mismatch")


def _require_string(
    body: Mapping[str, Any],
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} {field_name} type is invalid")


def _require_digest(
    body: Mapping[str, Any],
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} {field_name} type is invalid")


def _is_noncurrent_generation_path(root: Path, relative_path: str) -> bool:
    parts = Path(relative_path).parts
    try:
        generation_index = parts.index(".generations")
    except ValueError:
        return False
    if generation_index + 1 >= len(parts):
        return False
    pointer_path = root.joinpath(*parts[:generation_index], "CURRENT.json")
    if not pointer_path.is_file():
        return False
    pointer = _read_json(pointer_path)
    current_id = pointer.get("generation_id")
    if not isinstance(current_id, str):
        return False
    return parts[generation_index + 1] != current_id
