from __future__ import annotations

from collections.abc import Iterator, Mapping
import gc
import hashlib
import json
from pathlib import Path
import tempfile
import tracemalloc

import pytest

import tokenshare.experiments.paper_formal_metrics as formal_metrics
from tokenshare.experiments.paper_formal_metrics import (
    recompute_paper_formal_metrics,
)
from tokenshare.experiments.paper_exp5_model_comparison import (
    EXP5_V3_SEQUENCE_PLAN_DIGEST,
)
from tokenshare.experiments.paper_models import PaperModelExecutionRecord


EXP1 = "exp1_real_ai_feasibility"
EXP2 = "exp2_real_ai_scalability"
EXP3 = "exp3_real_ai_fault_recovery"
EXP4 = "exp4_real_ai_protocol_ablation"
EXP5 = "exp5_real_ai_model_endpoint_comparison"


def test_lazy_formal_run_bundle_mapping_loads_on_demand_without_cache(
    tmp_path: Path,
) -> None:
    _write_suite_manifest(tmp_path, (EXP1,))
    generation = _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="lazy-condition",
        condition={},
        task={"task_id": "task-1", "root_status": "completed"},
        attempts=[_attempt(total_tokens=7, latency_ms=11)],
        events=_timing_events(20),
    )

    with formal_metrics.LazyFormalRunBundleMapping(tmp_path) as bundles:
        work_directory = bundles.work_directory
        assert work_directory.is_dir()
        assert list(bundles) == ["lazy-condition"]

        first = bundles["lazy-condition"]
        assert first["tasks"][0]["root_status"] == "completed"
        del first
        _write_jsonl(
            generation / "per_task_results.jsonl",
            [{"task_id": "task-1", "root_status": "failed"}],
        )

        second = bundles["lazy-condition"]
        assert second["tasks"][0]["root_status"] == "failed"

    assert not work_directory.exists()


def test_lazy_mapping_preserves_windows_path_case_folded_order(
    tmp_path: Path,
) -> None:
    _write_suite_manifest(tmp_path, (EXP1,))
    for condition_id in ("B-condition", "a.txt", "a"):
        _write_run(
            tmp_path,
            experiment_id=EXP1,
            condition_id=condition_id,
            condition={},
            task={"task_id": condition_id, "root_status": "completed"},
            attempts=[_attempt(total_tokens=1, latency_ms=1)],
            events=_timing_events(2),
        )

    with formal_metrics.LazyFormalRunBundleMapping(tmp_path) as bundles:
        assert list(bundles) == ["a", "a.txt", "B-condition"]


def test_read_jsonl_streams_lines_without_path_read_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "records.jsonl"
    _write_jsonl(path, [{"row": 1}, {"row": 2}])
    original_read_text = Path.read_text

    def forbidden_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise AssertionError("JSONL reader must not materialize the whole file")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    assert formal_metrics._read_jsonl(path) == [{"row": 1}, {"row": 2}]


def test_recompute_uses_lazy_run_bundle_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_suite_manifest(tmp_path, (EXP1,))
    for index in range(3):
        _write_run(
            tmp_path,
            experiment_id=EXP1,
            condition_id=f"lazy-recompute-{index}",
            condition={"domain": "factorization", "worker_count": 1},
            task={
                "task_id": f"task-{index}",
                "root_status": "completed",
                "accepted_validity": True,
                "paper_eligible": False,
            },
            attempts=[_attempt(total_tokens=index + 1, latency_ms=10)],
            events=_timing_events(20),
        )
    load_count = 0
    original_loader = formal_metrics._load_run_bundle

    def tracking_loader(**kwargs: object) -> dict[str, object]:
        nonlocal load_count
        load_count += 1
        return original_loader(**kwargs)

    monkeypatch.setattr(formal_metrics, "_load_run_bundle", tracking_loader)

    result = recompute_paper_formal_metrics(tmp_path)

    assert len(result.condition_rows) == 3
    assert load_count >= 3


def test_digest_streams_json_encoder_without_json_dumps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = {"b": [1, {"中文": "值"}], "a": 2.5}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    def forbidden_dumps(*args: object, **kwargs: object) -> str:
        raise AssertionError("digest must use JSONEncoder.iterencode")

    monkeypatch.setattr(formal_metrics.json, "dumps", forbidden_dumps)

    assert formal_metrics._digest(value) == expected


def test_hash_file_reads_fixed_chunks_without_path_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "large.bin"
    content = b"abc123" * 400_000
    path.write_bytes(content)

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("file hashing must be chunked")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    assert formal_metrics._hash_file(path) == (
        "sha256:" + hashlib.sha256(content).hexdigest()
    )


def test_atomic_chunk_writer_preserves_existing_file_on_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "result.jsonl"
    path.write_text("old\n", encoding="utf-8")

    def broken_chunks() -> object:
        yield "new\n"
        raise RuntimeError("sentinel write failure")

    with pytest.raises(RuntimeError, match="sentinel write failure"):
        formal_metrics._write_chunks_atomic(path, broken_chunks())

    assert path.read_text(encoding="utf-8") == "old\n"
    assert not path.with_name(path.name + ".tmp").exists()


def test_metrics_outputs_stream_model_inventory_without_text_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_model_text(*args: object, **kwargs: object) -> str:
        raise AssertionError("production writer must stream model records")

    monkeypatch.setattr(
        formal_metrics,
        "_model_record_text",
        forbidden_model_text,
    )

    refs = formal_metrics._write_metrics_outputs(
        root=tmp_path,
        metrics_body={},
        condition_rows=(),
        experiment_rows={
            EXP1: (),
            EXP2: (),
            EXP3: (),
            EXP4: (),
            EXP5: (),
        },
        run_bundles={},
    )

    canonical = tmp_path / "model_execution_records.jsonl"
    compatibility = tmp_path / "metrics" / "model_execution_records.jsonl"
    assert canonical.read_bytes() == compatibility.read_bytes() == b""
    assert {ref["path"] for ref in refs}.issuperset(
        {
            "model_execution_records.jsonl",
            "metrics/model_execution_records.jsonl",
        }
    )


def test_model_inventory_schema_failure_closes_before_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection:
        closed = False

        def executescript(self, script: str) -> None:
            raise RuntimeError("schema init sentinel")

        def close(self) -> None:
            self.closed = True

    connection = FailingConnection()

    class GuardedTemporaryDirectory:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._inner = tempfile.TemporaryDirectory(
                prefix="guarded-model-inventory-",
                dir=tmp_path,
            )

        def __enter__(self) -> str:
            return self._inner.__enter__()

        def __exit__(self, *exc_info: object) -> object:
            if not connection.closed:
                raise PermissionError("open sqlite handle blocked cleanup")
            return self._inner.__exit__(*exc_info)

    monkeypatch.setattr(
        formal_metrics,
        "TemporaryDirectory",
        GuardedTemporaryDirectory,
    )
    monkeypatch.setattr(
        formal_metrics.sqlite3,
        "connect",
        lambda *args, **kwargs: connection,
    )

    with pytest.raises(RuntimeError, match="schema init sentinel"):
        list(
            formal_metrics._iter_model_record_chunks(
                {},
                work_parent=tmp_path,
            )
        )

    assert connection.closed is True
    assert not list(tmp_path.glob("guarded-model-inventory-*"))


def test_model_inventory_rejects_same_artifact_id_with_conflicting_hashes(
    tmp_path: Path,
) -> None:
    condition_id = "conflicting-artifact-condition"
    attempts: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    for index in range(2):
        record = {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "condition_id": condition_id,
            "repeat_id": 0,
            "run_id": f"run-{index}",
            "task_id": f"task-{index}",
            "unit_id": f"unit-{index}",
            "attempt_id": f"attempt-{index}",
        }
        path = tmp_path / f"record-{index}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        content_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        model_ref = {
            "artifact_id": "shared-artifact-id",
            "content_hash": content_hash,
        }
        artifacts.append(
            {
                **model_ref,
                "path": path.relative_to(tmp_path).as_posix(),
            }
        )
        attempts.append(
            {
                "condition_id": condition_id,
                "repeat_id": 0,
                "run_id": f"run-{index}",
                "task_id": f"task-{index}",
                "unit_id": f"unit-{index}",
                "attempt_id": f"attempt-{index}",
                "attempt_status": "succeeded",
                "executor_type": "ai_api",
                "provider_attempt_count": 1,
                "model_execution_record_ref": model_ref,
            }
        )
    bundles = {
        condition_id: {
            "condition": {"condition_id": condition_id},
            "suite_root": tmp_path,
            "tasks": [],
            "attempts": attempts,
            "artifacts": artifacts,
        }
    }

    with pytest.raises(ValueError, match="conflicting content hash"):
        formal_metrics._model_record_text(bundles)


def test_exp5_renderer_keeps_only_lazy_condition_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    condition_ids = tuple(f"exp5-lazy-{index}" for index in range(8))

    class TrackedBundle(dict[str, object]):
        def __init__(self, condition_id: str) -> None:
            nonlocal active, max_active
            super().__init__(
                condition={
                    "condition_id": condition_id,
                    "experiment_id": EXP5,
                    "schema_version": "tokenshare.paper_condition.v3",
                    "cohort_member_id": condition_id,
                }
            )
            active += 1
            max_active = max(max_active, active)

        def __del__(self) -> None:
            nonlocal active
            active -= 1

    class OnDemandBundles(Mapping[str, Mapping[str, object]]):
        def __getitem__(self, key: str) -> Mapping[str, object]:
            if key not in condition_ids:
                raise KeyError(key)
            return TrackedBundle(key)

        def __iter__(self) -> Iterator[str]:
            return iter(condition_ids)

        def __len__(self) -> int:
            return len(condition_ids)

    monkeypatch.setattr(formal_metrics, "_exp5_v3_case_records", lambda _: ())
    monkeypatch.setattr(formal_metrics, "_exp5_v3_execution_rows", lambda _: ())
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_paired_comparison_rows",
        lambda _: (),
    )
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_order_and_concurrency_rows",
        lambda _: (),
    )
    monkeypatch.setattr(formal_metrics, "_exp5_overall_rows", lambda *a, **k: ())
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_domain_topic_rows",
        lambda *a, **k: (),
    )
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_failure_taxonomy_rows",
        lambda _: (),
    )

    rows = formal_metrics._exp5_renderer_rows(
        suite={"status": "completed"},
        bundles=OnDemandBundles(),
    )
    del rows
    gc.collect()

    assert max_active <= 1
    assert active == 0


def test_exp5_renderer_full_empty_inventory_keeps_one_live_bundle() -> None:
    active = 0
    max_active = 0
    condition_ids = tuple(f"exp5-full-lazy-{index}" for index in range(8))

    class TrackedBundle(dict[str, object]):
        def __init__(self, condition_id: str) -> None:
            nonlocal active, max_active
            super().__init__(
                condition={
                    "condition_id": condition_id,
                    "experiment_id": EXP5,
                    "schema_version": "tokenshare.paper_condition.v3",
                    "cohort_member_id": condition_id,
                    "domain": "factorization",
                    "paper_difficulty": "hard",
                    "repeat_id": 0,
                },
                tasks=(),
                attempts=(),
                artifacts=(),
            )
            active += 1
            max_active = max(max_active, active)

        def __del__(self) -> None:
            nonlocal active
            active -= 1

    class OnDemandBundles(Mapping[str, Mapping[str, object]]):
        def __getitem__(self, key: str) -> Mapping[str, object]:
            if key not in condition_ids:
                raise KeyError(key)
            return TrackedBundle(key)

        def __iter__(self) -> Iterator[str]:
            return iter(condition_ids)

        def __len__(self) -> int:
            return len(condition_ids)

    rows = formal_metrics._exp5_renderer_rows(
        suite={"status": "incomplete"},
        bundles=OnDemandBundles(),
    )
    del rows
    gc.collect()

    assert max_active <= 1
    assert active == 0


