from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_metrics import recompute_exp1_pilot_metrics
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_unit_commitments import (
    factorization_range_plugin_payload,
)
from tokenshare.storage.artifacts import ArtifactStore


def test_paper_metrics_and_report_are_exported_from_experiments_package() -> None:
    import tokenshare.experiments as experiments

    assert experiments.recompute_exp1_pilot_metrics is recompute_exp1_pilot_metrics
    assert callable(experiments.write_exp1_pilot_report)


def test_recompute_metrics_uses_attempt_event_and_artifact_evidence(
    complete_paper_evidence_suite,
) -> None:
    result = recompute_exp1_pilot_metrics(
        complete_paper_evidence_suite["root"]
    )

    assert result.paper_eligible is True
    assert result.pilot_only is True
    assert result.totals == {
        "planned_root_count": 3,
        "attempted_root_count": 2,
        "completed_root_count": 1,
        "failed_root_count": 1,
        "blocked_root_count": 1,
        "accepted_valid_root_count": 1,
        "attempt_count": 2,
        "provider_attempt_count": 2,
        "parser_failure_count": 0,
        "verifier_rejection_count": 0,
        "checker_rejection_count": 1,
        "provider_error_count": 0,
        "prompt_tokens": 18,
        "completion_tokens": 6,
        "total_tokens": 24,
        "provider_latency_ms": 12,
        "wall_clock_ms": 50,
        "cost_estimate": 0.03,
    }
    assert [row["domain"] for row in result.task_metrics] == [
        "factorization",
        "lean_proof",
        "lean_proof",
    ]
    assert result.task_metrics[1]["failure_stage"] == "checker"
    assert result.task_metrics[1]["checker_rejection_count"] == 1
    assert result.task_metrics[2]["provider_attempt_count"] == 0
    assert result.task_metrics[0]["provider_latency_ms"] == 5
    assert result.task_metrics[0]["wall_clock_ms"] == 20
    assert result.task_metrics[1]["provider_latency_ms"] == 7
    assert result.task_metrics[1]["wall_clock_ms"] == 30
    assert {row["summary_scope"] for row in result.summary_rows} == {
        "overall",
        "domain_paper_difficulty",
    }


def test_recompute_metrics_rejects_tampered_manifest_evidence(
    complete_paper_evidence_suite,
) -> None:
    path = complete_paper_evidence_suite["root"] / "per_task_results.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["total_tokens"] = 999
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="evidence manifest"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_incomplete_rehashed_evidence(
    complete_paper_evidence_suite,
) -> None:
    path = complete_paper_evidence_suite["root"] / "per_attempt_results.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n", encoding="utf-8")
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="task/attempt.*(count|inventory)"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_marks_scripted_transport_ineligible(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    runs_path = root / "run_results.jsonl"
    runs = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()]
    runs[0]["transport_evidence"] = {
        "real_transport": False,
        "transport_kind": "scripted",
    }
    runs[0]["paper_eligible"] = False
    runs_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in runs),
        encoding="utf-8",
    )
    complete_paper_evidence_suite["refresh_manifest"]()

    result = recompute_exp1_pilot_metrics(root)

    assert result.paper_eligible is False
    assert "non_real_transport:factor_run" in result.ineligibility_reasons


