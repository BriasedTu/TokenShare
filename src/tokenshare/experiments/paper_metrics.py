"""Evidence-derived metrics for real-AI paper experiment suites."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import csv
import json
import math
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_models import JsonObject, digest_json
from tokenshare.experiments.paper_unit_commitments import (
    build_ai_unit_binding_from_request,
    validate_ai_unit_binding,
)
from tokenshare.storage.artifacts import ArtifactStore


EVIDENCE_FILES = (
    "execution_plan.json",
    "conditions.jsonl",
    "run_results.jsonl",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)
ATTEMPT_REF_FIELDS = (
    "request_ref",
    "raw_output_ref",
    "parsed_output_ref",
    "parse_failure_ref",
    "provenance_ref",
    "usage_ref",
    "model_execution_record_ref",
)
REQUIRED_ATTEMPT_REF_FIELDS = (
    "request_ref",
    "raw_output_ref",
    "provenance_ref",
    "usage_ref",
    "model_execution_record_ref",
)
PLAN_CONDITION_BIND_FIELDS = (
    "condition_id",
    "repeat_id",
    "domain",
    "difficulty",
    "paper_difficulty",
    "topic_family",
)
RUN_PLAN_BIND_FIELDS = (
    "condition_id",
    "repeat_id",
    "run_id",
    "case_id",
    "execution_status",
)
TASK_PLAN_BIND_FIELDS = (
    "condition_id",
    "repeat_id",
    "task_id",
    "domain",
    "difficulty",
    "paper_difficulty",
    "topic_family",
)
ATTEMPT_PLAN_BIND_FIELDS = (
    "condition_id",
    "repeat_id",
    "run_id",
    "task_id",
)
TASK_EVENT_TYPES = {
    "task_started",
    "task_completed",
    "task_blocked",
    "task_budget_exhausted",
}


@dataclass(frozen=True, kw_only=True)
class PaperMetricsResult:
    """从不可变 evidence 复算出的逐任务与聚合指标。"""

    suite_id: str
    pilot_only: bool
    paper_eligible: bool
    ineligibility_reasons: tuple[str, ...]
    totals: JsonObject
    task_metrics: tuple[JsonObject, ...]
    summary_rows: tuple[JsonObject, ...]
    audit: JsonObject
    schema_version: str = "tokenshare.paper_metrics.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "pilot_only": self.pilot_only,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "totals": _json_copy(self.totals),
            "task_metrics": [_json_copy(row) for row in self.task_metrics],
            "summary_rows": [_json_copy(row) for row in self.summary_rows],
            "audit": _json_copy(self.audit),
        }


def _require_gate_c_indexed_attempt_ref(
    attempt: JsonObject,
    field_name: str,
    indexed_artifact_ids: set[str],
) -> None:
    ref = attempt.get(field_name)
    if not isinstance(ref, dict) or str(ref.get("artifact_id")) not in (
        indexed_artifact_ids
    ):
        raise ValueError(f"Gate C pilot attempt has unindexed {field_name}")


def _validate_gate_c_attempt_artifact_refs(
    attempt: JsonObject,
    indexed_artifact_ids: set[str],
) -> None:
    attempt_status = str(attempt.get("attempt_status"))
    required_fields = [
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ]
    if attempt_status != "provider_error":
        required_fields.append("raw_output_ref")
    if attempt_status == "parse_failed":
        required_fields.append("parse_failure_ref")
    elif attempt_status in {
        "succeeded",
        "verification_rejected",
        "checker_rejected",
    }:
        required_fields.append("parsed_output_ref")
    elif attempt_status != "provider_error" and not (
        isinstance(attempt.get("parsed_output_ref"), dict)
        or isinstance(attempt.get("parse_failure_ref"), dict)
    ):
        raise ValueError(
            "Gate C pilot attempt requires parsed output or parse failure"
        )

    for field_name in required_fields:
        _require_gate_c_indexed_attempt_ref(
            attempt,
            field_name,
            indexed_artifact_ids,
        )

    for field_name in ATTEMPT_REF_FIELDS:
        if field_name in required_fields or attempt.get(field_name) is None:
            continue
        _require_gate_c_indexed_attempt_ref(
            attempt,
            field_name,
            indexed_artifact_ids,
        )


def recompute_gate_c_pilot_metrics(suite_root: str | Path) -> JsonObject:
    """从 Gate C 单 case pilot evidence 复算，不读取既有 CSV/summary。"""

    root = Path(suite_root)
    plan = _read_json(root / "execution_plan.json")
    stored_digest = plan.get("execution_plan_digest")
    digest_body = {
        key: value
        for key, value in plan.items()
        if key != "execution_plan_digest"
    }
    if stored_digest != digest_json(digest_body):
        raise ValueError("Gate C pilot execution plan digest is invalid")
    tasks = _read_jsonl(root / "per_task_results.jsonl")
    attempts = _read_jsonl(root / "per_attempt_results.jsonl")
    events = _read_jsonl(root / "events" / "event_log.jsonl")
    artifacts = _read_jsonl(root / "artifacts" / "artifact_index.jsonl")
    if len(tasks) != 1:
        raise ValueError("Gate C pilot metrics require exactly one task")
    task = tasks[0]
    if task.get("condition_id") != plan.get("condition_id"):
        raise ValueError("Gate C pilot task condition binding drift")
    if task.get("case_id") != plan.get("selected_case_id"):
        raise ValueError("Gate C pilot task case binding drift")
    attempt_ids = [str(attempt.get("attempt_id")) for attempt in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("Gate C pilot attempt IDs must be unique")
    if any(
        attempt.get("condition_id") != task.get("condition_id")
        or attempt.get("task_id") != task.get("task_id")
        or attempt.get("run_id") != task.get("run_id")
        for attempt in attempts
    ):
        raise ValueError("Gate C pilot attempt binding drift")
    event_types = {str(event.get("event_type")) for event in events}
    if event_types != {
        "suite_started",
        "task_started",
        "task_completed",
        "suite_finished",
    }:
        raise ValueError("Gate C pilot event lifecycle is incomplete")
    indexed_artifact_ids = {
        str(record.get("artifact_ref", {}).get("artifact_id"))
        for record in artifacts
        if isinstance(record.get("artifact_ref"), dict)
    }
    for attempt in attempts:
        _validate_gate_c_attempt_artifact_refs(attempt, indexed_artifact_ids)
    provider_attempt_count = sum(
        1
        for attempt in attempts
        if attempt.get("attempt_status")
        not in {"cancelled_by_budget", "lease_expired"}
    )
    row: JsonObject = {
        "condition_id": task["condition_id"],
        "run_id": task["run_id"],
        "case_id": task["case_id"],
        "domain": task["domain"],
        "paper_difficulty": task["paper_difficulty"],
        "root_status": task["root_status"],
        "accepted_validity": task["accepted_validity"],
        "attempt_count": len(attempts),
        "provider_attempt_count": provider_attempt_count,
        "parse_failure_count": sum(
            attempt.get("attempt_status") == "parse_failed"
            for attempt in attempts
        ),
        "verification_rejection_count": sum(
            attempt.get("attempt_status") == "verification_rejected"
            for attempt in attempts
        ),
        "checker_rejection_count": sum(
            attempt.get("attempt_status") == "checker_rejected"
            for attempt in attempts
        ),
        "total_tokens": sum(int(attempt.get("total_tokens", 0)) for attempt in attempts),
        "cost_estimate": sum(
            float(attempt.get("cost_estimate", 0.0)) for attempt in attempts
        ),
        "paper_eligible": False,
        "pilot_only": True,
    }
    return {
        "schema_version": "tokenshare.paper_gate_c_pilot_metrics.v1",
        "suite_id": plan["suite_id"],
        "execution_plan_digest": stored_digest,
        "row": row,
        "evidence_counts": {
            "task_count": len(tasks),
            "attempt_count": len(attempts),
            "event_count": len(events),
            "artifact_index_count": len(artifacts),
        },
        "paper_eligible": False,
        "pilot_only": True,
    }


def write_gate_c_pilot_metrics(suite_root: str | Path) -> JsonObject:
    root = Path(suite_root)
    metrics = recompute_gate_c_pilot_metrics(root)
    row = dict(metrics["row"])
    path = root / "metrics" / "per_condition_summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (root / "metrics" / "gate_c_pilot_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metrics


def recompute_exp1_pilot_metrics(suite_root: str | Path) -> PaperMetricsResult:
    """读取 run/task/attempt/event/artifact evidence，拒绝信任旧 summary。"""

    root = Path(suite_root)
    evidence = _load_and_validate_manifest(root)
    plan = evidence["execution_plan"]
    suite = _read_json(root / "suite_manifest.json")
    conditions = evidence["conditions"]
    runs = evidence["runs"]
    tasks = evidence["tasks"]
    attempts = evidence["attempts"]
    events = evidence["events"]
    artifacts = evidence["artifacts"]
    attested_run_ids, attested_secret_scan = _load_secret_scan_attestation(
        root=root,
        suite=suite,
        runs=runs,
        tasks=tasks,
    )

    _validate_execution_plan(plan)
    _validate_complete_structure(
        plan=plan,
        suite=suite,
        conditions=conditions,
        runs=runs,
        tasks=tasks,
        attempts=attempts,
        events=events,
    )
    artifact_reasons, indexed_refs_by_run = _validate_artifacts(
        plan=plan,
        runs=runs,
        tasks=tasks,
        attempts=attempts,
        artifacts=artifacts,
    )

    planned_by_condition = {
        str(item["condition_id"]): item for item in plan["tasks"]
    }
    runs_by_condition = {str(item["condition_id"]): item for item in runs}
    attempts_by_condition: dict[str, list[JsonObject]] = {}
    for attempt in attempts:
        attempts_by_condition.setdefault(str(attempt["condition_id"]), []).append(
            attempt
        )

    ineligibility_reasons = list(artifact_reasons)
    if int(plan["planned_ai_units"]) != len(attempts):
        ineligibility_reasons.append("planned_ai_units_not_fully_attempted")
    task_rows: list[JsonObject] = []
    for task in tasks:
        condition_id = str(task["condition_id"])
        planned = planned_by_condition[condition_id]
        run = runs_by_condition[condition_id]
        task_attempts = attempts_by_condition.get(condition_id, [])
        _validate_task_attempt_totals(
            task=task,
            run=run,
            planned=planned,
            attempts=task_attempts,
        )
        event_wall_clock_ms = _validate_task_event_lifecycle(
            suite_id=str(plan["suite_id"]),
            suite_stop_reason=suite.get("stop_reason"),
            task=task,
            run=run,
            planned=planned,
            events=events,
        )
        ineligibility_reasons.extend(
            _task_eligibility_reasons(
                task=task,
                run=run,
                planned=planned,
                attempts=task_attempts,
                indexed_refs=indexed_refs_by_run.get(str(run["run_id"]), set()),
                suite_secret_scan_attested=str(run["run_id"]) in attested_run_ids,
            )
        )
        task_rows.append(
            _task_metric_row(
                task=task,
                run=run,
                planned=planned,
                attempts=task_attempts,
                wall_clock_ms=event_wall_clock_ms,
                suite_secret_scan_attested=str(run["run_id"]) in attested_run_ids,
            )
        )

    pilot_only = plan.get("pilot_only") is True and suite.get("pilot_only") is True
    if not pilot_only:
        ineligibility_reasons.append("pilot_only_marker_missing")
    if plan.get("provider_calls_made_before_execution") != 0:
        ineligibility_reasons.append("pre_execution_provider_call_count_not_zero")
    if suite.get("stop_reason") is not None:
        ineligibility_reasons.append(f"suite_stop_reason:{suite['stop_reason']}")
    if str(suite.get("status")) in {"budget_exhausted", "failed", "blocked"}:
        ineligibility_reasons.append(f"suite_status:{suite.get('status')}")

    unique_reasons = tuple(dict.fromkeys(ineligibility_reasons))
    paper_eligible = not unique_reasons
    if suite.get("paper_eligible") is True and not paper_eligible:
        unique_reasons = tuple(
            dict.fromkeys((*unique_reasons, "suite_manifest_eligibility_overclaim"))
        )
        paper_eligible = False

    totals = _aggregate_rows(task_rows)
    summary_rows = _summary_rows(task_rows, paper_eligible=paper_eligible)
    _validate_suite_totals(
        suite=suite,
        plan=plan,
        totals=totals,
        runs=runs,
        tasks=tasks,
        attempts=attempts,
    )
    audit: JsonObject = {
        "schema_version": "tokenshare.paper_metrics_audit.v1",
        "evidence_manifest_valid": True,
        "execution_plan_digest_valid": True,
        "suite_lifecycle_complete": True,
        "artifact_integrity_valid": not artifact_reasons,
        "cross_layer_totals_valid": True,
        "budget_digest_match": suite.get("budget_digest") == plan.get("budget_digest"),
        "real_transport_evidence_valid": not any(
            reason.startswith("non_real_transport:")
            or reason.startswith("missing_transport_evidence:")
            for reason in unique_reasons
        ),
        "structured_blocked_zero_call_valid": not any(
            reason.startswith("structured_blocked_has_attempts:")
            for reason in unique_reasons
        ),
        "evidence_manifest_digest": _read_json(
            root / "evidence_manifest.json"
        )["evidence_manifest_digest"],
        "execution_plan_digest": plan["execution_plan_digest"],
        "artifact_index_record_count": len(artifacts),
        "event_record_count": len(events),
        "historical_provider_attempt_count": len(attempts),
        "current_provider_calls_made": int(suite["provider_calls_made"]),
        "replayed_run_count": int(suite["replayed_run_count"]),
        "wall_clock_source": "task_started_to_terminal_event_recorded_at",
        "provider_latency_source": "sum_of_attempt_latency_ms",
        "suite_secret_scan_attestation_valid": bool(attested_run_ids),
        "suite_secret_scan_attested_run_count": len(attested_run_ids),
        "attested_secret_scan": _json_copy(attested_secret_scan),
    }
    return PaperMetricsResult(
        suite_id=str(plan["suite_id"]),
        pilot_only=pilot_only,
        paper_eligible=paper_eligible,
        ineligibility_reasons=unique_reasons,
        totals=totals,
        task_metrics=tuple(task_rows),
        summary_rows=tuple(summary_rows),
        audit=audit,
    )


def _load_and_validate_manifest(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"paper suite root does not exist: {root}")
    for relative_path in (*EVIDENCE_FILES, "evidence_manifest.json", "suite_manifest.json"):
        if not (root / relative_path).is_file():
            raise ValueError(f"paper evidence file is missing: {relative_path}")

    plan = _read_json(root / "execution_plan.json")
    conditions = _read_jsonl(root / "conditions.jsonl")
    runs = _read_jsonl(root / "run_results.jsonl")
    tasks = _read_jsonl(root / "per_task_results.jsonl")
    attempts = _read_jsonl(root / "per_attempt_results.jsonl")
    events = _read_jsonl(root / "events" / "event_log.jsonl")
    artifacts = _read_jsonl(root / "artifacts" / "artifact_index.jsonl")
    records_by_path: dict[str, list[JsonObject]] = {
        "execution_plan.json": [plan],
        "conditions.jsonl": conditions,
        "run_results.jsonl": runs,
        "per_task_results.jsonl": tasks,
        "per_attempt_results.jsonl": attempts,
        "events/event_log.jsonl": events,
        "artifacts/artifact_index.jsonl": artifacts,
    }
    manifest = _read_json(root / "evidence_manifest.json")
    manifest_digest = manifest.get("evidence_manifest_digest")
    manifest_without_digest = {
        key: value
        for key, value in manifest.items()
        if key != "evidence_manifest_digest"
    }
    if manifest_digest != digest_json(manifest_without_digest):
        raise ValueError("paper evidence manifest digest is invalid")
    file_records = manifest.get("files")
    if not isinstance(file_records, dict) or set(file_records) != set(EVIDENCE_FILES):
        raise ValueError("paper evidence manifest file inventory is incomplete")
    for relative_path, records in records_by_path.items():
        expected = file_records.get(relative_path)
        actual = {
            "record_count": len(records),
            "records_digest": digest_json(records),
        }
        if expected != actual:
            raise ValueError(f"paper evidence manifest mismatch: {relative_path}")
    if manifest.get("execution_plan_digest") != plan.get("execution_plan_digest"):
        raise ValueError("paper evidence manifest execution plan digest mismatch")
    return {
        "execution_plan": plan,
        "conditions": conditions,
        "runs": runs,
        "tasks": tasks,
        "attempts": attempts,
        "events": events,
        "artifacts": artifacts,
    }


def _validate_execution_plan(plan: JsonObject) -> None:
    stored_digest = plan.get("execution_plan_digest")
    without_digest = {
        key: value for key, value in plan.items() if key != "execution_plan_digest"
    }
    if stored_digest != digest_json(without_digest):
        raise ValueError("paper execution plan digest is invalid")
    if not isinstance(plan.get("suite_id"), str) or not plan["suite_id"]:
        raise ValueError("paper execution plan suite_id is missing")
    if not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        raise ValueError("paper execution plan tasks are missing")


def _validate_complete_structure(
    *,
    plan: JsonObject,
    suite: JsonObject,
    conditions: list[JsonObject],
    runs: list[JsonObject],
    tasks: list[JsonObject],
    attempts: list[JsonObject],
    events: list[JsonObject],
) -> None:
    planned_tasks = list(plan["tasks"])
    if conditions != plan.get("conditions"):
        raise ValueError("paper condition evidence does not match execution plan")
    _require_unique(conditions, "condition_id", "condition")
    _require_unique(planned_tasks, "condition_id", "planned task")
    _require_unique(planned_tasks, "run_id", "planned run")
    _require_unique(runs, "condition_id", "run condition")
    _require_unique(runs, "run_id", "run")
    _require_unique(tasks, "condition_id", "task")
    _require_unique(attempts, "attempt_id", "attempt")
    _require_unique(events, "event_id", "event")

    planned_condition_ids = {str(item["condition_id"]) for item in planned_tasks}
    if {str(item["condition_id"]) for item in runs} != planned_condition_ids:
        raise ValueError("paper run evidence is incomplete")
    if {str(item["condition_id"]) for item in tasks} != planned_condition_ids:
        raise ValueError("paper task evidence is incomplete")
    if int(plan.get("planned_conditions", -1)) != len(conditions):
        raise ValueError("paper planned condition count mismatch")
    if int(plan.get("planned_root_runs", -1)) != len(planned_tasks):
        raise ValueError("paper planned root count mismatch")
    if str(suite.get("suite_id")) != str(plan["suite_id"]):
        raise ValueError("paper suite manifest suite_id mismatch")
    for field_name in ("budget_digest", "profile_digest", "catalog_digest"):
        if suite.get(field_name) != plan.get(field_name):
            raise ValueError(f"paper suite manifest {field_name} mismatch")
    if int(suite.get("condition_count", -1)) != len(conditions):
        raise ValueError("paper suite condition count mismatch")
    if int(suite.get("run_count", -1)) != len(runs):
        raise ValueError("paper suite run count mismatch")
    if int(suite.get("task_count", -1)) != len(tasks):
        raise ValueError("paper suite task count mismatch")
    if suite.get("hard_limits") != plan.get("hard_limits"):
        raise ValueError("paper suite hard limits do not match execution plan")
    _validate_plan_record_bindings(
        plan=plan,
        conditions=conditions,
        runs=runs,
        tasks=tasks,
        attempts=attempts,
    )
    _validate_suite_event_lifecycle(plan=plan, suite=suite, events=events)


def _validate_plan_record_bindings(
    *,
    plan: JsonObject,
    conditions: list[JsonObject],
    runs: list[JsonObject],
    tasks: list[JsonObject],
    attempts: list[JsonObject],
) -> None:
    planned_by_condition = {
        str(item["condition_id"]): item for item in plan["tasks"]
    }
    conditions_by_id = {
        str(item["condition_id"]): item for item in conditions
    }
    runs_by_condition = {str(item["condition_id"]): item for item in runs}
    tasks_by_condition = {str(item["condition_id"]): item for item in tasks}
    max_attempts_by_condition: dict[str, int] = {}

    for condition_id, planned in planned_by_condition.items():
        condition = conditions_by_id[condition_id]
        _require_bound_fields(
            actual=planned,
            expected=condition,
            field_names=PLAN_CONDITION_BIND_FIELDS,
            label="planned task/condition execution plan",
        )
        _require_bound_fields(
            actual=runs_by_condition[condition_id],
            expected=planned,
            field_names=RUN_PLAN_BIND_FIELDS,
            label="run/execution plan",
        )
        _require_bound_fields(
            actual=tasks_by_condition[condition_id],
            expected=planned,
            field_names=TASK_PLAN_BIND_FIELDS,
            label="task/execution plan",
        )
        ai_units = planned.get("ai_units")
        if (
            not isinstance(ai_units, list)
            or any(not isinstance(unit_id, str) or not unit_id for unit_id in ai_units)
            or len(ai_units) != len(set(ai_units))
        ):
            raise ValueError("paper execution plan AI unit inventory is invalid")
        ai_unit_bindings = planned.get("ai_unit_bindings")
        if not isinstance(ai_unit_bindings, list):
            raise ValueError("paper execution plan AI unit commitment is missing")
        if planned["execution_status"] == "structured_blocked":
            if ai_unit_bindings:
                raise ValueError(
                    "paper execution plan structured-blocked AI unit commitment is invalid"
                )
        else:
            if len(ai_unit_bindings) != len(ai_units):
                raise ValueError(
                    "paper execution plan AI unit commitment inventory mismatch"
                )
            binding_order: list[str] = []
            binding_unit_ids: set[str] = set()
            for binding in ai_unit_bindings:
                if not isinstance(binding, dict):
                    raise ValueError(
                        "paper execution plan AI unit commitment is invalid"
                    )
                validate_ai_unit_binding(binding)
                planned_ai_unit_id = str(binding["planned_ai_unit_id"])
                unit_id = str(binding["unit_id"])
                binding_order.append(planned_ai_unit_id)
                if unit_id in binding_unit_ids:
                    raise ValueError(
                        "paper execution plan AI unit commitment duplicate unit_id"
                    )
                binding_unit_ids.add(unit_id)
            if binding_order != ai_units or len(binding_order) != len(set(binding_order)):
                raise ValueError(
                    "paper execution plan AI unit commitment inventory mismatch"
                )
        max_attempts_by_condition[condition_id] = (
            _planned_max_provider_attempts_per_ai_unit(
                planned=planned,
                ai_units=ai_units,
            )
        )

    attempts_by_condition: dict[str, list[JsonObject]] = {}
    for attempt in attempts:
        condition_id = str(attempt.get("condition_id") or "")
        planned = planned_by_condition.get(condition_id)
        if planned is None:
            raise ValueError("paper attempt/execution plan condition binding mismatch")
        _require_bound_fields(
            actual=attempt,
            expected=planned,
            field_names=ATTEMPT_PLAN_BIND_FIELDS,
            label="attempt/execution plan",
        )
        unit_id = attempt.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("paper attempt/execution plan AI unit binding mismatch")
        planned_ai_unit_id = attempt.get("planned_ai_unit_id")
        if not isinstance(planned_ai_unit_id, str) or not planned_ai_unit_id:
            raise ValueError(
                "paper attempt/execution plan planned AI unit binding mismatch"
            )
        planned_binding = _planned_ai_unit_binding(
            planned=planned,
            planned_ai_unit_id=planned_ai_unit_id,
        )
        if planned_binding is None:
            raise ValueError(
                "paper attempt/execution plan planned AI unit commitment mismatch"
            )
        if unit_id != planned_binding.get("unit_id"):
            raise ValueError(
                "paper unit commitment mismatch: attempt unit_id conflicts with execution plan"
            )
        _require_non_negative_int(
            attempt.get("provider_attempt_index"),
            "attempt provider_attempt_index",
        )
        attempts_by_condition.setdefault(condition_id, []).append(attempt)

    _require_unique_fields(
        attempts,
        ("run_id", "task_id", "unit_id", "provider_attempt_index"),
        "attempt AI unit",
    )
    _require_unique_fields(
        attempts,
        (
            "run_id",
            "task_id",
            "planned_ai_unit_id",
            "provider_attempt_index",
        ),
        "attempt planned AI unit",
    )
    for condition_id, planned in planned_by_condition.items():
        expected_units = list(planned["ai_units"])
        task = tasks_by_condition[condition_id]
        task_attempts = attempts_by_condition.get(condition_id, [])
        zero_attempt_task = (
            planned["execution_status"] == "structured_blocked"
            or task.get("root_status") == "budget_exhausted"
        )
        if zero_attempt_task:
            if task_attempts:
                raise ValueError(
                    "paper task/attempt count and execution plan AI unit coverage mismatch"
                )
            continue

        indices_by_unit: dict[str, list[int]] = {}
        actual_unit_order: list[str] = []
        for attempt in task_attempts:
            planned_ai_unit_id = str(attempt["planned_ai_unit_id"])
            if planned_ai_unit_id not in indices_by_unit:
                actual_unit_order.append(planned_ai_unit_id)
                indices_by_unit[planned_ai_unit_id] = []
            indices_by_unit[planned_ai_unit_id].append(
                int(attempt["provider_attempt_index"])
            )
        if actual_unit_order != expected_units:
            raise ValueError(
                "paper task/attempt execution plan AI unit inventory mismatch"
            )
        max_attempts = max_attempts_by_condition[condition_id]
        for planned_ai_unit_id in expected_units:
            indices = indices_by_unit.get(planned_ai_unit_id, [])
            if (
                not indices
                or indices != list(range(len(indices)))
                or len(indices) > max_attempts
            ):
                raise ValueError(
                    "paper provider attempt index sequence conflicts with execution plan"
                )


def _planned_max_provider_attempts_per_ai_unit(
    *,
    planned: JsonObject,
    ai_units: list[str],
) -> int:
    upper_bound = planned.get("provider_attempt_upper_bound")
    _require_non_negative_int(upper_bound, "planned provider_attempt_upper_bound")
    request_limits = planned.get("request_limits")
    if not isinstance(request_limits, dict):
        raise ValueError("paper execution plan request_limits is invalid")
    configured = request_limits.get("max_provider_attempts_per_ai_unit")
    if not ai_units:
        if int(upper_bound) != 0 or (
            configured is not None
            and (
                not isinstance(configured, int)
                or isinstance(configured, bool)
                or configured < 1
            )
        ):
            raise ValueError("paper execution plan zero-unit retry budget is invalid")
        return int(configured or 0)
    if configured is None:
        if int(upper_bound) % len(ai_units) != 0:
            raise ValueError("paper execution plan retry budget is not per-unit exact")
        configured = int(upper_bound) // len(ai_units)
    if (
        not isinstance(configured, int)
        or isinstance(configured, bool)
        or configured < 1
        or int(upper_bound) != len(ai_units) * configured
    ):
        raise ValueError("paper execution plan retry budget is invalid")
    return configured


def _planned_ai_unit_binding(
    *,
    planned: JsonObject,
    planned_ai_unit_id: str,
) -> JsonObject | None:
    bindings = planned.get("ai_unit_bindings")
    if not isinstance(bindings, list):
        return None
    for binding in bindings:
        if (
            isinstance(binding, dict)
            and binding.get("planned_ai_unit_id") == planned_ai_unit_id
        ):
            return binding
    return None


def _validate_suite_event_lifecycle(
    *,
    plan: JsonObject,
    suite: JsonObject,
    events: list[JsonObject],
) -> None:
    timestamps = [_event_timestamp(event) for event in events]
    if any(current < previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("paper event order conflicts with recorded_at chronology")

    suite_started = [
        event for event in events if event.get("event_type") == "suite_started"
    ]
    suite_finished = [
        event for event in events if event.get("event_type") == "suite_finished"
    ]
    if len(suite_started) != 1:
        raise ValueError("paper suite lifecycle requires one suite_started event")
    if len(suite_finished) != 1:
        raise ValueError("paper suite lifecycle requires one suite_finished event")
    if events[0] is not suite_started[0] or events[-1] is not suite_finished[0]:
        raise ValueError("paper event order requires suite lifecycle boundaries")

    suite_id = str(plan["suite_id"])
    for event in (*suite_started, *suite_finished):
        if (
            event.get("suite_id") != suite_id
            or event.get("run_id") is not None
            or event.get("task_id") is not None
        ):
            raise ValueError("paper suite event binding is invalid")
    detail = suite_finished[0].get("detail")
    if not isinstance(detail, dict) or detail.get("stop_reason") != suite.get(
        "stop_reason"
    ):
        raise ValueError("paper suite_finished stop_reason mismatch")

    planned_by_run = {str(item["run_id"]): item for item in plan["tasks"]}
    for event in events:
        if event.get("event_type") not in TASK_EVENT_TYPES:
            continue
        planned = planned_by_run.get(str(event.get("run_id") or ""))
        if (
            planned is None
            or event.get("suite_id") != suite_id
            or event.get("task_id") != planned.get("task_id")
        ):
            raise ValueError("paper task event binding is invalid")


def _validate_artifacts(
    *,
    plan: JsonObject,
    runs: list[JsonObject],
    tasks: list[JsonObject],
    attempts: list[JsonObject],
    artifacts: list[JsonObject],
) -> tuple[list[str], dict[str, set[str]]]:
    runs_by_id = {str(run["run_id"]): run for run in runs}
    condition_to_run = {
        str(run["condition_id"]): str(run["run_id"]) for run in runs
    }
    task_id_by_run = {
        condition_to_run[str(task["condition_id"])]: str(task["task_id"])
        for task in tasks
    }
    planned_by_condition = {
        str(item["condition_id"]): item for item in plan["tasks"]
    }
    reasons: list[str] = []
    indexed_refs_by_run: dict[str, set[str]] = {}
    for record in artifacts:
        run_id = str(record.get("run_id"))
        ref = record.get("artifact_ref")
        if (
            run_id not in runs_by_id
            or record.get("task_id") != task_id_by_run.get(run_id)
            or not isinstance(ref, dict)
        ):
            raise ValueError("paper artifact index contains an invalid record")
        ref_key = _canonical_json(ref)
        indexed = indexed_refs_by_run.setdefault(run_id, set())
        if ref_key in indexed:
            raise ValueError("paper artifact index contains a duplicate record")
        indexed.add(ref_key)
        if not _complete_artifact_ref(ref):
            reasons.append(f"incomplete_artifact_ref:{run_id}")
            continue
        try:
            artifact_ref = ArtifactRef.from_dict(ref)
            verified = ArtifactStore(Path(str(runs_by_id[run_id]["artifact_root"]))).verify(
                artifact_ref
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("paper artifact integrity metadata is invalid") from exc
        if not verified:
            raise ValueError(f"paper artifact integrity verification failed: {run_id}")

    attempts_by_condition: dict[str, list[JsonObject]] = {}
    for attempt in attempts:
        attempts_by_condition.setdefault(str(attempt["condition_id"]), []).append(
            attempt
        )
    for task in tasks:
        condition_id = str(task["condition_id"])
        run_id = condition_to_run[condition_id]
        indexed = indexed_refs_by_run.get(run_id, set())
        refs = [
            ref
            for ref in task.get("artifact_refs", [])
            if isinstance(ref, dict)
        ]
        for attempt in attempts_by_condition.get(condition_id, []):
            refs.extend(
                ref
                for field_name in ATTEMPT_REF_FIELDS
                if isinstance((ref := attempt.get(field_name)), dict)
            )
        for ref in refs:
            if _canonical_json(ref) not in indexed:
                raise ValueError("paper artifact reference is missing from index")
    for attempt in attempts:
        run_id = str(attempt["run_id"])
        request_ref = attempt.get("request_ref")
        try:
            store = ArtifactStore(Path(str(runs_by_id[run_id]["artifact_root"])))
            request_body = _read_artifact_json(store=store, ref=request_ref)
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            raise ValueError("paper attempt request artifact is invalid") from exc
        if not isinstance(request_body, dict):
            raise ValueError("paper attempt request artifact is invalid")
        for field_name in ("attempt_id", "task_id", "unit_id"):
            if request_body.get(field_name) != attempt.get(field_name):
                raise ValueError(
                    "paper attempt/execution plan AI unit binding conflicts "
                    f"with request artifact: {field_name}"
                )
        condition_id = str(attempt["condition_id"])
        planned = planned_by_condition[condition_id]
        soft_hints = request_body.get("soft_hints")
        if not isinstance(soft_hints, dict):
            raise ValueError("paper attempt request planned AI unit binding is missing")
        if (
            soft_hints.get("paper_condition_id") != condition_id
            or soft_hints.get("planned_ai_unit_id")
            != attempt.get("planned_ai_unit_id")
            or soft_hints.get("planned_ai_unit_id") not in planned["ai_units"]
            or soft_hints.get("paper_provider_attempt_index")
            != attempt.get("provider_attempt_index")
        ):
            raise ValueError(
                "paper attempt/execution plan AI unit binding conflicts "
                "with request artifact"
            )
        task_unit_snapshot = request_body.get("task_unit_snapshot")
        if (
            not isinstance(task_unit_snapshot, dict)
            or task_unit_snapshot.get("task_id") != attempt.get("task_id")
            or task_unit_snapshot.get("unit_id") != attempt.get("unit_id")
        ):
            raise ValueError(
                "paper attempt protocol unit binding conflicts with request task snapshot"
            )
        expected_binding = _planned_ai_unit_binding(
            planned=planned,
            planned_ai_unit_id=str(attempt["planned_ai_unit_id"]),
        )
        if expected_binding is None:
            raise ValueError("paper unit commitment missing from execution plan")
        try:
            recomputed_binding = build_ai_unit_binding_from_request(
                planned_ai_unit_id=str(attempt["planned_ai_unit_id"]),
                request_body=request_body,
                store=store,
                include_request_artifacts=(
                    expected_binding.get("request_artifact_commitment") is not None
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            raise ValueError(
                "paper unit commitment mismatch: request domain commitment "
                "conflicts with execution plan"
            ) from exc
        if recomputed_binding != expected_binding:
            raise ValueError(
                "paper unit commitment mismatch: request domain commitment "
                "conflicts with execution plan"
            )
        request_id = request_body.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("paper attempt request_id binding is invalid")
        for field_name in ("prompt_package_ref", "execution_instruction_ref"):
            nested_ref = request_body.get(field_name)
            if nested_ref is None:
                continue
            if not isinstance(nested_ref, dict):
                raise ValueError("paper request nested artifact reference is invalid")
            try:
                nested_body = _read_artifact_json(store=store, ref=nested_ref)
            except (
                KeyError,
                TypeError,
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                OSError,
            ) as exc:
                raise ValueError("paper request nested artifact is invalid") from exc
            if (
                nested_body.get("request_id") != request_id
                or nested_body.get("unit_id") != attempt.get("unit_id")
                or (
                    field_name == "prompt_package_ref"
                    and nested_body.get("task_id") != attempt.get("task_id")
                )
            ):
                raise ValueError(
                    "paper attempt protocol unit binding conflicts with request artifact"
                )
    return list(dict.fromkeys(reasons)), indexed_refs_by_run


def _read_artifact_json(*, store: ArtifactStore, ref: Any) -> JsonObject:
    artifact_ref = ArtifactRef.from_dict(ref)
    if not store.verify(artifact_ref):
        raise ValueError("artifact integrity verification failed")
    body = json.loads(
        store.read_bytes(artifact_ref).decode("utf-8")
    )
    if not isinstance(body, dict):
        raise ValueError("artifact JSON body must be an object")
    return body


def _validate_task_attempt_totals(
    *,
    task: JsonObject,
    run: JsonObject,
    planned: JsonObject,
    attempts: list[JsonObject],
) -> None:
    for field_name in (
        "attempt_count",
        "provider_attempt_count",
        "total_tokens",
        "wall_clock_ms",
    ):
        _require_non_negative_int(task.get(field_name), f"task {field_name}")
    _require_non_negative_number(task.get("cost_estimate"), "task cost_estimate")
    if int(task["attempt_count"]) != len(attempts):
        raise ValueError("paper task/attempt count mismatch")
    if int(task["provider_attempt_count"]) != len(attempts):
        raise ValueError("paper task/provider-attempt count mismatch")
    total_tokens = 0
    cost = 0.0
    for attempt in attempts:
        for field_name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latency_ms",
        ):
            _require_non_negative_int(attempt.get(field_name), f"attempt {field_name}")
        _require_non_negative_number(
            attempt.get("cost_estimate"), "attempt cost_estimate"
        )
        if int(attempt["prompt_tokens"]) + int(attempt["completion_tokens"]) != int(
            attempt["total_tokens"]
        ):
            raise ValueError("paper attempt token breakdown mismatch")
        total_tokens += int(attempt["total_tokens"])
        cost += float(attempt["cost_estimate"])
    if int(task["total_tokens"]) != total_tokens:
        raise ValueError("paper task/attempt token total mismatch")
    if not math.isclose(
        float(task["cost_estimate"]), cost, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("paper task/attempt cost total mismatch")
    derived_stage, derived_kind = _derived_failure_evidence(attempts)
    budget_exhausted = str(task.get("root_status")) == "budget_exhausted"
    expected_failure_kind = "budget_limit" if budget_exhausted else derived_kind
    if task.get("failure_stage") != derived_stage:
        raise ValueError("paper task failure stage conflicts with attempt evidence")
    if task.get("failure_kind") != expected_failure_kind:
        raise ValueError("paper task outcome failure kind conflicts with attempt evidence")

    execution_status = str(planned["execution_status"])
    if execution_status == "structured_blocked":
        expected_root_status = "blocked"
        expected_accepted_validity = False
        if attempts or derived_stage is not None or derived_kind is not None:
            raise ValueError("paper structured-blocked task outcome is invalid")
    elif budget_exhausted:
        expected_root_status = "budget_exhausted"
        expected_accepted_validity = False
        if attempts or task.get("failure_stage") is not None or task.get(
            "failure_kind"
        ) != "budget_limit":
            raise ValueError("paper budget-exhausted task outcome is invalid")
    elif derived_stage is None:
        expected_root_status = "completed"
        expected_accepted_validity = True
    else:
        expected_root_status = "failed"
        expected_accepted_validity = False
    if (
        task.get("root_status") != expected_root_status
        or task.get("accepted_validity") is not expected_accepted_validity
    ):
        raise ValueError("paper task outcome conflicts with attempt evidence")

    expected_run_status = _run_status_for_root_status(expected_root_status)
    if run.get("status") != expected_run_status:
        raise ValueError("paper run status conflicts with task outcome")


def _validate_task_event_lifecycle(
    *,
    suite_id: str,
    suite_stop_reason: Any,
    task: JsonObject,
    run: JsonObject,
    planned: JsonObject,
    events: list[JsonObject],
) -> int:
    run_events = [event for event in events if event.get("run_id") == run["run_id"]]
    if any(
        event.get("suite_id") != suite_id
        or event.get("task_id") != task.get("task_id")
        for event in run_events
    ):
        raise ValueError("paper task event binding is invalid")
    starts = [
        event for event in run_events if event.get("event_type") == "task_started"
    ]
    terminals = [
        event
        for event in run_events
        if event.get("event_type")
        in {"task_completed", "task_blocked", "task_budget_exhausted"}
    ]
    root_status = str(task["root_status"])
    if planned["execution_status"] == "structured_blocked":
        if (
            starts
            or len(terminals) != 1
            or terminals[0].get("event_type") != "task_blocked"
        ):
            raise ValueError(
                "paper structured-blocked task event lifecycle is invalid"
            )
        return 0
    if root_status == "budget_exhausted":
        if (
            starts
            or len(terminals) != 1
            or terminals[0].get("event_type") != "task_budget_exhausted"
        ):
            raise ValueError("paper task event lifecycle is invalid")
        detail = terminals[0].get("detail")
        if (
            not isinstance(detail, dict)
            or detail.get("stop_reason") != suite_stop_reason
        ):
            raise ValueError("paper task budget event stop_reason is inconsistent")
        return 0
    if (
        len(starts) != 1
        or len(terminals) != 1
        or terminals[0].get("event_type") != "task_completed"
    ):
        raise ValueError("paper task event lifecycle is invalid")
    detail = terminals[0].get("detail")
    if not isinstance(detail, dict):
        raise ValueError("paper task terminal event detail is invalid")
    expected_detail = {
        "root_status": task["root_status"],
        "provider_attempt_count": task["provider_attempt_count"],
        "total_tokens": task["total_tokens"],
        "cost_estimate": task["cost_estimate"],
    }
    for field_name, expected_value in expected_detail.items():
        actual_value = detail.get(field_name)
        if field_name == "cost_estimate":
            matches = isinstance(actual_value, (int, float)) and math.isclose(
                float(actual_value),
                float(expected_value),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        else:
            matches = actual_value == expected_value
        if not matches:
            raise ValueError("paper task terminal event detail is inconsistent")
    started_at = _event_timestamp(starts[0])
    terminal_at = _event_timestamp(terminals[0])
    if terminal_at < started_at:
        raise ValueError("paper task terminal event precedes task start")
    delta = terminal_at - started_at
    total_microseconds = (
        (delta.days * 86400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )
    return (total_microseconds + 500) // 1000


def _load_secret_scan_attestation(
    *,
    root: Path,
    suite: JsonObject,
    runs: list[JsonObject],
    tasks: list[JsonObject],
) -> tuple[set[str], JsonObject]:
    path = root / "audit" / "suite_secret_scan_attestation.json"
    if not path.is_file():
        return set(), {}
    attestation = _read_json(path)
    if (
        attestation.get("schema_version")
        != "tokenshare.paper_suite_secret_scan_attestation.v1"
    ):
        raise ValueError("paper suite secret scan attestation schema is invalid")
    stored_digest = attestation.get("attestation_digest")
    without_digest = {
        key: value
        for key, value in attestation.items()
        if key != "attestation_digest"
    }
    if stored_digest != digest_json(without_digest):
        raise ValueError("paper suite secret scan attestation digest is invalid")
    evidence_manifest = _read_json(root / "evidence_manifest.json")
    if attestation.get("source_evidence_manifest_digest") != evidence_manifest.get(
        "evidence_manifest_digest"
    ):
        raise ValueError("paper suite secret scan attestation is stale")
    if attestation.get("suite_id") != suite.get("suite_id"):
        raise ValueError("paper suite secret scan attestation suite mismatch")
    if attestation.get("provider_calls_made_for_attestation") != 0:
        raise ValueError("paper suite secret scan attestation made provider calls")

    expected_reasons = {
        "secret_scan_failed",
        "adapter_task_not_paper_eligible",
        "adapter_eligibility_report_not_paper_eligible",
    }
    if set(attestation.get("superseded_adapter_ineligibility_reasons", ())) != (
        expected_reasons
    ):
        raise ValueError("paper suite secret scan attestation reason scope is invalid")
    tasks_by_condition = {str(task["condition_id"]): task for task in tasks}
    expected_run_ids: list[str] = []
    for run in runs:
        transport = run.get("transport_evidence")
        repairable = (
            run.get("execution_status") == "executable"
            and run.get("paper_eligible") is False
            and isinstance(transport, dict)
            and transport.get("real_transport") is True
            and transport.get("transport_kind") == "ai_api"
            and set(run.get("ineligibility_reasons", ())) == expected_reasons
        )
        if repairable:
            task = tasks_by_condition.get(str(run["condition_id"]))
            if not isinstance(task, dict) or task.get("paper_eligible") is not False:
                raise ValueError(
                    "paper suite secret scan attestation task state is inconsistent"
                )
            expected_run_ids.append(str(run["run_id"]))
    attested_run_ids = attestation.get("attested_run_ids")
    if (
        not isinstance(attested_run_ids, list)
        or attested_run_ids != expected_run_ids
        or len(set(attested_run_ids)) != len(attested_run_ids)
    ):
        raise ValueError("paper suite secret scan attestation run scope is invalid")
    if not attested_run_ids:
        raise ValueError("paper suite secret scan attestation has empty run scope")

    secret_scan = attestation.get("secret_scan")
    if not isinstance(secret_scan, dict):
        raise ValueError("paper suite secret scan attestation report is missing")
    if (
        secret_scan.get("schema_version") != "tokenshare.paper_secret_scan.v1"
        or secret_scan.get("passed") is not True
        or int(secret_scan.get("secret_checked_count", 0)) <= 0
        or int(secret_scan.get("files_scanned", 0)) <= 0
        or int(secret_scan.get("leak_count", -1)) != 0
    ):
        raise ValueError("paper suite secret scan attestation report is invalid")
    return set(str(run_id) for run_id in attested_run_ids), _json_copy(secret_scan)


def _task_eligibility_reasons(
    *,
    task: JsonObject,
    run: JsonObject,
    planned: JsonObject,
    attempts: list[JsonObject],
    indexed_refs: set[str],
    suite_secret_scan_attested: bool,
) -> list[str]:
    run_id = str(run["run_id"])
    reasons: list[str] = []
    if planned["execution_status"] == "structured_blocked":
        if attempts or int(task.get("provider_attempt_count", -1)) != 0:
            reasons.append(f"structured_blocked_has_attempts:{run_id}")
        return reasons

    transport = run.get("transport_evidence")
    if not isinstance(transport, dict):
        reasons.append(f"missing_transport_evidence:{run_id}")
    elif transport.get("real_transport") is not True or transport.get(
        "transport_kind"
    ) != "ai_api":
        reasons.append(f"non_real_transport:{run_id}")
    if run.get("paper_eligible") is not True and not suite_secret_scan_attested:
        reasons.append(f"run_not_paper_eligible:{run_id}")
    if task.get("paper_eligible") is not True and not suite_secret_scan_attested:
        reasons.append(f"task_not_paper_eligible:{task['task_id']}")
    if not attempts:
        reasons.append(f"missing_provider_attempt:{run_id}")
    for attempt in attempts:
        attempt_id = str(attempt.get("attempt_id") or "unknown")
        for field_name in REQUIRED_ATTEMPT_REF_FIELDS:
            ref = attempt.get(field_name)
            if not _complete_artifact_ref(ref):
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
            elif _canonical_json(ref) not in indexed_refs:
                reasons.append(f"attempt:{attempt_id}:unindexed_{field_name}")
        if not (
            _complete_artifact_ref(attempt.get("parsed_output_ref"))
            or _complete_artifact_ref(attempt.get("parse_failure_ref"))
        ):
            reasons.append(
                f"attempt:{attempt_id}:missing_parsed_output_or_parse_failure_ref"
            )
        for field_name in ("provider", "model", "entry_id"):
            if not isinstance(attempt.get(field_name), str) or not attempt[field_name]:
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    return reasons


def _task_metric_row(
    *,
    task: JsonObject,
    run: JsonObject,
    planned: JsonObject,
    attempts: list[JsonObject],
    wall_clock_ms: int,
    suite_secret_scan_attested: bool,
) -> JsonObject:
    statuses = [str(attempt.get("attempt_status")) for attempt in attempts]
    attempted = planned["execution_status"] == "executable"
    return {
        "schema_version": "tokenshare.paper_task_metrics.v1",
        "condition_id": task["condition_id"],
        "run_id": run["run_id"],
        "case_id": run["case_id"],
        "task_id": task["task_id"],
        "domain": task["domain"],
        "difficulty": task["difficulty"],
        "paper_difficulty": task.get("paper_difficulty") or task["difficulty"],
        "topic_family": task.get("topic_family"),
        "execution_status": planned["execution_status"],
        "root_status": task["root_status"],
        "attempted": attempted,
        "completed": task["root_status"] == "completed",
        "accepted_validity": bool(task.get("accepted_validity")),
        "failure_stage": task.get("failure_stage"),
        "failure_kind": task.get("failure_kind"),
        "attempt_count": len(attempts),
        "provider_attempt_count": len(attempts),
        "parser_failure_count": statuses.count("parse_failed"),
        "verifier_rejection_count": statuses.count("verification_rejected"),
        "checker_rejection_count": statuses.count("checker_rejected"),
        "provider_error_count": statuses.count("provider_error"),
        "prompt_tokens": sum(int(item["prompt_tokens"]) for item in attempts),
        "completion_tokens": sum(
            int(item["completion_tokens"]) for item in attempts
        ),
        "total_tokens": sum(int(item["total_tokens"]) for item in attempts),
        "provider_latency_ms": sum(int(item["latency_ms"]) for item in attempts),
        "wall_clock_ms": wall_clock_ms,
        "cost_estimate": round(
            sum(float(item["cost_estimate"]) for item in attempts), 12
        ),
        "artifact_ref_count": sum(
            1
            for attempt in attempts
            for field_name in ATTEMPT_REF_FIELDS
            if isinstance(attempt.get(field_name), dict)
        ),
        "paper_eligible": bool(task.get("paper_eligible"))
        or suite_secret_scan_attested,
        "pilot_only": True,
    }


def _aggregate_rows(rows: list[JsonObject]) -> JsonObject:
    return {
        "planned_root_count": len(rows),
        "attempted_root_count": sum(bool(row["attempted"]) for row in rows),
        "completed_root_count": sum(bool(row["completed"]) for row in rows),
        "failed_root_count": sum(row["root_status"] == "failed" for row in rows),
        "blocked_root_count": sum(row["root_status"] == "blocked" for row in rows),
        "accepted_valid_root_count": sum(
            bool(row["accepted_validity"]) for row in rows
        ),
        "attempt_count": sum(int(row["attempt_count"]) for row in rows),
        "provider_attempt_count": sum(
            int(row["provider_attempt_count"]) for row in rows
        ),
        "parser_failure_count": sum(
            int(row["parser_failure_count"]) for row in rows
        ),
        "verifier_rejection_count": sum(
            int(row["verifier_rejection_count"]) for row in rows
        ),
        "checker_rejection_count": sum(
            int(row["checker_rejection_count"]) for row in rows
        ),
        "provider_error_count": sum(
            int(row["provider_error_count"]) for row in rows
        ),
        "prompt_tokens": sum(int(row["prompt_tokens"]) for row in rows),
        "completion_tokens": sum(int(row["completion_tokens"]) for row in rows),
        "total_tokens": sum(int(row["total_tokens"]) for row in rows),
        "provider_latency_ms": sum(
            int(row["provider_latency_ms"]) for row in rows
        ),
        "wall_clock_ms": sum(int(row["wall_clock_ms"]) for row in rows),
        "cost_estimate": round(sum(float(row["cost_estimate"]) for row in rows), 12),
    }


def _summary_rows(
    rows: list[JsonObject], *, paper_eligible: bool
) -> list[JsonObject]:
    groups: list[tuple[str, str, str, list[JsonObject]]] = [
        ("overall", "all", "all", rows)
    ]
    keys = sorted(
        {(str(row["domain"]), str(row["paper_difficulty"])) for row in rows}
    )
    for domain, paper_difficulty in keys:
        groups.append(
            (
                "domain_paper_difficulty",
                domain,
                paper_difficulty,
                [
                    row
                    for row in rows
                    if row["domain"] == domain
                    and row["paper_difficulty"] == paper_difficulty
                ],
            )
        )
    result: list[JsonObject] = []
    for scope, domain, paper_difficulty, group_rows in groups:
        totals = _aggregate_rows(group_rows)
        denominator = int(totals["attempted_root_count"])
        result.append(
            {
                "schema_version": "tokenshare.paper_summary_metrics.v1",
                "summary_scope": scope,
                "domain": domain,
                "paper_difficulty": paper_difficulty,
                **totals,
                "completion_rate": _rate(
                    int(totals["completed_root_count"]), denominator
                ),
                "accepted_validity_rate": _rate(
                    int(totals["accepted_valid_root_count"]), denominator
                ),
                "failure_stage_counts": _failure_stage_counts(group_rows),
                "paper_eligible": paper_eligible,
                "pilot_only": True,
            }
        )
    return result


def _validate_suite_totals(
    *,
    suite: JsonObject,
    plan: JsonObject,
    totals: JsonObject,
    runs: list[JsonObject],
    tasks: list[JsonObject],
    attempts: list[JsonObject],
) -> None:
    expected = {
        "blocked_run_count": totals["blocked_root_count"],
        "provider_attempt_count": totals["provider_attempt_count"],
        "total_tokens": totals["total_tokens"],
    }
    for field_name, value in expected.items():
        if int(suite.get(field_name, -1)) != int(value):
            raise ValueError(f"paper suite manifest {field_name} total mismatch")
    if not math.isclose(
        float(suite.get("total_cost_estimate", -1.0)),
        float(totals["cost_estimate"]),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("paper suite manifest cost total mismatch")
    derived_stop_reason = _derive_suite_stop_reason_from_evidence(
        plan=plan,
        tasks=tasks,
    )
    if suite.get("stop_reason") != derived_stop_reason:
        raise ValueError(
            "paper suite stop_reason conflicts with plan/task evidence"
        )
    expected_status = _suite_status_from_task_outcomes(
        tasks=tasks,
        stop_reason=derived_stop_reason,
    )
    if suite.get("status") != expected_status:
        raise ValueError("paper suite manifest status conflicts with task outcomes")

    _require_non_negative_int(
        suite.get("provider_calls_made"),
        "suite provider_calls_made",
    )
    _require_non_negative_int(
        suite.get("replayed_run_count"),
        "suite replayed_run_count",
    )
    historical_attempts = len(attempts)
    current_calls = int(suite["provider_calls_made"])
    replayed_runs = int(suite["replayed_run_count"])
    if replayed_runs == 0:
        if current_calls != historical_attempts:
            raise ValueError(
                "paper suite current provider calls conflict with historical attempts"
            )
    elif replayed_runs != len(runs) or current_calls != 0:
        raise ValueError("paper suite replay/current provider call totals mismatch")


def _derive_suite_stop_reason_from_evidence(
    *,
    plan: JsonObject,
    tasks: list[JsonObject],
) -> str | None:
    """按 runner 的 stop-after-current-task 规则从计划和 task evidence 反推。"""

    hard_limits = plan.get("hard_limits")
    if not isinstance(hard_limits, dict):
        raise ValueError("paper execution plan hard limits are invalid")
    _require_non_negative_int(
        hard_limits.get("provider_attempt_limit"),
        "execution plan provider_attempt_limit",
    )
    _require_non_negative_int(
        hard_limits.get("token_limit"),
        "execution plan token_limit",
    )
    _require_non_negative_number(
        hard_limits.get("cost_limit"),
        "execution plan cost_limit",
    )
    tasks_by_condition = {
        str(task["condition_id"]): task for task in tasks
    }
    used_provider_attempts = 0
    used_tokens = 0
    used_cost = 0.0
    stop_reason: str | None = None
    for planned in plan["tasks"]:
        task = tasks_by_condition[str(planned["condition_id"])]
        if planned["execution_status"] == "structured_blocked":
            continue
        if stop_reason is not None:
            if task.get("root_status") != "budget_exhausted":
                raise ValueError(
                    "paper task outcomes conflict with stop-after-current-task evidence"
                )
            continue

        _require_non_negative_int(
            planned.get("provider_attempt_upper_bound"),
            "planned task provider_attempt_upper_bound",
        )
        _require_non_negative_int(
            planned.get("token_upper_bound"),
            "planned task token_upper_bound",
        )
        _require_non_negative_number(
            planned.get("cost_upper_bound"),
            "planned task cost_upper_bound",
        )
        if (
            used_provider_attempts
            + int(planned["provider_attempt_upper_bound"])
            > int(hard_limits["provider_attempt_limit"])
        ):
            stop_reason = "provider_attempt_limit"
        elif (
            used_tokens + int(planned["token_upper_bound"])
            > int(hard_limits["token_limit"])
        ):
            stop_reason = "token_limit"
        elif (
            used_cost + float(planned["cost_upper_bound"])
            > float(hard_limits["cost_limit"]) + 1e-12
        ):
            stop_reason = "cost_limit"
        if stop_reason is not None:
            if task.get("root_status") != "budget_exhausted":
                raise ValueError(
                    "paper task outcome conflicts with planned hard-limit evidence"
                )
            continue
        if task.get("root_status") == "budget_exhausted":
            raise ValueError(
                "paper task budget outcome lacks planned hard-limit evidence"
            )

        used_provider_attempts += int(task["provider_attempt_count"])
        used_tokens += int(task["total_tokens"])
        used_cost += float(task["cost_estimate"])
        if int(task["provider_attempt_count"]) > int(
            planned["provider_attempt_upper_bound"]
        ):
            stop_reason = "observed_provider_attempts_exceeded_approved_profile"
        elif int(task["total_tokens"]) > int(planned["token_upper_bound"]):
            stop_reason = "observed_tokens_exceeded_approved_profile"
        elif float(task["cost_estimate"]) > float(
            planned["cost_upper_bound"]
        ) + 1e-12:
            stop_reason = "observed_cost_exceeded_approved_profile"
    return stop_reason


def _suite_status_from_task_outcomes(
    *, tasks: list[JsonObject], stop_reason: Any
) -> str:
    root_statuses = [str(task.get("root_status")) for task in tasks]
    if stop_reason is not None or "budget_exhausted" in root_statuses:
        return "budget_exhausted"
    if any(status not in {"completed", "blocked"} for status in root_statuses):
        return "completed_with_failures"
    if "blocked" in root_statuses:
        return "completed_with_failures"
    return "completed"


def _run_status_for_root_status(root_status: str) -> str:
    if root_status == "completed":
        return "completed"
    if root_status == "blocked":
        return "blocked"
    if root_status == "budget_exhausted":
        return "budget_exhausted"
    return "failed"


def _derived_failure_evidence(
    attempts: list[JsonObject],
) -> tuple[str | None, str | None]:
    statuses = {str(attempt.get("attempt_status")) for attempt in attempts}
    allowed = {
        "succeeded",
        "model_identity_mismatch",
        "provider_error",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "lease_expired",
        "late_rejected",
        "worker_died",
        "cancelled_by_budget",
    }
    if not statuses.issubset(allowed):
        raise ValueError("paper attempt status is invalid")
    if "model_identity_mismatch" in statuses:
        return "audit", "model_identity_mismatch"
    if "parse_failed" in statuses:
        return "parse", "parse_failure"
    if "provider_error" in statuses:
        return "provider", "provider_error"
    if "verification_rejected" in statuses:
        return "verification", "verifier_rejected"
    if "checker_rejected" in statuses:
        return "checker", "checker_rejected"
    if "lease_expired" in statuses:
        return "request", "lease_expired"
    if "late_rejected" in statuses:
        return "canonical", "late_submission"
    if "worker_died" in statuses:
        return "request", "internal_error"
    if "cancelled_by_budget" in statuses:
        return "request", "budget_limit"
    return None, None


def _failure_stage_counts(rows: list[JsonObject]) -> JsonObject:
    counts: JsonObject = {}
    for row in rows:
        stage = row.get("failure_stage")
        if isinstance(stage, str) and stage:
            counts[stage] = int(counts.get(stage, 0)) + 1
    return dict(sorted(counts.items()))


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 12)


def _require_unique(
    records: list[JsonObject], field_name: str, label: str
) -> None:
    values = [str(record.get(field_name) or "") for record in records]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"paper {label} evidence is missing or duplicate")


def _require_unique_fields(
    records: list[JsonObject],
    field_names: tuple[str, ...],
    label: str,
) -> None:
    keys = [
        tuple(record.get(field_name) for field_name in field_names)
        for record in records
    ]
    if any(any(value is None or value == "" for value in key) for key in keys):
        raise ValueError(f"paper {label} evidence is missing or duplicate")
    canonical_keys = [
        json.dumps(key, ensure_ascii=False, separators=(",", ":")) for key in keys
    ]
    if len(canonical_keys) != len(set(canonical_keys)):
        raise ValueError(f"paper {label} evidence is missing or duplicate")


def _require_bound_fields(
    *,
    actual: JsonObject,
    expected: JsonObject,
    field_names: tuple[str, ...],
    label: str,
) -> None:
    mismatches = [
        field_name
        for field_name in field_names
        if actual.get(field_name) != expected.get(field_name)
    ]
    if mismatches:
        raise ValueError(
            f"paper {label} fields mismatch: {','.join(mismatches)}"
        )


def _event_timestamp(event: JsonObject) -> datetime:
    value = event.get("recorded_at")
    if not isinstance(value, str) or not value:
        raise ValueError("paper event recorded_at is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("paper event recorded_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("paper event recorded_at must include a timezone")
    return parsed


def _require_non_negative_int(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"paper {label} must be a non-negative integer")


def _require_non_negative_number(value: Any, label: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"paper {label} must be finite and non-negative")


def _complete_artifact_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "artifact_id",
        "artifact_type",
        "uri",
        "content_hash",
        "size_bytes",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "created_at",
    }
    return required.issubset(value)


def _read_json(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"expected JSON object: {path}")
    return body


def _read_jsonl(path: Path) -> list[JsonObject]:
    records: list[JsonObject] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        body = json.loads(line)
        if not isinstance(body, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        records.append(body)
    return records


def _canonical_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