def test_recompute_large_suite_memory_is_bounded_by_local_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "x" * (512 * 1024)

    def build_suite(root: Path, run_count: int) -> None:
        _write_suite_manifest(root, (EXP1,))
        for index in range(run_count):
            _write_run(
                root,
                experiment_id=EXP1,
                condition_id=f"memory-condition-{index:03d}",
                condition={"domain": "factorization", "worker_count": 1},
                task={
                    "task_id": f"task-{index:03d}",
                    "root_status": "completed",
                    "accepted_validity": True,
                    "paper_eligible": False,
                    "memory_sentinel": payload,
                },
                attempts=[_attempt(total_tokens=index + 1, latency_ms=10)],
                events=_timing_events(20),
            )

    small_root = tmp_path / "small"
    large_root = tmp_path / "large"
    build_suite(small_root, 5)
    build_suite(large_root, 20)

    active = 0
    max_active = 0
    original_loader = formal_metrics._load_run_bundle

    class TrackedBundle(dict[str, object]):
        def __init__(self, value: Mapping[str, object]) -> None:
            nonlocal active, max_active
            super().__init__(value)
            active += 1
            max_active = max(max_active, active)

        def __del__(self) -> None:
            nonlocal active
            active -= 1

    def tracking_loader(**kwargs: object) -> dict[str, object]:
        return TrackedBundle(original_loader(**kwargs))

    monkeypatch.setattr(formal_metrics, "_load_run_bundle", tracking_loader)

    def measured_peak(root: Path) -> int:
        gc.collect()
        tracemalloc.start()
        recompute_paper_formal_metrics(root)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        gc.collect()
        return peak

    small_peak = measured_peak(small_root)
    large_peak = measured_peak(large_root)
    (tmp_path / "bounded-memory-measurement.json").write_text(
        json.dumps(
            {
                "small_run_count": 5,
                "small_peak_bytes": small_peak,
                "large_run_count": 20,
                "large_peak_bytes": large_peak,
                "max_live_bundles": max_active,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert active == 0
    assert max_active <= 1
    assert large_peak <= int(small_peak * 1.20) + 1024 * 1024
    assert large_peak - small_peak <= 64 * 1024 * 1024


def test_exp3_missing_fault_identity_is_not_counted_as_zero_measurement(
    tmp_path: Path,
) -> None:
    rows = {
        "exp3-missing-fault-identity": {
            "condition_id": "exp3-missing-fault-identity",
            "wall_clock_ms": 10,
            "total_tokens": 0,
            "total_cost_estimate": 0.0,
            "provider_latency_sum_ms": 0.0,
            "paper_eligible": True,
            "paper_ineligibility_reasons": [],
        }
    }
    bundles = {
        "exp3-missing-fault-identity": {
            "condition": {
                "experiment_id": EXP3,
                "condition_id": "exp3-missing-fault-identity",
                "fault_type": "false_negative",
                "fault_rate": 1.0,
            },
            "faults": [{"fault_type": "false_negative"}],
            "events": [],
            "attempts": [],
            "tasks": [],
            "suite_root": tmp_path,
        }
    }

    row = formal_metrics._exp3_rows(rows, bundles)[0]

    assert row["reassignment_count"] is None
    assert row["reassignment_count_sample_size"] == 0
    assert row["reassignment_count_missing_count"] == 1
    assert row["wasted_actual_tokens"] is None
    assert row["wasted_actual_tokens_sample_size"] == 0
    assert row["wasted_actual_tokens_missing_count"] == 1
    assert "missing_fault_attempt_evidence" in row[
        "paper_ineligibility_reasons"
    ]


def test_exp3_negative_recovery_interval_is_invalid_not_zero() -> None:
    outcomes, reasons = formal_metrics._exp3_fault_outcomes(
        faults=[
            {
                "fault_type": "false_negative",
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
            }
        ],
        attempts=[
            {
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
                "attempt_status": "verification_rejected",
                "ended_at": "2026-07-31T00:00:00.200+00:00",
                "total_tokens": 5,
            },
            {
                "attempt_id": "replacement-attempt",
                "unit_id": "unit-1",
                "attempt_status": "succeeded",
                "canonical": True,
                "started_at": "2026-07-31T00:00:00.100+00:00",
            },
        ],
        events=[
            {
                "event_id": "verification-fault",
                "event_type": "VERIFICATION_RECORDED",
                "payload": {
                    "attempt_id": "fault-attempt",
                    "status": "failed",
                },
            },
            {
                "event_id": "recovery-fault",
                "event_type": "RECOVERY_ACTION_RECORDED",
                "payload": {
                    "recovery_action": {
                        "attempt_id": "fault-attempt",
                        "retry_allowed": True,
                    }
                },
            },
            {
                "event_id": "canonical-replacement",
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "payload": {
                    "selected_attempt_id": "replacement-attempt",
                    "unit_id": "unit-1",
                },
            },
            {
                "event_id": "completed-replacement",
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "object_id": "unit-1",
                "payload": {"new_state": "completed"},
            },
        ],
    )

    assert outcomes[0]["recovered"] is True
    assert outcomes[0]["recovery_latency_ms"] is None
    assert "invalid_recovery_timing_evidence" in reasons


def test_formal_metrics_are_recomputed_from_mutable_input_evidence(
    tmp_path: Path,
) -> None:
    generation = _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="exp1-condition",
        condition={"domain": "factorization", "worker_count": 1},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "accepted_validity": True,
            "paper_eligible": False,
        },
        attempts=[_attempt(total_tokens=12, latency_ms=40)],
        events=_timing_events(100),
    )
    _write_suite_manifest(tmp_path, (EXP1,))

    first = recompute_paper_formal_metrics(tmp_path)
    first_row = first.experiment_rows[EXP1][0]
    assert first_row["completion_rate"] == 1.0
    assert first_row["accepted_validity_rate"] == 1.0
    assert first_row["token_p50"] == 12
    assert first.paper_eligible is False

    task_path = generation / "per_task_results.jsonl"
    changed = {
        **json.loads(task_path.read_text(encoding="utf-8")),
        "root_status": "failed",
        "accepted_validity": False,
    }
    task_path.write_text(json.dumps(changed) + "\n", encoding="utf-8")

    second = recompute_paper_formal_metrics(tmp_path)
    second_row = second.experiment_rows[EXP1][0]
    assert second_row["completion_rate"] == 0.0
    assert second_row["accepted_validity_rate"] == 0.0
    assert second.metrics_digest != first.metrics_digest


def test_formal_metrics_top_level_cannot_override_ineligible_lower_evidence(
    tmp_path: Path,
) -> None:
    _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="exp1-claimed-eligible",
        condition={"domain": "factorization", "worker_count": 1},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "paper_eligible": False,
        },
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=20),
                "paper_eligible": False,
            }
        ],
        events=_timing_events(20),
    )
    _write_suite_manifest(tmp_path, (EXP1,))
    manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    manifest.update(
        {
            "regression_only": False,
            "capturing": True,
            "paper_eligible": True,
        }
    )
    _write_json(tmp_path / "suite_manifest.json", manifest)

    result = recompute_paper_formal_metrics(tmp_path)

    assert result.condition_rows[0]["paper_eligible"] is False
    assert result.paper_eligible is False
    metrics_body = json.loads(
        (tmp_path / "metrics" / "formal_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert metrics_body["paper_eligible"] is False


def test_formal_metrics_recompute_exp2_critical_path_and_all_experiment_views(
    tmp_path: Path,
) -> None:
    _write_run(
        tmp_path,
        experiment_id=EXP2,
        condition_id="exp2-w1",
        condition={"domain": "factorization", "difficulty": "hard", "worker_count": 1},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "paper_eligible": True,
            "runtime_generation_identity": {"run_id": "run-w1", "root_unit_id": "root"},
            "runtime_observation": {
                "runtime_wall_clock_ms": 100,
                "planned_ai_unit_ids": ["unit-a"],
                "dispatched_ai_unit_ids": ["unit-a"],
                "completed_ai_unit_ids": ["unit-a"],
                "unscheduled_ai_unit_ids": [],
                "in_flight_ai_unit_ids_at_witness": [],
                "observed_peak_concurrency": 1,
            },
        },
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=80),
                **_timed_attempt("attempt-a", "unit-a", 0, 90),
                "paper_eligible": True,
            }
        ],
        events=_critical_path_events(
            required=("unit-a",),
            canonical_ms={"unit-a": 90},
            merge_gate_ms=90,
            merge_done_ms=100,
            root_done_ms=100,
        ),
    )
    _write_run(
        tmp_path,
        experiment_id=EXP2,
        condition_id="exp2-w2",
        condition={"domain": "factorization", "difficulty": "hard", "worker_count": 2},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "paper_eligible": True,
            "runtime_generation_identity": {"run_id": "run-w2", "root_unit_id": "root"},
            "runtime_observation": {
                "runtime_wall_clock_ms": 50,
                "planned_ai_unit_ids": ["unit-b"],
                "dispatched_ai_unit_ids": ["unit-b"],
                "completed_ai_unit_ids": ["unit-b"],
                "unscheduled_ai_unit_ids": [],
                "in_flight_ai_unit_ids_at_witness": [],
                "observed_peak_concurrency": 1,
            },
        },
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=80),
                **_timed_attempt("attempt-rate", "unit-b", 0, 20),
                "attempt_status": "provider_error",
                "error_kind": "rate_limited",
                "paper_eligible": True,
            },
            {
                **_attempt(total_tokens=10, latency_ms=20),
                **_timed_attempt("attempt-b", "unit-b", 20, 40),
                "paper_eligible": True,
            },
        ],
        events=_critical_path_events(
            required=("unit-b",),
            canonical_ms={"unit-b": 40},
            merge_gate_ms=40,
            merge_done_ms=50,
            root_done_ms=50,
        ),
    )
    _write_run(
        tmp_path,
        experiment_id=EXP3,
        condition_id="exp3-fault",
        condition={"domain": "factorization", "worker_count": 10},
        task={"task_id": "case-1", "root_status": "completed"},
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=30),
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
                "attempt_status": "verification_rejected",
                "canonical": False,
            },
            {
                **_attempt(total_tokens=10, latency_ms=30),
                "attempt_id": "replacement-attempt",
                "unit_id": "unit-1",
                "canonical": True,
            },
        ],
        events=[
            {
                "event_id": "verification-fault",
                "event_type": "VERIFICATION_RECORDED",
                "payload": {
                    "attempt_id": "fault-attempt",
                    "unit_id": "unit-1",
                    "status": "failed",
                },
            },
            {
                "event_id": "recovery-fault",
                "event_type": "RECOVERY_ACTION_RECORDED",
                "payload": {
                    "recovery_action": {
                        "attempt_id": "fault-attempt",
                        "unit_id": "unit-1",
                        "retry_allowed": True,
                    }
                },
            },
            {
                "event_id": "canonical-replacement",
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "payload": {
                    "selected_attempt_id": "replacement-attempt",
                    "unit_id": "unit-1",
                },
            },
            {
                "event_id": "completed-replacement",
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "object_id": "unit-1",
                "payload": {"new_state": "Completed"},
            },
        ],
        faults=[
            {
                "fault_injection_id": "fault-1",
                "fault_type": "false_negative",
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
            }
        ],
    )
    _write_run(
        tmp_path,
        experiment_id=EXP4,
        condition_id="exp4-mode",
        condition={"domain": "factorization", "worker_count": 10, "ablation_mode": "NO_VERIFICATION"},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "final_deterministic_validity": False,
            "ablation_runtime": {
                "attempt_observations": [
                    {
                        "attempt_id": "attempt-10-30",
                        "raw_output_ref": {"artifact_id": "raw-exp4"},
                        "candidate_output_ref": {
                            "artifact_id": "candidate-exp4"
                        },
                        "canonical_output_refs": {
                            "range_result": {
                                "artifact_id": "canonical-exp4"
                            }
                        },
                        "independent_candidate_validity": False,
                        "final_validity": False,
                    }
                ],
                "hook_observations": [],
            },
        },
        attempts=[_attempt(total_tokens=10, latency_ms=30)],
        events=[{"event_type": "ABLATION_BOUNDARY_APPLIED"}],
    )
    exp5_attempt = _attempt(total_tokens=13, latency_ms=25)
    exp5_item = _exp5_model_item(
        condition_id="exp5-member",
        task_id="case-1",
        attempt=exp5_attempt,
    )
    _write_run(
        tmp_path,
        experiment_id=EXP5,
        condition_id="exp5-member",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "model_entry_id": "glm_5_2_exp1_baseline",
            "cohort_member_id": "glm_5_2_siliconflow",
        },
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "accepted_validity": True,
            "paper_eligible": True,
            "model_execution_records": [exp5_item],
        },
        attempts=[exp5_item["attempt"]],
        events=_timing_events(30),
    )
    _write_suite_manifest(tmp_path, (EXP2, EXP3, EXP4, EXP5))

    result = recompute_paper_formal_metrics(tmp_path)

    exp2_rows = {row["worker_count"]: row for row in result.experiment_rows[EXP2]}
    assert exp2_rows[1]["critical_path_ms"] == pytest.approx(100)
    assert exp2_rows[2]["critical_path_ms"] == pytest.approx(50)
    assert exp2_rows[2]["speedup"] == pytest.approx(2.0)
    assert exp2_rows[2]["efficiency"] == pytest.approx(1.0)
    assert exp2_rows[2]["rate_limited_attempt_count"] == 1
    assert result.experiment_rows[EXP3][0]["recovery_rate"] == 1.0
    assert result.experiment_rows[EXP4][0]["escaped_error_count"] is None
    assert result.experiment_rows[EXP4][0]["paper_eligible"] is False
    assert result.experiment_rows[EXP5][0]["model_identity_match_rate"] == 1.0

    for relative_path in (
        "metrics/per_condition_summary.csv",
        "metrics/paper_table_feasibility.csv",
        "metrics/paper_plot_scalability.csv",
        "metrics/paper_plot_robustness.csv",
        "metrics/paper_table_ablation.csv",
        "metrics/paper_table_model_comparison.csv",
        "metrics/model_execution_records.jsonl",
        "metrics/failure_examples.json",
        "metrics/formal_metrics.json",
    ):
        assert (tmp_path / relative_path).is_file(), relative_path


def test_formal_model_execution_inventory_aggregates_exp1_through_exp4_records(
    tmp_path: Path,
) -> None:
    bundles: dict[str, dict[str, object]] = {}
    expected_records = []
    for index, experiment_id in enumerate((EXP1, EXP2, EXP3, EXP4), start=1):
        condition_id = f"{experiment_id}-condition"
        attempt_id = f"attempt-{index}"
        artifact_id = f"model-record-{index}"
        record = {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": 0,
            "run_id": f"run-{index}",
            "task_id": f"task-{index}",
            "unit_id": f"unit-{index}",
            "attempt_id": attempt_id,
            "identity_status": "matched",
            "paper_eligible": True,
        }
        artifact_path = (
            tmp_path
            / "persisted-artifacts"
            / f"{artifact_id}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        content_hash = "sha256:" + hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
        model_ref = {
            "artifact_id": artifact_id,
            "artifact_type": "PaperModelExecutionRecord",
            "content_hash": content_hash,
        }
        bundles[condition_id] = {
            "condition": {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
            },
            "suite_root": tmp_path,
            "tasks": [],
            "attempts": [
                {
                    "attempt_id": attempt_id,
                    "model_execution_record_ref": model_ref,
                }
            ],
            "artifacts": [
                {
                    **model_ref,
                    "path": artifact_path.relative_to(tmp_path).as_posix(),
                }
            ],
        }
        expected_records.append(record)

    refs = formal_metrics._write_metrics_outputs(
        root=tmp_path,
        metrics_body={},
        condition_rows=(),
        experiment_rows={
            EXP1: (),
            EXP2: (),
            EXP3: (),
            EXP4: (),
            EXP5: (),
        },
        run_bundles=bundles,
    )

    canonical_path = tmp_path / "model_execution_records.jsonl"
    compatibility_path = tmp_path / "metrics" / "model_execution_records.jsonl"
    assert [
        json.loads(line)
        for line in canonical_path.read_text(encoding="utf-8").splitlines()
    ] == expected_records
    assert compatibility_path.read_bytes() == canonical_path.read_bytes()
    ref_paths = {ref["path"] for ref in refs}
    assert "model_execution_records.jsonl" in ref_paths
    assert "metrics/model_execution_records.jsonl" in ref_paths


def test_formal_model_execution_inventory_rejects_missing_ai_record_ref(
    tmp_path: Path,
) -> None:
    bundles = {
        "exp1-condition": {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-condition",
            },
            "suite_root": tmp_path,
            "tasks": [{"task_id": "case-1"}],
            "attempts": [
                {
                    "condition_id": "exp1-condition",
                    "repeat_id": 0,
                    "run_id": "run-1",
                    "task_id": "case-1",
                    "unit_id": "unit-1",
                    "attempt_id": "attempt-1",
                    "attempt_status": "succeeded",
                    "executor_type": "ai_api",
                    "provider_attempt_count": 1,
                    "model_execution_record_ref": None,
                }
            ],
            "artifacts": [],
        }
    }

    with pytest.raises(
        ValueError,
        match="missing canonical model execution record ref",
    ):
        formal_metrics._model_record_text(bundles)


def test_formal_model_execution_inventory_allows_explicit_zero_call_attempts(
    tmp_path: Path,
) -> None:
    bundles = {
        "exp1-condition": {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-condition",
            },
            "suite_root": tmp_path,
            "tasks": [{"task_id": "case-1"}],
            "attempts": [
                {
                    "schema_version": "tokenshare.paper_attempt_result.v2",
                    "condition_id": "exp1-condition",
                    "repeat_id": 0,
                    "run_id": "run-1",
                    "task_id": "case-1",
                    "unit_id": "unit-1",
                    "attempt_id": "attempt-1",
                    "attempt_status": "executor_error",
                    "executor_type": "ai_api",
                    "provider": None,
                    "model": None,
                    "provider_attempt_count": 0,
                    "model_execution_record_ref": None,
                }
            ],
            "artifacts": [],
        }
    }

    assert formal_metrics._model_record_text(bundles) == ""


def test_condition_provider_latency_keeps_post_provider_executor_error_missing(
) -> None:
    row = formal_metrics._condition_metrics(
        {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-post-provider-executor-error",
                "repeat_id": 0,
                "worker_count": 1,
            },
            "tasks": [
                {
                    "task_id": "case-1",
                    "root_status": "failed",
                    "paper_eligible": False,
                }
            ],
            "attempts": [
                {
                    "schema_version": "tokenshare.paper_attempt_result.v2",
                    "task_id": "case-1",
                    "attempt_id": "attempt-1",
                    "attempt_status": "executor_error",
                    "record_scope": "protocol",
                    "provider_attempt_count": 1,
                    "latency_ms": None,
                    "error_kind": "parser_bridge_error",
                    "paper_eligible": False,
                }
            ],
            "events": [],
            "artifacts": [{"artifact_id": "request-1"}],
        }
    )

    assert row["provider_attempt_count"] == 1
    assert row["provider_latency_sum_ms"] is None
    assert row["provider_latency_evidence_status"] == "incomplete"
    assert (
        row["provider_latency_unavailable_reason"]
        == "missing_provider_latency_evidence"
    )


def test_formal_model_execution_inventory_rejects_cross_attempt_identity(
    tmp_path: Path,
) -> None:
    record = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "experiment_id": EXP1,
        "condition_id": "exp1-condition",
        "repeat_id": 0,
        "run_id": "run-1",
        "task_id": "different-task",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
    }
    artifact_path = tmp_path / "persisted-artifacts" / "model-record.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    content_hash = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    model_ref = {
        "artifact_id": "model-record",
        "artifact_type": "PaperModelExecutionRecord",
        "content_hash": content_hash,
    }
    bundles = {
        "exp1-condition": {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-condition",
            },
            "suite_root": tmp_path,
            "tasks": [{"task_id": "case-1"}],
            "attempts": [
                {
                    "condition_id": "exp1-condition",
                    "repeat_id": 0,
                    "run_id": "run-1",
                    "task_id": "case-1",
                    "unit_id": "unit-1",
                    "attempt_id": "attempt-1",
                    "attempt_status": "succeeded",
                    "executor_type": "ai_api",
                    "provider_attempt_count": 1,
                    "model_execution_record_ref": model_ref,
                }
            ],
            "artifacts": [
                {
                    **model_ref,
                    "path": artifact_path.relative_to(tmp_path).as_posix(),
                }
            ],
        }
    }

    with pytest.raises(ValueError, match="record identity is invalid"):
        formal_metrics._model_record_text(bundles)


def test_formal_model_execution_inventory_accepts_preserved_protocol_task_identity(
    tmp_path: Path,
) -> None:
    record = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "experiment_id": EXP1,
        "condition_id": "exp1-condition",
        "repeat_id": 0,
        "run_id": "run-1",
        "task_id": "protocol-task-1",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
    }
    artifact_path = tmp_path / "persisted-artifacts" / "model-record.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    content_hash = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    model_ref = {
        "artifact_id": "model-record",
        "artifact_type": "PaperModelExecutionRecord",
        "content_hash": content_hash,
    }
    bundles = {
        "exp1-condition": {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-condition",
            },
            "suite_root": tmp_path,
            "tasks": [{"task_id": "case-1"}],
            "attempts": [
                {
                    "condition_id": "exp1-condition",
                    "repeat_id": 0,
                    "run_id": "run-1",
                    "task_id": "case-1",
                    "protocol_task_id": "protocol-task-1",
                    "unit_id": "unit-1",
                    "attempt_id": "attempt-1",
                    "attempt_status": "succeeded",
                    "executor_type": "ai_api",
                    "provider_attempt_count": 1,
                    "model_execution_record_ref": model_ref,
                }
            ],
            "artifacts": [
                {
                    **model_ref,
                    "path": artifact_path.relative_to(tmp_path).as_posix(),
                }
            ],
        }
    }

    assert formal_metrics._model_record_text(bundles) == (
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    )


def test_formal_model_execution_inventory_rejects_duplicate_attempt_identity(
    tmp_path: Path,
) -> None:
    record = {
        "schema_version": "tokenshare.paper_model_execution_record.v2",
        "condition_id": "exp1-condition",
        "repeat_id": 0,
        "run_id": "run-1",
        "task_id": "case-1",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
    }
    artifact_path = tmp_path / "persisted-artifacts" / "model-record.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    content_hash = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    refs = [
        {
            "artifact_id": f"model-record-{index}",
            "artifact_type": "PaperModelExecutionRecord",
            "content_hash": content_hash,
        }
        for index in (1, 2)
    ]
    base_attempt = {
        "condition_id": "exp1-condition",
        "repeat_id": 0,
        "run_id": "run-1",
        "task_id": "case-1",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
        "attempt_status": "succeeded",
        "executor_type": "ai_api",
        "provider_attempt_count": 1,
    }
    bundles = {
        "exp1-condition": {
            "condition": {
                "experiment_id": EXP1,
                "condition_id": "exp1-condition",
            },
            "suite_root": tmp_path,
            "tasks": [{"task_id": "case-1"}],
            "attempts": [
                {**base_attempt, "model_execution_record_ref": model_ref}
                for model_ref in refs
            ],
            "artifacts": [
                {
                    **model_ref,
                    "path": artifact_path.relative_to(tmp_path).as_posix(),
                }
                for model_ref in refs
            ],
        }
    }

    with pytest.raises(
        ValueError,
        match="duplicate model execution attempt identity",
    ):
        formal_metrics._model_record_text(bundles)