def test_recompute_metrics_rejects_tampered_artifact_bytes(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    run = json.loads((root / "run_results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    attempt = json.loads(
        (root / "per_attempt_results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    (Path(run["artifact_root"]) / attempt["raw_output_ref"]["uri"]).write_bytes(
        b"tampered"
    )

    with pytest.raises(ValueError, match="artifact integrity"):
        recompute_exp1_pilot_metrics(root)


@pytest.mark.parametrize(
    ("record_index", "field_name", "tampered_value"),
    [
        (0, "domain", "lean_proof"),
        (0, "difficulty", "hard"),
        (0, "paper_difficulty", "hard"),
        (0, "task_id", "tampered_task_id"),
        (1, "topic_family", "function_set"),
        (0, "repeat_id", 9),
    ],
)
def test_recompute_metrics_rejects_task_fields_not_bound_to_execution_plan(
    complete_paper_evidence_suite,
    record_index,
    field_name,
    tampered_value,
) -> None:
    records = _read_jsonl(complete_paper_evidence_suite, "per_task_results.jsonl")
    records[record_index][field_name] = tampered_value
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_task_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="task.*execution plan"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("run_id", "tampered_run_id"),
        ("case_id", "tampered_case_id"),
        ("execution_status", "structured_blocked"),
        ("repeat_id", 9),
    ],
)
def test_recompute_metrics_rejects_run_fields_not_bound_to_execution_plan(
    complete_paper_evidence_suite,
    field_name,
    tampered_value,
) -> None:
    records = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    records[0][field_name] = tampered_value
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "run_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="run.*execution plan"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("run_id", "lean_run"),
        ("task_id", "paper_lean_lean_easy_01"),
        ("unit_id", "unplanned_ai_unit"),
        ("repeat_id", 9),
    ],
)
def test_recompute_metrics_rejects_attempt_fields_not_bound_to_planned_ai_unit(
    complete_paper_evidence_suite,
    field_name,
    tampered_value,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    records[0][field_name] = tampered_value
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="attempt.*execution plan"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_attempt_condition_binding_swap(
    complete_paper_evidence_suite,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    records[0]["condition_id"], records[1]["condition_id"] = (
        records[1]["condition_id"],
        records[0]["condition_id"],
    )
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="attempt.*execution plan"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_coordinated_unplanned_unit_and_request_tamper(
    complete_paper_evidence_suite,
) -> None:
    """重签 request/ref/manifest 也不能把计划外 AI unit 混入论文 evidence。"""

    root: Path = complete_paper_evidence_suite["root"]
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    runs = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    artifacts = _read_jsonl(
        complete_paper_evidence_suite,
        "artifacts/artifact_index.jsonl",
    )
    attempt = attempts[0]
    old_ref = dict(attempt["request_ref"])
    tampered_unit_id = "unplanned_coordinated_ai_unit"
    attempt["unit_id"] = tampered_unit_id

    run = next(item for item in runs if item["run_id"] == attempt["run_id"])
    request_path = Path(run["artifact_root"]) / old_ref["uri"]
    request_body = json.loads(request_path.read_text(encoding="utf-8"))
    request_body["unit_id"] = tampered_unit_id
    request_bytes = json.dumps(
        request_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request_path.write_bytes(request_bytes)

    new_ref = dict(old_ref)
    new_ref["content_hash"] = "sha256:" + hashlib.sha256(request_bytes).hexdigest()
    new_ref["size_bytes"] = len(request_bytes)
    attempt["request_ref"] = new_ref
    artifact_record = next(
        item
        for item in artifacts
        if item["run_id"] == attempt["run_id"]
        and item["artifact_ref"]["artifact_id"] == old_ref["artifact_id"]
    )
    artifact_record["artifact_ref"] = new_ref

    _write_jsonl(
        root / "per_attempt_results.jsonl",
        attempts,
    )
    _write_jsonl(
        root / "artifacts" / "artifact_index.jsonl",
        artifacts,
    )
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="AI unit|unit binding|unit commitment"):
        recompute_exp1_pilot_metrics(root)


def test_recompute_metrics_rejects_full_protocol_unit_payload_substitution(
    complete_paper_evidence_suite,
) -> None:
    """attempt/request/snapshot/prompt/instruction/index/manifest 全部重签也必须拒绝。"""

    root: Path = complete_paper_evidence_suite["root"]
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    runs = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    artifacts = _read_jsonl(
        complete_paper_evidence_suite,
        "artifacts/artifact_index.jsonl",
    )
    attempt = attempts[0]
    old_request_ref = dict(attempt["request_ref"])
    run = next(item for item in runs if item["run_id"] == attempt["run_id"])
    store = ArtifactStore(Path(run["artifact_root"]))
    request_path = Path(run["artifact_root"]) / old_request_ref["uri"]
    request_body = json.loads(request_path.read_text(encoding="utf-8"))
    replacement_unit_id = "coordinated_replacement_protocol_unit"
    replacement_payload = {
        "schema_version": "factorization.factor_search_range_input.v1",
        "target_n": "99991",
        "range_start": "97",
        "range_end": "101",
        "coverage_id": "coverage:replacement",
        "child_index": 0,
        "child_count": 1,
        "partition_params_digest": "sha256:" + "9" * 64,
    }
    replacement_payload["range_digest"] = digest_json(replacement_payload)

    attempt["unit_id"] = replacement_unit_id
    request_body["unit_id"] = replacement_unit_id
    request_body["task_unit_snapshot"] = {
        **dict(request_body["task_unit_snapshot"]),
        "unit_id": replacement_unit_id,
        "plugin_payload": factorization_range_plugin_payload(
            case={"case_id": "factor_easy_01"},
            range_input_body=replacement_payload,
        ),
    }
    request_body["input_artifact_refs"] = {
        "range_input": _save_test_artifact(
            store=store,
            artifact_id="coordinated_replacement_range_input",
            artifact_type="FactorSearchRangeInput",
            body=replacement_payload,
        ),
    }
    request_body["execution_instruction_ref"] = _save_test_artifact(
        store=store,
        artifact_id="coordinated_replacement_instruction",
        artifact_type="ExecutionInstruction",
        body={
            "schema_version": "factorization.factor_search_instruction.v1",
            "request_id": request_body["request_id"],
            "task_id": attempt["task_id"],
            "unit_id": replacement_unit_id,
            "range_start": replacement_payload["range_start"],
            "range_end": replacement_payload["range_end"],
        },
    )
    request_body["prompt_package_ref"] = _save_test_artifact(
        store=store,
        artifact_id="coordinated_replacement_prompt",
        artifact_type="PromptPackage",
        body={
            "schema_version": "phase3.prompt_package.v1",
            "request_id": request_body["request_id"],
            "task_id": attempt["task_id"],
            "unit_id": replacement_unit_id,
            "range_payload_digest": digest_json(replacement_payload),
        },
    )
    request_bytes = json.dumps(
        request_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request_path.write_bytes(request_bytes)
    new_request_ref = dict(old_request_ref)
    new_request_ref["content_hash"] = "sha256:" + hashlib.sha256(
        request_bytes
    ).hexdigest()
    new_request_ref["size_bytes"] = len(request_bytes)
    attempt["request_ref"] = new_request_ref

    indexed_request = next(
        item
        for item in artifacts
        if item["run_id"] == attempt["run_id"]
        and item["artifact_ref"]["artifact_id"] == old_request_ref["artifact_id"]
    )
    indexed_request["artifact_ref"] = new_request_ref
    for nested_ref in (
        request_body["input_artifact_refs"]["range_input"],
        request_body["execution_instruction_ref"],
        request_body["prompt_package_ref"],
    ):
        artifacts.append(
            {
                "schema_version": "tokenshare.paper_artifact_index_record.v1",
                "run_id": attempt["run_id"],
                "task_id": attempt["task_id"],
                "artifact_ref": nested_ref,
            }
        )

    _write_jsonl(root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(root / "artifacts" / "artifact_index.jsonl", artifacts)
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="unit commitment|domain commitment"):
        recompute_exp1_pilot_metrics(root)


def test_recompute_metrics_rejects_payload_only_replacement_with_legal_plan_id(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    runs = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    artifacts = _read_jsonl(
        complete_paper_evidence_suite,
        "artifacts/artifact_index.jsonl",
    )
    attempt = attempts[0]
    old_request_ref = dict(attempt["request_ref"])
    run = next(item for item in runs if item["run_id"] == attempt["run_id"])
    store = ArtifactStore(Path(run["artifact_root"]))
    request_path = Path(run["artifact_root"]) / old_request_ref["uri"]
    request_body = json.loads(request_path.read_text(encoding="utf-8"))
    replacement_payload = {
        "schema_version": "factorization.factor_search_range_input.v1",
        "target_n": "391",
        "range_start": "17",
        "range_end": "23",
        "coverage_id": "coverage:payload-only",
        "child_index": 0,
        "child_count": 1,
        "partition_params_digest": "sha256:" + "8" * 64,
    }
    replacement_payload["range_digest"] = digest_json(replacement_payload)
    range_ref = _save_test_artifact(
        store=store,
        artifact_id="payload_only_replacement_range_input",
        artifact_type="FactorSearchRangeInput",
        body=replacement_payload,
    )
    request_body["input_artifact_refs"] = {"range_input": range_ref}
    request_body["task_unit_snapshot"]["plugin_payload"] = (
        factorization_range_plugin_payload(
            case={"case_id": "factor_easy_01"},
            range_input_body=replacement_payload,
        )
    )
    _rewrite_request_artifact(
        root=root,
        attempt=attempt,
        artifacts=artifacts,
        request_path=request_path,
        old_request_ref=old_request_ref,
        request_body=request_body,
    )
    artifacts.append(
        {
            "schema_version": "tokenshare.paper_artifact_index_record.v1",
            "run_id": attempt["run_id"],
            "task_id": attempt["task_id"],
            "artifact_ref": range_ref,
        }
    )

    _write_jsonl(root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(root / "artifacts" / "artifact_index.jsonl", artifacts)
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="unit commitment|domain commitment"):
        recompute_exp1_pilot_metrics(root)


def test_recompute_metrics_rejects_prompt_and_instruction_resigning(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    runs = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    artifacts = _read_jsonl(
        complete_paper_evidence_suite,
        "artifacts/artifact_index.jsonl",
    )
    attempt = attempts[0]
    old_request_ref = dict(attempt["request_ref"])
    run = next(item for item in runs if item["run_id"] == attempt["run_id"])
    store = ArtifactStore(Path(run["artifact_root"]))
    request_path = Path(run["artifact_root"]) / old_request_ref["uri"]
    request_body = json.loads(request_path.read_text(encoding="utf-8"))
    request_body["prompt_package_ref"] = _save_test_artifact(
        store=store,
        artifact_id="resigned_prompt_same_unit",
        artifact_type="PromptPackage",
        body={
            "schema_version": "phase3.prompt_package.v1",
            "request_id": request_body["request_id"],
            "task_id": attempt["task_id"],
            "unit_id": attempt["unit_id"],
            "prompt_contract": "resigned-after-approval",
        },
    )
    request_body["execution_instruction_ref"] = _save_test_artifact(
        store=store,
        artifact_id="resigned_instruction_same_unit",
        artifact_type="ExecutionInstruction",
        body={
            "schema_version": "phase3.execution_instruction.v1",
            "request_id": request_body["request_id"],
            "task_id": attempt["task_id"],
            "unit_id": attempt["unit_id"],
            "instruction_contract": "resigned-after-approval",
        },
    )
    _rewrite_request_artifact(
        root=root,
        attempt=attempt,
        artifacts=artifacts,
        request_path=request_path,
        old_request_ref=old_request_ref,
        request_body=request_body,
    )
    for nested_ref in (
        request_body["prompt_package_ref"],
        request_body["execution_instruction_ref"],
    ):
        artifacts.append(
            {
                "schema_version": "tokenshare.paper_artifact_index_record.v1",
                "run_id": attempt["run_id"],
                "task_id": attempt["task_id"],
                "artifact_ref": nested_ref,
            }
        )

    _write_jsonl(root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(root / "artifacts" / "artifact_index.jsonl", artifacts)
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="unit commitment|request artifact"):
        recompute_exp1_pilot_metrics(root)


def test_recompute_metrics_rejects_actual_payload_swap_between_legal_units(
    complete_paper_evidence_suite,
) -> None:
    root: Path = complete_paper_evidence_suite["root"]
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    runs = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    artifacts = _read_jsonl(
        complete_paper_evidence_suite,
        "artifacts/artifact_index.jsonl",
    )
    first, second = attempts[0], attempts[1]
    run_by_id = {item["run_id"]: item for item in runs}
    first_path = Path(run_by_id[first["run_id"]]["artifact_root"]) / first[
        "request_ref"
    ]["uri"]
    second_path = Path(run_by_id[second["run_id"]]["artifact_root"]) / second[
        "request_ref"
    ]["uri"]
    first_body = json.loads(first_path.read_text(encoding="utf-8"))
    second_body = json.loads(second_path.read_text(encoding="utf-8"))
    first_inputs = dict(first_body["input_artifact_refs"])
    first_body["input_artifact_refs"] = dict(second_body["input_artifact_refs"])
    second_body["input_artifact_refs"] = first_inputs

    _rewrite_request_artifact(
        root=root,
        attempt=first,
        artifacts=artifacts,
        request_path=first_path,
        old_request_ref=dict(first["request_ref"]),
        request_body=first_body,
    )
    _rewrite_request_artifact(
        root=root,
        attempt=second,
        artifacts=artifacts,
        request_path=second_path,
        old_request_ref=dict(second["request_ref"]),
        request_body=second_body,
    )

    _write_jsonl(root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(root / "artifacts" / "artifact_index.jsonl", artifacts)
    complete_paper_evidence_suite["refresh_manifest"]()

    with pytest.raises(ValueError, match="unit commitment|domain commitment"):
        recompute_exp1_pilot_metrics(root)


def test_recompute_metrics_rejects_provider_attempt_index_outside_planned_sequence(
    complete_paper_evidence_suite,
) -> None:
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    attempts[0]["provider_attempt_index"] = 1
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
        attempts,
    )

    with pytest.raises(ValueError, match="provider attempt.*sequence"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_duplicate_unit_replacing_planned_inventory(
    complete_paper_evidence_suite,
) -> None:
    attempts = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    duplicate = json.loads(json.dumps(attempts[0]))
    duplicate["attempt_id"] = "duplicate_attempt_for_same_planned_unit"
    duplicate["unit_id"] = "duplicate_protocol_unit"
    attempts.append(duplicate)
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
        attempts,
    )

    with pytest.raises(ValueError, match="planned AI unit.*duplicate|unit commitment"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_failed_attempts_rewritten_as_succeeded(
    complete_paper_evidence_suite,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
    )
    for record in records:
        if record["condition_id"] == "lean_condition":
            record["attempt_status"] = "succeeded"
            record["error_kind"] = None
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_attempt_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="failure stage"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("accepted_validity", True),
        ("root_status", "completed"),
        ("failure_kind", "verifier_rejected"),
    ],
)
def test_recompute_metrics_rejects_task_outcome_conflicting_with_attempt_evidence(
    complete_paper_evidence_suite,
    field_name,
    tampered_value,
) -> None:
    records = _read_jsonl(complete_paper_evidence_suite, "per_task_results.jsonl")
    records[1][field_name] = tampered_value
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "per_task_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="task outcome"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_run_status_conflicting_with_task_outcome(
    complete_paper_evidence_suite,
) -> None:
    records = _read_jsonl(complete_paper_evidence_suite, "run_results.jsonl")
    records[1]["status"] = "completed"
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "run_results.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="run status"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


@pytest.mark.parametrize("field_name", ["suite_id", "run_id", "task_id"])
def test_recompute_metrics_rejects_task_event_binding_tamper(
    complete_paper_evidence_suite,
    field_name,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
    )
    event = next(
        item
        for item in records
        if item["event_id"] == "factor_run:completed"
    )
    event[field_name] = f"tampered_{field_name}"
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="event.*binding"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_start", "missing_terminal", "reversed_order", "terminal_before_start"],
)
def test_recompute_metrics_rejects_invalid_task_event_lifecycle(
    complete_paper_evidence_suite,
    mutation,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
    )
    start_index = next(
        index
        for index, item in enumerate(records)
        if item["event_id"] == "factor_run:started"
    )
    terminal_index = next(
        index
        for index, item in enumerate(records)
        if item["event_id"] == "factor_run:completed"
    )
    if mutation == "duplicate_start":
        duplicate = dict(records[start_index])
        duplicate["event_id"] = "factor_run:started:duplicate"
        duplicate["recorded_at"] = "2026-07-17T00:00:01.010000Z"
        records.insert(terminal_index, duplicate)
    elif mutation == "missing_terminal":
        records.pop(terminal_index)
    elif mutation == "reversed_order":
        records[start_index], records[terminal_index] = (
            records[terminal_index],
            records[start_index],
        )
    else:
        records[terminal_index]["recorded_at"] = "2026-07-17T00:00:00.500000Z"
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="event lifecycle|event order"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_structured_blocked_start_or_wrong_terminal(
    complete_paper_evidence_suite,
) -> None:
    records = _read_jsonl(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
    )
    blocked = next(
        item for item in records if item["event_id"] == "blocked_run:blocked"
    )
    blocked["event_type"] = "task_completed"
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
        records,
    )

    with pytest.raises(ValueError, match="structured-blocked.*event lifecycle"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_suite_finished_stop_reason_mismatch(
    complete_paper_evidence_suite,
) -> None:
    suite = _read_json(complete_paper_evidence_suite, "suite_manifest.json")
    suite["stop_reason"] = "token_limit"
    _write_json(complete_paper_evidence_suite, "suite_manifest.json", suite)

    with pytest.raises(ValueError, match="suite_finished stop_reason"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_coordinated_suite_stop_reason_tamper(
    complete_paper_evidence_suite,
) -> None:
    suite = _read_json(complete_paper_evidence_suite, "suite_manifest.json")
    suite["stop_reason"] = "token_limit"
    suite["status"] = "budget_exhausted"
    _write_json(complete_paper_evidence_suite, "suite_manifest.json", suite)
    events = _read_jsonl(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
    )
    suite_finished = next(
        item for item in events if item["event_type"] == "suite_finished"
    )
    suite_finished["detail"]["stop_reason"] = "token_limit"
    _write_jsonl_and_refresh(
        complete_paper_evidence_suite,
        "events/event_log.jsonl",
        events,
    )

    with pytest.raises(ValueError, match="stop_reason.*plan/task evidence"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_rejects_suite_status_conflicting_with_task_outcomes(
    complete_paper_evidence_suite,
) -> None:
    suite = _read_json(complete_paper_evidence_suite, "suite_manifest.json")
    suite["status"] = "completed"
    _write_json(complete_paper_evidence_suite, "suite_manifest.json", suite)

    with pytest.raises(ValueError, match="suite manifest status"):
        recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])


def test_recompute_metrics_distinguishes_historical_attempts_from_replay_calls(
    complete_paper_evidence_suite,
) -> None:
    suite = _read_json(complete_paper_evidence_suite, "suite_manifest.json")
    suite["provider_calls_made"] = 0
    suite["replayed_run_count"] = 3
    _write_json(complete_paper_evidence_suite, "suite_manifest.json", suite)

    result = recompute_exp1_pilot_metrics(complete_paper_evidence_suite["root"])

    assert result.totals["provider_attempt_count"] == 2
    assert result.audit["historical_provider_attempt_count"] == 2
    assert result.audit["current_provider_calls_made"] == 0
    assert result.audit["replayed_run_count"] == 3


def _read_jsonl(fixture, relative_path: str) -> list[dict]:
    path = fixture["root"] / relative_path
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_json(fixture, relative_path: str) -> dict:
    return json.loads(
        (fixture["root"] / relative_path).read_text(encoding="utf-8")
    )


def _write_jsonl_and_refresh(fixture, relative_path: str, records: list[dict]) -> None:
    _write_jsonl(fixture["root"] / relative_path, records)
    fixture["refresh_manifest"]()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _write_json(fixture, relative_path: str, body: dict) -> None:
    (fixture["root"] / relative_path).write_text(
        json.dumps(body, sort_keys=True),
        encoding="utf-8",
    )


def _save_test_artifact(
    *,
    store: ArtifactStore,
    artifact_id: str,
    artifact_type: str,
    body: dict,
) -> dict:
    ref = store.save_json(
        body,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_schema_id=str(body["schema_version"]).rsplit(".v", 1)[0],
        artifact_schema_version=str(body["schema_version"]),
        source={"kind": "paper_metrics_tamper_test"},
        metadata={},
        created_at="2026-07-17T00:00:00Z",
    )
    return ref.to_dict()


def _rewrite_request_artifact(
    *,
    root: Path,
    attempt: dict,
    artifacts: list[dict],
    request_path: Path,
    old_request_ref: dict,
    request_body: dict,
) -> None:
    request_bytes = json.dumps(
        request_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request_path.write_bytes(request_bytes)
    new_request_ref = dict(old_request_ref)
    new_request_ref["content_hash"] = "sha256:" + hashlib.sha256(
        request_bytes
    ).hexdigest()
    new_request_ref["size_bytes"] = len(request_bytes)
    attempt["request_ref"] = new_request_ref
    indexed_request = next(
        item
        for item in artifacts
        if item["run_id"] == attempt["run_id"]
        and item["artifact_ref"]["artifact_id"] == old_request_ref["artifact_id"]
    )
    indexed_request["artifact_ref"] = new_request_ref