def test_exp2_scalability_csv_is_per_root_and_pairs_worker_one_by_case_repeat(
    tmp_path: Path,
) -> None:
    planned = [f"range_{index}" for index in range(20)]
    for worker_count, base_wall_clock_ms, dispatched_count, peak in (
        (1, 1000, 20, 1),
        (3, 500, 3, 3),
    ):
        for repeat_id in (0, 1):
            wall_clock_ms = base_wall_clock_ms + repeat_id * (
                200 if worker_count == 1 else 100
            )
            attempts = [
                {
                    **_attempt(total_tokens=10, latency_ms=100),
                    "attempt_id": f"attempt-w{worker_count}-{index}",
                    "unit_id": f"unit-w{worker_count}-{index}",
                    "worker_id": f"worker-{index % worker_count}",
                    "started_at": "2026-07-24T00:00:00.000+00:00",
                    "ended_at": "2026-07-24T00:00:01.000+00:00",
                    "paper_eligible": True,
                }
                for index in range(dispatched_count)
            ]
            _write_run(
                tmp_path,
                experiment_id=EXP2,
                condition_id=f"exp2-hard-w{worker_count}-r{repeat_id}",
                condition={
                    "domain": "factorization",
                    "difficulty": "hard",
                    "paper_difficulty": "hard",
                    "worker_count": worker_count,
                },
                task={
                    "task_id": "factor_v2_hard_001",
                    "case_id": "factor_v2_hard_001",
                    "factor_position_quantile": "early",
                    "root_status": "completed",
                    "accepted_validity": True,
                    "paper_eligible": True,
                    "runtime_observation": {
                        "schema_version": "tokenshare.protocol_runtime_observation.v1",
                        "runtime_started_at": "2026-07-24T00:00:00.000+00:00",
                        "runtime_ended_at": "2026-07-24T00:00:01.000+00:00",
                        "runtime_wall_clock_ms": wall_clock_ms,
                        "planned_ai_unit_ids": planned,
                        "dispatched_ai_unit_ids": planned[:dispatched_count],
                        "completed_ai_unit_ids": planned[:dispatched_count],
                        "unscheduled_ai_unit_ids": planned[dispatched_count:],
                        "in_flight_ai_unit_ids_at_witness": [],
                        "witness_observed_at": "2026-07-24T00:00:00.500+00:00",
                        "worker_execution_facts": [],
                        "observed_peak_concurrency": peak,
                    },
                },
                attempts=attempts,
                events=_timing_events(wall_clock_ms),
                repeat_id=repeat_id,
            )
    _write_suite_manifest(tmp_path, (EXP2,))

    result = recompute_paper_formal_metrics(tmp_path)
    rows = {
        row["worker_count"]: row
        for row in result.experiment_rows[EXP2]
        if row["row_scope"] == "root_run" and row["repeat_id"] == 0
    }
    scaled = rows[3]

    assert scaled["row_scope"] == "root_run"
    assert scaled["case_id"] == "factor_v2_hard_001"
    assert scaled["factor_position_quantile"] == "early"
    assert scaled["planned_ai_unit_count"] == 20
    assert scaled["executed_ai_unit_count"] == 3
    assert scaled["early_stop_unscheduled_count"] == 17
    assert scaled["in_flight_after_witness_count"] == 0
    assert scaled["observed_peak_concurrency"] == 3
    assert scaled["wall_clock_ms"] == 500
    assert scaled["speedup"] == pytest.approx(2.0)
    assert scaled["parallel_efficiency"] == pytest.approx(2.0 / 3.0)
    assert scaled["throughput"] == pytest.approx(6.0)
    assert scaled["provider_attempt_count"] == 3
    assert scaled["total_tokens"] == 30
    assert scaled["completion_rate"] == 1.0
    assert scaled["http_429_count"] == 0
    assert scaled["retry_count"] == 0
    aggregate = next(
        row
        for row in result.experiment_rows[EXP2]
        if row["row_scope"] == "repeat_aggregate"
        and row["worker_count"] == 3
    )
    assert aggregate["repeat_count"] == 2
    assert aggregate["wall_clock_min_ms"] == 500
    assert aggregate["wall_clock_max_ms"] == 600
    assert aggregate["wall_clock_relative_difference"] == pytest.approx(0.2)
    assert "wall_clock_p50" not in aggregate
    assert "wall_clock_p95" not in aggregate
    csv_header = (
        tmp_path / "metrics" / "paper_plot_scalability.csv"
    ).read_text(encoding="utf-8").splitlines()[0]
    for field_name in (
        "case_id",
        "factor_position_quantile",
        "planned_ai_unit_count",
        "executed_ai_unit_count",
        "early_stop_unscheduled_count",
        "in_flight_after_witness_count",
        "parallel_efficiency",
        "worker_utilization",
    ):
        assert field_name in csv_header


def test_formal_specialty_rows_follow_evidence_mutations_and_fail_closed(
    tmp_path: Path,
) -> None:
    exp2_generation = _write_run(
        tmp_path,
        experiment_id=EXP2,
        condition_id="exp2-evidence",
        condition={"domain": "factorization", "difficulty": "hard", "worker_count": 1},
        task={"task_id": "case-exp2", "root_status": "completed"},
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=80),
                "started_at": "2026-07-24T00:00:00.000+00:00",
                "ended_at": "2026-07-24T00:00:00.100+00:00",
                "worker_id": "worker-0",
            }
        ],
        events=_timing_events(999),
    )
    exp3_generation = _write_run(
        tmp_path,
        experiment_id=EXP3,
        condition_id="exp3-evidence",
        condition={"domain": "factorization", "worker_count": 10},
        task={"task_id": "case-exp3", "root_status": "completed"},
        attempts=[
            {
                **_attempt(total_tokens=11, latency_ms=30),
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
                "canonical": False,
            }
        ],
        events=[
            {
                "event_id": "canonical-fault",
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "payload": {
                    "selected_attempt_id": "fault-attempt",
                    "unit_id": "unit-1",
                },
            }
        ],
        faults=[
            {
                "fault_injection_id": "fault-1",
                "fault_type": "false_negative",
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
            }
        ],
    )
    exp4_raw_ref = {
        "artifact_id": "raw-exp4",
        "content_hash": "sha256:" + "4" * 64,
    }
    exp4_candidate_ref = {
        "artifact_id": "candidate-exp4",
        "content_hash": "sha256:" + "5" * 64,
    }
    exp4_generation = _write_run(
        tmp_path,
        experiment_id=EXP4,
        condition_id="exp4-evidence",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "ablation_mode": "NO_PARSER_POLICY",
        },
        task={
            "task_id": "case-exp4",
            "root_status": "completed",
            "ablation_runtime": {
                "schema_version": "tokenshare.paper_ablation_runtime.v1",
                "condition_id": "exp4-evidence",
                "case_id": "case-exp4",
                "repeat_id": 0,
                "mode": "NO_PARSER_POLICY",
                    "attempt_observations": [
                        {
                            "attempt_id": "attempt-12-30",
                            "raw_output_ref": exp4_raw_ref,
                            "candidate_output_ref": exp4_candidate_ref,
                        "canonical_output_refs": {},
                        "independent_candidate_validity": True,
                        "final_validity": True,
                    }
                ],
                "hook_observations": [
                    {
                            "event_type": "EXPERIMENT_ABLATION_GATE_APPLIED",
                            "ablation_mode": "NO_PARSER_POLICY",
                            "disabled_mechanism": "parser_policy",
                            "artifact_refs": [exp4_raw_ref],
                        "hook_input": {
                            "task_id": "case-exp4",
                            "unit_id": "unit-1",
                            "attempt_id": "attempt-12-30",
                        },
                        "hook_result": {"bypass": True, "stop": False},
                    }
                ],
            },
        },
        attempts=[_attempt(total_tokens=12, latency_ms=30)],
        events=[{"event_type": "EXPERIMENT_ABLATION_GATE_APPLIED"}],
        artifacts=[exp4_raw_ref, exp4_candidate_ref],
    )
    exp5_attempt = _attempt(total_tokens=13, latency_ms=25)
    exp5_model_item = _exp5_model_item(
        condition_id="exp5-evidence",
        task_id="case-exp5",
        attempt=exp5_attempt,
    )
    exp5_generation = _write_run(
        tmp_path,
        experiment_id=EXP5,
        condition_id="exp5-evidence",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "model_entry_id": "glm_5_2_exp1_baseline",
            "cohort_member_id": "glm_5_2_siliconflow",
        },
        task={
            "task_id": "case-exp5",
            "root_status": "completed",
            "accepted_validity": True,
            "paper_eligible": True,
            "model_execution_records": [exp5_model_item],
        },
        attempts=[exp5_model_item["attempt"]],
        events=_timing_events(30),
    )
    _write_suite_manifest(tmp_path, (EXP2, EXP3, EXP4, EXP5))

    first = recompute_paper_formal_metrics(tmp_path)
    exp2_row = first.experiment_rows[EXP2][0]
    assert exp2_row["wall_clock_ms"] == pytest.approx(100)
    assert exp2_row["wall_clock_source"] == "worker_execution_intervals"
    assert "wall_clock_p50" not in exp2_row
    assert "wall_clock_p95" not in exp2_row
    assert first.experiment_rows[EXP3][0]["false_accept_rate"] == 1.0
    assert first.experiment_rows[EXP4][0]["raw_only_count"] == 0
    assert first.experiment_rows[EXP5][0]["model_identity_match_rate"] == 1.0

    exp2_attempt_path = exp2_generation / "per_attempt_results.jsonl"
    exp2_attempt = json.loads(exp2_attempt_path.read_text(encoding="utf-8"))
    exp2_attempt["ended_at"] = "2026-07-24T00:00:00.250+00:00"
    exp2_attempt_path.write_text(json.dumps(exp2_attempt) + "\n", encoding="utf-8")

    exp3_event_path = exp3_generation / "events" / "event_log.jsonl"
    exp3_events = [
        {
            "experiment_id": EXP3,
            "condition_id": "exp3-evidence",
            "repeat_id": 0,
            "task_id": "case-exp3",
            "event_id": "verification-fault",
            "event_type": "VERIFICATION_RECORDED",
            "payload": {
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
                "status": "failed",
            },
        },
        {
            "experiment_id": EXP3,
            "condition_id": "exp3-evidence",
            "repeat_id": 0,
            "task_id": "case-exp3",
            "event_id": "canonical-other",
            "event_type": "CANONICAL_OUTPUTS_BOUND",
            "payload": {
                "selected_attempt_id": "other-attempt",
                "unit_id": "unit-1",
            },
        },
    ]
    _write_jsonl(exp3_event_path, exp3_events)

    exp4_task_path = exp4_generation / "per_task_results.jsonl"
    exp4_task = json.loads(exp4_task_path.read_text(encoding="utf-8"))
    exp4_observation = exp4_task["ablation_runtime"]["attempt_observations"][0]
    exp4_observation["candidate_output_ref"] = exp4_observation["raw_output_ref"]
    exp4_task_path.write_text(json.dumps(exp4_task) + "\n", encoding="utf-8")

    exp5_task_path = exp5_generation / "per_task_results.jsonl"
    exp5_task = json.loads(exp5_task_path.read_text(encoding="utf-8"))
    exp5_item = exp5_task["model_execution_records"][0]
    exp5_item["record"]["resolved_model"] = "unexpected/model"
    exp5_item["record"]["identity_status"] = "model_identity_mismatch"
    exp5_item["record"]["mismatch_reasons"] = ["resolved_model_mismatch"]
    exp5_item["record"]["paper_eligible"] = False
    exp5_item["raw_output"]["resolved_model"] = "unexpected/model"
    exp5_item["raw_output"]["raw_response_json"]["model"] = "unexpected/model"
    _refresh_model_record_digest(exp5_item["record"])
    exp5_task_path.write_text(json.dumps(exp5_task) + "\n", encoding="utf-8")

    mutated = recompute_paper_formal_metrics(tmp_path)
    assert mutated.experiment_rows[EXP2][0]["wall_clock_ms"] == pytest.approx(250)
    assert mutated.experiment_rows[EXP3][0]["false_accept_rate"] == 0.0
    assert mutated.experiment_rows[EXP4][0]["raw_only_count"] == 1
    assert mutated.experiment_rows[EXP5][0]["model_identity_match_rate"] == 0.0

    del exp3_events[1]["event_id"]
    _write_jsonl(exp3_event_path, exp3_events)
    del exp4_task["ablation_runtime"]
    exp4_task_path.write_text(json.dumps(exp4_task) + "\n", encoding="utf-8")
    del exp5_task["model_execution_records"]
    exp5_task_path.write_text(json.dumps(exp5_task) + "\n", encoding="utf-8")

    missing = recompute_paper_formal_metrics(tmp_path)
    assert "missing_canonical_event_evidence" in missing.experiment_rows[EXP3][0][
        "paper_ineligibility_reasons"
    ]
    assert "missing_ablation_observation" in missing.experiment_rows[EXP4][0][
        "paper_ineligibility_reasons"
    ]
    assert "missing_model_execution_v2_record" in missing.experiment_rows[EXP5][0][
        "paper_ineligibility_reasons"
    ]


def test_exp3_metrics_derive_recovery_from_protocol_evidence_and_dedicated_baseline(
    tmp_path: Path,
) -> None:
    baseline_path = (
        tmp_path
        / "supporting_baselines"
        / "exp3-worker-baseline"
        / "case-exp3"
        / "baseline_evidence.json"
    )
    baseline_body = {
        "schema_version": "tokenshare.paper_exp3_baseline_evidence.v1",
        "condition_id": "exp3-worker-baseline",
        "condition_digest": "sha256:" + "b" * 64,
        "case_id": "case-exp3",
        "repeat_id": 0,
        "seed": 330001,
        "worker_count": 10,
        "request_limits": {"max_tokens": 1024, "timeout_seconds": 30},
        "task_result": {
            "root_status": "completed",
            "wall_clock_ms": 200,
            "provider_attempt_count": 1,
            "total_tokens": 20,
            "cost_estimate": 0.2,
        },
        "attempt_results": [
            {
                "attempt_id": "baseline-attempt",
                "unit_id": "unit-1",
                "attempt_status": "succeeded",
                "canonical": True,
                "total_tokens": 20,
                "cost_estimate": 0.2,
                "latency_ms": 150,
            }
        ],
        "event_records": [
            {
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "object_id": "unit-1",
                "payload": {"new_state": "Completed"},
            }
        ],
    }
    _write_json(baseline_path, baseline_body)
    baseline_ref = {
        "path": baseline_path.relative_to(tmp_path).as_posix(),
        "content_hash": "sha256:"
        + hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "condition_id": "exp3-worker-baseline",
        "case_id": "case-exp3",
    }
    generation = _write_run(
        tmp_path,
        experiment_id=EXP3,
        condition_id="exp3-evidence-derived",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "fault_type": "false_negative",
            "seed": 330001,
        },
        task={
            "task_id": "case-exp3",
            "root_status": "completed",
            "paper_eligible": True,
            "matched_baseline_condition_id": "exp3-worker-baseline",
            "matched_baseline_evidence_ref": baseline_ref,
        },
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=50),
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
                "attempt_status": "verification_rejected",
                "canonical": False,
                "started_at": "2026-07-25T00:00:00.000+00:00",
                "ended_at": "2026-07-25T00:00:00.050+00:00",
                "paper_eligible": True,
            },
            {
                **_attempt(total_tokens=20, latency_ms=100),
                "attempt_id": "replacement-attempt",
                "unit_id": "unit-1",
                "attempt_status": "succeeded",
                "canonical": True,
                "started_at": "2026-07-25T00:00:00.150+00:00",
                "ended_at": "2026-07-25T00:00:00.250+00:00",
                "paper_eligible": True,
            },
        ],
        events=[
            {
                "event_id": "verification-fault",
                "event_type": "VERIFICATION_RECORDED",
                "payload": {
                    "attempt_id": "fault-attempt",
                    "unit_id": "unit-1",
                    "status": "failed",
                },
            },
            {
                "event_id": "recovery-fault",
                "event_type": "RECOVERY_ACTION_RECORDED",
                "occurred_at": "2026-07-25T00:00:00.100+00:00",
                "payload": {
                    "recovery_action": {
                        "attempt_id": "fault-attempt",
                        "unit_id": "unit-1",
                        "retry_allowed": True,
                    }
                },
            },
            {
                "event_id": "canonical-replacement",
                "event_type": "CANONICAL_OUTPUTS_BOUND",
                "payload": {
                    "selected_attempt_id": "replacement-attempt",
                    "unit_id": "unit-1",
                },
            },
            {
                "event_id": "completed-replacement",
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "object_id": "unit-1",
                "payload": {"new_state": "Completed"},
            },
        ],
        faults=[
            {
                "fault_injection_id": "fault-1",
                "fault_type": "false_negative",
                "attempt_id": "fault-attempt",
                "unit_id": "unit-1",
            }
        ],
    )
    _write_suite_manifest(tmp_path, (EXP3,))

    result = recompute_paper_formal_metrics(tmp_path)
    row = result.experiment_rows[EXP3][0]

    assert row["detection_rate"] == pytest.approx(1.0)
    assert row["false_accept_rate"] == pytest.approx(0.0)
    assert row["recoverable_fault_count"] == 1
    assert row["recovery_rate"] == pytest.approx(1.0)
    assert row["completion_rate"] == pytest.approx(1.0)
    assert row["recovery_latency_ms"] == pytest.approx(100.0)
    assert row["reassignment_count"] == 1
    assert row["wasted_actual_tokens"] == 10
    assert row["wasted_actual_tokens_sample_size"] == 1
    assert row["wasted_actual_tokens_missing_count"] == 0
    assert row["outcome_evidence_complete"] is True
    assert row["matched_baseline_source"] == "dedicated_condition_evidence"
    assert row["token_overhead"] == 10

    task_path = generation / "per_task_results.jsonl"
    self_baselined_task = json.loads(
        task_path.read_text(encoding="utf-8")
    )
    self_baselined_task["matched_baseline_condition_id"] = (
        "exp3-evidence-derived"
    )
    task_path.write_text(
        json.dumps(self_baselined_task) + "\n",
        encoding="utf-8",
    )

    self_baselined = recompute_paper_formal_metrics(tmp_path).experiment_rows[
        EXP3
    ][0]
    assert self_baselined["matched_baseline_source"] == "not_available"
    assert "self_matched_baseline_forbidden" in self_baselined[
        "paper_ineligibility_reasons"
    ]

    self_baselined_task["matched_baseline_condition_id"] = (
        "exp3-worker-baseline"
    )
    task_path.write_text(
        json.dumps(self_baselined_task) + "\n",
        encoding="utf-8",
    )

    event_path = generation / "events" / "event_log.jsonl"
    events = [
        event
        for event in (
            json.loads(line)
            for line in event_path.read_text(encoding="utf-8").splitlines()
        )
        if event["event_type"] != "RECOVERY_ACTION_RECORDED"
    ]
    _write_jsonl(event_path, events)

    missing_recovery = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][
        0
    ]
    assert missing_recovery["recovery_rate"] is None
    assert "missing_recovery_event_evidence" in missing_recovery[
        "paper_ineligibility_reasons"
    ]

    attempt_path = generation / "per_attempt_results.jsonl"
    attempt_rows = [
        json.loads(line)
        for line in attempt_path.read_text(encoding="utf-8").splitlines()
    ]
    for attempt_row in attempt_rows:
        if attempt_row["attempt_id"] == "fault-attempt":
            attempt_row["total_tokens"] = None
    _write_jsonl(attempt_path, attempt_rows)

    missing_usage = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][0]
    assert missing_usage["wasted_actual_tokens"] is None
    assert missing_usage["wasted_actual_tokens_sample_size"] == 0
    assert missing_usage["wasted_actual_tokens_missing_count"] == 1


def test_exp3_metrics_use_shared_exp1_usage_and_null_failed_source_comparison(
    tmp_path: Path,
) -> None:
    source_runtime_identity = {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": "source-exp1-run",
        "task_id": "case-exp3",
        "root_unit_id": "source-exp1-root",
        "ledger_digest": "sha256:" + "4" * 64,
    }
    source_generation = _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="exp1-factor-easy-w10-r0",
        condition={"domain": "factorization", "worker_count": 10, "seed": 1},
        task={
            "task_id": "case-exp3",
            "root_status": "completed",
            "provider_attempt_count": 1,
            "wall_clock_ms": 200,
            "runtime_generation_identity": source_runtime_identity,
        },
        attempts=[
            {
                **_attempt(total_tokens=20, latency_ms=150),
                "canonical": True,
                "provider_attempt_count": 1,
                "prompt_tokens": 8,
                "completion_tokens": 12,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "estimated",
            }
        ],
        events=_timing_events(200),
    )
    source_task = json.loads(
        (source_generation / "per_task_results.jsonl").read_text(encoding="utf-8")
    )
    source_attempt = json.loads(
        (source_generation / "per_attempt_results.jsonl").read_text(
            encoding="utf-8"
        )
    )
    source_event = json.loads(
        (source_generation / "events" / "event_log.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    source_artifact = tmp_path / "source-exp1-artifact.bin"
    source_artifact.write_bytes(b"source exp1 artifact")
    shared_reference_core = {
        "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
        "source_experiment_id": EXP1,
        "source_condition_id": "exp1-factor-easy-w10-r0",
        "source_case_id": "case-exp3",
        "source_task_id": "case-exp3",
        "source_repeat_id": 0,
        "source_root_status": "completed",
        "evidence_integrity": "complete",
        "baseline_comparison_eligible": True,
        "baseline_unavailable_reason": None,
        "source_usage": {
            "provider_attempt_count": 1,
            "expected_provider_attempt_count": 1,
            "prompt_tokens": 8,
            "completion_tokens": 12,
            "total_tokens": 20,
            "cost_estimate": 0.1,
            "usage_complete": True,
            "usage_missing_provider_attempt_count": 0,
            "cost_estimate_status": "estimated",
            "cost_estimate_currency": "USD",
        },
        "source_versions": {
            "schema_version": "tokenshare.paper_execution_version_identity.v1",
            "plugin_version": "plugin-test-v1",
            "parser_version": "parser-test-v1",
            "verifier_version": "verifier-test-v1",
            "executor_version": "executor-test-v1",
            "prompt_version": "prompt-test-v1",
            "split_profile_digest": "sha256:" + "6" * 64,
            "runtime_generation_schema_version": (
                "tokenshare.paper_runtime_generation_identity.v1"
            ),
            "runtime_generation_identity_digest": formal_metrics._digest(
                source_runtime_identity
            ),
        },
        "source_task_ref": {
            "path": (
                source_generation / "per_task_results.jsonl"
            ).relative_to(tmp_path).as_posix(),
            "record_hash": formal_metrics._digest(source_task),
        },
        "source_attempt_refs": [
            {
                "path": (
                    source_generation / "per_attempt_results.jsonl"
                ).relative_to(tmp_path).as_posix(),
                "record_hash": formal_metrics._digest(source_attempt),
            }
        ],
        "source_event_refs": [
            {
                "path": (
                    source_generation / "events" / "event_log.jsonl"
                ).relative_to(tmp_path).as_posix(),
                "record_hash": formal_metrics._digest(source_event),
            }
        ],
        "source_artifact_refs": [
            {
                "path": source_artifact.relative_to(tmp_path).as_posix(),
                "content_hash": "sha256:"
                + hashlib.sha256(source_artifact.read_bytes()).hexdigest(),
            }
        ],
    }
    shared_reference = {
        **shared_reference_core,
        "source_hash": formal_metrics._digest(shared_reference_core),
    }
    exp3_generation = _write_run(
        tmp_path,
        experiment_id=EXP3,
        condition_id="exp3-shared-reference",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "fault_type": "false_positive",
            "seed": 330001,
        },
        task={
            "task_id": "case-exp3",
            "root_status": "completed",
            "matched_baseline_condition_id": "exp1-factor-easy-w10-r0",
            "matched_baseline_evidence_ref": shared_reference,
            "shared_exp1_reference": shared_reference,
            "baseline_comparison_eligible": True,
            "baseline_unavailable_reason": None,
        },
        attempts=[
            {
                **_attempt(total_tokens=30, latency_ms=180),
                "canonical": True,
            }
        ],
        events=_timing_events(250),
    )
    _write_suite_manifest(tmp_path, (EXP1, EXP3))

    row = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][0]

    assert row["matched_baseline_source"] == "shared_exp1_reference"
    assert row["comparison_kind"] == "shared_reference"
    assert row["baseline_comparison_eligible"] is True
    assert row["matched_baseline_condition_ids"] == [
        "exp1-factor-easy-w10-r0"
    ]
    assert row["matched_baseline_total_tokens"] == 20
    assert row["shared_reference_source_usage"]["total_tokens"] == 20
    assert row["token_overhead"] == 10

    task_path = exp3_generation / "per_task_results.jsonl"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    source_task_path = source_generation / "per_task_results.jsonl"
    source_task["root_status"] = "failed"
    source_task_path.write_text(json.dumps(source_task) + "\n", encoding="utf-8")
    failed_reference = {
        **shared_reference,
        "source_root_status": "failed",
        "source_task_ref": {
            **shared_reference["source_task_ref"],
            "record_hash": formal_metrics._digest(source_task),
        },
        "baseline_comparison_eligible": False,
        "baseline_unavailable_reason": "source_exp1_failed_experimental",
    }
    failed_reference["source_hash"] = formal_metrics._digest(
        {
            key: value
            for key, value in failed_reference.items()
            if key not in {"source_hash", "source_reference_id"}
        }
    )
    task.update(
        {
            "matched_baseline_evidence_ref": failed_reference,
            "shared_exp1_reference": failed_reference,
            "baseline_comparison_eligible": False,
            "baseline_unavailable_reason": "source_exp1_failed_experimental",
        }
    )
    task_path.write_text(json.dumps(task) + "\n", encoding="utf-8")

    failed = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][0]

    assert failed["matched_baseline_source"] == "shared_exp1_reference"
    assert failed["baseline_comparison_eligible"] is False
    assert failed["baseline_unavailable_reason"] == (
        "source_exp1_failed_experimental"
    )
    assert failed["shared_reference_source_usage"]["total_tokens"] == 20
    assert failed["matched_baseline_total_tokens"] is None
    assert failed["token_overhead"] is None

    invalid_event_reference = {
        **failed_reference,
        "source_event_refs": [
            {
                "path": "missing/source-event.jsonl",
                "record_hash": failed_reference["source_event_refs"][0]["record_hash"],
            }
        ],
    }
    invalid_event_reference["source_hash"] = formal_metrics._digest(
        {
            key: value
            for key, value in invalid_event_reference.items()
            if key not in {"source_hash", "source_reference_id"}
        }
    )
    task.update(
        {
            "matched_baseline_evidence_ref": invalid_event_reference,
            "shared_exp1_reference": invalid_event_reference,
        }
    )
    task_path.write_text(json.dumps(task) + "\n", encoding="utf-8")

    invalid_event = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][0]

    assert invalid_event["baseline_comparison_eligible"] is False
    assert invalid_event["baseline_unavailable_reason"] == (
        "invalid_shared_exp1_event_reference"
    )
    assert "invalid_shared_exp1_event_reference" in invalid_event[
        "paper_ineligibility_reasons"
    ]

    tampered_reference = {
        **shared_reference,
        "source_usage": {**shared_reference["source_usage"], "total_tokens": 0},
    }
    task.update(
        {
            "matched_baseline_evidence_ref": tampered_reference,
            "shared_exp1_reference": tampered_reference,
            "baseline_comparison_eligible": True,
            "baseline_unavailable_reason": None,
        }
    )
    task_path.write_text(json.dumps(task) + "\n", encoding="utf-8")

    tampered = recompute_paper_formal_metrics(tmp_path).experiment_rows[EXP3][0]

    assert tampered["baseline_comparison_eligible"] is False
    assert tampered["matched_baseline_total_tokens"] is None
    assert tampered["token_overhead"] is None
    assert "shared_exp1_reference_hash_mismatch" in tampered[
        "paper_ineligibility_reasons"
    ]


def test_exp3_multi_case_condition_preserves_all_shared_source_condition_ids() -> None:
    scalar, condition_ids = formal_metrics._matched_baseline_condition_identity(
        tasks=(
            {"matched_baseline_condition_id": "exp1-factor-easy-w10-r0"},
            {"matched_baseline_condition_id": "exp1-factor-medium-w10-r0"},
            {"matched_baseline_condition_id": "exp1-factor-hard-w10-r0"},
        ),
        baseline_manifest={},
    )

    assert scalar is None
    assert condition_ids == [
        "exp1-factor-easy-w10-r0",
        "exp1-factor-hard-w10-r0",
        "exp1-factor-medium-w10-r0",
    ]


def test_shared_source_usage_comparison_rejects_boolean_integer_alias() -> None:
    recomputed = {
        "provider_attempt_count": 1,
        "expected_provider_attempt_count": 1,
        "prompt_tokens": 8,
        "completion_tokens": 12,
        "total_tokens": 20,
        "cost_estimate": 0.1,
        "usage_complete": True,
        "usage_missing_provider_attempt_count": 0,
        "cost_estimate_status": "estimated",
        "cost_estimate_currency": "USD",
    }
    frozen = {**recomputed, "provider_attempt_count": True}

    assert not formal_metrics._shared_source_usage_matches(
        frozen=frozen,
        recomputed=recomputed,
    )


def test_exp3_two_repeat_aggregate_reports_min_max_and_relative_difference() -> None:
    rows = []
    for repeat_id, scale in ((0, 1.0), (1, 1.5)):
        rows.append(
            {
                "experiment_id": EXP3,
                "condition_id": f"exp3-repeat-{repeat_id}",
                "row_scope": "repeat_condition",
                "repeat_id": repeat_id,
                "domain": "factorization",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "topic_family": None,
                "case_id": None,
                "worker_count": 10,
                "fault_type": "worker_death",
                "fault_rate": 0.0,
                "ablation_mode": "FULL",
                "cohort_member_id": None,
                "provider_family": "siliconflow",
                "provider_model_id": "zai-org/GLM-5.2",
                "model_entry_id": "glm_5_2_exp1_baseline",
                "worker_death_count": 1,
                "dead_worker_count": 1,
                "actual_dead_worker_count": 1,
                "target_dead_worker_count": 1,
                "kill_progress": 0.5 * scale,
                "coordinator_continued": True,
                "required_slot_count": 2,
                "recovered_slot_count": 2,
                "result_completeness_rate": 1.0,
                "result_completeness_applicability": "applicable",
                "root_output_complete": True,
                "accepted_validity": True,
                "worker_death_evidence_refs": [
                    {"event_id": f"worker-death-{repeat_id}"}
                ],
                "kill_progress_target_ratio": 0.5,
                "wall_clock_ms": 100 * scale,
                "completed_root_count": 1,
                "task_count": 1,
                "total_tokens": int(20 * scale),
                "total_cost_estimate": 0.2 * scale,
                "detection_rate": 1.0,
                "false_accept_rate": 0.0,
                "recovery_rate": 1.0,
                "completion_rate": 1.0,
                "recovery_latency_ms": 40 * scale,
                "reassignment_count": 1,
                "wasted_actual_tokens": int(5 * scale),
                "wall_clock_overhead_ms": 20 * scale,
                "token_overhead": int(4 * scale),
                "provider_latency_overhead_ms": 10 * scale,
                "evidence_completeness_rate": 1.0,
                "kill_progress_actual_ratio_mean": 0.5 * scale,
                "paper_eligible": True,
                "paper_ineligibility_reasons": [],
            }
        )

    result = formal_metrics._with_repeat_aggregates(EXP3, rows)
    aggregate = next(
        row for row in result if row.get("row_scope") == "repeat_aggregate"
    )

    assert len(result) == 3
    assert aggregate["recovery_latency_ms"] is None
    assert aggregate["recovery_latency_ms_min"] == pytest.approx(40)
    assert aggregate["recovery_latency_ms_max"] == pytest.approx(60)
    assert aggregate["recovery_latency_ms_relative_difference"] == pytest.approx(
        0.5
    )
    assert aggregate["wasted_actual_tokens_min"] == 5
    assert aggregate["wasted_actual_tokens_max"] == 7
    assert aggregate["kill_progress_actual_ratio_mean_min"] == pytest.approx(0.5)
    assert aggregate["kill_progress_actual_ratio_mean_max"] == pytest.approx(0.75)
    assert aggregate["dead_worker_count"] == 2
    assert aggregate["required_slot_count"] == 4
    assert aggregate["recovered_slot_count"] == 4
    assert aggregate["result_completeness_rate"] == 1.0
    assert aggregate["coordinator_continued"] is True
    assert aggregate["root_output_complete"] is True
    assert aggregate["accepted_validity"] is True
    assert len(aggregate["worker_death_evidence_refs"]) == 2


def test_exp4_csv_is_evidence_derived_paired_and_three_level(tmp_path: Path) -> None:
    for repeat_id in (0, 1, 2):
        for mode in ("FULL", "NO_VERIFICATION"):
            condition_id = f"exp4-{mode.lower()}-r{repeat_id}"
            attempt_id = f"attempt-{mode.lower()}-{repeat_id}"
            invalid = mode != "FULL"
            raw_ref = {
                "artifact_id": f"raw-{mode.lower()}-{repeat_id}",
                "content_hash": "sha256:" + "1" * 64,
            }
            candidate_ref = {
                "artifact_id": f"candidate-{mode.lower()}-{repeat_id}",
                "content_hash": "sha256:" + "2" * 64,
            }
            canonical_refs = {
                "range_result": {
                    "artifact_id": f"canonical-{mode.lower()}-{repeat_id}",
                    "content_hash": "sha256:" + "3" * 64,
                }
            }
            attempt = {
                **_attempt(total_tokens=10 + repeat_id, latency_ms=30),
                **_timed_attempt(
                    attempt_id,
                    f"unit-{mode.lower()}-{repeat_id}",
                    0,
                    30,
                ),
                "paper_eligible": True,
            }
            _write_run(
                tmp_path,
                experiment_id=EXP4,
                condition_id=condition_id,
                condition={
                    "domain": "factorization",
                    "worker_count": 10,
                    "ablation_mode": mode,
                },
                task={
                    "task_id": "shared-case",
                    "case_id": "shared-case",
                    "root_status": "completed",
                    "accepted_validity": not invalid,
                    "paper_eligible": True,
                    "runtime_generation_identity": {
                        "run_id": f"run-{mode.lower()}-{repeat_id}",
                        "root_unit_id": "root",
                    },
                    "ablation_runtime": {
                        "schema_version": "tokenshare.paper_ablation_runtime.v1",
                        "condition_id": condition_id,
                        "case_id": "shared-case",
                        "repeat_id": repeat_id,
                        "mode": mode,
                        "attempt_observations": [
                            {
                                "attempt_id": attempt_id,
                                "raw_output_ref": raw_ref,
                                "candidate_output_ref": candidate_ref,
                                "canonical_output_refs": canonical_refs,
                                "independent_candidate_validity": not invalid,
                                "final_validity": not invalid,
                            }
                        ],
                        "hook_observations": (
                            []
                            if mode == "FULL"
                            else [
                                {
                                    "event_type": "EXPERIMENT_ABLATION_GATE_APPLIED",
                                    "ablation_mode": mode,
                                    "disabled_mechanism": "verification",
                                    "artifact_refs": [raw_ref],
                                    "hook_input": {
                                        "task_id": "shared-case",
                                        "unit_id": f"unit-{mode.lower()}-{repeat_id}",
                                        "attempt_id": attempt_id,
                                    },
                                    "hook_result": {
                                        "bypass": True,
                                        "stop": False,
                                    },
                                }
                            ]
                        ),
                    },
                },
                attempts=[attempt],
                events=_critical_path_events(
                    required=(f"unit-{mode.lower()}-{repeat_id}",),
                    canonical_ms={f"unit-{mode.lower()}-{repeat_id}": 30},
                    merge_gate_ms=30,
                    merge_done_ms=31 + repeat_id,
                    root_done_ms=31 + repeat_id,
                ),
                artifacts=[raw_ref, candidate_ref, *canonical_refs.values()],
                repeat_id=repeat_id,
            )
    _write_suite_manifest(tmp_path, (EXP4,))

    result = recompute_paper_formal_metrics(tmp_path)
    rows = result.experiment_rows[EXP4]
    task_rows = [row for row in rows if row["row_scope"] == "task"]
    repeat_rows = [
        row for row in rows if row["row_scope"] == "repeat_condition"
    ]
    aggregate_rows = [
        row for row in rows if row["row_scope"] == "three_repeat_aggregate"
    ]

    assert len(task_rows) == 6
    assert len(repeat_rows) == 6
    assert len(aggregate_rows) == 2
    no_verification_tasks = [
        row for row in task_rows if row["ablation_mode"] == "NO_VERIFICATION"
    ]
    assert all(row["wrong_canonical_count"] == 1 for row in no_verification_tasks)
    assert all(
        row["paired_full_condition_id"] == f"exp4-full-r{row['repeat_id']}"
        for row in no_verification_tasks
    )
    no_verification_aggregate = next(
        row
        for row in aggregate_rows
        if row["ablation_mode"] == "NO_VERIFICATION"
    )
    assert no_verification_aggregate["wrong_canonical_count"] == 3
    assert no_verification_aggregate[
        "wrong_canonical_acceptance_rate"
    ] == pytest.approx(1.0)

    header = (
        tmp_path / "metrics" / "paper_table_ablation.csv"
    ).read_text(encoding="utf-8").splitlines()[0]
    for field in (
        "wrong_canonical_count",
        "wrong_canonical_acceptance_rate",
        "raw_only_exposure_count",
        "raw_only_acceptance_rate",
        "stuck_task_count",
        "stuck_task_rate",
        "premature_merge_attempt_count",
        "premature_merge_failure_rate",
        "exposed_error_count",
        "escaped_error_count",
        "error_escape_rate",
        "error_escape_applicability",
    ):
        assert field in header

    task_path = (
        tmp_path
        / "experiments"
        / EXP4
        / "runs"
        / "exp4-no_verification-r0"
        / "0"
        / ".generations"
        / "generation-1"
        / "per_task_results.jsonl"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["ablation_mode"] = "NO_PARSER_POLICY"
    task_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    condition_path = tmp_path / "conditions.jsonl"
    conditions = [
        json.loads(line)
        for line in condition_path.read_text(encoding="utf-8").splitlines()
    ]
    next(
        condition
        for condition in conditions
        if condition["condition_id"] == "exp4-no_verification-r0"
    )["ablation_mode"] = "NO_PARSER_POLICY"
    _write_jsonl(condition_path, conditions)

    relabeled = recompute_paper_formal_metrics(tmp_path)
    relabeled_task = next(
        row
        for row in relabeled.experiment_rows[EXP4]
        if row["row_scope"] == "task"
        and row["condition_id"] == "exp4-no_verification-r0"
    )
    original_task = next(
        row
        for row in task_rows
        if row["condition_id"] == "exp4-no_verification-r0"
    )
    assert relabeled_task["paper_eligible"] is False
    assert "ablation_mode_mismatch" in relabeled_task[
        "paper_ineligibility_reasons"
    ]
    assert relabeled_task["wrong_canonical_count"] is None
    assert original_task["wrong_canonical_count"] == 1


def test_wall_clock_quantiles_are_only_reported_for_three_repeat_aggregate(
    tmp_path: Path,
) -> None:
    for repeat_id, wall_ms in ((0, 100), (1, 200)):
        _write_run(
            tmp_path,
            experiment_id=EXP1,
            condition_id=f"exp1-repeat-{repeat_id}",
            condition={"domain": "factorization", "worker_count": 10},
            task={"task_id": f"case-{repeat_id}", "root_status": "completed"},
            attempts=[_attempt(total_tokens=10, latency_ms=20)],
            events=_timing_events(wall_ms),
            repeat_id=repeat_id,
        )
    _write_suite_manifest(tmp_path, (EXP1,))

    two_repeat = recompute_paper_formal_metrics(tmp_path)
    two_aggregate = next(
        row
        for row in two_repeat.experiment_rows[EXP1]
        if row["row_scope"] == "repeat_aggregate"
    )
    assert two_aggregate["wall_clock_min_ms"] == pytest.approx(100)
    assert two_aggregate["wall_clock_max_ms"] == pytest.approx(200)
    assert two_aggregate["wall_clock_relative_difference"] == pytest.approx(1.0)
    assert "wall_clock_p50" not in two_aggregate
    assert "wall_clock_p95" not in two_aggregate

    _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="exp1-repeat-2",
        condition={"domain": "factorization", "worker_count": 10},
        task={"task_id": "case-2", "root_status": "completed"},
        attempts=[_attempt(total_tokens=10, latency_ms=20)],
        events=_timing_events(300),
        repeat_id=2,
    )
    three_repeat = recompute_paper_formal_metrics(tmp_path)
    three_aggregate = next(
        row
        for row in three_repeat.experiment_rows[EXP1]
        if row["row_scope"] == "repeat_aggregate"
    )
    assert three_aggregate["wall_clock_p50"] == pytest.approx(200)
    assert three_aggregate["wall_clock_p95"] == pytest.approx(290)


def test_exp5_formal_endpoint_table_uses_strict_v2_rows_and_three_repeat_aggregate(
    tmp_path: Path,
) -> None:
    for repeat_id, wall_ms in ((0, 100), (1, 140), (2, 180)):
        condition_id = f"exp5-glm-factor-hard-r{repeat_id}"
        attempt = {
            **_attempt(total_tokens=20 + repeat_id, latency_ms=50 + repeat_id),
            "worker_id": "worker-1",
            "provider_attempt_index": 0,
            "request_ref": {"artifact_id": f"request-{repeat_id}"},
            "raw_output_ref": {"artifact_id": f"raw-{repeat_id}"},
            "parsed_output_ref": None,
            "parse_failure_ref": None,
            "provenance_ref": {"artifact_id": f"provenance-{repeat_id}"},
            "usage_ref": {"artifact_id": f"usage-{repeat_id}"},
            "model_execution_record_ref": {
                "artifact_id": f"model-record-{repeat_id}"
            },
            "started_at": f"2026-07-25T00:00:0{repeat_id}Z",
            "ended_at": f"2026-07-25T00:00:1{repeat_id}Z",
            "prompt_tokens": 8,
            "completion_tokens": 12 + repeat_id,
            "error_kind": None,
            "paper_eligible": True,
        }
        model_item = _exp5_model_item(
            condition_id=condition_id,
            task_id="case-exp5-hard",
            attempt=attempt,
            repeat_id=repeat_id,
        )
        _write_run(
            tmp_path,
            experiment_id=EXP5,
            condition_id=condition_id,
            condition={
                "domain": "factorization",
                "difficulty": "hard",
                "paper_difficulty": "hard",
                "worker_count": 10,
                "provider_family": "siliconflow",
                "provider_model_id": "zai-org/GLM-5.2",
                "model_entry_id": "glm_5_2_exp1_baseline",
                "cohort_member_id": "glm_5_2_siliconflow",
            },
            task={
                "task_id": "case-exp5-hard",
                "root_status": "completed",
                "accepted_validity": True,
                "paper_eligible": True,
                "model_execution_records": [model_item],
            },
            attempts=[model_item["attempt"]],
            events=_timing_events(wall_ms),
            repeat_id=repeat_id,
        )
    _write_suite_manifest(tmp_path, (EXP5,))

    result = recompute_paper_formal_metrics(tmp_path)

    rows = result.experiment_rows[EXP5]
    repeats = [row for row in rows if row["row_scope"] == "repeat_condition"]
    aggregate = next(
        row for row in rows if row["row_scope"] == "three_repeat_aggregate"
    )
    assert len(repeats) == 3
    assert aggregate["repeat_count"] == 3
    assert aggregate["completion_rate"] == 1.0
    assert aggregate["accepted_validity_rate"] == 1.0
    assert aggregate["model_identity_match_rate"] == 1.0
    assert aggregate["provider_confounding"] == "model_provider_endpoint_pair"
    for field_name in (
        "completion_rate",
        "accepted_validity_rate",
        "total_tokens",
        "total_cost_estimate",
        "wall_clock_ms",
        "provider_latency_sum_ms",
        "provider_error_count",
        "rate_limited_attempt_count",
        "retry_attempt_count",
        "identity_status",
        "provider_confounding",
    ):
        assert field_name in repeats[0]

    endpoint_table = (
        tmp_path / "metrics" / "paper_table_model_endpoint_comparison.csv"
    )
    assert endpoint_table.is_file()
    header = endpoint_table.read_text(encoding="utf-8").splitlines()[0]
    assert "provider_confounding" in header
    model_rows = [
        json.loads(line)
        for line in (
            tmp_path / "model_execution_records.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(model_rows) == 3
    assert all(
        row["schema_version"]
        == "tokenshare.paper_model_execution_record.v2"
        for row in model_rows
    )
    assert (
        tmp_path / "metrics" / "model_execution_records.jsonl"
    ).read_bytes() == (tmp_path / "model_execution_records.jsonl").read_bytes()
    assert all("model_execution_ref" not in row for row in model_rows)


def test_protocol_critical_path_uses_parallel_dependency_graph_not_sum_or_wall() -> None:
    task = {"runtime_generation_identity": {"root_unit_id": "root"}}
    attempts = [
        _timed_attempt("attempt-a", "unit-a", 0, 100),
        _timed_attempt("attempt-b", "unit-b", 0, 100),
    ]
    events = _critical_path_events(
        required=("unit-a", "unit-b"),
        canonical_ms={"unit-a": 100, "unit-b": 100},
        merge_gate_ms=100,
        merge_done_ms=120,
        root_done_ms=120,
    )

    result = formal_metrics._protocol_critical_path(
        task=task,
        attempts=attempts,
        events=events,
    )

    assert result["critical_path_ms"] == pytest.approx(120)
    assert result["critical_path_ms"] != 200
    assert result["critical_path_ms"] != 1000
    assert result["critical_path_source"] == "protocol_dependency_graph"
    assert result["critical_path_evidence_refs"]


def test_protocol_critical_path_accumulates_serial_dependency_and_merge_wait() -> None:
    task = {"runtime_generation_identity": {"root_unit_id": "root"}}
    attempts = [
        _timed_attempt("attempt-a", "unit-a", 0, 100),
        _timed_attempt("attempt-b", "unit-b", 100, 200),
    ]
    events = [
        {
            "event_id": "relation-a-b",
            "event_type": "TASK_RELATION_CREATED",
            "occurred_at": _ms_timestamp(100),
            "payload": {
                "task_relation": {
                    "source_unit_id": "unit-a",
                    "target_unit_id": "unit-b",
                }
            },
        },
        {
            "event_id": "created-unit-a",
            "event_type": "TASK_UNIT_CREATED",
            "object_id": "unit-a",
            "occurred_at": _ms_timestamp(0),
            "payload": {"task_unit": {"unit_id": "unit-a"}},
        },
        *_critical_path_events(
            required=("unit-b",),
            canonical_ms={"unit-b": 225},
            merge_gate_ms=250,
            merge_done_ms=280,
            root_done_ms=280,
        ),
    ]

    result = formal_metrics._protocol_critical_path(
        task=task,
        attempts=attempts,
        events=events,
    )

    assert result["critical_path_ms"] == pytest.approx(280)


def test_protocol_critical_path_includes_root_to_first_attempt_wait() -> None:
    task = {"runtime_generation_identity": {"root_unit_id": "root"}}
    attempts = [_timed_attempt("attempt-a", "unit-a", 50, 150)]
    events = _critical_path_events(
        required=("unit-a",),
        canonical_ms={"unit-a": 150},
        merge_gate_ms=150,
        merge_done_ms=170,
        root_done_ms=170,
    )

    result = formal_metrics._protocol_critical_path(
        task=task,
        attempts=attempts,
        events=events,
    )

    assert result["critical_path_ms"] == pytest.approx(170)


def test_protocol_critical_path_fails_closed_without_dependency_evidence() -> None:
    result = formal_metrics._protocol_critical_path(
        task={"runtime_generation_identity": {"root_unit_id": "root"}},
        attempts=[_timed_attempt("attempt-a", "unit-a", 0, 100)],
        events=[],
    )

    assert result["critical_path_ms"] is None
    assert "missing_merge_dependency_evidence" in result[
        "paper_ineligibility_reasons"
    ]


@pytest.mark.parametrize("experiment_id", (EXP1, EXP3, EXP4, EXP5))
def test_non_scalability_failed_roots_do_not_require_success_critical_path(
    experiment_id: str,
) -> None:
    row = formal_metrics._condition_metrics(
        _evidence_complete_failed_condition_bundle(experiment_id)
    )

    assert row["completion_rate"] == pytest.approx(0.0)
    assert row["critical_path_ms"] is None
    assert row["paper_eligible"] is True
    assert "missing_merge_dependency_evidence" not in row[
        "paper_ineligibility_reasons"
    ]


def test_exp2_still_requires_protocol_critical_path_evidence() -> None:
    row = formal_metrics._condition_metrics(
        _evidence_complete_failed_condition_bundle(EXP2)
    )

    assert row["completion_rate"] == pytest.approx(0.0)
    assert row["critical_path_ms"] is None
    assert row["paper_eligible"] is False
    assert "missing_merge_dependency_evidence" in row[
        "paper_ineligibility_reasons"
    ]


def test_exp4_empty_or_mismatched_hook_observations_are_ineligible() -> None:
    base = {
        "experiment_id": EXP4,
        "condition_id": "condition-no-verification",
        "repeat_id": 0,
        "ablation_mode": "NO_VERIFICATION",
        "paper_eligible": True,
        "paper_ineligibility_reasons": [],
    }
    task = {
        "task_id": "case-1",
        "case_id": "case-1",
        "root_status": "completed",
        "ablation_runtime": {
            "schema_version": "tokenshare.paper_ablation_runtime.v1",
            "condition_id": "condition-no-verification",
            "case_id": "case-1",
            "repeat_id": 0,
            "mode": "NO_VERIFICATION",
            "attempt_observations": [],
            "hook_observations": [],
        },
    }

    empty = formal_metrics._exp4_task_row(
        base_row=base,
        task=task,
        attempts=[],
    )
    assert empty["paper_eligible"] is False
    assert empty["wrong_canonical_count"] is None

    task["ablation_runtime"]["mode"] = "NO_REQUEUE"
    mismatched = formal_metrics._exp4_task_row(
        base_row=base,
        task=task,
        attempts=[],
    )
    assert mismatched["paper_eligible"] is False
    assert "ablation_mode_mismatch" in mismatched["paper_ineligibility_reasons"]


def test_artifact_inventory_requires_id_hash_and_matches_all_provided_fields() -> None:
    content_hash = "sha256:" + "1" * 64
    inventory = [
        {
            "artifact_id": "artifact-1",
            "content_hash": content_hash,
            "path": "runs/case-1/artifacts/artifact-1",
            "uri": "artifacts/artifact-1",
        }
    ]

    assert formal_metrics._artifact_ref_in_inventory(
        {"uri": "artifacts/artifact-1"},
        inventory,
    ) is False
    assert formal_metrics._artifact_ref_in_inventory(
        {
            "artifact_id": "artifact-1",
            "content_hash": content_hash,
        },
        inventory,
    ) is True
    assert formal_metrics._artifact_ref_in_inventory(
        {
            "artifact_id": "artifact-1",
            "content_hash": content_hash,
            "path": "runs/case-1/artifacts/artifact-1",
            "uri": "artifacts/artifact-1",
        },
        inventory,
    ) is True
    for field_name, conflicting_value in (
        ("artifact_id", "artifact-2"),
        ("content_hash", "sha256:" + "2" * 64),
        ("path", "runs/case-1/artifacts/artifact-2"),
        ("uri", "artifacts/artifact-2"),
    ):
        reference = {
            "artifact_id": "artifact-1",
            "content_hash": content_hash,
            "path": "runs/case-1/artifacts/artifact-1",
            "uri": "artifacts/artifact-1",
        }
        reference[field_name] = conflicting_value
        assert formal_metrics._artifact_ref_in_inventory(
            reference,
            inventory,
        ) is False


def test_exp4_applied_hook_requires_input_result_and_persisted_refs() -> None:
    raw_ref = {
        "artifact_id": "raw-1",
        "content_hash": "sha256:" + "1" * 64,
    }
    candidate_ref = {
        "artifact_id": "candidate-1",
        "content_hash": "sha256:" + "2" * 64,
    }
    base = {
        "experiment_id": EXP4,
        "condition_id": "condition-no-verification",
        "repeat_id": 0,
        "ablation_mode": "NO_VERIFICATION",
        "paper_eligible": True,
        "paper_ineligibility_reasons": [],
    }
    attempt = {"attempt_id": "attempt-1", "task_id": "case-1"}
    task = {
        "task_id": "case-1",
        "case_id": "case-1",
        "root_status": "completed",
        "ablation_runtime": {
            "schema_version": "tokenshare.paper_ablation_runtime.v1",
            "condition_id": "condition-no-verification",
            "case_id": "case-1",
            "repeat_id": 0,
            "mode": "NO_VERIFICATION",
            "attempt_observations": [
                {
                    "attempt_id": "attempt-1",
                    "raw_output_ref": raw_ref,
                    "candidate_output_ref": candidate_ref,
                    "canonical_output_refs": {},
                    "independent_candidate_validity": False,
                    "final_validity": False,
                }
            ],
            "hook_observations": [
                {
                    "event_type": "EXPERIMENT_ABLATION_GATE_APPLIED",
                    "ablation_mode": "NO_VERIFICATION",
                    "disabled_mechanism": "verification",
                    "protocol_event_refs": [{"event_id": "submission-1"}],
                }
            ],
        },
    }

    incomplete = formal_metrics._exp4_task_row(
        base_row=base,
        task=task,
        attempts=[attempt],
    )
    assert incomplete["paper_eligible"] is False
    assert "incomplete_ablation_hook_observation" in incomplete[
        "paper_ineligibility_reasons"
    ]

    hook = task["ablation_runtime"]["hook_observations"][0]
    hook["hook_input"] = {
        "task_id": "case-1",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
    }
    hook["hook_result"] = {"bypass": True, "stop": False}
    unresolved = formal_metrics._exp4_task_row(
        base_row=base,
        task=task,
        attempts=[attempt],
    )
    assert unresolved["paper_eligible"] is False
    assert "unresolved_ablation_evidence_ref" in unresolved[
        "paper_ineligibility_reasons"
    ]

    complete = formal_metrics._exp4_task_row(
        base_row=base,
        task=task,
        attempts=[attempt],
        events=[{"event_id": "submission-1"}],
        artifacts=[raw_ref, candidate_ref],
    )
    assert complete["paper_eligible"] is True


def test_exp4_non_full_requires_eligible_exact_full_pair() -> None:
    ablation = {
        "experiment_id": EXP4,
        "condition_id": "no-verification",
        "case_id": "case-1",
        "repeat_id": 0,
        "row_scope": "task",
        "domain": "factorization",
        "difficulty": "hard",
        "paper_difficulty": "hard",
        "topic_family": None,
        "worker_count": 10,
        "ablation_mode": "NO_VERIFICATION",
        "paper_eligible": True,
        "paper_ineligibility_reasons": [],
    }

    missing = formal_metrics._with_exp4_full_pairing([ablation])[0]
    assert missing["paper_eligible"] is False
    assert "missing_paired_full_evidence" in missing[
        "paper_ineligibility_reasons"
    ]

    full = {
        **ablation,
        "condition_id": "full",
        "ablation_mode": "FULL",
        "paper_eligible": False,
    }
    paired = formal_metrics._with_exp4_full_pairing([full, ablation])
    non_full = next(row for row in paired if row["ablation_mode"] != "FULL")
    assert non_full["paper_eligible"] is False
    assert "paired_full_not_paper_eligible" in non_full[
        "paper_ineligibility_reasons"
    ]


def test_exp4_zero_exposure_denominator_is_null_not_zero() -> None:
    applicability, rate = formal_metrics._exp4_error_escape(
        exposed_error_count=0,
        escaped_error_count=0,
        stuck_task_count=0,
        wrong_canonical_count=0,
        premature_merge_attempt_count=0,
    )

    assert applicability == "zero_denominator"
    assert rate is None


def test_exp5_identity_inventory_uses_all_expected_attempts() -> None:
    attempt_one = {
        **_attempt(total_tokens=10, latency_ms=20),
        "attempt_id": "attempt-1",
        "run_id": "run-case",
        "provider_attempt_index": 0,
        "provider_attempt_count": 1,
        "paper_eligible": True,
    }
    attempt_two = {
        **attempt_one,
        "attempt_id": "attempt-2",
        "unit_id": "unit-2",
        "provider_attempt_index": 1,
    }
    item = _exp5_model_item(
        condition_id="exp5-condition",
        task_id="case-1",
        attempt=attempt_one,
    )
    bundle = {
        "tasks": [
            {
                "condition_id": "exp5-condition",
                "repeat_id": 0,
                "task_id": "case-1",
                "paper_eligible": True,
                "model_execution_records": [item],
            }
        ],
        "attempts": [item["attempt"], {**item["attempt"], **attempt_two}],
    }

    audit = formal_metrics._exp5_identity_inventory(bundle)

    assert audit["identity_denominator"] == 2
    assert audit["model_execution_v2_record_count"] == 1
    assert audit["missing_record_count"] == 1
    assert audit["paper_eligible"] is False


def test_exp5_identity_inventory_rejects_duplicate_or_orphan_records() -> None:
    attempt = {
        **_attempt(total_tokens=10, latency_ms=20),
        "attempt_id": "attempt-1",
        "run_id": "run-case",
        "provider_attempt_index": 0,
        "provider_attempt_count": 1,
        "paper_eligible": True,
    }
    item = _exp5_model_item(
        condition_id="exp5-condition",
        task_id="case-1",
        attempt=attempt,
    )
    base_task = {
        "condition_id": "exp5-condition",
        "repeat_id": 0,
        "task_id": "case-1",
        "paper_eligible": True,
    }
    duplicate = formal_metrics._exp5_identity_inventory(
        {
            "tasks": [{**base_task, "model_execution_records": [item, item]}],
            "attempts": [item["attempt"]],
        }
    )
    assert duplicate["duplicate_record_count"] == 1
    assert duplicate["paper_eligible"] is False

    orphan_item = _exp5_model_item(
        condition_id="exp5-condition",
        task_id="case-1",
        attempt={**attempt, "attempt_id": "orphan-attempt"},
    )
    orphan = formal_metrics._exp5_identity_inventory(
        {
            "tasks": [{**base_task, "model_execution_records": [item, orphan_item]}],
            "attempts": [item["attempt"]],
        }
    )
    assert orphan["orphan_record_count"] == 1
    assert orphan["paper_eligible"] is False


def test_exp5_provider_retries_are_expanded_for_identity_coverage() -> None:
    attempt = {
        **_attempt(total_tokens=10, latency_ms=20),
        "attempt_id": "attempt-1",
        "run_id": "run-case",
        "provider_attempt_index": 0,
        "provider_attempt_count": 2,
        "paper_eligible": True,
    }
    item = _exp5_model_item(
        condition_id="exp5-condition",
        task_id="case-1",
        attempt=attempt,
    )
    item["record"]["actual_provider_attempts"].append(
        dict(item["record"]["actual_provider_attempts"][0])
    )
    _refresh_model_record_digest(item["record"])
    audit = formal_metrics._exp5_identity_inventory(
        {
            "tasks": [
                {
                    "condition_id": "exp5-condition",
                    "repeat_id": 0,
                    "task_id": "case-1",
                    "paper_eligible": True,
                    "model_execution_records": [item],
                }
            ],
            "attempts": [item["attempt"]],
        }
    )

    assert audit["provider_attempt_identity_denominator"] == 2
    assert audit["provider_attempt_identity_covered_count"] == 2


def test_exp5_auditable_provider_failure_is_response_identity_na_not_mismatch() -> None:
    attempt = {
        **_attempt(total_tokens=0, latency_ms=20),
        "attempt_id": "attempt-provider-failure",
        "run_id": "run-case",
        "provider_attempt_index": 0,
        "provider_attempt_count": 2,
        "attempt_status": "provider_error",
        "error_kind": "timeout",
        "paper_eligible": True,
    }
    item = _exp5_model_item(
        condition_id="exp5-condition",
        task_id="case-1",
        attempt=attempt,
    )
    item["record"].update(
        {
            "raw_output_ref": None,
            "resolved_model": None,
            "response_model_status": "unavailable",
            "identity_status": "not_observed",
            "mismatch_reasons": [],
            "paper_eligible": False,
        }
    )
    item["record"]["actual_provider_attempts"] = [
        {
            **dict(item["record"]["actual_provider_attempts"][0]),
            "result_kind": failure_kind,
        }
        for failure_kind in ("timeout", "provider_error")
    ]
    item["record"]["actual_request_identities"] = [
        dict(item["record"]["actual_request_identities"][0]),
        dict(item["record"]["actual_request_identities"][0]),
    ]
    item["raw_output"] = None
    item["attempt"].update(
        {
            "raw_output_ref": None,
            "attempt_status": "provider_error",
            "provider_attempt_count": 2,
            "error_kind": "timeout",
        }
    )
    item["provider_errors"] = ["timeout", "provider_error"]
    _refresh_model_record_digest(item["record"])
    bundle = {
        "condition": {
            "experiment_id": EXP5,
            "condition_id": "exp5-condition",
            "provider_family": "siliconflow",
            "model_entry_id": "glm_5_2_exp1_baseline",
            "provider_model_id": "zai-org/GLM-5.2",
        },
        "tasks": [
            {
                "condition_id": "exp5-condition",
                "repeat_id": 0,
                "task_id": "case-1",
                "paper_eligible": True,
                "model_execution_records": [item],
            }
        ],
        "attempts": [item["attempt"]],
    }

    audit = formal_metrics._exp5_identity_inventory(bundle)
    row = formal_metrics._exp5_rows(
        {
            "exp5-condition": {
                "condition_id": "exp5-condition",
                "paper_eligible": True,
                "paper_ineligibility_reasons": [],
                "provider_error_count": 1,
            }
        },
        {"exp5-condition": bundle},
    )[0]

    assert audit["paper_eligible"] is True
    assert audit["provider_attempt_identity_denominator"] == 2
    assert audit["provider_attempt_identity_covered_count"] == 2
    assert row["identity_status"] == "not_observed"
    assert row["model_identity_match_count"] == 0
    assert row["model_identity_denominator"] == 0
    assert row["model_identity_not_observed_count"] == 1
    assert row["paper_eligible"] is True
    assert "model_identity_mismatch" not in row["paper_ineligibility_reasons"]


def test_exp5_v3_case_records_are_derived_from_root_and_attempt_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = {
        "condition": {
            "schema_version": "tokenshare.paper_condition.v3",
            "experiment_id": EXP5,
            "condition_id": "exp5-v3-condition",
            "repeat_id": 1,
            "domain": "lean_proof",
            "paper_difficulty": "hard",
            "topic_family": "induction",
            "cohort_member_id": "model-a",
            "order_slot": 2,
            "predecessor_member_id": "model-b",
            "sequence_plan_digest": EXP5_V3_SEQUENCE_PLAN_DIGEST,
            "exp5_selection_digest": "sha256:" + "1" * 64,
            "exp5_selection_parent_catalog_digest": "sha256:" + "2" * 64,
        },
        "tasks": [
            {
                "task_id": "case-1",
                "case_id": "case-1",
                "root_status": "completed",
                "accepted_validity": True,
                "paper_eligible": True,
            }
        ],
        "attempts": [
            {
                "task_id": "case-1",
                "attempt_status": "succeeded",
                "provider_attempt_count": 1,
                "total_tokens": 17,
                "latency_ms": 23,
                "paper_eligible": True,
                "started_at": "2026-07-30T00:00:00+00:00",
                "ended_at": "2026-07-30T00:00:01+00:00",
            }
        ],
        "events": [],
    }
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_identity_inventory",
        lambda value: {"paper_eligible": True},
    )

    rows = formal_metrics._exp5_v3_case_records(
        {"exp5-v3-condition": bundle}
    )

    assert rows == (
        {
            "case_id": "case-1",
            "repeat_id": 1,
            "cohort_member_id": "model-a",
            "condition_id": "exp5-v3-condition",
            "stratum_id": "lean_proof:hard:induction",
            "domain": "lean_proof",
            "topic_family": "induction",
            "root_status": "completed",
            "root_completed": True,
            "accepted_validity": True,
            "total_tokens": 17,
            "provider_latency_ms": 23.0,
            "paper_eligible": True,
            "order_slot": 2,
            "predecessor_member_id": "model-b",
            "sequence_plan_digest": EXP5_V3_SEQUENCE_PLAN_DIGEST,
            "exp5_selection_digest": "sha256:" + "1" * 64,
            "exp5_selection_parent_catalog_digest": "sha256:" + "2" * 64,
            "condition_started_at": "2026-07-30T00:00:00+00:00",
            "condition_ended_at": "2026-07-30T00:00:01+00:00",
            "observed_peak_concurrency": 1,
        },
    )


def test_exp5_renderer_rows_preserve_failures_and_usage_missingness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    condition = {
        "schema_version": "tokenshare.paper_condition.v3",
        "experiment_id": EXP5,
        "condition_id": "exp5-v3-failed",
        "repeat_id": 0,
        "domain": "factorization",
        "paper_difficulty": "hard",
        "topic_family": None,
        "cohort_member_id": "glm_5_2_siliconflow",
    }
    bundles = {
        "exp5-v3-failed": {
            "condition": condition,
            "tasks": [
                {
                    "task_id": "case-1",
                    "case_id": "case-1",
                    "root_status": "failed",
                    "accepted_validity": False,
                    "failure_stage": "provider",
                    "failure_kind": "provider_error",
                    "paper_eligible": True,
                    "evidence_artifact_refs": [
                        {
                            "artifact_id": "failure-1",
                            "content_hash": "sha256:" + "1" * 64,
                        }
                    ],
                }
            ],
            "attempts": [],
            "events": [],
            "artifacts": [],
        }
    }
    case_rows = (
        {
            "case_id": "case-1",
            "repeat_id": 0,
            "cohort_member_id": "glm_5_2_siliconflow",
            "condition_id": "exp5-v3-failed",
            "stratum_id": "factorization:hard",
            "root_completed": False,
            "accepted_validity": False,
            "total_tokens": None,
            "provider_latency_ms": None,
            "paper_eligible": True,
        },
    )
    execution_rows = (
        {
            "schema_version": "tokenshare.paper_exp5_model_execution_row.v1",
            "cohort_member_id": "glm_5_2_siliconflow",
            "configured_model": "zai-org/GLM-5.2",
            "identity_status": "matched",
            "provider_attempt_count": 1,
            "prompt_tokens": 10,
            "reasoning_tokens": None,
            "visible_output_tokens": None,
            "total_tokens": None,
            "latency_ms": None,
            "cost_estimate": None,
            "cost_estimate_status": "usage_missing",
            "paper_eligible": True,
        },
    )
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_case_records",
        lambda _bundles: case_rows,
    )
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_execution_rows",
        lambda _bundles: execution_rows,
        raising=False,
    )

    rows = formal_metrics._exp5_renderer_rows(
        suite={"status": "completed_with_failures"},
        bundles=bundles,
    )

    overall = rows["overall_rows"][0]
    assert rows["suite_status"] == "completed_with_failures"
    assert overall["root_count"] == 1
    assert overall["completion_count"] == 0
    assert overall["completion_rate"] == 0.0
    assert overall["prompt_tokens"] == 10
    assert overall["prompt_tokens_sample_size"] == 1
    assert overall["prompt_tokens_missing_count"] == 0
    assert overall["reasoning_tokens"] is None
    assert overall["reasoning_tokens_missing_count"] == 1
    assert overall["total_tokens"] is None
    assert overall["total_tokens_sample_size"] == 0
    assert overall["total_tokens_missing_count"] == 1
    assert overall["provider_latency_ms"] is None
    assert overall["provider_latency_ms_sample_size"] == 0
    assert overall["provider_latency_ms_missing_count"] == 1
    assert overall["visible_output_tokens"] is None
    assert overall["cost_estimate"] is None
    assert rows["failure_taxonomy_rows"] == (
        {
            "cohort_member_id": "glm_5_2_siliconflow",
            "domain": "factorization",
            "topic_family": None,
            "failure_stage": "provider",
            "failure_kind": "provider_error",
            "count": 1,
            "evidence_ref": {
                "artifact_id": "failure-1",
                "content_hash": "sha256:" + "1" * 64,
            },
            "short_summary": (
                "Observed provider_error at provider; see persisted evidence reference."
            ),
        },
    )


def test_exp5_failure_taxonomy_keeps_executor_error_separate_from_provider_error() -> None:
    bundle = {
        "condition": {
            "cohort_member_id": "glm_5_2_siliconflow",
            "domain": "factorization",
            "topic_family": None,
        },
        "tasks": [
            {
                "root_status": "failed",
                "failure_stage": "provider",
                "failure_kind": "provider_error",
                "evidence_artifact_refs": [],
            },
            {
                "root_status": "failed",
                "failure_stage": "executor",
                "failure_kind": "executor_error",
                "evidence_artifact_refs": [],
            },
        ],
    }

    rows = formal_metrics._exp5_failure_taxonomy_rows({"condition": bundle})

    assert {(row["failure_stage"], row["failure_kind"]) for row in rows} == {
        ("provider", "provider_error"),
        ("executor", "executor_error"),
    }


def test_condition_metrics_preserve_provider_usage_missingness() -> None:
    bundle = _evidence_complete_failed_condition_bundle(EXP5)
    attempt = bundle["attempts"][0]
    attempt.update(
        {
            "attempt_status": "provider_error",
            "error_kind": "timeout",
            "total_tokens": None,
            "cost_estimate": None,
            "paper_eligible": True,
        }
    )

    row = formal_metrics._condition_metrics(bundle)

    assert row["total_tokens"] is None
    assert row["total_cost_estimate"] is None
    assert row["token_p50"] is None
    assert row["token_p95"] is None
    assert row["provider_latency_sum_ms"] == 100
    assert row["token_usage_missing_count"] == 1
    assert row["cost_estimate_missing_count"] == 1


def test_repeat_aggregate_keeps_fixed_denominator_when_usage_is_missing() -> None:
    base = {
        "row_scope": "repeat_condition",
        "domain": "factorization",
        "difficulty": "hard",
        "paper_difficulty": "hard",
        "worker_count": 1,
        "fault_type": "none",
        "fault_rate": 0.1,
        "ablation_mode": "FULL",
        "task_count": 1,
        "completed_root_count": 0,
        "wall_clock_ms": 100.0,
        "paper_eligible": True,
        "paper_ineligibility_reasons": [],
    }
    rows = [
        {
            **base,
            "condition_id": "condition-0",
            "repeat_id": 0,
            "total_tokens": None,
            "total_cost_estimate": None,
            "token_usage_sample_size": 0,
            "token_usage_missing_count": 1,
            "cost_estimate_sample_size": 0,
            "cost_estimate_missing_count": 1,
        },
        {
            **base,
            "condition_id": "condition-1",
            "repeat_id": 1,
            "total_tokens": 10,
            "total_cost_estimate": 0.1,
            "token_usage_sample_size": 1,
            "token_usage_missing_count": 0,
            "cost_estimate_sample_size": 1,
            "cost_estimate_missing_count": 0,
        },
    ]

    output = formal_metrics._with_repeat_aggregates(EXP3, rows)
    aggregate = next(row for row in output if row["row_scope"] == "repeat_aggregate")

    assert aggregate["repeat_count"] == 2
    assert aggregate["total_tokens"] is None
    assert aggregate["total_cost_estimate"] is None
    assert aggregate["token_usage_sample_size"] == 1
    assert aggregate["token_usage_missing_count"] == 1
    assert aggregate["cost_estimate_sample_size"] == 1
    assert aggregate["cost_estimate_missing_count"] == 1


def test_exp5_renderer_rows_keep_audit_statistics_for_ineligible_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    condition_id = "exp5-v3-ineligible"
    bundle = {
        "condition": {
            "schema_version": "tokenshare.paper_condition.v3",
            "experiment_id": EXP5,
            "condition_id": condition_id,
            "repeat_id": 0,
            "domain": "factorization",
            "paper_difficulty": "hard",
            "topic_family": None,
            "cohort_member_id": "glm_5_2_siliconflow",
        },
        "tasks": (),
        "attempts": (),
        "events": (),
        "artifacts": (),
    }
    case_rows = (
        {
            "case_id": "case-1",
            "repeat_id": 0,
            "cohort_member_id": "glm_5_2_siliconflow",
            "condition_id": condition_id,
            "stratum_id": "factorization:hard",
            "root_completed": False,
            "accepted_validity": False,
            "total_tokens": None,
            "total_tokens_unavailable_reason": "provider_usage_missing",
            "provider_latency_ms": None,
            "provider_latency_ms_unavailable_reason": "provider_latency_missing",
            "paper_eligible": False,
        },
    )
    observed: dict[str, tuple[dict, ...]] = {}

    def paired(records):
        observed["paired"] = tuple(dict(row) for row in records)
        return ({"metric": "root_completion", "pairing_denominator": 1},)

    def ordered(records):
        observed["order"] = tuple(dict(row) for row in records)
        return ({"row_scope": "order_sensitivity", "case_repeat_denominator": 1},)

    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_case_records",
        lambda _bundles: case_rows,
    )
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_execution_rows",
        lambda _bundles: (),
    )
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_paired_comparison_rows",
        paired,
    )
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_order_and_concurrency_rows",
        ordered,
    )

    rows = formal_metrics._exp5_renderer_rows(
        suite={"status": "completed_with_failures"},
        bundles={condition_id: bundle},
    )

    assert observed["paired"][0]["paper_eligible"] is True
    assert observed["paired"][0]["root_completed"] is False
    assert observed["paired"][0]["total_tokens"] is None
    assert observed["order"] == observed["paired"]
    assert rows["identity_complete"] is False
    assert rows["paired_comparison_rows"][0]["pairing_denominator"] == 1
    assert rows["paired_comparison_rows"][0]["paper_eligible"] is False
    assert rows["order_concurrency_rows"][0]["case_repeat_denominator"] == 1
    assert rows["order_concurrency_rows"][0]["paper_eligible"] is False


def test_exp5_attempt_peak_uses_half_open_intervals_at_timestamp_ties() -> None:
    attempts = (
        {
            "started_at": "2026-07-30T00:00:00+00:00",
            "ended_at": "2026-07-30T00:00:01+00:00",
        },
        {
            "started_at": "2026-07-30T00:00:01+00:00",
            "ended_at": "2026-07-30T00:00:02+00:00",
        },
    )

    assert formal_metrics._attempt_interval_peak(attempts) == 1


def test_exp5_v3_formal_outputs_freeze_csv_and_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundles = {
        "exp5-v3-condition": {
            "condition": {
                "schema_version": "tokenshare.paper_condition.v3",
                "experiment_id": EXP5,
            },
            "tasks": [],
        }
    }
    case_records = ({"case_id": "sentinel"},)
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_case_records",
        lambda value: case_records,
        raising=False,
    )
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_paired_comparison_rows",
        lambda value: (
            {"metric": "root_completion", "paired_sample_size": 12},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        formal_metrics,
        "build_exp5_order_and_concurrency_rows",
        lambda value: (
            {"row_scope": "arm_audit", "global_peak_in_flight": 3},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        formal_metrics,
        "_exp5_v3_execution_record_text",
        lambda value: '{"case_id":"case-1"}\n',
        raising=False,
    )
    monkeypatch.setattr(formal_metrics, "_model_record_text", lambda value: "")

    first = formal_metrics._exp5_v3_analysis_outputs(bundles)
    second = formal_metrics._exp5_v3_analysis_outputs(bundles)
    renderer_rows = {
        "suite_status": "completed",
        "identity_complete": False,
        "overall_rows": (),
        "domain_topic_rows": (),
        "paired_comparison_rows": (),
        "model_execution_rows": (),
        "order_concurrency_rows": (),
        "failure_taxonomy_rows": (),
    }

    assert first == second
    assert set(first) == {
        "metrics/exp5_paired_comparisons.csv",
        "metrics/exp5_model_execution_records.jsonl",
        "metrics/exp5_order_and_concurrency.csv",
    }
    assert first["metrics/exp5_model_execution_records.jsonl"] == (
        '{"case_id":"case-1"}\n'
    )

    refs = formal_metrics._write_metrics_outputs(
        root=tmp_path,
        metrics_body={},
        condition_rows=(),
        experiment_rows={
            EXP1: (),
            EXP2: (),
            EXP3: (),
            EXP4: (),
            EXP5: (),
        },
        run_bundles=bundles,
        exp5_artifact_rows=renderer_rows,
    )
    ref_paths = {ref["path"] for ref in refs}
    assert set(first).isdisjoint(ref_paths)
    assert all(not (tmp_path / path).exists() for path in first)
    assert "metrics/exp5_renderer_rows.json" in ref_paths
    assert json.loads(
        (tmp_path / "metrics" / "exp5_renderer_rows.json").read_text(
            encoding="utf-8"
        )
    ) == {key: list(value) if isinstance(value, tuple) else value for key, value in renderer_rows.items()}
    formal_metrics._write_metrics_outputs(
        root=tmp_path,
        metrics_body={},
        condition_rows=(),
        experiment_rows={
            EXP1: (),
            EXP2: (),
            EXP3: (),
            EXP4: (),
            EXP5: (),
        },
        run_bundles=bundles,
        exp5_artifact_rows=renderer_rows,
    )
    assert all(not (tmp_path / path).exists() for path in first)


def test_exp2_rate_limit_views_keep_all_runs_and_list_exclusions() -> None:
    rows = [
        {
            "condition_id": "condition-clean",
            "task_id": "shared-case",
            "run_id": "run-clean",
            "http_429_count": 0,
            "paper_eligible": True,
        },
        {
            "condition_id": "condition-limited",
            "task_id": "shared-case",
            "run_id": "run-limited",
            "http_429_count": 1,
            "paper_eligible": True,
        },
    ]

    views = formal_metrics._exp2_rate_limit_views(rows)

    assert views["all_runs"]["sample_count"] == 2
    assert views["rate_limit_excluded"]["sample_count"] == 1
    assert views["rate_limit_excluded"]["excluded_run_ids"] == ["run-limited"]
    assert views["rate_limit_excluded"]["excluded_condition_ids"] == [
        "condition-limited"
    ]
    assert views["rate_limit_excluded"]["exclusion_reason"] == "provider_http_429"


def test_worker_death_metrics_require_real_post_death_recovery_evidence() -> None:
    fault = {
        "schema_version": "tokenshare.paper_worker_death.v1",
        "record_ref": {"artifact_id": "worker-death-record"},
        "target_ai_unit": {"unit_id": "unit-1"},
        "dependency_graph": {
            "schema_version": "tokenshare.paper_ai_unit_dependency_graph.v1",
            "expected_ai_unit_count": 2,
            "unit_ids": ["unit-1", "unit-2"],
        },
        "worker_process_exitcode": 1,
        "replacement_process_exitcode": 0,
        "killed_at": _ms_timestamp(100),
        "replacement_attempt": {"attempt_id": "replacement-1", "unit_id": "unit-1"},
        "kill_progress_target_ratio": 0.25,
        "kill_progress_actual_ratio": 0.5,
        "kill_progress_completed_ai_unit_count": 1,
        "kill_progress_total_ai_unit_count": 2,
        "kill_progress_observed_at": _ms_timestamp(100),
        "kill_progress_error": None,
        "protocol_event_refs": ["replacement-lease"],
    }
    events = [
        {
            "event_id": "replacement-lease",
            "event_type": "LEASE_STATE_CHANGED",
            "occurred_at": _ms_timestamp(150),
            "payload": {"new_state": "Active"},
        },
        {
            "event_id": "canonical-1",
            "event_type": "CANONICAL_OUTPUTS_BOUND",
            "occurred_at": _ms_timestamp(200),
            "payload": {"canonical_selection": {"unit_id": "unit-1", "selected_attempt_id": "replacement-1"}},
        },
        {
            "event_id": "canonical-2",
            "event_type": "CANONICAL_OUTPUTS_BOUND",
            "occurred_at": _ms_timestamp(200),
            "payload": {"canonical_selection": {"unit_id": "unit-2", "selected_attempt_id": "attempt-2"}},
        },
        {
            "event_id": "merge-link",
            "event_type": "MERGE_TASK_LINK_RECORDED",
            "occurred_at": _ms_timestamp(210),
            "payload": {"required_slot_count": 2},
        },
        {
            "event_id": "merge-record",
            "event_type": "MERGE_RECORDED",
            "occurred_at": _ms_timestamp(220),
        },
        {
            "event_id": "root-complete",
            "event_type": "TASK_UNIT_STATE_CHANGED",
            "object_id": "root",
            "occurred_at": _ms_timestamp(220),
            "payload": {"new_state": "Completed"},
        },
    ]
    result, reasons = formal_metrics._worker_death_recovery_metrics(
        condition={"dead_worker_count": 1, "kill_progress_percent": 25},
        tasks=[{"accepted_validity": True, "runtime_generation_identity": {"root_unit_id": "root"}}],
        attempts=[
            {"attempt_id": "replacement-1", "unit_id": "unit-1", "canonical": True},
            {"attempt_id": "attempt-2", "unit_id": "unit-2", "canonical": True},
        ],
        worker_faults=[fault],
        events=events,
    )

    assert reasons == []
    assert result["dead_worker_count"] == 1
    assert result["kill_progress"] == pytest.approx(0.5)
    assert result["coordinator_continued"] is True
    assert result["required_slot_count"] == 2
    assert result["recovered_slot_count"] == 2
    assert result["result_completeness_rate"] == 1.0
    assert result["root_output_complete"] is True
    assert result["accepted_validity"] is True
    assert result["worker_death_evidence_refs"]

    task_rows = formal_metrics._worker_death_task_rows(
        base_row={
            "experiment_id": EXP3,
            "condition_id": "worker-death-condition",
            "repeat_id": 0,
            "paper_eligible": True,
            "paper_ineligibility_reasons": [],
        },
        condition={
            "fault_type": "worker_death",
            "dead_worker_count": 1,
            "kill_progress": 0.25,
        },
        tasks=[
            {
                "task_id": "case-1",
                "case_id": "case-1",
                "root_status": "completed",
                "accepted_validity": True,
                "runtime_generation_identity": {"root_unit_id": "root"},
            }
        ],
        attempts=[
            {
                "task_id": "case-1",
                "attempt_id": "replacement-1",
                "unit_id": "unit-1",
                "canonical": True,
            },
            {
                "task_id": "case-1",
                "attempt_id": "attempt-2",
                "unit_id": "unit-2",
                "canonical": True,
            },
        ],
        worker_faults=[{**fault, "task_id": "case-1"}],
        events=[{**event, "task_id": "case-1"} for event in events],
    )

    assert len(task_rows) == 1
    assert task_rows[0]["row_scope"] == "task"
    assert task_rows[0]["case_id"] == "case-1"
    for field_name in (
        "dead_worker_count",
        "kill_progress",
        "coordinator_continued",
        "required_slot_count",
        "recovered_slot_count",
        "result_completeness_rate",
        "root_output_complete",
        "accepted_validity",
        "worker_death_evidence_refs",
    ):
        assert field_name in task_rows[0]


def test_worker_death_incomplete_record_is_counted_and_explicitly_ineligible() -> None:
    fault = {
        "schema_version": "tokenshare.paper_worker_death_incomplete.v1",
        "fault_type": "worker_death",
        "record_ref": {"artifact_id": "worker-death-incomplete"},
        "target_ai_unit": {"unit_id": "unit-1"},
        "dependency_graph": {
            "schema_version": "tokenshare.paper_ai_unit_dependency_graph.v1",
            "expected_ai_unit_count": 1,
            "unit_ids": ["unit-1"],
        },
        "worker_process_exitcode": 1,
        "killed_at": _ms_timestamp(100),
        "dead_attempt": {"attempt_id": "dead-1", "unit_id": "unit-1"},
        "replacement_fact": None,
        "recovery_completed": False,
        "evidence_complete": False,
        "kill_progress_target_ratio": 0.25,
        "kill_progress_actual_ratio": 1.0,
        "kill_progress_completed_ai_unit_count": 1,
        "kill_progress_total_ai_unit_count": 1,
        "kill_progress_observed_at": _ms_timestamp(100),
        "kill_progress_error": None,
        "protocol_event_refs": ["terminal-recovery"],
    }
    events = [
        {
            "event_id": "terminal-recovery",
            "event_type": "RECOVERY_ACTION_RECORDED",
            "occurred_at": _ms_timestamp(150),
            "payload": {
                "recovery_action": {
                    "attempt_id": "replacement-1",
                    "unit_id": "unit-1",
                    "retry_allowed": False,
                    "reason": "retry_limit_reached",
                }
            },
        }
    ]

    progress, progress_reasons = formal_metrics._worker_kill_progress_metrics(
        [fault]
    )
    metrics, reasons = formal_metrics._worker_death_recovery_metrics(
        condition={
            "fault_type": "worker_death",
            "dead_worker_count": 1,
            "kill_progress_percent": 25,
        },
        tasks=[
            {
                "task_id": "case-1",
                "root_status": "failed",
                "accepted_validity": False,
            }
        ],
        attempts=[
            {
                "task_id": "case-1",
                "attempt_id": "dead-1",
                "unit_id": "unit-1",
                "attempt_status": "worker_died",
            },
            {
                "task_id": "case-1",
                "attempt_id": "replacement-1",
                "unit_id": "unit-1",
                "attempt_status": "provider_error",
            },
        ],
        worker_faults=[fault],
        events=events,
    )

    assert progress_reasons == []
    assert progress["kill_progress_actual_ratio_mean"] == pytest.approx(1.0)
    assert metrics["actual_dead_worker_count"] == 1
    assert metrics["recovered_slot_count"] == 0
    assert metrics["root_output_complete"] is False
    assert "incomplete_worker_death_recovery" in reasons
    assert "invalid_worker_replacement_identity" in reasons


def test_executor_error_attempt_is_not_counted_as_provider_call_or_failure() -> None:
    row = formal_metrics._condition_metrics(
        {
            "condition": {
                "experiment_id": "exp3_real_ai_fault_recovery",
                "condition_id": "exp3-condition-executor-error",
                "repeat_id": 0,
                "domain": "factorization",
                "difficulty": "easy",
                "worker_count": 1,
                "fault_type": "none",
                "fault_rate": 0.0,
                "ablation_mode": "FULL",
            },
            "tasks": [
                {
                    "task_id": "case-1",
                    "root_status": "failed",
                    "accepted_validity": False,
                    "paper_eligible": False,
                }
            ],
            "attempts": [
                {
                    "schema_version": "tokenshare.paper_attempt_result.v2",
                    "task_id": "case-1",
                    "unit_id": "unit-root-1",
                    "attempt_id": "attempt-root-1",
                    "attempt_status": "executor_error",
                    "provider": None,
                    "model": None,
                    "entry_id": None,
                    "executor_id": "executor_factorization_runtime",
                    "executor_type": "deterministic_local",
                    "provider_attempt_count": 0,
                    "provider_attempt_index": 0,
                    "total_tokens": 0,
                    "cost_estimate": 0.0,
                    "latency_ms": 0,
                    "error_kind": "retry_limit_reached",
                    "started_at": "2026-07-28T00:00:00Z",
                    "ended_at": "2026-07-28T00:00:01Z",
                    "paper_eligible": False,
                }
            ],
            "events": [],
            "artifacts": [{"artifact_id": "request-root-1"}],
        }
    )

    assert row["provider_attempt_count"] == 0
    assert row["provider_error_count"] == 0
    assert row["total_tokens"] == 0
    assert row["provider_latency_sum_ms"] == 0
    assert row["token_p50"] is None
    assert row["token_p95"] is None
    assert row["failure_breakdown"]["executor_error"] == 1
    assert row["paper_eligible"] is False


def test_experiment_scope_runner_exception_is_excluded_from_provider_inventory() -> None:
    protocol_attempt = {
        **_attempt(total_tokens=100, latency_ms=200),
        "task_id": "case-1",
        "record_scope": "protocol",
        "paper_eligible": True,
    }
    runner_exception = {
        **_attempt(total_tokens=999, latency_ms=888),
        "task_id": "case-1",
        "attempt_id": "runner-exception",
        "attempt_status": "provider_error",
        "error_kind": "runner_exception",
        "record_scope": "experiment",
        "paper_eligible": False,
    }

    row = formal_metrics._condition_metrics(
        {
            "condition": {
                "experiment_id": "exp1_real_ai_cross_domain",
                "condition_id": "exp1-condition-runner-exception",
                "repeat_id": 0,
                "domain": "factorization",
                "difficulty": "easy",
                "worker_count": 1,
                "fault_type": "none",
                "fault_rate": 0.0,
                "ablation_mode": "FULL",
            },
            "tasks": [
                {
                    "task_id": "case-1",
                    "root_status": "failed",
                    "accepted_validity": False,
                    "paper_eligible": False,
                }
            ],
            "attempts": [protocol_attempt, runner_exception],
            "events": [],
            "artifacts": [{"artifact_id": "request-root-1"}],
        }
    )

    assert row["provider_attempt_count"] == 1
    assert row["provider_error_count"] == 0
    assert row["total_tokens"] == 100
    assert row["total_cost_estimate"] == 0.1
    assert row["provider_latency_sum_ms"] == 200
    assert row["token_p50"] == 100
    assert row["token_p95"] == 100


def _write_suite_manifest(root: Path, experiment_ids: tuple[str, ...]) -> None:
    _write_json(
        root / "suite_manifest.json",
        {
            "formal": True,
            "pilot_only": False,
            "execution_scope": "formal_matrix",
            "regression_only": True,
            "paper_eligible": False,
            "experiment_ids": list(experiment_ids),
        },
    )


def _write_run(
    root: Path,
    *,
    experiment_id: str,
    condition_id: str,
    condition: dict[str, object],
    task: dict[str, object],
    attempts: list[dict[str, object]],
    events: list[dict[str, object]],
    faults: list[dict[str, object]] | None = None,
    artifacts: list[dict[str, object]] | None = None,
    repeat_id: int = 0,
) -> Path:
    conditions_path = root / "conditions.jsonl"
    conditions_path.parent.mkdir(parents=True, exist_ok=True)
    with conditions_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "experiment_id": experiment_id,
                    "condition_id": condition_id,
                    "repeat_id": repeat_id,
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    **condition,
                }
            )
            + "\n"
        )
    run_root = (
        root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    generation_id = "generation-1"
    generation = run_root / ".generations" / generation_id
    task_body = json.loads(json.dumps(task))
    attempt_bodies = json.loads(json.dumps(attempts))
    artifact_bodies = json.loads(json.dumps(artifacts or []))
    for model_item in task_body.get("model_execution_records", []):
        record = model_item.get("record")
        record_ref = model_item.get("record_ref")
        if not isinstance(record, dict) or not isinstance(record_ref, dict):
            continue
        artifact_id = record_ref.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        artifact_path = (
            generation
            / "artifacts"
            / "model_execution_records"
            / f"{artifact_id}.json"
        )
        _write_json(artifact_path, record)
        content_hash = "sha256:" + hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
        persisted_ref = {
            "artifact_id": artifact_id,
            "artifact_type": "PaperModelExecutionRecord",
            "content_hash": content_hash,
        }
        model_item["record_ref"] = persisted_ref
        for attempt in attempt_bodies:
            if attempt.get("attempt_id") == record.get("attempt_id"):
                attempt["model_execution_record_ref"] = persisted_ref
        artifact_bodies.append(
            {
                **persisted_ref,
                "path": artifact_path.relative_to(root).as_posix(),
            }
        )
    _write_json(run_root / "CURRENT.json", {"generation_id": generation_id})
    _write_jsonl(
        generation / "per_task_results.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                **task_body,
            }
        ],
    )
    _write_jsonl(
        generation / "per_attempt_results.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_body["task_id"],
                **attempt,
            }
            for attempt in attempt_bodies
        ],
    )
    _write_jsonl(
        generation / "fault_injections.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_body["task_id"],
                **fault,
            }
            for fault in (faults or [])
        ],
    )
    _write_jsonl(
        generation / "events" / "event_log.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_body["task_id"],
                **event,
            }
            for event in events
        ],
    )
    _write_jsonl(
        generation / "artifacts" / "artifact_index.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_body["task_id"],
                **artifact,
            }
            for artifact in artifact_bodies
        ],
    )
    return generation


def _attempt(*, total_tokens: int, latency_ms: int) -> dict[str, object]:
    return {
        "attempt_id": f"attempt-{total_tokens}-{latency_ms}",
        "unit_id": "unit-1",
        "attempt_status": "succeeded",
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "total_tokens": total_tokens,
        "cost_estimate": 0.1,
        "latency_ms": latency_ms,
    }


def _evidence_complete_failed_condition_bundle(
    experiment_id: str,
) -> dict[str, object]:
    condition_id = f"{experiment_id}-failed-condition"
    task_id = f"{experiment_id}-failed-case"
    return {
        "condition": {
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": 0,
            "domain": "lean_proof",
            "difficulty": "hard",
            "paper_difficulty": "hard_frontier",
            "topic_family": "pure_logic",
            "worker_count": 10,
            "fault_type": "none",
            "fault_rate": 0.0,
            "ablation_mode": "FULL",
        },
        "tasks": [
            {
                "task_id": task_id,
                "root_status": "failed",
                "accepted_validity": False,
                "paper_eligible": True,
                "runtime_generation_identity": {"root_unit_id": "root"},
            }
        ],
        "attempts": [
            {
                **_attempt(total_tokens=10, latency_ms=100),
                "task_id": task_id,
                "attempt_id": "failed-attempt",
                "unit_id": "unit-a",
                "worker_id": "worker-1",
                "attempt_status": "checker_rejected",
                "started_at": _ms_timestamp(0),
                "ended_at": _ms_timestamp(100),
                "paper_eligible": True,
            }
        ],
        "events": [
            {
                "task_id": task_id,
                "event_id": "registered-root",
                "event_type": "TASK_REGISTERED",
                "occurred_at": _ms_timestamp(0),
                "payload": {},
            },
            {
                "task_id": task_id,
                "event_id": "created-unit-a",
                "event_type": "TASK_UNIT_CREATED",
                "object_id": "unit-a",
                "occurred_at": _ms_timestamp(0),
                "payload": {"task_unit": {"unit_id": "unit-a"}},
            },
            {
                "task_id": task_id,
                "event_id": "failed-root",
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "object_id": "root",
                "occurred_at": _ms_timestamp(110),
                "payload": {
                    "task_unit_state_change": {
                        "unit_id": "root",
                        "new_state": "Failed",
                    }
                },
            },
        ],
        "artifacts": [{"artifact_id": "failed-output-evidence"}],
    }


def _exp5_model_item(
    *,
    condition_id: str,
    task_id: str,
    attempt: dict[str, object],
    repeat_id: int = 0,
) -> dict[str, object]:
    attempt_id = str(attempt["attempt_id"])
    request_ref = dict(
        attempt.get("request_ref")
        or {"artifact_id": f"request-{attempt_id}"}
    )
    raw_ref = dict(
        attempt.get("raw_output_ref")
        or {"artifact_id": f"raw-{attempt_id}"}
    )
    provenance_ref = dict(
        attempt.get("provenance_ref")
        or {"artifact_id": f"provenance-{attempt_id}"}
    )
    usage_ref = dict(
        attempt.get("usage_ref")
        or {"artifact_id": f"usage-{attempt_id}"}
    )
    record_ref = dict(
        attempt.get("model_execution_record_ref")
        or {"artifact_id": f"model-record-{attempt_id}"}
    )
    expected_identity = {
        "model_cohort_id": "paper-model-endpoint-cohort-v1",
        "model_cohort_digest": "sha256:" + "4" * 64,
        "cohort_member_id": "glm_5_2_siliconflow",
        "provider_config_id": "exp1_baseline_siliconflow",
        "selected_entry_id": "glm_5_2_exp1_baseline",
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "default",
        "effective_reasoning_controls": {"enable_thinking": False},
    }
    record = PaperModelExecutionRecord(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=f"run-{task_id}",
        task_id=task_id,
        unit_id=str(attempt.get("unit_id", "unit-1")),
        attempt_id=attempt_id,
        expected_identity=expected_identity,
        source_provider_config_digest="sha256:" + "5" * 64,
        prepared_execution_config_digest="sha256:" + "6" * 64,
        request_ref=request_ref,
        provenance_ref=provenance_ref,
        raw_output_ref=raw_ref,
        usage_ref=usage_ref,
        actual_request_identities=[
            {
                "schema_version": "phase7.provider_request_identity.v2",
                "provider_family": "siliconflow",
                "entry_id": "glm_5_2_exp1_baseline",
                "configured_model": "zai-org/GLM-5.2",
                "requested_model": "zai-org/GLM-5.2",
                "reasoning_controls": {"enable_thinking": False},
                "effective_request_controls_digest": "sha256:" + "7" * 64,
            }
        ],
        actual_provider_attempts=[
            {
                "provider_family": "siliconflow",
                "entry_id": "glm_5_2_exp1_baseline",
                "configured_model": "zai-org/GLM-5.2",
                "result_kind": "succeeded",
            }
        ],
        requested_model="zai-org/GLM-5.2",
        resolved_model="zai-org/GLM-5.2",
        response_model_status="present",
        identity_status="matched",
        mismatch_reasons=[],
        paper_eligible=True,
        created_at="2026-07-25T00:00:00Z",
    )
    strict_attempt = {
        "condition_id": condition_id,
        "repeat_id": repeat_id,
        "run_id": f"run-{task_id}",
        "task_id": task_id,
        "unit_id": str(attempt.get("unit_id", "unit-1")),
        "attempt_id": attempt_id,
        "worker_id": str(attempt.get("worker_id", "worker-1")),
        "provider_attempt_index": int(
            attempt.get("provider_attempt_index", 0)
        ),
        "provider_attempt_count": int(
            attempt.get("provider_attempt_count", 1)
        ),
        "attempt_status": str(attempt.get("attempt_status", "succeeded")),
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "request_ref": request_ref,
        "raw_output_ref": raw_ref,
        "parsed_output_ref": attempt.get("parsed_output_ref"),
        "parse_failure_ref": attempt.get("parse_failure_ref"),
        "provenance_ref": provenance_ref,
        "usage_ref": usage_ref,
        "model_execution_record_ref": record_ref,
        "started_at": str(
            attempt.get("started_at", "2026-07-25T00:00:00Z")
        ),
        "ended_at": str(
            attempt.get("ended_at", "2026-07-25T00:00:01Z")
        ),
        "latency_ms": int(attempt["latency_ms"]),
        "prompt_tokens": int(attempt.get("prompt_tokens", 8)),
        "completion_tokens": int(attempt.get("completion_tokens", 12)),
        "total_tokens": int(attempt["total_tokens"]),
        "cost_estimate": float(attempt["cost_estimate"]),
        "error_kind": attempt.get("error_kind"),
        "fault_injection_ref": attempt.get("fault_injection_ref"),
        "paper_eligible": bool(attempt.get("paper_eligible", True)),
    }
    return {
        "record": record.to_dict(),
        "record_ref": record_ref,
        "raw_output": {
            "schema_version": "phase7.raw_model_output.v2",
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "configured_model": "zai-org/GLM-5.2",
            "requested_model": "zai-org/GLM-5.2",
            "resolved_model": "zai-org/GLM-5.2",
            "response_model_status": "present",
            "raw_response_json": {"model": "zai-org/GLM-5.2"},
        },
        "request": {"model": "zai-org/GLM-5.2"},
        "provenance": {
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
        },
        "usage": {
            "total_tokens": int(attempt["total_tokens"]),
            "cost_estimate": float(attempt["cost_estimate"]),
        },
        "attempt": strict_attempt,
        "task": {
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_id": task_id,
            "paper_eligible": True,
        },
        "transport_kind": "ai_api",
        "model_policy": "fixed_entry",
        "provider_errors": [],
        "pilot_only": False,
        "formal_strict_join": True,
    }


def _refresh_model_record_digest(record: dict[str, object]) -> None:
    canonical = {
        key: value for key, value in record.items() if key != "record_digest"
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    record["record_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def _timing_events(wall_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "AI_UNIT_STARTED", "offset_ms": 0},
        {"event_type": "AI_UNIT_ENDED", "offset_ms": wall_ms, "duration_ms": wall_ms},
    ]


def _scheduler_events(*, unit_ms: int, merge_ms: int, wall_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "AI_UNIT_STARTED", "offset_ms": 0},
        {"event_type": "AI_UNIT_ENDED", "offset_ms": unit_ms, "duration_ms": unit_ms},
        {"event_type": "MERGE_GATE_COMPLETED", "offset_ms": wall_ms, "duration_ms": merge_ms},
    ]


def _ms_timestamp(offset_ms: int) -> str:
    seconds, milliseconds = divmod(offset_ms, 1000)
    return f"2026-07-26T00:00:{seconds:02d}.{milliseconds:03d}000Z"


def _timed_attempt(
    attempt_id: str,
    unit_id: str,
    started_ms: int,
    ended_ms: int,
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "unit_id": unit_id,
        "worker_id": "worker-1",
        "started_at": _ms_timestamp(started_ms),
        "ended_at": _ms_timestamp(ended_ms),
    }


def _critical_path_events(
    *,
    required: tuple[str, ...],
    canonical_ms: dict[str, int],
    merge_gate_ms: int,
    merge_done_ms: int,
    root_done_ms: int,
) -> list[dict[str, object]]:
    canonical = [
        {
            "event_id": f"canonical-{unit_id}",
            "event_type": "CANONICAL_OUTPUTS_BOUND",
            "occurred_at": _ms_timestamp(canonical_ms[unit_id]),
            "payload": {
                "canonical_selection": {
                    "unit_id": unit_id,
                    "selected_attempt_id": f"attempt-{unit_id.removeprefix('unit-')}",
                }
            },
        }
        for unit_id in required
    ]
    return [
        {
            "event_id": "root-registered",
            "event_type": "TASK_REGISTERED",
            "object_id": "root",
            "occurred_at": _ms_timestamp(0),
            "payload": {"root_unit_id": "root"},
        },
        *[
            {
                "event_id": f"created-{unit_id}",
                "event_type": "TASK_UNIT_CREATED",
                "object_id": unit_id,
                "occurred_at": _ms_timestamp(0),
                "payload": {"task_unit": {"unit_id": unit_id}},
            }
            for unit_id in required
        ],
        *canonical,
        {
            "event_id": "merge-link",
            "event_type": "MERGE_TASK_LINK_RECORDED",
            "occurred_at": _ms_timestamp(merge_gate_ms),
            "payload": {
                "merge_task_link": {
                    "merge_unit_id": "merge-unit",
                    "required_slot_bindings": [
                        {"source_child_unit_id": unit_id}
                        for unit_id in required
                    ],
                },
                "required_slot_count": len(required),
            },
        },
        {
            "event_id": "merge-record",
            "event_type": "MERGE_RECORDED",
            "occurred_at": _ms_timestamp(merge_done_ms),
            "payload": {"merge_record": {"merge_unit_id": "merge-unit"}},
        },
        {
            "event_id": "root-complete",
            "event_type": "TASK_UNIT_STATE_CHANGED",
            "object_id": "root",
            "occurred_at": _ms_timestamp(root_done_ms),
            "payload": {"new_state": "Completed"},
        },
    ]


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
