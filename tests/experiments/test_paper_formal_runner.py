import hashlib
import inspect
import json
import os
import pickle
import shutil
import stat
import gc
import weakref
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

import tokenshare.experiments.paper_budget as paper_budget
import tokenshare.experiments.paper_formal_runner as formal_runner
from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelectionBatch,
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_formal_callbacks import (
    Exp5RootHardLimitAuthority,
    PaperProviderExceptionAccounting,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperModelExecutionRecord,
    PaperStatus,
    PaperTaskStatus,
)
from tokenshare.experiments.paper_smoke_report import generate_paper_smoke_report
from tokenshare.local_runtime import (
    ParsedCandidateContext,
    WorkerTerminationPolicy,
    build_experiment_ablation_gate_applied_observation,
    build_experiment_premature_merge_attempted_observation,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


def test_metric_recompute_releases_protected_evidence_bytes_before_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from tokenshare.experiments import paper_formal_metrics

    observed = {}

    class EvidenceFiles:
        def __iter__(self):
            yield "evidence_manifest.json", b"{}"

    def load_inputs(_output_root, *, _defer_evidence_validation=False):
        assert _defer_evidence_validation is True
        evidence_files = EvidenceFiles()
        observed["evidence_files"] = weakref.ref(evidence_files)
        return SimpleNamespace(
            direct={"exp1_feasibility": ()},
            current={
                "global_infrastructure_valid": True,
                "canonical_runtime_evidence": (),
                "requested_lineage_root_ids": (),
                "current_trace_wrappers_by_root": {},
                "eligibility_facts_by_root": {},
            },
            source={"trace_source_bindings_by_root": {}},
            current_evidence_files=evidence_files,
        )

    def recompute(*_args, **_kwargs):
        gc.collect()
        assert observed["evidence_files"]() is None
        return SimpleNamespace(output_refs=())

    monkeypatch.setattr(
        formal_runner,
        "load_paper_traceability_replay_inputs",
        load_inputs,
    )
    monkeypatch.setattr(
        paper_formal_metrics,
        "derive_paper_metric_projection_rows",
        lambda value: value,
    )
    monkeypatch.setattr(
        paper_formal_metrics,
        "recompute_paper_formal_metrics",
        recompute,
    )

    formal_runner.recompute_paper_formal_metrics_from_runner_inputs(tmp_path)


@pytest.mark.parametrize(
    ("invalid_kind", "expect_error"),
    (
        (None, None),
        ("missing_expected_root", "fixed denominator mismatch"),
        ("duplicate_root", "duplicate canonical metric root"),
        ("non_bool_global", "global_infrastructure_valid must be a bool"),
        ("conflicting_source_resolver", "source resolver identity conflicts"),
        pytest.param(
            "cross_group_partition",
            "combined closure experiment/evidence partition is invalid",
            id="cross_group_partition",
        ),
        pytest.param(
            "trace_experiment_quota_mismatch",
            "combined closure experiment/evidence partition is invalid",
            id="trace_experiment_quota_mismatch",
        ),
        pytest.param(
            "trace_infrastructure_false",
            None,
            id="trace_infrastructure_false",
        ),
        pytest.param(
            "exp5_infrastructure_false",
            None,
            id="exp5_infrastructure_false",
        ),
    ),
)
def test_recompute_paper_formal_metrics_from_multiple_runner_inputs_rehydrates_two_namespaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str | None,
    expect_error: str | None,
) -> None:
    """两个既有 closure 只能分别复水，再以固定 115-root 分母合并。"""

    from tokenshare.experiments import paper_formal_metrics

    trace_copy_calls: list[Path] = []
    exp5_copy_calls: list[Path] = []
    rehydrate_calls: list[tuple[Path, object, object]] = []
    metric_calls: list[dict[str, object]] = []
    verified_resolver_maps: list[object] = []

    class EvidenceFiles:
        def __init__(self, copy_calls: list[Path]) -> None:
            self._copy_calls = copy_calls

        def copy_to(self, root: Path) -> None:
            self._copy_calls.append(root)
            root.mkdir(parents=True, exist_ok=True)
            (root / "evidence.json").write_text("{}", encoding="utf-8")

    class FixedTemporaryDirectory:
        def __init__(self, *, dir: str | Path, **_kwargs) -> None:
            self._root = Path(dir) / "stage"

        def __enter__(self) -> str:
            self._root.mkdir(parents=True, exist_ok=True)
            return str(self._root)

        def __exit__(self, *_args) -> None:
            return None

    base = _exp1_direct_metric_fixture(
        tmp_path / "fixture",
        evidence_class="real_model_trace_protocol_run",
    )

    def make_direct(
        *,
        root_id: str,
        experiment_id: str,
        evidence_class: str,
        condition_id: str,
    ):
        return replace(
            base,
            preregistered_root_run_id=root_id,
            experiment_id=experiment_id,
            evidence_class=evidence_class,
            condition_id=condition_id,
            case_id=f"case-{root_id}",
            _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
        )

    trace_rows = (
        *(
            make_direct(
                root_id=f"trace-exp1-{index:02d}",
                experiment_id="exp1_real_ai_feasibility",
                evidence_class="real_model_trace_protocol_run",
                condition_id="exp1-condition",
            )
            for index in range(12)
        ),
        *(
            make_direct(
                root_id=f"trace-exp2-{index:02d}",
                experiment_id="exp2_real_ai_scalability",
                evidence_class="real_model_trace_protocol_run",
                condition_id="exp2-condition",
            )
            for index in range(6)
        ),
        *(
            make_direct(
                root_id=f"trace-exp3-{index:02d}",
                experiment_id="exp3_real_ai_fault_recovery",
                evidence_class="real_model_trace_protocol_run",
                condition_id="exp3-condition",
            )
            for index in range(81)
        ),
    )
    exp5_rows = tuple(
        make_direct(
            root_id=f"exp5-{index:02d}",
            experiment_id="exp5_real_ai_model_endpoint_comparison",
            evidence_class="online_real_provider",
            condition_id="exp5-condition",
        )
        for index in range(16)
    )
    expected_exp5_root_ids = tuple(
        row.preregistered_root_run_id for row in exp5_rows
    )
    if invalid_kind == "cross_group_partition":
        trace_first = trace_rows[0]
        exp5_first = exp5_rows[0]
        trace_rows = (exp5_first, *trace_rows[1:])
        exp5_rows = (trace_first, *exp5_rows[1:])
    if invalid_kind == "trace_experiment_quota_mismatch":
        trace_rows = (
            *trace_rows[:12],
            replace(
                trace_rows[12],
                experiment_id="exp1_real_ai_feasibility",
                _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
            ),
            *trace_rows[13:],
        )
    if invalid_kind == "duplicate_root":
        exp5_rows = (
            replace(
                exp5_rows[0],
                preregistered_root_run_id=trace_rows[0].preregistered_root_run_id,
                _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
            ),
            *exp5_rows[1:],
        )

    empty = {key: () for key in formal_runner._DIRECT_METRIC_INPUT_KEYS}
    trace_inputs = {
        **empty,
        "exp1_feasibility": trace_rows[:12],
        "exp2_trace_scalability": trace_rows[12:18],
        "exp3_trace_robustness": trace_rows[18:],
    }
    exp5_inputs = {**empty, "experiment_5": exp5_rows}
    expected_root_ids = (
        *(row.preregistered_root_run_id for row in trace_rows),
        *(
            expected_exp5_root_ids
            if invalid_kind == "duplicate_root"
            else (row.preregistered_root_run_id for row in exp5_rows)
        ),
    )
    if invalid_kind == "missing_expected_root":
        expected_root_ids = expected_root_ids[:-1]

    def root_indexed(values):
        return {
            row.preregistered_root_run_id: (f"evidence:{row.preregistered_root_run_id}",)
            for row in values
        }

    trace_resolvers = {"trace-bank": object()}
    exp5_resolvers = {"exp5-bank": object()}
    if invalid_kind == "conflicting_source_resolver":
        exp5_resolvers = {"trace-bank": object()}

    trace_loaded = SimpleNamespace(
        direct=trace_inputs,
        current={
            "global_infrastructure_valid": (
                1
                if invalid_kind == "non_bool_global"
                else False
                if invalid_kind == "trace_infrastructure_false"
                else True
            ),
            "canonical_runtime_evidence": tuple(
                SimpleNamespace(preregistered_root_run_id=row.preregistered_root_run_id)
                for row in trace_rows
            ),
            "requested_lineage_root_ids": tuple(
                row.preregistered_root_run_id for row in trace_rows
            ),
            "current_trace_wrappers_by_root": root_indexed(trace_rows),
            "eligibility_facts_by_root": root_indexed(trace_rows),
        },
        source={
            "trace_source_bindings_by_root": root_indexed(trace_rows),
            "source_resolvers": trace_resolvers,
        },
        current_evidence_files=EvidenceFiles(trace_copy_calls),
    )
    exp5_loaded = SimpleNamespace(
        direct=exp5_inputs,
        current={
            "global_infrastructure_valid": invalid_kind != "exp5_infrastructure_false",
            "canonical_runtime_evidence": tuple(
                SimpleNamespace(preregistered_root_run_id=row.preregistered_root_run_id)
                for row in exp5_rows
            ),
            "requested_lineage_root_ids": tuple(
                row.preregistered_root_run_id for row in exp5_rows
            ),
            "current_trace_wrappers_by_root": root_indexed(exp5_rows),
            "eligibility_facts_by_root": root_indexed(exp5_rows),
        },
        source={
            "trace_source_bindings_by_root": root_indexed(exp5_rows),
            "source_resolvers": exp5_resolvers,
        },
        current_evidence_files=EvidenceFiles(exp5_copy_calls),
    )

    def load_inputs(root: Path):
        if root == tmp_path / "paid-output" / "exp1-exp3-trace":
            return trace_loaded
        if root == tmp_path / "paid-output" / "exp5-online":
            return exp5_loaded
        raise AssertionError(f"unexpected protected suite root: {root}")

    def rehydrate(*, evidence_root, canonical_metric_inputs, source_resolvers):
        rehydrate_calls.append(
            (Path(evidence_root), canonical_metric_inputs, source_resolvers)
        )
        return canonical_metric_inputs

    def recompute(metric_stage_root, canonical_direct_rows, **kwargs):
        metric_calls.append(
            {
                "metric_stage_root": Path(metric_stage_root),
                "canonical_direct_rows": canonical_direct_rows,
                **kwargs,
            }
        )
        metric_file = Path(metric_stage_root) / "metrics" / "paper_metric_drafts.v1.json"
        metric_file.parent.mkdir(parents=True, exist_ok=True)
        metric_file.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            provider_calls=0,
            metric_observations=(),
            output_refs=(
                {
                    "path": "metrics/paper_metric_drafts.v1.json",
                    "content_hash": "sha256:test",
                },
            ),
        )

    def verify(observations, resolvers):
        assert observations == ()
        verified_resolver_maps.append(resolvers)
        return 0

    monkeypatch.setattr(
        formal_runner,
        "load_paper_traceability_replay_inputs",
        load_inputs,
    )
    monkeypatch.setattr(formal_runner, "rehydrate_persisted_metric_inputs", rehydrate)
    monkeypatch.setattr(formal_runner.tempfile, "TemporaryDirectory", FixedTemporaryDirectory)
    monkeypatch.setattr(paper_formal_metrics, "recompute_paper_formal_metrics", recompute)
    monkeypatch.setattr(
        "tokenshare.experiments.paper_traceability.verify_external_source_locators",
        verify,
    )

    publication_root = tmp_path / "combined-publication"
    kwargs = {
        "publication_root": publication_root,
        "trace_suite_root": tmp_path / "paid-output" / "exp1-exp3-trace",
        "exp5_suite_root": tmp_path / "paid-output" / "exp5-online",
        "expected_root_ids": expected_root_ids,
    }
    if expect_error is not None:
        with pytest.raises(ValueError, match=expect_error):
            formal_runner.recompute_paper_formal_metrics_from_runner_input_roots(
                **kwargs
            )
        assert not publication_root.exists()
        if invalid_kind in {
            "cross_group_partition",
            "trace_experiment_quota_mismatch",
        }:
            assert trace_copy_calls == []
            assert exp5_copy_calls == []
            assert rehydrate_calls == []
            assert metric_calls == []
        return

    result = formal_runner.recompute_paper_formal_metrics_from_runner_input_roots(
        **kwargs
    )

    assert result.provider_calls == 0
    assert trace_copy_calls == [tmp_path / "stage" / "trace_evidence"]
    assert exp5_copy_calls == [tmp_path / "stage" / "exp5_evidence"]
    assert [call[0] for call in rehydrate_calls] == [
        tmp_path / "stage" / "trace_evidence",
        tmp_path / "stage" / "exp5_evidence",
    ]
    assert len(metric_calls) == 1
    assert set(metric_calls[0]["canonical_direct_rows"]) == set(
        formal_runner._DIRECT_METRIC_INPUT_KEYS
    )
    assert metric_calls[0]["requested_lineage_root_ids"] == tuple(
        sorted(expected_root_ids)
    )
    assert metric_calls[0]["lineage_evidence_roots_by_experiment"] == {
        "exp1_real_ai_feasibility": tmp_path / "stage" / "trace_evidence",
        "exp2_real_ai_scalability": tmp_path / "stage" / "trace_evidence",
        "exp3_real_ai_fault_recovery": tmp_path / "stage" / "trace_evidence",
        "exp5_real_ai_model_endpoint_comparison": tmp_path / "stage" / "exp5_evidence",
    }
    assert metric_calls[0]["global_infrastructure_valid"] is (
        invalid_kind
        not in {"trace_infrastructure_false", "exp5_infrastructure_false"}
    )
    assert verified_resolver_maps == [{**trace_resolvers, **exp5_resolvers}]
    assert (
        publication_root / "metrics" / "paper_metric_drafts.v1.json"
    ).is_file()


def test_runner_loads_protected_metrics_inputs_once(monkeypatch, tmp_path: Path) -> None:
    from tokenshare.experiments import paper_traceability

    calls = []
    protected = object()
    loaded = object()

    def load_root(_output_root, *, _defer_full_validation=False):
        calls.append(("root", _defer_full_validation))
        return protected

    def hydrate(value, *, _defer_evidence_validation=False):
        calls.append(("hydrate", value, _defer_evidence_validation))
        return loaded

    monkeypatch.setattr(
        formal_runner,
        "load_paper_traceability_replay_input_root",
        load_root,
    )
    monkeypatch.setattr(paper_traceability, "_load_protected_replay_inputs", hydrate)

    assert formal_runner.load_paper_traceability_replay_inputs(tmp_path) is loaded
    assert calls == [("root", True), ("hydrate", protected, False)]


def test_protected_evidence_closure_is_verified_lazily_without_resident_bytes(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_traceability import (
        _load_protected_replay_inputs,
    )
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs

    protected, _rows = _genuine_l4_inputs(tmp_path / "source")
    loaded = _load_protected_replay_inputs(protected)

    assert not isinstance(loaded.current_evidence_files, tuple)
    assert not any(
        isinstance(value, bytes)
        for value in vars(loaded.current_evidence_files).values()
    )
    materialized = tuple(loaded.current_evidence_files)
    assert materialized
    assert all(isinstance(content, bytes) for _name, content in materialized)


def test_protected_input_pickles_are_verified_and_loaded_without_read_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.experiments.paper_traceability import (
        _load_protected_replay_inputs,
    )
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs

    protected, _rows = _genuine_l4_inputs(tmp_path / "source")
    original = Path.read_bytes

    def guarded_read_bytes(path):
        if path.name.endswith("_inputs.pickle"):
            raise AssertionError("protected pickle must not be buffered as bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    loaded = _load_protected_replay_inputs(
        protected,
        _defer_evidence_validation=True,
    )
    assert loaded.direct and loaded.current and loaded.source


def test_protected_evidence_copy_is_chunked_verified_and_byte_exact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.experiments.paper_traceability import (
        _load_protected_replay_inputs,
    )
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs

    protected, _rows = _genuine_l4_inputs(tmp_path / "source")
    loaded = _load_protected_replay_inputs(
        protected,
        _defer_evidence_validation=True,
    )
    original = Path.read_bytes

    def guarded_read_bytes(path):
        if "current_evidence" in path.parts:
            raise AssertionError("protected evidence must be streamed")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    output_root = tmp_path / "materialized"
    loaded.current_evidence_files.copy_to(output_root)

    for ref in loaded.current_evidence_files.refs:
        relative = Path(str(ref["evidence_relative_path"]))
        source = protected.root_path / str(ref["path"])
        assert (output_root / relative).read_bytes() == original(source)


def test_metric_output_copy_is_atomic_streamed_and_byte_exact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.jsonl"
    target = tmp_path / "published" / "target.jsonl"
    content = (b'{"row":1}\n' * 1_000) + b'{"row":2}\n'
    source.write_bytes(content)
    original = Path.read_bytes

    def guarded_read_bytes(path):
        if path == source:
            raise AssertionError("metric output source must be streamed")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    formal_runner._atomic_copy_file(source, target)

    assert target.read_bytes() == content


CATALOG_DIGEST = "sha256:" + "1" * 64
BUDGET_DIGEST = "sha256:" + "2" * 64
EXPERIMENT_ID = "exp1_real_ai_feasibility"
PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
ENDPOINT_DIGEST = "sha256:" + "3" * 64
EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
MODEL_COHORT_ID = "paper-model-endpoint-cohort-v1"
MODEL_COHORT_DIGEST = "sha256:" + "4" * 64


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
@pytest.mark.parametrize("detector", ("junction", "file_attribute"))
def test_formal_path_guard_rejects_windows_reparse_identity_before_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    detector: str,
) -> None:
    guarded = tmp_path / "guarded"
    guarded.mkdir()
    real_is_junction = Path.is_junction
    real_lstat = Path.lstat
    if detector == "junction":
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda self: self == guarded or real_is_junction(self),
        )
    else:
        monkeypatch.setattr(Path, "is_junction", lambda _self: False)

        def fake_lstat(self):
            if self == guarded:
                return SimpleNamespace(
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
                )
            return real_lstat(self)

        monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="reparse"):
        formal_runner._reject_formal_reparse_path(
            guarded,
            recursive=True,
            check_existing_parents=True,
        )


def _smoke_execution_classification() -> dict[str, object]:
    return {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
        "baseline_policy": "omitted_for_smoke_regression",
        "ineligibility_reasons": [
            "smoke_suite",
            "pilot_only",
            "smoke_baseline_not_requested",
        ],
    }


def _condition_result(
    status: PaperStatus,
    *,
    evidence_complete_experimental_failure: bool = False,
) -> PaperConditionResult:
    return PaperConditionResult(
        condition_id="condition-status-probe",
        status=status,
        repeat_count=1,
        task_count=1,
        completed_root_count=0,
        failed_root_count=1,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref=(
            {
                "evidence_integrity": "complete",
                "outcome_counts": {"failed_experimental": 1},
            }
            if evidence_complete_experimental_failure
            else None
        ),
    )


@pytest.mark.parametrize(
    "status",
    (
        PaperStatus.FAILED,
        PaperStatus.BLOCKED,
        PaperStatus.BUDGET_EXHAUSTED,
        PaperStatus.INCOMPLETE,
    ),
)
def test_suite_status_preserves_infrastructure_terminal_status(
    status: PaperStatus,
) -> None:
    assert formal_runner._suite_status(
        plans=(SimpleNamespace(status="planned"),),
        results=[_condition_result(status)],
    ) is status


@pytest.mark.parametrize(
    "status",
    (
        PaperStatus.FAILED,
        PaperStatus.BLOCKED,
        PaperStatus.BUDGET_EXHAUSTED,
    ),
)
def test_smoke_classification_does_not_wash_infrastructure_status(
    status: PaperStatus,
) -> None:
    assert formal_runner._classified_suite_status(
        status,
        classification=_smoke_execution_classification(),
    ) is status


def test_smoke_keeps_evidence_complete_experimental_failure_nonfatal() -> None:
    status = formal_runner._suite_status(
        plans=(SimpleNamespace(status="planned"),),
        results=[
            _condition_result(
                PaperStatus.COMPLETED_WITH_FAILURES,
                evidence_complete_experimental_failure=True,
            )
        ],
    )

    assert status is PaperStatus.COMPLETED_WITH_FAILURES
    assert formal_runner._classified_suite_status(
        status,
        classification=_smoke_execution_classification(),
    ) is PaperStatus.COMPLETED_WITH_FAILURES


def test_smoke_classification_does_not_make_technical_attempt_evidence_incomplete() -> None:
    classification = _smoke_execution_classification()
    classified_attempt = {
        "paper_eligible": False,
        "paper_ineligibility_reasons": [],
        "ineligibility_reasons": classification["ineligibility_reasons"],
    }

    reasons = formal_runner._task_checkpoint_ineligibility_reasons(
        task={
            "record_scope": "protocol",
            "root_status": "completed",
            "evidence_artifact_refs": [{"artifact_id": "artifact-complete"}],
        },
        attempts=(classified_attempt,),
        events=({"record_scope": "protocol", "event_id": "event-complete"},),
        protocol_runtime={
            "execution_scope": "whole_root",
            "selected_ai_unit_ids": [],
        },
        real_transport=True,
        transport=object(),
        source_task_eligible=True,
    )
    classified_flags = formal_runner._evidence_flags(
        paper_eligible=True,
        execution_classification=classification,
    )

    assert "attempt_evidence_incomplete" not in reasons
    assert classified_attempt["paper_eligible"] is False
    assert classified_attempt["paper_ineligibility_reasons"] == []
    assert classified_flags["paper_eligible"] is False
    assert classified_flags["ineligibility_reasons"] == [
        "smoke_suite",
        "pilot_only",
        "smoke_baseline_not_requested",
    ]


@pytest.mark.parametrize(
    ("condition_status", "expected_suite_status"),
    (
        ("completed", "completed"),
        ("completed_with_failures", "completed_with_failures"),
        ("blocked", "blocked"),
        ("failed", "failed"),
        ("budget_exhausted", "budget_exhausted"),
        ("incomplete", "incomplete"),
    ),
)
def test_recomputed_suite_status_matches_live_terminal_semantics(
    condition_status: str,
    expected_suite_status: str,
) -> None:
    assert formal_runner._recomputed_suite_status(
        condition_statuses=[condition_status],
        has_plans=True,
        any_blocked_plan=False,
    ) == expected_suite_status


def test_protocol_event_projection_does_not_mutate_hashed_ledger_body(
    tmp_path: Path,
) -> None:
    ledger = EventLedger(tmp_path / "source-events.jsonl")
    event = ledger.append(
        event_type="TASK_REGISTERED",
        object_type="Task",
        object_id="paper_factorization_case-1",
        payload={"root_task_id": "paper_factorization_case-1"},
        idempotency_key="register-paper-factorization-case-1",
        task_id="paper_factorization_case-1",
        occurred_at="2026-07-31T00:00:00Z",
    ).to_dict()
    condition = SimpleNamespace(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-1",
        repeat_id=0,
    )

    projected = formal_runner._event_record_with_context(
        event,
        condition=condition,
        task_id="case-1",
    )

    assert projected == event


def test_provider_latency_observation_is_complete_or_null() -> None:
    incomplete = formal_runner._provider_latency_observation(
        attempts=(
            SimpleNamespace(provider_attempt_count=1, latency_ms=25),
            SimpleNamespace(provider_attempt_count=1, latency_ms=None),
        ),
        expected_provider_attempt_count=2,
    )
    zero_call = formal_runner._provider_latency_observation(
        attempts=(
            SimpleNamespace(provider_attempt_count=0, latency_ms=0),
        ),
        expected_provider_attempt_count=0,
    )

    assert incomplete == (
        None,
        "incomplete",
        "missing_provider_latency_evidence",
    )
    assert zero_call == (0.0, "not_applicable", "no_provider_attempts")


@pytest.mark.parametrize("selection", ("representative", "full"))
def test_trace_terminal_counts_only_explicit_current_provider_attempts(
    selection: str,
) -> None:
    attempts = (
        {
            "record_scope": "protocol",
            "schema_version": "tokenshare.paper_attempt_result.v3",
            "attempt_id": f"{selection}-trace-attempt-1",
            "provider": "deepseek",
            "provider_attempt_count": 0,
            # trace replacement/fault lineage 会保留 ordinal；它不是 transport 调用数。
            "provider_attempt_index": 1,
            "raw_output_ref": {"source": {"kind": "external_response_bank_locator"}},
        },
        {
            "record_scope": "protocol",
            "schema_version": "tokenshare.paper_attempt_result.v3",
            "attempt_id": f"{selection}-trace-attempt-2",
            "provider": "deepseek",
            "provider_attempt_count": 0,
            "provider_attempt_index": 3,
            "raw_output_ref": {"source": {"kind": "external_response_bank_locator"}},
        },
    )

    assert formal_runner._persisted_provider_attempt_count(attempts) == 0


def test_terminal_provider_count_keeps_legacy_fallback_only_when_count_is_absent(
) -> None:
    assert formal_runner._persisted_provider_attempt_count(
        (
            {
                "record_scope": "protocol",
                "schema_version": "tokenshare.paper_attempt_result.v1",
                "attempt_id": "legacy-provider-attempt",
                "provider": "deepseek",
                "provider_attempt_index": 1,
            },
        )
    ) == 1


@pytest.mark.parametrize(
    "attempt",
    (
        {
            "record_scope": "protocol",
            "schema_version": "tokenshare.paper_attempt_result.v3",
            "provider": "deepseek",
            "provider_attempt_index": 1,
        },
        {
            "record_scope": "protocol",
            "provider": "deepseek",
            "provider_attempt_index": 1,
        },
        {
            "record_scope": "protocol",
            "schema_version": "tokenshare.paper_attempt_result.v3",
            "provider": "deepseek",
            "provider_attempts": [{"provider": "deepseek"}],
            "provider_attempt_index": 1,
        },
        {
            "record_scope": "protocol",
            "provider": "deepseek",
            "provider_attempt_count": 0,
            "provider_attempts": [{"provider": "deepseek"}],
            "provider_attempt_index": 1,
        },
        {
            "record_scope": "protocol",
            "provider": "deepseek",
            "provider_attempt_count": None,
            "provider_attempt_index": 1,
        },
        {
            "record_scope": "protocol",
            "provider": "deepseek",
            "provider_attempt_count": False,
            "provider_attempt_index": 1,
        },
    ),
)


def test_terminal_provider_count_rejects_unknown_or_conflicting_mixed_evidence(
    attempt: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="provider attempt evidence"):
        formal_runner._persisted_provider_attempt_count((attempt,))


def _shared_exp1_manifest(
    *,
    request_limits: dict[str, object],
    fault_target_manifest: dict[str, object] | None,
    worker_death_manifest: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "baseline_policy": "omitted_for_smoke_regression",
        "fault_target_manifest": fault_target_manifest,
        "worker_death_manifest": worker_death_manifest,
        "request_limits": request_limits,
    }


def _complete_shared_exp1_reference(*_args, **_kwargs) -> dict[str, object]:
    source_versions = {
        **dict(_kwargs.get("expected_source_versions", {})),
        "runtime_generation_identity_digest": "sha256:" + "5" * 64,
    }
    core = {
        "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
        "reference_id": "shared-exp1-reference-case-1",
        "source_experiment_id": "exp1_real_ai_feasibility",
        "source_condition_id": "exp1_factorization_easy_w10_r0",
        "source_case_id": "case-1",
        "source_root_status": "completed",
        "evidence_integrity": "complete",
        "baseline_comparison_eligible": True,
        "baseline_unavailable_reason": None,
        "provider_calls_made": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
        "source_versions": source_versions,
    }
    return {**core, "source_hash": formal_runner.digest_json(core)}


def test_exp3_missing_trace_runtime_blocks_before_provider_dispatch() -> None:
    callback = object.__new__(formal_runner._FormalConditionExecutionCallback)
    object.__setattr__(callback, "execution_classification", None)
    object.__setattr__(callback, "trace_context", None)
    condition = SimpleNamespace(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3-factor-easy-w10-r0",
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="paired trace bank runtime is missing",
    ) as captured:
        callback._prepare_exp3_trace_reference(
            condition=condition,
            case_id="case-1",
            callback_kwargs={"execution_manifest": {}},
        )
    assert captured.value.terminal_outcome.failure_stage == "paired_trace_reference"


def test_formal_exp3_rejects_smoke_baseline_omission_before_dispatch() -> None:
    callback = object.__new__(formal_runner._FormalConditionExecutionCallback)
    object.__setattr__(callback, "execution_classification", None)
    condition = SimpleNamespace(experiment_id="exp3_real_ai_fault_recovery")

    with pytest.raises(ValueError, match="formal Exp3 scope"):
        callback._prepare_exp3_trace_reference(
            condition=condition,
            case_id="case-1",
            callback_kwargs={
                "execution_manifest": {
                    "baseline_policy": "omitted_for_smoke_regression",
                }
            },
        )


def test_formal_runner_rejects_exp3_before_exp1_before_output_creation(
    tmp_path: Path,
) -> None:
    suite_root = tmp_path / "reversed-suite"
    config = _ai_config()
    exp3 = "exp3_real_ai_fault_recovery"
    exp1 = "exp1_real_ai_feasibility"
    exp3_plan = _plan_for_experiment(
        tmp_path=suite_root,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-reversed",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    exp1_plan = _plan_for_experiment(
        tmp_path=suite_root,
        config=config,
        experiment_id=exp1,
        condition_id="condition-exp1-reversed",
    )
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=exp3_plan,
    )
    kwargs.update(
        {
            "dispatch_plans": (exp3_plan, exp1_plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(exp3, exp1),
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *exp3_plan.bound_items(),
                *exp1_plan.bound_items(),
            ),
        }
    )

    with pytest.raises(ValueError, match="Exp1 must precede Exp3"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert not suite_root.exists()


def test_dependency_integrity_classification_is_typed_not_message_based() -> None:
    assert formal_runner._dependency_integrity_for_error(
        ValueError("missing hash corrupt absent")
    ) is formal_runner.PaperEvidenceIntegrity.INVALID
    assert formal_runner._dependency_integrity_for_error(
        FileNotFoundError("opaque")
    ) is formal_runner.PaperEvidenceIntegrity.MISSING
    assert formal_runner._dependency_integrity_for_error(
        json.JSONDecodeError("opaque", "", 0)
    ) is formal_runner.PaperEvidenceIntegrity.CORRUPT


def test_missing_trace_bank_closes_suite_and_stops_new_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id="condition-exp3-shared-source-missing",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-must-not-dispatch",
        ablation_mode="NO_VERIFICATION",
    )
    invoked_conditions: list[str] = []
    provider_dispatches: list[str] = []

    class Exp3Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest={
                    "baseline_policy": "shared_exp1_reference",
                    "fault_target_manifest": {
                        "fault_type": "false_positive",
                        "selected_target_ai_unit_ids": ["case-1:range_0"],
                        "reserve_target_ai_unit_ids": [],
                    },
                    "worker_death_manifest": None,
                    "matched_baseline": {
                        "source_kind": "shared_exp1_reference",
                        "comparison_kind": "shared_reference",
                        "additional_execution_required": False,
                        "source_experiment_id": "exp1_real_ai_feasibility",
                        "source_repeat_id": 0,
                        "source_seed": 1,
                        "source_worker_count": 10,
                        "source_reference_ids_by_case": {
                            "case-1": "planned-ref-case-1"
                        },
                        "reference_policy_id": "policy-case-1",
                        "request_limits": dict(context.request_limits),
                    },
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3Module()), (exp4, Exp4Module())),
    )

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        raise AssertionError("missing shared evidence must block before provider dispatch")

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            ),
            "dispatch_plans": (exp3_plan, exp4_plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(exp3, exp4),
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *exp3_plan.bound_items(),
                *exp4_plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.BLOCKED
    assert invoked_conditions == ["condition-exp3-shared-source-missing"]
    assert provider_dispatches == []
    assert suite.error_summary == (
        {
            "outcome_status": "blocked_dependency",
            "evidence_integrity": "missing",
                "failure_stage": "paired_trace_reference",
                "failure_kind": "source_bank_runtime_unavailable",
            "condition_id": "condition-exp3-shared-source-missing",
            "task_id": "case-1",
        },
    )
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "condition-exp3-shared-source-missing",
        "per_task_results.jsonl",
    )[0]
    exp4_task = _generation_records(
        tmp_path,
        exp4,
        "condition-exp4-must-not-dispatch",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["root_status"] == "blocked"
    assert exp3_task["outcome_status"] == "blocked_dependency"
    assert exp3_task["evidence_integrity"] == "missing"
    assert exp4_task["root_status"] == "not_started"
    assert exp4_task["outcome_status"] == "blocked_dependency"
    assert exp4_task["evidence_integrity"] == "missing"
    for experiment_id in (exp3, exp4):
        manifest = json.loads(
            (
                tmp_path
                / "experiments"
                / experiment_id
                / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "blocked"
    assert json.loads(
        (tmp_path / "formal_runner_result.json").read_text(encoding="utf-8")
    )["status"] == "blocked"


def test_smoke_launch_manifest_is_persisted_fail_closed_before_execution(
    tmp_path: Path,
) -> None:
    launch = {
        "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
        "launch_manifest_digest": "sha256:" + "5" * 64,
        "paper_eligible": False,
    }

    normalized = formal_runner._normalize_pre_execution_documents(
        {"smoke_launch_manifest.json": launch}
    )
    formal_runner._persist_pre_execution_documents(
        suite_root=tmp_path,
        documents=normalized,
    )

    persisted = json.loads(
        (tmp_path / "smoke_launch_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted == launch
    with pytest.raises(
        ValueError,
        match="pre-execution document identity mismatch",
    ):
        formal_runner._persist_pre_execution_documents(
            suite_root=tmp_path,
            documents={
                "smoke_launch_manifest.json": {
                    **launch,
                    "launch_manifest_digest": "sha256:" + "6" * 64,
                }
            },
        )


def test_smoke_recovery_manifest_is_append_only_and_path_safe(
    tmp_path: Path,
) -> None:
    recovery = {
        "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
        "recovery_id": "smoke_recovery_1234abcd",
        "recovery_manifest_digest": "sha256:" + "7" * 64,
        "paper_eligible": False,
    }
    relative_path = "smoke_recoveries/smoke_recovery_1234abcd.json"

    normalized = formal_runner._normalize_recovery_documents(
        {relative_path: recovery}
    )
    formal_runner._persist_pre_execution_documents(
        suite_root=tmp_path,
        documents=normalized,
    )

    assert json.loads(
        (tmp_path / relative_path).read_text(encoding="utf-8")
    ) == recovery
    with pytest.raises(ValueError, match="recovery document path"):
        formal_runner._normalize_recovery_documents(
            {"../smoke_recovery_escape.json": recovery}
        )


def test_formal_runner_binds_frozen_lean_case_metadata_for_adapter() -> None:
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_ID,
        condition_id="exp1_lean_simple_pure_logic_w10_r0",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        topic_family="pure_logic",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    case = {
        "paper_difficulty": "simple",
        "topic_family": "pure_logic",
        "topic_family_version": "shallow_v1",
        "construction_rule_id": None,
        "oracle_package_group": None,
        "proof_assembly_shape": None,
    }

    bound = formal_runner._condition_with_frozen_case_metadata(
        condition=condition,
        case=case,
    )

    assert condition.topic_family_version is None
    assert bound.condition_id == condition.condition_id
    assert bound.topic_family_version == "shallow_v1"
    assert bound.construction_rule_id is None

    with pytest.raises(ValueError, match="condition topic_family_version"):
        formal_runner._condition_with_frozen_case_metadata(
            condition=replace(condition, topic_family_version="wrong_v1"),
            case=case,
        )


def test_formal_runner_binds_frozen_factorization_difficulty_for_adapter() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r0__rep0",
        domain="factorization",
        difficulty="medium",
        paper_difficulty="medium",
        worker_count=10,
        fault_type="false_positive",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )

    bound = formal_runner._condition_with_frozen_case_metadata(
        condition=condition,
        case={"difficulty": "easy", "paper_difficulty": "easy"},
    )

    assert condition.difficulty == "medium"
    assert condition.paper_difficulty == "medium"
    assert bound.condition_id == condition.condition_id
    assert bound.difficulty == "easy"
    assert bound.paper_difficulty == "easy"


def test_direct_collector_reuses_catalog_case_across_distinct_condition_axes(
    tmp_path: Path,
) -> None:
    config = _ai_config()
    exp1_plan = _planned_dispatch_plan(tmp_path, config=config)
    exp1_condition, exp1_selection = exp1_plan.bound_items()[0]
    exp3_condition = replace(
        exp1_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r50__rep0",
        difficulty="medium",
        paper_difficulty="medium",
        fault_type="false_positive",
        fault_rate=0.5,
    )
    exp3_selection = replace(
        exp1_selection,
        selection_id="exp3-selection",
        experiment_id=exp3_condition.experiment_id,
        paper_difficulty="medium",
    )
    exp3_plan = replace(
        exp1_plan,
        experiment_id=exp3_condition.experiment_id,
        output_root=(tmp_path / exp3_condition.experiment_id).as_posix(),
        conditions=(exp3_condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                exp3_condition,
                exp3_selection,
            ),
        ),
    )
    catalog = {
        "catalog_digest": CATALOG_DIGEST,
        "factorization_cases": (
            {
                "case_id": "case-1",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "factor_position_quantile": "early",
                "expected_ai_unit_count": 1,
            },
        ),
        "lean_cases": (),
        "lean_lemma_graph_cases": (),
    }

    collector = formal_runner._build_canonical_direct_collector(
        bound_plans=(
            (exp1_plan, exp1_plan.bound_items()),
            (exp3_plan, exp3_plan.bound_items()),
        ),
        catalog_manifest=catalog,
        normalized_root_filter={},
        evidence_class="real_model_trace_protocol_run",
    )

    assert len(collector.catalog_manifests[0]["records"]) == 1
    case_record = collector.catalog_manifests[0]["records"][0]
    assert (case_record["domain"], case_record["difficulty"]) == (
        "factorization",
        "easy",
    )
    condition_records = collector.condition_manifests[0]["records"]
    assert [record["condition_axes"]["difficulty"] for record in condition_records] == [
        "easy",
        "medium",
    ]
    assert len(collector.inventory.rows) == 2
    assert {
        row.preregistered_case_ref["case_record_digest"]
        for row in collector.inventory.rows
    } == {case_record["case_record_digest"]}
    projection = formal_runner.project_paper_direct_results(
        root_inventory_manifest=collector.inventory,
        condition_manifests=collector.condition_manifests,
        catalog_manifests=collector.catalog_manifests,
        canonical_runtime_evidence=(),
    )
    assert {row.condition_id for row in projection.rows} == {
        exp1_condition.condition_id,
        exp3_condition.condition_id,
    }
    assert {row.case_id for row in projection.rows} == {"case-1"}


@pytest.mark.parametrize(
    ("experiment_id", "paper_difficulty", "ablation_mode"),
    (
        pytest.param(
            "exp3_real_ai_fault_recovery",
            "medium_lemma_dag",
            "FULL",
            id="exp3-all-topics-rate-fault",
        ),
        *(
            pytest.param(
                "exp4_real_ai_protocol_ablation",
                paper_difficulty,
                ablation_mode,
                id=f"exp4-{paper_difficulty}-{ablation_mode.lower()}",
            )
            for paper_difficulty in (
                "simple",
                "medium_lemma_dag",
                "hard_frontier",
            )
            for ablation_mode in (
                "FULL",
                "NO_VERIFICATION",
                "NO_PARSER_POLICY",
                "NO_REQUEUE",
                "NO_MERGE_GATE",
            )
        ),
    ),
)
def test_direct_collector_binds_mixed_lean_topic_axis_from_official_selection(
    tmp_path: Path,
    experiment_id: str,
    paper_difficulty: str,
    ablation_mode: str,
) -> None:
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tokenshare.experiments.paper_exp3_fault_recovery import (
        Exp3MixedLeanCaseSelection,
    )
    from tokenshare.experiments.paper_exp4_ablation_runner import (
        Exp4MixedLeanCaseSelection,
    )

    exp4_case_ids = {
        "simple": (
            "lean_easy_01",
            "lean_easy_06",
            "lean_v2_simple_function_set_direct_subset_01",
            "lean_v2_simple_function_set_direct_subset_02",
            "lean_v2_simple_induction_direct_nat_01",
        ),
        "medium_lemma_dag": (
            "lean_v2_medium_lemma_dag_01",
            "lean_v2_medium_function_set_dx_subset_chain_01",
            "lean_v2_medium_function_set_dx_subset_chain_02",
            "lean_v2_medium_induction_nat_predicate_chain_01",
            "lean_v2_medium_induction_nat_predicate_chain_02",
        ),
        "hard_frontier": (
            "lean_v2_hard_frontier_pure_logic_checker_01",
            "lean_v2_hard_frontier_pure_logic_checker_02",
            "lean_v2_hard_frontier_function_set_checker_01",
            "lean_v2_hard_frontier_induction_checker_01",
            "lean_v2_hard_frontier_induction_checker_02",
        ),
    }
    exp3_case_ids = (
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
    )
    selected_case_ids = (
        exp3_case_ids
        if experiment_id == "exp3_real_ai_fault_recovery"
        else exp4_case_ids[paper_difficulty]
    )
    official_cases = {
        case["case_id"]: case
        for case in (
            *(
                paper_catalog_module._with_lean_v1_paper_difficulty(
                    json.loads(line)
                )
                for line in Path("benchmarks/paper/lean_catalog.v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ),
            *(
                json.loads(line)
                for line in Path(
                    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
                )
                .read_text(encoding="utf-8")
                .splitlines()
            ),
        )
        if case["case_id"] in selected_case_ids
    }
    assert set(official_cases) == set(selected_case_ids)
    assert {case["topic_family"] for case in official_cases.values()} == {
        "pure_logic",
        "function_set",
        "induction",
    }

    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id=(
            f"condition-mixed-lean-{paper_difficulty}-{ablation_mode.lower()}"
        ),
        domain="lean_proof",
        difficulty={
            "simple": "easy",
            "medium_lemma_dag": "medium",
            "hard_frontier": "hard",
        }[paper_difficulty],
        paper_difficulty=paper_difficulty,
        topic_family=None,
        fault_type=(
            "false_positive"
            if experiment_id == "exp3_real_ai_fault_recovery"
            else "none"
        ),
        fault_rate=(
            0.1 if experiment_id == "exp3_real_ai_fault_recovery" else 0.0
        ),
        ablation_mode=ablation_mode,
    )
    selection_type = (
        Exp3MixedLeanCaseSelection
        if experiment_id == "exp3_real_ai_fault_recovery"
        else Exp4MixedLeanCaseSelection
    )
    selected_cases = tuple(official_cases[case_id] for case_id in selected_case_ids)
    selection = selection_type(
        selection_id=f"selection:{condition.condition_id}",
        experiment_id=experiment_id,
        suite_version=base_selection.suite_version,
        catalog_version=base_selection.catalog_version,
        domain="lean_proof",
        paper_difficulty=paper_difficulty,
        topic_family=None,
        ordered_case_ids=selected_case_ids,
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=sum(
            paper_catalog_module.estimated_ai_units_for_case(case)
            for case in selected_cases
        ),
        paper_eligible_required=True,
    )
    plan = replace(
        base,
        experiment_id=experiment_id,
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    catalog = {
        "catalog_digest": CATALOG_DIGEST,
        "factorization_cases": (),
        "lean_cases": tuple(
            case
            for case in selected_cases
            if case["schema_version"] == "tokenshare.paper_lean_case.v1"
        ),
        "lean_lemma_graph_cases": tuple(
            case
            for case in selected_cases
            if case["schema_version"]
            == "tokenshare.paper_lean_lemma_graph_case.v1"
        ),
    }

    collector = formal_runner._build_canonical_direct_collector(
        bound_plans=((plan, plan.bound_items()),),
        catalog_manifest=catalog,
        normalized_root_filter={},
        evidence_class="real_model_trace_protocol_run",
    )

    axes = collector.condition_manifests[0]["records"][0]["condition_axes"]
    assert axes["topic_family"] == "mixed_topic_family"
    projection = formal_runner.project_paper_direct_results(
        root_inventory_manifest=collector.inventory,
        condition_manifests=collector.condition_manifests,
        catalog_manifests=collector.catalog_manifests,
        canonical_runtime_evidence=(),
    )
    assert len(projection.rows) == len(selected_case_ids)


def test_direct_collector_rejects_unbound_partial_mixed_lean_topic_axis() -> None:
    with pytest.raises(
        ValueError,
        match="mixed Lean condition topic axis requires all official topic families",
    ):
        formal_runner._canonical_condition_topic_axis(
            condition=SimpleNamespace(domain="lean_proof", topic_family=None),
            selection=SimpleNamespace(ordered_case_ids=("lean-pure-only",)),
            cases={
                "lean-pure-only": {
                    "case_id": "lean-pure-only",
                    "topic_family": "pure_logic",
                }
            },
            catalog_case_identity_axes={
                "lean-pure-only": ("lean_proof", "medium_lemma_dag")
            },
        )


def test_direct_collector_binds_official_lean_root_theorem_authority(
    tmp_path: Path,
) -> None:
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tokenshare.experiments.paper_catalog import lean_theorem_payload_from_case

    case = paper_catalog_module._with_lean_v1_paper_difficulty(
        json.loads(
            Path("benchmarks/paper/lean_catalog.v1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
    )
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        condition_id="condition-lean-official-authority",
        domain="lean_proof",
        difficulty=str(case["difficulty"]),
        paper_difficulty=str(case["paper_difficulty"]),
        topic_family=str(case["topic_family"]),
        topic_family_version=str(case["topic_family_version"]),
    )
    selection = replace(
        base_selection,
        selection_id="selection-lean-official-authority",
        domain="lean_proof",
        paper_difficulty=str(case["paper_difficulty"]),
        topic_family=str(case["topic_family"]),
        ordered_case_ids=(str(case["case_id"]),),
        expected_ai_unit_count=paper_catalog_module.estimated_ai_units_for_case(
            case
        ),
    )
    plan = replace(
        base,
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    catalog = {
        "catalog_digest": CATALOG_DIGEST,
        "factorization_cases": (),
        "lean_cases": (case,),
        "lean_lemma_graph_cases": (),
    }

    collector = formal_runner._build_canonical_direct_collector(
        bound_plans=((plan, plan.bound_items()),),
        catalog_manifest=catalog,
        normalized_root_filter={},
        evidence_class="online_real_provider",
    )
    row = collector.inventory.rows[0]
    official_root = lean_theorem_payload_from_case(case)

    assert row.preregistered_case_ref["schema_version"] == (
        "tokenshare.preregistered_lean_case_ref.v1"
    )
    assert row.preregistered_case_ref["official_case_digest"] == (
        formal_runner.digest_json(case)
    )
    assert row.preregistered_case_ref["official_root_theorem_id"] == (
        official_root.theorem_id
    )
    assert row.preregistered_case_ref[
        "official_root_theorem_payload_digest"
    ] == official_root.payload_digest
    projected = formal_runner.project_paper_direct_results(
        root_inventory_manifest=collector.inventory,
        condition_manifests=collector.condition_manifests,
        catalog_manifests=collector.catalog_manifests,
        canonical_runtime_evidence=(),
    )
    assert projected.rows[0].root_status == "not_started"


@pytest.mark.parametrize(
    "protocol_runtime",
    (
        {},
        {"event_ledger_path": ""},
        {"event_ledger_path": "../outside.jsonl"},
        {"event_ledger_path": "/absolute/event_log.jsonl"},
        {"event_ledger_path": "events\\event_log.jsonl"},
    ),
)
def test_native_event_ledger_path_requires_explicit_safe_relative_posix_binding(
    tmp_path: Path,
    protocol_runtime: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="event ledger path"):
        formal_runner._native_event_ledger_path(
            native_root=tmp_path,
            protocol_runtime=protocol_runtime,
        )


def test_native_event_ledger_path_accepts_lean_runtime_binding(tmp_path: Path) -> None:
    expected = tmp_path / "events" / "event_log.jsonl"

    assert formal_runner._native_event_ledger_path(
        native_root=tmp_path,
        protocol_runtime={"event_ledger_path": "events/event_log.jsonl"},
    ) == expected.resolve(strict=False)


def test_runtime_final_artifact_ref_selects_lean_proof_from_multi_output_merge() -> None:
    merge_result = ArtifactRef(
        artifact_id="canonical-lean-merge-result",
        artifact_type="canonical_output",
        uri="artifacts/canonical-lean-merge-result",
        content_hash="sha256:" + "1" * 64,
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="lean_proof.merge_result",
        artifact_schema_version="v1",
        source={"kind": "lean_runtime_merge_promotion"},
        metadata={"output_name": "lean_merge_result"},
        created_at="2026-07-14T00:00:00Z",
    )
    proof_artifact = ArtifactRef(
        artifact_id="canonical-lean-proof-artifact",
        artifact_type="canonical_output",
        uri="artifacts/canonical-lean-proof-artifact",
        content_hash="sha256:" + "2" * 64,
        size_bytes=1,
        media_type="text/x-lean",
        artifact_schema_id="lean_proof.proof_artifact",
        artifact_schema_version="v1",
        source={"kind": "lean_runtime_merge_promotion"},
        metadata={"output_name": "lean_proof_artifact"},
        created_at="2026-07-14T00:00:00Z",
    )
    event = SimpleNamespace(
        event_type="MERGE_RECORDED",
        payload={
            "parent_unit_id": "paper-lean-root",
            "merge_output_refs": {
                "lean_merge_result": merge_result.to_dict(),
                "lean_proof_artifact": proof_artifact.to_dict(),
            },
        },
    )

    assert formal_runner._runtime_final_artifact_ref(
        events=(event,),
        root_unit_id="paper-lean-root",
    ) == proof_artifact


@pytest.mark.parametrize("ledger_body", (None, "{}\n"))
def test_native_event_ledger_binding_rejects_empty_or_invalid_ledger(
    tmp_path: Path,
    ledger_body: str | None,
) -> None:
    ledger_path = tmp_path / "events" / "event_log.jsonl"
    if ledger_body is not None:
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text(ledger_body, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="canonical direct evidence native ledger is invalid",
    ):
        formal_runner._validated_native_event_ledger(
            native_root=tmp_path,
            protocol_runtime={"event_ledger_path": "events/event_log.jsonl"},
        )


def test_exp3_hook_ignores_non_target_parsed_candidate_before_raw_output() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r0__rep0",
        domain="factorization",
        difficulty="medium",
        paper_difficulty="medium",
        worker_count=10,
        fault_type="false_positive",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    ref = ArtifactRef(
        artifact_id="subject",
        artifact_type="Subject",
        uri="artifacts/subject",
        content_hash="sha256:" + "a" * 64,
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="test.subject",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-07-27T00:00:00Z",
    )
    bridge = formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id="factor_v2_easy_001",
        fault_type="false_positive",
        selected_unit_ids=(),
        reserve_unit_ids=(),
        runtime_records=[],
    )

    directive = bridge.after_parsed_candidate_persisted(
        ParsedCandidateContext(
            run_id="run-root",
            task_id="task-root",
            unit_id="factor-root",
            attempt_id="attempt-root",
            lease_id="lease-root",
            worker_id="worker-root",
            raw_output_ref=None,
            original_parsed_output_ref=ref,
            candidate_output_refs={"subject": ref},
            submitted_at="2026-07-27T00:00:01Z",
            experiment_unit_id=None,
        )
    )

    assert directive is None


def test_exp3_hook_rejects_target_parsed_candidate_without_prior_raw_output() -> None:
    condition = PaperExperimentCondition(
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_rate_fault_factorization__false_positive__r0__rep0",
        domain="factorization",
        difficulty="medium",
        paper_difficulty="medium",
        worker_count=10,
        fault_type="false_positive",
        fault_rate=1.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    ref = ArtifactRef(
        artifact_id="candidate",
        artifact_type="RangeResult",
        uri="artifacts/candidate",
        content_hash="sha256:" + "b" * 64,
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="factorization.range_result",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-07-27T00:00:00Z",
    )
    bridge = formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id="factor_v2_easy_001",
        fault_type="false_positive",
        selected_unit_ids=("factor_v2_easy_001:range_0",),
        reserve_unit_ids=(),
        runtime_records=[],
    )

    with pytest.raises(ValueError, match="requires prior raw observation"):
        bridge.after_parsed_candidate_persisted(
            ParsedCandidateContext(
                run_id="run-root",
                task_id="task-root",
                unit_id="factor-range-0",
                attempt_id="attempt-root",
                lease_id="lease-root",
                worker_id="worker-root",
                raw_output_ref=ref,
                original_parsed_output_ref=ref,
                candidate_output_refs={"range_result": ref},
                submitted_at="2026-07-27T00:00:01Z",
                experiment_unit_id="range_0",
            )
        )


COHORT_MEMBER_ID = "glm_5_2_siliconflow"


def test_formal_runner_checkpoints_required_evidence_and_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        adapter_calls.append(kwargs["case"]["case_id"])
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner.shutil,
        "copytree",
        lambda *_args, **_kwargs: pytest.fail(
            "formal execution must not publish a compatibility copy"
        ),
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    executed = formal_runner.execute_paper_formal_suite(**kwargs)
    assert executed.status == PaperStatus.COMPLETED
    assert executed.provider_attempt_count == 1
    assert executed.total_tokens == 11
    assert executed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]

    suite_files = {
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "conditions.jsonl",
        "evidence_manifest.json",
    }
    assert suite_files <= {item.name for item in tmp_path.iterdir()}
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["formal"] is True
    assert suite_manifest["pilot_only"] is False
    assert suite_manifest["execution_scope"] == "formal_matrix"
    assert suite_manifest["regression_only"] is True
    assert suite_manifest["paper_eligible"] is False
    assert not (tmp_path / EXPERIMENT_ID / "experiment_manifest.json").exists()

    adapter_root = (
        tmp_path / EXPERIMENT_ID / "runs" / "condition-1" / "case-1"
    )
    assert not adapter_root.exists()
    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
    )
    assert (run_root / "CURRENT.json").is_file()
    assert len(list((run_root / "artifacts" / "case-1").glob("*-raw-output.txt"))) == 1
    assert len(list((run_root / "artifacts" / "case-1").glob("*-request.json"))) == 1
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    checkpoint_task = json.loads(
        (
            run_root
            / ".generations"
            / current["generation_id"]
            / "per_task_results.jsonl"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint_task["runtime_generation_identity"] == {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": "run-case-1",
        "task_id": "case-1",
        "root_unit_id": "root-case-1",
        "event_count": 1,
        "first_event_id": "event-case-1",
        "last_event_id": "event-case-1",
        "last_event_hash": "sha256:event-case-1",
        "ledger_digest": "sha256:ledger-case-1",
    }
    assert checkpoint_task["case_id"] == "case-1"
    assert checkpoint_task["factor_position_quantile"] == "early"
    assert checkpoint_task["runtime_observation"][
        "planned_ai_unit_ids"
    ] == ["range_0"]

    evidence_store = formal_runner.FormalEvidenceStore(tmp_path)
    evidence_store._validate_evidence_manifest()

    recovery_path = "smoke_recoveries/smoke_recovery_test.json"
    resumed = formal_runner.execute_paper_formal_suite(
        **kwargs,
        resume=True,
        recovery_documents={
            recovery_path: {
                "schema_version": "tokenshare.paper_smoke_recovery_manifest.v1",
                "recovery_id": "smoke_recovery_test",
                "recovery_manifest_digest": "sha256:" + "8" * 64,
                "paper_eligible": False,
            }
        },
    )
    assert resumed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]
    assert (tmp_path / recovery_path).is_file()

    replayed = formal_runner.execute_paper_formal_suite(
        **{**kwargs, "ai_api_configs": {}, "transport": None},
        replay_only=True,
    )
    assert replayed.to_dict() == executed.to_dict()
    assert adapter_calls == ["case-1"]

    replay_report_ref = formal_runner.write_paper_formal_replay_report(
        output_root=tmp_path
    )
    replay_report_path = tmp_path / "audit" / "replay_report.json"
    replay_report = json.loads(replay_report_path.read_text(encoding="utf-8"))
    assert replay_report_ref["path"] == "audit/replay_report.json"
    assert replay_report_ref["content_hash"] == (
        "sha256:" + hashlib.sha256(replay_report_path.read_bytes()).hexdigest()
    )
    assert replay_report["schema_version"] == "tokenshare.paper_replay_report.v1"
    assert replay_report["status"] == "replay_verified"
    assert replay_report["provider_calls_made"] == 0
    assert replay_report["replayed_result"] == executed.to_dict()
    assert replay_report["replayed_result_digest"].startswith("sha256:")
    assert replay_report["persisted_result_ref"]["path"] == (
        "formal_runner_result.json"
    )
    assert replay_report["persisted_result_ref"]["content_hash"].startswith("sha256:")
    assert replay_report["comparison"]["status"] == "matched"
    assert replay_report["comparison"]["mismatched_fields"] == []
    recomputed = replay_report["independently_recomputed_summary"]
    assert recomputed["status"] == "completed"
    assert recomputed["condition_count"] == 1
    assert recomputed["run_count"] == 1
    assert recomputed["task_count"] == 1
    assert recomputed["provider_attempt_count"] == 1
    assert recomputed["total_tokens"] == 11
    assert recomputed["total_cost_estimate"] == pytest.approx(0.125)
    assert recomputed["paper_eligible"] is False
    assert recomputed["error_summary"] == []
    assert replay_report["independently_recomputed_summary_digest"].startswith(
        "sha256:"
    )
    assert replay_report["source_refs"]["formal_runner_result"]["path"] == (
        "formal_runner_result.json"
    )
    assert replay_report["source_refs"]["evidence_manifest"]["path"] == (
        "evidence_manifest.json"
    )
    assert replay_report["cell_traceability_replay"] == "separate_l4_entrypoint"
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()
    first_report_bytes = replay_report_path.read_bytes()
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    repeated_ref = formal_runner.write_paper_formal_replay_report(
        output_root=tmp_path
    )
    assert replay_report_path.read_bytes() == first_report_bytes
    assert repeated_ref == replay_report_ref


def test_formal_runner_preserves_adapter_tree_when_checkpoint_fails(
    tmp_path: Path,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
    adapter_root = (
        Path(plan.output_root)
        / "runs"
        / condition.condition_id
        / "case-1"
    )
    adapter_result = _complete_adapter_result(
        output_root=adapter_root.parent,
        condition=condition,
        case_id="case-1",
    )

    class FailingEvidenceStore:
        output_root = tmp_path

        def checkpoint_root(self, **_kwargs):
            raise RuntimeError("checkpoint publication failed")

    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=FailingEvidenceStore(),
        completed_task_keys=set(),
        usage=SimpleNamespace(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
    )

    with pytest.raises(RuntimeError, match="checkpoint publication failed"):
        callback._checkpoint_adapter_result(
            condition=condition,
            task_id="case-1",
            task=adapter_result.task_result,
            adapter_result=adapter_result,
            adapter_root=adapter_root,
            adapter_output_root=adapter_root,
        )

    assert adapter_root.is_dir()
    assert any(path.is_file() for path in adapter_root.rglob("*"))
    assert not (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-1"
        / "0"
        / "CURRENT.json"
    ).exists()


@pytest.mark.parametrize("terminal_kind", ("exception", "budget"))
def test_terminal_checkpoint_removes_existing_exact_adapter_case(
    tmp_path: Path,
    terminal_kind: str,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
    task_id = "case-1"
    adapter_root = (
        Path(plan.output_root)
        / "runs"
        / condition.condition_id
        / task_id
    )
    adapter_root.mkdir(parents=True)
    (adapter_root / "working.txt").write_text("working", encoding="utf-8")
    canonical_run_root = (
        tmp_path
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    canonical_run_root.mkdir(parents=True)
    (canonical_run_root / "CURRENT.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "1" * 64,
            }
        ),
        encoding="utf-8",
    )

    class CommitEvidenceStore:
        output_root = tmp_path

        def checkpoint_root(self, **_kwargs):
            return {
                "schema_version": "tokenshare.paper_checkpoint_commit.v1",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": str(condition.repeat_id),
                "task_id": task_id,
                "run_root": (
                    Path("experiments")
                    / condition.experiment_id
                    / "runs"
                    / condition.condition_id
                    / str(condition.repeat_id)
                ).as_posix(),
                "generation_id": "generation-1",
                "generation_manifest_digest": "sha256:" + "1" * 64,
            }

    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=CommitEvidenceStore(),
        completed_task_keys=set(),
        usage=formal_runner._UsageTotals(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
    )

    if terminal_kind == "exception":
        callback._checkpoint_exception(
            condition=condition,
            task_id=task_id,
            error=RuntimeError("experiment failure"),
        )
    else:
        callback._checkpoint_budget_exhausted(
            condition=condition,
            task_id=task_id,
        )

    assert not adapter_root.exists()


def test_formal_adapter_cleanup_rejects_non_exact_target(
    tmp_path: Path,
) -> None:
    plan_root = tmp_path / EXPERIMENT_ID
    outside = tmp_path / "outside" / "case-1"
    outside.mkdir(parents=True)
    marker = outside / "preserve.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="exact adapter case root"):
        formal_runner._verified_adapter_case_root(
            plan_root=plan_root,
            condition_id="condition-1",
            task_id="case-1",
            adapter_root=outside,
        )

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_resume_cleanup_deletes_only_checkpointed_adapter_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    checkpointed_case, uncheckpointed_case = selection.ordered_case_ids

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
            *plan.bound_items(),
            selected_case_ids_by_condition={
                condition.condition_id: (checkpointed_case,)
            },
        ),
        "root_case_filter": {
            condition.condition_id: (checkpointed_case,),
        },
    }
    formal_runner.execute_paper_formal_suite(**kwargs)
    plan_root = Path(plan.output_root)
    checkpointed_root = (
        plan_root / "runs" / condition.condition_id / checkpointed_case
    )
    uncheckpointed_root = (
        plan_root / "runs" / condition.condition_id / uncheckpointed_case
    )
    for working_root in (checkpointed_root, uncheckpointed_root):
        working_root.mkdir(parents=True, exist_ok=True)
        (working_root / "working.txt").write_text("working", encoding="utf-8")
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    order: list[str] = []
    original_load = formal_runner.FormalEvidenceStore.load.__func__
    original_cleanup = formal_runner._cleanup_checkpointed_adapter_trees

    def traced_load(cls, **load_kwargs):
        order.append("load")
        return original_load(cls, **load_kwargs)

    def traced_cleanup(**cleanup_kwargs):
        assert order == ["load"]
        order.append("cleanup")
        return original_cleanup(**cleanup_kwargs)

    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "load",
        classmethod(traced_load),
    )
    monkeypatch.setattr(
        formal_runner,
        "_cleanup_checkpointed_adapter_trees",
        traced_cleanup,
    )
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "archive_uncheckpointed_adapter_runs",
        lambda self: pytest.fail("resume must not archive before full load"),
    )

    formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert order == ["load", "cleanup"]
    assert not checkpointed_root.exists()
    assert (uncheckpointed_root / "working.txt").is_file()


def test_resume_cleanup_uses_full_load_terminal_keys_without_per_root_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    task_ids = tuple(f"case-{index:03d}" for index in range(500))
    selection = replace(
        selection,
        ordered_case_ids=task_ids,
        expected_ai_unit_count=len(task_ids),
    )
    plan = replace(
        plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                condition,
                selection,
            ),
        ),
    )
    plan_root = Path(plan.output_root)
    for task_id in task_ids:
        working_root = plan_root / "runs" / condition.condition_id / task_id
        working_root.mkdir(parents=True)
        (working_root / "working.txt").write_text("working", encoding="utf-8")
    terminal_ids = set(task_ids[:250])
    terminal_keys = {
        formal_runner._formal_task_key(condition, task_id)
        for task_id in terminal_ids
    }
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "_validate_run",
        lambda *_args, **_kwargs: pytest.fail(
            "batch cleanup must consume the full-load terminal key set"
        ),
    )

    removed = formal_runner._cleanup_checkpointed_adapter_trees(
        suite_root=tmp_path,
        bound_plans=((plan, plan.bound_items()),),
        normalized_root_filter={},
        terminal_task_keys=terminal_keys,
    )

    assert removed == 250
    assert all(
        not (
            plan_root / "runs" / condition.condition_id / task_id
        ).exists()
        for task_id in terminal_ids
    )
    assert all(
        (
            plan_root / "runs" / condition.condition_id / task_id
        ).is_dir()
        for task_id in task_ids[250:]
    )


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    (
        ("status", "failed"),
        ("condition_count", 7),
        ("run_count", 7),
        ("task_count", 7),
        ("provider_attempt_count", 7),
        ("total_tokens", 777),
        ("total_cost_estimate", 777.0),
        ("paper_eligible", True),
        (
            "error_summary",
            [
                {
                    "failure_stage": "tampered_summary",
                    "failure_kind": "not_canonical_evidence",
                }
            ],
        ),
    ),
)
def test_formal_replay_report_rejects_runner_summary_drift_after_manifest_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    mutated_value: object,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    result_path = tmp_path / "formal_runner_result.json"
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    persisted[field_name] = mutated_value
    result_path.write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: pytest.fail("replay must not call provider dispatch"),
    )

    with pytest.raises(
        ValueError,
        match="formal runner result does not match independently recomputed evidence",
    ):
        formal_runner.write_paper_formal_replay_report(output_root=tmp_path)


def test_formal_runner_rejects_completed_protocol_result_without_real_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def missing_evidence_dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )
        result.attempt_results = []
        result.event_records = []
        result.task_result.event_refs = []
        return result

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        missing_evidence_dispatch,
    )

    suite = formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    assert suite.status == PaperStatus.BLOCKED
    task = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_task_results.jsonl",
    )[0]
    assert task["outcome_status"] == "blocked_dependency"
    assert task["evidence_integrity"] == "invalid"
    assert json.loads(
        (tmp_path / "formal_runner_result.json").read_text(encoding="utf-8")
    )["status"] == "blocked"

def test_formal_runner_dispatches_planned_conditions_with_isolated_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    calls: list[tuple[object, object, str]] = []

    def capture_dispatch(*, context, plan, condition_id):
        calls.append((context, plan, condition_id))
        return PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=1,
            metrics_ref=None,
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", capture_dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(
            catalog_digest=CATALOG_DIGEST,
            factorization_cases=(
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            lean_cases=(),
            lean_lemma_graph_cases=(),
        ),
        budget=_with_ai_unit_commitments(
            _budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
            *plan.bound_items(),
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    assert [(item[1], item[2]) for item in calls] == [(plan, "condition-1")]
    assert Path(calls[0][0].output_root) == tmp_path / EXPERIMENT_ID
    assert (tmp_path / EXPERIMENT_ID).is_dir()
    assert suite.status == PaperStatus.COMPLETED
    assert suite.condition_count == len(plan.bound_items())
    assert suite.paper_eligible is False
    assert suite.provider_attempt_count == 1


def test_formal_runner_accepts_frozen_supporting_baseline_budget_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    budget = _with_ai_unit_commitments(
        _budget(
            planned_conditions=1,
            planned_root_runs=2,
            planned_ai_units=3,
        ),
        *plan.bound_items(),
    )
    quota = json.loads(json.dumps(budget.quota_preflight))
    quota["budget_commitments"]["experiment_budget_identity"] = {
        "schema_version": "tokenshare.paper_experiment_budget_identity.v1",
        "headline_root_runs_by_experiment": {EXPERIMENT_ID: 1},
        "supporting_baseline_root_runs_by_experiment": {EXPERIMENT_ID: 1},
        "actual_scheduled_root_runs_by_experiment": {EXPERIMENT_ID: 2},
        "headline_ai_units_by_experiment": {EXPERIMENT_ID: 1},
        "supporting_baseline_ai_units_by_experiment": {EXPERIMENT_ID: 2},
        "planned_first_attempt_ai_units_by_experiment": {EXPERIMENT_ID: 3},
    }
    budget = replace(budget, quota_preflight=quota)
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        ),
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(
            catalog_digest=CATALOG_DIGEST,
            factorization_cases=(
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            lean_cases=(),
            lean_lemma_graph_cases=(),
        ),
        budget=budget,
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    assert suite.status == PaperStatus.COMPLETED
    persisted_budget = json.loads(
        (tmp_path / "run_budget.json").read_text(encoding="utf-8")
    )
    assert persisted_budget["planned_root_runs"] == 2
    assert persisted_budget["planned_ai_units"] == 3


def test_completed_experiment_is_committed_before_later_experiment_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    exp1_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-exp1-commit",
    )
    exp2_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=formal_runner.EXP2_EXPERIMENT_ID,
        condition_id="condition-exp2-crash",
    )
    committed_result = PaperConditionResult(
        condition_id="condition-exp1-commit",
        status=PaperStatus.COMPLETED,
        repeat_count=1,
        task_count=1,
        completed_root_count=1,
        failed_root_count=0,
        blocked_root_count=0,
        provider_attempt_count=1,
        metrics_ref={
            "paper_eligible": True,
            "evidence_refs": [{"path": "committed-exp1"}],
        },
    )

    def interrupted_dispatch(*, context, plan, condition_id):
        del context
        if plan.experiment_id == formal_runner.EXP2_EXPERIMENT_ID:
            raise RuntimeError("later experiment crashed")
        assert condition_id == committed_result.condition_id
        return committed_result

    def hard_stop_before_suite_finalizer(**_kwargs):
        raise RuntimeError("process stopped before suite finalizer")

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        interrupted_dispatch,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_before_suite_finalizer,
    )
    with pytest.raises(RuntimeError, match="process stopped"):
        formal_runner.execute_paper_formal_suite(
            dispatch_plans=(exp1_plan, exp2_plan),
            catalog_manifest={
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    {
                        "case_id": "case-1",
                        "difficulty": "easy",
                        "paper_difficulty": "easy",
                        "expected_ai_unit_count": 1,
                    },
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            budget=_with_ai_unit_commitments(
                _budget(
                    planned_experiments=(
                        EXPERIMENT_ID,
                        formal_runner.EXP2_EXPERIMENT_ID,
                    ),
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *exp1_plan.bound_items(),
                *exp2_plan.bound_items(),
            ),
            budget_approval={
                "approval_mode": "user_bypassed",
                "budget_digest": BUDGET_DIGEST,
            },
            output_root=tmp_path,
            ai_api_configs={PROVIDER_CONFIG_ID: config},
            transport=object(),
            real_transport=False,
            hard_limits={},
        )

    exp1_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert exp1_manifest["status"] == "completed"
    rows = [
        json.loads(line)
        for line in (tmp_path / "condition_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert {
        (row["experiment_id"], row["condition_id"], row["repeat_id"])
        for row in rows
    } == {(EXPERIMENT_ID, "condition-exp1-commit", 0)}
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["status"] == "running"
    assert suite_manifest["paper_eligible"] is False
    rows_path = tmp_path / "condition_results.jsonl"
    manifest_path = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "experiment_manifest.json"
    )
    before = (rows_path.read_bytes(), manifest_path.read_bytes())

    formal_runner._finalize_formal_experiment(
        suite_root=tmp_path,
        plan=exp1_plan,
        condition_results=(committed_result,),
        execution_classification=None,
    )

    assert (rows_path.read_bytes(), manifest_path.read_bytes()) == before


def test_resume_rebuilds_missing_experiment_and_suite_closure_from_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_finalizer = formal_runner._finalize_formal_experiment

    def hard_stop_after_checkpoint(**_kwargs):
        raise RuntimeError("process stopped after canonical checkpoint")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        hard_stop_after_checkpoint,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_after_checkpoint,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="canonical checkpoint"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "formal_runner_result.json").exists()
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        original_finalizer,
    )

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    experiment_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert experiment_manifest["status"] == "completed"
    assert (tmp_path / "formal_runner_result.json").is_file()


@pytest.mark.parametrize(
    "crash_stage",
    (
        "condition_results_written",
        "experiment_manifest_written",
        "inventory_refreshed",
    ),
)
def test_resume_repairs_experiment_finalization_intent_at_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_hook(*, stage, suite_root):
        del suite_root
        if stage == crash_stage:
            raise RuntimeError(f"crash at {stage}")

    def preserve_crash_seam(**_kwargs):
        raise RuntimeError("process stopped before blocked-suite closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", crash_hook)
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        preserve_crash_seam,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="blocked-suite closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert (tmp_path / "PENDING.json").is_file()
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "PENDING.json").exists()
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()


@pytest.mark.parametrize(
    "crash_stage",
    (
        "formal_runner_result_written",
        "suite_manifest_written",
        "suite_inventory_refreshed",
    ),
)
def test_resume_repairs_suite_finalization_intent_at_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_hook(*, stage, suite_root):
        del suite_root
        if stage == crash_stage:
            raise RuntimeError(f"crash at {stage}")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", crash_hook)
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match=crash_stage):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    assert (tmp_path / "PENDING.json").is_file()
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]
    assert not (tmp_path / "PENDING.json").exists()
    formal_runner.FormalEvidenceStore(tmp_path)._validate_evidence_manifest()


def test_resume_rebuilds_failed_terminal_root_without_provider_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def rejected_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": SimpleNamespace(
                    **{
                        **vars(result.task_result),
                        "root_status": PaperTaskStatus.FAILED,
                    }
                ),
                "attempt_results": [
                    replace(
                        result.attempt_results[0],
                        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
                        error_kind="verifier_rejected",
                    )
                ],
            }
        )

    original_hook = formal_runner._formal_finalization_hook

    def crash_after_failed_condition_row(*, stage, suite_root):
        del suite_root
        if stage == "condition_results_written":
            raise RuntimeError("crash after failed terminal checkpoint")

    def preserve_crash_seam(**_kwargs):
        raise RuntimeError("process stopped before failure closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        rejected_case_dispatch,
    )
    monkeypatch.setattr(
        formal_runner,
        "_formal_finalization_hook",
        crash_after_failed_condition_row,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        preserve_crash_seam,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    with pytest.raises(RuntimeError, match="failure closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    monkeypatch.setattr(formal_runner, "_formal_finalization_hook", original_hook)

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.125)
    assert adapter_calls == ["case-1"]


def test_formal_runner_materializes_nested_windows_ads_artifact_name(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    nested_artifacts = adapter_root / "case-1" / "artifacts"
    nested_artifacts.mkdir(parents=True)
    artifact_name = "merge_input_bundle:merge_plan_1"
    source = nested_artifacts / artifact_name
    source.write_bytes(b"nested-merge-input")

    refs = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=(
            {
                "uri": f"artifacts/{artifact_name}",
                "content_hash": formal_runner._sha256_bytes(
                    b"nested-merge-input"
                ),
            },
        ),
    )

    assert len(refs) == 1
    copied = tmp_path / "suite" / refs[0]["path"]
    assert copied.read_bytes() == b"nested-merge-input"


def test_formal_runner_materializer_rejects_truncated_source_bytes(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "raw-output.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"truncated":')

    with pytest.raises(ValueError, match="hash mismatched"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=adapter_root,
            source_refs=(
                {
                    "artifact_id": "raw-output.json",
                    "uri": "artifacts/raw-output.json",
                    "content_hash": formal_runner._sha256_bytes(
                        b'{"complete":true}'
                    ),
                },
            ),
        )


def test_adapter_artifact_refs_discovers_event_only_nested_ref() -> None:
    event_only_ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": "event-only",
        "artifact_type": "EventOnlyEvidence",
        "uri": "artifacts/event-only.json",
        "content_hash": "sha256:" + "a" * 64,
        "size_bytes": 2,
        "media_type": "application/json",
        "artifact_schema_id": "test.event_only",
        "artifact_schema_version": "v1",
        "source": {},
        "metadata": {},
        "created_at": "2026-07-20T00:00:00Z",
    }

    refs = formal_runner._adapter_artifact_refs(
        {},
        (),
        (),
        events=({"payload": {"deeply_nested": [event_only_ref]}},),
    )

    assert refs == (event_only_ref,)


def test_materialize_artifacts_recursively_closes_metadata_and_json_payload_refs(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"

    def write_ref(
        relative_path: str,
        *,
        artifact_id: str,
        body: bytes,
        source: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        path = adapter_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return {
            "schema_version": "ArtifactRef.v1",
            "artifact_id": artifact_id,
            "artifact_type": "RecursiveEvidence",
            "uri": relative_path,
            "content_hash": formal_runner._sha256_bytes(body),
            "size_bytes": len(body),
            "media_type": "application/json",
            "artifact_schema_id": "test.recursive_evidence",
            "artifact_schema_version": "v1",
            "source": source or {},
            "metadata": metadata or {},
            "created_at": "2026-07-20T00:00:00Z",
        }

    metadata_ref = write_ref(
        "one/artifacts/result.json",
        artifact_id="metadata-child",
        body=b'{"source":"metadata"}',
    )
    payload_ref = write_ref(
        "two/artifacts/result.json",
        artifact_id="payload-child",
        body=b'{"source":"payload"}',
    )
    root_body = json.dumps(
        {"payload_ref": payload_ref},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    root_ref = write_ref(
        "root/artifacts/root.json",
        artifact_id="root",
        body=root_body,
        metadata={"metadata_ref": metadata_ref},
    )

    refs = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=(root_ref,),
    )

    assert {ref["artifact_id"] for ref in refs} == {
        "root",
        "metadata-child",
        "payload-child",
    }
    assert len({ref["path"] for ref in refs}) == 3
    child_paths = {
        Path(ref["path"]).name
        for ref in refs
        if ref["artifact_id"] in {"metadata-child", "payload-child"}
    }
    assert len(child_paths) == 2
    for ref in refs:
        assert ref["source_artifact_ref"]["artifact_id"] == ref["artifact_id"]
        assert ref["source_uri"] == ref["source_artifact_ref"]["uri"]
        copied = tmp_path / "suite" / ref["path"]
        assert copied.read_bytes()


def test_materialize_artifacts_rejects_size_mismatch(tmp_path: Path) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "size.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"{}")

    with pytest.raises(ValueError, match="size mismatched"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=adapter_root,
            source_refs=(
                {
                    "schema_version": "ArtifactRef.v1",
                    "artifact_id": "size",
                    "artifact_type": "SizeEvidence",
                    "uri": "artifacts/size.json",
                    "content_hash": formal_runner._sha256_bytes(b"{}"),
                    "size_bytes": 3,
                    "media_type": "application/json",
                    "artifact_schema_id": "test.size_evidence",
                    "artifact_schema_version": "v1",
                    "source": {},
                    "metadata": {},
                    "created_at": "2026-07-20T00:00:00Z",
                },
            ),
        )


def test_materialize_artifacts_rejects_complete_ref_missing_size(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=tmp_path / "adapter",
            source_refs=(
                {
                    "schema_version": "ArtifactRef.v1",
                    "artifact_id": "missing-size",
                    "artifact_type": "MalformedEvidence",
                    "uri": "artifacts/missing-size.json",
                    "content_hash": "sha256:" + "a" * 64,
                    "media_type": "application/json",
                    "artifact_schema_id": "test.malformed_evidence",
                    "artifact_schema_version": "v1",
                    "source": {},
                    "metadata": {},
                    "created_at": "2026-07-20T00:00:00Z",
                },
            ),
        )


def test_materialize_artifacts_rejects_invalid_declared_json(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    source = adapter_root / "artifacts" / "bad.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{"truncated":')
    source_ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": "bad-json",
        "artifact_type": "MalformedJsonEvidence",
        "uri": "artifacts/bad.json",
        "content_hash": formal_runner._sha256_bytes(source.read_bytes()),
        "size_bytes": len(source.read_bytes()),
        "media_type": "application/json",
        "artifact_schema_id": "test.malformed_json_evidence",
        "artifact_schema_version": "v1",
        "source": {},
        "metadata": {},
        "created_at": "2026-07-20T00:00:00Z",
    }

    with pytest.raises(ValueError, match="declared JSON"):
        formal_runner._materialize_artifacts(
            suite_root=tmp_path / "suite",
            condition=SimpleNamespace(
                experiment_id=EXPERIMENT_ID,
                condition_id="condition-1",
                repeat_id=0,
            ),
            task_id="case-1",
            adapter_root=adapter_root,
            source_refs=(source_ref,),
        )


def test_materialize_artifacts_deduplicates_same_identity_uri_alias(
    tmp_path: Path,
) -> None:
    adapter_root = tmp_path / "adapter"
    body = b'{"same":true}'
    refs = []
    for directory in ("one", "two"):
        relative_path = f"{directory}/artifacts/alias.json"
        source = adapter_root / relative_path
        source.parent.mkdir(parents=True)
        source.write_bytes(body)
        refs.append(
            {
                "schema_version": "ArtifactRef.v1",
                "artifact_id": "stable-identity",
                "artifact_type": "AliasEvidence",
                "uri": relative_path,
                "content_hash": formal_runner._sha256_bytes(body),
                "size_bytes": len(body),
                "media_type": "application/json",
                "artifact_schema_id": "test.alias_evidence",
                "artifact_schema_version": "v1",
                "source": {},
                "metadata": {},
                "created_at": "2026-07-20T00:00:00Z",
            }
        )

    materialized = formal_runner._materialize_artifacts(
        suite_root=tmp_path / "suite",
        condition=SimpleNamespace(
            experiment_id=EXPERIMENT_ID,
            condition_id="condition-1",
            repeat_id=0,
        ),
        task_id="case-1",
        adapter_root=adapter_root,
        source_refs=refs,
    )

    assert len(materialized) == 1
    assert materialized[0]["artifact_id"] == "stable-identity"


def test_formal_checkpoint_materializes_nested_no_return_artifact_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition = plan.conditions[0]
    expected_ids = {
        "no-return-request",
        "no-return-raw",
        "no-return-provenance",
        "no-return-usage",
        "no-return-model-record",
        "no-return-primitive-fault",
        "no-return-runtime-fault",
    }

    class CheckpointModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen dispatch plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def nested_no_return_dispatch(**kwargs):
        adapter_root = Path(kwargs["output_root"])
        case_id = kwargs["case"]["case_id"]
        base = _complete_adapter_result(
            output_root=adapter_root,
            condition=kwargs["condition"],
            case_id=case_id,
        )
        store = ArtifactStore(adapter_root / case_id / "nested" / "runtime")
        created_at = "2026-07-20T00:00:00Z"

        def save_json(artifact_id: str, artifact_type: str, body: dict[str, object]):
            assert artifact_id in expected_ids
            return store.save_json(
                body,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                artifact_schema_id=f"test.{artifact_type.lower()}",
                artifact_schema_version="v1",
                source={"test": "formal-no-return-materialization"},
                metadata={},
                created_at=created_at,
            )

        request_ref = save_json(
            "no-return-request",
            "ExecutionRequest",
            {"request_id": "request-case-1", "attempt_id": "attempt-case-1"},
        )
        raw_ref = save_json(
            "no-return-raw",
            "RawModelOutput",
            {"content_text": "{}"},
        )
        provenance_ref = save_json(
            "no-return-provenance",
            "AIProviderResponseProvenance",
            {
                "request_id": "request-case-1",
                "raw_output_ref": raw_ref.to_dict(),
            },
        )
        usage_ref = save_json(
            "no-return-usage",
            "AIUsageSummary",
            {"request_id": "request-case-1", "total_tokens": 11},
        )
        model_ref = save_json(
            "no-return-model-record",
            "PaperModelExecutionRecord",
            {
                "attempt_id": "attempt-case-1",
                "request_ref": request_ref.to_dict(),
                "raw_output_ref": raw_ref.to_dict(),
                "provenance_ref": provenance_ref.to_dict(),
                "usage_ref": usage_ref.to_dict(),
            },
        )
        primitive_fault_ref = save_json(
            "no-return-primitive-fault",
            "FaultInjectionRecord",
            {
                "attempt_id": "attempt-case-1",
                "fault_type": "no_return",
            },
        )
        runtime_fault_ref = save_json(
            "no-return-runtime-fault",
            "RuntimeFaultInjectionRecord",
            {
                "attempt_id": "attempt-case-1",
                "fault_type": "no_return",
                "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
                "original_raw_output_ref": raw_ref.to_dict(),
                "original_provenance_ref": provenance_ref.to_dict(),
                "pre_fault_usage_ref": usage_ref.to_dict(),
            },
        )
        attempt = SimpleNamespace(
            **{
                **vars(base.attempt_results[0]),
                "attempt_status": PaperAttemptStatus.LEASE_EXPIRED,
                "request_ref": request_ref.to_dict(),
                "raw_output_ref": raw_ref.to_dict(),
                "provenance_ref": provenance_ref.to_dict(),
                "usage_ref": usage_ref.to_dict(),
                "model_execution_record_ref": model_ref.to_dict(),
                "fault_injection_ref": primitive_fault_ref.to_dict(),
                "error_kind": "lease_expired",
                "paper_eligible": True,
            }
        )
        task = SimpleNamespace(
            **{
                **vars(base.task_result),
                "artifact_refs": [
                    request_ref.to_dict(),
                    runtime_fault_ref.to_dict(),
                ],
                "paper_eligible": True,
            }
        )
        runtime_fault = {
            "fault_injection_id": "no-return-runtime-fault",
            "record_ref": runtime_fault_ref.to_dict(),
            "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
            "original_raw_output_ref": raw_ref.to_dict(),
            "original_provenance_ref": provenance_ref.to_dict(),
            "pre_fault_usage_ref": usage_ref.to_dict(),
        }
        return SimpleNamespace(
            **{
                **vars(base),
                "task_result": task,
                "attempt_results": [attempt],
                "fault_records": [runtime_fault],
            }
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CheckpointModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        nested_no_return_dispatch,
    )

    formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    )

    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    generation_root = run_root / ".generations" / current["generation_id"]
    artifact_index = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "artifacts/artifact_index.jsonl",
    )
    assert {
        str(ref["artifact_id"]) for ref in artifact_index
    } == expected_ids
    for ref in artifact_index:
        payload = tmp_path / str(ref["path"])
        assert payload.is_file()
        assert formal_runner._sha256_bytes(payload.read_bytes()) == ref["content_hash"]

    attempts = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert (
        generation_root / "artifacts" / "artifact_index.jsonl"
    ).is_file()
    for field_name in (
        "request_ref",
        "raw_output_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
        "fault_injection_ref",
    ):
        assert formal_runner._persisted_artifact_ref_resolves(
            attempts[0][field_name],
            artifact_index,
        )


def test_formal_runner_builds_endpoint_context_through_registered_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    observed: dict[str, object] = {}
    adapter_calls: list[dict[str, object]] = []

    class ContextCheckingModule:
        def expand_conditions(self, context):
            raise AssertionError("execution must consume the frozen dispatch plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            observed["approved_endpoint_binding"] = dict(
                context.approved_endpoint_binding
            )
            observed["request_limits"] = dict(context.request_limits)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def capture_case_dispatch(**kwargs):
        adapter_calls.append(kwargs)
        return _budget_fitting_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, ContextCheckingModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        capture_case_dispatch,
        raising=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest=SimpleNamespace(
            catalog_digest=CATALOG_DIGEST,
            factorization_cases=(
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                },
            ),
            lean_cases=(),
            lean_lemma_graph_cases=(),
        ),
        budget=_with_ai_unit_commitments(
            _budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
            *plan.bound_items(),
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={"stop_after_current_task": True},
    )

    request_controls = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    assert observed["approved_endpoint_binding"] == {
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "model_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    assert observed["request_limits"] == request_controls
    assert len(adapter_calls) == 1
    assert adapter_calls[0]["ai_api_config"] is config
    assert adapter_calls[0]["entry_id"] == MODEL_ENTRY_ID
    assert adapter_calls[0]["max_tokens"] == 1024
    assert adapter_calls[0]["timeout_seconds"] == 30
    assert suite.status == PaperStatus.COMPLETED


def test_formal_runner_accepts_mapping_catalog_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: PaperConditionResult(
            condition_id=condition_id,
            status=PaperStatus.COMPLETED,
            repeat_count=1,
            task_count=1,
            completed_root_count=1,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        ),
    )

    result = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(plan,),
        catalog_manifest={
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        budget=_with_ai_unit_commitments(
            _budget(planned_conditions=1, planned_root_runs=1, planned_ai_units=1),
            *plan.bound_items(),
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        transport=object(),
        real_transport=False,
        hard_limits={},
    )

    assert result.status == PaperStatus.COMPLETED


@pytest.mark.parametrize(
    "drift",
    (
        "preflight_cohort_digest",
        "member_cohort_digest",
        "member_request_controls",
    ),
)
def test_exp5_endpoint_contract_rejects_cohort_or_control_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    config = _ai_config()
    base_condition = _planned_dispatch_plan(
        tmp_path,
        config=config,
    ).conditions[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    approved_binding = {
        "status": "planned",
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "member_plans": {COHORT_MEMBER_ID: member_plan},
        "request_controls_snapshot": request_controls["comparable"],
        "request_controls_snapshot_digest": request_controls[
            "comparable_digest"
        ],
    }
    if drift == "preflight_cohort_digest":
        approved_binding["model_cohort_digest"] = "sha256:" + "9" * 64
    elif drift == "member_cohort_digest":
        member_plan["model_cohort_digest"] = "sha256:" + "9" * 64
    else:
        member_plan["request_controls"]["comparable"]["max_tokens"] = 2048

    with pytest.raises(ValueError, match="Exp5"):
        formal_runner._condition_endpoint_contract(
            experiment_id=EXP5_EXPERIMENT_ID,
            condition=condition,
            ai_api_configs={
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: approved_binding,
                },
            },
        )


def test_exp5_endpoint_contract_accepts_preregistered_thinking_budget(
    tmp_path: Path,
) -> None:
    base_config = _ai_config()
    thinking_entry = replace(
        base_config.entries[0],
        request_overrides={
            **dict(base_config.entries[0].request_overrides),
            "enable_thinking": True,
            "thinking_budget": 32_768,
        },
    )
    config = replace(base_config, entries=[thinking_entry])
    base_condition = _planned_dispatch_plan(
        tmp_path,
        config=config,
    ).conditions[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
        reasoning_profile_id="thinking",
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "thinking",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    approved_binding = {
        "status": "planned",
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "member_plans": {COHORT_MEMBER_ID: member_plan},
        "request_controls_snapshot": request_controls["comparable"],
        "request_controls_snapshot_digest": request_controls[
            "comparable_digest"
        ],
    }

    _binding, request_limits, _config = (
        formal_runner._condition_endpoint_contract(
            experiment_id=EXP5_EXPERIMENT_ID,
            condition=condition,
            ai_api_configs={
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: approved_binding,
                },
            },
        )
    )

    assert request_limits["enable_thinking"] is True
    assert request_limits["thinking_budget"] == 32_768


def test_formal_runner_blocked_plan_never_dispatches_or_calls_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    def forbidden_dispatch(**_kwargs):
        raise AssertionError("blocked formal plan must not dispatch a condition")

    def transport(*_args, **_kwargs):
        transport_calls.append(object())
        raise AssertionError("blocked formal plan must not call transport")

    monkeypatch.setattr(formal_runner, "dispatch_paper_condition", forbidden_dispatch)
    blocked_plan = _dispatch_plan_type()(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        output_root=(tmp_path / "exp5_real_ai_model_endpoint_comparison").as_posix(),
        conditions=(),
        condition_selection_bindings=(),
        status="blocked",
        blocked_reason="incomplete endpoint cohort",
        paper_eligible_possible=False,
    )

    suite = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(blocked_plan,),
        catalog_manifest=SimpleNamespace(catalog_digest=CATALOG_DIGEST),
        budget=_with_ai_unit_commitments(
            _budget(
                planned_experiments=(),
                planned_conditions=0,
                planned_root_runs=0,
                planned_ai_units=0,
            ),
        ),
        budget_approval={
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        output_root=tmp_path,
        ai_api_configs={},
        transport=transport,
        real_transport=False,
        hard_limits={},
    )

    assert transport_calls == []
    assert suite.status == PaperStatus.BLOCKED
    assert suite.provider_attempt_count == 0
    assert suite.paper_eligible is False


def test_formal_runner_hard_limit_blocks_second_root_and_resume_keeps_first_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _budget_fitting_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            *plan.bound_items(),
        ),
        "hard_limits": {"max_total_provider_attempts": 1},
    }

    exhausted = formal_runner.execute_paper_formal_suite(**kwargs)
    assert exhausted.status == PaperStatus.BUDGET_EXHAUSTED
    assert len(exhausted.condition_results) == 1
    assert exhausted.condition_results[0]["condition_id"] == (
        plan.bound_items()[0][0].condition_id
    )
    assert adapter_calls == ["case-1"]

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)
    assert resumed.status == PaperStatus.BUDGET_EXHAUSTED
    assert resumed.condition_results == exhausted.condition_results
    assert adapter_calls == ["case-1"]


def _two_not_started_direct_rows(tmp_path: Path):
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    catalog = {
        "catalog_digest": CATALOG_DIGEST,
        "factorization_cases": (
            {
                "case_id": "case-1",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "expected_ai_unit_count": 1,
            },
            {
                "case_id": "case-2",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "expected_ai_unit_count": 1,
            },
        ),
        "lean_cases": (),
        "lean_lemma_graph_cases": (),
    }
    collector = formal_runner._build_canonical_direct_collector(
        bound_plans=((plan, plan.bound_items()),),
        catalog_manifest=catalog,
        normalized_root_filter={},
        evidence_class="online_real_provider",
    )
    return formal_runner.project_paper_direct_results(
        root_inventory_manifest=collector.inventory,
        condition_manifests=collector.condition_manifests,
        catalog_manifests=collector.catalog_manifests,
        canonical_runtime_evidence=(),
    ).rows


@pytest.mark.parametrize("invalid_kind", ("empty", "duck", "subclass", "failed", "mixed"))
def test_empty_runtime_requires_exact_all_not_started_direct_rows(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    import copy

    from tokenshare.experiments.paper_direct_results import PaperDirectRootResult
    from tokenshare.experiments.paper_traceability import TraceabilityBlockedError

    first, second = _two_not_started_direct_rows(tmp_path / "inventory")
    if invalid_kind == "empty":
        direct = {"probe": ()}
    elif invalid_kind == "duck":
        duck = SimpleNamespace(**second.to_dict())
        direct = {"probe": (first, duck)}
    elif invalid_kind == "subclass":
        class DirectSubclass(PaperDirectRootResult):
            pass

        subclass = object.__new__(DirectSubclass)
        subclass.__dict__.update(first.__dict__)
        direct = {"probe": (subclass,)}
    elif invalid_kind == "failed":
        failed = copy.copy(first)
        object.__setattr__(failed, "root_status", "failed")
        direct = {"probe": (failed,)}
    else:
        failed = copy.copy(second)
        object.__setattr__(failed, "root_status", "failed")
        direct = {"probe": (first, failed)}

    replay_root = tmp_path / f"invalid-{invalid_kind}"
    with pytest.raises(
        TraceabilityBlockedError,
        match="requires persisted canonical runtime evidence",
    ):
        formal_runner.persist_paper_traceability_replay_input_root(
            replay_input_root=replay_root,
            canonical_direct_rows=direct,
            canonical_runtime_evidence=(),
        )
    assert not replay_root.exists()


def test_empty_runtime_all_not_started_rows_reaches_later_closure_validation(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_traceability import TraceabilityBlockedError

    rows = _two_not_started_direct_rows(tmp_path / "inventory")

    with pytest.raises(
        TraceabilityBlockedError,
        match="requires persisted current formal evidence",
    ):
        formal_runner.persist_paper_traceability_replay_input_root(
            replay_input_root=tmp_path / "missing-formal-evidence",
            canonical_direct_rows={"probe": rows},
            canonical_runtime_evidence=(),
        )


def test_zero_dispatch_hard_limit_closes_full_not_started_direct_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def unexpected_case_dispatch(**kwargs):
        adapter_calls.append(kwargs["case"]["case_id"])
        raise AssertionError("zero hard limit must block before adapter dispatch")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        unexpected_case_dispatch,
    )
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            *plan.bound_items(),
        ),
        "real_transport": True,
        "enforce_publication_closure": True,
        "hard_limits": {"max_total_provider_attempts": 0},
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status is PaperStatus.BUDGET_EXHAUSTED
    assert suite.provider_attempt_count == 0
    assert adapter_calls == []
    loaded = formal_runner.load_paper_traceability_replay_inputs(tmp_path)
    direct = loaded.direct["exp1_feasibility"]
    assert [row.direct_result.case_id for row in direct] == ["case-1", "case-2"]
    assert [row.direct_result.root_status for row in direct] == [
        "not_started",
        "not_started",
    ]
    assert loaded.current["canonical_runtime_evidence"] == ()
    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(tmp_path)
    assert metrics.observations_digest.startswith("sha256:")


def test_resume_hard_limit_rebuilds_missing_closure_without_provider_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        return _budget_fitting_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    original_finalizer = formal_runner._finalize_formal_experiment

    def hard_stop_after_condition_checkpoint(**_kwargs):
        raise RuntimeError("process stopped before hard-limit closure")

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        hard_stop_after_condition_checkpoint,
    )
    monkeypatch.setattr(
        formal_runner,
        "_close_blocked_formal_suite",
        hard_stop_after_condition_checkpoint,
    )
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            *plan.bound_items(),
        ),
        "hard_limits": {"max_total_provider_attempts": 1},
    }

    with pytest.raises(RuntimeError, match="hard-limit closure"):
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert adapter_calls == ["case-1"]
    monkeypatch.setattr(
        formal_runner,
        "_finalize_formal_experiment",
        original_finalizer,
    )

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.BUDGET_EXHAUSTED
    assert resumed.provider_attempt_count == 1
    assert resumed.total_tokens == 11
    assert resumed.total_cost_estimate == pytest.approx(0.005)
    assert adapter_calls == ["case-1"]
    experiment_manifest = json.loads(
        (
            tmp_path
            / "experiments"
            / EXPERIMENT_ID
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert experiment_manifest["status"] == "budget_exhausted"


def test_smoke_unexpected_runner_error_records_reportable_blocked_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )

    def failed_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        adapter_calls.append(case_id)
        if case_id == "case-1":
            raise RuntimeError("adapter failed")
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", failed_dispatch)

    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            *plan.bound_items(),
        ),
        "suite_id": "test_smoke_blocked",
        "execution_classification": {
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        },
        "pre_execution_documents": {
            "smoke_execution_plan.json": {
                "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
                "suite_id": "test_smoke_blocked",
                "baseline_policy": "omitted_for_smoke_regression",
                "items": [
                    {
                        "item_id": f"smoke-{case_id}",
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-1",
                        "case_id": case_id,
                        "repeat_id": 0,
                        "condition_selector": {
                            "domain": "factorization",
                            "difficulty": "easy",
                        },
                    }
                    for case_id in ("case-1", "case-2")
                ],
            }
        },
    }
    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status == PaperStatus.BLOCKED
    assert suite.provider_attempt_count == 0
    assert suite.error_summary[0]["failure_stage"] == "adapter_runtime"
    assert suite.error_summary[0]["failure_kind"] == "RuntimeError"
    run_root = tmp_path / "experiments" / EXPERIMENT_ID / "runs" / "condition-1" / "0"
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    generation_root = run_root / ".generations" / current["generation_id"]
    tasks = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_task_results.jsonl",
    )
    attempts = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_attempt_results.jsonl",
    )
    assert generation_root.is_dir()
    assert adapter_calls == ["case-1"]
    assert [task["task_id"] for task in tasks] == ["case-1", "case-2"]
    assert tasks[0]["root_status"] == "blocked"
    assert tasks[0]["outcome_status"] == "blocked_dependency"
    assert tasks[0]["evidence_integrity"] == "invalid"
    assert tasks[0]["failure_stage"] == "adapter_runtime"
    assert tasks[0]["failure_kind"] == "RuntimeError"
    assert tasks[1]["root_status"] == "not_started"
    assert tasks[1]["outcome_status"] == "blocked_dependency"
    assert tasks[1]["evidence_integrity"] == "invalid"
    for task in tasks:
        assert task["formal"] is False
        assert task["pilot_only"] is True
        assert task["regression_only"] is True
        assert task["paper_eligible"] is False
        assert {"smoke_suite", "pilot_only"}.issubset(
            task["ineligibility_reasons"]
        )
    assert [attempt["attempt_status"] for attempt in attempts] == [
        "blocked_dependency",
        "not_started",
    ]
    for attempt in attempts:
        assert attempt["formal"] is False
        assert attempt["pilot_only"] is True
        assert attempt["regression_only"] is True
        assert attempt["paper_eligible"] is False
    assert (run_root / "artifacts" / "case-1" / "dependency-blocked.json").is_file()
    assert (run_root / "artifacts" / "case-2" / "not-started.json").is_file()
    report = generate_paper_smoke_report(output_root=tmp_path)
    assert [row["root_status"] for row in report["rows"]] == [
        "blocked",
        "not_started",
    ]


def test_formal_runner_exp2_delegates_worker_count_to_protocol_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-workers-2",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    lock = Lock()
    active = 0
    observed_max = 0

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        nonlocal active, observed_max
        with lock:
            active += 1
            observed_max = max(observed_max, active)
        sleep(0.05)
        with lock:
            active -= 1
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_experiments=(experiment_id,),
                planned_conditions=1,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            *plan.bound_items(),
        ),
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status == PaperStatus.COMPLETED
    assert observed_max == 1
    condition_result = json.loads(
        (tmp_path / "condition_results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert condition_result["metrics_ref"]["worker_count"] == 2
    run_root = (
        tmp_path
        / "experiments"
        / experiment_id
        / "runs"
        / condition.condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    event_log = (
        run_root
        / ".generations"
        / current["generation_id"]
        / "events"
        / "event_log.jsonl"
    ).read_text(encoding="utf-8")
    assert event_log.count('"event_type":"TASK_COMPLETED"') == 2
    assert "AI_UNIT_STARTED" not in event_log
    assert "MERGE_GATE_COMPLETED" not in event_log


def test_formal_runner_exp2_reserves_hard_limit_before_parallel_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp2_real_ai_scalability"
    config = _ai_config()
    base_plan = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base_plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp2-hard-limit",
        worker_count=2,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    plan = replace(
        base_plan,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    adapter_calls: list[str] = []
    call_lock = Lock()

    class Exp2CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        with call_lock:
            adapter_calls.append(kwargs["case"]["case_id"])
        sleep(0.05)
        return _budget_fitting_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp2CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    {
                        "case_id": "case-1",
                        "difficulty": "easy",
                        "paper_difficulty": "easy",
                        "expected_ai_unit_count": 1,
                    },
                    {
                        "case_id": "case-2",
                        "difficulty": "easy",
                        "paper_difficulty": "easy",
                        "expected_ai_unit_count": 1,
                    },
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *plan.bound_items(),
            ),
            "hard_limits": {"max_total_provider_attempts": 1},
        }
    )

    assert adapter_calls == ["case-1"]
    assert suite.provider_attempt_count == 1
    assert suite.status == PaperStatus.BUDGET_EXHAUSTED


def test_formal_runner_does_not_deduplicate_same_root_across_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    first_condition, first_selection = base.bound_items()[0]
    second_condition = replace(
        first_condition,
        condition_id="condition-2",
        repeat_id=1,
        seed=2,
    )
    second_selection = replace(first_selection, selection_id="selection-2")
    plan = replace(
        base,
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    calls: list[tuple[str, str]] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        calls.append((kwargs["condition"].condition_id, kwargs["case"]["case_id"]))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert calls == [("condition-1", "case-1"), ("condition-2", "case-1")]


def test_formal_runner_exp3_maps_planned_target_to_protocol_unit_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp3-rate-fault",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    adapter_calls: list[tuple[str | None, bool]] = []

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest={
                        "fault_type": "false_positive",
                        "selected_target_ai_unit_ids": ["case-1:range_0"],
                    },
                    worker_death_manifest=None,
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def fake_case_dispatch(**kwargs):
        selected_unit_id = kwargs.get("selected_ai_unit_id")
        adapter_calls.append(
            (selected_unit_id, callable(kwargs.get("post_raw_output_hook")))
        )
        original = _faultable_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            attempt_suffix="original",
        )
        original_attempt = original.attempt_results[0]
        adapter_store = ArtifactStore(Path(original.output_root))
        raw_directive = kwargs["post_raw_output_hook"](
            request=SimpleNamespace(
                task_id=original_attempt.task_id,
                unit_id=original_attempt.unit_id,
                attempt_id=original_attempt.attempt_id,
                allocation_decision={"worker_id": original_attempt.worker_id},
                soft_hints={"planned_ai_unit_id": "range_0"},
            ),
            artifact_store=adapter_store,
            submitted_at=original_attempt.ended_at,
            usage_summary={
                "prompt_tokens": original_attempt.prompt_tokens,
                "completion_tokens": original_attempt.completion_tokens,
                "total_tokens": original_attempt.total_tokens,
                "cost_estimate": original_attempt.cost_estimate,
            },
            raw_output_ref=ArtifactRef.from_dict(original_attempt.raw_output_ref),
            provenance_ref=ArtifactRef.from_dict(original_attempt.provenance_ref),
            usage_ref=ArtifactRef.from_dict(original_attempt.usage_ref),
            content_text='{"result_kind":"no_factor"}',
            provider_family=original_attempt.provider,
            model=original_attempt.model,
            entry_id=original_attempt.entry_id,
        )
        assert raw_directive is None
        directive = kwargs[
            "post_raw_output_hook"
        ].after_parsed_candidate_persisted(
            ParsedCandidateContext(
                run_id=original_attempt.run_id,
                task_id=original_attempt.task_id,
                unit_id=original_attempt.unit_id,
                attempt_id=original_attempt.attempt_id,
                lease_id="lease-original",
                worker_id=original_attempt.worker_id,
                raw_output_ref=ArtifactRef.from_dict(
                    original_attempt.raw_output_ref
                ),
                original_parsed_output_ref=ArtifactRef.from_dict(
                    original_attempt.parsed_output_ref
                ),
                candidate_output_refs={
                    "result": ArtifactRef.from_dict(
                        original_attempt.parsed_output_ref
                    )
                },
                submitted_at=original_attempt.ended_at,
                experiment_unit_id="range_0",
            )
        )
        assert directive is not None
        replacement = _faultable_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            attempt_suffix="replacement",
        )
        task = SimpleNamespace(
            **{
                **vars(original.task_result),
                "provider_attempt_count": 2,
                "total_tokens": 22,
                "cost_estimate": 0.25,
            }
        )
        return SimpleNamespace(
            **{
                **vars(original),
                "task_result": task,
                "attempt_results": [
                    original_attempt,
                    replacement.attempt_results[0],
                ],
                "event_records": [
                    *original.event_records,
                    *replacement.event_records,
                ],
            }
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "execution_classification": _smoke_execution_classification(),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert adapter_calls == [(None, True)]
    attempts = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "per_attempt_results.jsonl"
    )
    faults = _generation_records(
        tmp_path, experiment_id, "condition-exp3-rate-fault", "fault_injections.jsonl"
    )
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-rate-fault",
        "events/event_log.jsonl",
    )
    assert {attempt["attempt_id"] for attempt in attempts} == {
        "attempt-case-1-original",
        "attempt-case-1-replacement",
    }
    assert len(faults) == 1
    assert faults[0]["unit_id"] == "unit-case-1"
    assert faults[0]["selected_target_ai_unit_id"] == "case-1:range_0"
    task = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp3-rate-fault",
        "per_task_results.jsonl",
    )[0]
    assert task["fault_injected"] is True
    assert task["recovered_fault_target_count"] == 1
    assert any(
        event["event_type"] == "EXPERIMENT_FAULT_OBSERVED"
        for event in events
    )


def test_formal_runner_exp3_worker_death_does_not_fabricate_process_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id=(
            "exp3_worker_death_factorization__test__dead3__p25__rep0"
        ),
        fault_type="worker_death",
        fault_rate=0.0,
    )
    termination_policies: list[WorkerTerminationPolicy | None] = []
    dispatched_condition_ids: list[str] = []

    class Exp3WorkerCallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 3,
                        "termination_count_target_by_case": {"case-1": 3},
                        "kill_progress_target_percent": 25,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": [
                                "case-1:range_0",
                                "case-1:range_1",
                            ]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 20},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3WorkerCallbackModule()),),
    )
    def complete_without_runtime_death(**kwargs):
        dispatched_condition_ids.append(kwargs["condition"].condition_id)
        termination_policies.append(kwargs.get("worker_termination_policy"))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        complete_without_runtime_death,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "execution_classification": _smoke_execution_classification(),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert dispatched_condition_ids == [
        "exp3_worker_death_factorization__test__dead3__p25__rep0"
    ]
    assert termination_policies[0] == WorkerTerminationPolicy(
        target_planned_ai_unit_ids=("range_0", "range_1"),
        termination_count_target=3,
        kill_point="progress_25",
        total_planned_ai_unit_count=20,
        process_timeout_seconds=60.0,
    )
    faults = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__test__dead3__p25__rep0",
        "fault_injections.jsonl",
    )
    task = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__test__dead3__p25__rep0",
        "per_task_results.jsonl",
    )[0]
    assert faults == []
    assert task["worker_death_count"] == 0
    assert task["worker_replacement_count"] == 0
    assert task["worker_death_evidence_complete"] is False
    assert task["baseline"] is None
    assert task["baseline_comparison_eligible"] is False
    assert task["baseline_unavailable_reason"] == "smoke_baseline_not_requested"
    assert "matched_baseline_total_tokens" not in task


def test_trace_regression_omits_baseline_and_keeps_exp3_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp3_real_ai_fault_recovery"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id=(
            "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0"
        ),
        fault_type="worker_death",
    )

    class Exp3Module:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 1,
                        "termination_count_target_by_case": {"case-1": 1},
                        "kill_progress_target_percent": 50,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": ["case-1:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 1},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    provider_dispatches: list[str] = []

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    def failed_reference_builder(*args, **kwargs):
        reference = {
            **_complete_shared_exp1_reference(*args, **kwargs),
            "source_root_status": "failed",
            "baseline_comparison_eligible": False,
            "baseline_unavailable_reason": "source_exp1_failed_experimental",
        }
        core = {
            key: value
            for key, value in reference.items()
            if key
            not in {
                "source_hash",
                "source_reference_id",
                "planned_source_reference_id",
                "reference_policy_id",
            }
        }
        return {**reference, "source_hash": formal_runner.digest_json(core)}
    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3Module()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "execution_classification": _smoke_execution_classification(),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED
    assert provider_dispatches == [
        "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0"
    ]
    task = _generation_records(
        tmp_path,
        experiment_id,
        "exp3_worker_death_factorization__failed_shared__dead1__p50__rep0",
        "per_task_results.jsonl",
    )[0]
    assert task["baseline"] is None
    assert task["baseline_comparison_eligible"] is False
    assert task["baseline_unavailable_reason"] == "smoke_baseline_not_requested"


def test_formal_runner_worker_death_paired_trace_identity_mismatch_fails_closed_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_full_resource_trace import (
        _pressure_factor_cases,
        _pressure_trace_context,
    )

    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id=(
            "exp3_worker_death_factorization__invalid_baseline__dead1__p25__rep0"
        ),
        fault_type="worker_death",
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-must-not-run",
        ablation_mode="NO_VERIFICATION",
    )
    trace_cases = _pressure_factor_cases(1)
    trace_case_id = str(trace_cases[0]["case_id"])
    exp3_condition, exp3_selection = exp3_plan.bound_items()[0]
    exp3_selection = replace(
        exp3_selection,
        ordered_case_ids=(trace_case_id,),
        expected_ai_unit_count=1,
    )
    exp3_plan = replace(
        exp3_plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                exp3_condition,
                exp3_selection,
            ),
        ),
    )
    trace_context = _pressure_trace_context(
        bank_root=tmp_path.with_name(tmp_path.name + "-identity-mismatch-trace-bank"),
        condition_id=exp3_condition.condition_id + "__mismatch",
        cases=trace_cases,
        provider_config_digest=exp3_condition.source_provider_config_digest,
        model_entry_id=str(exp3_condition.model_entry_id),
        provider_family=str(exp3_condition.provider_family),
        provider_model_id=str(exp3_condition.provider_model_id),
    )
    invoked_conditions: list[str] = []
    provider_dispatches: list[str] = []

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest={
                    "baseline_policy": "required_by_formal_plan",
                    "fault_target_manifest": None,
                    "worker_death_manifest": {
                        "dead_worker_count_target": 1,
                        "termination_count_target_by_case": {trace_case_id: 1},
                        "kill_progress_target_percent": 25,
                        "selected_target_ai_unit_ids_by_case": {
                            trace_case_id: [f"{trace_case_id}:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {trace_case_id: 1},
                    },
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            invoked_conditions.append(condition.condition_id)
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3CallbackModule()), (exp4, Exp4CallbackModule())),
    )

    def dispatch(**kwargs):
        provider_dispatches.append(kwargs["condition"].condition_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)

    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            ),
            "dispatch_plans": (exp3_plan, exp4_plan),
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (
                    *trace_cases,
                    {
                        "case_id": "case-1",
                        "difficulty": "easy",
                        "paper_difficulty": "easy",
                        "expected_ai_unit_count": 1,
                    },
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(exp3, exp4),
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *exp3_plan.bound_items(),
                *exp4_plan.bound_items(),
            ),
            "trace_context": trace_context,
        }
    )

    assert suite.status == PaperStatus.BLOCKED
    assert invoked_conditions == [
        "exp3_worker_death_factorization__invalid_baseline__dead1__p25__rep0"
    ]
    assert provider_dispatches == []
    assert (tmp_path / "condition_results.jsonl").is_file()
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "exp3_worker_death_factorization__invalid_baseline__dead1__p25__rep0",
        "per_task_results.jsonl",
    )[0]
    exp4_task = _generation_records(
        tmp_path,
        exp4,
        "condition-exp4-must-not-run",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["outcome_status"] == "blocked_dependency"
    assert exp3_task["evidence_integrity"] == "invalid"
    assert exp3_task["failure_stage"] == "paired_trace_reference"
    assert exp3_task["failure_kind"] == "source_bank_reference_unavailable"
    assert exp4_task["root_status"] == "not_started"


@pytest.mark.parametrize("execution_path", ("smoke", "formal"))
def test_failed_worker_death_closes_manifests_and_continues_exp4_in_both_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    execution_path: str,
) -> None:
    exp3 = "exp3_real_ai_fault_recovery"
    exp4 = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    exp3_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp3,
        condition_id=(
            "exp3_worker_death_factorization__failed__dead1__p50__rep0"
        ),
        fault_type="worker_death",
    )
    exp4_plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=exp4,
        condition_id="condition-exp4-after-worker-death",
        ablation_mode="NO_VERIFICATION",
    )

    class Exp3CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp3,
                execution_manifest=_shared_exp1_manifest(
                    request_limits=dict(context.request_limits),
                    fault_target_manifest=None,
                    worker_death_manifest={
                        "dead_worker_count_target": 1,
                        "termination_count_target_by_case": {"case-1": 1},
                        "kill_progress_target_percent": 50,
                        "selected_target_ai_unit_ids_by_case": {
                            "case-1": ["case-1:range_0"]
                        },
                        "planned_ai_unit_count_by_case": {"case-1": 1},
                    },
                ),
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp3, rows=())

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=exp4,
                mode_config={"ablation_mode": "NO_VERIFICATION"},
                ablation_profile={"disabled_mechanisms": ["verification"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=exp4, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((exp3, Exp3CallbackModule()), (exp4, Exp4CallbackModule())),
    )
    dispatched: list[str] = []

    def dispatch(**kwargs):
        condition = kwargs["condition"]
        dispatched.append(condition.condition_id)
        base = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=condition,
            case_id=kwargs["case"]["case_id"],
        )
        if condition.condition_id == (
            "exp3_worker_death_factorization__failed__dead1__p50__rep0"
        ):
            attempt = replace(
                base.attempt_results[0],
                attempt_status=PaperAttemptStatus.WORKER_DIED,
                error_kind="worker_died",
            )
            task = SimpleNamespace(
                **{
                    **vars(base.task_result),
                    "root_status": PaperTaskStatus.FAILED,
                }
            )
            return SimpleNamespace(
                **{
                    **vars(base),
                    "task_result": task,
                    "attempt_results": [attempt],
                    "fault_records": [
                        {
                            "schema_version": (
                                "tokenshare.paper_worker_death_incomplete.v1"
                            ),
                            "fault_type": "worker_death",
                            "dead_attempt": {
                                "attempt_id": attempt.attempt_id,
                                "unit_id": attempt.unit_id,
                            },
                            "replacement_fact": None,
                            "recovery_completed": False,
                            "evidence_complete": False,
                            "coordinator": {"survived": True},
                        }
                    ],
                }
            )
        if condition.experiment_id == exp4:
            return _rejected_adapter_result(
                output_root=Path(kwargs["output_root"]),
                condition=condition,
                case_id=kwargs["case"]["case_id"],
            )
        return base

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    smoke_items = []
    for plan in (exp3_plan, exp4_plan):
        condition, selection = plan.bound_items()[0]
        smoke_items.append(
            {
                "item_id": f"item-{condition.condition_id}",
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "selection_id": selection.selection_id,
                "selection_digest": selection.selection_digest,
                "case_id": "case-1",
                "repeat_id": condition.repeat_id,
                "condition_selector": {
                    "domain": condition.domain,
                    "difficulty": condition.difficulty,
                    "fault_type": condition.fault_type,
                    "ablation_mode": condition.ablation_mode,
                },
            }
        )
    budget = _with_ai_unit_commitments(
        _budget(
            planned_experiments=(exp3, exp4),
            planned_conditions=2,
            planned_root_runs=2,
            planned_ai_units=2,
        ),
        *exp3_plan.bound_items(),
        *exp4_plan.bound_items(),
    )
    if execution_path == "smoke":
        from tokenshare.experiments.paper_smoke import (
            PaperSmokeExecutionPlan,
            PaperSmokeItem,
            PaperSmokeProfile,
            ResolvedPaperSmokeItem,
            execute_paper_smoke_suite,
        )

        profile_items = tuple(
            PaperSmokeItem(
                item_id=item["item_id"],
                experiment_id=item["experiment_id"],
                case_id=item["case_id"],
                repeat_id=item["repeat_id"],
                condition_selector=item["condition_selector"],
            )
            for item in smoke_items
        )
        profile = PaperSmokeProfile(
            suite_id="worker-death-continuation-smoke",
            profile_version="test-v1",
            catalog_id="test-catalog",
            catalog_version="test-v1",
            catalog_digest=CATALOG_DIGEST,
            experiment_ids=(exp3, exp4),
            expected_root_runs=2,
            output_mode="separate_root",
            items=profile_items,
            ineligibility_reasons=(
                "offline_regression",
                "smoke_baseline_not_requested",
            ),
            baseline_policy="omitted_for_smoke_regression",
        )
        execution_plan = PaperSmokeExecutionPlan(
            suite_id=profile.suite_id,
            profile_digest=profile.profile_digest,
            catalog_id=profile.catalog_id,
            catalog_version=profile.catalog_version,
            catalog_digest=profile.catalog_digest,
            output_root=str(tmp_path),
            experiment_ids=(exp3, exp4),
            items=tuple(ResolvedPaperSmokeItem(**item) for item in smoke_items),
            dispatch_plans=(exp3_plan, exp4_plan),
            root_case_filter={
                item["condition_id"]: (item["case_id"],)
                for item in smoke_items
            },
            baseline_policy="omitted_for_smoke_regression",
        )
        quota = json.loads(json.dumps(budget.quota_preflight))
        quota["budget_approval"] = {
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        }
        budget = replace(budget, quota_preflight=quota)
        suite = execute_paper_smoke_suite(
            profile=profile,
            execution_plan=execution_plan,
            catalog_manifest=_formal_execution_kwargs(
                tmp_path=tmp_path,
                config=config,
                plan=exp3_plan,
            )["catalog_manifest"],
            budget=budget,
            ai_api_configs={PROVIDER_CONFIG_ID: config},
            transport=object(),
            real_transport=False,
            hard_limits={},
            launch_manifest={
                "schema_version": "tokenshare.paper_smoke_launch_manifest.v1",
                "launch_id": "offline-worker-death-continuation",
                "paper_eligible": False,
            },
        )
    else:
        from tokenshare.experiments.paper_formal_metrics import (
            recompute_paper_formal_metrics,
        )
        from tokenshare.experiments.paper_formal_report import (
            generate_paper_formal_report,
        )

        suite = formal_runner.execute_paper_formal_suite(
            **{
                **_formal_execution_kwargs(
                    tmp_path=tmp_path,
                    config=config,
                    plan=exp3_plan,
                ),
                "dispatch_plans": (exp3_plan, exp4_plan),
                "budget": budget,
                "execution_classification": _smoke_execution_classification(),
            }
        )
        from tokenshare.experiments.paper_metric_contract import (
            load_paper_metric_contract,
        )
        from tokenshare.experiments.paper_metric_registry import (
            load_paper_metric_registry,
        )

        metric_contract = load_paper_metric_contract()
        metric_registry = load_paper_metric_registry(metric_contract)
        metrics = recompute_paper_formal_metrics(
            tmp_path,
            {key: () for key in metric_registry.required_input_keys},
            global_infrastructure_valid=False,
            registry=metric_registry,
            contract=metric_contract,
        )
        # 旧 report 壳尚未迁移到 Task 18 draft shape；本回归只验证失败封口。
        object.__setattr__(metrics, "condition_rows", ())
        object.__setattr__(metrics, "experiment_rows", {})
        object.__setattr__(metrics, "capturing", False)
        object.__setattr__(metrics, "exp5_artifact_rows", None)
        object.__setattr__(metrics, "exp5_artifact_rows_digest", None)
        formal_report = generate_paper_formal_report(
            output_root=tmp_path,
            metrics=metrics,
            secret_values=(),
        )
        assert metrics.paper_eligible is False
        assert formal_report.paper_eligible is False
        assert formal_report.formal_paper_table_generated is False

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatched == [
        "exp3_worker_death_factorization__failed__dead1__p50__rep0",
        "condition-exp4-after-worker-death",
    ]
    exp3_task = _generation_records(
        tmp_path,
        exp3,
        "exp3_worker_death_factorization__failed__dead1__p50__rep0",
        "per_task_results.jsonl",
    )[0]
    assert exp3_task["root_status"] == "failed"
    assert exp3_task["worker_death_evidence_complete"] is False
    assert exp3_task["outcome_status"] == "failed_experimental"
    assert exp3_task["evidence_integrity"] == "complete"
    assert (tmp_path / "condition_results.jsonl").is_file()
    for experiment_id in (exp3, exp4):
        manifest = json.loads(
            (
                tmp_path
                / "experiments"
                / experiment_id
                / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "completed_with_failures"
    if execution_path == "smoke":
        report = json.loads(
            (tmp_path / "metrics" / "smoke_summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert report["suite_status"] == "completed_with_failures"
        assert report["row_count"] == 2
        exp3_row = next(
            row for row in report["rows"] if row["experiment_id"] == exp3
        )
        assert exp3_row["baseline_policy"] == (
            "omitted_for_smoke_regression"
        )
        assert exp3_row["baseline"] is None
        assert exp3_row["baseline_comparison_eligible"] is False
        assert exp3_row["baseline_unavailable_reason"] == (
            "smoke_baseline_not_requested"
        )
        assert exp3_row["outcome_status"] == "failed_experimental"
        assert exp3_row["evidence_integrity"] == "complete"
        assert exp3_row["accepted_validity"] is None
        assert exp3_row["accepted_validity_unavailable_reason"] == (
            "not_applicable_failed_experimental"
        )
        assert exp3_row["wall_clock_ms"] == 1000
        assert exp3_row["wall_clock_ms_unavailable_reason"] is None
        assert exp3_row["smoke_execution_status"] == "failed"
        expected_outputs = (
            "metrics/smoke_summary.json",
            "metrics/smoke_failures.json",
            "audit/smoke_eligibility_report.json",
            "audit/secret_scan_report.json",
            "audit/smoke_evidence_manifest.json",
        )
    else:
        suite_manifest = json.loads(
            (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
        )
        assert suite_manifest["formal"] is False
        assert suite_manifest["pilot_only"] is True
        assert suite_manifest["execution_scope"] == "smoke_suite"
        expected_outputs = (
            "metrics/paper_metric_drafts.v1.json",
            "audit/paper_eligibility_report.json",
            "audit/secret_scan_report.json",
            "formal_report_result.json",
        )
    for relative in expected_outputs:
        assert (tmp_path / relative).is_file()


def test_formal_runner_exp4_persists_mode_specific_wrapper_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id="condition-exp4-no-verification",
        ablation_mode="NO_VERIFICATION",
    )
    dispatched_modes: list[str | None] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": "NO_VERIFICATION"},
                ablation_profile={"disabled_mechanisms": ["verification"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    def rejected_dispatch(**kwargs):
        dispatched_modes.append(kwargs.get("ablation_mode"))
        return _rejected_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", rejected_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatched_modes == ["NO_VERIFICATION"]
    task = _generation_records(
        tmp_path, experiment_id, "condition-exp4-no-verification", "per_task_results.jsonl"
    )[0]
    events = _generation_records(
        tmp_path,
        experiment_id,
        "condition-exp4-no-verification",
        "events/event_log.jsonl",
    )
    assert task["root_status"] == "failed"
    assert task["ablation_runtime_flags"]["wrong_canonical_exposed"] is True
    assert task["final_deterministic_validity"] is False
    assert any(
        event["event_type"] == "EXPERIMENT_ABLATION_OBSERVED"
        for event in events
    )


def _apply_exp4_runtime_probe(
    *,
    tmp_path: Path,
    hook_observations: list[dict[str, object]],
) -> formal_runner._RootExecutionOutcome:
    condition = SimpleNamespace(
        condition_id="condition-exp4-probe",
        repeat_id=0,
        ablation_mode="NO_VERIFICATION",
    )
    task = {
        "task_id": "case-exp4-probe",
        "root_status": "completed",
        "event_refs": [],
        "artifact_refs": [],
    }
    outcome = formal_runner._RootExecutionOutcome(
        case_id="case-exp4-probe",
        root_status="completed",
        adapter_root=tmp_path,
        worker_id="worker-1",
        task=task,
        adapter_result={
            "task_result": task,
            "attempt_results": [{"attempt_status": "succeeded"}],
            "fault_records": [],
            "event_records": [],
            "run_evidence": {
                "ablation_runtime": {
                    "schema_version": "tokenshare.paper_ablation_runtime.v1",
                    "hook_observations": hook_observations,
                }
            },
        },
    )
    return formal_runner._FormalConditionExecutionCallback._apply_exp4_mode(
        None,
        condition=condition,
        outcome=outcome,
        callback_kwargs={
            "mode_config": {"ablation_mode": "NO_VERIFICATION"}
        },
    )


def test_formal_runner_exp4_preserves_typed_hook_envelope_without_flat_fields(
    tmp_path: Path,
) -> None:
    artifact_ref = ArtifactRef(
        artifact_id="runtime-hook-artifact",
        artifact_type="experiment_evidence",
        uri="artifacts/runtime-hook-artifact.json",
        content_hash="sha256:" + "8" * 64,
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="tokenshare.test.runtime_hook",
        artifact_schema_version="v1",
        source={"kind": "pytest"},
        metadata={},
        created_at="2026-08-01T00:00:00Z",
    )
    observation = build_experiment_ablation_gate_applied_observation(
        ablation_mode="NO_VERIFICATION",
        disabled_mechanism="verification",
        protocol_event_refs=(),
        artifact_refs=(artifact_ref,),
        hook_input={
            "task_id": "case-exp4-probe",
            "unit_id": "unit-1",
            "attempt_id": "attempt-1",
            "lease_id": "lease-1",
        },
        hook_result={"bypass": True, "stop": False},
    ).to_dict()

    result = _apply_exp4_runtime_probe(
        tmp_path=tmp_path,
        hook_observations=[observation],
    )

    assert result.task["ablation_runtime"]["hook_observations"] == [
        observation
    ]
    assert set(
        result.task["ablation_runtime"]["hook_observations"][0]
    ) == {"schema_version", "kind", "payload", "observation_digest"}


def test_formal_runner_exp4_keeps_not_applicable_summary_outside_hooks(
    tmp_path: Path,
) -> None:
    result = _apply_exp4_runtime_probe(
        tmp_path=tmp_path,
        hook_observations=[],
    )
    runtime = result.task["ablation_runtime"]

    assert runtime["hook_observations"] == []
    assert runtime["target_hook_not_applicable"] == {
        "ablation_mode": "NO_VERIFICATION",
        "disabled_mechanism": "verification",
        "applicability": "not_applicable",
        "not_applicable_reason": "target_lifecycle_boundary_not_reached",
        "protocol_event_refs": [],
        "artifact_refs": [],
    }


def test_formal_runner_exp4_rejects_legacy_flat_hook_in_formal_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="runtime hook observation"):
        _apply_exp4_runtime_probe(
            tmp_path=tmp_path,
            hook_observations=[
                {
                    "event_type": "EXPERIMENT_ABLATION_GATE_APPLIED",
                    "ablation_mode": "NO_VERIFICATION",
                    "disabled_mechanism": "verification",
                }
            ],
        )


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    (
        ("FULL", PaperStatus.COMPLETED_WITH_FAILURES),
        ("NO_REQUEUE", PaperStatus.COMPLETED_WITH_FAILURES),
    ),
)
def test_formal_runner_exp4_observes_without_synthetic_replacement_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: PaperStatus,
) -> None:
    experiment_id = "exp4_real_ai_protocol_ablation"
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=experiment_id,
        condition_id=f"condition-exp4-{mode.lower()}",
        ablation_mode=mode,
    )
    dispatch_calls: list[Path] = []

    class Exp4CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=experiment_id,
                mode_config={"ablation_mode": mode},
                ablation_profile={"disabled_mechanisms": ["requeue"]},
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    def rejecting_then_complete_dispatch(**kwargs):
        output_root = Path(kwargs["output_root"])
        dispatch_calls.append(output_root)
        builder = (
            _rejected_adapter_result
            if len(dispatch_calls) == 1
            else _complete_adapter_result
        )
        return builder(
            output_root=output_root,
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp4CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        rejecting_then_complete_dispatch,
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                    max_provider_attempts=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert len(dispatch_calls) == 1
    assert suite.status == expected_status
    task = _generation_records(
        tmp_path,
        experiment_id,
        f"condition-exp4-{mode.lower()}",
        "per_task_results.jsonl",
    )[0]
    assert task["root_status"] == "failed"
    assert task["ablation_mode"] == mode
    assert "stuck_after_rejection" not in task
    assert "replacement_attempt_count" not in task


def test_formal_runner_exp4_no_requeue_stuck_is_failed_not_blocked() -> None:
    """唯一 typed NO_REQUEUE stuck root 只能作为实验失败进入 checkpoint。"""

    task = {
        "root_status": "blocked",
        "wall_clock_ms": 610_991,
        "ablation_mode": "NO_REQUEUE",
        "ablation_applicable": True,
        "ablation_runtime_flags": {"stuck_after_rejection": True},
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
    }

    normalized = formal_runner._normalize_exp4_no_requeue_stuck_checkpoint_task(
        condition=SimpleNamespace(
            experiment_id="exp4_real_ai_protocol_ablation",
            ablation_mode="NO_REQUEUE",
        ),
        task=task,
    )

    assert normalized is True
    assert task["root_status"] == "failed"
    assert task["outcome_status"] == "failed_experimental"
    assert task["wall_clock_ms"] is None


@pytest.mark.parametrize(
    ("experiment_id", "condition_mode", "task_mode", "applicable", "stuck"),
    (
        ("foreign_experiment", "NO_REQUEUE", "NO_REQUEUE", True, True),
        ("exp4_real_ai_protocol_ablation", "FULL", "NO_REQUEUE", True, True),
        ("exp4_real_ai_protocol_ablation", "NO_REQUEUE", "FULL", True, True),
        ("exp4_real_ai_protocol_ablation", "NO_REQUEUE", "NO_REQUEUE", False, True),
        ("exp4_real_ai_protocol_ablation", "NO_REQUEUE", "NO_REQUEUE", True, False),
    ),
)
def test_no_requeue_checkpoint_normalization_is_fail_closed(
    experiment_id: str,
    condition_mode: str,
    task_mode: str,
    applicable: bool,
    stuck: bool,
) -> None:
    """只有 frozen condition 与 persisted task 同时精确匹配才可降为实验失败。"""

    task = {
        "root_status": "blocked",
        "wall_clock_ms": 610_991,
        "ablation_mode": task_mode,
        "ablation_applicable": applicable,
        "ablation_runtime_flags": {"stuck_after_rejection": stuck},
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
    }

    normalized = formal_runner._normalize_exp4_no_requeue_stuck_checkpoint_task(
        condition=SimpleNamespace(
            experiment_id=experiment_id,
            ablation_mode=condition_mode,
        ),
        task=task,
    )

    assert normalized is False
    assert task["root_status"] == "blocked"
    assert task["wall_clock_ms"] == 610_991


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("root_status", "blocked"),
        ("outcome_status", "blocked_dependency"),
        ("evidence_integrity", "invalid"),
    ),
)
def test_no_requeue_incomplete_clock_requires_normalized_complete_failure(
    field_name: str,
    invalid_value: str,
) -> None:
    """非终态 clock 只接受 checkpoint 已归类的完整实验失败。"""

    task = {
        "root_status": "failed",
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
        "ablation_mode": "NO_REQUEUE",
        "ablation_applicable": True,
        "ablation_runtime_flags": {"stuck_after_rejection": True},
    }
    task[field_name] = invalid_value

    assert not formal_runner._is_exp4_no_requeue_stuck_clock_binding(
        row=SimpleNamespace(
            experiment_id="exp4_real_ai_protocol_ablation",
            condition_axes={"ablation_mode": "NO_REQUEUE"},
        ),
        task=task,
    )


def test_formal_runner_exp5_persists_v2_observed_identity_without_fixed_entry_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id="condition-exp5-fixed-entry",
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    selection = replace(base_selection, experiment_id=EXP5_EXPERIMENT_ID)
    plan = replace(
        base,
        experiment_id=EXP5_EXPERIMENT_ID,
        output_root=(tmp_path / EXP5_EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }

    class Exp5CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=EXP5_EXPERIMENT_ID,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXP5_EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXP5_EXPERIMENT_ID, Exp5CallbackModule()),),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: _exp5_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
            resolved_model="unexpected/resolved-model",
        ),
    )
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
            "ai_api_configs": {
                PROVIDER_CONFIG_ID: config,
                formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    EXP5_EXPERIMENT_ID: {
                        "status": "planned",
                        "cohort_id": MODEL_COHORT_ID,
                        "model_cohort_digest": MODEL_COHORT_DIGEST,
                        "member_plans": {COHORT_MEMBER_ID: member_plan},
                        "request_controls_snapshot": request_controls[
                            "comparable"
                        ],
                        "request_controls_snapshot_digest": request_controls[
                            "comparable_digest"
                        ],
                    }
                },
            },
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(EXP5_EXPERIMENT_ID,),
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=1,
                ),
                *plan.bound_items(),
            ),
        }
    )

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    task = _generation_records(
        tmp_path, EXP5_EXPERIMENT_ID, condition.condition_id, "per_task_results.jsonl"
    )[0]
    attempt = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )[0]
    model_input = task["model_execution_records"][0]
    assert (
        model_input["record"]["schema_version"]
        == "tokenshare.paper_model_execution_record.v2"
    )
    assert model_input["record"]["identity_status"] == "model_identity_mismatch"
    assert "resolved_model_mismatch" in model_input["record"]["mismatch_reasons"]
    assert attempt["model_identity_audit"] == "model_identity_mismatch"
    assert attempt["model_identity_audit"] != "fixed_entry_match"
    assert task["root_status"] == "ineligible"
    assert attempt["attempt_status"] == "model_identity_mismatch"
    assert attempt["cohort_member_id"] == COHORT_MEMBER_ID


def test_formal_runner_exp5_identity_mismatch_stops_condition_and_materializes_remaining_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _two_case_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    first_condition = replace(
        base_condition,
        experiment_id=EXP5_EXPERIMENT_ID,
        condition_id="condition-exp5-first",
        model_cohort_id=MODEL_COHORT_ID,
        model_cohort_digest=MODEL_COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
    )
    second_condition = replace(
        first_condition,
        condition_id="condition-exp5-second",
    )
    first_selection = replace(
        base_selection,
        experiment_id=EXP5_EXPERIMENT_ID,
    )
    second_selection = replace(
        first_selection,
        selection_id="selection-exp5-second",
        ordered_case_ids=("case-3",),
        expected_ai_unit_count=1,
    )
    plan = replace(
        base,
        experiment_id=EXP5_EXPERIMENT_ID,
        output_root=(tmp_path / EXP5_EXPERIMENT_ID).as_posix(),
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    request_controls = _normalized_exp5_request_controls(config)
    member_plan = {
        "cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": request_controls,
    }
    dispatch_calls: list[tuple[str, str]] = []

    class Exp5CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                experiment_id=EXP5_EXPERIMENT_ID,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXP5_EXPERIMENT_ID, rows=())

    def mismatching_dispatch(**kwargs):
        condition = kwargs["condition"]
        case_id = kwargs["case"]["case_id"]
        dispatch_calls.append((condition.condition_id, case_id))
        return _exp5_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=condition,
            case_id=case_id,
            resolved_model="unexpected/resolved-model",
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXP5_EXPERIMENT_ID, Exp5CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", mismatching_dispatch)
    execution_kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": tuple(
                {
                    "case_id": case_id,
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                }
                for case_id in ("case-1", "case-2", "case-3")
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "ai_api_configs": {
            PROVIDER_CONFIG_ID: config,
            formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                EXP5_EXPERIMENT_ID: {
                    "status": "planned",
                    "cohort_id": MODEL_COHORT_ID,
                    "model_cohort_digest": MODEL_COHORT_DIGEST,
                    "member_plans": {COHORT_MEMBER_ID: member_plan},
                    "request_controls_snapshot": request_controls["comparable"],
                    "request_controls_snapshot_digest": request_controls[
                        "comparable_digest"
                    ],
                }
            },
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_experiments=(EXP5_EXPERIMENT_ID,),
                planned_conditions=2,
                planned_root_runs=3,
                planned_ai_units=3,
            ),
            *plan.bound_items(),
        ),
    }
    suite = formal_runner.execute_paper_formal_suite(**execution_kwargs)

    assert suite.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert suite.task_count == 3
    assert suite.provider_attempt_count == 2
    assert dispatch_calls == [
        (first_condition.condition_id, "case-1"),
        (second_condition.condition_id, "case-3"),
    ]
    first_tasks = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        first_condition.condition_id,
        "per_task_results.jsonl",
    )
    first_attempts = _generation_records(
        tmp_path,
        EXP5_EXPERIMENT_ID,
        first_condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert [task["task_id"] for task in first_tasks] == ["case-1", "case-2"]
    assert [task["root_status"] for task in first_tasks] == [
        "ineligible",
        "not_started",
    ]
    assert first_tasks[1]["error_kind"] == "model_identity_fail_stop"
    assert [attempt["attempt_status"] for attempt in first_attempts] == [
        "model_identity_mismatch",
        "not_started",
    ]
    assert first_attempts[0]["provider_attempt_count"] == 1
    assert first_attempts[1]["provider_attempt_index"] == 0
    assert all(
        task["cohort_member_id"] == COHORT_MEMBER_ID for task in first_tasks
    )

    resumed = formal_runner.execute_paper_formal_suite(
        **execution_kwargs,
        resume=True,
    )
    assert resumed.status == PaperStatus.COMPLETED_WITH_FAILURES
    assert dispatch_calls == [
        (first_condition.condition_id, "case-1"),
        (second_condition.condition_id, "case-3"),
    ]


def _planned_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-1",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="full_protocol",
        model_policy="fixed_entry",
        provider_config_id=PROVIDER_CONFIG_ID,
        model_entry_id=MODEL_ENTRY_ID,
        provider_family="siliconflow",
        provider_model_id=PROVIDER_MODEL_ID,
        reasoning_profile_id="default",
        source_provider_config_digest=config.config_digest,
        model_endpoint_identity_digest=ENDPOINT_DIGEST,
        repeat_id=0,
        seed=1,
        catalog_digest=CATALOG_DIGEST,
    )
    selection = FrozenCaseSelection(
        selection_id="selection-1",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="factorization",
        paper_difficulty="easy",
        topic_family=None,
        ordered_case_ids=("case-1",),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=1,
        paper_eligible_required=True,
    )
    return _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(output_root / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _two_case_dispatch_plan(
    output_root: Path,
    *,
    config: AIAPIExecutorConfig,
) -> formal_runner.PaperExperimentDispatchPlan:
    plan = _planned_dispatch_plan(output_root, config=config)
    condition, selection = plan.bound_items()[0]
    selection = replace(
        selection,
        ordered_case_ids=("case-1", "case-2"),
        expected_ai_unit_count=2,
    )
    return replace(
        plan,
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _plan_for_experiment(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    experiment_id: str,
    condition_id: str,
    fault_type: str = "none",
    fault_rate: float = 0.0,
    ablation_mode: str = "full_protocol",
) -> formal_runner.PaperExperimentDispatchPlan:
    base = _planned_dispatch_plan(tmp_path, config=config)
    base_condition, base_selection = base.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id=condition_id,
        fault_type=fault_type,
        fault_rate=fault_rate,
        ablation_mode=ablation_mode,
    )
    selection = replace(base_selection, experiment_id=experiment_id)
    return replace(
        base,
        experiment_id=experiment_id,
        output_root=(tmp_path / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )


def _ai_config() -> AIAPIExecutorConfig:
    return AIAPIExecutorConfig(
        schema_version="phase7.ai_api_executor_config.v1",
        executor_id="formal-runner-test",
        provider_family="siliconflow",
        selection_policy={"kind": "fixed"},
        defaults={
            "timeout_seconds": 30,
            "max_tokens": 512,
            "temperature": 0.2,
            "top_p": 1.0,
            "stream": False,
            "max_provider_attempts": 1,
        },
        entries=[
            AIAPIProviderEntry(
                entry_id=MODEL_ENTRY_ID,
                enabled=True,
                base_url="https://example.invalid/v1",
                api_key_env="TOKENSHARE_FORMAL_RUNNER_TEST_KEY",
                model=PROVIDER_MODEL_ID,
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={
                    "max_tokens": 1024,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                pricing={
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                tags=["test"],
            )
        ],
        local_concurrency={"max_in_flight_global": 1},
        metadata={"test_only": True},
    )


def _dispatch_plan_type():
    return formal_runner.execute_paper_formal_suite.__globals__[
        "PaperExperimentDispatchPlan"
    ]


def _budget(
    *,
    planned_experiments: tuple[str, ...] = (EXPERIMENT_ID,),
    planned_conditions: int,
    planned_root_runs: int,
    planned_ai_units: int,
    max_provider_attempts: int | None = None,
) -> PaperBudgetResult:
    attempt_upper_bound = (
        planned_ai_units
        if max_provider_attempts is None
        else max_provider_attempts
    )
    max_tokens = 1024
    disk_estimate = paper_budget._paper_disk_estimate(
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        provider_attempt_upper_bound=attempt_upper_bound,
        max_tokens=max_tokens,
        max_condition_root_runs=planned_root_runs,
        max_condition_ai_units=planned_ai_units,
        max_condition_provider_attempts=attempt_upper_bound,
    )
    return PaperBudgetResult(
        budget_digest=BUDGET_DIGEST,
        planned_experiments=planned_experiments,
        planned_conditions=planned_conditions,
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        max_provider_attempts=attempt_upper_bound,
        token_upper_bound=attempt_upper_bound * max_tokens,
        cost_upper_bound=attempt_upper_bound * 0.01,
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={
            "provider_calls_made": 0,
            "budget_commitments": {
                "disk_estimate_authority": {
                    "schema_version": (
                        "tokenshare.paper_disk_estimate_authority.v1"
                    ),
                    "token_upper_bound_per_provider_attempt": max_tokens,
                },
                "request_limits": {
                    "token_upper_bound_per_provider_attempt": max_tokens,
                }
            },
        },
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate=disk_estimate,
        status=PaperStatus.PLANNED,
    )


def _with_ai_unit_commitments(
    budget: PaperBudgetResult,
    *bound_items: tuple[object, FrozenCaseSelection],
    selected_case_ids_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> PaperBudgetResult:
    quota = json.loads(json.dumps(budget.quota_preflight))
    commitments: list[dict[str, object]] = []
    condition_counts: dict[str, tuple[int, int, int]] = {}
    for condition, selection in bound_items:
        case_ids = tuple(selection.ordered_case_ids)
        if not case_ids:
            continue
        expected_count = int(selection.expected_ai_unit_count)
        per_case_count, remainder = divmod(expected_count, len(case_ids))
        if per_case_count < 1 or remainder:
            raise ValueError(
                "test selection must expose an exact uniform per-case AI-unit count"
            )
        unit_prefix = "child" if condition.domain == "lean_proof" else "range"
        selected_case_ids = (
            tuple(selected_case_ids_by_condition[condition.condition_id])
            if selected_case_ids_by_condition is not None
            and condition.condition_id in selected_case_ids_by_condition
            else case_ids
        )
        if any(case_id not in case_ids for case_id in selected_case_ids):
            raise ValueError("test selected-case budget projection is outside selection")
        replacement = paper_budget._condition_replacement_policy(condition)
        replacement_multiplier = (
            int(replacement["max_retries"])
            if replacement["replacement_attempts_allowed"] is True
            else 0
        )
        selected_ai_units = len(selected_case_ids) * per_case_count
        selected_provider_attempts = selected_ai_units * (1 + replacement_multiplier)
        condition_counts[condition.condition_id] = (
            len(selected_case_ids),
            selected_ai_units,
            selected_provider_attempts,
        )
        for case_id in case_ids:
            commitments.append(
                {
                    "condition_id": condition.condition_id,
                    "condition_digest": condition.condition_digest,
                    "case_id": case_id,
                    "planned_ai_unit_ids": [
                        f"{unit_prefix}_{index}" for index in range(per_case_count)
                    ],
                }
            )
    quota["budget_commitments"]["ai_unit_commitments"] = commitments
    selected_provider_ceiling = sum(row[2] for row in condition_counts.values())
    provider_attempt_upper_bound = max(
        int(budget.max_provider_attempts),
        selected_provider_ceiling,
    )
    max_tokens = int(
        quota["budget_commitments"]["disk_estimate_authority"][
            "token_upper_bound_per_provider_attempt"
        ]
    )
    token_upper_bound = provider_attempt_upper_bound * max_tokens
    disk_estimate = paper_budget._paper_disk_estimate(
        planned_conditions=budget.planned_conditions,
        planned_root_runs=budget.planned_root_runs,
        planned_ai_units=budget.planned_ai_units,
        provider_attempt_upper_bound=provider_attempt_upper_bound,
        max_tokens=max_tokens,
        token_upper_bound=token_upper_bound,
        max_condition_root_runs=max(
            (row[0] for row in condition_counts.values()),
            default=0,
        ),
        max_condition_ai_units=max(
            (row[1] for row in condition_counts.values()),
            default=0,
        ),
        max_condition_provider_attempts=max(
            (row[2] for row in condition_counts.values()),
            default=0,
        ),
    )
    return replace(
        budget,
        max_provider_attempts=provider_attempt_upper_bound,
        token_upper_bound=token_upper_bound,
        cost_upper_bound=provider_attempt_upper_bound * 0.01,
        quota_preflight=quota,
        disk_estimate=disk_estimate,
    )


def _rolling_tracker(budget: PaperBudgetResult) -> formal_runner._RollingDiskForecast:
    estimate = budget.disk_estimate
    inputs = estimate["inputs"]
    policy = estimate["policy"]
    return formal_runner._RollingDiskForecast(
        remaining_root_runs=int(inputs["planned_root_runs"]),
        remaining_ai_units=int(inputs["planned_ai_units"]),
        remaining_provider_attempts=int(inputs["provider_attempt_upper_bound"]),
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )


def _formal_execution_kwargs(
    *,
    tmp_path: Path,
    config: AIAPIExecutorConfig,
    plan: formal_runner.PaperExperimentDispatchPlan,
) -> dict[str, object]:
    return {
        "dispatch_plans": (plan,),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
            *plan.bound_items(),
        ),
        "budget_approval": {
            "approval_mode": "user_bypassed",
            "budget_digest": BUDGET_DIGEST,
        },
        "output_root": tmp_path,
        "ai_api_configs": {PROVIDER_CONFIG_ID: config},
        "transport": object(),
        "real_transport": False,
        "hard_limits": {},
    }


def _install_single_case_execution(
    *,
    monkeypatch: pytest.MonkeyPatch,
    dispatch,
) -> None:
    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)


def test_real_transport_smoke_skips_publication_direct_closure_and_keeps_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    def rejected_case_dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": SimpleNamespace(
                    **{
                        **vars(result.task_result),
                        "root_status": PaperTaskStatus.FAILED,
                        "accepted_validity": False,
                    }
                ),
                "attempt_results": [
                    replace(
                        result.attempt_results[0],
                        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
                        error_kind="verifier_rejected",
                    )
                ],
            }
        )

    _install_single_case_execution(
        monkeypatch=monkeypatch,
        dispatch=rejected_case_dispatch,
    )

    def forbid_publication_closure(**_kwargs):
        raise AssertionError("smoke must not enter publication direct closure")

    monkeypatch.setattr(
        formal_runner,
        "_capture_canonical_direct_evidence",
        forbid_publication_closure,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    kwargs.update(
        {
            "real_transport": True,
            "execution_classification": _smoke_execution_classification(),
        }
    )

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status is PaperStatus.COMPLETED_WITH_FAILURES
    assert suite.provider_attempt_count == 1
    assert suite.total_tokens == 11
    assert suite.total_cost_estimate == pytest.approx(0.125)
    task = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_task_results.jsonl",
    )[0]
    attempt = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        "condition-1",
        "per_attempt_results.jsonl",
    )[0]
    assert task["root_status"] == "failed"
    assert task["accepted_validity"] is False
    assert task["runtime_observation"]["runtime_wall_clock_ms"] == 1000
    assert attempt["attempt_status"] == "verification_rejected"
    assert attempt["latency_ms"] == 1000
    assert attempt["prompt_tokens"] == 5
    assert attempt["completion_tokens"] == 6
    assert attempt["total_tokens"] == 11
    assert attempt["cost_estimate"] == pytest.approx(0.125)


def test_formal_real_transport_still_requires_publication_direct_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)

    def complete_case_dispatch(**kwargs):
        case_id = kwargs["case"]["case_id"]
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    _install_single_case_execution(
        monkeypatch=monkeypatch,
        dispatch=complete_case_dispatch,
    )
    capture_calls: list[str] = []

    def block_publication_closure(**kwargs):
        capture_calls.append(str(kwargs["case_id"]))
        raise ValueError("formal publication closure blocked")

    monkeypatch.setattr(
        formal_runner,
        "_capture_canonical_direct_evidence",
        block_publication_closure,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    kwargs["real_transport"] = True
    kwargs["enforce_publication_closure"] = True

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert capture_calls == ["case-1"]
    assert suite.status is PaperStatus.BLOCKED
    assert suite.error_summary[0]["failure_stage"] == "runner_internal"
    assert suite.error_summary[0]["resource_diagnostics"]["message"] == (
        "formal publication closure blocked"
    )


def test_formal_default_skips_publication_closure_after_real_lean_full_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tokenshare.experiments.lean_paper_adapter as lean_adapter_module
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tests.experiments.test_lean_paper_adapter import _condition_for_case
    from tests.support.lean_checker import RecordingLeanChecker
    from tokenshare.experiments.lean_paper_adapter import (
        ScriptedLeanPaperProofTransport,
    )
    from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport

    config = _ai_config()
    monkeypatch.setenv("TOKENSHARE_FORMAL_RUNNER_TEST_KEY", "offline-lean-key")
    from tokenshare.experiments.paper_model_identity import (
        build_model_endpoint_identity,
    )

    identity = build_model_endpoint_identity(
        model_cohort_id="test_exp1_fixed_endpoint_cohort",
        model_cohort_digest="sha256:" + "6" * 64,
        cohort_member_id="test_exp1_fixed_endpoint_member",
        provider_config_id=PROVIDER_CONFIG_ID,
        selected_entry_id=MODEL_ENTRY_ID,
        expected_provider_family="siliconflow",
        expected_provider_model_id=PROVIDER_MODEL_ID,
        expected_reasoning_profile_id="default",
        source_config=config,
    )
    case = paper_catalog_module._with_lean_v1_paper_difficulty(
        json.loads(
            Path("benchmarks/paper/lean_catalog.v1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
    )
    environment = lean_adapter_module.default_lean_paper_environment_manifest()
    object.__setattr__(environment, "environment_digest", case["environment_digest"])
    monkeypatch.setattr(
        lean_adapter_module,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    condition = replace(
        _condition_for_case(CATALOG_DIGEST, case),
        model_cohort_id=identity.model_cohort_id,
        model_cohort_digest=identity.model_cohort_digest,
        cohort_member_id=identity.cohort_member_id,
        provider_config_id=identity.provider_config_id,
        model_entry_id=identity.selected_entry_id,
        provider_family=identity.provider_family,
        provider_model_id=identity.provider_model_id,
        reasoning_profile_id=identity.reasoning_profile_id,
        source_provider_config_digest=identity.source_provider_config_digest,
        model_endpoint_identity_digest=identity.model_endpoint_identity_digest,
    )
    expected_ai_unit_count = int(case["expected_child_count"])
    selection = FrozenCaseSelection(
        selection_id="selection-real-lean-full",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="lean_proof",
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=condition.topic_family,
        ordered_case_ids=(str(case["case_id"]),),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )
    plan = _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(tmp_path / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    checker = RecordingLeanChecker()
    adapter_results: list[object] = []
    real_dispatch = formal_runner.dispatch_paper_case

    def dispatch_with_recording_checker(**kwargs):
        result = real_dispatch(**kwargs, checker=checker)
        adapter_results.append(result)
        return result

    _install_single_case_execution(
        monkeypatch=monkeypatch,
        dispatch=dispatch_with_recording_checker,
    )

    transport = UrlLibSiliconFlowTransport()
    scripted_transport = ScriptedLeanPaperProofTransport()

    def local_chat_completion(**call_kwargs):
        response = scripted_transport.post_chat_completion(**call_kwargs)
        response.body["model"] = json.loads(
            call_kwargs["body_bytes"].decode("utf-8")
        )["model"]
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response

    transport.post_chat_completion = local_chat_completion

    def forbid_publication_capture(**_kwargs):
        raise AssertionError("default formal execution must skip publication closure")

    monkeypatch.setattr(
        formal_runner,
        "_capture_canonical_direct_evidence",
        forbid_publication_capture,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    kwargs.update(
        {
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (),
                "lean_cases": (case,),
                "lean_lemma_graph_cases": (),
            },
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=expected_ai_unit_count,
                ),
                *plan.bound_items(),
            ),
            "transport": transport,
            "real_transport": True,
        }
    )

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status is PaperStatus.COMPLETED
    assert len(adapter_results) == 1
    adapter_result = adapter_results[0]
    assert adapter_result.task_result.root_status is PaperTaskStatus.COMPLETED
    assert adapter_result.task_result.accepted_validity is True
    assert len(checker.requests) == expected_ai_unit_count + 1
    attempts = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert len(attempts) == expected_ai_unit_count
    assert suite.provider_attempt_count == expected_ai_unit_count
    assert suite.total_tokens == sum(int(row["total_tokens"]) for row in attempts)
    assert suite.total_cost_estimate == pytest.approx(
        sum(float(row["cost_estimate"]) for row in attempts)
    )
    assert all(float(row["latency_ms"]) >= 0 for row in attempts)
    assert all(int(row["prompt_tokens"]) > 0 for row in attempts)
    assert all(int(row["completion_tokens"]) > 0 for row in attempts)
    assert all(
        int(row["total_tokens"])
        == int(row["prompt_tokens"]) + int(row["completion_tokens"])
        for row in attempts
    )


def test_offline_capturing_is_regression_only_and_direct_capture_is_native_only() -> None:
    import inspect

    capturing = SimpleNamespace(tokenshare_offline_capturing_transport=True)

    assert formal_runner._direct_evidence_class(
        real_transport=True,
        transport=capturing,
        trace_context=None,
    ) == "regression_only"
    assert formal_runner._direct_evidence_class(
        real_transport=False,
        transport=capturing,
        trace_context=object(),
    ) == "regression_only"
    capture_source = inspect.getsource(
        formal_runner._capture_canonical_direct_evidence
    )
    assert "_save_direct_role_artifact" not in capture_source
    assert "accepted_validity" not in capture_source


def test_formal_runner_offline_capturing_cannot_persist_online_direct_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    config = _ai_config()
    monkeypatch.setenv("TOKENSHARE_FORMAL_RUNNER_TEST_KEY", "offline-capture-key")
    case = generate_factorization_paper_cases()[0]
    expected_ai_unit_count = int(case["split_params"]["requested_child_count"])
    condition = replace(
        _v2_condition(case),
        condition_id="condition-canonical-direct-closure",
        worker_count=1,
        catalog_digest=CATALOG_DIGEST,
        provider_config_id=PROVIDER_CONFIG_ID,
        model_entry_id=MODEL_ENTRY_ID,
        provider_family="siliconflow",
        provider_model_id=PROVIDER_MODEL_ID,
        reasoning_profile_id="default",
        source_provider_config_digest=config.config_digest,
        model_endpoint_identity_digest=ENDPOINT_DIGEST,
    )
    selection = FrozenCaseSelection(
        selection_id="selection-canonical-direct-closure",
        experiment_id=EXPERIMENT_ID,
        suite_version="paper_v1",
        catalog_version="catalog-v1",
        domain="factorization",
        paper_difficulty=str(case["difficulty"]),
        topic_family=None,
        ordered_case_ids=(str(case["case_id"]),),
        catalog_digest=CATALOG_DIGEST,
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )
    plan = _dispatch_plan_type()(
        experiment_id=EXPERIMENT_ID,
        output_root=(tmp_path / EXPERIMENT_ID).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    class DirectClosureModule:
        def expand_conditions(self, context):
            raise AssertionError("direct closure consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, DirectClosureModule()),),
    )
    class CapturingTransport:
        tokenshare_offline_capturing_transport = True

        def __init__(self) -> None:
            self.delegate = ScriptedFactorizationRangeTransport()

        def post_chat_completion(self, **call_kwargs):
            response = self.delegate.post_chat_completion(**call_kwargs)
            response.body["model"] = json.loads(
                call_kwargs["body_bytes"].decode("utf-8")
            )["model"]
            response.text = json.dumps(response.body, ensure_ascii=False)
            return response

    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)
    kwargs.update(
        {
            "catalog_manifest": {
                "catalog_digest": CATALOG_DIGEST,
                "factorization_cases": (case,),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_conditions=1,
                    planned_root_runs=1,
                    planned_ai_units=expected_ai_unit_count,
                ),
                *plan.bound_items(),
            ),
            "transport": CapturingTransport(),
        }
    )

    suite = formal_runner.execute_paper_formal_suite(**kwargs)
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite.status is PaperStatus.COMPLETED
    assert "traceability_replay_input_root_ref" not in suite_manifest
    with pytest.raises(ValueError, match="no traceability replay input root"):
        formal_runner.load_paper_traceability_replay_input_root(tmp_path)
    assert not tmp_path.with_name(
        tmp_path.name + ".canonical_direct_evidence"
    ).exists()


def _disk_test_budget() -> PaperBudgetResult:
    return _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )


def test_formal_disk_preflight_allows_exact_available_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    condition_compaction_bytes = int(
        budget.disk_estimate["max_condition_compaction_bytes"]
    )
    headroom_bytes = max((estimate_bytes + 3) // 4, 2 * 1024**3)
    required = estimate_bytes + headroom_bytes + condition_compaction_bytes
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=tmp_path / "new-suite",
        budget=budget,
        resume=False,
    )

    assert details["required_bytes"] == required
    assert details["available_bytes"] == required
    assert details["headroom_bytes"] == headroom_bytes
    assert details["condition_compaction_bytes"] == condition_compaction_bytes


def test_formal_disk_preflight_blocks_one_byte_short_before_evidence_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "disk-blocked-suite"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    condition_compaction_bytes = int(
        budget.disk_estimate["max_condition_compaction_bytes"]
    )
    headroom_bytes = max((estimate_bytes + 3) // 4, 2 * 1024**3)
    required = estimate_bytes + headroom_bytes + condition_compaction_bytes
    kwargs["budget"] = budget
    calls = {"evidence": 0, "provider": 0}

    def forbidden_initialize(**_kwargs: object) -> object:
        calls["evidence"] += 1
        raise AssertionError("evidence initialize must follow disk preflight")

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            calls["provider"] += 1
            raise AssertionError("provider must follow disk preflight")

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "initialize",
        forbidden_initialize,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=required,
            used=1,
            free=required - 1,
        ),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="formal disk preflight failed",
    ) as captured:
        formal_runner.execute_paper_formal_suite(**kwargs)

    summary = captured.value.to_summary()
    assert summary["failure_stage"] == "disk_preflight"
    assert summary["failure_kind"] == "insufficient_disk_capacity"
    assert summary["resource_diagnostics"]["required_bytes"] == required
    assert summary["resource_diagnostics"]["available_bytes"] == required - 1
    assert summary["resource_diagnostics"]["components"] == {
        **budget.disk_estimate["components"],
        "condition_compaction_bytes": condition_compaction_bytes,
        "headroom_bytes": headroom_bytes,
    }
    assert calls == {"evidence": 0, "provider": 0}
    assert not suite_root.exists()


def test_formal_rolling_root_disk_guard_allows_exact_capacity_and_blocks_one_short(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=6,
    )
    case = {"case_id": "case-1", "expected_ai_unit_count": 2}
    request_limits = {"max_provider_attempts": 3, "max_tokens": 100_000}
    policy = budget.disk_estimate["policy"]
    tracker = formal_runner._RollingDiskForecast(
        remaining_root_runs=1,
        remaining_ai_units=2,
        remaining_provider_attempts=6,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    forecast = int(budget.disk_estimate["forecast_bytes"])
    headroom = max((forecast + 3) // 4, 2 * 1024**3)
    theoretical = 2 * 3 * 100_000 * int(policy["utf8_bytes_per_token"])
    forecast_payload = 6 * int(policy["p95_provider_attempt_payload_bytes"])
    theoretical_over_forecast = max(0, theoretical - forecast_payload)
    expected = (
        forecast
        + headroom
        + int(budget.disk_estimate["max_condition_compaction_bytes"])
        + theoretical_over_forecast
    )
    available = iter((expected, expected - 1))
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=expected, used=0, free=next(available)),
    )

    details = formal_runner._preflight_formal_root_capacity(
        output_root=tmp_path / "rolling-root",
        condition=SimpleNamespace(
            condition_id="condition-1",
            experiment_id="exp1_real_ai_feasibility",
        ),
        task_id="case-1",
        case=case,
        request_limits=request_limits,
        rolling_forecast=tracker,
    )

    assert details["required_bytes"] == expected
    assert details["available_bytes"] == expected
    assert details["theoretical_response_bytes"] == theoretical
    assert details["theoretical_over_forecast_bytes"] == theoretical_over_forecast
    assert details["remaining_forecast_bytes"] == forecast
    assert details["headroom_bytes"] == headroom
    assert tracker.remaining_root_runs == 0
    assert tracker.remaining_ai_units == 0
    assert tracker.remaining_provider_attempts == 0
    assert tracker.in_flight_forecast_bytes == details["root_reservation_bytes"]
    tracker.consume_root_forecast(
        root_reservation_bytes=int(details["root_reservation_bytes"])
    )
    assert tracker.in_flight_forecast_bytes == 0
    tracker_short = formal_runner._RollingDiskForecast(
        remaining_root_runs=1,
        remaining_ai_units=2,
        remaining_provider_attempts=6,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="rolling root disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_root_capacity(
            output_root=tmp_path / "rolling-root",
            condition=SimpleNamespace(
                condition_id="condition-1",
                experiment_id="exp1_real_ai_feasibility",
            ),
            task_id="case-1",
            case=case,
            request_limits=request_limits,
            rolling_forecast=tracker_short,
        )
    summary = captured.value.to_summary()
    assert summary["failure_kind"] == "insufficient_rolling_root_capacity"
    assert summary["condition_id"] == "condition-1"
    assert summary["task_id"] == "case-1"
    assert summary["resource_diagnostics"]["available_bytes"] == expected - 1
    assert tracker_short.remaining_root_runs == 1
    assert tracker_short.remaining_ai_units == 2
    assert tracker_short.remaining_provider_attempts == 6
    assert tracker_short.in_flight_forecast_bytes == 0


def test_formal_condition_compaction_guard_has_exact_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reachable = 3 * 1024**3
    required = reachable + 2 * 1024**3
    available = iter((required, required - 1))
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=next(available)),
    )

    details = formal_runner._preflight_formal_condition_compaction_capacity(
        output_root=tmp_path / "compaction-root",
        condition_id="condition-1",
        current_condition_reachable_bytes=reachable,
    )

    assert details["required_bytes"] == required
    assert details["available_bytes"] == required
    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="condition compaction disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_condition_compaction_capacity(
            output_root=tmp_path / "compaction-root",
            condition_id="condition-1",
            current_condition_reachable_bytes=reachable,
        )
    assert captured.value.to_summary()["failure_kind"] == (
        "insufficient_condition_compaction_capacity"
    )


def test_compaction_guard_resume_reuses_nonempty_tail_without_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, _selection = plan.bound_items()[0]
    adapter_calls: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("formal execution consumes the frozen plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        adapter_calls.append(kwargs["case"]["case_id"])
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    original_schedule = formal_runner.run_scheduled_cases

    def scheduled_with_terminal_event(**kwargs):
        result = original_schedule(**kwargs)
        if result.ordered_case_ids:
            return replace(
                result,
                events=(
                    {
                        "experiment_id": condition.experiment_id,
                        "condition_id": condition.condition_id,
                        "repeat_id": condition.repeat_id,
                        "task_id": "case-1",
                        "event_id": "merge-gate-condition-1",
                        "event_type": "MERGE_GATE_COMPLETED",
                    },
                ),
            )
        return result

    guard_calls = 0

    def fail_then_allow_guard(**_kwargs):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 1:
            raise formal_runner.PaperInfrastructureBlockedError(
                "synthetic compaction guard failure",
                evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
                failure_stage="condition_compaction",
                failure_kind="insufficient_condition_compaction_capacity",
                condition_id=condition.condition_id,
            )
        return {"required_bytes": 1, "available_bytes": 1}

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    monkeypatch.setattr(formal_runner, "run_scheduled_cases", scheduled_with_terminal_event)
    monkeypatch.setattr(
        formal_runner,
        "_preflight_formal_condition_compaction_capacity",
        fail_then_allow_guard,
    )
    kwargs = _formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan)

    first = formal_runner.execute_paper_formal_suite(**kwargs)

    assert first.status == PaperStatus.BLOCKED
    assert adapter_calls == ["case-1"]
    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / condition.condition_id
        / "0"
    )
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    tail_root = run_root / ".generations" / current["generation_id"]
    tail_manifest = json.loads(
        (tail_root / "generation_manifest.json").read_text(encoding="utf-8")
    )
    assert tail_manifest["delta_role"] == "condition_tail_events"
    assert "MERGE_GATE_COMPLETED" in (
        tail_root / "events" / "event_log.jsonl"
    ).read_text(encoding="utf-8")
    parent_ids = {path.name for path in (run_root / ".generations").iterdir()}
    assert len(parent_ids) == 2

    resumed = formal_runner.execute_paper_formal_suite(**kwargs, resume=True)

    assert resumed.status == PaperStatus.COMPLETED
    assert adapter_calls == ["case-1"]
    assert guard_calls == 2
    current = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
    snapshot_root = run_root / ".generations" / current["generation_id"]
    snapshot_manifest = json.loads(
        (snapshot_root / "generation_manifest.json").read_text(encoding="utf-8")
    )
    assert snapshot_manifest["generation_kind"] == "snapshot"
    assert {path.name for path in (run_root / ".generations").iterdir()} == {
        current["generation_id"]
    }


def test_formal_rolling_guard_counts_prior_in_flight_theoretical_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=2,
        planned_ai_units=2,
        max_provider_attempts=2,
    )
    policy = budget.disk_estimate["policy"]
    tracker = formal_runner._RollingDiskForecast(
        remaining_root_runs=2,
        remaining_ai_units=2,
        remaining_provider_attempts=2,
        fixed_forecast_bytes=(
            int(policy["fixed_manifest_bytes"])
            + int(policy["fixed_temp_bytes"])
        ),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
        policy=policy,
    )
    free = {"bytes": 10**15}
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=10**15,
            used=0,
            free=free["bytes"],
        ),
    )
    condition = SimpleNamespace(
        condition_id="condition-1",
        experiment_id="exp1_real_ai_feasibility",
    )
    first = formal_runner._preflight_formal_root_capacity(
        output_root=tmp_path,
        condition=condition,
        task_id="case-1",
        case={
            "case_id": "case-1",
            "difficulty": "easy",
            "paper_difficulty": "easy",
            "expected_ai_unit_count": 1,
        },
        request_limits={"max_provider_attempts": 1, "max_tokens": 100_000},
        rolling_forecast=tracker,
    )
    assert first["theoretical_over_forecast_bytes"] > 0
    assert tracker.in_flight_forecast_bytes == first["root_reservation_bytes"]
    free["bytes"] = int(first["required_bytes"])

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="rolling root disk guard failed",
    ) as captured:
        formal_runner._preflight_formal_root_capacity(
            output_root=tmp_path,
            condition=condition,
            task_id="case-2",
            case={"case_id": "case-2", "expected_ai_unit_count": 1},
            request_limits={
                "max_provider_attempts": 1,
                "max_tokens": 100_000,
            },
            rolling_forecast=tracker,
        )

    diagnostics = captured.value.to_summary()["resource_diagnostics"]
    assert diagnostics["required_bytes"] > first["required_bytes"]
    assert diagnostics["remaining_forecast_bytes"] > (
        first["remaining_forecast_bytes"] - first["root_forecast_bytes"]
    )


def test_formal_rolling_root_guard_blocks_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "rolling-provider-blocked"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    disk_checks = {"count": 0}
    provider_calls = {"count": 0}

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            provider_calls["count"] += 1
            raise AssertionError("provider dispatch must follow rolling disk guard")

    def disk_usage(_path: object) -> SimpleNamespace:
        disk_checks["count"] += 1
        free = 10**15 if disk_checks["count"] == 1 else 0
        return SimpleNamespace(total=10**15, used=0, free=free)

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(formal_runner.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_condition",
        lambda *, context, plan, condition_id: context.execution_callback(
            condition=plan.conditions[0],
            selection=plan.condition_selection_bindings[0].selection,
        ),
    )
    monkeypatch.setattr(
        formal_runner,
        "_checkpoint_dependency_outcome",
        lambda **_kwargs: pytest.fail(
            "disk-resource closure must not checkpoint remaining roots"
        ),
    )

    result = formal_runner.execute_paper_formal_suite(**kwargs)

    assert result.status == PaperStatus.BLOCKED
    assert result.error_summary[0]["failure_kind"] == (
        "insufficient_rolling_root_capacity"
    )
    assert provider_calls["count"] == 0
    assert disk_checks["count"] >= 2
    marker = json.loads(
        (suite_root / "infrastructure_blocked.json").read_text(encoding="utf-8")
    )
    assert marker["remaining_task_count"] == 1
    assert marker["failure"]["failure_kind"] == (
        "insufficient_rolling_root_capacity"
    )


def test_disk_resource_block_closure_writes_one_bounded_marker_for_50k_remaining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    condition = SimpleNamespace(
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-50k",
        repeat_id=0,
    )
    selection = SimpleNamespace(
        ordered_case_ids=tuple(f"case-{index:05d}" for index in range(50_000))
    )
    plan = SimpleNamespace(experiment_id=EXPERIMENT_ID, status="planned")
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(
        formal_runner,
        "_atomic_write_json",
        lambda _path, body: writes.append(dict(body)),
    )
    monkeypatch.setattr(
        formal_runner,
        "_checkpoint_dependency_outcome",
        lambda **_kwargs: pytest.fail("disk closure must not write per-root evidence"),
    )
    blocked_error = formal_runner.PaperInfrastructureBlockedError(
        "rolling capacity exhausted",
        evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
        failure_stage="disk_preflight",
        failure_kind="insufficient_rolling_root_capacity",
        condition_id=condition.condition_id,
        task_id="case-00000",
        diagnostics={"required_bytes": 2, "available_bytes": 1},
    )

    result = formal_runner._close_disk_resource_blocked_suite(
        suite_root=tmp_path,
        suite_id="suite-50k",
        started_at="2026-07-31T00:00:00Z",
        plans=(plan,),
        bound_plans=((plan, ((condition, selection),)),),
        condition_results=(),
        blocked_error=blocked_error,
        root_case_filter={},
        usage=formal_runner._UsageTotals(),
        budget=_budget(
            planned_conditions=1,
            planned_root_runs=50_000,
            planned_ai_units=50_000,
        ),
        budget_approval={"approval_mode": "user_bypassed"},
        completed_task_keys=set(),
    )

    assert result.status == PaperStatus.BLOCKED
    assert result.task_count == 50_000
    assert len(writes) == 1
    assert writes[0]["remaining_task_count"] == 50_000
    assert len(json.dumps(writes[0])) < 5_000


def test_formal_disk_preflight_resume_conservatively_keeps_full_forecast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "resume-suite"
    suite_root.mkdir()
    budget = _disk_test_budget()
    estimate_bytes = int(budget.disk_estimate["forecast_bytes"])
    reachable_bytes = estimate_bytes + 500
    (suite_root / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v2",
                "static_files": [{"path": "suite_manifest.json", "size": 700}],
                "conditions": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-1",
                        "repeat_id": 0,
                        "condition_manifest_ref": {
                            "path": "condition_manifest.json",
                            "size": 100,
                        },
                        "reachable_size_bytes": reachable_bytes,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    two_gib = 2 * 1024**3
    headroom = max((estimate_bytes + 3) // 4, two_gib)
    required = estimate_bytes + headroom + reachable_bytes
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=suite_root,
        budget=budget,
        resume=True,
    )

    assert details["existing_canonical_bytes"] == 700 + reachable_bytes
    assert details["remaining_forecast_bytes"] == estimate_bytes
    assert details["headroom_bytes"] == headroom
    assert details["condition_compaction_bytes"] == reachable_bytes
    assert details["required_bytes"] == required


def test_formal_disk_preflight_resume_includes_largest_condition_cow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_root = tmp_path / "resume-cow-suite"
    suite_root.mkdir()
    three_gib = 3 * 1024**3
    (suite_root / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v2",
                "static_files": [],
                "conditions": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": "condition-large",
                        "repeat_id": 0,
                        "condition_manifest_ref": {
                            "path": "condition_manifest.json",
                            "size": 100,
                        },
                        "reachable_size_bytes": three_gib,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    forecast = int(_disk_test_budget().disk_estimate["forecast_bytes"])
    headroom = max((forecast + 3) // 4, 2 * 1024**3)
    required = forecast + headroom + three_gib
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=required, used=0, free=required),
    )

    details = formal_runner._preflight_formal_disk_capacity(
        output_root=suite_root,
        budget=_disk_test_budget(),
        resume=True,
    )

    assert details["remaining_forecast_bytes"] == int(
        _disk_test_budget().disk_estimate["forecast_bytes"]
    )
    assert details["condition_compaction_bytes"] == three_gib
    assert details["headroom_bytes"] == 2 * 1024**3
    assert details["required_bytes"] == required


def test_formal_replay_only_skips_disk_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SimpleNamespace(status="replayed")
    monkeypatch.setattr(
        formal_runner,
        "replay_paper_formal_suite",
        lambda *, output_root: expected,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: pytest.fail("replay must skip disk preflight"),
    )

    result = formal_runner.execute_paper_formal_suite(
        dispatch_plans=(),
        catalog_manifest={},
        budget=_disk_test_budget(),
        budget_approval={},
        output_root=tmp_path,
        ai_api_configs={},
        transport=object(),
        real_transport=False,
        hard_limits={},
        replay_only=True,
    )

    assert result is expected


@pytest.mark.parametrize(
    "drift_kind",
    (
        "schema",
        "forecast_sum",
        "policy",
        "input_count",
        "negative_component",
        "theoretical_payload",
        "token_identity",
        "missing_token_identity",
        "extra_top_level",
    ),
)
def test_formal_disk_preflight_rejects_estimate_drift_before_evidence_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    suite_root = tmp_path / f"disk-drift-{drift_kind}"
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    budget = _disk_test_budget()
    estimate = json.loads(json.dumps(budget.disk_estimate))
    if drift_kind == "schema":
        estimate["schema_version"] = "tokenshare.paper_disk_estimate.v1"
    elif drift_kind == "forecast_sum":
        estimate["forecast_bytes"] = 0
    elif drift_kind == "policy":
        estimate["policy"]["utf8_bytes_per_token"] = 3
    elif drift_kind == "input_count":
        estimate["inputs"]["planned_root_runs"] = 2
    elif drift_kind == "negative_component":
        estimate["components"]["forecast_provider_attempt_payload_bytes"] = -1
    elif drift_kind == "theoretical_payload":
        estimate["theoretical_max_payload_bytes"] = 0
    elif drift_kind == "token_identity":
        quota = json.loads(json.dumps(budget.quota_preflight))
        quota["budget_commitments"]["disk_estimate_authority"][
            "token_upper_bound_per_provider_attempt"
        ] = 2048
        budget = replace(budget, quota_preflight=quota)
    elif drift_kind == "missing_token_identity":
        quota = json.loads(json.dumps(budget.quota_preflight))
        del quota["budget_commitments"]["disk_estimate_authority"]
        budget = replace(budget, quota_preflight=quota)
    elif drift_kind == "extra_top_level":
        estimate["unexpected"] = True
    kwargs["budget"] = replace(budget, disk_estimate=estimate)
    calls = {"evidence": 0, "provider": 0}

    def forbidden_initialize(**_kwargs: object) -> object:
        calls["evidence"] += 1
        raise AssertionError("invalid estimate reached EvidenceStore")

    class ForbiddenTransport:
        def complete(self, **_kwargs: object) -> object:
            calls["provider"] += 1
            raise AssertionError("invalid estimate reached provider")

    kwargs["transport"] = ForbiddenTransport()
    monkeypatch.setattr(
        formal_runner.FormalEvidenceStore,
        "initialize",
        forbidden_initialize,
    )
    monkeypatch.setattr(
        formal_runner.shutil,
        "disk_usage",
        lambda _path: pytest.fail("invalid estimate reached disk usage"),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="formal disk estimate is invalid",
    ) as captured:
        formal_runner.execute_paper_formal_suite(**kwargs)

    assert captured.value.to_summary()["failure_stage"] == "disk_preflight"
    assert calls == {"evidence": 0, "provider": 0}
    assert not suite_root.exists()


def _normalized_exp5_request_controls(
    config: AIAPIExecutorConfig,
) -> dict[str, object]:
    effective = {
        **config.defaults,
        **config.entries[0].request_overrides,
    }
    comparable = {
        field_name: effective[field_name]
        for field_name in formal_runner.EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS
    }
    comparable["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in (
            formal_runner.EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
        )
    }
    reasoning = {
        field_name: effective[field_name]
        for field_name in (
            "enable_thinking",
            "thinking_budget",
            "thinking",
            "reasoning_effort",
        )
        if field_name in effective
    }
    return {
        "schema_version": "tokenshare.paper_exp5_request_controls.v1",
        "comparable": comparable,
        "comparable_digest": formal_runner.digest_json(comparable),
        "provider_specific_reasoning": reasoning,
        "provider_specific_reasoning_digest": formal_runner.digest_json(
            reasoning
        ),
    }


def _exp5_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
    resolved_model: str,
) -> SimpleNamespace:
    adapter_parent = output_root
    output_root = output_root / case_id
    base = _complete_adapter_result(
        output_root=adapter_parent,
        condition=condition,
        case_id=case_id,
    )
    store = ArtifactStore(output_root)
    created_at = "2026-07-20T00:00:00Z"
    request_ref = store.save_json(
        {"model": PROVIDER_MODEL_ID},
        artifact_id="exp5-request.json",
        artifact_type="AIRequest",
        artifact_schema_id="test.exp5.request",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    raw_body = {
        "schema_version": "phase7.raw_model_output.v2",
        "provider_family": "siliconflow",
        "entry_id": MODEL_ENTRY_ID,
        "configured_model": PROVIDER_MODEL_ID,
        "requested_model": PROVIDER_MODEL_ID,
        "resolved_model": resolved_model,
        "response_model_status": "present",
        "raw_response_json": {"model": resolved_model},
    }
    raw_ref = store.save_json(
        raw_body,
        artifact_id="exp5-raw.json",
        artifact_type="RawModelOutput",
        artifact_schema_id="phase7.raw_model_output",
        artifact_schema_version="v2",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    provenance_ref = store.save_json(
        {
            "provider_family": "siliconflow",
            "entry_id": MODEL_ENTRY_ID,
            "configured_model": PROVIDER_MODEL_ID,
        },
        artifact_id="exp5-provenance.json",
        artifact_type="AIProviderCallProvenance",
        artifact_schema_id="test.exp5.provenance",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    usage_ref = store.save_json(
        {"total_tokens": 11, "cost_estimate": 0.125},
        artifact_id="exp5-usage.json",
        artifact_type="AIUsage",
        artifact_schema_id="test.exp5.usage",
        artifact_schema_version="v1",
        source={"test": "formal-runner-exp5"},
        metadata={},
        created_at=created_at,
    )
    expected_identity = {
        "model_cohort_id": MODEL_COHORT_ID,
        "model_cohort_digest": MODEL_COHORT_DIGEST,
        "cohort_member_id": COHORT_MEMBER_ID,
        "provider_config_id": PROVIDER_CONFIG_ID,
        "selected_entry_id": MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": PROVIDER_MODEL_ID,
        "reasoning_profile_id": "default",
        "effective_reasoning_controls": {"enable_thinking": False},
    }
    record = PaperModelExecutionRecord(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}",
        expected_identity=expected_identity,
        source_provider_config_digest="sha256:" + "5" * 64,
        prepared_execution_config_digest="sha256:" + "6" * 64,
        request_ref=request_ref.to_dict(),
        provenance_ref=provenance_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        actual_request_identities=[
            {
                "schema_version": "phase7.provider_request_identity.v2",
                "provider_family": "siliconflow",
                "entry_id": MODEL_ENTRY_ID,
                "configured_model": PROVIDER_MODEL_ID,
                "requested_model": PROVIDER_MODEL_ID,
                "reasoning_controls": {"enable_thinking": False},
                "effective_request_controls_digest": "sha256:" + "7" * 64,
            }
        ],
        actual_provider_attempts=[
            {
                "provider_family": "siliconflow",
                "entry_id": MODEL_ENTRY_ID,
                "configured_model": PROVIDER_MODEL_ID,
                "result_kind": "succeeded",
            }
        ],
        requested_model=PROVIDER_MODEL_ID,
        resolved_model=resolved_model,
        response_model_status="present",
        identity_status="model_identity_mismatch",
        mismatch_reasons=["resolved_model_mismatch"],
        paper_eligible=False,
        created_at=created_at,
    )
    model_ref = store.save_json(
        record.to_dict(),
        artifact_id="exp5-model-execution.json",
        artifact_type="PaperModelExecutionRecord",
        artifact_schema_id="tokenshare.paper_model_execution_record",
        artifact_schema_version="v2",
        source={"test": "formal-runner-exp5"},
        metadata={"identity_status": record.identity_status},
        created_at=created_at,
    )
    original_attempt = base.attempt_results[0]
    attempt = replace(
        original_attempt,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        provenance_ref=provenance_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        model_execution_record_ref=model_ref.to_dict(),
        provider_attempt_count=1,
    )
    task = SimpleNamespace(
        **{
            **vars(base.task_result),
            "paper_eligible": False,
            "artifact_refs": [
                request_ref.to_dict(),
                raw_ref.to_dict(),
                provenance_ref.to_dict(),
                usage_ref.to_dict(),
                model_ref.to_dict(),
            ],
        }
    )
    return SimpleNamespace(
        **{
            **vars(base),
            "task_result": task,
            "attempt_results": [attempt],
            "output_root": output_root.as_posix(),
        }
    )


def _complete_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    output_root = output_root / case_id
    adapter_store = ArtifactStore(output_root)
    raw_ref = adapter_store.save_bytes(
        b"raw model output",
        artifact_id="raw-output.txt",
        artifact_type="raw_model_output",
        media_type="text/plain",
        artifact_schema_id="test.raw_output",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    request_ref = adapter_store.save_bytes(
        b'{"request":"body"}',
        artifact_id="request.json",
        artifact_type="provider_request",
        media_type="application/json",
        artifact_schema_id="test.request",
        artifact_schema_version="v1",
        source={"test": "formal-runner"},
        metadata={},
        created_at="2026-07-20T00:00:00Z",
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}",
        worker_id="worker-1",
        provider_attempt_index=1,
        provider_attempt_count=1,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-20T00:00:00Z",
        ended_at="2026-07-20T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[raw_ref.to_dict(), request_ref.to_dict()],
        event_refs=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[{"event_id": f"event-{case_id}", "event_type": "TASK_COMPLETED"}],
        eligibility_report=SimpleNamespace(paper_eligible=False),
        run_evidence={
            "protocol_runtime": {
                "case_id": case_id,
                "factor_position_quantile": "early",
                "runtime_observation": {
                    "schema_version": "tokenshare.protocol_runtime_observation.v1",
                    "run_id": f"run-{case_id}",
                    "runtime_started_at": "2026-07-20T00:00:00Z",
                    "runtime_ended_at": "2026-07-20T00:00:01Z",
                    "runtime_wall_clock_ms": 1000,
                    "planned_ai_unit_ids": ["range_0"],
                    "dispatched_ai_unit_ids": ["range_0"],
                    "completed_ai_unit_ids": ["range_0"],
                    "unscheduled_ai_unit_ids": [],
                    "in_flight_ai_unit_ids_at_witness": [],
                    "witness_observed_at": "2026-07-20T00:00:01Z",
                    "worker_execution_facts": [],
                    "observed_peak_concurrency": 1,
                },
                "generation_identity": {
                    "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
                    "run_id": f"run-{case_id}",
                    "task_id": case_id,
                    "root_unit_id": f"root-{case_id}",
                    "event_count": 1,
                    "first_event_id": f"event-{case_id}",
                    "last_event_id": f"event-{case_id}",
                    "last_event_hash": f"sha256:event-{case_id}",
                    "ledger_digest": f"sha256:ledger-{case_id}",
                }
            }
        },
        output_root=output_root.as_posix(),
    )


def test_formal_runner_builds_online_callback_once_for_dispatched_pending_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _plan_for_experiment(
        tmp_path=tmp_path,
        config=config,
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-online-callback",
    )
    factory_calls: list[tuple[str, str]] = []
    hook_events: list[str] = []

    class OnlineHook:
        def after_prepared_dispatch(self, **_context):
            hook_events.append("prepared")

        def __call__(self, **_context):
            hook_events.append("raw")

    def factory(**context):
        factory_calls.append((context["condition"].condition_id, context["case_id"]))
        return OnlineHook()

    def dispatch(**kwargs):
        hook = kwargs["post_raw_output_hook"]
        hook.after_prepared_dispatch(submission_id="submission-1")
        assert hook(submission_id="submission-1") is None
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    condition, selection = plan.bound_items()[0]
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=SimpleNamespace(output_root=tmp_path),
        completed_task_keys=set(),
        usage=formal_runner._UsageTotals(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
        online_root_callback_factory=factory,
    )
    outcome = callback._dispatch_root_case(
        condition=condition,
        selection=selection,
        case_id="case-1",
        case={
            "case_id": "case-1",
            "difficulty": "easy",
            "paper_difficulty": "easy",
            "expected_ai_unit_count": 1,
        },
        worker_id="worker-1",
        callback_kwargs={},
    )
    assert outcome.case_id == "case-1"
    assert factory_calls == [("condition-online-callback", "case-1")]
    assert hook_events == ["prepared", "raw"]


def _budget_fitting_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    result = _complete_adapter_result(
        output_root=output_root,
        condition=condition,
        case_id=case_id,
    )
    return SimpleNamespace(
        **{
            **vars(result),
            "task_result": SimpleNamespace(
                **{**vars(result.task_result), "cost_estimate": 0.005}
            ),
            "attempt_results": [
                replace(result.attempt_results[0], cost_estimate=0.005)
            ],
        }
    )


def _adapter_root_binding_probe(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_id: str,
    domain: str,
    dispatch,
) -> tuple[formal_runner._FormalConditionExecutionCallback, object, object, dict[str, object]]:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    paper_difficulty = "simple" if domain == "lean_proof" else "easy"
    condition = replace(
        condition,
        experiment_id=experiment_id,
        condition_id=f"{experiment_id}-{domain}-output-root",
        domain=domain,
        paper_difficulty=paper_difficulty,
        topic_family=("pure_logic" if domain == "lean_proof" else None),
        topic_family_version=None,
        model_cohort_id=(
            MODEL_COHORT_ID if experiment_id == EXP5_EXPERIMENT_ID else None
        ),
        model_cohort_digest=(
            MODEL_COHORT_DIGEST if experiment_id == EXP5_EXPERIMENT_ID else None
        ),
        cohort_member_id=(
            COHORT_MEMBER_ID if experiment_id == EXP5_EXPERIMENT_ID else None
        ),
    )
    selection = replace(
        selection,
        experiment_id=experiment_id,
        domain=domain,
        paper_difficulty=paper_difficulty,
        topic_family=("pure_logic" if domain == "lean_proof" else None),
    )
    case: dict[str, object] = {
        "case_id": "case-1",
        "difficulty": "easy",
        "paper_difficulty": paper_difficulty,
        "expected_ai_unit_count": 1,
    }
    if domain == "lean_proof":
        case.update(
            {
                "topic_family": "pure_logic",
                "topic_family_version": "v1",
                "construction_rule_id": None,
                "oracle_package_group": None,
                "proof_assembly_shape": None,
            }
        )
    budget = _budget(
        planned_experiments=(experiment_id,),
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
    )
    callback = formal_runner._FormalConditionExecutionCallback(
        catalog_manifest={},
        config=config,
        transport=object(),
        real_transport=False,
        output_root=Path(plan.output_root),
        request_limits=config.defaults,
        evidence_store=SimpleNamespace(output_root=tmp_path),
        completed_task_keys=set(),
        usage=formal_runner._UsageTotals(),
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={},
        root_case_ids=None,
        execution_classification=None,
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", dispatch)
    return callback, condition, selection, case


@pytest.mark.parametrize(
    ("experiment_id", "domain"),
    (
        (EXPERIMENT_ID, "factorization"),
        (EXPERIMENT_ID, "lean_proof"),
        (EXP5_EXPERIMENT_ID, "factorization"),
        (EXP5_EXPERIMENT_ID, "lean_proof"),
    ),
)
def test_formal_runner_binds_adapter_output_root_before_all_experiment_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_id: str,
    domain: str,
) -> None:
    def dispatch(**kwargs):
        case_id = str(kwargs["case"]["case_id"])
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=experiment_id,
        domain=domain,
        dispatch=dispatch,
    )

    outcome = callback._dispatch_root_case(
        condition=condition,
        selection=selection,
        case_id="case-1",
        case=case,
        worker_id="worker-root-binding",
        callback_kwargs={},
    )

    assert outcome.adapter_output_root == (
        outcome.adapter_root / outcome.case_id
    ).resolve(strict=False)


@pytest.mark.parametrize(
    ("experiment_id", "domain"),
    (
        (EXPERIMENT_ID, "factorization"),
        (EXPERIMENT_ID, "lean_proof"),
        (EXP5_EXPERIMENT_ID, "factorization"),
        (EXP5_EXPERIMENT_ID, "lean_proof"),
    ),
)
@pytest.mark.parametrize("root_drift", ("wrong", "missing", "nested"))
def test_formal_runner_rejects_adapter_output_root_before_artifact_or_ledger_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_id: str,
    domain: str,
    root_drift: str,
) -> None:
    forbidden_reads: list[str] = []

    class OutputRootOnlyResult:
        def __init__(self, output_root: str | None) -> None:
            if output_root is not None:
                self.output_root = output_root

        def __getattr__(self, field_name: str):
            if field_name == "output_root":
                return None
            forbidden_reads.append(field_name)
            raise AssertionError(
                f"adapter evidence read before output-root validation: {field_name}"
            )

    def dispatch(**kwargs):
        parent = Path(kwargs["output_root"])
        case_id = str(kwargs["case"]["case_id"])
        expected = parent / case_id
        output_root = {
            "wrong": parent.with_name(parent.name + "-wrong"),
            "missing": None,
            "nested": expected / case_id,
        }[root_drift]
        return OutputRootOnlyResult(
            output_root.as_posix() if output_root is not None else None
        )

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=experiment_id,
        domain=domain,
        dispatch=dispatch,
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="adapter result output_root",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-root-binding",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "adapter_runtime"
    assert raised.value.terminal_outcome.failure_kind == "ValueError"
    assert isinstance(raised.value.__cause__, ValueError)
    assert forbidden_reads == []


@pytest.mark.parametrize(
    (
        "provider_attempt_count",
        "total_tokens",
        "total_cost_estimate",
        "cost_estimate_status",
    ),
    (
        (1, 24, 0.005, "single_currency_estimate"),
        (0, 0, 0.0, "explicit_zero_preprovider"),
    ),
    ids=("post-provider-settled", "pre-provider-explicit-zero"),
)
def test_formal_runner_settles_root_reservation_from_official_exception_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_attempt_count: int,
    total_tokens: int,
    total_cost_estimate: float,
    cost_estimate_status: str,
) -> None:
    observed_stores: list[Path] = []

    class OfficialExceptionAccountingHook:
        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            observed_stores.append(artifact_store.root_path.resolve(strict=False))
            return PaperProviderExceptionAccounting(
                provider_attempt_count=provider_attempt_count,
                total_tokens=total_tokens,
                total_cost_estimate=total_cost_estimate,
                cost_estimate_currency="USD",
                cost_estimate_status=cost_estimate_status,
                usage_missing_count=0,
                ambiguous_count=0,
                conservative_total_tokens=total_tokens,
                conservative_total_cost_estimate=total_cost_estimate,
                state_counts={
                    "released": int(provider_attempt_count == 0),
                    "ambiguous": 0,
                    "settled": provider_attempt_count,
                },
            )

    hook = OfficialExceptionAccountingHook()

    def dispatch(**_kwargs):
        raise ValueError("captured model execution record reconciliation failed")

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )
    budget = _with_ai_unit_commitments(
        _budget(
            planned_conditions=1,
            planned_root_runs=1,
            planned_ai_units=1,
        ),
        (condition, selection),
    )
    callback = replace(
        callback,
        budget=budget,
        rolling_disk_forecast=_rolling_tracker(budget),
        hard_limits={
            "max_total_provider_attempts": 100,
            "max_total_tokens": 100_000,
            "max_total_cost_estimate": 100.0,
        },
        online_root_callback_factory=lambda **_context: hook,
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="captured model execution record reconciliation failed",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-exception-accounting",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "adapter_runtime"
    assert raised.value.terminal_outcome.failure_kind == "ValueError"
    assert isinstance(raised.value.__cause__, ValueError)
    expected_store = (
        callback.output_root
        / "runs"
        / condition.condition_id
        / "case-1"
        / "case-1"
    ).resolve(strict=False)
    assert observed_stores == [expected_store]
    assert callback.usage.provider_attempt_count == provider_attempt_count
    assert callback.usage.total_tokens == total_tokens
    assert callback.usage.cost_estimate_by_currency == {
        "USD": total_cost_estimate,
    }
    assert callback.usage.usage_missing_count == 0
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_total_cost_estimate == 0.0
    assert callback.usage.reserved_cost_estimate_by_currency == {}


def test_formal_runner_exp5_exception_accounting_uses_exact_typed_root_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExactExp5Hook:
        def __init__(self, authority: Exp5RootHardLimitAuthority) -> None:
            self.root_hard_limit_authority = authority

        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            assert isinstance(artifact_store, ArtifactStore)
            authority = self.root_hard_limit_authority
            return PaperProviderExceptionAccounting(
                provider_attempt_count=authority.provider_attempt_count,
                total_tokens=None,
                total_cost_estimate=None,
                cost_estimate_currency=authority.currency,
                cost_estimate_status="usage_missing",
                usage_missing_count=authority.provider_attempt_count,
                ambiguous_count=authority.provider_attempt_count,
                conservative_total_tokens=authority.total_tokens,
                conservative_total_cost_estimate=authority.total_cost_estimate,
                state_counts={
                    "released": 0,
                    "ambiguous": authority.provider_attempt_count,
                    "settled": 0,
                },
            )

    def dispatch(**_kwargs):
        raise ValueError("adapter failed after durable dispatch intents")

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXP5_EXPERIMENT_ID,
        domain="lean_proof",
        dispatch=dispatch,
    )
    # suite-average authority is intentionally far below this selected root's
    # exact seven-slot cost; the runner must reserve from the typed Exp5 callback.
    assert callback.budget.cost_upper_bound == 0.01
    exact_authority = Exp5RootHardLimitAuthority(
        condition_id=condition.condition_id,
        condition_digest=condition.condition_digest,
        case_id="case-1",
        selection_digest=selection.selection_digest,
        model_endpoint_identity_digest=condition.model_endpoint_identity_digest,
        inventory_digest="sha256:" + "a" * 64,
        provider_attempt_count=7,
        total_tokens=487_424,
        total_cost_estimate=Decimal("13.075932"),
        currency="USD",
    )

    class ExactExp5Factory:
        inventory_digest = exact_authority.inventory_digest

        def __call__(self, **context):
            runtime_condition = context["condition"]
            return ExactExp5Hook(
                replace(
                    exact_authority,
                    condition_id=runtime_condition.condition_id,
                    condition_digest=runtime_condition.condition_digest,
                    case_id=context["case_id"],
                    selection_digest=context["selection"].selection_digest,
                    model_endpoint_identity_digest=(
                        runtime_condition.model_endpoint_identity_digest
                    ),
                )
            )

    callback = replace(
        callback,
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 1_000_000,
            "max_total_cost_estimate": 20.0,
        },
        online_root_callback_factory=ExactExp5Factory(),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="adapter failed after durable dispatch intents",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-exp5-exact-root-authority",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "adapter_runtime"
    assert callback.usage.provider_attempt_count == 7
    assert callback.usage.total_tokens == 0
    assert callback.usage.usage_missing_count == 7
    assert callback.usage.hard_limit_provider_attempt_count == 7
    assert callback.usage.hard_limit_total_tokens == exact_authority.total_tokens
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": exact_authority.total_cost_estimate,
    }
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_cost_estimate_by_currency == {}


def test_formal_runner_decimal_root_reservation_settles_exact_and_rejects_one_unit_overrun() -> None:
    exact_root_cost = Decimal("0.3000000000000000000000000003")
    reservation = formal_runner._RootBudgetReservation(
        provider_attempt_count=2,
        total_tokens=2,
        total_cost_estimate=exact_root_cost,
        currency="CNY",
    )
    accounting = PaperProviderExceptionAccounting(
        provider_attempt_count=2,
        total_tokens=None,
        total_cost_estimate=None,
        cost_estimate_currency="CNY",
        cost_estimate_status="usage_missing",
        usage_missing_count=1,
        ambiguous_count=1,
        conservative_total_tokens=2,
        conservative_total_cost_estimate=exact_root_cost,
        state_counts={"released": 0, "ambiguous": 1, "settled": 1},
    )
    usage = formal_runner._UsageTotals()
    assert formal_runner._reserve_hard_limit_capacity(
        usage=usage,
        reservation=reservation,
        hard_limits={"max_total_cost_estimate": exact_root_cost},
    )

    formal_runner._settle_provider_exception_accounting(
        usage=usage,
        reservation=reservation,
        accounting=accounting,
    )

    assert usage.reserved_cost_estimate_by_currency == {}
    assert usage.hard_limit_cost_estimate_by_currency == {
        "CNY": exact_root_cost,
    }

    overrun_usage = formal_runner._UsageTotals()
    assert formal_runner._reserve_hard_limit_capacity(
        usage=overrun_usage,
        reservation=reservation,
        hard_limits={"max_total_cost_estimate": exact_root_cost},
    )
    with pytest.raises(
        ValueError,
        match="official exception accounting exceeds root reservation",
    ):
        formal_runner._settle_provider_exception_accounting(
            usage=overrun_usage,
            reservation=reservation,
            accounting=replace(
                accounting,
                conservative_total_cost_estimate=(
                    exact_root_cost + Decimal("0.0000000000000000000000000001")
                ),
            ),
        )


@pytest.mark.parametrize("use_official_accounting", (True, False))
def test_formal_runner_success_usage_missing_settles_conservative_root_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_official_accounting: bool,
) -> None:
    observed_stores: list[Path] = []

    class OfficialSuccessAccountingHook:
        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            observed_stores.append(artifact_store.root_path.resolve(strict=False))
            return PaperProviderExceptionAccounting(
                provider_attempt_count=1,
                total_tokens=None,
                total_cost_estimate=None,
                cost_estimate_currency="USD",
                cost_estimate_status="usage_missing",
                usage_missing_count=1,
                ambiguous_count=0,
                conservative_total_tokens=1024,
                conservative_total_cost_estimate=0.01,
                state_counts={"released": 0, "ambiguous": 0, "settled": 1},
            )

    hook = OfficialSuccessAccountingHook()

    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task = SimpleNamespace(
            **{
                **vars(result.task_result),
                "total_tokens": None,
                "cost_estimate": None,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "usage_missing",
            }
        )
        return SimpleNamespace(**{**vars(result), "task_result": task})

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )
    callback = replace(
        callback,
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 10_000,
            "max_total_cost_estimate": 10.0,
        },
        online_root_callback_factory=(
            (lambda **_context: hook) if use_official_accounting else None
        ),
    )

    outcome = callback._dispatch_root_case(
        condition=condition,
        selection=selection,
        case_id="case-1",
        case=case,
        worker_id="worker-success-missing-accounting",
        callback_kwargs={},
    )

    expected_store = (
        callback.output_root
        / "runs"
        / condition.condition_id
        / "case-1"
        / "case-1"
    ).resolve(strict=False)
    assert observed_stores == ([expected_store] if use_official_accounting else [])
    assert outcome.task.total_tokens is None
    assert outcome.task.cost_estimate is None
    assert outcome.task.cost_estimate_status == "usage_missing"
    assert callback.usage.provider_attempt_count == 1
    assert callback.usage.total_tokens == 0
    assert callback.usage.cost_estimate_by_currency == {}
    assert callback.usage.usage_missing_count == 1
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_cost_estimate_by_currency == {}


@pytest.mark.parametrize(
    ("task_provider_attempt_count", "official_provider_attempt_count"),
    ((0, 1), (1, 0)),
)
def test_formal_runner_success_usage_missing_rejects_official_count_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_provider_attempt_count: int,
    official_provider_attempt_count: int,
) -> None:
    class OfficialSuccessAccountingHook:
        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            assert isinstance(artifact_store, ArtifactStore)
            usage_missing = official_provider_attempt_count > 0
            return PaperProviderExceptionAccounting(
                provider_attempt_count=official_provider_attempt_count,
                total_tokens=None if usage_missing else 0,
                total_cost_estimate=None if usage_missing else 0.0,
                cost_estimate_currency="USD",
                cost_estimate_status=(
                    "usage_missing" if usage_missing else "explicit_zero_preprovider"
                ),
                usage_missing_count=int(usage_missing),
                ambiguous_count=0,
                conservative_total_tokens=1024 if usage_missing else 0,
                conservative_total_cost_estimate=0.01 if usage_missing else 0.0,
                state_counts={
                    "released": int(not usage_missing),
                    "ambiguous": 0,
                    "settled": official_provider_attempt_count,
                },
            )

    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task = SimpleNamespace(
            **{
                **vars(result.task_result),
                "provider_attempt_count": task_provider_attempt_count,
                "total_tokens": None,
                "cost_estimate": None,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "usage_missing",
            }
        )
        attempts = tuple(
            replace(
                attempt,
                provider_attempt_count=task_provider_attempt_count,
                total_tokens=None,
                cost_estimate=None,
                cost_estimate_status="usage_missing",
            )
            for attempt in result.attempt_results
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": task,
                "attempt_results": attempts,
            }
        )

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )
    callback = replace(
        callback,
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 10_000,
            "max_total_cost_estimate": 10.0,
        },
        online_root_callback_factory=(
            lambda **_context: OfficialSuccessAccountingHook()
        ),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="official provider success accounting count mismatch",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-success-accounting-mismatch",
            callback_kwargs={},
        )

    error = raised.value
    assert error.terminal_outcome.failure_stage == "provider_accounting"
    assert callback.usage.provider_attempt_count == official_provider_attempt_count
    assert callback.usage.total_tokens == 0
    assert callback.usage.cost_estimate_by_currency == {}
    assert callback.usage.usage_missing_count == 1
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert error.diagnostics["provider_accounting_mismatch"] == {
        "task_provider_attempt_count": task_provider_attempt_count,
        "official_provider_attempt_count": official_provider_attempt_count,
        "official_cost_estimate_status": (
            "usage_missing"
            if official_provider_attempt_count
            else "explicit_zero_preprovider"
        ),
    }
    assert error.diagnostics["provider_accounting"][
        "provider_attempt_count"
    ] == official_provider_attempt_count
    assert error.diagnostics["provider_accounting"][
        "cost_estimate_status"
    ] == "usage_missing"


def _trace_zero_accounting_probe(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_provider_attempt_count: object = 0,
    omit_task_provider_attempt_count: bool = False,
    task_total_tokens: object = None,
    task_cost_estimate: object = None,
    first_attempt_provider_count: object = 0,
    runtime_provider_count: object = 0,
    omit_runtime_provider_count: bool = False,
    attempt_sequence_override: object = None,
):
    trace_source_usage = {
        "schema_version": "tokenshare.paper_trace_source_usage.v1",
        "attribution_kind": "immutable_response_bank",
        "current_provider_call_count": 0,
        "current_provider_spend_cny": "0",
        "committed_consumption_count": 2,
        "consumptions": [
            {"consumption_id": "source-consumption-1"},
            {"consumption_id": "source-consumption-2"},
        ],
    }

    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task_fields = {
            **vars(result.task_result),
            "provider_attempt_count": task_provider_attempt_count,
            "total_tokens": task_total_tokens,
            "cost_estimate": task_cost_estimate,
            "cost_estimate_currency": None,
            "cost_estimate_status": "usage_missing",
        }
        if omit_task_provider_attempt_count:
            task_fields.pop("provider_attempt_count")
        task = SimpleNamespace(**task_fields)
        first_attempt = replace(
            result.attempt_results[0],
            provider_attempt_count=first_attempt_provider_count,
            provider_attempt_index=0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            cost_estimate=None,
            cost_estimate_currency=None,
            cost_estimate_status="usage_missing",
        )
        second_attempt = replace(
            first_attempt,
            attempt_id=first_attempt.attempt_id + "-replacement",
            provider_attempt_count=0,
            provider_attempt_index=1,
        )
        attempt_results = (
            [first_attempt, second_attempt]
            if attempt_sequence_override is None
            else attempt_sequence_override
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": task,
                "attempt_results": attempt_results,
            }
        )

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )

    class TraceContext:
        def runtime_for(self, **_identity):
            fields = {"current_provider_call_count": runtime_provider_count}
            if omit_runtime_provider_count:
                fields.pop("current_provider_call_count")
            return SimpleNamespace(**fields)

    callback = replace(
        callback,
        trace_context=TraceContext(),
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 10_000,
            "max_total_cost_estimate": 10.0,
        },
    )
    monkeypatch.setattr(
        formal_runner,
        "_project_committed_trace_source_usage",
        lambda **_context: trace_source_usage,
    )
    monkeypatch.setattr(
        formal_runner,
        "_evaluate_trace_root_evidence",
        lambda **_context: SimpleNamespace(
            paper_eligible=False,
            to_dict=lambda: {
                "schema_version": (
                    "tokenshare.paper_evidence_eligibility_report.v2"
                ),
                "paper_eligible": False,
                "evidence_class": "real_model_trace_protocol_run",
                "current_provider_call_count": 0,
            },
        ),
    )
    return callback, condition, selection, case, trace_source_usage


def test_fresh_multi_attempt_trace_root_settles_explicit_current_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback, condition, selection, case, trace_source_usage = (
        _trace_zero_accounting_probe(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    )

    outcome = callback._dispatch_root_case(
        condition=condition,
        selection=selection,
        case_id="case-1",
        case=case,
        worker_id="worker-trace-zero",
        callback_kwargs={},
    )

    assert outcome.provider_attempt_count == 0
    assert outcome.total_tokens == 0
    assert outcome.cost_estimate == 0.0
    assert outcome.cost_estimate_status == "not_applicable"
    assert outcome.task["trace_source_usage"] == trace_source_usage
    assert outcome.task["provider_attempt_count"] == 0
    assert outcome.task["total_tokens"] == 0
    assert outcome.task["cost_estimate"] == 0.0
    assert outcome.task["cost_estimate_status"] == "not_applicable"
    assert outcome.hard_limit_consumption == {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "total_cost_estimate_decimal": "0",
        "cost_estimate_by_currency": {},
        "cost_estimate_decimal_by_currency": {},
        "usage_missing_count": 0,
    }
    assert callback.usage.provider_attempt_count == 0
    assert callback.usage.usage_missing_count == 0
    assert callback.usage.hard_limit_provider_attempt_count == 0
    assert callback.usage.hard_limit_total_tokens == 0
    assert callback.usage.hard_limit_total_cost_estimate == 0.0


@pytest.mark.parametrize(
    ("task_count", "attempt_count", "runtime_count"),
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
)
def test_fresh_trace_current_provider_header_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_count: int,
    attempt_count: int,
    runtime_count: int,
) -> None:
    callback, condition, selection, case, _trace_source_usage = (
        _trace_zero_accounting_probe(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            task_provider_attempt_count=task_count,
            first_attempt_provider_count=attempt_count,
            runtime_provider_count=runtime_count,
        )
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="trace current provider accounting",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-trace-drift",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "provider_accounting"
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.provider_attempt_count == 1
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_total_cost_estimate == 0.0
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.usage_missing_count == 1
    assert raised.value.diagnostics["provider_accounting"][
        "provider_attempt_count"
    ] == 1


@pytest.mark.parametrize(
    ("task_overrides", "expected_reported_provider_count"),
    (
        ({"task_provider_attempt_count": 2}, None),
        ({"task_total_tokens": 1025}, 0),
        ({"task_cost_estimate": 0.011}, 0),
        ({"task_provider_attempt_count": "malformed"}, None),
    ),
)
def test_trace_prevalidation_failure_settles_conservative_missing_not_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_overrides: dict[str, object],
    expected_reported_provider_count: int | None,
) -> None:
    callback, condition, selection, case, _trace_source_usage = (
        _trace_zero_accounting_probe(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            **task_overrides,
        )
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-trace-prevalidation",
            callback_kwargs={},
        )

    error = raised.value
    assert error.terminal_outcome.failure_stage == "provider_accounting"
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_cost_estimate_by_currency == {}
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.usage_missing_count == 1
    assert error.diagnostics["hard_limit_consumption"] == {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 1,
        "total_tokens": 1024,
        "total_cost_estimate": 0.01,
        "total_cost_estimate_decimal": "0.01",
        "cost_estimate_by_currency": {"USD": 0.01},
        "cost_estimate_decimal_by_currency": {"USD": "0.01"},
        "usage_missing_count": 1,
    }
    assert error.diagnostics["provider_accounting"] == {
        "provider_attempt_count": expected_reported_provider_count,
        "total_tokens": None,
        "total_cost_estimate": None,
        "cost_estimate_currency": None,
        "cost_estimate_status": "usage_missing",
        "usage_missing_count": 1,
    }
    observed = error.diagnostics["observed_provider_usage"]
    assert observed["provider_attempt_count"] == task_overrides.get(
        "task_provider_attempt_count", 0
    )
    assert observed["total_tokens"] == task_overrides.get("task_total_tokens")
    assert observed["total_cost_estimate"] == task_overrides.get(
        "task_cost_estimate"
    )
    assert observed["attempt_provider_attempt_counts"] == [0, 0]
    assert observed["runtime_current_provider_call_count"] == 0


@pytest.mark.parametrize(
    "probe_overrides",
    (
        {"omit_task_provider_attempt_count": True},
        {"first_attempt_provider_count": None},
        {"first_attempt_provider_count": "malformed"},
        {"omit_runtime_provider_count": True},
        {"runtime_provider_count": "malformed"},
        {"attempt_sequence_override": 7},
        {"attempt_sequence_override": ()},
    ),
)
def test_trace_malformed_or_missing_accounting_fails_conservative_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_overrides: dict[str, object],
) -> None:
    callback, condition, selection, case, _trace_source_usage = (
        _trace_zero_accounting_probe(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            **probe_overrides,
        )
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-trace-malformed",
            callback_kwargs={},
        )

    error = raised.value
    assert error.terminal_outcome.failure_stage == "provider_accounting"
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.usage_missing_count == 1
    assert error.diagnostics["provider_accounting"] == {
        "provider_attempt_count": None,
        "total_tokens": None,
        "total_cost_estimate": None,
        "cost_estimate_currency": None,
        "cost_estimate_status": "usage_missing",
        "usage_missing_count": 1,
    }
    assert "observed_provider_usage" in error.diagnostics
    assert error.diagnostics["observed_provider_usage"]["trace_counts"] is None


@pytest.mark.parametrize(
    ("override", "match"),
    (
        ({"provider_attempt_count": 2}, "call reservation"),
        ({"total_tokens": 1025}, "token reservation"),
        ({"total_cost_estimate": 0.011}, "cost reservation"),
    ),
)
def test_formal_root_exact_settlement_rejects_over_reservation_without_mutation(
    override: dict[str, object],
    match: str,
) -> None:
    reservation = formal_runner._RootBudgetReservation(
        provider_attempt_count=1,
        total_tokens=1024,
        total_cost_estimate=0.01,
        currency="USD",
    )
    usage = formal_runner._UsageTotals(
        provider_attempt_count=3,
        total_tokens=300,
        cost_estimate_by_currency={"USD": 0.003},
        usage_missing_count=2,
        reserved_provider_attempt_count=1,
        reserved_total_tokens=1024,
        reserved_cost_estimate_by_currency={"USD": 0.01},
    )
    before = vars(usage).copy()
    before["cost_estimate_by_currency"] = dict(usage.cost_estimate_by_currency)
    before["reserved_cost_estimate_by_currency"] = dict(
        usage.reserved_cost_estimate_by_currency
    )
    values = {
        "provider_attempt_count": 1,
        "total_tokens": 11,
        "total_cost_estimate": 0.001,
        "cost_estimate_currency": "USD",
        "cost_estimate_status": "single_currency_estimate",
        **override,
    }

    with pytest.raises(ValueError, match=match):
        formal_runner._settle_hard_limit_reservation(
            usage=usage,
            reservation=reservation,
            **values,
        )

    assert vars(usage) == before


@pytest.mark.parametrize(
    ("task_override", "match", "expected_provider_attempt_count"),
    (
        ({"provider_attempt_count": 2}, "call reservation", 0),
        ({"total_tokens": 1025}, "token reservation", 1),
        ({"cost_estimate": 0.011}, "cost reservation", 1),
    ),
)
def test_formal_runner_over_reservation_blocks_and_settles_only_conservative_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_override: dict[str, object],
    match: str,
    expected_provider_attempt_count: int,
) -> None:
    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task = SimpleNamespace(
            **{
                **vars(result.task_result),
                "total_tokens": 11,
                "cost_estimate": 0.001,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "single_currency_estimate",
                **task_override,
            }
        )
        return SimpleNamespace(**{**vars(result), "task_result": task})

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )
    callback = replace(
        callback,
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 10_000,
            "max_total_cost_estimate": 10.0,
        },
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match=match,
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-over-reservation",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "provider_accounting"
    assert raised.value.terminal_outcome.failure_kind == "ValueError"
    assert (
        callback.usage.provider_attempt_count == expected_provider_attempt_count
    )
    assert callback.usage.total_tokens == 0
    assert callback.usage.cost_estimate_by_currency == {}
    assert callback.usage.usage_missing_count == 1
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_cost_estimate_by_currency == {}


def test_formal_runner_success_accounting_failure_blocks_after_conservative_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenOfficialAccountingHook:
        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            assert isinstance(artifact_store, ArtifactStore)
            raise ValueError("durable success accounting is inconsistent")

    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task = SimpleNamespace(
            **{
                **vars(result.task_result),
                "total_tokens": None,
                "cost_estimate": None,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "usage_missing",
            }
        )
        return SimpleNamespace(**{**vars(result), "task_result": task})

    callback, condition, selection, case = _adapter_root_binding_probe(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        experiment_id=EXPERIMENT_ID,
        domain="factorization",
        dispatch=dispatch,
    )
    callback = replace(
        callback,
        hard_limits={
            "max_total_provider_attempts": 10,
            "max_total_tokens": 10_000,
            "max_total_cost_estimate": 10.0,
        },
        online_root_callback_factory=lambda **_context: BrokenOfficialAccountingHook(),
    )

    with pytest.raises(
        formal_runner.PaperInfrastructureBlockedError,
        match="official provider success accounting failed",
    ) as raised:
        callback._dispatch_root_case(
            condition=condition,
            selection=selection,
            case_id="case-1",
            case=case,
            worker_id="worker-broken-success-accounting",
            callback_kwargs={},
        )

    assert raised.value.terminal_outcome.failure_stage == "provider_accounting"
    assert raised.value.terminal_outcome.failure_kind == "ValueError"
    assert callback.usage.provider_attempt_count == 1
    assert callback.usage.total_tokens == 0
    assert callback.usage.cost_estimate_by_currency == {}
    assert callback.usage.usage_missing_count == 1
    assert callback.usage.hard_limit_provider_attempt_count == 1
    assert callback.usage.hard_limit_total_tokens == 1024
    assert callback.usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    assert callback.usage.reserved_provider_attempt_count == 0
    assert callback.usage.reserved_total_tokens == 0
    assert callback.usage.reserved_cost_estimate_by_currency == {}


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        ("missing", "missing hard-limit consumption"),
        ("duplicate", "protocol task identity is duplicate"),
        ("understated", "actual usage exceeds hard-limit consumption"),
        ("missingness", "hard-limit missingness drifted from actual usage"),
    ),
)
def test_current_usage_requires_one_complete_hard_row_per_protocol_root(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    run_root = (
        tmp_path
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-hard-completeness"
        / "0"
    )
    generation = run_root / ".generations" / "generation-1"
    generation.mkdir(parents=True)
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-1"}),
        encoding="utf-8",
    )

    def hard_row(
        total_tokens: int,
        *,
        usage_missing_count: int = 0,
    ) -> dict[str, object]:
        return {
            "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
            "provider_attempt_count": 1,
            "total_tokens": total_tokens,
            "total_cost_estimate": 0.005,
            "cost_estimate_by_currency": {"USD": 0.005},
            "usage_missing_count": usage_missing_count,
        }

    tasks = [
        {
            "record_scope": "protocol",
            "task_id": "root-1",
            "cost_estimate_status": (
                "usage_missing" if mutation == "missingness" else "complete"
            ),
            "hard_limit_consumption": hard_row(
                10,
                usage_missing_count=1 if mutation == "missingness" else 0,
            ),
        },
        {
            "record_scope": "protocol",
            "task_id": "root-2",
            "cost_estimate_status": (
                "usage_missing" if mutation == "missingness" else "complete"
            ),
            "hard_limit_consumption": hard_row(
                9 if mutation == "understated" else 10,
            ),
        },
    ]
    if mutation == "missing":
        tasks[1].pop("hard_limit_consumption")
    elif mutation == "duplicate":
        tasks[1]["task_id"] = "root-1"
    attempts = [
        {
            "record_scope": "protocol",
            "task_id": task_id,
            "attempt_id": f"attempt-{index}",
            "provider_attempt_count": 1,
            "total_tokens": None if mutation == "missingness" else 10,
            "cost_estimate": None if mutation == "missingness" else 0.005,
            "cost_estimate_currency": "USD",
            "cost_estimate_status": (
                "usage_missing"
                if mutation == "missingness"
                else "single_currency_estimate"
            ),
        }
        for index, task_id in enumerate(("root-1", "root-2"), start=1)
    ]
    (generation / "per_task_results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in tasks),
        encoding="utf-8",
    )
    (generation / "per_attempt_results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=match):
        formal_runner._usage_from_current_checkpoints(tmp_path)


def test_current_resume_preserves_canonical_decimal_hard_cost_after_result_removal(
    tmp_path: Path,
) -> None:
    exact_cost = Decimal("0.3000000000000000000000000003")
    hard_row = formal_runner._root_hard_limit_consumption_body(
        provider_attempt_count=1,
        total_tokens=1,
        total_cost_estimate=exact_cost,
        cost_estimate_currency="CNY",
        usage_missing_count=1,
    )
    assert hard_row["total_cost_estimate"] == float(exact_cost)
    assert hard_row["total_cost_estimate_decimal"] == str(exact_cost)
    assert hard_row["cost_estimate_decimal_by_currency"] == {
        "CNY": str(exact_cost)
    }
    common = {
        "record_scope": "protocol",
        "task_id": "root-exact-decimal",
        "provider_attempt_count": 1,
        "total_tokens": None,
        "cost_estimate": None,
        "cost_estimate_currency": "CNY",
        "cost_estimate_status": "usage_missing",
    }
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[{**common, "hard_limit_consumption": hard_row}],
        attempts=[
            {
                **common,
                "schema_version": "tokenshare.paper_attempt_result.v3",
                "attempt_id": "attempt-exact-decimal",
                "attempt_status": "blocked_dependency",
                "provider_attempt_index": 0,
            }
        ],
    )
    result_path = tmp_path / "formal_runner_result.json"
    result_path.write_text("{}", encoding="utf-8")
    result_path.unlink()

    restored = formal_runner._usage_from_evidence(tmp_path)

    assert restored.hard_limit_cost_estimate_by_currency == {"CNY": exact_cost}


@pytest.mark.parametrize(
    "drift_field",
    ("total_cost_estimate", "cost_estimate_by_currency"),
)
def test_current_resume_rejects_numeric_decimal_hard_cost_drift(
    tmp_path: Path,
    drift_field: str,
) -> None:
    exact_cost = Decimal("0.3000000000000000000000000003")
    hard_row = formal_runner._root_hard_limit_consumption_body(
        provider_attempt_count=1,
        total_tokens=1,
        total_cost_estimate=exact_cost,
        cost_estimate_currency="CNY",
        usage_missing_count=1,
    )
    if drift_field == "total_cost_estimate":
        hard_row[drift_field] = 0.31
    else:
        hard_row[drift_field] = {"CNY": 0.31}
    common = {
        "record_scope": "protocol",
        "task_id": "root-drifted-decimal",
        "provider_attempt_count": 1,
        "total_tokens": None,
        "cost_estimate": None,
        "cost_estimate_currency": "CNY",
        "cost_estimate_status": "usage_missing",
    }
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[{**common, "hard_limit_consumption": hard_row}],
        attempts=[
            {
                **common,
                "schema_version": "tokenshare.paper_attempt_result.v3",
                "attempt_id": "attempt-drifted-decimal",
                "attempt_status": "blocked_dependency",
                "provider_attempt_index": 0,
            }
        ],
    )

    with pytest.raises(ValueError, match="numeric/decimal"):
        formal_runner._usage_from_current_checkpoints(tmp_path)


def test_hard_limit_replay_detects_one_decimal_unit_aggregate_drift() -> None:
    exact_cost = Decimal("0.3000000000000000000000000003")
    left = formal_runner._hard_limit_consumption_body(
        formal_runner._UsageTotals(
            hard_limit_provider_attempt_count=1,
            hard_limit_total_tokens=1,
            hard_limit_cost_estimate_by_currency={"CNY": exact_cost},
        )
    )
    right = formal_runner._hard_limit_consumption_body(
        formal_runner._UsageTotals(
            hard_limit_provider_attempt_count=1,
            hard_limit_total_tokens=1,
            hard_limit_cost_estimate_by_currency={
                "CNY": exact_cost + Decimal("0.0000000000000000000000000001")
            },
        )
    )
    assert left["total_cost_estimate"] == right["total_cost_estimate"] == 0.3

    assert not formal_runner._hard_limit_consumption_equal(left, right)


def _legacy_trace_zero_current_rows(
    *,
    task_id: str,
    root_status: str = "completed",
) -> tuple[dict[str, object], dict[str, object]]:
    task = {
        "schema_version": "tokenshare.paper_task_result.v2",
        "record_scope": "protocol",
        "task_id": task_id,
        "root_status": root_status,
        "outcome_status": (
            "succeeded" if root_status == "completed" else "failed_experimental"
        ),
        "evidence_integrity": "complete",
        "provider_attempt_count": 0,
        "total_tokens": None,
        "cost_estimate": None,
        "cost_estimate_status": "usage_missing",
        "trace_source_usage": {
            "schema_version": "tokenshare.paper_trace_source_usage.v1",
            "attribution_kind": "immutable_response_bank",
            "current_provider_call_count": 0,
            "current_provider_spend_cny": "0",
            "committed_consumption_count": 1,
            "consumptions": [
                {
                    "source_acquisition_attempt_id": "sha256:source-attempt",
                    "source_bank_roles": [
                        "request_body",
                        "raw_output_or_provider_failure",
                        "provenance",
                        "usage_status",
                        "pricing",
                        "acquisition_attempt",
                        "model_record",
                    ],
                    "total_tokens": 100,
                    "cost_estimate_cny": "0.00039",
                }
            ],
        },
        "versioned_paper_evidence_report": {
            "schema_version": "tokenshare.paper_evidence_eligibility_report.v2",
            "evidence_class": "real_model_trace_protocol_run",
            "source_classification": "approved_real_full_acquisition",
            "current_provider_call_count": 0,
            "source_provider_call_count": 1,
            "identity_consistent": True,
            "direct_evidence_complete": True,
        },
    }
    attempt = {
        "schema_version": "tokenshare.paper_attempt_result.v3",
        "record_scope": "protocol",
        "task_id": task_id,
        "attempt_id": f"attempt-{task_id}",
        "attempt_status": "succeeded",
        "provider_attempt_count": 0,
        "provider_attempt_index": 0,
        "total_tokens": None,
        "cost_estimate": None,
        "cost_estimate_status": "usage_missing",
    }
    return task, attempt


def _write_legacy_trace_current_fixture(
    *,
    output_root: Path,
    tasks: list[dict[str, object]],
    attempts: list[dict[str, object]],
) -> None:
    (output_root / "suite_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_formal_runner.v1",
                "formal": True,
                "pilot_only": False,
                "regression_only": True,
                "capturing": True,
                "execution_scope": "formal_matrix",
                "traceability_replay_input_root_ref": {
                    "schema_version": (
                        "tokenshare.paper_traceability_replay_input_ref.v1"
                    ),
                    "descriptor_digest": "sha256:descriptor",
                    "handle_digest": "sha256:handle",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    run_root = (
        output_root
        / "experiments"
        / EXPERIMENT_ID
        / "runs"
        / "condition-legacy-trace"
        / "0"
    )
    generation = run_root / ".generations" / "generation-legacy"
    generation.mkdir(parents=True)
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-legacy"}),
        encoding="utf-8",
    )
    (generation / "per_task_results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in tasks),
        encoding="utf-8",
    )
    (generation / "per_attempt_results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )


@pytest.mark.parametrize("root_status", ("completed", "failed"))
def test_current_usage_projects_qualified_legacy_trace_zero_provider_to_zero(
    tmp_path: Path,
    root_status: str,
) -> None:
    task, attempt = _legacy_trace_zero_current_rows(
        task_id="root-legacy-zero",
        root_status=root_status,
    )
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[task],
        attempts=[attempt],
    )

    usage = formal_runner._usage_from_current_checkpoints(tmp_path)

    assert usage.provider_attempt_count == 0
    assert usage.total_tokens == 0
    assert usage.total_cost_estimate == 0.0
    assert usage.cost_estimate_by_currency == {}
    assert usage.usage_missing_count == 0
    assert usage.hard_limit_provider_attempt_count == 0
    assert usage.hard_limit_total_tokens == 0
    assert usage.hard_limit_total_cost_estimate == 0.0
    assert usage.hard_limit_cost_estimate_by_currency == {}


def test_current_usage_legacy_trace_qualification_survives_closure_staging(
    tmp_path: Path,
) -> None:
    task, attempt = _legacy_trace_zero_current_rows(task_id="root-staged-trace")
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[task],
        attempts=[attempt],
    )
    manifest_path = tmp_path / "suite_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("traceability_replay_input_root_ref")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    usage = formal_runner._usage_from_current_checkpoints(tmp_path)

    assert usage.provider_attempt_count == 0
    assert usage.usage_missing_count == 0
    assert usage.hard_limit_provider_attempt_count == 0
    assert usage.hard_limit_total_tokens == 0


@pytest.mark.parametrize(
    "mutation",
    (
        "positive_task_provider",
        "positive_attempt_provider",
        "unknown_current_provider",
        "online_evidence_class",
        "source_usage_missing",
        "identity_unknown",
        "fresh_task_zero_signature",
        "fresh_attempt_zero_signature",
        "new_provider_accounting_marker",
        "duplicate_attempt_id",
    ),
)
def test_current_usage_rejects_unqualified_legacy_missing_hard_row(
    tmp_path: Path,
    mutation: str,
) -> None:
    task, attempt = _legacy_trace_zero_current_rows(task_id="root-unqualified")
    if mutation == "positive_task_provider":
        task["provider_attempt_count"] = 1
    elif mutation == "positive_attempt_provider":
        attempt["provider_attempt_count"] = 1
    elif mutation == "unknown_current_provider":
        trace_usage = dict(task["trace_source_usage"])
        trace_usage.pop("current_provider_call_count")
        task["trace_source_usage"] = trace_usage
    elif mutation == "online_evidence_class":
        report = dict(task["versioned_paper_evidence_report"])
        report["evidence_class"] = "real_transport"
        task["versioned_paper_evidence_report"] = report
    elif mutation == "source_usage_missing":
        trace_usage = dict(task["trace_source_usage"])
        consumptions = [dict(trace_usage["consumptions"][0])]
        consumptions[0]["total_tokens"] = None
        trace_usage["consumptions"] = consumptions
        task["trace_source_usage"] = trace_usage
    elif mutation == "identity_unknown":
        report = dict(task["versioned_paper_evidence_report"])
        report["identity_consistent"] = False
        task["versioned_paper_evidence_report"] = report
    elif mutation == "fresh_task_zero_signature":
        task.update(
            {
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "cost_estimate_currency": None,
                "cost_estimate_status": "not_applicable",
            }
        )
    elif mutation == "fresh_attempt_zero_signature":
        attempt.update(
            {
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "cost_estimate_currency": None,
                "cost_estimate_status": "not_applicable",
            }
        )
    elif mutation == "new_provider_accounting_marker":
        task["provider_accounting"] = {
            "cost_estimate_status": "usage_missing"
        }
    attempts = [attempt]
    if mutation == "duplicate_attempt_id":
        attempts.append(dict(attempt))
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[task],
        attempts=attempts,
    )

    with pytest.raises(ValueError, match="missing hard-limit consumption"):
        formal_runner._usage_from_current_checkpoints(tmp_path)


def test_current_usage_rejects_mixed_hard_and_legacy_trace_rows(
    tmp_path: Path,
) -> None:
    first_task, first_attempt = _legacy_trace_zero_current_rows(
        task_id="root-new-hard"
    )
    first_task["hard_limit_consumption"] = {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "cost_estimate_by_currency": {},
        "usage_missing_count": 0,
    }
    legacy_task, legacy_attempt = _legacy_trace_zero_current_rows(
        task_id="root-legacy-missing"
    )
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[first_task, legacy_task],
        attempts=[first_attempt, legacy_attempt],
    )

    with pytest.raises(ValueError, match="missing hard-limit consumption"):
        formal_runner._usage_from_current_checkpoints(tmp_path)


def test_current_usage_counts_fresh_usage_missing_once_per_root(
    tmp_path: Path,
) -> None:
    task, first_attempt = _legacy_trace_zero_current_rows(
        task_id="root-fresh-missing"
    )
    task["hard_limit_consumption"] = {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 1,
        "total_tokens": 1024,
        "total_cost_estimate": 0.01,
        "total_cost_estimate_decimal": "0.01",
        "cost_estimate_by_currency": {"USD": 0.01},
        "cost_estimate_decimal_by_currency": {"USD": "0.01"},
        "usage_missing_count": 1,
    }
    second_attempt = {
        **first_attempt,
        "attempt_id": "attempt-root-fresh-missing-replacement",
    }
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[task],
        attempts=[first_attempt, second_attempt],
    )

    usage = formal_runner._usage_from_current_checkpoints(tmp_path)

    assert usage.provider_attempt_count == 0
    assert usage.usage_missing_count == 1
    assert usage.hard_limit_provider_attempt_count == 1
    assert usage.hard_limit_total_tokens == 1024
    assert usage.hard_limit_cost_estimate_by_currency == {"USD": Decimal("0.01")}


def test_current_resume_preserves_blocked_unknown_provider_as_missing(
    tmp_path: Path,
) -> None:
    common = {
        "schema_version": "tokenshare.paper_task_result.v2",
        "record_scope": "experiment",
        "task_id": "root-blocked-provider-unknown",
        "root_status": "blocked",
        "outcome_status": "blocked_dependency",
        "provider_attempt_count": None,
        "total_tokens": None,
        "cost_estimate": None,
        "cost_estimate_currency": None,
        "cost_estimate_status": "usage_missing",
    }
    task = {
        **common,
        "hard_limit_consumption": {
            "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
            "provider_attempt_count": 1,
            "total_tokens": 1024,
            "total_cost_estimate": 0.01,
            "cost_estimate_by_currency": {"USD": 0.01},
            "usage_missing_count": 1,
        },
    }
    attempt = {
        **common,
        "schema_version": "tokenshare.paper_attempt_result.v3",
        "attempt_id": "attempt-blocked-provider-unknown",
        "attempt_status": "blocked_dependency",
        "provider_attempt_index": 0,
    }
    _write_legacy_trace_current_fixture(
        output_root=tmp_path,
        tasks=[task],
        attempts=[attempt],
    )

    usage = formal_runner._usage_from_current_checkpoints(tmp_path)
    baseline = formal_runner.load_paper_formal_resume_baseline(tmp_path)

    assert usage.provider_attempt_count == 0
    assert usage.usage_missing_count == 1
    assert usage.hard_limit_provider_attempt_count == 1
    assert usage.hard_limit_total_tokens == 1024
    assert usage.hard_limit_cost_estimate_by_currency == {"USD": Decimal("0.01")}
    assert baseline.provider_attempt_count == 0
    assert baseline.total_cost_estimate is None
    assert baseline.total_cost_estimate_status == "usage_missing"
    assert baseline.usage_missing_count == 1


def _install_usage_missing_suite_dispatch(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def dispatch(**kwargs):
        result = _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=str(kwargs["case"]["case_id"]),
        )
        task = SimpleNamespace(
            **{
                **vars(result.task_result),
                "total_tokens": None,
                "cost_estimate": None,
                "cost_estimate_currency": "USD",
                "cost_estimate_status": "usage_missing",
            }
        )
        attempt = replace(
            result.attempt_results[0],
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            cost_estimate=None,
            cost_estimate_currency="USD",
            cost_estimate_status="usage_missing",
        )
        return SimpleNamespace(
            **{
                **vars(result),
                "task_result": task,
                "attempt_results": [attempt],
            }
        )

    _install_single_case_execution(monkeypatch=monkeypatch, dispatch=dispatch)


def _usage_missing_suite_kwargs(
    *,
    tmp_path: Path,
) -> dict[str, object]:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path, config=config)
    return {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "hard_limits": {
            "max_total_provider_attempts": 1,
            "max_total_tokens": 1024,
            "max_total_cost_estimate": 0.01,
        },
    }


def test_formal_suite_usage_missing_replay_reports_actual_and_separates_hard_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_missing_suite_dispatch(monkeypatch=monkeypatch)

    suite = formal_runner.execute_paper_formal_suite(
        **_usage_missing_suite_kwargs(tmp_path=tmp_path)
    )

    assert suite.status is PaperStatus.COMPLETED
    assert suite.provider_attempt_count == 1
    assert suite.total_tokens == 0
    assert suite.total_cost_estimate == 0.0
    assert suite.cost_estimate_by_currency is None
    assert suite.total_cost_estimate_status == "usage_missing"
    persisted = json.loads(
        (tmp_path / "formal_runner_result.json").read_text(encoding="utf-8")
    )
    assert persisted["total_tokens"] == 0
    assert persisted["total_cost_estimate"] == 0.0
    assert persisted.get("cost_estimate_by_currency") is None
    assert persisted["total_cost_estimate_status"] == "usage_missing"
    assert persisted["budget_ref"]["hard_limit_consumption"] == {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 1,
        "total_tokens": 1024,
        "total_cost_estimate": 0.01,
        "total_cost_estimate_decimal": "0.01",
        "cost_estimate_by_currency": {"USD": 0.01},
        "cost_estimate_decimal_by_currency": {"USD": "0.01"},
        "usage_missing_count": 1,
    }

    replayed = formal_runner.replay_paper_formal_suite(output_root=tmp_path)

    assert replayed.provider_attempt_count == 1
    assert replayed.total_tokens == 0
    assert replayed.total_cost_estimate == 0.0
    assert replayed.cost_estimate_by_currency is None
    assert replayed.total_cost_estimate_status == "usage_missing"


def test_formal_resume_usage_missing_restores_conservative_hard_limit_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_missing_suite_dispatch(monkeypatch=monkeypatch)
    formal_runner.execute_paper_formal_suite(
        **_usage_missing_suite_kwargs(tmp_path=tmp_path)
    )

    usage = formal_runner._usage_from_evidence(tmp_path)
    baseline = formal_runner.load_paper_formal_resume_baseline(tmp_path)

    assert usage.provider_attempt_count == 1
    assert usage.total_tokens == 0
    assert usage.total_cost_estimate == 0.0
    assert usage.cost_estimate_by_currency == {}
    assert usage.usage_missing_count == 1
    assert usage.hard_limit_provider_attempt_count == 1
    assert usage.hard_limit_total_tokens == 1024
    assert usage.hard_limit_total_cost_estimate == 0.0
    assert usage.hard_limit_cost_estimate_by_currency == {"USD": Decimal("0.01")}
    assert formal_runner._hard_limit_reached(
        usage,
        {
            "max_total_provider_attempts": 1,
            "max_total_tokens": 1024,
            "max_total_cost_estimate": 0.01,
        },
    )
    assert baseline.provider_attempt_count == 1
    assert baseline.total_cost_estimate is None
    assert baseline.cost_estimate_by_currency == {}
    assert baseline.total_cost_estimate_status == "usage_missing"
    assert baseline.usage_missing_count == 1
    (tmp_path / "formal_runner_result.json").unlink()
    checkpoint_usage = formal_runner._usage_from_evidence(tmp_path)
    assert checkpoint_usage.provider_attempt_count == 1
    assert checkpoint_usage.total_tokens == 0
    assert checkpoint_usage.cost_estimate_by_currency == {}
    assert checkpoint_usage.usage_missing_count == 1
    assert checkpoint_usage.hard_limit_provider_attempt_count == 1
    assert checkpoint_usage.hard_limit_total_tokens == 1024
    assert checkpoint_usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    resume = formal_runner.load_paper_formal_resume_baseline(tmp_path)
    assert resume.provider_attempt_count == 1
    assert resume.total_cost_estimate is None
    assert resume.total_cost_estimate_status == "usage_missing"
    assert resume.usage_missing_count == 1


def test_formal_replay_recomputes_hard_limit_aggregate_from_canonical_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_missing_suite_dispatch(monkeypatch=monkeypatch)
    formal_runner.execute_paper_formal_suite(
        **_usage_missing_suite_kwargs(tmp_path=tmp_path)
    )
    result_path = tmp_path / "formal_runner_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["budget_ref"]["hard_limit_consumption"]["total_tokens"] = 1023
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    formal_runner.FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()

    with pytest.raises(
        ValueError,
        match="hard-limit consumption does not match canonical tasks",
    ):
        formal_runner.replay_paper_formal_suite(output_root=tmp_path)


@pytest.mark.parametrize(
    ("failure_mode", "expected_provider_attempt_count"),
    (
        ("official_reconciliation_failure", 1),
        ("exact_over_reservation", 1),
    ),
)
def test_blocked_settlement_survives_current_only_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
    expected_provider_attempt_count: int,
) -> None:
    class BrokenOfficialAccountingHook:
        def __call__(self, **_context):
            return None

        def reconcile_exception_accounting(self, *, artifact_store: ArtifactStore):
            assert isinstance(artifact_store, ArtifactStore)
            raise ValueError("durable blocked accounting is inconsistent")

    kwargs = _usage_missing_suite_kwargs(tmp_path=tmp_path)
    if failure_mode == "official_reconciliation_failure":
        _install_usage_missing_suite_dispatch(monkeypatch=monkeypatch)
        kwargs["online_root_callback_factory"] = (
            lambda **_context: BrokenOfficialAccountingHook()
        )
    else:
        def over_reservation_dispatch(**dispatch_kwargs):
            return _complete_adapter_result(
                output_root=Path(dispatch_kwargs["output_root"]),
                condition=dispatch_kwargs["condition"],
                case_id=str(dispatch_kwargs["case"]["case_id"]),
            )

        _install_single_case_execution(
            monkeypatch=monkeypatch,
            dispatch=over_reservation_dispatch,
        )

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status is PaperStatus.BLOCKED
    assert suite.provider_attempt_count == expected_provider_attempt_count
    assert suite.total_tokens == 0
    assert suite.total_cost_estimate == 0.0
    assert suite.total_cost_estimate_status == "usage_missing"
    assert suite.budget_ref["hard_limit_consumption"] == {
        "schema_version": "tokenshare.paper_hard_limit_consumption.v1",
        "provider_attempt_count": 1,
        "total_tokens": 1024,
        "total_cost_estimate": 0.01,
        "total_cost_estimate_decimal": "0.01",
        "cost_estimate_by_currency": {"USD": 0.01},
        "cost_estimate_decimal_by_currency": {"USD": "0.01"},
        "usage_missing_count": 1,
    }
    (tmp_path / "formal_runner_result.json").unlink()

    checkpoint_usage = formal_runner._usage_from_evidence(tmp_path)

    assert (
        checkpoint_usage.provider_attempt_count
        == expected_provider_attempt_count
    )
    assert checkpoint_usage.total_tokens == 0
    assert checkpoint_usage.cost_estimate_by_currency == {}
    assert checkpoint_usage.usage_missing_count == 1
    assert checkpoint_usage.hard_limit_provider_attempt_count == 1
    assert checkpoint_usage.hard_limit_total_tokens == 1024
    assert checkpoint_usage.hard_limit_cost_estimate_by_currency == {
        "USD": Decimal("0.01")
    }
    resume = formal_runner.load_paper_formal_resume_baseline(tmp_path)
    assert resume.provider_attempt_count == expected_provider_attempt_count
    assert resume.total_cost_estimate is None
    assert resume.total_cost_estimate_status == "usage_missing"
    assert resume.usage_missing_count == 1


def test_online_callback_factory_forces_single_root_scheduler_worker() -> None:
    assert (
        formal_runner._root_scheduler_worker_count(
            protocol_worker_count=50,
            online_root_callback_factory=object(),
        )
        == 1
    )
    assert (
        formal_runner._root_scheduler_worker_count(
            protocol_worker_count=50,
            online_root_callback_factory=None,
        )
        == 50
    )

    class ConcurrentOnlineFactory:
        serialize_roots = False

    assert (
        formal_runner._root_scheduler_worker_count(
            protocol_worker_count=50,
            online_root_callback_factory=ConcurrentOnlineFactory(),
        )
        == 50
    )


def test_formal_runner_composite_observes_online_before_existing_exp3_hook() -> None:
    events: list[str] = []

    class OnlineHook:
        def __call__(self, **_context):
            events.append("online_raw")

        def after_parsed_candidate_persisted(self, _context):
            events.append("online_parsed")

    class ExistingExp3Hook:
        def __call__(self, **_context):
            events.append("exp3_raw")
            return {"result_kind": "verification_rejected"}

        def after_parsed_candidate_persisted(self, _context):
            events.append("exp3_parsed")
            return "exp3-directive"

    composite = formal_runner._compose_official_runtime_hooks(
        OnlineHook(), ExistingExp3Hook()
    )
    assert composite(submission_id="submission-1") == {
        "result_kind": "verification_rejected"
    }
    assert composite.after_parsed_candidate_persisted(object()) == "exp3-directive"
    assert events == [
        "online_raw",
        "exp3_raw",
        "online_parsed",
        "exp3_parsed",
    ]


def _faultable_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
    attempt_suffix: str,
) -> SimpleNamespace:
    output_root = output_root / case_id
    store = ArtifactStore(output_root)
    created_at = "2026-07-19T00:00:00Z"

    def save_json(artifact_id: str, artifact_type: str, body: dict[str, object]):
        return store.save_json(
            body,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            artifact_schema_id=f"test.{artifact_type.lower()}",
            artifact_schema_version="v1",
            source={"test": "formal-exp3-runner"},
            metadata={},
            created_at=created_at,
        )

    raw_ref = save_json(
        f"raw-{case_id}-{attempt_suffix}",
        "RawModelOutput",
        {"content_text": '{"result_kind":"no_factor"}'},
    )
    parsed_ref = save_json(
        f"parsed-{case_id}-{attempt_suffix}",
        "ParsedModelOutput",
        {
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )
    provenance_ref = save_json(
        f"provenance-{case_id}-{attempt_suffix}",
        "AIProviderCallProvenance",
        {"provider_family": "siliconflow", "model": PROVIDER_MODEL_ID},
    )
    usage_ref = save_json(
        f"usage-{case_id}-{attempt_suffix}",
        "AIUsage",
        {"total_tokens": 11, "cost_estimate": 0.125},
    )
    request_ref = save_json(
        f"request-{case_id}-{attempt_suffix}",
        "AIRequest",
        {"model": PROVIDER_MODEL_ID},
    )
    attempt = PaperAttemptResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        run_id=f"run-{case_id}",
        task_id=case_id,
        unit_id=f"unit-{case_id}",
        attempt_id=f"attempt-{case_id}-{attempt_suffix}",
        worker_id="worker-1",
        provider_attempt_index=1 if attempt_suffix == "original" else 2,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model=PROVIDER_MODEL_ID,
        entry_id=MODEL_ENTRY_ID,
        request_ref=request_ref.to_dict(),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict(),
        parse_failure_ref=None,
        provenance_ref=provenance_ref.to_dict(),
        usage_ref=usage_ref.to_dict(),
        started_at=created_at,
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=5,
        completion_tokens=6,
        total_tokens=11,
        cost_estimate=0.125,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    task = SimpleNamespace(
        task_id=case_id,
        repeat_id=condition.repeat_id,
        root_status=PaperTaskStatus.COMPLETED,
        provider_attempt_count=1,
        total_tokens=11,
        cost_estimate=0.125,
        artifact_refs=[
            raw_ref.to_dict(),
            parsed_ref.to_dict(),
            provenance_ref.to_dict(),
            usage_ref.to_dict(),
            request_ref.to_dict(),
        ],
        event_refs=[],
    )
    return SimpleNamespace(
        task_result=task,
        attempt_results=[attempt],
        fault_records=[],
        event_records=[
            {
                "event_id": f"event-{case_id}-{attempt_suffix}",
                "event_type": "TASK_COMPLETED",
            }
        ],
        eligibility_report=SimpleNamespace(paper_eligible=False),
        output_root=output_root.as_posix(),
    )


def _rejected_adapter_result(
    *,
    output_root: Path,
    condition: PaperExperimentCondition,
    case_id: str,
) -> SimpleNamespace:
    result = _faultable_adapter_result(
        output_root=output_root,
        condition=condition,
        case_id=case_id,
        attempt_suffix="rejected",
    )
    rejected_attempt = replace(
        result.attempt_results[0],
        attempt_status=PaperAttemptStatus.VERIFICATION_REJECTED,
        error_kind="verifier_rejected",
    )
    task = SimpleNamespace(
        **{
            **vars(result.task_result),
            "root_status": PaperTaskStatus.FAILED,
        }
    )
    return SimpleNamespace(
        **{
            **vars(result),
            "task_result": task,
            "attempt_results": [rejected_attempt],
        }
    )


def _generation_records(
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    relative_path: str,
) -> list[dict[str, object]]:
    logical = formal_runner.FormalEvidenceStore(
        suite_root
    ).load_logical_run_records(
        experiment_id=experiment_id,
        condition_id=condition_id,
        repeat_id=0,
    )
    key_by_path = {
        "per_task_results.jsonl": "tasks",
        "per_attempt_results.jsonl": "attempts",
        "fault_injections.jsonl": "faults",
        "events/event_log.jsonl": "events",
        "artifacts/artifact_index.jsonl": "artifacts",
    }
    return list(logical[key_by_path[relative_path]])


def test_formal_runner_smoke_filter_executes_one_canonical_root_and_freezes_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    plan = _two_case_dispatch_plan(tmp_path, config=config)
    condition, selection = plan.bound_items()[0]
    selected_case_id = selection.ordered_case_ids[0]
    dispatched: list[str] = []

    class SmokeCallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("smoke execution consumes the canonical plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        case_id = str(kwargs["case"]["case_id"])
        dispatched.append(case_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=case_id,
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, SmokeCallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "catalog_manifest": {
            "catalog_digest": CATALOG_DIGEST,
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
                {
                    "case_id": "case-2",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=1,
                planned_root_runs=1,
                planned_ai_units=1,
            ),
            *plan.bound_items(),
            selected_case_ids_by_condition={
                condition.condition_id: (selected_case_id,)
            },
        ),
        "root_case_filter": {condition.condition_id: (selected_case_id,)},
        "execution_classification": {
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "execution_scope": "smoke_suite",
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        },
        "suite_id": "test_smoke",
        "pre_execution_documents": {
            "smoke_profile.json": {
                "schema_version": "tokenshare.paper_smoke_profile.v1",
                "suite_id": "test_smoke",
                "paper_eligible": False,
            },
            "smoke_execution_plan.json": {
                "schema_version": "tokenshare.paper_smoke_execution_plan.v1",
                "suite_id": "test_smoke",
                "direct_root_run_count": 1,
                "formal": False,
                "pilot_only": True,
                "regression_only": True,
                "paper_eligible": False,
                "ineligibility_reasons": ["smoke_suite", "pilot_only"],
                "items": [
                    {
                        "item_id": "smoke-root",
                        "experiment_id": EXPERIMENT_ID,
                        "condition_id": condition.condition_id,
                        "condition_digest": condition.condition_digest,
                        "selection_id": selection.selection_id,
                        "selection_digest": selection.selection_digest,
                        "case_id": selected_case_id,
                        "repeat_id": condition.repeat_id,
                        "condition_selector": {
                            "domain": condition.domain,
                            "difficulty": condition.difficulty,
                            "worker_count": condition.worker_count,
                        },
                    }
                ],
            },
        },
    }

    suite = formal_runner.execute_paper_formal_suite(**kwargs)

    assert dispatched == [selected_case_id]
    assert suite.suite_id == "test_smoke"
    assert suite.paper_eligible is False
    suite_manifest = json.loads(
        (tmp_path / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert suite_manifest["formal"] is False
    assert suite_manifest["pilot_only"] is True
    assert suite_manifest["regression_only"] is True
    compatibility_root = tmp_path / EXPERIMENT_ID
    assert (compatibility_root / "experiment_manifest.json").is_file()
    assert (
        compatibility_root
        / "runs"
        / condition.condition_id
        / "0"
        / "CURRENT.json"
    ).is_file()
    assert not (
        compatibility_root
        / "runs"
        / condition.condition_id
        / selected_case_id
    ).exists()
    tasks = _generation_records(
        tmp_path,
        EXPERIMENT_ID,
        condition.condition_id,
        "per_task_results.jsonl",
    )
    assert [task["task_id"] for task in tasks] == [selected_case_id]
    assert tasks[0]["formal"] is False
    assert tasks[0]["pilot_only"] is True
    assert tasks[0]["regression_only"] is True
    assert tasks[0]["paper_eligible"] is False
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("smoke replay must not call provider dispatch")
        ),
    )
    from tokenshare.experiments.paper_smoke import replay_paper_smoke_suite
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    generate_paper_smoke_report(output_root=tmp_path, secret_values=())
    FormalEvidenceStore(tmp_path)._refresh_evidence_manifest()
    tree_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    import tokenshare.experiments.paper_smoke_report as smoke_report_module

    monkeypatch.setattr(
        smoke_report_module,
        "generate_paper_smoke_report",
        lambda **_kwargs: pytest.fail("smoke replay must be read-only"),
    )

    replayed = replay_paper_smoke_suite(output_root=tmp_path)
    summary = json.loads(
        (tmp_path / "metrics" / "smoke_summary.json").read_text(encoding="utf-8")
    )
    assert replayed.suite_id == "test_smoke"
    assert summary["provider_attempt_count"] == 0
    assert summary["total_tokens"] == 0
    assert summary["cost_estimate"] == 0.0
    assert summary["provider_actual_billing"] is None
    assert summary["provider_actual_billing_available"] is False
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == tree_before


def test_representative_runner_validates_full_budget_then_dispatches_selected_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path, config=config)
    first_condition, first_selection = base.bound_items()[0]
    second_condition = replace(
        first_condition,
        condition_id="condition-2",
        repeat_id=1,
        seed=2,
    )
    second_selection = replace(first_selection, selection_id="selection-2")
    plan = replace(
        base,
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    dispatched: list[str] = []

    class CallbackModule:
        def expand_conditions(self, context):
            raise AssertionError("representative execution consumes full formal plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=EXPERIMENT_ID, rows=())

    def fake_case_dispatch(**kwargs):
        dispatched.append(kwargs["condition"].condition_id)
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((EXPERIMENT_ID, CallbackModule()),),
    )
    monkeypatch.setattr(formal_runner, "dispatch_paper_case", fake_case_dispatch)
    representative_kwargs = {
        **_formal_execution_kwargs(tmp_path=tmp_path, config=config, plan=plan),
        "budget": _with_ai_unit_commitments(
            _budget(
                planned_conditions=2,
                planned_root_runs=2,
                planned_ai_units=2,
            ),
            (first_condition, first_selection),
            (second_condition, second_selection),
        ),
        "selected_condition_ids": (first_condition.condition_id,),
        "root_case_filter": {
            first_condition.condition_id: first_selection.ordered_case_ids,
        },
        "bypass_nonmetric_facility_gates": True,
        "suite_id": "representative-full-plan-smoke-exp1-exp4",
    }
    suite = formal_runner.execute_paper_formal_suite(**representative_kwargs)

    assert dispatched == [first_condition.condition_id]
    assert suite.condition_count == 1
    assert suite.task_count == 1
    assert suite.experiment_ids == (EXPERIMENT_ID,)
    assert suite.status is PaperStatus.COMPLETED
    assert tuple(
        item["condition_id"] for item in suite.condition_results
    ) == (first_condition.condition_id,)

    monkeypatch.setattr(
        formal_runner,
        "_dispatch_formal_conditions",
        lambda **_kwargs: pytest.fail(
            "completed representative resume must return from selected closure"
        ),
    )
    resumed = formal_runner.execute_paper_formal_suite(
        **{**representative_kwargs, "resume": True}
    )
    assert resumed == suite
    assert resumed.condition_results == suite.condition_results


def test_representative_runner_freezes_only_active_experiments_after_full_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    authority_root = tmp_path / "formal-authority"
    execution_root = tmp_path / "execution"
    active_plan = _plan_for_experiment(
        tmp_path=authority_root,
        config=config,
        experiment_id=EXPERIMENT_ID,
        condition_id="condition-active",
    )
    inactive_plan = _plan_for_experiment(
        tmp_path=authority_root,
        config=config,
        experiment_id="exp2_real_ai_scalability",
        condition_id="condition-inactive",
    )
    active_condition, active_selection = active_plan.bound_items()[0]
    validated_experiment_ids: list[tuple[str, ...]] = []
    real_validate = formal_runner._validate_suite_inputs

    def record_full_validation(**kwargs):
        validated_experiment_ids.append(
            tuple(plan.experiment_id for plan in kwargs["dispatch_plans"])
        )
        return real_validate(**kwargs)

    monkeypatch.setattr(formal_runner, "_validate_suite_inputs", record_full_validation)

    def fake_case_dispatch(**kwargs):
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    _install_single_case_execution(monkeypatch=monkeypatch, dispatch=fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=execution_root,
                config=config,
                plan=active_plan,
            ),
            "dispatch_plans": (active_plan, inactive_plan),
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(
                        EXPERIMENT_ID,
                        inactive_plan.experiment_id,
                    ),
                    planned_conditions=2,
                    planned_root_runs=2,
                    planned_ai_units=2,
                ),
                *active_plan.bound_items(),
                *inactive_plan.bound_items(),
            ),
            "selected_condition_ids": (active_condition.condition_id,),
            "root_case_filter": {
                active_condition.condition_id: active_selection.ordered_case_ids,
            },
            "execution_output_root_by_experiment": {
                EXPERIMENT_ID: execution_root / EXPERIMENT_ID,
            },
            "bypass_nonmetric_facility_gates": True,
            "suite_id": "representative-full-plan-subset",
        }
    )

    assert validated_experiment_ids == [
        (EXPERIMENT_ID, inactive_plan.experiment_id),
    ]
    assert suite.experiment_ids == (EXPERIMENT_ID,)
    dispatch = json.loads(
        (execution_root / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )
    assert [plan["experiment_id"] for plan in dispatch["plans"]] == [EXPERIMENT_ID]


def test_representative_rolling_forecast_projects_selected_full_commitments(
    tmp_path: Path,
) -> None:
    config = _ai_config()
    base = _planned_dispatch_plan(tmp_path / "authority", config=config)
    first_condition, first_selection = base.bound_items()[0]
    second_condition = replace(
        first_condition,
        condition_id="condition-2",
        repeat_id=1,
        seed=2,
    )
    second_selection = replace(first_selection, selection_id="selection-2")
    plan = replace(
        base,
        conditions=(first_condition, second_condition),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(
                first_condition,
                first_selection,
            ),
            FrozenConditionSelectionBinding.from_condition(
                second_condition,
                second_selection,
            ),
        ),
    )
    budget = _budget(
        planned_conditions=2,
        planned_root_runs=2,
        planned_ai_units=2,
        max_provider_attempts=2,
    )
    quota = json.loads(json.dumps(budget.quota_preflight))
    quota["budget_commitments"]["ai_unit_commitments"] = [
        {
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
            "case_id": "case-1",
            "planned_ai_unit_ids": ["range_0"],
        }
        for condition in (first_condition, second_condition)
    ]
    budget = replace(budget, quota_preflight=quota)

    tracker = formal_runner._rolling_disk_forecast_from_plan(
        budget=budget,
        bound_plans=((plan, ((first_condition, first_selection),)),),
        catalog_manifest={
            "factorization_cases": (
                {
                    "case_id": "case-1",
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    "expected_ai_unit_count": 1,
                },
            ),
            "lean_cases": (),
            "lean_lemma_graph_cases": (),
        },
        ai_api_configs={PROVIDER_CONFIG_ID: config},
        normalized_root_filter={
            first_condition.condition_id: first_selection.ordered_case_ids,
        },
        completed_task_keys=set(),
        max_condition_compaction_bytes=int(
            budget.disk_estimate["max_condition_compaction_bytes"]
        ),
    )

    assert tracker.remaining_root_runs == 1
    assert tracker.remaining_ai_units == 1
    assert tracker.remaining_provider_attempts == 1


def test_representative_rolling_forecast_rejects_selected_commitment_over_full_budget(
    tmp_path: Path,
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path / "authority", config=config)
    condition, selection = plan.bound_items()[0]
    budget = _budget(
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=1,
        max_provider_attempts=1,
    )
    quota = json.loads(json.dumps(budget.quota_preflight))
    quota["budget_commitments"]["ai_unit_commitments"] = [
        {
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
            "case_id": "case-1",
            "planned_ai_unit_ids": ["range_0", "range_1"],
        }
    ]
    budget = replace(budget, quota_preflight=quota)

    with pytest.raises(ValueError, match="selected disk commitment exceeds full budget"):
        formal_runner._rolling_disk_forecast_from_plan(
            budget=budget,
            bound_plans=((plan, ((condition, selection),)),),
            catalog_manifest={
                "factorization_cases": (
                    {"case_id": "case-1", "expected_ai_unit_count": 1},
                ),
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            ai_api_configs={PROVIDER_CONFIG_ID: config},
            normalized_root_filter={
                condition.condition_id: selection.ordered_case_ids,
            },
            completed_task_keys=set(),
            max_condition_compaction_bytes=int(
                budget.disk_estimate["max_condition_compaction_bytes"]
            ),
        )


def test_formal_resume_baseline_loads_cumulative_condition_usage_read_only(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "formal_runner_result.json"
    result_path.write_text(
        json.dumps(
            {
                "provider_attempt_count": 2,
                "total_tokens": 20,
                "total_cost_estimate": 1.5,
                "cost_estimate_by_currency": {"CNY": 1.5},
                "total_cost_estimate_status": "single_currency_estimate",
                "condition_results": [
                    {"condition_id": "condition-a", "provider_attempt_count": 1},
                    {"condition_id": "condition-b", "provider_attempt_count": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    before = result_path.read_bytes()
    loader = getattr(formal_runner, "load_paper_formal_resume_baseline", None)

    assert callable(loader), "formal runner must expose a typed resume baseline loader"
    baseline = loader(tmp_path)

    assert dict(baseline.provider_attempts_by_condition) == {
        "condition-a": 1,
        "condition-b": 1,
    }
    assert baseline.provider_attempt_count == 2
    assert baseline.total_cost_estimate == 1.5
    assert dict(baseline.cost_estimate_by_currency) == {"CNY": 1.5}
    assert baseline.total_cost_estimate_status == "single_currency_estimate"
    assert baseline.spend_missing_reason is None
    assert result_path.read_bytes() == before


def test_formal_resume_baseline_recovers_partial_current_checkpoint_usage(
    tmp_path: Path,
) -> None:
    run_root = (
        tmp_path
        / "experiments"
        / EXP5_EXPERIMENT_ID
        / "runs"
        / "condition-partial"
        / "0"
    )
    generation = run_root / ".generations" / "generation-1"
    generation.mkdir(parents=True)
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-1"}),
        encoding="utf-8",
    )
    (generation / "per_task_results.jsonl").write_text(
        json.dumps(
            {
                "record_scope": "protocol",
                "task_id": "root-partial",
                "hard_limit_consumption": {
                    "schema_version": (
                        "tokenshare.paper_hard_limit_consumption.v1"
                    ),
                    "provider_attempt_count": 1,
                    "total_tokens": 10,
                    "total_cost_estimate": 0.5,
                    "cost_estimate_by_currency": {"CNY": 0.5},
                    "usage_missing_count": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (generation / "per_attempt_results.jsonl").write_text(
        json.dumps(
            {
                "record_scope": "protocol",
                "task_id": "root-partial",
                "provider_attempt_count": 1,
                "total_tokens": 10,
                "cost_estimate": 0.5,
                "cost_estimate_currency": "CNY",
                "cost_estimate_status": "complete",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    baseline = formal_runner.load_paper_formal_resume_baseline(tmp_path)

    assert dict(baseline.provider_attempts_by_condition) == {
        "condition-partial": 1
    }
    assert baseline.provider_attempt_count == 1
    assert baseline.total_cost_estimate == 0.5
    assert dict(baseline.cost_estimate_by_currency) == {"CNY": 0.5}
    assert baseline.total_cost_estimate_status == "single_currency_estimate"


def test_formal_resume_baseline_is_zero_when_evidence_does_not_exist(
    tmp_path: Path,
) -> None:
    baseline = formal_runner.load_paper_formal_resume_baseline(
        tmp_path / "missing-suite"
    )

    assert baseline.evidence_exists is False
    assert dict(baseline.provider_attempts_by_condition) == {}
    assert baseline.provider_attempt_count == 0
    assert baseline.total_cost_estimate == 0.0
    assert dict(baseline.cost_estimate_by_currency) == {}
    assert baseline.total_cost_estimate_status == "not_applicable"
    assert baseline.spend_missing_reason is None


def test_representative_runner_maps_all_adapter_artifacts_to_execution_suite_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ai_config()
    authority_root = tmp_path / "formal-authority"
    execution_root = tmp_path / "execution"
    plan = _planned_dispatch_plan(authority_root, config=config)
    condition, selection = plan.bound_items()[0]
    adapter_roots: list[Path] = []

    def fake_case_dispatch(**kwargs):
        adapter_roots.append(Path(kwargs["output_root"]))
        return _complete_adapter_result(
            output_root=Path(kwargs["output_root"]),
            condition=kwargs["condition"],
            case_id=kwargs["case"]["case_id"],
        )

    _install_single_case_execution(monkeypatch=monkeypatch, dispatch=fake_case_dispatch)
    suite = formal_runner.execute_paper_formal_suite(
        **{
            **_formal_execution_kwargs(
                tmp_path=execution_root,
                config=config,
                plan=plan,
            ),
            "selected_condition_ids": (condition.condition_id,),
            "root_case_filter": {
                condition.condition_id: selection.ordered_case_ids,
            },
            "execution_output_root_by_experiment": {
                EXPERIMENT_ID: execution_root / EXPERIMENT_ID,
            },
            "bypass_nonmetric_facility_gates": True,
        }
    )

    assert suite.status is PaperStatus.COMPLETED, json.dumps(
        suite.error_summary, ensure_ascii=False, sort_keys=True
    )
    assert adapter_roots == [
        execution_root / EXPERIMENT_ID / "runs" / condition.condition_id / "case-1"
    ]
    assert not (authority_root / EXPERIMENT_ID / "runs").exists()


@pytest.mark.parametrize(
    "execution_roots",
    (
        {},
        {
            EXPERIMENT_ID: Path("execution") / EXPERIMENT_ID,
            "extra": Path("execution") / "extra",
        },
        {EXPERIMENT_ID: Path("outside") / EXPERIMENT_ID},
    ),
)
def test_representative_runner_rejects_missing_extra_or_escaped_execution_roots(
    tmp_path: Path,
    execution_roots: dict[str, Path],
) -> None:
    config = _ai_config()
    plan = _planned_dispatch_plan(tmp_path / "formal-authority", config=config)
    condition, selection = plan.bound_items()[0]
    suite_root = tmp_path / "execution"
    resolved = {
        key: (tmp_path / value if not value.is_absolute() else value)
        for key, value in execution_roots.items()
    }

    with pytest.raises(ValueError, match="execution output root"):
        formal_runner.execute_paper_formal_suite(
            **{
                **_formal_execution_kwargs(
                    tmp_path=suite_root,
                    config=config,
                    plan=plan,
                ),
                "selected_condition_ids": (condition.condition_id,),
                "root_case_filter": {
                    condition.condition_id: selection.ordered_case_ids,
                },
                "execution_output_root_by_experiment": resolved,
                "bypass_nonmetric_facility_gates": True,
            }
        )


def test_missing_bank_preflight_records_block_without_engine_task_lease_request_provider_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_response_bank import (
        PaperFormalTraceContext,
        PaperTraceCaseBinding,
        SemanticInventoryPlan,
    )
    from tokenshare.executors.response_bank import canonical_digest

    case = generate_factorization_paper_cases()[0]
    source = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    complete_runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "complete-bank",
        adapter_result=source,
    )
    omitted_id = complete_runtime.resolver.index.entries[-1].entry_id
    runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "incomplete-bank",
        adapter_result=source,
        omitted_terminal_entry_ids=(omitted_id,),
    )
    rows = runtime.resolver.index.inventory_rows
    config = _ai_config()
    dispatch_plan = _planned_dispatch_plan(tmp_path / "suite", config=config)
    condition = dispatch_plan.conditions[0]
    inventory_plan = SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=runtime.resolver.index.manifest.inventory_digest,
        rows=rows,
        condition_refs=(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "experiment_id": condition.experiment_id,
                "worker_count": condition.worker_count,
                "repeat_id": condition.repeat_id,
                "fault_type": condition.fault_type,
                "ablation_mode": condition.ablation_mode,
                "semantic_slot_keys": [row.semantic_slot_key for row in rows],
                "case_refs": [
                    {
                        "case_id": "case-1",
                        "case_record_digest": "sha256:" + "a" * 64,
                        "semantic_slot_keys": [
                            row.semantic_slot_key for row in rows
                        ],
                    }
                ],
            },
        ),
        exp2_online_condition_refs=(),
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=0,
        terminal_success_count=len(runtime.resolver.index.entries),
        terminal_unacquired_count=1,
    )
    case_binding = PaperTraceCaseBinding(
        condition_id=condition.condition_id,
        case_id="case-1",
        case_record_digest="sha256:" + "a" * 64,
        runtime=runtime,
        inventory_entry_ids=tuple(row.inventory_entry_id for row in rows),
    )
    with pytest.raises(ValueError, match="identity"):
        PaperFormalTraceContext(
            inventory_plan=inventory_plan,
            cases=(replace(case_binding, inventory_entry_ids=("wrong",)),),
        )

    alternate_digest = "sha256:" + "b" * 64
    alternate_runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "alternate-bank",
        adapter_result=source,
        case_record_digest=alternate_digest,
    )
    alternate_rows = alternate_runtime.resolver.index.inventory_rows
    combined_rows = tuple(rows) + tuple(alternate_rows)
    cross_case_plan = replace(
        inventory_plan,
        inventory_digest=canonical_digest(
            [row.to_dict() for row in sorted(
                combined_rows, key=lambda item: item.inventory_entry_id
            )]
        ),
        rows=combined_rows,
        condition_refs=(
            {
                **inventory_plan.condition_refs[0],
                "semantic_slot_keys": [
                    row.semantic_slot_key for row in combined_rows
                ],
                "case_refs": [
                    inventory_plan.condition_refs[0]["case_refs"][0],
                    {
                        "case_id": "case-2",
                        "case_record_digest": alternate_digest,
                        "semantic_slot_keys": [
                            row.semantic_slot_key for row in alternate_rows
                        ],
                    },
                ],
            },
        ),
        expected_slot_count=len(combined_rows),
        terminal_success_count=(
            len(runtime.resolver.index.entries)
            + len(alternate_runtime.resolver.index.entries)
        ),
        terminal_unacquired_count=1,
    )
    alternate_binding = PaperTraceCaseBinding(
        condition_id=condition.condition_id,
        case_id="case-2",
        case_record_digest=alternate_digest,
        runtime=alternate_runtime,
        inventory_entry_ids=tuple(
            row.inventory_entry_id for row in alternate_rows
        ),
    )
    PaperFormalTraceContext(
        inventory_plan=cross_case_plan,
        cases=(case_binding, alternate_binding),
    )
    with pytest.raises(ValueError, match="case record digest"):
        PaperFormalTraceContext(
            inventory_plan=cross_case_plan,
            cases=(
                replace(
                    case_binding,
                    runtime=alternate_runtime,
                    inventory_entry_ids=alternate_binding.inventory_entry_ids,
                ),
                replace(
                    alternate_binding,
                    runtime=runtime,
                    inventory_entry_ids=case_binding.inventory_entry_ids,
                ),
            ),
        )
    context = PaperFormalTraceContext(
        inventory_plan=inventory_plan,
        cases=(case_binding,),
    )
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: pytest.fail("preflight block must precede protocol dispatch"),
    )
    suite_root = tmp_path / "suite"
    suite = formal_runner.execute_paper_formal_suite(
        **_formal_execution_kwargs(
            tmp_path=suite_root,
            config=config,
            plan=dispatch_plan,
        ),
        trace_context=context,
    )

    assert suite.status is PaperStatus.BLOCKED
    marker = suite_root / "paper_preflight_blocked.v1.json"
    assert marker.is_file()
    assert not tuple(suite_root.rglob("*.jsonl"))
    assert not tuple(suite_root.rglob("events"))


def test_formal_runner_passes_trace_context_to_condition_callback() -> None:
    import ast
    import inspect
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(formal_runner._dispatch_formal_conditions))
    )
    callback_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_FormalConditionExecutionCallback"
    ]

    assert len(callback_calls) == 1
    trace_keywords = [
        keyword
        for keyword in callback_calls[0].keywords
        if keyword.arg == "trace_context"
    ]
    assert len(trace_keywords) == 1
    assert isinstance(trace_keywords[0].value, ast.Name)
    assert trace_keywords[0].value.id == "trace_context"


def test_formal_runner_finalizer_calls_match_public_signature() -> None:
    import ast
    import inspect
    import textwrap

    signature = inspect.signature(formal_runner._finalize_formal_experiment)
    accepted_keywords = set(signature.parameters)
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(formal_runner._dispatch_formal_conditions))
    )
    finalizer_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_finalize_formal_experiment"
    ]

    assert len(finalizer_calls) == 2
    assert all(
        keyword.arg in accepted_keywords
        for call in finalizer_calls
        for keyword in call.keywords
    )


def _trace_context_from_adapter_result(
    *,
    bank_root: Path,
    adapter_result,
    replacement_count: int = 1,
    omitted_terminal_entry_ids: tuple[str, ...] = (),
    case_record_digest: str = "sha256:" + "a" * 64,
    provider_config_digest: str = "sha256:provider",
    model_entry_id: str = "frozen-model-entry",
    provider_family: str = "fixture-provider",
    provider_model_id: str = "frozen-model",
):
    from hashlib import sha256

    from tokenshare.executors.response_bank import (
        OBJECT_ROLES,
        ExternalBankObjectLocator,
        ResponseBankEntry,
        ResponseBankInventoryRow,
        ResponseBankManifest,
        ResponseBankResolver,
        canonical_digest,
        initialize_response_bank,
        inventory_entry_id,
        semantic_slot_key,
    )
    from tokenshare.executors.trace_backed import freeze_trace_source_binding
    from tokenshare.experiments.paper_dispatcher import PaperTraceRuntimeContext

    def digest(data: bytes) -> str:
        return f"sha256:{sha256(data).hexdigest()}"

    source_store = ArtifactStore(Path(adapter_result.output_root))
    specs = []
    rows = []
    for attempt in adapter_result.attempt_results:
        if attempt.parsed_output_ref is None:
            continue
        candidate = source_store.read_bytes(
            ArtifactRef.from_dict(attempt.parsed_output_ref)
        ).decode("utf-8")
        for replacement_slot in range(replacement_count):
            entry_id = f"entry-{attempt.planned_ai_unit_id}-{replacement_slot}"
            request_body = json.dumps(
                {
                "planned_ai_unit_id": attempt.planned_ai_unit_id,
                "replacement_slot": replacement_slot,
                "model": provider_model_id,
                },
                sort_keys=True,
            ).encode("utf-8")
            inference_digest = digest(request_body + b":inference")
            values = {
                "inventory_entry_id": "",
                "semantic_slot_key": semantic_slot_key(
                    case_record_digest=case_record_digest,
                    planned_ai_unit_id=attempt.planned_ai_unit_id,
                    sample_slot_index=0,
                    replacement_slot=replacement_slot,
                    provider_config_digest=provider_config_digest,
                    prompt_profile_digest="sha256:prompt",
                    prompt_admission_profile_digest="sha256:admission",
                    plugin_version="2.0.0",
                ),
                "case_record_digest": case_record_digest,
                "planned_ai_unit_id": attempt.planned_ai_unit_id,
                "sample_slot_index": 0,
                "replacement_slot": replacement_slot,
                "provider_config_digest": provider_config_digest,
                "prompt_profile_digest": "sha256:prompt",
                "prompt_admission_profile_digest": "sha256:admission",
                "plugin_version": "2.0.0",
                "entry_id": entry_id,
                "body_digest": digest(request_body),
                "inference_request_digest": inference_digest,
            }
            values["inventory_entry_id"] = inventory_entry_id(values)
            row = ResponseBankInventoryRow(**values)
            rows.append(row)
            specs.append((row, request_body, inference_digest, candidate))
    inventory_digest = canonical_digest(
        [row.to_dict() for row in sorted(rows, key=lambda item: item.inventory_entry_id)]
    )
    retained_specs = tuple(
        spec for spec in specs if spec[0].entry_id not in omitted_terminal_entry_ids
    )
    receipt_digest = "sha256:" + "9" * 64
    manifest = ResponseBankManifest.create(
        bank_root_id=f"bank-{bank_root.name}",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest=inventory_digest,
        provider_config_digest=provider_config_digest,
        entry_ids=tuple(row.entry_id for row, *_rest in retained_specs),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(retained_specs),
        created_by_paid_receipt_digest=receipt_digest,
    )
    entries = []
    objects = {}
    for row, request_body, inference_digest, candidate in retained_specs:
        raw_objects = {
            "request_body": request_body,
            "raw_output": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_raw_output.v1",
                    "raw_response_json": {"id": row.entry_id},
                    "content_text": candidate,
                    "reasoning_content": None,
                    "provider_response_id": row.entry_id,
                    "finish_reason": "stop",
                },
                sort_keys=True,
            ).encode("utf-8"),
            "provenance": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_provenance.v1",
                    "provider_family": provider_family,
                    "entry_id": model_entry_id,
                    "provider_config_digest": provider_config_digest,
                    "inference_request_digest": inference_digest,
                    "normalized_absolute_endpoint": (
                        "https://example.invalid/v1/chat/completions"
                    ),
                    "transport_call_count": 1,
                    "secret_persisted": False,
                    "receipt_digest": receipt_digest,
                },
                sort_keys=True,
            ).encode("utf-8"),
            "usage": b'{"usage_status":"reported","usage":{"total_tokens":7}}',
            "latency": json.dumps(
                {"latency_ms": 10 + row.replacement_slot},
                sort_keys=True,
            ).encode("utf-8"),
            "pricing": b'{"cost_usd":"0.01"}',
            "acquisition_attempt": b'{"attempt":"approved"}',
            "model_record": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_model_record.v1",
                    "configured_model": provider_model_id,
                    "requested_model": provider_model_id,
                    "resolved_model": provider_model_id,
                    "response_model_status": "present",
                },
                sort_keys=True,
            ).encode("utf-8"),
        }
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=digest(data),
            )
            for role, data in raw_objects.items()
        )
        objects.update(
            {locator.object_digest: raw_objects[locator.object_role] for locator in locators}
        )
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=inference_digest,
                entry_id=row.entry_id,
                sample_slot_index=0,
                replacement_slot=row.replacement_slot,
                terminal_kind="success",
                object_locators=locators,
                acquisition_state_ref=f"state-{row.entry_id}",
            )
        )
    initialize_response_bank(
        bank_root,
        manifest=manifest,
        inventory_rows=rows,
        entries=entries,
        objects=objects,
    )
    resolver = ResponseBankResolver.open(bank_root)
    bindings = tuple(
        freeze_trace_source_binding(
            resolver,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=0,
            entry_ids=tuple(
                row.entry_id
                for row in rows
                if row.planned_ai_unit_id == planned_ai_unit_id
                and row.entry_id not in omitted_terminal_entry_ids
            ),
        )
        for planned_ai_unit_id in dict.fromkeys(
            row.planned_ai_unit_id for row in rows
        )
        if any(
            row.planned_ai_unit_id == planned_ai_unit_id
            and row.entry_id not in omitted_terminal_entry_ids
            for row in rows
        )
    )
    return PaperTraceRuntimeContext(
        resolver=resolver,
        bindings=bindings,
        inventory_rows=resolver.index.inventory_rows,
    )


def test_trace_source_model_identity_accepts_provider_failure_without_fabricated_resolution() -> None:
    expected_model = "deepseek-v4-pro"
    provider_failure_model = {
        "schema_version": "tokenshare.response_bank_model_record.v1",
        "configured_model": expected_model,
        "requested_model": expected_model,
        "resolved_model": None,
        "response_model_status": "unavailable_provider_failure",
    }

    formal_runner._validate_trace_source_model_identity(
        entry_terminal_kind="provider_failure",
        model_body=provider_failure_model,
        request_model=expected_model,
        expected_model=expected_model,
    )

    with pytest.raises(ValueError, match="trace source model identity mismatch"):
        formal_runner._validate_trace_source_model_identity(
            entry_terminal_kind="provider_failure",
            model_body={
                **provider_failure_model,
                "resolved_model": expected_model,
                "response_model_status": "present",
            },
            request_model=expected_model,
            expected_model=expected_model,
        )


def test_trace_source_role_v2_is_strict_and_legacy_v1_remains_readable() -> None:
    legacy_provenance = {"schema_version": "tokenshare.response_bank_provenance.v1"}
    legacy_latency = {
        "schema_version": "tokenshare.response_bank_latency.v1",
        "latency_ms": 1,
    }
    formal_runner._validate_trace_evidence_role_bundle(
        terminal_kind="success",
        provenance=legacy_provenance,
        latency=legacy_latency,
        provider_failure=None,
    )

    provenance = {
        "schema_version": "tokenshare.response_bank_provenance.v2",
        "evidence_kind": "supervised_stopped_attempt",
        "provider_family": "deepseek",
        "entry_id": "model-entry",
        "provider_config_digest": "sha256:" + "1" * 64,
        "inference_request_digest": "sha256:" + "2" * 64,
        "normalized_absolute_endpoint": "https://example.invalid/v1/chat/completions",
        "transport_call_count": 1,
        "closure_provider_call_count": 0,
        "closure_authority_digest": "sha256:" + "3" * 64,
        "stop_evidence_digest": "sha256:" + "4" * 64,
        "secret_persisted": False,
        "receipt_digest": "sha256:" + "5" * 64,
    }
    latency = {
        "schema_version": "tokenshare.response_bank_latency.v2",
        "evidence_kind": "supervised_stopped_attempt",
        "latency_ms": None,
        "latency_missing": True,
        "timing_source": "supervised_no_terminal_response",
        "observed_inflight_lower_bound_ms": 720_000,
        "unchanged_observation_window_ms": 120_000,
        "stop_evidence_digest": "sha256:" + "4" * 64,
    }
    provider_failure = {
        "schema_version": "tokenshare.response_bank_provider_failure.v2",
        "evidence_kind": "supervised_stopped_attempt",
        "failure_kind": "no_response",
        "http_status": None,
        "message": "no response",
        "raw_response_json": None,
        "transport_call_count": 1,
        "closure_provider_call_count": 0,
        "closure_authority_digest": "sha256:" + "3" * 64,
        "stop_evidence_digest": "sha256:" + "4" * 64,
    }
    formal_runner._validate_trace_evidence_role_bundle(
        terminal_kind="provider_failure",
        provenance=provenance,
        latency=latency,
        provider_failure=provider_failure,
    )
    drifts = (
        ({**provenance, "closure_provider_call_count": 1}, latency, provider_failure),
        (provenance, {**latency, "observed_inflight_lower_bound_ms": 719_999}, provider_failure),
        (provenance, {**latency, "unchanged_observation_window_ms": 119_999}, provider_failure),
        (provenance, {**latency, "timing_source": "wrong"}, provider_failure),
        (provenance, latency, {**provider_failure, "failure_kind": "executor_error"}),
        (provenance, {**latency, "stop_evidence_digest": "sha256:" + "6" * 64}, provider_failure),
        ({**provenance, "closure_authority_digest": "sha256:" + "7" * 64}, latency, provider_failure),
    )
    for drifted_provenance, drifted_latency, drifted_failure in drifts:
        with pytest.raises(ValueError):
            formal_runner._validate_trace_evidence_role_bundle(
                terminal_kind="provider_failure",
                provenance=drifted_provenance,
                latency=drifted_latency,
                provider_failure=drifted_failure,
            )
    from tests.executors.test_trace_backed import _hard_deadline_v2_roles

    hard_roles = _hard_deadline_v2_roles()
    formal_runner._validate_trace_evidence_role_bundle(
        terminal_kind="provider_failure",
        provenance=hard_roles["provenance"],
        latency=hard_roles["latency"],
        provider_failure=hard_roles["provider_failure"],
    )
    drifted_hard_failure = {
        **hard_roles["provider_failure"],
        "hard_deadline_evidence": {
            **hard_roles["provider_failure"]["hard_deadline_evidence"],
            "child_reaped": False,
        },
    }
    with pytest.raises(ValueError):
        formal_runner._validate_trace_evidence_role_bundle(
            terminal_kind="provider_failure",
            provenance=hard_roles["provenance"],
            latency=hard_roles["latency"],
            provider_failure=drifted_hard_failure,
        )
    projector_source = inspect.getsource(
        formal_runner._project_committed_trace_source_usage
    )
    assert "_validate_trace_source_model_identity(" in projector_source


def test_trace_run_uses_normal_coordinator_fault_verifier_checker_merge_settlement(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import (
        _v2_condition,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
    )
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        trace_context=trace_context,
    )
    event_types = {event["event_type"] for event in result.event_records}
    assert {
        "TASK_REGISTERED",
        "LEASE_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
    } <= event_types
    assert {"VERIFICATION_RECORDED", "CANONICAL_OUTPUTS_BOUND"} <= event_types
    assert any("MERGE" in event_type for event_type in event_types)
    assert any("SETTLEMENT" in event_type for event_type in event_types)
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED


def test_runner_object_graph_never_materializes_source(tmp_path: Path) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    bank_root = tmp_path / "bank"
    runtime = _trace_context_from_adapter_result(
        bank_root=bank_root,
        adapter_result=source,
    )
    source_bytes = {
        path.relative_to(bank_root).as_posix(): path.read_bytes()
        for path in bank_root.rglob("*")
        if path.is_file()
    }
    assert not any(
        isinstance(value, (bytes, bytearray))
        for value in runtime.__dict__.values()
    )
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        trace_context=runtime,
    )
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    assert {
        path.relative_to(bank_root).as_posix(): path.read_bytes()
        for path in bank_root.rglob("*")
        if path.is_file()
    } == source_bytes
    assert not (Path(result.output_root) / "objects").exists()


def test_trace_current_provider_calls_are_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_lean_paper_adapter import _condition_for_case
    from tests.support.lean_checker import RecordingLeanChecker
    from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
    from tokenshare.experiments.lean_paper_adapter import (
        ScriptedLeanPaperProofTransport,
        run_lean_paper_case,
    )
    import tokenshare.experiments.lean_paper_adapter as lean_adapter_module
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tokenshare.experiments.paper_dispatcher import PaperTraceRuntimeContext

    case = paper_catalog_module._with_lean_v1_paper_difficulty(
        json.loads(
            Path("benchmarks/paper/lean_catalog.v1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
    )
    environment = lean_adapter_module.default_lean_paper_environment_manifest()
    object.__setattr__(environment, "environment_digest", case["environment_digest"])
    monkeypatch.setattr(
        lean_adapter_module,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    condition = _condition_for_case(CATALOG_DIGEST, case)
    source = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lean-source",
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        checker=RecordingLeanChecker(),
    )
    context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "lean-bank",
        adapter_result=source,
        replacement_count=2,
    )
    transport = ScriptedLeanPaperProofTransport()

    class RejectFirstChecker(RecordingLeanChecker):
        def __call__(self, request, *, artifact_store, environment_manifest):
            self.status = (
                LeanCheckerStatus.REJECTED
                if not self.requests
                else LeanCheckerStatus.ACCEPTED
            )
            return super().__call__(
                request,
                artifact_store=artifact_store,
                environment_manifest=environment_manifest,
            )

    checker = RejectFirstChecker()
    result = formal_runner.dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "lean-trace").as_posix(),
        transport=transport,
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=1024,
        timeout_seconds=30,
        checker=checker,
        trace_context=context,
    )

    assert PaperTraceRuntimeContext.current_provider_call_count == 0
    protocol_runtime = result.run_evidence["protocol_runtime"]
    assert protocol_runtime["event_ledger_path"] == "events/event_log.jsonl"
    native_ledger, native_events = formal_runner._validated_native_event_ledger(
        native_root=Path(result.output_root),
        protocol_runtime=protocol_runtime,
    )
    assert native_ledger.verify_hash_chain() is True
    assert len(native_events) == len(result.event_records)
    assert transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert checker.requests
    event_types = {event["event_type"] for event in result.event_records}
    assert {
        "TASK_REGISTERED",
        "LEASE_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
        "VERIFICATION_RECORDED",
        "CANONICAL_OUTPUTS_BOUND",
    } <= event_types
    assert any(
        event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
        for event in result.event_records
    )
    assert result.merge_summary["status"] == "completed"
    assert any(
        attempt.attempt_status is PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )
    captured_facts = []
    evaluate_evidence = formal_runner.evaluate_versioned_paper_evidence

    def capture_evidence(facts):
        captured_facts.append(facts)
        return evaluate_evidence(facts)

    monkeypatch.setattr(
        formal_runner,
        "evaluate_versioned_paper_evidence",
        capture_evidence,
    )
    missing_receipt = formal_runner._evaluate_trace_root_evidence(
        adapter_result=result,
        adapter_root=Path(result.output_root),
        trace_runtime=context,
    )
    manifest = context.resolver.index.manifest
    paid_context = replace(
        context,
        paid_receipt_claim={
            "schema_version": "tokenshare.paid_execution_receipt_claim.v1",
            "receipt_scope": "epd027_full_bank_acquisition",
            "receipt_digest": manifest.created_by_paid_receipt_digest,
            "manifest_digest": manifest.manifest_digest,
        },
    )
    paid = formal_runner._evaluate_trace_root_evidence(
        adapter_result=result,
        adapter_root=Path(result.output_root),
        trace_runtime=paid_context,
    )
    assert missing_receipt.paper_eligible is False
    assert "paid_full_acquisition_receipt_required" in (
        missing_receipt.ineligibility_reasons
    )
    assert paid.paper_eligible is True
    facts = captured_facts[-1]
    planned_ids = tuple(
        binding["planned_ai_unit_id"] for binding in facts.executed_unit_bindings
    )
    unit_ids = tuple(
        binding["unit_id"] for binding in facts.executed_unit_bindings
    )
    assert facts.executed_ai_unit_count == len(set(planned_ids)) == len(planned_ids)
    assert len(set(unit_ids)) == len(unit_ids)
    assert len(facts.current_lifecycle_refs) == len(unit_ids)
    rejected_planned_id = next(
        attempt.planned_ai_unit_id
        for attempt in result.attempt_results
        if attempt.attempt_status is PaperAttemptStatus.CHECKER_REJECTED
    )
    rejected_source_binding = next(
        binding
        for binding in facts.trace_source_bindings
        if binding["planned_ai_unit_id"] == rejected_planned_id
    )
    assert [
        replacement["replacement_slot"]
        for replacement in rejected_source_binding["replacements"]
    ] == [0, 1]
    assert len({
        replacement["entry_id"]
        for replacement in rejected_source_binding["replacements"]
    }) == 2


def _run_normal_exp3_trace_condition(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_kind: str | None,
    requested_child_count: int = 1,
    trace_replacement_count: int = 1,
    return_context: bool = False,
    enforce_publication_closure: bool = True,
    enable_metric_closure: bool = False,
    selected_execution: bool = False,
):
    from tests.experiments.test_paper_full_resource_trace import (
        _pressure_factor_cases,
        _pressure_trace_context,
    )
    from tokenshare.experiments.paper_response_bank import (
        PaperFormalTraceContext,
        PaperTraceCaseBinding,
        PaperTraceRuntimeContext,
        SemanticInventoryPlan,
    )

    experiment_id = "exp3_real_ai_fault_recovery"
    suite_root = tmp_path / "suite"
    bank_root = tmp_path / "external-bank"
    cases = _pressure_factor_cases(2 if mutation_kind else 1)
    if requested_child_count != 1:
        from tokenshare.experiments.paper_factorization_catalog import (
            generate_factorization_paper_cases,
        )

        cases = (
            next(
                case
                for case in generate_factorization_paper_cases()
                if case["split_params"]["requested_child_count"]
                == requested_child_count
                and case["factor_position_quantile"] == "late"
            ),
        )
    case_ids = tuple(str(case["case_id"]) for case in cases)
    config = _ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    base_condition, base_selection = plan.bound_items()[0]
    condition = replace(
        base_condition,
        experiment_id=experiment_id,
        condition_id="condition-exp3-current-trace",
        fault_type="false_positive",
    )
    selection = replace(
        base_selection,
        experiment_id=experiment_id,
        ordered_case_ids=case_ids,
        expected_ai_unit_count=requested_child_count * len(case_ids),
    )
    plan = replace(
        plan,
        experiment_id=experiment_id,
        output_root=(suite_root / experiment_id).as_posix(),
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    if requested_child_count == 1 and trace_replacement_count == 1:
        trace_context = _pressure_trace_context(
            bank_root=bank_root,
            condition_id=condition.condition_id,
            cases=cases,
            provider_config_digest=condition.source_provider_config_digest,
            model_entry_id=condition.model_entry_id,
            provider_family=condition.provider_family,
            provider_model_id=condition.provider_model_id,
        )
    else:
        from tests.experiments.test_factorization_paper_adapter import _v2_condition
        from tokenshare.executors.response_bank import canonical_digest
        from tokenshare.experiments.factorization_paper_adapter import (
            ScriptedFactorizationRangeTransport,
            run_factorization_paper_case,
        )

        source = run_factorization_paper_case(
            case=cases[0],
            condition=_v2_condition(cases[0]),
            output_root=tmp_path / "source-adapter",
            transport=ScriptedFactorizationRangeTransport(),
            real_transport=False,
        )
        runtime = _trace_context_from_adapter_result(
            bank_root=bank_root,
            adapter_result=source,
            replacement_count=trace_replacement_count,
            case_record_digest=canonical_digest(cases[0]),
            provider_config_digest=condition.source_provider_config_digest,
            model_entry_id=condition.model_entry_id,
            provider_family=condition.provider_family,
            provider_model_id=condition.provider_model_id,
        )
        rows = runtime.inventory_rows
        trace_context = PaperFormalTraceContext(
            inventory_plan=SemanticInventoryPlan(
                schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
                inventory_digest=runtime.resolver.index.manifest.inventory_digest,
                rows=rows,
                condition_refs=(
                    {
                        "condition_id": condition.condition_id,
                        "condition_digest": condition.condition_digest,
                        "experiment_id": condition.experiment_id,
                        "worker_count": condition.worker_count,
                        "repeat_id": condition.repeat_id,
                        "fault_type": condition.fault_type,
                        "ablation_mode": condition.ablation_mode,
                        "semantic_slot_keys": [row.semantic_slot_key for row in rows],
                        "case_refs": [
                            {
                                "case_id": cases[0]["case_id"],
                                "case_record_digest": canonical_digest(cases[0]),
                                "semantic_slot_keys": [
                                    row.semantic_slot_key for row in rows
                                ],
                            }
                        ],
                    },
                ),
                exp2_online_condition_refs=(),
                max_concurrent_roots=1,
                expected_slot_count=len(rows),
                terminal_provider_failure_count=0,
                terminal_success_count=len(runtime.resolver.index.entries),
                terminal_unacquired_count=0,
            ),
            cases=(
                PaperTraceCaseBinding(
                    condition_id=condition.condition_id,
                    case_id=cases[0]["case_id"],
                    case_record_digest=canonical_digest(cases[0]),
                    runtime=runtime,
                    inventory_entry_ids=tuple(
                        row.inventory_entry_id for row in rows
                    ),
                ),
            ),
        )
    first_runtime = trace_context.runtime_for(
        condition_id=condition.condition_id,
        case_id=case_ids[0],
    )
    first_entry_id = first_runtime.bindings[0].replacements[0].entry_id
    first_entry = first_runtime.resolver.entry(first_entry_id)
    if mutation_kind == "root":
        bank_root.rename(tmp_path / "external-bank-missing")
    elif mutation_kind in {"object", "role"}:
        role = "raw_output" if mutation_kind == "object" else "usage"
        locator = next(
            item for item in first_entry.object_locators if item.object_role == role
        )
        object_path = bank_root / "objects" / locator.object_digest.removeprefix(
            "sha256:"
        )
        if mutation_kind == "object":
            object_path.write_bytes(b"corrupted immutable source object")
        else:
            object_path.unlink()
    elif mutation_kind == "entry":
        first_runtime.resolver.index._entries_by_id.pop(first_entry_id)

    dispatched: list[str] = []
    original_dispatch = formal_runner._FormalConditionExecutionCallback._dispatch_root_case

    def tracked_dispatch(self, **kwargs):
        dispatched.append(str(kwargs["case_id"]))
        return original_dispatch(self, **kwargs)

    monkeypatch.setattr(
        formal_runner._FormalConditionExecutionCallback,
        "_dispatch_root_case",
        tracked_dispatch,
    )

    class Exp3TraceConditionModule:
        def expand_conditions(self, context):
            raise AssertionError("Exp3 trace run consumes the frozen formal plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
                execution_manifest={
                    "baseline_policy": "required_by_formal_plan",
                    "fault_target_manifest": {
                        "fault_type": condition.fault_type,
                        "selected_target_ai_unit_ids": [],
                        "reserve_target_ai_unit_ids": [],
                    },
                    "worker_death_manifest": None,
                },
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=experiment_id, rows=())

    monkeypatch.setitem(
        formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((experiment_id, Exp3TraceConditionModule()),),
    )

    class ForbiddenProviderTransport:
        def post_chat_completion(self, **_kwargs):
            raise AssertionError("Exp3 current provider dispatch is forbidden")

    kwargs = _formal_execution_kwargs(
        tmp_path=suite_root,
        config=config,
        plan=plan,
    )
    runner_catalog = {
                "catalog_digest": condition.catalog_digest,
                "factorization_cases": cases,
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
    }
    kwargs.update(
        {
            "catalog_manifest": runner_catalog,
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=len(cases),
                    planned_ai_units=requested_child_count * len(cases),
                    max_provider_attempts=3 * len(cases),
                ),
                *plan.bound_items(),
            ),
            "transport": ForbiddenProviderTransport(),
            "trace_context": trace_context,
            "enforce_publication_closure": enforce_publication_closure,
            "enable_metric_closure": enable_metric_closure,
        }
    )
    if selected_execution:
        kwargs.update(
            {
                "selected_condition_ids": (condition.condition_id,),
                "root_case_filter": {
                    condition.condition_id: selection.ordered_case_ids
                },
                "execution_output_root_by_experiment": {
                    experiment_id: suite_root / experiment_id
                },
            }
        )
    suite = formal_runner.execute_paper_formal_suite(**kwargs)
    tasks = _generation_records(
        suite_root,
        experiment_id,
        condition.condition_id,
        "per_task_results.jsonl",
    )
    attempts = _generation_records(
        suite_root,
        experiment_id,
        condition.condition_id,
        "per_attempt_results.jsonl",
    )
    assert PaperTraceRuntimeContext.current_provider_call_count == 0
    result = (suite, tasks, attempts, dispatched, first_entry, suite_root)
    if return_context:
        return (*result, plan, runner_catalog, trace_context)
    return result


def test_results_first_metric_closure_persists_direct_artifacts_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, *_rest, suite_root = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=None,
        enforce_publication_closure=False,
        enable_metric_closure=True,
    )

    assert suite.status is PaperStatus.COMPLETED, suite.error_summary
    suite_manifest = json.loads(
        (suite_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    assert "traceability_replay_input_root_ref" in suite_manifest
    loaded = formal_runner.load_paper_traceability_replay_inputs(suite_root)
    assert len(loaded.direct["exp3_trace_robustness"]) == 1
    direct_rows = tuple(
        direct
        for condition in loaded.direct["exp3_trace_robustness"]
        for direct in condition.direct_results
    )
    assert len(direct_rows) == 1
    assert direct_rows[0].trace_resource_book_ref is not None
    assert direct_rows[0].trace_resource_book_ref.artifact_type == "CurrentTraceWrapper"
    assert (
        direct_rows[0].trace_resource_book_ref.artifact_schema_id
        == "tokenshare.current_trace_wrapper"
    )
    assert direct_rows[0].trace_resource_book_ref.artifact_schema_version == "v1"
    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        suite_root
    )
    assert metrics.observations_digest.startswith("sha256:")
    assert metrics.output_refs

    import tokenshare.experiments.paper_formal_evidence as formal_evidence

    protected_root = (
        suite_root.with_name(suite_root.name + ".traceability_replay_inputs")
        / "current_evidence"
    )
    evidence_store = formal_evidence.FormalEvidenceStore(protected_root)
    direct = direct_rows[0]
    assert direct.execution_binding is not None
    binding = direct.execution_binding
    wrappers = next(iter(loaded.current["current_trace_wrappers_by_root"].values()))
    trace_source_bindings = next(
        iter(loaded.source["trace_source_bindings_by_root"].values())
    )
    logical = evidence_store.load_logical_run_records(
        experiment_id=direct.experiment_id,
        condition_id=direct.condition_id,
        repeat_id=direct.repeat_id,
    )
    artifact_records = tuple(
        value
        for value in logical["artifacts"]
        if value.get("task_id") == binding.task_id
    )
    extra_locator = replace(
        direct.source_bank_object_locators[0],
        entry_id="entry-version-invariant-extra",
    )
    v1_multi_entry_direct = SimpleNamespace(
        trace_resource_book_ref=direct.trace_resource_book_ref,
        source_bank_object_locators=(
            *direct.source_bank_object_locators,
            extra_locator,
        ),
    )
    with pytest.raises(ValueError, match="producer version invariant"):
        formal_evidence._validate_persisted_trace_resource_book(
            store=evidence_store,
            direct=v1_multi_entry_direct,
            binding=binding,
            wrappers=wrappers,
            trace_source_bindings=trace_source_bindings,
            attempt_records=tuple(logical["attempts"]),
            events=tuple(logical["events"]),
            persisted_artifact_records=artifact_records,
        )


def test_multi_wrapper_v2_trace_lineage_survives_protected_recompute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, *_rest, suite_root = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=None,
        requested_child_count=2,
        enforce_publication_closure=False,
        enable_metric_closure=True,
    )

    assert suite.status is PaperStatus.COMPLETED, suite.error_summary
    loaded = formal_runner.load_paper_traceability_replay_inputs(suite_root)
    wrappers_by_root = loaded.current["current_trace_wrappers_by_root"]
    assert len(wrappers_by_root) == 1
    wrappers = next(iter(wrappers_by_root.values()))
    assert len(wrappers) > 1
    trace_rows = tuple(
        direct
        for condition in loaded.direct["exp3_trace_robustness"]
        for direct in condition.direct_results
    )
    assert len(trace_rows) == 1
    assert trace_rows[0].trace_resource_book_ref is not None
    assert (
        trace_rows[0].trace_resource_book_ref.artifact_type
        == "PaperTraceResourceBook"
    )

    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        suite_root
    )

    assert metrics.observations_digest.startswith("sha256:")
    assert metrics.output_refs

    import tokenshare.experiments.paper_formal_evidence as formal_evidence

    protected_root = (
        suite_root.with_name(suite_root.name + ".traceability_replay_inputs")
        / "current_evidence"
    )
    evidence_store = formal_evidence.FormalEvidenceStore(protected_root)
    direct = trace_rows[0]
    assert direct.execution_binding is not None
    binding = direct.execution_binding
    trace_source_bindings = next(
        iter(loaded.source["trace_source_bindings_by_root"].values())
    )
    logical = evidence_store.load_logical_run_records(
        experiment_id=direct.experiment_id,
        condition_id=direct.condition_id,
        repeat_id=direct.repeat_id,
    )
    artifact_records = tuple(
        value
        for value in logical["artifacts"]
        if value.get("task_id") == binding.task_id
    )
    resource_record = formal_evidence._unique_artifact_record_for_snapshot(
        artifact_records,
        direct.trace_resource_book_ref,
    )
    resource_ref = resource_record["source_artifact_ref"]
    resource_body = formal_evidence._read_verified_persisted_artifact_json(
        evidence_store,
        resource_record,
    )
    events = tuple(logical["events"])
    commits = formal_evidence._trace_wrapper_commits_by_attempt(
        events,
        task_id=binding.task_id,
    )
    canonical_refs_by_attempt = {}
    native_refs_by_attempt = {}
    prepared_by_attempt = {}
    for wrapper in wrappers:
        _, native_ref = commits[wrapper.current_attempt_id]
        canonical_record = formal_evidence._canonical_trace_wrapper_record(
            artifact_records,
            native_ref,
        )
        canonical_refs_by_attempt[wrapper.current_attempt_id] = canonical_record[
            "source_artifact_ref"
        ]
        native_refs_by_attempt[wrapper.current_attempt_id] = native_ref
        prepared_by_attempt[wrapper.current_attempt_id] = (
            formal_evidence._read_verified_persisted_artifact_json(
                evidence_store,
                canonical_record,
            )
        )

    first_wrapper = wrappers[0]
    first_entry_id = first_wrapper.entry_id
    v2_single_entry_body = json.loads(json.dumps(resource_body))
    v2_single_entry_body["replacement_entry_ids"] = [first_entry_id]
    v2_single_entry_body["current_wrappers"] = [
        v2_single_entry_body["current_wrappers"][0]
    ]
    v2_single_entry_body["current_wrapper_refs"] = [
        v2_single_entry_body["current_wrapper_refs"][0]
    ]
    v2_single_entry_body["source_bank_object_locators"] = [
        value
        for value in v2_single_entry_body["source_bank_object_locators"]
        if value["entry_id"] == first_entry_id
    ]
    v2_single_entry_direct = SimpleNamespace(
        preregistered_root_run_id=direct.preregistered_root_run_id,
        source_bank_object_locators=tuple(
            value
            for value in direct.source_bank_object_locators
            if value.entry_id == first_entry_id
        ),
    )
    v2_single_entry_ref = json.loads(json.dumps(resource_ref))
    v2_single_entry_ref["source"]["source_artifact_refs"] = [
        native_refs_by_attempt[first_wrapper.current_attempt_id]
    ]
    with pytest.raises(ValueError, match="producer version invariant"):
        formal_evidence._validate_trace_resource_book_v2_body(
            body=v2_single_entry_body,
            resource_ref=v2_single_entry_ref,
            direct=v2_single_entry_direct,
            binding=binding,
            wrappers=(first_wrapper,),
            canonical_refs_by_attempt={
                first_wrapper.current_attempt_id: canonical_refs_by_attempt[
                    first_wrapper.current_attempt_id
                ]
            },
            native_refs_by_attempt={
                first_wrapper.current_attempt_id: native_refs_by_attempt[
                    first_wrapper.current_attempt_id
                ]
            },
        )

    def rejected_v2_body(mutated: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="persisted trace resource book"):
            formal_evidence._validate_trace_resource_book_v2_body(
                body=mutated,
                resource_ref=resource_ref,
                direct=direct,
                binding=binding,
                wrappers=wrappers,
                canonical_refs_by_attempt=canonical_refs_by_attempt,
                native_refs_by_attempt=native_refs_by_attempt,
            )

    tampered = json.loads(json.dumps(resource_body))
    tampered["current_wrappers"][0]["entry_id"] = "entry-tampered"
    rejected_v2_body(tampered)
    tampered = json.loads(json.dumps(resource_body))
    tampered["unexpected_producer_field"] = True
    rejected_v2_body(tampered)
    tampered_ref = json.loads(json.dumps(resource_ref))
    tampered_ref["artifact_schema_version"] = "v999"
    with pytest.raises(ValueError, match="persisted trace resource book"):
        formal_evidence._validate_trace_resource_book_v2_body(
            body=resource_body,
            resource_ref=tampered_ref,
            direct=direct,
            binding=binding,
            wrappers=wrappers,
            canonical_refs_by_attempt=canonical_refs_by_attempt,
            native_refs_by_attempt=native_refs_by_attempt,
        )
    tampered = json.loads(json.dumps(resource_body))
    tampered["current_wrappers"].reverse()
    rejected_v2_body(tampered)
    tampered = json.loads(json.dumps(resource_body))
    tampered["current_wrapper_refs"].reverse()
    rejected_v2_body(tampered)
    tampered = json.loads(json.dumps(resource_body))
    tampered["source_bank_object_locators"].reverse()
    rejected_v2_body(tampered)
    tampered = json.loads(json.dumps(resource_body))
    tampered["current_wrapper_refs"][0]["content_hash"] = "sha256:" + "9" * 64
    rejected_v2_body(tampered)
    tampered = json.loads(json.dumps(resource_body))
    tampered["source_bank_object_locators"][0]["object_digest"] = (
        "sha256:" + "8" * 64
    )
    rejected_v2_body(tampered)
    for field_name in ("preregistered_root_run_id", "task_id", "execution_id"):
        tampered = json.loads(json.dumps(resource_body))
        tampered[field_name] = f"tampered-{field_name}"
        rejected_v2_body(tampered)

    tampered_events = json.loads(json.dumps(events))
    commit = next(
        value
        for value in tampered_events
        if value.get("event_type") == "TRACE_DELIVERY_COMMITTED.v1"
        and value.get("task_id") == binding.task_id
    )
    commit["payload"]["current_wrapper_ref"]["content_hash"] = "sha256:" + "7" * 64
    with pytest.raises(ValueError, match="persisted trace wrapper projection"):
        formal_evidence._validate_persisted_trace_resource_book(
            store=evidence_store,
            direct=direct,
            binding=binding,
            wrappers=wrappers,
            trace_source_bindings=trace_source_bindings,
            attempt_records=tuple(logical["attempts"]),
            events=tampered_events,
            persisted_artifact_records=artifact_records,
        )

    prepared = prepared_by_attempt[first_wrapper.current_attempt_id]
    tampered_prepared = json.loads(json.dumps(prepared))
    tampered_prepared["unexpected_producer_field"] = True
    with pytest.raises(ValueError, match="prepared trace delivery"):
        formal_evidence._validate_prepared_trace_delivery(
            tampered_prepared,
            first_wrapper,
        )
    tampered_prepared = json.loads(json.dumps(prepared))
    tampered_prepared["child_worker_id"] = "tampered-worker"
    with pytest.raises(ValueError, match="delivery_digest"):
        formal_evidence._validate_prepared_trace_delivery(
            tampered_prepared,
            first_wrapper,
        )
    typed_delivery = formal_evidence.PreparedTraceDelivery.from_dict(prepared)
    formal_evidence._validate_prepared_trace_delivery_binding(
        typed_delivery,
        trace_source_bindings,
    )
    with pytest.raises(ValueError, match="binding is unreachable"):
        formal_evidence._validate_prepared_trace_delivery_binding(
            typed_delivery,
            (),
        )
    matched_binding = next(
        value
        for value in trace_source_bindings
        if value.binding_digest == typed_delivery.binding_digest
    )
    with pytest.raises(ValueError, match="binding digest is ambiguous"):
        formal_evidence._validate_prepared_trace_delivery_binding(
            typed_delivery,
            (matched_binding, matched_binding),
        )
    swapped_attempts = json.loads(json.dumps(logical["attempts"]))
    selected_attempts = [
        value
        for value in swapped_attempts
        if value.get("attempt_id") in {wrapper.current_attempt_id for wrapper in wrappers}
    ]
    assert len(selected_attempts) == 2
    selected_attempts[0]["planned_ai_unit_id"], selected_attempts[1][
        "planned_ai_unit_id"
    ] = (
        selected_attempts[1]["planned_ai_unit_id"],
        selected_attempts[0]["planned_ai_unit_id"],
    )
    with pytest.raises(ValueError, match="planned unit identity mismatch"):
        formal_evidence._validate_persisted_trace_resource_book(
            store=evidence_store,
            direct=direct,
            binding=binding,
            wrappers=wrappers,
            trace_source_bindings=trace_source_bindings,
            attempt_records=swapped_attempts,
            events=events,
            persisted_artifact_records=artifact_records,
        )
    conflicting_digest_attempts = json.loads(json.dumps(logical["attempts"]))
    selected_attempt = next(
        value
        for value in conflicting_digest_attempts
        if value.get("attempt_id") == first_wrapper.current_attempt_id
    )
    selected_attempt["source_binding_digest"] = "sha256:" + "6" * 64
    with pytest.raises(ValueError, match="binding digest identity mismatch"):
        formal_evidence._validate_persisted_trace_resource_book(
            store=evidence_store,
            direct=direct,
            binding=binding,
            wrappers=wrappers,
            trace_source_bindings=trace_source_bindings,
            attempt_records=conflicting_digest_attempts,
            events=events,
            persisted_artifact_records=artifact_records,
        )
    duplicate_attempts = (
        *logical["attempts"],
        dict(logical["attempts"][0]),
    )
    with pytest.raises(ValueError, match="attempt identity is ambiguous"):
        formal_evidence._validate_persisted_trace_resource_book(
            store=evidence_store,
            direct=direct,
            binding=binding,
            wrappers=wrappers,
            trace_source_bindings=trace_source_bindings,
            attempt_records=duplicate_attempts,
            events=events,
            persisted_artifact_records=artifact_records,
        )


def test_multi_replacement_binding_allows_one_authoritative_current_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, *_rest, suite_root = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=None,
        requested_child_count=1,
        trace_replacement_count=2,
        enforce_publication_closure=False,
        enable_metric_closure=True,
    )

    assert suite.status is PaperStatus.COMPLETED, suite.error_summary
    loaded = formal_runner.load_paper_traceability_replay_inputs(suite_root)
    wrappers = next(iter(loaded.current["current_trace_wrappers_by_root"].values()))
    source_bindings = next(
        iter(loaded.source["trace_source_bindings_by_root"].values())
    )
    trace_rows = tuple(
        direct
        for condition in loaded.direct["exp3_trace_robustness"]
        for direct in condition.direct_results
    )
    assert len(wrappers) == 1
    assert len(source_bindings) == 1
    assert len(source_bindings[0].replacements) == 2
    assert {wrapper.entry_id for wrapper in wrappers} < {
        replacement.entry_id
        for binding in source_bindings
        for replacement in binding.replacements
    }
    assert {
        locator.entry_id for locator in trace_rows[0].source_bank_object_locators
    } == {
        replacement.entry_id
        for binding in source_bindings
        for replacement in binding.replacements
    }
    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        suite_root
    )
    assert metrics.observations_digest.startswith("sha256:")


def _persist_genuine_direct_lineage_closure(
    tmp_path: Path,
    *,
    specifications: tuple[tuple[str, str, str, str], ...] | None = None,
) -> SimpleNamespace:
    from tests.experiments import test_paper_direct_results as direct_fixtures
    from tests.experiments import test_paper_formal_evidence as formal_fixtures
    from tokenshare.executors.response_bank import ResponseBankResolver
    from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity
    from tokenshare.experiments.paper_direct_results import (
        build_canonical_direct_evidence,
    )
    from tokenshare.experiments.paper_formal_evidence import (
        FormalEvidenceStore,
        _typed_direct_results_in_inputs,
        build_canonical_lineage_inputs,
    )
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
        recompute_paper_formal_metrics,
    )
    from tokenshare.experiments.paper_traceability import (
        _load_protected_replay_inputs,
    )

    if specifications is None:
        specifications = (
        (
            "exp1-factor",
            "exp1_real_ai_feasibility",
            "factorization",
            "online_real_provider",
        ),
        (
            "exp4-factor",
            "exp4_real_ai_protocol_ablation",
            "factorization",
            "real_model_trace_protocol_run",
        ),
        (
            "exp4-lean",
            "exp4_real_ai_protocol_ablation",
            "lean_proof",
            "real_model_trace_protocol_run",
        ),
        (
            "exp5-factor",
            "exp5_real_ai_model_endpoint_comparison",
            "factorization",
            "online_real_provider",
        ),
        (
            "exp5-lean",
            "exp5_real_ai_model_endpoint_comparison",
            "lean_proof",
            "online_real_provider",
        ),
    )
    inventory_rows = []
    condition_manifests = []
    catalogs = []
    evidence_by_root = {}
    source_by_root = {}

    for label, experiment_id, domain, evidence_class in specifications:
        root_id = f"lineage-{label}"
        condition_id = f"condition-{label}"
        case_id = f"case-{label}"
        base, _condition_manifest, _catalog = direct_fixtures._inventory_row(
            root_id=root_id,
            condition_id=condition_id,
            case_id=case_id,
            evidence_class=evidence_class,
        )
        axes = {
            **base.condition_axes,
            "domain": domain,
            "difficulty": (
                "hard_frontier" if domain == "lean_proof" else "hard"
            ),
            "topic_family": "pure_logic" if domain == "lean_proof" else None,
            "ablation_mode": (
                "FULL"
                if experiment_id == "exp4_real_ai_protocol_ablation"
                else None
            ),
            "model_endpoint_id": (
                "zai-org/GLM-5.2"
                if experiment_id == "exp5_real_ai_model_endpoint_comparison"
                else "deepseek_v4_pro_exp1_baseline"
            ),
            "fault_condition": (
                "false_positive"
                if experiment_id == "exp3_real_ai_fault_recovery"
                else None
            ),
            "death_condition": None,
        }
        condition_manifest, condition_ref = direct_fixtures._condition_manifest(
            condition_id,
            axes,
        )
        if domain == "factorization":
            catalog, case_records = direct_fixtures._catalog(
                [(case_id, "early", "front")]
            )
            case_ref = direct_fixtures._case_ref(catalog, case_records[case_id])
        else:
            case_axes = {
                "factor_position_quantile": "early",
                "position_stratum": "front",
            }
            theorem = direct_fixtures._test_lean_root_theorem_body(case_id)
            case_body = {
                "schema_version": "tokenshare.preregistered_lean_case_record.v1",
                "case_id": case_id,
                "domain": "lean_proof",
                "difficulty": "hard_frontier",
                "case_axes_digest": formal_runner.digest_json(case_axes),
                **case_axes,
                "official_case_digest": (
                    direct_fixtures._test_lean_official_case_digest(case_id)
                ),
                "official_root_theorem_id": theorem["theorem_id"],
                "official_root_theorem_payload_digest": theorem["payload_digest"],
            }
            case_record = {
                **case_body,
                "case_record_digest": formal_runner.digest_json(case_body),
            }
            catalog_body = {
                "schema_version": "tokenshare.preregistered_case_catalog_manifest.v1",
                "records": [case_record],
            }
            catalog = {
                **catalog_body,
                "catalog_digest": formal_runner.digest_json(catalog_body),
            }
            case_ref = {
                "schema_version": "tokenshare.preregistered_lean_case_ref.v1",
                "catalog_digest": catalog["catalog_digest"],
                "case_record_digest": case_record["case_record_digest"],
                "case_axes_digest": case_record["case_axes_digest"],
                "factor_position_quantile": case_record[
                    "factor_position_quantile"
                ],
                "position_stratum": case_record["position_stratum"],
                "official_case_digest": case_record["official_case_digest"],
                "official_root_theorem_id": case_record[
                    "official_root_theorem_id"
                ],
                "official_root_theorem_payload_digest": case_record[
                    "official_root_theorem_payload_digest"
                ],
            }
        row = direct_fixtures._replace_inventory_row(
            base,
            experiment_id=experiment_id,
            preregistered_condition_ref=condition_ref,
            condition_axes=axes,
            preregistered_case_ref=case_ref,
        )
        execution_id = f"execution-{label}"
        task_id = f"task-{label}"
        unit_id = f"unit-{label}"
        artifact_store = ArtifactStore(tmp_path / "canonical-sources" / label)
        ledger = EventLedger(artifact_store.root_path / "events" / "ledger.jsonl")
        theorem = direct_fixtures._test_lean_root_theorem_body(case_id)
        proof_source = b"by\n  trivial\n"
        proof_ref = (
            artifact_store.save_bytes(
                proof_source,
                artifact_id=f"proof-{label}",
                artifact_type="LeanProofArtifact",
                media_type="text/x-lean",
                artifact_schema_id="lean_proof.proof_artifact",
                artifact_schema_version="v1",
                source={
                    "kind": "lean_checker",
                    "request_id": f"request:{case_id}",
                },
                metadata={
                    "proof_digest": formal_runner.digest_json(
                        {
                            "theorem_payload_digest": theorem["payload_digest"],
                            "proof_source": proof_source.decode("utf-8"),
                        }
                    )
                },
                created_at="2026-08-01T00:00:00Z",
            )
            if domain == "lean_proof"
            else None
        )
        final_ref = direct_fixtures._save_role_artifact(
            artifact_store,
            label=f"final-{label}",
            role="final_result",
            execution_id=execution_id,
            task_id=task_id,
            body={
                "schema_version": "factorization.prime_factorization_result.v1",
                "target_n": "91",
                "prime_factors": [
                    {"prime": "7", "exponent": 1},
                    {"prime": "13", "exponent": 1},
                ],
            },
            source_extra=(
                {"source_ref": proof_ref.to_dict()} if proof_ref is not None else None
            ),
        )
        parser_ref = direct_fixtures._save_role_artifact(
            artifact_store,
            label=f"parser-{label}",
            role="parser_result",
            execution_id=execution_id,
            task_id=task_id,
        )
        if domain == "lean_proof":
            binding_ref, report_ref = direct_fixtures._lean_domain_verdict_refs(
                store=artifact_store,
                row=row,
                execution_id=execution_id,
                task_id=task_id,
                root_unit_id=unit_id,
                final_ref=final_ref,
                status="accepted",
            )
            verifier_refs = (binding_ref, report_ref)
        else:
            verifier_refs = (
                direct_fixtures._factor_domain_verdict_ref(
                    store=artifact_store,
                    row=row,
                    execution_id=execution_id,
                    task_id=task_id,
                    root_unit_id=unit_id,
                    final_ref=final_ref,
                    correct=True,
                ),
            )
        current_refs = (
            tuple(
                direct_fixtures._save_role_artifact(
                    artifact_store,
                    label=f"{role}-{label}",
                    role=role,
                    execution_id=execution_id,
                    task_id=task_id,
                )
                for role in (
                    "request_body",
                    "raw_output_or_provider_failure",
                    "provenance",
                    "usage_status",
                    "latency",
                    "pricing",
                    "provider_attempt",
                    "model_record",
                )
            )
            if evidence_class == "online_real_provider"
            else ()
        )
        resource_ref = direct_fixtures._save_role_artifact(
            artifact_store,
            label=f"resource-{label}",
            role=(
                "actual_resource_book"
                if evidence_class == "online_real_provider"
                else "trace_resource_book"
            ),
            execution_id=execution_id,
            task_id=task_id,
        )
        base_unit = {"unit_id": unit_id, "task_id": task_id, "state": "Ready"}
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=direct_fixtures.EventType.TASK_UNIT_CREATED,
            suffix=f"{label}-created",
            payload={"task_unit": base_unit},
        )
        for event_type, state, suffix in (
            (
                direct_fixtures.EventType.EXECUTION_REQUEST_RECORDED,
                "Running",
                "request",
            ),
            (
                direct_fixtures.EventType.EXECUTION_SUBMISSION_RECORDED,
                "Submitted",
                "submission",
            ),
            (
                direct_fixtures.EventType.VERIFICATION_RECORDED,
                "Verified",
                "verification",
            ),
        ):
            direct_fixtures._append_event(
                ledger,
                task_id=task_id,
                event_type=event_type,
                suffix=f"{label}-{suffix}",
                payload={
                    "schema_version": f"paper_direct_fixture.{suffix}.v1",
                    "task_id": task_id,
                    "unit_id": unit_id,
                    "attempt_id": f"attempt-{label}",
                    "state": state,
                },
            )
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=direct_fixtures.EventType.CANONICAL_OUTPUTS_BOUND,
            suffix=f"{label}-canonical",
            payload={
                "canonical_selection": {
                    "unit_id": unit_id,
                    "canonical_output_refs": {"answer": final_ref.to_dict()},
                }
            },
        )
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=direct_fixtures.EventType.MERGE_RECORDED,
            suffix=f"{label}-merge",
            payload={
                "schema_version": "phase5.merge_recorded.v1",
                "task_id": task_id,
                "parent_unit_id": unit_id,
                "merge_output_refs": {"answer": final_ref.to_dict()},
                "merge_record": {
                    "task_id": task_id,
                    "parent_unit_id": unit_id,
                    "merge_output_refs": {"answer": final_ref.to_dict()},
                },
            },
        )
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=direct_fixtures.EventType.TASK_UNIT_STATE_CHANGED,
            suffix=f"{label}-terminal",
            payload={"task_unit": {**base_unit, "state": "Completed"}},
        )
        runtime = direct_fixtures.project_protocol_run(
            run_id=execution_id,
            task_id=task_id,
            root_unit_id=unit_id,
            event_ledger=ledger,
            artifact_store=artifact_store,
        )
        kwargs = {
            "inventory_row": row,
            "execution_id": execution_id,
            "event_ledger": ledger,
            "artifact_store": artifact_store,
            "runtime_result": runtime,
            "final_result_ref": final_ref,
            "parser_refs": (parser_ref,),
            "verifier_checker_refs": verifier_refs,
            **(
                {
                    "current_provider_object_refs": current_refs,
                    "actual_resource_book_ref": resource_ref,
                }
                if evidence_class == "online_real_provider"
                else {
                    "source_bank_object_locators": tuple(
                        direct_fixtures.ExternalBankObjectLocator(
                            bank_root_id="approved-bank",
                            manifest_digest=direct_fixtures._digest("bank-manifest"),
                            entry_id="entry-1",
                            object_role=role,
                            object_digest=direct_fixtures._digest(f"source:{role}"),
                        )
                        for role in (
                            "request_body",
                            "raw_output_or_provider_failure",
                            "provenance",
                            "usage_status",
                            "latency",
                            "pricing",
                            "acquisition_attempt",
                            "model_record",
                        )
                    ),
                    "trace_resource_book_ref": resource_ref,
                }
            ),
        }
        evidence = build_canonical_direct_evidence(**kwargs)
        inventory_rows.append(row)
        condition_manifests.append(condition_manifest)
        catalogs.append(catalog)
        evidence_by_root[root_id] = evidence
        source_by_root[root_id] = (kwargs, ledger, artifact_store, runtime)

    projection = direct_fixtures._project(
        direct_fixtures._inventory_manifest(*inventory_rows),
        tuple(condition_manifests),
        tuple(catalogs),
        evidence_by_root,
    )
    direct_by_root = {
        value.preregistered_root_run_id: value for value in projection.rows
    }
    exp5_identity = PaperModelEndpointIdentity(
        model_cohort_id="cohort-v3",
        model_cohort_digest="sha256:" + "1" * 64,
        cohort_member_id="glm-siliconflow",
        provider_config_id="siliconflow",
        selected_entry_id="zai-org/GLM-5.2",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="thinking",
        effective_reasoning_controls={
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        source_provider_config_digest="sha256:" + "2" * 64,
    )
    producer_facts = {
        root_id: (
            {"model_endpoint_identity": exp5_identity}
            if direct.experiment_id == "exp5_real_ai_model_endpoint_comparison"
            else {}
        )
        for root_id, direct in direct_by_root.items()
    }
    canonical = formal_runner._canonical_metric_inputs(
        projection.rows,
        producer_facts_by_root=producer_facts,
    )

    conditions_by_experiment: dict[str, list[dict[str, object]]] = {}
    selections_by_experiment: dict[str, list[dict[str, object]]] = {}
    for direct in projection.rows:
        conditions_by_experiment.setdefault(direct.experiment_id, []).append(
            formal_fixtures._condition(direct.experiment_id, direct.condition_id)
        )
        selections_by_experiment.setdefault(direct.experiment_id, []).append(
            {"ordered_case_ids": [direct.case_id]}
        )
    experiment_ids = tuple(conditions_by_experiment)
    evidence_bodies = {
        "suite": {
            "schema_version": "tokenshare.paper_suite.v1",
            "suite_id": "runner-lineage-replay-matrix",
            "experiment_ids": list(experiment_ids),
        },
        "dispatch": {
            "schema_version": "tokenshare.paper_dispatch.v1",
            "plans": [
                {
                    "experiment_id": experiment_id,
                    "conditions": conditions_by_experiment[experiment_id],
                    "selections": selections_by_experiment[experiment_id],
                }
                for experiment_id in experiment_ids
            ],
        },
        "budget": {"budget_digest": "sha256:" + "1" * 64},
        "catalog": {"catalog_digest": "sha256:" + "2" * 64},
        "identity": {"endpoint_digest": "sha256:" + "3" * 64},
        "request_limits": {"max_tokens": 1024},
        "hard_limits": {"max_provider_attempts": 5},
    }
    current_evidence_root = tmp_path / "current-formal-evidence"
    evidence_store = FormalEvidenceStore.initialize(
        output_root=current_evidence_root,
        **evidence_bodies,
        capturing=False,
    )
    current_provider_files = {}
    for direct in projection.rows:
        kwargs, ledger, artifact_store, runtime = source_by_root[
            direct.preregistered_root_run_id
        ]
        binding = direct.execution_binding
        assert binding is not None
        source_refs = {
            ref.artifact_id: ref
            for ref in (
                *runtime.artifact_refs,
                kwargs["final_result_ref"],
                *kwargs.get("parser_refs", ()),
                *kwargs.get("verifier_checker_refs", ()),
                *kwargs.get("current_provider_object_refs", ()),
                *(
                    ()
                    if kwargs.get("actual_resource_book_ref") is None
                    else (kwargs["actual_resource_book_ref"],)
                ),
                *(
                    ()
                    if kwargs.get("trace_resource_book_ref") is None
                    else (kwargs["trace_resource_book_ref"],)
                ),
            )
        }
        source_refs.update(
            {
                ref.artifact_id: ref
                for ref in (
                    ArtifactRef.from_dict(
                        json.loads(manifest_path.read_text(encoding="utf-8"))
                    )
                    for manifest_path in artifact_store.artifact_dir.glob(
                        "*.manifest.json"
                    )
                )
            }
        )
        run_artifact_root = (
            current_evidence_root
            / "experiments"
            / direct.experiment_id
            / "runs"
            / direct.condition_id
            / str(direct.repeat_id)
            / "artifacts"
        )
        run_artifact_root.mkdir(parents=True, exist_ok=True)
        artifact_records = []
        for index, source_ref in enumerate(
            sorted(source_refs.values(), key=lambda value: value.artifact_id)
        ):
            target = run_artifact_root / f"{index:03d}-{source_ref.artifact_id}.bin"
            target.write_bytes(artifact_store.read_bytes(source_ref))
            artifact_records.append(
                {
                    "artifact_id": source_ref.artifact_id,
                    "experiment_id": direct.experiment_id,
                    "condition_id": direct.condition_id,
                    "repeat_id": direct.repeat_id,
                    "task_id": binding.task_id,
                    "path": target.relative_to(current_evidence_root).as_posix(),
                    "content_hash": source_ref.content_hash,
                    "size_bytes": source_ref.size_bytes,
                    "source_artifact_ref": source_ref.to_dict(),
                }
            )
        runtime_identity = {
            "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
            "run_id": binding.execution_id,
            "task_id": binding.task_id,
            "root_unit_id": binding.root_unit_id,
            "ledger_digest": binding.ledger_digest,
        }
        execution_identity = formal_fixtures._execution_version_identity()
        execution_identity["runtime_generation_identity_digest"] = (
            formal_fixtures._sha256(
                formal_fixtures._canonical_json(runtime_identity).encode("utf-8")
            )
        )
        task = {
            **formal_fixtures._task(
                direct.experiment_id,
                direct.condition_id,
                task_id=binding.task_id,
            ),
            "case_id": direct.case_id,
            "preregistered_root_run_id": direct.preregistered_root_run_id,
            "runtime_generation_identity": runtime_identity,
            "execution_version_identity": execution_identity,
        }
        attempt = formal_fixtures._attempt(
            direct.experiment_id,
            direct.condition_id,
            task_id=binding.task_id,
        )
        attempt["attempt_id"] = f"attempt:{direct.preregistered_root_run_id}"
        evidence_store.checkpoint_root(
            experiment_id=direct.experiment_id,
            condition=formal_fixtures._condition(
                direct.experiment_id,
                direct.condition_id,
            ),
            repeat_id=direct.repeat_id,
            task=task,
            attempts=(attempt,),
            faults=(),
            events=tuple(event.to_dict() for event in ledger.read_all()),
            artifact_refs=tuple(artifact_records),
        )
        for snapshot in direct.current_provider_object_refs:
            source_ref = source_refs[snapshot.artifact_id]
            current_provider_files[snapshot.artifact_id] = (
                artifact_store.root_path / source_ref.uri
            )

    source_resolver_root = tmp_path / "approved-source-bank"
    (source_resolver_root / "objects").mkdir(parents=True)
    trace_locators = tuple(
        locator
        for direct in projection.rows
        for locator in direct.source_bank_object_locators
    )
    for locator in trace_locators:
        payload = json.dumps(
            {"label": f"source:{locator.object_role}"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert "sha256:" + hashlib.sha256(payload).hexdigest() == (
            locator.object_digest
        )
        (
            source_resolver_root
            / "objects"
            / locator.object_digest.removeprefix("sha256:")
        ).write_bytes(payload)
    source_resolvers = (
        {
            "approved-bank": ResponseBankResolver(
                source_resolver_root,
                SimpleNamespace(
                    manifest=SimpleNamespace(
                        bank_root_id="approved-bank",
                        manifest_digest=trace_locators[0].manifest_digest,
                        object_role_schema=tuple(
                            sorted({locator.object_role for locator in trace_locators})
                        ),
                    )
                ),
            )
        }
        if trace_locators
        else {}
    )
    protected = formal_runner.persist_paper_traceability_replay_input_root(
        replay_input_root=tmp_path / "protected-replay-inputs",
        canonical_direct_rows=canonical,
        canonical_runtime_evidence=tuple(evidence_by_root.values()),
        requested_lineage_root_ids=tuple(sorted(direct_by_root)),
        source_resolvers=source_resolvers,
        current_provider_object_files=current_provider_files,
        current_evidence_root=current_evidence_root,
    )
    return SimpleNamespace(
        protected=protected,
        direct_by_root=direct_by_root,
        canonical=canonical,
        expected_root_ids=tuple(sorted(direct_by_root)),
    )


def test_exp4_exp5_direct_lineage_survives_protected_replay_with_exp1(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_formal_evidence import (
        _typed_direct_results_in_inputs,
        build_canonical_lineage_inputs,
    )
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
        recompute_paper_formal_metrics,
    )
    from tokenshare.experiments.paper_traceability import _load_protected_replay_inputs

    closure = _persist_genuine_direct_lineage_closure(tmp_path)
    loaded = _load_protected_replay_inputs(closure.protected)
    expected_root_ids = set(closure.direct_by_root)
    reachable = tuple(_typed_direct_results_in_inputs(loaded.direct))
    assert {direct.preregistered_root_run_id for direct in reachable} == (
        expected_root_ids
    )
    assert all(
        sum(
            direct.preregistered_root_run_id == root_id
            for direct in reachable
        )
        == 1
        for root_id in expected_root_ids
    )
    lineage = build_canonical_lineage_inputs(
        loaded.direct,
        canonical_runtime_evidence=loaded.current["canonical_runtime_evidence"],
    )
    assert {value.direct_result.preregistered_root_run_id for value in lineage} == (
        expected_root_ids
    )

    replay_output_root = tmp_path / "recomputed-metrics"
    for relative_name, content in loaded.current_evidence_files:
        target = replay_output_root / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    metrics = recompute_paper_formal_metrics(
        replay_output_root,
        loaded.direct,
        canonical_runtime_evidence=loaded.current["canonical_runtime_evidence"],
        metric_projection_rows=derive_paper_metric_projection_rows(loaded.direct),
    )
    assert metrics.output_refs

    exp4_rows = loaded.direct["exp4_ablation"]
    duplicate = replace(
        exp4_rows[0],
        direct_results=(
            *exp4_rows[0].direct_results,
            exp4_rows[0].direct_results[0],
        ),
    )
    duplicated_direct = {**loaded.direct, "exp4_ablation": (duplicate, *exp4_rows[1:])}
    with pytest.raises(
        ValueError,
        match="executed direct result is not uniquely reachable",
    ):
        build_canonical_lineage_inputs(
            duplicated_direct,
            canonical_runtime_evidence=loaded.current[
                "canonical_runtime_evidence"
            ],
        )


def test_combined_metrics_recomputes_two_real_protected_115_root_closures(
    tmp_path: Path,
) -> None:
    """99+16 的本地 evidence 必须经真实 loader、rehydrate 与 metrics 重算。"""

    trace_specifications = tuple(
        (
            f"exp1-{index:03d}",
            "exp1_real_ai_feasibility",
            "factorization",
            "real_model_trace_protocol_run",
        )
        for index in range(12)
    ) + tuple(
        (
            f"exp2-{index:03d}",
            "exp2_real_ai_scalability",
            "factorization",
            "real_model_trace_protocol_run",
        )
        for index in range(6)
    ) + tuple(
        (
            f"exp3-{index:03d}",
            "exp3_real_ai_fault_recovery",
            "factorization",
            "real_model_trace_protocol_run",
        )
        for index in range(81)
    )
    exp5_specifications = tuple(
        (
            f"exp5-{index:03d}",
            "exp5_real_ai_model_endpoint_comparison",
            "factorization",
            "online_real_provider",
        )
        for index in range(16)
    )
    trace = _persist_genuine_direct_lineage_closure(
        tmp_path / "trace-source",
        specifications=trace_specifications,
    )
    exp5 = _persist_genuine_direct_lineage_closure(
        tmp_path / "exp5-source",
        specifications=exp5_specifications,
    )

    def write_suite_handle(*, name: str, protected: object) -> Path:
        suite_root = tmp_path / name
        suite_root.mkdir()
        handle_path = suite_root / "traceability_replay_input_root.handle.pickle"
        handle_bytes = pickle.dumps(protected, protocol=5)
        handle_path.write_bytes(handle_bytes)
        descriptor_path = protected.descriptor_path
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "traceability_replay_input_root_ref": {
                        "schema_version": (
                            "tokenshare.paper_traceability_replay_input_ref.v1"
                        ),
                        "handle_path": handle_path.name,
                        "handle_digest": "sha256:"
                        + hashlib.sha256(handle_bytes).hexdigest(),
                        "descriptor_path": descriptor_path.as_posix(),
                        "descriptor_digest": protected.descriptor_digest,
                    }
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return suite_root

    trace_suite_root = write_suite_handle(
        name="exp1-exp3-trace",
        protected=trace.protected,
    )
    exp5_suite_root = write_suite_handle(
        name="exp5-online",
        protected=exp5.protected,
    )
    expected_root_ids = (*trace.expected_root_ids, *exp5.expected_root_ids)
    assert len(expected_root_ids) == 115
    assert len(set(expected_root_ids)) == 115
    assert formal_runner.load_paper_traceability_replay_input_root(trace_suite_root)
    assert formal_runner.load_paper_traceability_replay_input_root(exp5_suite_root)

    publication_root = tmp_path / "combined-metrics"
    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_input_roots(
        publication_root=publication_root,
        trace_suite_root=trace_suite_root,
        exp5_suite_root=exp5_suite_root,
        expected_root_ids=expected_root_ids,
    )

    assert metrics.provider_calls == 0
    assert metrics.output_refs
    assert all("content_digest" in ref for ref in metrics.output_refs)
    observation_manifest = json.loads(
        (
            publication_root
            / "metrics"
            / "paper_metric_observations_manifest.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert observation_manifest["provider_calls"] == 0
    assert observation_manifest["recompute_only"] is True


def test_results_first_metric_closure_resume_rebuilds_without_root_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import run_paper_pipeline as pipeline

    (
        original,
        _tasks,
        _attempts,
        _dispatched,
        _source_entry,
        source_root,
        plan,
        runner_catalog,
        trace_context,
    ) = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path / "original",
        monkeypatch=monkeypatch,
        mutation_kind=None,
        return_context=True,
        enforce_publication_closure=False,
        enable_metric_closure=True,
        selected_execution=True,
    )
    source_metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        source_root
    )
    scratch_root = tmp_path / "scratch-suite"
    source_checkpoints = source_root.with_name(
        source_root.name + ".canonical_direct_evidence"
    )
    scratch_checkpoints = scratch_root.with_name(
        scratch_root.name + ".canonical_direct_evidence"
    )
    shutil.copytree(source_root, scratch_root)
    shutil.copytree(source_checkpoints, scratch_checkpoints)
    pipeline._closure_replay_remove_derived(
        scratch_root=scratch_root,
        scratch_traceability_root=scratch_root.with_name(
            scratch_root.name + ".traceability_replay_inputs"
        ),
    )
    current_before = {
        path.relative_to(scratch_root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in scratch_root.rglob("CURRENT.json")
    }
    checkpoint_before = {
        path.relative_to(scratch_checkpoints).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in scratch_checkpoints.glob(
            "*/canonical_direct_checkpoint.json"
        )
    }
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_kwargs: pytest.fail(
            "completed closure resume redispatched a protocol root"
        ),
    )

    condition, selection = plan.bound_items()[0]
    kwargs = _formal_execution_kwargs(
        tmp_path=scratch_root,
        config=_ai_config(),
        plan=plan,
    )
    kwargs.update(
        {
            "catalog_manifest": runner_catalog,
            "budget": _with_ai_unit_commitments(
                _budget(
                    planned_experiments=(plan.experiment_id,),
                    planned_conditions=1,
                    planned_root_runs=len(selection.ordered_case_ids),
                    planned_ai_units=len(selection.ordered_case_ids),
                    max_provider_attempts=3 * len(selection.ordered_case_ids),
                ),
                (condition, selection),
            ),
            "transport": SimpleNamespace(
                post_chat_completion=lambda **_kwargs: pytest.fail(
                    "completed closure resume called provider transport"
                )
            ),
            "trace_context": trace_context,
            "selected_condition_ids": (condition.condition_id,),
            "root_case_filter": {
                condition.condition_id: selection.ordered_case_ids
            },
            "execution_output_root_by_experiment": {
                plan.experiment_id: scratch_root / plan.experiment_id
            },
            "enforce_publication_closure": False,
            "enable_metric_closure": True,
            "resume": True,
        }
    )
    resumed = formal_runner.execute_paper_formal_suite(**kwargs)

    assert resumed.status is PaperStatus.COMPLETED
    assert resumed.condition_count == original.condition_count == 1
    assert resumed.task_count == original.task_count == 1
    assert resumed.provider_attempt_count == original.provider_attempt_count == 0
    resumed_metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        scratch_root
    )
    assert resumed_metrics.output_refs
    assert resumed_metrics.observations_digest == source_metrics.observations_digest
    assert (scratch_root / "formal_runner_result.json").is_file()
    assert (
        scratch_root / "traceability_replay_input_root.handle.pickle"
    ).is_file()
    assert {
        path.relative_to(scratch_root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in scratch_root.rglob("CURRENT.json")
    } == current_before
    assert {
        path.relative_to(scratch_checkpoints).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in scratch_checkpoints.glob(
            "*/canonical_direct_checkpoint.json"
        )
    } == checkpoint_before


def test_normal_exp3_trace_persists_exact_source_binding_separate_from_current_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        suite,
        tasks,
        attempts,
        dispatched,
        source_entry,
        suite_root,
    ) = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=None,
    )

    assert suite.status is PaperStatus.COMPLETED
    assert dispatched == ["factor_pressure_000"]
    assert len(tasks) == len(attempts) == 1
    reference = tasks[0]["paired_trace_reference"]
    assert reference["source_entry_ids"] == [source_entry.entry_id]
    assert reference["source_acquisition_state_refs"] == [
        source_entry.acquisition_state_ref
    ]
    assert attempts[0]["attempt_id"] not in {
        source_entry.entry_id,
        source_entry.acquisition_state_ref,
    }
    assert attempts[0]["provider_attempt_count"] == 0
    assert attempts[0]["raw_output_ref"]["source"] == {
        "kind": "external_response_bank_locator"
    }
    wrapper_hash = attempts[0]["raw_output_ref"]["content_hash"].removeprefix(
        "sha256:"
    )
    wrapper_path = next(
        path
        for path in suite_root.rglob(f"{wrapper_hash}-*")
        if path.is_file()
    )
    wrapper = json.loads(wrapper_path.read_text(encoding="utf-8"))
    assert wrapper["attempt_id"] == attempts[0]["attempt_id"]
    assert wrapper["entry_id"] == source_entry.entry_id
    assert wrapper["binding_digest"] == reference["source_binding_digests"][0]
    assert {item["entry_id"] for item in wrapper["source_bank_object_locators"]} == {
        source_entry.entry_id
    }
    assert wrapper["logical_start_ms"] == 0
    assert wrapper["logical_finish_ms"] == 1


@pytest.mark.parametrize(
    ("experiment_id", "evidence_class", "expected"),
    (
        ("exp1_real_ai_feasibility", "online_real_provider", "exp1_feasibility"),
        (
            "exp1_real_ai_feasibility",
            "real_model_trace_protocol_run",
            "exp1_feasibility",
        ),
        ("exp2_real_ai_scalability", "real_model_trace_protocol_run", "exp2_trace_scalability"),
        ("exp2_real_ai_scalability", "online_real_provider", "exp2_online_concurrency"),
        ("exp3_real_ai_fault_recovery", "real_model_trace_protocol_run", "exp3_trace_robustness"),
        ("exp3_real_ai_fault_recovery", "online_real_provider", "exp3_online_recovery"),
        ("exp4_real_ai_protocol_ablation", "real_model_trace_protocol_run", "exp4_ablation"),
        ("exp5_real_ai_model_endpoint_comparison", "online_real_provider", "experiment_5"),
    ),
)
def test_direct_metric_route_uses_frozen_experiment_and_evidence_class(
    experiment_id: str,
    evidence_class: str,
    expected: str,
) -> None:
    assert formal_runner._direct_metric_input_key(
        experiment_id=experiment_id,
        evidence_class=evidence_class,
    ) == expected


def test_direct_metric_route_inventory_is_exactly_the_official_matrix() -> None:
    assert dict(formal_runner._DIRECT_METRIC_INPUT_ROUTES) == {
        (
            "exp1_real_ai_feasibility",
            "online_real_provider",
        ): "exp1_feasibility",
        (
            "exp1_real_ai_feasibility",
            "real_model_trace_protocol_run",
        ): "exp1_feasibility",
        (
            "exp2_real_ai_scalability",
            "real_model_trace_protocol_run",
        ): "exp2_trace_scalability",
        (
            "exp2_real_ai_scalability",
            "online_real_provider",
        ): "exp2_online_concurrency",
        (
            "exp3_real_ai_fault_recovery",
            "real_model_trace_protocol_run",
        ): "exp3_trace_robustness",
        (
            "exp3_real_ai_fault_recovery",
            "online_real_provider",
        ): "exp3_online_recovery",
        (
            "exp4_real_ai_protocol_ablation",
            "real_model_trace_protocol_run",
        ): "exp4_ablation",
        (
            "exp5_real_ai_model_endpoint_comparison",
            "online_real_provider",
        ): "experiment_5",
    }


def test_merge_canonical_metric_inputs_preserves_fixed_denominator_and_fails_closed(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_exp5_metrics import (
        Exp5PreregisteredRootFacts,
    )

    trace_direct = _exp1_direct_metric_fixture(
        tmp_path / "trace",
        evidence_class="real_model_trace_protocol_run",
    )
    exp5_direct = replace(
        _exp1_direct_metric_fixture(
            tmp_path / "exp5",
            evidence_class="online_real_provider",
        ),
        preregistered_root_run_id="paper-direct-root:" + "5" * 71,
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id="exp5-condition",
        condition_axes={
            "domain": "factorization",
            "difficulty": "hard",
            "topic_family": None,
            "worker_count": 3,
            "sample_slot_index": 0,
            "fault_condition": None,
            "death_condition": None,
            "ablation_mode": "FULL",
            "model_endpoint_id": "glm_5_2_exp5_v3",
        },
        case_id="factor-exp5-case",
        _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
    )
    exp5_group = formal_runner._RunnerExp5ModelRepeatFacts(
        model_arm_id="glm_5_2_exp5_v3",
        repeat_id=0,
        frozen_identity=None,
        observed_identity=None,
        persisted_model_endpoint_identity_digest=None,
        protocol_first_dispatch_at_ms=0,
        preregistered_roots=(
            Exp5PreregisteredRootFacts(
                preregistered_root_run_id=(
                    exp5_direct.preregistered_root_run_id
                ),
                root_dispatched_at_ms=0,
                root_terminal_at_ms=1,
                final_result_reference_complete=True,
                end_to_end_verified_success=True,
            ),
        ),
        planned_ai_units=(),
        first_provider_attempts=(),
        max_retries=0,
        replacement_attempts_allowed=False,
        infrastructure_valid=True,
        direct_results=(exp5_direct,),
    )
    empty = {key: () for key in formal_runner._DIRECT_METRIC_INPUT_KEYS}
    trace_inputs = {**empty, "exp1_feasibility": (trace_direct,)}
    exp5_inputs = {**empty, "experiment_5": (exp5_group,)}
    expected_root_ids = (
        trace_direct.preregistered_root_run_id,
        exp5_direct.preregistered_root_run_id,
    )

    merged = formal_runner.merge_canonical_metric_input_roots(
        (trace_inputs, exp5_inputs),
        expected_root_ids=expected_root_ids,
    )

    assert merged["exp1_feasibility"] == (trace_direct,)
    assert merged["experiment_5"] == (exp5_group,)
    with pytest.raises(ValueError, match="duplicate canonical metric root"):
        formal_runner.merge_canonical_metric_input_roots(
            (trace_inputs, exp5_inputs, exp5_inputs),
            expected_root_ids=expected_root_ids,
        )
    with pytest.raises(ValueError, match="fixed denominator mismatch"):
        formal_runner.merge_canonical_metric_input_roots(
            (trace_inputs, exp5_inputs),
            expected_root_ids=(*expected_root_ids, "missing-root"),
        )


def test_persisted_verification_event_verdict_is_identity_bound_and_fail_closed() -> None:
    from tests.experiments import test_paper_direct_results as direct_fixtures
    from tokenshare.experiments import paper_direct_results
    from tokenshare.experiments.paper_models import ArtifactIdentitySnapshot

    row, _condition_manifest, _catalog = direct_fixtures._inventory_row(
        root_id="paper-direct-root:persisted-event",
        condition_id="exp5-condition",
        case_id="factor-case",
        evidence_class="online_real_provider",
    )
    row = direct_fixtures._replace_inventory_row(
        row,
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_axes={**row.condition_axes, "domain": "factorization"},
    )
    final_ref = ArtifactIdentitySnapshot(
        artifact_id="final-result",
        artifact_type="canonical_output",
        uri="artifacts/final-result",
        content_hash="sha256:" + "1" * 64,
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="factorization.prime_factorization_result",
        artifact_schema_version="v1",
        source_role="final_result",
        source_task_id="task-1",
        source_execution_id="execution-1",
        created_at="2026-08-14T00:00:00Z",
    )
    candidate_ref = {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": final_ref.artifact_id,
        "artifact_type": final_ref.artifact_type,
        "uri": final_ref.uri,
        "content_hash": final_ref.content_hash,
        "size_bytes": final_ref.size_bytes,
        "media_type": final_ref.media_type,
        "artifact_schema_id": final_ref.artifact_schema_id,
        "artifact_schema_version": final_ref.artifact_schema_version,
        "source": {"kind": "factorization_runtime"},
        "metadata": {},
        "created_at": final_ref.created_at,
    }
    report = {
        "schema_version": "phase4.verification_report.v1",
        "task_id": "task-1",
        "unit_id": "merge-unit-1",
        "plugin_id": "factorization",
        "validator_policy_id": "factorization.merge_result.validator.v1",
        "status": "passed",
        "eligible_for_canonical": True,
        "candidate_output_refs": {"prime_factorization_result": candidate_ref},
    }
    payload = {
        "schema_version": "phase4.verification_record.v1",
        "task_id": "task-1",
        "unit_id": "merge-unit-1",
        "plugin_id": "factorization",
        "validator_policy_id": "factorization.merge_result.validator.v1",
        "status": "passed",
        "eligible_for_canonical": True,
        "verification_report": report,
        "verification_report_digest": formal_runner.digest_json(report),
    }
    event = {
        "schema_version": "LedgerEvent.v2",
        "event_seq": 7,
        "event_id": "event_000000000007",
        "event_type": "VERIFICATION_RECORDED",
        "event_hash": "sha256:" + "2" * 64,
        "prev_event_hash": "sha256:" + "3" * 64,
        "task_id": "task-1",
        "object_type": "verification",
        "object_id": "verification-1",
        "actor": "protocol_engine",
        "occurred_at": "2026-08-14T00:00:00Z",
        "correlation_id": "correlation-1",
        "causation_event_id": None,
        "idempotency_key": "verification-1",
        "batch_id": None,
        "batch_index": None,
        "batch_size": None,
        "payload": payload,
    }
    body = paper_direct_results.build_persisted_verification_event_verdict_body(
        inventory_row=row,
        execution_id="execution-1",
        task_id="task-1",
        root_unit_id="root-unit-1",
        final_result_ref=final_ref,
        verification_event=event,
    )

    assert paper_direct_results._read_persisted_verification_event_verdict(
        body,
        inventory_row=row,
        execution_id="execution-1",
        task_id="task-1",
        root_unit_id="root-unit-1",
        final_result_ref=final_ref,
        events=(event,),
    ) is True
    mutated = json.loads(json.dumps(body))
    mutated["verification_event"]["event_id"] = "event_000000000008"
    mutated["projection_digest"] = formal_runner.digest_json(
        {key: value for key, value in mutated.items() if key != "projection_digest"}
    )
    with pytest.raises(ValueError, match="verification event identity mismatch"):
        paper_direct_results._read_persisted_verification_event_verdict(
            mutated,
            inventory_row=row,
            execution_id="execution-1",
            task_id="task-1",
            root_unit_id="root-unit-1",
            final_result_ref=final_ref,
            events=(event,),
        )


@pytest.mark.parametrize(
    ("experiment_id", "evidence_class"),
    (
        ("exp1_real_ai_feasibility", "regression_only"),
        ("exp4_real_ai_protocol_ablation", "online_real_provider"),
        (
            "exp5_real_ai_model_endpoint_comparison",
            "real_model_trace_protocol_run",
        ),
        ("unknown_experiment", "online_real_provider"),
    ),
)
def test_direct_metric_route_rejects_every_unregistered_pair(
    experiment_id: str,
    evidence_class: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="no official metric input route",
    ):
        formal_runner._direct_metric_input_key(
            experiment_id=experiment_id,
            evidence_class=evidence_class,
        )


def test_direct_metric_projection_fails_closed_without_persisted_producer_facts() -> None:
    executed = SimpleNamespace(
        experiment_id="exp2_real_ai_scalability",
        evidence_class="real_model_trace_protocol_run",
        preregistered_root_run_id="inventory:missing-producer",
        root_status="completed",
    )

    with pytest.raises(
        ValueError,
        match="persisted metric producer facts are missing",
    ):
        formal_runner._canonical_metric_inputs(
            (executed,),
            producer_facts_by_root={},
        )


def _trace_metric_provider_truth_fixture(
    experiment_id: str,
) -> tuple[SimpleNamespace, dict[str, object]]:
    row = SimpleNamespace(
        experiment_id=experiment_id,
        evidence_class="real_model_trace_protocol_run",
        preregistered_root_run_id=f"inventory:{experiment_id}",
        root_status="completed",
        repeat_id=0,
        condition_id=f"condition:{experiment_id}",
        case_id="case:factorization",
        condition_axes={
            "sample_slot_index": 0,
            "worker_count": 10,
            "fault_condition": "false_positive",
            "ablation_mode": "FULL",
            "domain": "factorization",
        },
        current_provider_object_refs=(),
        source_bank_object_locators=(),
        preregistered_case_ref={"case_record_digest": "sha256:" + "1" * 64},
        final_result_reference_complete=True,
        end_to_end_verified_success=True,
        identity_consistent=True,
        paper_evidence_complete=True,
        infrastructure_valid=True,
    )
    facts: dict[str, object] = {
        "task": {
            "provider_attempt_count": 0,
            "trace_source_usage": {
                "schema_version": "tokenshare.paper_trace_source_usage.v1",
                "attribution_kind": "immutable_response_bank",
                "current_provider_call_count": 0,
                "current_provider_spend_cny": "0",
                "committed_consumption_count": 0,
                "consumptions": [],
            },
        },
        "attempts": [
            {
                "attempt_id": "trace-attempt-0",
                "provider_attempt_count": 0,
            }
        ],
        "run_evidence": {
            "protocol_runtime": {
                "runtime_observation": {
                    "planned_ai_unit_ids": [],
                    "dispatched_ai_unit_ids": [],
                    "completed_ai_unit_ids": [],
                }
            }
        },
    }
    return row, facts


@pytest.mark.parametrize(
    "experiment_id",
    (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    ),
)
@pytest.mark.parametrize(
    "tamper",
    (
        "task_provider_attempt_count",
        "attempt_provider_attempt_count",
        "current_provider_ref",
        "trace_header_provider_call_count",
    ),
)
def test_every_trace_metric_route_centrally_rejects_current_provider_facts(
    experiment_id: str,
    tamper: str,
) -> None:
    row, facts = _trace_metric_provider_truth_fixture(experiment_id)
    task = facts["task"]
    attempts = facts["attempts"]
    assert isinstance(task, dict)
    assert isinstance(attempts, list)
    if tamper == "task_provider_attempt_count":
        task["provider_attempt_count"] = 1
    elif tamper == "attempt_provider_attempt_count":
        attempts[0]["provider_attempt_count"] = 1
    elif tamper == "current_provider_ref":
        row.current_provider_object_refs = (
            SimpleNamespace(source_role="request_body"),
        )
    else:
        summary = task["trace_source_usage"]
        assert isinstance(summary, dict)
        summary["current_provider_call_count"] = 1

    with pytest.raises(
        ValueError,
        match="trace metric current provider facts are inconsistent",
    ):
        formal_runner._canonical_metric_inputs(
            (row,),
            producer_facts_by_root={row.preregistered_root_run_id: facts},
        )


def _exp1_direct_metric_fixture(
    tmp_path: Path,
    *,
    evidence_class: str = "online_real_provider",
):
    from tests.experiments.test_paper_direct_results import (
        _canonical_fixture,
        _inventory_manifest,
        _inventory_row,
        _project,
        _replace_inventory_row,
    )
    from tokenshare.experiments.paper_direct_results import (
        build_canonical_direct_evidence,
    )

    base, condition_manifest, catalog = _inventory_row(
        evidence_class=evidence_class,
    )
    row = _replace_inventory_row(base, experiment_id="exp1_real_ai_feasibility")
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    evidence = build_canonical_direct_evidence(**kwargs)
    return _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    ).rows[0]


def test_exp1_trace_metric_route_keeps_source_acquisition_separate_from_current_calls(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_exp1_metrics import build_exp1_observations
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
    )
    from tokenshare.experiments.paper_formal_evidence import (
        LineageSourceIndex,
        LineageSourceRecord,
    )
    from tokenshare.experiments.paper_metric_contract import (
        capture_metric_computation_traces,
        load_paper_metric_contract,
    )
    from tokenshare.experiments.paper_metric_observations import (
        _required_source_identities,
        materialize_metric_observations,
    )
    from tokenshare.experiments.paper_metric_registry import (
        load_paper_metric_registry,
    )
    from tokenshare.experiments.paper_models import digest_json

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    binding = row.execution_binding
    source_roles = formal_runner._source_roles(row)
    assert source_roles is not None
    source_entry_ids = {
        locator.entry_id for locator in row.source_bank_object_locators
    }
    assert len(source_entry_ids) == 1
    source_entry_id = next(iter(source_entry_ids))
    facts = {
        "task": {
            "provider_attempt_count": 0,
            "trace_source_usage": {
                "schema_version": "tokenshare.paper_trace_source_usage.v1",
                "attribution_kind": "immutable_response_bank",
                "current_provider_call_count": 0,
                "current_provider_spend_cny": "0",
                "committed_consumption_count": 1,
                "consumptions": [
                    {
                        "consumption_id": "source-consumption-1",
                        "entry_id": source_entry_id,
                        "total_tokens": 7,
                        "latency_ms": 11,
                        "cost_estimate_cny": "0.25",
                        "source_bank_roles": list(source_roles),
                    }
                ],
            },
        },
        # 这些是当前 trace-backed protocol attempts，不是 provider attempts。
        # 即使包含 provider/model 字段，也不得被 Exp1 当成本次 provider 调用。
        "attempts": [
            {
                "attempt_id": "current-trace-attempt-1",
                "provider_attempt_count": 0,
                "provider": "deepseek",
                "latency_ms": 999,
                "total_tokens": 999,
            }
        ],
        "run_evidence": {"protocol_runtime": {"runtime_observation": None}},
        "ledger_root_clock": {
            "schema_version": "tokenshare.paper_ledger_root_clock.v1",
            "run_id": binding.execution_id,
            "task_id": binding.task_id,
            "root_unit_id": binding.root_unit_id,
            "root_started_at": "1970-01-01T00:00:01Z",
            "root_terminal_at": "1970-01-01T00:00:02Z",
        },
    }

    routed = formal_runner._canonical_metric_inputs(
        (row,),
        producer_facts_by_root={row.preregistered_root_run_id: facts},
    )
    hydrated = routed["exp1_feasibility"][0]
    projected = derive_paper_metric_projection_rows(routed)
    metric_view = projected[
        "exp1_feasibility"
    ][0]
    contract = load_paper_metric_contract()
    observations = build_exp1_observations(
        (metric_view,),
        contract,
    )
    with capture_metric_computation_traces() as computation_traces:
        drafts = load_paper_metric_registry(contract).project_all(projected)
    source_input_digest = digest_json({"fixture": "exp1-trace-lineage"})
    direct_result_ref = row.to_dict()
    source_index = LineageSourceIndex.create(
        input_identity_digest=source_input_digest,
        records=(
            LineageSourceRecord.create(
                member_id=row.preregistered_root_run_id,
                evidence_class=row.evidence_class,
                direct_result_refs=(direct_result_ref,),
                source_bank_object_locators=row.source_bank_object_locators,
            ),
            LineageSourceRecord.create(
                member_id=source_entry_id,
                evidence_class=row.evidence_class,
                direct_result_refs=(direct_result_ref,),
                source_bank_object_locators=row.source_bank_object_locators,
            ),
        ),
    )
    publication = materialize_metric_observations(
        contract=contract,
        table_drafts=tuple(
            draft for draft in drafts if draft.table_id == "exp1_feasibility"
        ),
        computation_traces=tuple(computation_traces),
        source_index=source_index,
        expected_source_input_identity_digest=source_input_digest,
    )

    assert row.current_provider_object_refs == ()
    assert source_roles == formal_runner._SOURCE_BANK_METRIC_ROLE_ORDER
    assert {locator.object_role for locator in row.source_bank_object_locators} == set(
        source_roles
    )
    assert hydrated.actual_provider_attempts == ()
    assert hydrated.trace_consumptions is not None
    assert hydrated.trace_consumptions[0].source_bank_entry_id == source_entry_id
    trace_bundles = tuple(trace.bundle for trace in computation_traces)
    assert trace_bundles
    assert all(
        _required_source_identities(bundle) == (source_entry_id,)
        for bundle in trace_bundles
    )
    materialized = {
        observation.metric_id: observation for observation in publication.observations
    }
    for metric_id in (
        "preregistered_root_count",
        "completion_rate",
        "end_to_end_verified_success_rate",
    ):
        assert materialized[metric_id].numeric_value == 1
        assert materialized[metric_id].publish_blocked is False
        assert materialized[metric_id].covered_source_bank_roles == source_roles
    assert all(
        any(
            facts.get("member_kind") == "committed_trace_consumption"
            and facts.get("source_bank_entry_id") == source_entry_id
            for facts in bundle.member_facts_by_id.values()
        )
        for bundle in trace_bundles
    )
    assert metric_view.direct_result.experiment_id == "experiment_1"
    assert (
        metric_view.direct_result.evidence_class
        == "real_model_trace_protocol_run"
    )
    assert observations[0].require_cell("preregistered_root_count").value == 1
    assert observations[0].require_cell("completion_rate").value == 1
    assert (
        observations[0].require_cell("end_to_end_verified_success_rate").value
        == 1
    )
    for metric_id in (
        "actual_provider_latency_ms",
        "actual_total_tokens",
        "actual_cost_estimate_cny",
    ):
        cell = observations[0].require_cell(metric_id)
        assert cell.value == 0
        assert cell.reason is None
        assert cell.publish_blocked is False
    assert tuple(draft.table_id for draft in drafts) == tuple(
        table.table_id for table in contract.tables
    )


def test_persisted_metric_rehydration_uses_verified_producer_facts_for_exact_zero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    binding = row.execution_binding
    source_roles = formal_runner._source_roles(row)
    assert source_roles is not None
    source_entry_id = row.source_bank_object_locators[0].entry_id
    task = {
        "preregistered_root_run_id": row.preregistered_root_run_id,
        "experiment_id": row.experiment_id,
        "condition_id": row.condition_id,
        "repeat_id": row.repeat_id,
        "case_id": row.case_id,
        "task_id": binding.task_id,
        "protocol_task_id": binding.task_id,
        "provider_attempt_count": 0,
        "runtime_generation_identity": {
            "run_id": binding.execution_id,
            "task_id": binding.task_id,
            "root_unit_id": binding.root_unit_id,
        },
        "trace_source_usage": {
            "schema_version": "tokenshare.paper_trace_source_usage.v1",
            "attribution_kind": "immutable_response_bank",
            "current_provider_call_count": 0,
            "current_provider_spend_cny": "0",
            "committed_consumption_count": 1,
            "consumptions": [
                {
                    "consumption_id": "source-consumption-1",
                    "entry_id": source_entry_id,
                    "total_tokens": 7,
                    "latency_ms": 11,
                    "cost_estimate_cny": "0.25",
                    "source_bank_roles": list(source_roles),
                }
            ],
        },
    }
    attempt = {
        "attempt_id": "trace-attempt-1",
        "condition_id": row.condition_id,
        "repeat_id": row.repeat_id,
        "task_id": binding.task_id,
        "protocol_task_id": binding.task_id,
        "run_id": binding.execution_id,
        "provider_attempt_count": 0,
    }
    events = tuple(
        {
            "event_seq": event.event_seq,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "event_hash": event.event_hash,
            "prev_event_hash": event.prev_event_hash,
            "schema_version": "LedgerEvent.v1",
            "task_id": event.task_id,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "occurred_at": f"1970-01-01T00:00:{event.event_seq:02d}Z",
            "payload": {
                "task_unit": {
                    "unit_id": event.object_id,
                    "state": (
                        "completed"
                        if event.object_id == binding.root_unit_id
                        and event.event_seq
                        == max(
                            item.event_seq
                            for item in binding.events
                            if item.object_id == binding.root_unit_id
                        )
                        else "running"
                    ),
                }
            },
        }
        for event in binding.events
    )

    class Store:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path / "persisted"

        def load_logical_run_records(self, **identity):
            assert identity == {
                "experiment_id": row.experiment_id,
                "condition_id": row.condition_id,
                "repeat_id": row.repeat_id,
            }
            return {
                "tasks": (task,),
                "attempts": (attempt,),
                "faults": (),
                "events": events,
                "source_generation_roots": (),
            }

    monkeypatch.setattr(formal_runner, "FormalEvidenceStore", Store)
    monkeypatch.setattr(
        formal_runner,
        "_root_clock_from_verified_ledger",
        lambda **_kwargs: {
            "schema_version": "tokenshare.paper_ledger_root_clock.v1",
            "run_id": binding.execution_id,
            "task_id": binding.task_id,
            "root_unit_id": binding.root_unit_id,
            "root_started_at": "1970-01-01T00:00:01Z",
            "root_terminal_at": "1970-01-01T00:00:02Z",
        },
    )
    inputs = {key: () for key in formal_runner._DIRECT_METRIC_INPUT_KEYS}
    inputs["exp1_feasibility"] = (row,)

    hydrated = formal_runner.rehydrate_persisted_metric_inputs(
        evidence_root=tmp_path / "persisted",
        canonical_metric_inputs=inputs,
    )["exp1_feasibility"][0]

    assert hydrated.actual_provider_attempts == ()
    assert hydrated.root_start_at_ms == Decimal(1000)
    assert hydrated.root_terminal_at_ms == Decimal(2000)


def test_cloned_metric_root_cannot_be_rehydrated_from_another_root_evidence(
    tmp_path: Path,
) -> None:
    """RED：Task5 的 metadata clone 不能冒充与其不匹配的 formal record。"""

    from tokenshare.experiments.paper_models import ArtifactIdentitySnapshot
    from tokenshare.experiments.paper_traceability import (
        _CURRENT_PROVIDER_ROLES,
        _load_protected_replay_inputs,
        _walk_instances,
    )
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs

    descriptor, _rows = _genuine_l4_inputs(tmp_path / "source")
    source = _load_protected_replay_inputs(descriptor)
    base_direct = source.direct["exp1_feasibility"][0].direct_result
    cloned = replace(
        base_direct,
        preregistered_root_run_id="cloned-exp1-root",
        _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
    )
    inputs = {key: () for key in formal_runner._DIRECT_METRIC_INPUT_KEYS}
    inputs["exp1_feasibility"] = (cloned,)
    source_provider_object_files = {
        ref["artifact_id"]: descriptor.root_path / ref["path"]
        for ref in json.loads(
            descriptor.descriptor_path.read_text(encoding="utf-8")
        )["current_provider_object_refs"]
    }
    provider_object_files = {
        value.artifact_id: source_provider_object_files[value.artifact_id]
        for value in _walk_instances(inputs, ArtifactIdentitySnapshot)
        if value.source_role in _CURRENT_PROVIDER_ROLES
    }
    protected = formal_runner.persist_paper_traceability_replay_input_root(
        replay_input_root=tmp_path / "cloned-replay-inputs",
        canonical_direct_rows=inputs,
        global_infrastructure_valid=True,
        canonical_runtime_evidence=(
            SimpleNamespace(preregistered_root_run_id=cloned.preregistered_root_run_id),
        ),
        requested_lineage_root_ids=(cloned.preregistered_root_run_id,),
        current_trace_wrappers_by_root={cloned.preregistered_root_run_id: ()},
        trace_source_bindings_by_root={cloned.preregistered_root_run_id: ()},
        eligibility_facts_by_root={cloned.preregistered_root_run_id: {}},
        source_resolvers=source.source["source_resolvers"],
        current_provider_object_files=provider_object_files,
        current_evidence_root=descriptor.root_path / "current_evidence",
    )
    loaded = _load_protected_replay_inputs(protected)
    evidence_root = tmp_path / "rehydrated-evidence"
    for relative_name, content in loaded.current_evidence_files:
        target = evidence_root / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    # 期望的 Task6 路径：metadata clone 若没有一对一 checkpoint，真实复水必须失败。
    formal_runner.rehydrate_persisted_metric_inputs(
        evidence_root=evidence_root,
        canonical_metric_inputs=loaded.direct,
        source_resolvers=loaded.source["source_resolvers"],
    )


def test_rehydrate_exp5_identity_reads_verified_model_record_and_fails_closed(
    tmp_path: Path,
) -> None:
    """Exp5 identity 只能由已持久化的 matched model record 重建。"""

    from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity

    identity = PaperModelEndpointIdentity(
        model_cohort_id="cohort-v3",
        model_cohort_digest="sha256:" + "1" * 64,
        cohort_member_id="glm-siliconflow",
        provider_config_id="siliconflow",
        selected_entry_id="glm_5_2_exp5_v3",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="thinking",
        effective_reasoning_controls={
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        source_provider_config_digest="sha256:" + "2" * 64,
    )
    condition = PaperExperimentCondition(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id="exp5-condition",
        domain="factorization",
        difficulty="hard",
        paper_difficulty="hard",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_cohort_id=identity.model_cohort_id,
        model_cohort_digest=identity.model_cohort_digest,
        cohort_member_id=identity.cohort_member_id,
        provider_config_id=identity.provider_config_id,
        model_entry_id=identity.selected_entry_id,
        provider_family=identity.provider_family,
        provider_model_id=identity.provider_model_id,
        reasoning_profile_id=identity.reasoning_profile_id,
        source_provider_config_digest=identity.source_provider_config_digest,
        model_endpoint_identity_digest=identity.model_endpoint_identity_digest,
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "3" * 64,
    )
    source_store = ArtifactStore(tmp_path / "source-artifacts")

    def logical_for(
        record_body: dict[str, object] | None,
        *,
        artifact_id: str = "matched-model-record",
        artifact_type: str = PaperModelExecutionRecord.__name__,
        artifact_schema_id: str = "tokenshare.paper_model_execution_record",
        artifact_schema_version: str = "v2",
    ) -> dict[str, object]:
        evidence_root = tmp_path / (
            "missing-model-record" if record_body is None else "model-record"
        )
        records: list[dict[str, object]] = []
        attempt: dict[str, object] = {"provider_attempt_count": 1}
        if record_body is not None:
            record_ref = source_store.save_json(
                record_body,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                artifact_schema_id=artifact_schema_id,
                artifact_schema_version=artifact_schema_version,
                source={"kind": "test", "role": "model_record"},
                metadata={},
                created_at="2026-08-01T00:00:00Z",
            )
            target = evidence_root / "artifacts" / f"{record_ref.artifact_id}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_store.read_bytes(record_ref))
            records.append(
                {
                    "source_artifact_ref": record_ref.to_dict(),
                    "path": target.relative_to(evidence_root).as_posix(),
                }
            )
            attempt["model_execution_record_ref"] = record_ref.to_dict()
        return {"artifacts": tuple(records), "attempts": (attempt,)}

    matched = {
        "identity_status": "matched",
        "mismatch_reasons": [],
        "expected_identity": identity.to_dict(),
    }
    assert formal_runner._rehydrate_persisted_exp5_model_endpoint_identity(
        evidence_root=tmp_path / "model-record",
        logical=logical_for(matched),
        condition=condition,
    ) == identity

    with pytest.raises(ValueError, match="model execution identity is not matched"):
        formal_runner._rehydrate_persisted_exp5_model_endpoint_identity(
            evidence_root=tmp_path / "model-record",
            logical=logical_for(
                {
                    "identity_status": "mismatched",
                    "mismatch_reasons": ["wrong_endpoint"],
                    "expected_identity": identity.to_dict(),
                },
                artifact_id="wrong-model-record",
            ),
            condition=condition,
        )
    for artifact_kwargs in (
        {
            "artifact_id": "wrong-model-record-artifact-type",
            "artifact_type": "WrongModelExecutionRecord",
        },
        {
            "artifact_id": "wrong-model-record-artifact-schema",
            "artifact_schema_version": "v1",
        },
    ):
        with pytest.raises(ValueError, match="model record artifact contract"):
            formal_runner._rehydrate_persisted_exp5_model_endpoint_identity(
                evidence_root=tmp_path / "model-record",
                logical=logical_for(matched, **artifact_kwargs),
                condition=condition,
            )
    with pytest.raises(ValueError, match="model record ref is missing"):
        formal_runner._rehydrate_persisted_exp5_model_endpoint_identity(
            evidence_root=tmp_path / "missing-model-record",
            logical=logical_for(None),
            condition=condition,
        )


def test_persisted_metric_observations_bind_one_root_without_exposing_raw_records(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_exp3_metrics import Exp3PersistedObservation

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    observations = formal_runner._persisted_metric_observations(
        row,
        {
            "attempts": ({"attempt_id": "attempt-1"},),
            "faults": ({"fault_id": "fault-1"},),
            "events": ({"event_id": "event-1"},),
            "task": {"task_id": "task-1"},
        },
        observation_type=Exp3PersistedObservation,
    )

    root_members = tuple(
        observation
        for observation in observations
        if observation.facts.get("member_kind") == "preregistered_root"
    )
    assert len(root_members) == 1
    assert root_members[0].observation_id == row.preregistered_root_run_id
    assert root_members[0].facts == {
        "member_kind": "preregistered_root",
        "preregistered_root_run_id": row.preregistered_root_run_id,
        "final_result_reference_complete": row.final_result_reference_complete,
        "end_to_end_verified_success": row.end_to_end_verified_success,
    }
    assert observations == root_members


def test_exp3_persisted_observations_project_typed_fault_and_death_evidence(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_exp3_metrics import Exp3PersistedObservation

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    fault_id = "fault-fp-1"
    fault_attempt_id = "attempt-fp-1"
    fault_replacement_id = "attempt-fp-replacement-1"
    fault_unit_id = "unit-fp-1"
    candidate_hash = "sha256:" + "a" * 64
    false_positive = formal_runner._persisted_metric_observations(
        row,
        {
            "task": {
                "paired_trace_reference": {
                    "source_entry_ids": ("entry-fp-0", "entry-fp-1")
                },
                "trace_source_usage": {
                    "consumptions": (
                        {
                            "consumption_id": "consumption-fp-0",
                            "current_attempt_id": fault_attempt_id,
                            "entry_id": "entry-fp-0",
                            "replacement_slot": 0,
                            "total_tokens": 3,
                            "cost_estimate_cny": "0.03",
                        },
                        {
                            "consumption_id": "consumption-fp-1",
                            "current_attempt_id": fault_replacement_id,
                            "entry_id": "entry-fp-1",
                            "replacement_slot": 1,
                            "total_tokens": 5,
                            "cost_estimate_cny": "0.05",
                        },
                    )
                },
            },
            "faults": (
                {
                    "fault_id": fault_id,
                    "fault_type": "false_positive",
                    "attempt_id": fault_attempt_id,
                    "applicability_status": "injected",
                    "mutation_summary": {
                        "mutation_kind": "false_positive_invalid_claim",
                        "expected_detection": "verifier_or_checker_reject",
                    },
                    "mutated_output_ref": {"content_hash": candidate_hash},
                },
            ),
            "events": (
                {
                    "event_id": "event-created-fp-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": fault_attempt_id,
                            "attempt_ordinal": 0,
                            "unit_id": fault_unit_id,
                            "client_id": "worker-fp-1",
                        },
                    },
                },
                {
                    "event_id": "event-verification-fp-1",
                    "event_type": "VERIFICATION_RECORDED",
                    "payload": {
                        "attempt_id": fault_attempt_id,
                        "status": "rejected",
                        "verification_report": {
                            "candidate_output_refs": {
                                "answer": {"content_hash": candidate_hash}
                            }
                        },
                    },
                },
                {
                    "event_id": "event-recovery-fp-1",
                    "event_type": "RECOVERY_ACTION_RECORDED",
                    "payload": {
                        "recovery_action": {
                            "attempt_id": fault_attempt_id,
                            "unit_id": fault_unit_id,
                            "retry_allowed": True,
                            "retry_count": 1,
                        }
                    },
                },
                {
                    "event_id": "event-created-fp-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": fault_replacement_id,
                            "attempt_ordinal": 1,
                            "unit_id": fault_unit_id,
                            "client_id": "worker-fp-2",
                        },
                    },
                },
                {
                    "event_id": "event-request-fp-replacement-1",
                    "event_type": "EXECUTION_REQUEST_RECORDED",
                    "payload": {"attempt_id": fault_replacement_id},
                },
                {
                    "event_id": "event-trace-fp-replacement-1",
                    "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                    "payload": {"attempt_id": fault_replacement_id},
                },
                {
                    "event_id": "event-verification-fp-replacement-1",
                    "event_type": "VERIFICATION_RECORDED",
                    "payload": {
                        "attempt_id": fault_replacement_id,
                        "status": "passed",
                    },
                },
                {
                    "event_id": "event-canonical-fp-replacement-1",
                    "event_type": "CANONICAL_OUTPUTS_BOUND",
                    "payload": {"selected_attempt_id": fault_replacement_id},
                },
                {
                    "event_id": "event-completed-fp-1",
                    "event_type": "TASK_UNIT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Completed",
                        "task_unit": {"unit_id": fault_unit_id},
                    },
                },
            ),
        },
        observation_type=Exp3PersistedObservation,
    )
    candidate = next(
        observation
        for observation in false_positive
        if observation.facts.get("member_kind")
        == "exp3_controlled_wrong_candidate"
    )
    assert candidate.facts["candidate_fault_id"] == fault_id
    assert candidate.facts["candidate_attempt_id"] == fault_attempt_id
    assert candidate.facts["candidate_id"] == candidate_hash
    assert candidate.facts["verifier_rejected_candidate_id"] == candidate_hash
    assert candidate.facts["canonical_candidate_id"] != candidate_hash

    unit_id = "unit-death-1"
    dead_attempt_id = "attempt-dead-1"
    replacement_attempt_id = "attempt-replacement-1"
    worker_death = formal_runner._persisted_metric_observations(
        row,
        {
            "task": {
                "paired_trace_reference": {
                    "source_entry_ids": ("entry-death-replacement-1",)
                },
                "trace_source_usage": {
                    "consumptions": (
                        {
                            "consumption_id": "consumption-death-replacement-1",
                            "current_attempt_id": replacement_attempt_id,
                            "entry_id": "entry-death-replacement-1",
                            "replacement_slot": 0,
                            "total_tokens": 7,
                            "cost_estimate_cny": "0.07",
                        },
                    )
                },
            },
            "faults": (
                {
                    "fault_type": "worker_death",
                    "condition_id": row.condition_id,
                    "kill_progress_target_ratio": 0.25,
                    "kill_progress_actual_ratio": 0.5,
                    "kill_progress_completed_ai_unit_count": 1,
                    "kill_progress_total_ai_unit_count": 2,
                    "kill_progress_observed_at": "2026-08-14T00:00:00Z",
                    "kill_progress_error": None,
                    "record_ref": {
                        "artifact_id": "worker-death-record-1",
                        "content_hash": "sha256:" + "b" * 64,
                    },
                    "target_ai_unit": {"unit_id": unit_id},
                    "dead_attempt": {
                        "attempt_id": dead_attempt_id,
                        "unit_id": unit_id,
                    },
                    "replacement_attempt": {
                        "attempt_id": replacement_attempt_id,
                        "unit_id": unit_id,
                        "harness_status": "replacement_completed",
                        "process_exitcode": 0,
                    },
                    "replacement_process_exitcode": 0,
                    "reassignment": {
                        "original_attempt_id": dead_attempt_id,
                        "replacement_attempt_id": replacement_attempt_id,
                        "target_unit_id": unit_id,
                    },
                },
            ),
            "events": (
                {
                    "event_id": "event-created-dead-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": dead_attempt_id,
                            "attempt_ordinal": 0,
                            "unit_id": unit_id,
                            "client_id": "worker-dead-1",
                        },
                    },
                },
                {
                    "event_id": "event-created-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": replacement_attempt_id,
                            "attempt_ordinal": 1,
                            "unit_id": unit_id,
                            "client_id": "worker-replacement-1",
                        },
                    },
                },
                {
                    "event_id": "event-request-replacement-1",
                    "event_type": "EXECUTION_REQUEST_RECORDED",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "event-trace-replacement-1",
                    "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "event-verification-replacement-1",
                    "event_type": "VERIFICATION_RECORDED",
                    "payload": {
                        "attempt_id": replacement_attempt_id,
                        "status": "passed",
                    },
                },
                {
                    "event_id": "event-canonical-replacement-1",
                    "event_type": "CANONICAL_OUTPUTS_BOUND",
                    "payload": {"selected_attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "event-completed-replacement-1",
                    "event_type": "TASK_UNIT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Completed",
                        "task_unit": {"unit_id": unit_id},
                    },
                },
            ),
        },
        observation_type=Exp3PersistedObservation,
    )
    progress = next(
        observation
        for observation in worker_death
        if observation.facts.get("member_kind") == "exp3_worker_death_progress"
    )
    assert progress.facts["progress_evidence_complete"] is True
    assert progress.facts["target_kill_progress_ratio"] == Decimal("0.25")
    assert progress.facts["actual_kill_progress_ratio"] == Decimal("0.5")
    slot = next(
        observation
        for observation in worker_death
        if observation.facts.get("member_kind") == "exp3_required_slot"
    )
    assert slot.facts["required_slot_unit_id"] == unit_id
    assert slot.facts["recovered_valid_canonical"] is True


def test_exp3_persisted_observations_preserve_rejected_replacement_as_unsuccessful(
    tmp_path: Path,
) -> None:
    """真实 verifier 拒绝是 Exp3 结果，不能被误报为 evidence infrastructure block。"""

    from tokenshare.experiments.paper_exp3_metrics import Exp3PersistedObservation

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    original_attempt_id = "attempt-original-1"
    replacement_attempt_id = "attempt-replacement-1"
    unit_id = "unit-1"

    observations = formal_runner._persisted_metric_observations(
        row,
        {
            "task": {
                "paired_trace_reference": {
                    "source_entry_ids": ("entry-original-1", "entry-replacement-1")
                },
                "trace_source_usage": {
                    "consumptions": (
                        {
                            "consumption_id": "consumption-original-1",
                            "current_attempt_id": original_attempt_id,
                            "entry_id": "entry-original-1",
                            "replacement_slot": 0,
                            "total_tokens": 3,
                            "cost_estimate_cny": "0.03",
                        },
                        {
                            "consumption_id": "consumption-replacement-1",
                            "current_attempt_id": replacement_attempt_id,
                            "entry_id": "entry-replacement-1",
                            "replacement_slot": 1,
                            "total_tokens": 5,
                            "cost_estimate_cny": "0.05",
                        },
                    )
                },
            },
            "faults": (
                {
                    "fault_id": "fault-executor-error-1",
                    "fault_type": "executor_error",
                    "attempt_id": original_attempt_id,
                    "applicability_status": "injected",
                },
            ),
            "events": (
                {
                    "event_id": "created-original-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": original_attempt_id,
                            "attempt_ordinal": 0,
                            "unit_id": unit_id,
                            "client_id": "worker-original-1",
                        },
                    },
                },
                {
                    "event_id": "recovery-original-1",
                    "event_type": "RECOVERY_ACTION_RECORDED",
                    "payload": {
                        "recovery_action": {
                            "attempt_id": original_attempt_id,
                            "unit_id": unit_id,
                            "retry_allowed": True,
                            "retry_count": 1,
                        }
                    },
                },
                {
                    "event_id": "created-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": replacement_attempt_id,
                            "attempt_ordinal": 1,
                            "unit_id": unit_id,
                            "client_id": "worker-replacement-1",
                        },
                    },
                },
                {
                    "event_id": "request-replacement-1",
                    "event_type": "EXECUTION_REQUEST_RECORDED",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "trace-replacement-1",
                    "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "verification-replacement-1",
                    "event_type": "VERIFICATION_RECORDED",
                    "payload": {
                        "attempt_id": replacement_attempt_id,
                        "status": "rejected",
                    },
                },
                {
                    "event_id": "rejected-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Rejected",
                        "attempt": {
                            "attempt_id": replacement_attempt_id,
                            "state": "Rejected",
                            "failure_kind": "invalid_output",
                            "failure_reason": "plugin domain check rejected",
                        },
                    },
                },
            ),
        },
        observation_type=Exp3PersistedObservation,
    )

    started = next(
        observation
        for observation in observations
        if observation.observation_id.endswith(
            f"replacement-started:{replacement_attempt_id}"
        )
    )
    failed = next(
        observation
        for observation in observations
        if observation.observation_id.endswith(
            f"replacement-failed:{replacement_attempt_id}"
        )
    )
    assert started.facts["replacement_attempt_id"] == replacement_attempt_id
    assert failed.facts["replacement_terminal_state"] == "Rejected"
    assert failed.facts["replacement_failure_kind"] == "invalid_output"
    assert not any(
        observation.observation_id.endswith(
            f"replacement-successful:{replacement_attempt_id}"
        )
        for observation in observations
    )


def test_exp3_persisted_observations_keep_unverified_false_positive_observable(
    tmp_path: Path,
) -> None:
    """已注入但被 supersede、未达 verifier 的 false-positive 仍是实验结果。"""

    from tokenshare.experiments.paper_exp3_metrics import Exp3PersistedObservation

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    original_attempt_id = "attempt-fp-original-1"
    replacement_attempt_id = "attempt-fp-replacement-1"
    unit_id = "unit-fp-1"
    candidate_hash = "sha256:" + "c" * 64

    observations = formal_runner._persisted_metric_observations(
        row,
        {
            "task": {
                "paired_trace_reference": {
                    "source_entry_ids": ("entry-fp-original-1", "entry-fp-replacement-1")
                },
                "trace_source_usage": {
                    "consumptions": (
                        {
                            "consumption_id": "consumption-fp-original-1",
                            "current_attempt_id": original_attempt_id,
                            "entry_id": "entry-fp-original-1",
                            "replacement_slot": 0,
                            "total_tokens": 3,
                            "cost_estimate_cny": "0.03",
                        },
                        {
                            "consumption_id": "consumption-fp-replacement-1",
                            "current_attempt_id": replacement_attempt_id,
                            "entry_id": "entry-fp-replacement-1",
                            "replacement_slot": 1,
                            "total_tokens": 5,
                            "cost_estimate_cny": "0.05",
                        },
                    )
                },
            },
            "faults": (
                {
                    "fault_id": "fault-fp-unverified-1",
                    "fault_type": "false_positive",
                    "attempt_id": original_attempt_id,
                    "applicability_status": "injected",
                    "mutation_summary": {
                        "mutation_kind": "false_positive_invalid_claim",
                        "expected_detection": "verifier_or_checker_reject",
                    },
                    "mutated_output_ref": {"content_hash": candidate_hash},
                },
            ),
            "events": (
                {
                    "event_id": "created-fp-original-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": original_attempt_id,
                            "attempt_ordinal": 0,
                            "unit_id": unit_id,
                            "client_id": "worker-fp-original-1",
                        },
                    },
                },
                {
                    "event_id": "request-fp-original-1",
                    "event_type": "EXECUTION_REQUEST_RECORDED",
                    "payload": {"attempt_id": original_attempt_id},
                },
                {
                    "event_id": "trace-fp-original-1",
                    "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                    "payload": {"attempt_id": original_attempt_id},
                },
                {
                    "event_id": "superseded-fp-original-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Superseded",
                        "attempt": {
                            "attempt_id": original_attempt_id,
                            "state": "Superseded",
                        },
                    },
                },
                {
                    "event_id": "recovery-fp-original-1",
                    "event_type": "RECOVERY_ACTION_RECORDED",
                    "payload": {
                        "recovery_action": {
                            "attempt_id": original_attempt_id,
                            "unit_id": unit_id,
                            "retry_allowed": True,
                            "retry_count": 1,
                        }
                    },
                },
                {
                    "event_id": "created-fp-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Created",
                        "attempt": {
                            "attempt_id": replacement_attempt_id,
                            "attempt_ordinal": 1,
                            "unit_id": unit_id,
                            "client_id": "worker-fp-replacement-1",
                        },
                    },
                },
                {
                    "event_id": "request-fp-replacement-1",
                    "event_type": "EXECUTION_REQUEST_RECORDED",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "trace-fp-replacement-1",
                    "event_type": "TRACE_DELIVERY_COMMITTED.v1",
                    "payload": {"attempt_id": replacement_attempt_id},
                },
                {
                    "event_id": "superseded-fp-replacement-1",
                    "event_type": "ATTEMPT_STATE_CHANGED",
                    "payload": {
                        "new_state": "Superseded",
                        "attempt": {
                            "attempt_id": replacement_attempt_id,
                            "state": "Superseded",
                        },
                    },
                },
            ),
        },
        observation_type=Exp3PersistedObservation,
    )

    candidate = next(
        observation
        for observation in observations
        if observation.facts.get("member_kind") == "exp3_controlled_wrong_candidate"
    )
    assert candidate.facts["candidate_id"] == candidate_hash
    assert candidate.facts["injection_completed"] is True
    assert candidate.facts["reached_verification"] is False
    assert candidate.facts["verifier_rejected_candidate_id"] == "not-rejected:fault-fp-unverified-1"


def test_exp4_persisted_observations_use_typed_hooks_not_mode_flags(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_exp4_metrics import Exp4PersistedObservation

    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    attempt_id = "attempt-ai-1"
    facts = {
        "task": {
            "trace_source_usage": {
                "consumptions": (
                    {
                        "consumption_id": "consumption-1",
                        "current_attempt_id": attempt_id,
                        "planned_ai_unit_id": "range-0",
                        "entry_id": "entry-1",
                        "replacement_slot": 0,
                    },
                )
            },
            "ablation_runtime": {
                "attempt_observations": (
                    {
                        "attempt_id": attempt_id,
                        "raw_output_ref": {"content_hash": "sha256:" + "a" * 64},
                        "candidate_output_ref": {"content_hash": "sha256:" + "a" * 64},
                        "canonical_output_refs": {},
                    },
                ),
                "hook_observations": (
                    build_experiment_ablation_gate_applied_observation(
                        ablation_mode="NO_PARSER_POLICY",
                        disabled_mechanism="parser_policy",
                        protocol_event_refs=(),
                        artifact_refs=(),
                        hook_input={
                            "task_id": "task-1",
                            "unit_id": "unit-1",
                            "attempt_id": attempt_id,
                            "lease_id": "lease-1",
                        },
                        hook_result={"bypass": True, "stop": False},
                    ).to_dict(),
                ),
            },
            "ablation_runtime_flags": {"raw_only_exposed": False},
        },
        "attempts": (),
        "faults": (),
        "events": (),
    }

    observations = formal_runner._persisted_metric_observations(
        row,
        facts,
        observation_type=Exp4PersistedObservation,
    )

    typed = tuple(
        observation
        for observation in observations
        if observation.facts.get("member_kind") == "raw_only_exposure_event"
    )
    assert len(typed) == 1
    assert typed[0].facts["parser_policy_disabled"] is True
    assert typed[0].facts["raw_candidate_exposed"] is True
    assert typed[0].facts["raw_candidate_accepted"] is False

    without_hook = formal_runner._persisted_metric_observations(
        row,
        {
            **facts,
            "task": {
                **facts["task"],
                "ablation_runtime": {
                    **facts["task"]["ablation_runtime"],
                    "hook_observations": (),
                },
                "ablation_runtime_flags": {"raw_only_exposed": True},
            },
        },
        observation_type=Exp4PersistedObservation,
    )
    assert not any(
        observation.facts.get("member_kind") == "raw_only_exposure_event"
        for observation in without_hook
    )


def test_exp4_hydration_uses_verified_ledger_root_duration(tmp_path: Path) -> None:
    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )
    binding = row.execution_binding
    assert binding is not None
    hydrated = formal_runner._hydrate_exp4_root(
        row,
        {
            "task": {
                "trace_source_usage": {
                    "schema_version": "tokenshare.paper_trace_source_usage.v1",
                    "attribution_kind": "immutable_response_bank",
                    "current_provider_call_count": 0,
                    "current_provider_spend_cny": "0",
                    "committed_consumption_count": 1,
                    "consumptions": (
                        {
                            "consumption_id": "consumption-1",
                            "entry_id": "entry-1",
                            "replacement_slot": 0,
                            "total_tokens": 7,
                            "cost_estimate_cny": "0.25",
                        },
                    )
                }
            },
            "attempts": (),
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {"runtime_wall_clock_ms": 0}
                }
            },
            "ledger_root_clock": {
                "schema_version": "tokenshare.paper_ledger_root_clock.v1",
                "run_id": binding.execution_id,
                "task_id": binding.task_id,
                "root_unit_id": binding.root_unit_id,
                "root_started_at": "1970-01-01T00:00:01Z",
                "root_terminal_at": "1970-01-01T00:00:02.250Z",
            },
        },
    )

    assert hydrated.trace_replay_wall_clock_ms == Decimal("1250")


def test_exp2_trace_hydration_uses_verified_ledger_duration_not_zero_runtime(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_exp2_metrics import _direct_row

    row = _direct_row(
        tmp_path,
        root_id="exp2-ledger-duration",
        worker=1,
        evidence_class="real_model_trace_protocol_run",
    )
    binding = row.execution_binding
    assert binding is not None
    source_entry_id = row.source_bank_object_locators[0].entry_id
    source_roles = formal_runner._source_roles(row)
    assert source_roles is not None
    hydrated = formal_runner._hydrate_exp2_trace_metric_row(
        row,
        {
            "task": {
                "trace_source_usage": {
                    "schema_version": "tokenshare.paper_trace_source_usage.v1",
                    "attribution_kind": "immutable_response_bank",
                    "current_provider_call_count": 0,
                    "current_provider_spend_cny": "0",
                    "committed_consumption_count": 1,
                    "consumptions": (
                        {
                            "consumption_id": "exp2-consumption",
                            "entry_id": source_entry_id,
                            "planned_ai_unit_id": "range_0",
                            "unit_id": "range_0",
                            "replacement_slot": 0,
                            "latency_ms": 10,
                            "total_tokens": 7,
                            "cost_estimate_cny": "0.25",
                            "source_bank_roles": source_roles,
                        },
                    ),
                }
            },
            "attempts": (),
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {
                        "runtime_wall_clock_ms": 0,
                        "planned_ai_unit_ids": ("range_0",),
                        "dispatched_ai_unit_ids": ("range_0",),
                        "completed_ai_unit_ids": ("range_0",),
                        "in_flight_ai_unit_ids_at_witness": (),
                        "observed_peak_concurrency": 1,
                    }
                }
            },
            "ledger_root_clock": {
                "schema_version": "tokenshare.paper_ledger_root_clock.v1",
                "run_id": binding.execution_id,
                "task_id": binding.task_id,
                "root_unit_id": binding.root_unit_id,
                "root_started_at": "1970-01-01T00:00:01Z",
                "root_terminal_at": "1970-01-01T00:00:02.250Z",
            },
        },
    )

    assert hydrated.persisted_logical_makespan_ms == Decimal("1250")


def test_exp5_metric_hydration_requires_persisted_matched_endpoint_identity(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity

    direct = replace(
        _exp1_direct_metric_fixture(tmp_path, evidence_class="online_real_provider"),
        preregistered_root_run_id="paper-direct-root:" + "5" * 71,
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id="exp5-condition",
        condition_axes={
            "domain": "factorization",
            "difficulty": "hard",
            "topic_family": None,
            "worker_count": 3,
            "sample_slot_index": 0,
            "fault_condition": None,
            "death_condition": None,
            "ablation_mode": "FULL",
            "model_endpoint_id": "glm_5_2_exp5_v3",
        },
        case_id="factor-exp5-case",
        repeat_id=0,
        _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
    )
    identity = PaperModelEndpointIdentity(
        model_cohort_id="cohort-v3",
        model_cohort_digest="sha256:" + "1" * 64,
        cohort_member_id="glm-siliconflow",
        provider_config_id="siliconflow",
        selected_entry_id="glm_5_2_exp5_v3",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="thinking",
        effective_reasoning_controls={
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        source_provider_config_digest="sha256:" + "2" * 64,
    )
    facts = {
        direct.preregistered_root_run_id: {
            "task": {"provider_attempt_count": 1},
            "attempts": (
                {
                    "attempt_id": "attempt-1",
                    "unit_id": "unit-1",
                    "provider": "siliconflow",
                    "provider_attempt_count": 1,
                    "attempt_status": "completed",
                    "error_kind": None,
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
                    "cost_estimate": 0.01,
                    "cost_estimate_currency": "CNY",
                    "cost_estimate_status": "estimated",
                    "usage_ref": {"artifact_id": "usage"},
                },
            ),
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {"planned_ai_unit_ids": ["unit-1"]}
                }
            },
            "model_endpoint_identity": identity,
        }
    }

    hydrated = formal_runner._hydrate_exp5_metric_rows((direct,), facts)[0]

    assert hydrated.frozen_identity == identity
    assert hydrated.observed_identity == identity
    assert (
        hydrated.persisted_model_endpoint_identity_digest
        == identity.model_endpoint_identity_digest
    )


def _exp5_hydration_projection(
    tmp_path: Path,
    *,
    repeat0_attempts: tuple[dict[str, object], ...],
    repeat0_planned_ai_unit_ids: tuple[str, ...] = ("planned-unit",),
    repeat0_additional_roots: tuple[
        tuple[tuple[str, ...], tuple[dict[str, object], ...]], ...
    ] = (),
):
    """构造三次完整 repeat，让测试只观察 hydration 的事实投影。"""

    from tokenshare.experiments.paper_exp5_metrics import build_exp5_observations
    from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
    from tokenshare.experiments.paper_model_identity import PaperModelEndpointIdentity

    identity = PaperModelEndpointIdentity(
        model_cohort_id="cohort-v3",
        model_cohort_digest="sha256:" + "1" * 64,
        cohort_member_id="glm-siliconflow",
        provider_config_id="siliconflow",
        selected_entry_id="glm_5_2_exp5_v3",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="thinking",
        effective_reasoning_controls={
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
        source_provider_config_digest="sha256:" + "2" * 64,
    )
    rows = []
    facts_by_root = {}
    for repeat_id in range(3):
        root_specs = (
            (
                repeat0_planned_ai_unit_ids,
                repeat0_attempts,
            ),
            *repeat0_additional_roots,
        ) if repeat_id == 0 else ((("planned-unit",), ()),)
        for root_index, (planned_ai_unit_ids, attempts) in enumerate(root_specs):
            direct = replace(
                _exp1_direct_metric_fixture(
                    tmp_path, evidence_class="online_real_provider"
                ),
                preregistered_root_run_id=(
                    "paper-direct-root:exp5-hydration-"
                    + str(repeat_id)
                    + "-"
                    + str(root_index)
                ),
                experiment_id=EXP5_EXPERIMENT_ID,
                condition_id="exp5-hydration-condition",
                condition_axes={
                    "domain": "factorization",
                    "difficulty": "hard",
                    "topic_family": None,
                    "worker_count": 3,
                    "sample_slot_index": repeat_id,
                    "fault_condition": None,
                    "death_condition": None,
                    "ablation_mode": "FULL",
                    "model_endpoint_id": "glm_5_2_exp5_v3",
                },
                case_id="factor-exp5-hydration-case-" + str(root_index),
                repeat_id=repeat_id,
                _factory_token=formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
            )
            facts_by_root[direct.preregistered_root_run_id] = {
                "task": {
                    "provider_attempt_count": sum(
                        1 for attempt in attempts if attempt.get("provider")
                    )
                },
                "attempts": attempts,
                "run_evidence": {
                    "protocol_runtime": {
                        "runtime_observation": {
                            "planned_ai_unit_ids": list(planned_ai_unit_ids)
                        }
                    }
                },
                "model_endpoint_identity": identity,
            }
            rows.append(direct)
    return build_exp5_observations(
        formal_runner._hydrate_exp5_metric_rows(tuple(rows), facts_by_root),
        load_paper_metric_contract(),
    )


def _exp5_hydration_attempt(
    attempt_id: str,
    *,
    unit_id: str,
    planned_ai_unit_id: str | None = None,
    provider: str | None = "siliconflow",
    provider_attempt_index: int = 0,
    provider_attempt_count: int = 1,
) -> dict[str, object]:
    attempt: dict[str, object] = {
        "attempt_id": attempt_id,
        "unit_id": unit_id,
        "provider": provider,
        "provider_attempt_index": provider_attempt_index,
        "provider_attempt_count": provider_attempt_count,
        "attempt_status": "completed",
        "error_kind": None,
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
        "cost_estimate": 0.01,
        "cost_estimate_currency": "CNY",
        "cost_estimate_status": "estimated",
        "usage_ref": {"artifact_id": "usage"},
    }
    if planned_ai_unit_id is not None:
        attempt["planned_ai_unit_id"] = planned_ai_unit_id
    return attempt


def test_exp5_metric_hydration_rejects_usage_with_missing_provider_identity(
    tmp_path: Path,
) -> None:
    """有完整 CNY 用量的 provider attempt 不能缺失 provider identity。"""

    with pytest.raises(
        ValueError,
        match="Exp5 persisted provider attempt requires provider identity",
    ):
        _exp5_hydration_projection(
            tmp_path,
            repeat0_attempts=(
                _exp5_hydration_attempt(
                    "attempt-missing-provider",
                    unit_id="runtime-child-missing-provider",
                    planned_ai_unit_id="planned-unit",
                    provider=None,
                    provider_attempt_count=1,
                ),
            ),
        )


def test_exp5_metric_hydration_counts_single_identified_provider_attempt(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-identified-provider",
                unit_id="runtime-child-identified-provider",
                planned_ai_unit_id="planned-unit",
            ),
        ),
    )
    resource_payload = next(
        payload
        for payload in projection.table_payloads
        if payload.table_id == "exp5_resources"
    )
    row = resource_payload.rows[0]

    assert row.require_cell("actual_first_provider_attempt_count").value == 1
    assert row.require_cell("actual_total_tokens").value == Decimal("3")
    assert row.require_cell("actual_cost_estimate_cny").value == Decimal("0.01")


def test_exp5_metric_hydration_does_not_count_zero_provider_attempt(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-zero-provider-count",
                unit_id="runtime-child-zero-provider-count",
                planned_ai_unit_id="planned-unit",
                provider_attempt_count=0,
            ),
        ),
    )
    resource_payload = next(
        payload
        for payload in projection.table_payloads
        if payload.table_id == "exp5_resources"
    )
    row = resource_payload.rows[0]

    assert row.require_cell("actual_first_provider_attempt_count").value == 0
    assert row.require_cell("actual_total_tokens").value == Decimal("0")
    assert row.require_cell("actual_cost_estimate_cny").value == Decimal("0")
    assert "exp5_retry_forbidden" not in row.ineligibility_reasons


def test_exp5_metric_hydration_uses_explicit_planned_identity_not_runtime_child(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-stable-runtime-child",
                unit_id="runtime-child-9f49d8",
                planned_ai_unit_id="planned-unit",
            ),
        ),
    )

    assert all(
        "provider_attempt_without_planned_ai_unit" not in row.ineligibility_reasons
        for payload in projection.table_payloads
        for row in payload.rows
    )


def test_exp5_metric_hydration_blocks_unknown_planned_identity_within_its_root(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_planned_ai_unit_ids=("range_0", "root-a-only"),
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-root-a",
                unit_id="runtime-child-root-a",
                planned_ai_unit_id="range_0",
            ),
        ),
        repeat0_additional_roots=(
            (
                ("range_0",),
                (
                    _exp5_hydration_attempt(
                        "attempt-root-b",
                        unit_id="runtime-child-root-b",
                        planned_ai_unit_id="root-a-only",
                    ),
                ),
            ),
        ),
    )

    for payload in projection.table_payloads:
        for row in payload.rows:
            assert "provider_attempt_without_planned_ai_unit" in row.ineligibility_reasons
            assert "duplicate_planned_ai_unit_id" not in row.ineligibility_reasons
            assert "multiple_provider_attempts_for_ai_unit" not in row.ineligibility_reasons


def test_exp5_metric_hydration_blocks_duplicate_actual_attempt_for_planned_unit(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-0",
                unit_id="runtime-child-duplicate",
                planned_ai_unit_id="planned-unit",
            ),
            _exp5_hydration_attempt(
                "attempt-1",
                unit_id="runtime-child-duplicate",
                planned_ai_unit_id="planned-unit",
            ),
        ),
    )

    assert all(
        "exp5_retry_forbidden" in row.ineligibility_reasons
        for payload in projection.table_payloads
        for row in payload.rows
    )


def test_exp5_metric_hydration_preserves_actual_attempt_for_unplanned_unit(
    tmp_path: Path,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-unplanned",
                unit_id="actual-unit",
            ),
        ),
    )

    assert all(
        "provider_attempt_without_planned_ai_unit" in row.ineligibility_reasons
        for payload in projection.table_payloads
        for row in payload.rows
    )


@pytest.mark.parametrize(
    ("provider_attempt_index", "provider_attempt_count"),
    ((1, 1), (0, 2)),
)
def test_exp5_metric_hydration_blocks_nonfirst_or_nonsingular_provider_attempt(
    tmp_path: Path,
    provider_attempt_index: int,
    provider_attempt_count: int,
) -> None:
    projection = _exp5_hydration_projection(
        tmp_path,
        repeat0_attempts=(
            _exp5_hydration_attempt(
                "attempt-retry",
                unit_id="runtime-child-retry",
                planned_ai_unit_id="planned-unit",
                provider_attempt_index=provider_attempt_index,
                provider_attempt_count=provider_attempt_count,
            ),
        ),
    )

    assert all(
        "exp5_retry_forbidden" in row.ineligibility_reasons
        for payload in projection.table_payloads
        for row in payload.rows
    )


def test_exp1_trace_metric_route_rejects_nonzero_current_provider_header(
    tmp_path: Path,
) -> None:
    row = _exp1_direct_metric_fixture(
        tmp_path,
        evidence_class="real_model_trace_protocol_run",
    )

    with pytest.raises(
        ValueError,
        match="persisted trace source usage header is invalid",
    ):
        formal_runner._hydrate_exp1_metric_row(
            row,
            {
                "task": {
                    "provider_attempt_count": 0,
                    "trace_source_usage": {
                        "schema_version": "tokenshare.paper_trace_source_usage.v1",
                        "attribution_kind": "immutable_response_bank",
                        "current_provider_call_count": 1,
                        "current_provider_spend_cny": "0",
                        "committed_consumption_count": 0,
                        "consumptions": [],
                    },
                },
                "attempts": [],
                "run_evidence": {"protocol_runtime": {}},
            },
        )


def test_persisted_exp3_worker_death_first_redelivery_uses_frozen_source_mapping() -> None:
    """死亡发生在首个 delivery 前时，replacement 仍严格复用冻结 source。"""

    source_identity = {
        "entry_id": "entry-worker-death-0",
        "source_acquisition_attempt_id": "acquisition-worker-death-0",
        "source_api_latency_ref": "response-bank:bank:entry-worker-death-0:latency",
        "source_terminal_kind": "success",
    }

    def consumption(*, ordinal: int) -> dict[str, object]:
        return {
            "consumption_id": f"worker-death-redelivery-{ordinal}",
            "current_attempt_id": f"current-attempt-{ordinal}",
            "current_attempt_ordinal": ordinal,
            "delivery_kind": "fault_redelivery",
            "redelivery_reason": (
                "exp3_real_ai_fault_recovery:exp3_worker_death_test:"
                "worker_death:FULL:saved_terminal_artifact_redelivery"
            ),
            "unit_id": "unit-range-0",
            "planned_ai_unit_id": "range_0",
            "source_planned_ai_unit_id": "range_0",
            "replacement_slot": 0,
            "source_sample_slot_index": 0,
            "source_replacement_slot": 0,
            **source_identity,
            "source_model_record": {},
            "prompt_tokens": 4,
            "completion_tokens": 6,
            "total_tokens": 10,
            "latency_ms": 20,
            "source_api_latency_missing": False,
            "source_api_latency_missing_count": 0,
            "protocol_operational_delay_ms": 3,
            "cost_estimate_cny": "0.5",
            "source_bank_roles": [],
        }

    task = {
        "experiment_id": "exp3_real_ai_fault_recovery",
        "condition_id": "exp3_worker_death_test",
        "dead_worker_count_target": 1,
        "worker_death_count": 1,
        "worker_death_evidence_complete": False,
        "paired_trace_reference": {
            "source_entry_ids": [source_identity["entry_id"]],
            "source_binding_digests": ["binding-worker-death-0"],
            "current_delivery_source_mappings": [
                {
                    "source_binding_digest": "binding-worker-death-0",
                    "current_planned_ai_unit_id": "range_0",
                    "current_sample_slot_index": 0,
                    "current_attempt_ordinal": ordinal,
                    "source_entry_id": source_identity["entry_id"],
                    "source_sample_slot_index": 0,
                    "source_replacement_slot": 0,
                    "source_inference_request_digest": "request-worker-death-0",
                }
                for ordinal in (0, 1, 2)
            ],
        },
        "trace_source_usage": {
            "schema_version": "tokenshare.paper_trace_source_usage.v1",
            "attribution_kind": "immutable_response_bank",
            "current_provider_call_count": 0,
            "current_provider_spend_cny": "0",
            "committed_consumption_count": 2,
            "source_provider_attempt_count": 1,
            "source_tokens_total": 10,
            "source_tokens_known_total": 10,
            "source_tokens_missing_attempt_count": 0,
            "source_api_latency_total_ms": 20,
            "source_api_latency_known_total_ms": 20,
            "source_api_latency_missing_attempt_count": 0,
            "source_cost_total_cny": "0.5",
            "source_cost_known_total_cny": "0.5",
            "source_cost_missing_attempt_count": 0,
            "consumptions": [consumption(ordinal=1), consumption(ordinal=2)],
        },
    }

    assert formal_runner._persisted_trace_source_consumptions(task) == tuple(
        task["trace_source_usage"]["consumptions"]
    )

    incomplete_worker_death = json.loads(json.dumps(task))
    incomplete_worker_death["worker_death_count"] = 0
    with pytest.raises(
        ValueError,
        match="persisted trace fault redelivery lacks ordinary source",
    ):
        formal_runner._persisted_trace_source_consumptions(incomplete_worker_death)

    drifted_mapping = json.loads(json.dumps(task))
    drifted_mapping["paired_trace_reference"][
        "current_delivery_source_mappings"
    ][1]["source_entry_id"] = "foreign-entry"
    with pytest.raises(
        ValueError,
        match="persisted trace fault redelivery lacks ordinary source",
    ):
        formal_runner._persisted_trace_source_consumptions(drifted_mapping)


def test_exp1_metric_hydration_uses_persisted_ledger_clock_without_runtime_observation(
    tmp_path: Path,
) -> None:
    row = _exp1_direct_metric_fixture(tmp_path)
    binding = row.execution_binding
    facts = {
        "task": {"provider_attempt_count": 1},
        "attempts": [
            {
                "attempt_id": "attempt-1",
                "provider_attempt_count": 1,
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
                "cost_estimate": 0.25,
                "cost_estimate_currency": "CNY",
                "cost_estimate_status": "estimated",
                "usage_ref": {"artifact_id": "usage-1"},
            }
        ],
        "run_evidence": {"protocol_runtime": {"runtime_observation": None}},
        "ledger_root_clock": {
            "schema_version": "tokenshare.paper_ledger_root_clock.v1",
            "run_id": binding.execution_id,
            "task_id": binding.task_id,
            "root_unit_id": binding.root_unit_id,
            "root_started_at": "1970-01-01T00:00:01Z",
            "root_terminal_at": "1970-01-01T00:00:02.500000Z",
        },
    }

    hydrated = formal_runner._hydrate_exp1_metric_row(row, facts)

    assert hydrated.root_start_at_ms == 1000
    assert hydrated.root_terminal_at_ms == 2500
    assert hydrated.actual_provider_attempts[0].cost_estimate_cny == 0.25


def test_lean_root_clock_view_uses_verified_ledger_without_runtime_observation() -> None:
    events = (
        SimpleNamespace(
            task_id="lean-task-1",
            object_id="lean-root-unit-1",
            occurred_at="1970-01-01T00:00:01.000123Z",
            payload={
                "task_unit": {"unit_id": "lean-root-unit-1", "state": "ready"}
            },
        ),
        SimpleNamespace(
            task_id="lean-task-1",
            object_id="lean-root-unit-1",
            occurred_at="1970-01-01T00:00:02.500987Z",
            payload={
                "task_unit": {
                    "unit_id": "lean-root-unit-1",
                    "state": "completed",
                }
            },
        ),
    )
    clock = formal_runner._root_clock_from_verified_ledger(
        events=events,
        run_id="lean-run-1",
        task_id="lean-task-1",
        root_unit_id="lean-root-unit-1",
    )

    started, terminal = formal_runner._persisted_ledger_root_clock_ms(
        {"ledger_root_clock": clock, "run_evidence": {"runtime_observation": None}},
        SimpleNamespace(
            execution_binding=SimpleNamespace(
                execution_id="lean-run-1",
                task_id="lean-task-1",
                root_unit_id="lean-root-unit-1",
            )
        ),
    )

    assert str(started) == "1000.123"
    assert str(terminal) == "2500.987"


def test_no_requeue_stuck_root_persists_typed_incomplete_ledger_clock() -> None:
    events = (
        SimpleNamespace(
            task_id="factor-task-1",
            object_id="factor-root-unit-1",
            occurred_at="1970-01-01T00:00:01.000123Z",
            payload={
                "task_unit": {"unit_id": "factor-root-unit-1", "state": "ready"}
            },
        ),
        SimpleNamespace(
            task_id="factor-task-1",
            object_id="factor-root-unit-1",
            occurred_at="1970-01-01T00:00:02.500987Z",
            payload={
                "task_unit": {
                    "unit_id": "factor-root-unit-1",
                    "state": "processing",
                }
            },
        ),
    )
    row = SimpleNamespace(
        experiment_id="exp4_real_ai_protocol_ablation",
        condition_axes={"ablation_mode": "NO_REQUEUE"},
        execution_binding=SimpleNamespace(
            execution_id="factor-run-1",
            task_id="factor-task-1",
            root_unit_id="factor-root-unit-1",
        ),
    )
    task = {
        "root_status": "failed",
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
        "ablation_mode": "NO_REQUEUE",
        "ablation_applicable": True,
        "ablation_runtime_flags": {"stuck_after_rejection": True},
    }

    clock = formal_runner._root_clock_from_verified_ledger(
        events=events,
        run_id="factor-run-1",
        task_id="factor-task-1",
        root_unit_id="factor-root-unit-1",
        row=row,
        task=task,
    )

    assert clock == {
        "schema_version": "tokenshare.paper_ledger_root_clock.v2",
        "run_id": "factor-run-1",
        "task_id": "factor-task-1",
        "root_unit_id": "factor-root-unit-1",
        "root_started_at": "1970-01-01T00:00:01.000123Z",
        "root_terminal_at": None,
        "incomplete_reason": "no_requeue_stuck_after_rejection",
    }
    started, terminal = formal_runner._persisted_ledger_root_clock_ms(
        {"ledger_root_clock": clock, "task": task},
        row,
    )
    assert str(started) == "1000.123"
    assert terminal is None


def test_no_merge_gate_premature_merge_persists_typed_incomplete_ledger_clock() -> None:
    events = (
        SimpleNamespace(
            task_id="factor-task-1",
            object_id="factor-root-unit-1",
            occurred_at="1970-01-01T00:00:01.000123Z",
            payload={
                "task_unit": {"unit_id": "factor-root-unit-1", "state": "ready"}
            },
        ),
        SimpleNamespace(
            task_id="factor-task-1",
            object_id="factor-root-unit-1",
            occurred_at="1970-01-01T00:00:02.500987Z",
            payload={
                "task_unit": {
                    "unit_id": "factor-root-unit-1",
                    "state": "processing",
                }
            },
        ),
    )
    row = SimpleNamespace(
        experiment_id="exp4_real_ai_protocol_ablation",
        condition_axes={"ablation_mode": "NO_MERGE_GATE"},
    )
    artifact_ref = ArtifactRef(
        artifact_id="premature-merge-1",
        artifact_type="experiment_evidence",
        uri="artifacts/premature-merge-1.json",
        content_hash="sha256:" + "1" * 64,
        size_bytes=17,
        media_type="application/json",
        artifact_schema_id="tokenshare.premature_merge_attempt",
        artifact_schema_version="v1",
        source={"kind": "pytest"},
        metadata={"ablation_mode": "NO_MERGE_GATE"},
        created_at="1970-01-01T00:00:01Z",
    )
    hook_observations = [
        build_experiment_ablation_gate_applied_observation(
            ablation_mode="NO_MERGE_GATE",
            disabled_mechanism="merge_gate",
            protocol_event_refs=(),
            artifact_refs=(artifact_ref,),
            hook_input={
                "gate_satisfied": False,
                "required_child_unit_ids": ["child-1", "child-2"],
            },
            hook_result={"bypass": True, "stop": False},
        ).to_dict(),
        build_experiment_premature_merge_attempted_observation(
            attempt_schema_version="tokenshare.premature_merge_attempt.v1",
            run_id="factor-run-1",
            task_id="factor-task-1",
            parent_unit_id="factor-root-unit-1",
            required_child_unit_ids=("child-1", "child-2"),
            canonical_child_unit_ids=("child-1",),
            missing_child_unit_ids=("child-2",),
            attempt_status="executed",
            plugin_result_type=None,
            plugin_error="ValueError: missing child",
            root_check_passed=False,
            failure_kind="merge_readiness_unsatisfied",
            protocol_event_refs=(),
            result_artifact_ref=artifact_ref,
        ).to_dict(),
    ]
    task = {
        "root_status": "blocked",
        "wall_clock_ms": 2_500,
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
        "ablation_mode": "NO_MERGE_GATE",
        "ablation_applicable": True,
        "ablation_runtime_flags": {"premature_merge_attempted": True},
        "ablation_runtime": {"hook_observations": hook_observations},
        "runtime_generation_identity": {
            "run_id": "factor-run-1",
            "task_id": "factor-task-1",
            "root_unit_id": "factor-root-unit-1",
        },
    }
    normalized = (
        formal_runner._normalize_exp4_no_merge_gate_premature_merge_checkpoint_task(
            condition=SimpleNamespace(
                experiment_id="exp4_real_ai_protocol_ablation",
                ablation_mode="NO_MERGE_GATE",
            ),
            task=task,
            run_id="factor-run-1",
            task_id="factor-task-1",
            root_unit_id="factor-root-unit-1",
        )
    )
    assert normalized is True
    assert task["root_status"] == "failed"
    assert task["wall_clock_ms"] is None
    assert task["ablation_runtime"]["hook_observations"] == hook_observations

    for field_name, foreign_value in (
        ("run_id", "foreign-run"),
        ("task_id", "foreign-task"),
        ("parent_unit_id", "foreign-root"),
    ):
        foreign_hook_task = json.loads(json.dumps(task))
        foreign_hook_task["root_status"] = "blocked"
        foreign_hook_task["wall_clock_ms"] = 2_500
        foreign_hook_task["ablation_runtime"]["hook_observations"][1]["payload"][
            field_name
        ] = foreign_value
        assert (
            formal_runner._normalize_exp4_no_merge_gate_premature_merge_checkpoint_task(
                condition=SimpleNamespace(
                    experiment_id="exp4_real_ai_protocol_ablation",
                    ablation_mode="NO_MERGE_GATE",
                ),
                task=foreign_hook_task,
                run_id="factor-run-1",
                task_id="factor-task-1",
                root_unit_id="factor-root-unit-1",
            )
            is False
        )
        assert foreign_hook_task["root_status"] == "blocked"
        foreign_hook_task["root_status"] = "failed"
        with pytest.raises(
            ValueError,
            match="verified ledger root lifecycle clock is incomplete",
        ):
            formal_runner._root_clock_from_verified_ledger(
                events=events,
                run_id="factor-run-1",
                task_id="factor-task-1",
                root_unit_id="factor-root-unit-1",
                row=row,
                task=foreign_hook_task,
            )

    for field_name, foreign_value in (
        ("run_id", "foreign-run"),
        ("task_id", "foreign-task"),
        ("root_unit_id", "foreign-root"),
    ):
        foreign_identity_task = json.loads(json.dumps(task))
        foreign_identity_task["runtime_generation_identity"][field_name] = (
            foreign_value
        )
        with pytest.raises(
            ValueError,
            match="verified ledger root lifecycle clock is incomplete",
        ):
            formal_runner._root_clock_from_verified_ledger(
                events=events,
                run_id="factor-run-1",
                task_id="factor-task-1",
                root_unit_id="factor-root-unit-1",
                row=row,
                task=foreign_identity_task,
            )

    clock = formal_runner._root_clock_from_verified_ledger(
        events=events,
        run_id="factor-run-1",
        task_id="factor-task-1",
        root_unit_id="factor-root-unit-1",
        row=row,
        task=task,
    )

    assert clock == {
        "schema_version": "tokenshare.paper_ledger_root_clock.v3",
        "run_id": "factor-run-1",
        "task_id": "factor-task-1",
        "root_unit_id": "factor-root-unit-1",
        "root_started_at": "1970-01-01T00:00:01.000123Z",
        "root_terminal_at": None,
        "incomplete_reason": "no_merge_gate_premature_merge_unsatisfied",
    }
    started, terminal = formal_runner._persisted_ledger_root_clock_ms(
        {"ledger_root_clock": clock, "task": task},
        row,
    )
    assert str(started) == "1000.123"
    assert terminal is None


def test_no_merge_gate_checkpoint_normalization_requires_native_hook_evidence() -> None:
    task = {
        "root_status": "blocked",
        "wall_clock_ms": 2_500,
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
        "ablation_mode": "NO_MERGE_GATE",
        "ablation_applicable": True,
        "ablation_runtime_flags": {"premature_merge_attempted": True},
        "ablation_runtime": {"hook_observations": []},
    }

    normalized = (
        formal_runner._normalize_exp4_no_merge_gate_premature_merge_checkpoint_task(
            condition=SimpleNamespace(
                experiment_id="exp4_real_ai_protocol_ablation",
                ablation_mode="NO_MERGE_GATE",
            ),
            task=task,
            run_id="factor-run-1",
            task_id="factor-task-1",
            root_unit_id="factor-root-unit-1",
        )
    )

    assert normalized is False
    assert task["root_status"] == "blocked"
    assert task["wall_clock_ms"] == 2_500


@pytest.mark.parametrize(
    ("ablation_mode", "stuck_after_rejection", "ablation_applicable"),
    (
        ("FULL", True, True),
        ("NO_REQUEUE", False, True),
        ("NO_REQUEUE", True, False),
    ),
)
def test_nonterminal_root_clock_remains_fail_closed_outside_typed_no_requeue_stuck(
    ablation_mode: str,
    stuck_after_rejection: bool,
    ablation_applicable: bool,
) -> None:
    with pytest.raises(
        ValueError,
        match="verified ledger root lifecycle clock is incomplete",
    ):
        formal_runner._root_clock_from_verified_ledger(
            events=(
                SimpleNamespace(
                    task_id="factor-task-1",
                    object_id="factor-root-unit-1",
                    occurred_at="1970-01-01T00:00:01Z",
                    payload={
                        "task_unit": {
                            "unit_id": "factor-root-unit-1",
                            "state": "processing",
                        }
                    },
                ),
            ),
            run_id="factor-run-1",
            task_id="factor-task-1",
            root_unit_id="factor-root-unit-1",
            row=SimpleNamespace(
                experiment_id="exp4_real_ai_protocol_ablation",
                condition_axes={"ablation_mode": ablation_mode},
            ),
            task={
                "ablation_mode": ablation_mode,
                "ablation_applicable": ablation_applicable,
                "ablation_runtime_flags": {
                    "stuck_after_rejection": stuck_after_rejection
                },
            },
        )


@pytest.mark.parametrize(
    ("experiment_id", "row_mode", "task_applicable"),
    (
        ("foreign_experiment", "NO_REQUEUE", True),
        ("exp4_real_ai_protocol_ablation", "FULL", True),
        ("exp4_real_ai_protocol_ablation", "NO_REQUEUE", False),
    ),
)
def test_persisted_incomplete_clock_rejects_foreign_or_unbound_row(
    experiment_id: str,
    row_mode: str,
    task_applicable: bool,
) -> None:
    clock = {
        "schema_version": "tokenshare.paper_ledger_root_clock.v2",
        "run_id": "factor-run-1",
        "task_id": "factor-task-1",
        "root_unit_id": "factor-root-unit-1",
        "root_started_at": "1970-01-01T00:00:01Z",
        "root_terminal_at": None,
        "incomplete_reason": "no_requeue_stuck_after_rejection",
    }
    row = SimpleNamespace(
        experiment_id=experiment_id,
        condition_axes={"ablation_mode": row_mode},
        execution_binding=SimpleNamespace(
            execution_id="factor-run-1",
            task_id="factor-task-1",
            root_unit_id="factor-root-unit-1",
        ),
    )
    with pytest.raises(
        ValueError,
        match="persisted incomplete ledger root clock binding is invalid",
    ):
        formal_runner._persisted_ledger_root_clock_ms(
            {
                "ledger_root_clock": clock,
                "task": {
                    "ablation_mode": "NO_REQUEUE",
                    "ablation_applicable": task_applicable,
                    "ablation_runtime_flags": {"stuck_after_rejection": True},
                },
            },
            row,
        )


def test_ledger_root_clock_rejects_timezone_free_timestamps() -> None:
    with pytest.raises(ValueError, match="must include timezone"):
        formal_runner._root_clock_from_verified_ledger(
            events=(
                SimpleNamespace(
                    task_id="task-1",
                    object_id="root-1",
                    occurred_at="1970-01-01T00:00:01",
                    payload={"task_unit": {"unit_id": "root-1", "state": "ready"}},
                ),
                SimpleNamespace(
                    task_id="task-1",
                    object_id="root-1",
                    occurred_at="1970-01-01T00:00:02",
                    payload={
                        "task_unit": {"unit_id": "root-1", "state": "completed"}
                    },
                ),
            ),
            run_id="run-1",
            task_id="task-1",
            root_unit_id="root-1",
        )


@pytest.mark.parametrize(
    ("override",),
    [
        ({"cost_estimate_currency": "USD"},),
        ({"cost_estimate_status": None},),
        ({"cost_estimate_status": "usage_missing"},),
        ({"usage_ref": None},),
        ({"total_tokens": None},),
    ],
)
def test_exp1_metric_hydration_keeps_non_cny_or_incomplete_usage_blocked(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    attempt = {
        "attempt_id": "attempt-1",
        "provider_attempt_count": 1,
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
        "cost_estimate": 0.25,
        "cost_estimate_currency": "CNY",
        "cost_estimate_status": "estimated",
        "usage_ref": {"artifact_id": "usage-1"},
        **override,
    }

    hydrated = formal_runner._hydrate_exp1_metric_row(
        _exp1_direct_metric_fixture(tmp_path),
        {
            "task": {"provider_attempt_count": 1},
            "attempts": [attempt],
            "run_evidence": {"protocol_runtime": {}},
        },
    )

    assert hydrated.actual_provider_attempts[0].cost_estimate_cny is None


def test_exp1_metric_identity_view_preserves_authoritative_frozen_row(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_direct_results import (
        _canonical_fixture,
        _inventory_manifest,
        _inventory_row,
        _project,
        _replace_inventory_row,
    )
    from tokenshare.experiments.paper_direct_results import (
        build_canonical_direct_evidence,
    )
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
    )

    base, condition_manifest, catalog = _inventory_row(
        evidence_class="online_real_provider",
    )
    row = _replace_inventory_row(
        base,
        experiment_id="exp1_real_ai_feasibility",
    )
    kwargs, *_ = _canonical_fixture(tmp_path, row)
    evidence = build_canonical_direct_evidence(**kwargs)
    authoritative = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    ).rows[0]

    projected = derive_paper_metric_projection_rows(
        {"exp1_feasibility": (authoritative,)}
    )["exp1_feasibility"][0]

    assert authoritative.experiment_id == "exp1_real_ai_feasibility"
    assert projected.experiment_id == "experiment_1"
    authoritative_body = authoritative.to_dict()
    authoritative_body["experiment_id"] = "experiment_1"
    assert projected.to_dict() == authoritative_body


@pytest.mark.parametrize(
    ("evidence_class", "input_key", "metric_experiment_id"),
    (
        (
            "real_model_trace_protocol_run",
            "exp2_trace_scalability",
            "experiment_2_trace",
        ),
        (
            "online_real_provider",
            "exp2_online_concurrency",
            "experiment_2_online",
        ),
    ),
)
def test_exp2_metric_identity_view_preserves_authoritative_frozen_row(
    tmp_path: Path,
    evidence_class: str,
    input_key: str,
    metric_experiment_id: str,
) -> None:
    from copy import copy

    from tests.experiments.test_paper_direct_results import (
        _canonical_fixture,
        _inventory_manifest,
        _inventory_row,
        _project,
        _replace_inventory_row,
    )
    from tokenshare.experiments.paper_direct_results import (
        build_canonical_direct_evidence,
    )
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
        _resolve_metric_projection_rows,
    )

    base, condition_manifest, catalog = _inventory_row(
        evidence_class=evidence_class,
    )
    row = _replace_inventory_row(
        base,
        experiment_id="exp2_real_ai_scalability",
    )
    kwargs, *_ = _canonical_fixture(
        tmp_path,
        row,
        evidence_class=evidence_class,
    )
    evidence = build_canonical_direct_evidence(**kwargs)
    direct = _project(
        _inventory_manifest(row),
        (condition_manifest,),
        (catalog,),
        {row.preregistered_root_run_id: evidence},
    ).rows[0]
    task_facts: dict[str, object] = {"member_kind": "preregistered_root"}
    if evidence_class == "real_model_trace_protocol_run":
        source_roles = formal_runner._source_roles(direct)
        assert source_roles is not None
        source_entry_ids = {
            locator.entry_id for locator in direct.source_bank_object_locators
        }
        assert len(source_entry_ids) == 1
        source_entry_id = next(iter(source_entry_ids))
        planned_ai_unit_id = "range_0"
        assert direct.current_provider_object_refs == ()
        assert direct.source_bank_object_locators
        task_facts.update(
            {
                "provider_attempt_count": 0,
                "trace_source_usage": {
                    "schema_version": "tokenshare.paper_trace_source_usage.v1",
                    "attribution_kind": "immutable_response_bank",
                    "current_provider_call_count": 0,
                    "current_provider_spend_cny": "0",
                    "committed_consumption_count": 1,
                    "consumptions": [
                        {
                            "consumption_id": "exp2-source-consumption-1",
                            "entry_id": source_entry_id,
                            "planned_ai_unit_id": planned_ai_unit_id,
                            "unit_id": "unit-range-0",
                            "total_tokens": 7,
                            "latency_ms": 11,
                            "cost_estimate_cny": "0.25",
                            "source_bank_roles": list(source_roles),
                        }
                    ],
                },
            }
        )
    producer_facts = {
        direct.preregistered_root_run_id: {
            "task": task_facts,
            "attempts": [],
            "faults": [],
            "events": [],
            "run_evidence": {
                "protocol_runtime": {
                    "runtime_observation": {
                        "runtime_wall_clock_ms": 1,
                        "planned_ai_unit_ids": (
                            [planned_ai_unit_id]
                            if evidence_class == "real_model_trace_protocol_run"
                            else []
                        ),
                        "dispatched_ai_unit_ids": (
                            [planned_ai_unit_id]
                            if evidence_class == "real_model_trace_protocol_run"
                            else []
                        ),
                        "completed_ai_unit_ids": (
                            [planned_ai_unit_id]
                            if evidence_class == "real_model_trace_protocol_run"
                            else []
                        ),
                        "in_flight_ai_unit_ids_at_witness": [],
                        "observed_peak_concurrency": 0,
                    }
                }
            },
        }
    }

    canonical = formal_runner._canonical_metric_inputs(
        (direct,),
        producer_facts_by_root=producer_facts,
    )
    projected = derive_paper_metric_projection_rows(canonical)
    authoritative = canonical[input_key][0].direct_result
    metric_view = projected[input_key][0].direct_result

    assert authoritative.experiment_id == "exp2_real_ai_scalability"
    assert metric_view.experiment_id == metric_experiment_id
    authoritative_body = authoritative.to_dict()
    metric_body = metric_view.to_dict()
    authoritative_body["experiment_id"] = metric_experiment_id
    assert metric_body == authoritative_body
    canonical_view = {input_key: canonical[input_key]}
    projected_view = {input_key: projected[input_key]}
    assert (
        _resolve_metric_projection_rows(canonical_view, projected_view)
        is projected_view
    )

    wrong_root_direct = copy(metric_view)
    object.__setattr__(
        wrong_root_direct,
        "preregistered_root_run_id",
        "inventory:wrong-root",
    )
    wrong_root_row = replace(
        projected[input_key][0],
        direct_result=wrong_root_direct,
    )
    with pytest.raises(ValueError, match="root/order mismatch"):
        _resolve_metric_projection_rows(
            canonical_view,
            {input_key: (wrong_root_row,)},
        )

    wrong_field_direct = copy(metric_view)
    object.__setattr__(wrong_field_direct, "case_id", "wrong-case")
    wrong_field_row = replace(
        projected[input_key][0],
        direct_result=wrong_field_direct,
    )
    with pytest.raises(ValueError, match="differs beyond allowed identity view"):
        _resolve_metric_projection_rows(
            canonical_view,
            {input_key: (wrong_field_row,)},
        )
    with pytest.raises(ValueError, match="keys must match"):
        _resolve_metric_projection_rows(canonical_view, {})
    with pytest.raises(ValueError, match="row count mismatch"):
        _resolve_metric_projection_rows(canonical_view, {input_key: ()})

    second_direct = copy(authoritative)
    object.__setattr__(
        second_direct,
        "preregistered_root_run_id",
        "inventory:second-root",
    )
    second_row = copy(canonical[input_key][0])
    object.__setattr__(second_row, "direct_result", second_direct)
    canonical_pair = {input_key: (canonical[input_key][0], second_row)}
    projected_pair = derive_paper_metric_projection_rows(canonical_pair)
    with pytest.raises(ValueError, match="root/order mismatch"):
        _resolve_metric_projection_rows(
            canonical_pair,
            {input_key: tuple(reversed(projected_pair[input_key]))},
        )

    other_key = (
        "exp2_online_concurrency"
        if input_key == "exp2_trace_scalability"
        else "exp2_trace_scalability"
    )
    with pytest.raises(TypeError, match="row type mismatch"):
        derive_paper_metric_projection_rows({other_key: canonical[input_key]})

    cross_evidence = (
        "online_real_provider"
        if evidence_class == "real_model_trace_protocol_run"
        else "real_model_trace_protocol_run"
    )
    corrupted_row = copy(canonical[input_key][0])
    corrupted_direct = copy(authoritative)
    object.__setattr__(corrupted_direct, "evidence_class", cross_evidence)
    object.__setattr__(
        corrupted_row,
        "direct_result",
        corrupted_direct,
    )
    with pytest.raises(ValueError, match="identity route mismatch"):
        derive_paper_metric_projection_rows({input_key: (corrupted_row,)})

    wrong_id_row = copy(canonical[input_key][0])
    wrong_id_direct = copy(authoritative)
    object.__setattr__(wrong_id_direct, "experiment_id", "wrong-experiment")
    object.__setattr__(wrong_id_row, "direct_result", wrong_id_direct)
    with pytest.raises(ValueError, match="identity route mismatch"):
        derive_paper_metric_projection_rows({input_key: (wrong_id_row,)})


def test_exp2_metric_identity_view_rejects_key_or_evidence_cross_route(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
    )

    marker = object()
    with pytest.raises(TypeError, match="row type mismatch"):
        derive_paper_metric_projection_rows(
            {
                "exp2_trace_scalability": (marker,),
                "exp2_online_concurrency": (),
            }
        )


def test_exp3_to_exp5_metric_inputs_are_not_identity_projected() -> None:
    from tokenshare.experiments.paper_formal_metrics import (
        derive_paper_metric_projection_rows,
    )

    values = {
        "exp3_trace_robustness": (object(),),
        "exp3_online_recovery": (object(),),
        "exp4_ablation": (object(),),
        "experiment_5": (object(),),
    }
    assert derive_paper_metric_projection_rows(values) is values


def _identity_bound_real_guard_condition(
    condition: PaperExperimentCondition,
    *,
    source_config: AIAPIExecutorConfig,
) -> PaperExperimentCondition:
    from tokenshare.experiments.paper_model_identity import (
        build_model_endpoint_identity,
    )

    identity = build_model_endpoint_identity(
        model_cohort_id="test_real_transport_guard_cohort",
        model_cohort_digest="sha256:" + "5" * 64,
        cohort_member_id="test_real_transport_guard_member",
        provider_config_id="siliconflow",
        selected_entry_id="real_transport_guard",
        expected_provider_family="siliconflow",
        expected_provider_model_id="Qwen/Qwen2.5-7B-Instruct",
        expected_reasoning_profile_id="default",
        source_config=source_config,
    )
    return replace(
        condition,
        model_cohort_id=identity.model_cohort_id,
        cohort_member_id=identity.cohort_member_id,
        provider_config_id=identity.provider_config_id,
        model_entry_id=identity.selected_entry_id,
        provider_family=identity.provider_family,
        provider_model_id=identity.provider_model_id,
        reasoning_profile_id=identity.reasoning_profile_id,
        model_cohort_digest=identity.model_cohort_digest,
        source_provider_config_digest=identity.source_provider_config_digest,
        model_endpoint_identity_digest=identity.model_endpoint_identity_digest,
    )


def _apply_exp5_identity_to_real_adapter_result(
    *,
    condition: PaperExperimentCondition,
    case_id: str,
    result: object,
    expected_adapter_parent: Path | None = None,
) -> formal_runner._RootExecutionOutcome:
    """让真实 adapter 产物经过 production Exp5 strict artifact join。"""

    result_output_root = getattr(result, "output_root", None)
    adapter_parent = (
        expected_adapter_parent
        if expected_adapter_parent is not None
        else Path(str(result_output_root)).parent
    )
    outcome = formal_runner._RootExecutionOutcome(
        case_id=case_id,
        root_status=str(getattr(result.task_result.root_status, "value", result.task_result.root_status)),
        adapter_root=adapter_parent,
        worker_id="worker-exp5-artifact-root-test",
        adapter_output_root=formal_runner._validated_adapter_output_root(
            adapter_result=result,
            expected_output_root=adapter_parent / case_id,
        ),
        task=SimpleNamespace(
            **{
                **vars(result.task_result),
                "condition_id": condition.condition_id,
                "task_id": result.task_result.task_id,
            }
        ),
        adapter_result=result,
        provider_attempt_count=result.task_result.provider_attempt_count,
        total_tokens=result.task_result.total_tokens or 0,
        cost_estimate=result.task_result.cost_estimate or 0.0,
        paper_eligible=result.eligibility_report.paper_eligible,
    )
    return formal_runner._FormalConditionExecutionCallback._apply_exp5_identity(
        SimpleNamespace(real_transport=True, execution_classification=None),
        condition=condition,
        outcome=outcome,
    )


def _assert_exp5_identity_join_loaded_all_adapter_refs(
    outcome: formal_runner._RootExecutionOutcome,
) -> None:
    records = outcome.task["model_execution_records"]
    expected_attempt_count = sum(
        int(attempt["provider_attempt_count"])
        for attempt in outcome.adapter_result["attempt_results"]
    )
    assert len(records) == expected_attempt_count
    assert records
    assert all(
        set(record) >= {"record", "request", "provenance", "usage"}
        and record["record"]["schema_version"]
        == "tokenshare.paper_model_execution_record.v2"
        for record in records
    )


def test_factorization_online_adapter_persists_native_direct_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import (
        _real_transport_config,
        _v2_condition,
    )
    from tokenshare.experiments import factorization_paper_adapter as adapter
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.executors.ai_api_config import AIAPIProviderEntry

    events: list[str] = []
    original_resolve = AIAPIProviderEntry.resolve_api_key

    def tracked_resolve(entry):
        events.append("resolve")
        return original_resolve(entry)

    class LifecycleHook:
        def __call__(self, **_context):
            return None

        def after_prepared_dispatch(self, **_context):
            events.append("reserved")

        def before_provider_dispatch(self, **_context):
            events.append("intent")

    class OrderedTransport(adapter.ScriptedFactorizationRangeTransport):
        def post_chat_completion(self, **kwargs):
            events.append("transport")
            return super().post_chat_completion(**kwargs)

    case = generate_factorization_paper_cases()[0]
    monkeypatch.setenv("TOKENSHARE_REAL_TRANSPORT_GUARD_KEY", "test-only")
    monkeypatch.setattr(AIAPIProviderEntry, "resolve_api_key", tracked_resolve)
    monkeypatch.setattr(adapter, "_validate_real_transport_mode", lambda **_kwargs: None)
    config = _real_transport_config()
    condition = _identity_bound_real_guard_condition(
        _v2_condition(case),
        source_config=config,
    )
    result = adapter.run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "factor-online",
        transport=OrderedTransport(),
        real_transport=True,
        ai_api_config=config,
        entry_id="real_transport_guard",
        post_raw_output_hook=LifecycleHook(),
    )

    assert events[:4] == ["reserved", "resolve", "intent", "transport"]
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert result.run_evidence["secret_scan_report"]["secret_checked_count"] == 1
    assert all(
        attempt.model_execution_record_ref is not None
        for attempt in result.attempt_results
        if attempt.provider_attempt_count > 0
    )

    native = result.run_evidence["paper_direct_native_artifacts"]
    store = ArtifactStore(result.output_root)
    assert all(
        store.load_artifact_ref(
            ArtifactRef.from_dict(attempt.model_execution_record_ref).artifact_id
        ).artifact_type
        == "PaperModelExecutionRecord"
        for attempt in result.attempt_results
        if attempt.provider_attempt_count > 0
    )
    resource_ref = ArtifactRef.from_dict(native["actual_resource_book_ref"])
    verdict_ref = ArtifactRef.from_dict(native["independent_verdict_ref"])
    assert store.load_artifact_ref(resource_ref.artifact_id) == resource_ref
    assert store.load_artifact_ref(verdict_ref.artifact_id) == verdict_ref
    assert resource_ref.source["role"] == "actual_resource_book"
    assert verdict_ref.source["role"] == "independent_verdict"
    verdict_body = json.loads(store.read_bytes(verdict_ref).decode("utf-8"))
    assert verdict_body["case_id"] == case["case_id"]
    assert verdict_body["status"] == "accepted"
    assert verdict_body["correct"] is True
    exp5_outcome = _apply_exp5_identity_to_real_adapter_result(
        condition=condition,
        case_id=case["case_id"],
        result=result,
    )
    _assert_exp5_identity_join_loaded_all_adapter_refs(exp5_outcome)

    for invalid_result in (
        replace(result, output_root=(tmp_path / "wrong-factor-root").as_posix()),
        replace(result, output_root=(Path(result.output_root) / case["case_id"]).as_posix()),
        SimpleNamespace(
            **{
                key: value
                for key, value in vars(result).items()
                if key != "output_root"
            }
        ),
    ):
        with pytest.raises(ValueError, match="adapter result output_root"):
            _apply_exp5_identity_to_real_adapter_result(
                condition=condition,
                case_id=case["case_id"],
                result=invalid_result,
                expected_adapter_parent=Path(result.output_root).parent,
            )
    runtime = result.run_evidence["protocol_runtime"]
    parser_refs, verifier_refs, provider_refs, copied_resource_ref = (
        formal_runner._native_direct_role_refs(
            native_store=store,
            target_store=ArtifactStore(tmp_path / "factor-direct-copy"),
            root_id="inventory:factor-online",
            execution_id=runtime["run_id"],
            task_id=runtime["task_id"],
            task=result.task_result,
            adapter_result=result,
        )
    )
    assert {ref.source["role"] for ref in parser_refs} == {"parser_result"}
    assert {ref.source["role"] for ref in verifier_refs} == {"independent_verdict"}
    assert {ref.source["role"] for ref in provider_refs} == {
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    }
    assert copied_resource_ref.source["role"] == "actual_resource_book"


def test_failed_online_adapter_preserves_missing_final_and_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import (
        _ProviderErrorTransport,
        _real_transport_config,
        _v2_condition,
    )
    from tokenshare.experiments import factorization_paper_adapter as adapter
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )

    case = generate_factorization_paper_cases()[0]
    monkeypatch.setenv("TOKENSHARE_REAL_TRANSPORT_GUARD_KEY", "test-only")
    monkeypatch.setattr(adapter, "_validate_real_transport_mode", lambda **_kwargs: None)
    config = _real_transport_config()
    condition = _identity_bound_real_guard_condition(
        _v2_condition(case),
        source_config=config,
    )
    result = adapter.run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "factor-online-failed",
        transport=_ProviderErrorTransport(),
        real_transport=True,
        ai_api_config=config,
        entry_id="real_transport_guard",
    )

    native = result.run_evidence["paper_direct_native_artifacts"]
    assert result.task_result.root_status is PaperTaskStatus.FAILED
    assert "independent_verdict_ref" not in native
    runtime = result.run_evidence["protocol_runtime"]
    parser_refs, verifier_refs, provider_refs, resource_ref = (
        formal_runner._native_direct_role_refs(
            native_store=ArtifactStore(result.output_root),
            target_store=ArtifactStore(tmp_path / "failed-direct-copy"),
            root_id="inventory:failed-online",
            execution_id=runtime["run_id"],
            task_id=runtime["task_id"],
            task=result.task_result,
            adapter_result=result,
        )
    )
    assert parser_refs == ()
    assert verifier_refs == ()
    assert len(provider_refs) == 8
    assert resource_ref is not None


def test_lean_online_adapter_persists_native_direct_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_lean_paper_adapter import (
        _condition_for_case,
        _real_transport_config,
    )
    from tests.support.lean_checker import RecordingLeanChecker
    from tokenshare.experiments import lean_paper_adapter as adapter
    import tokenshare.experiments.paper_catalog as paper_catalog_module
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.executors.ai_api_config import AIAPIProviderEntry

    events: list[str] = []
    original_resolve = AIAPIProviderEntry.resolve_api_key

    def tracked_resolve(entry):
        events.append("resolve")
        return original_resolve(entry)

    class LifecycleHook:
        def __call__(self, **_context):
            return None

        def after_prepared_dispatch(self, **_context):
            events.append("reserved")

        def before_provider_dispatch(self, **_context):
            events.append("intent")

    class OrderedTransport(adapter.ScriptedLeanPaperProofTransport):
        def post_chat_completion(self, **kwargs):
            events.append("transport")
            return super().post_chat_completion(**kwargs)

    case = paper_catalog_module._with_lean_v1_paper_difficulty(
        json.loads(
            Path("benchmarks/paper/lean_catalog.v1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
    )
    environment = adapter.default_lean_paper_environment_manifest()
    object.__setattr__(environment, "environment_digest", case["environment_digest"])
    monkeypatch.setattr(adapter, "default_lean_paper_environment_manifest", lambda: environment)
    monkeypatch.setattr(adapter, "_validate_real_transport_mode", lambda **_kwargs: None)
    monkeypatch.setenv("TOKENSHARE_REAL_TRANSPORT_GUARD_KEY", "test-only")
    monkeypatch.setattr(AIAPIProviderEntry, "resolve_api_key", tracked_resolve)
    config = _real_transport_config()
    condition = _identity_bound_real_guard_condition(
        _condition_for_case(CATALOG_DIGEST, case),
        source_config=config,
    )
    result = adapter.run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lean-online",
        transport=OrderedTransport(
            model="Qwen/Qwen2.5-7B-Instruct"
        ),
        real_transport=True,
        ai_api_config=config,
        entry_id="real_transport_guard",
        post_raw_output_hook=LifecycleHook(),
        checker=RecordingLeanChecker(),
    )

    assert events[:4] == ["reserved", "resolve", "intent", "transport"]
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert result.run_evidence["secret_scan_report"]["secret_checked_count"] == 1
    assert all(
        attempt.model_execution_record_ref is not None
        for attempt in result.attempt_results
        if attempt.provider_attempt_count > 0
    )

    native = result.run_evidence["paper_direct_native_artifacts"]
    store = ArtifactStore(result.output_root)
    assert all(
        store.load_artifact_ref(
            ArtifactRef.from_dict(attempt.model_execution_record_ref).artifact_id
        ).artifact_type
        == "PaperModelExecutionRecord"
        for attempt in result.attempt_results
        if attempt.provider_attempt_count > 0
    )
    resource_ref = ArtifactRef.from_dict(native["actual_resource_book_ref"])
    verdict_ref = ArtifactRef.from_dict(native["independent_verdict_ref"])
    checker_ref = ArtifactRef.from_dict(result.merge_summary["root_checker_report_ref"])
    assert native["schema_version"] == "tokenshare.paper_direct_native_artifacts.v2"
    assert store.load_artifact_ref(resource_ref.artifact_id) == resource_ref
    assert store.load_artifact_ref(verdict_ref.artifact_id) == verdict_ref
    assert resource_ref.source["role"] == "actual_resource_book"
    assert verdict_ref != checker_ref
    assert [ArtifactRef.from_dict(value) for value in native["domain_report_refs"]] == [
        checker_ref
    ]
    merge_event = next(
        event
        for event in result.event_records
        if event["event_type"] == "MERGE_RECORDED"
        and event["payload"]["parent_unit_id"]
        == result.run_evidence["protocol_runtime"]["root_unit_id"]
    )
    canonical_proof_ref = merge_event["payload"]["merge_output_refs"][
        "lean_proof_artifact"
    ]
    binding_body = json.loads(store.read_bytes(verdict_ref).decode("utf-8"))
    verdict_body = json.loads(store.read_bytes(checker_ref).decode("utf-8"))
    assert binding_body["schema_version"] == (
        "tokenshare.paper_lean_domain_verdict_binding.v2"
    )
    assert binding_body["root_checker_report_ref"] == checker_ref.to_dict()
    assert binding_body["final_result_ref"] == canonical_proof_ref
    assert verdict_body["schema_version"] == "lean_proof.checker_report.v1"
    assert verdict_body["status"] == "accepted"
    exp5_outcome = _apply_exp5_identity_to_real_adapter_result(
        condition=condition,
        case_id=case["case_id"],
        result=result,
    )
    _assert_exp5_identity_join_loaded_all_adapter_refs(exp5_outcome)
    assert "correct" not in verdict_body
    assert verdict_body["proof_artifact_ref"] == canonical_proof_ref["source"][
        "source_ref"
    ]
    assert binding_body["normalized_theorem_digest"] == verdict_body[
        "normalized_theorem_digest"
    ]
    root_theorem_ref = ArtifactRef.from_dict(
        binding_body["root_theorem_payload_ref"]
    )
    assert root_theorem_ref.metadata["case_id"] == case["case_id"]
    runtime = result.run_evidence["protocol_runtime"]
    copied_store = ArtifactStore(tmp_path / "lean-direct-copy")
    _parser_refs, verifier_refs, _provider_refs, _resource_ref = (
        formal_runner._native_direct_role_refs(
            native_store=store,
            target_store=copied_store,
            root_id="inventory:lean-online",
            execution_id=runtime["run_id"],
            task_id=runtime["task_id"],
            task=result.task_result,
            adapter_result=result,
        )
    )
    assert {ref.source["role"] for ref in verifier_refs} == {
        "lean_checker_verdict",
        "root_checker_report",
    }
    copied_by_role = {ref.source["role"]: ref for ref in verifier_refs}
    assert json.loads(
        copied_store.read_bytes(copied_by_role["lean_checker_verdict"]).decode("utf-8")
    ) == binding_body
    assert json.loads(
        copied_store.read_bytes(copied_by_role["root_checker_report"]).decode("utf-8")
    ) == verdict_body

    from tests.experiments.test_paper_direct_results import (
        _inventory_manifest,
        _inventory_row,
        _replace_inventory_row,
    )

    base_row, _, _ = _inventory_row(
        root_id="inventory:lean-native-closure",
        condition_id=condition.condition_id,
        case_id=str(case["case_id"]),
        evidence_class="online_real_provider",
    )
    row = _replace_inventory_row(
        base_row,
        preregistered_case_ref={
            **base_row.preregistered_case_ref,
            "official_case_digest": binding_body["official_case_digest"],
            "official_root_theorem_id": binding_body[
                "official_root_theorem_id"
            ],
            "official_root_theorem_payload_digest": binding_body[
                "official_root_theorem_payload_digest"
            ],
        },
        condition_axes={
            **base_row.condition_axes,
            "domain": "lean_proof",
            "topic_family": case["topic_family"],
        },
    )
    collector = formal_runner._CanonicalDirectCollector(
        inventory=_inventory_manifest(row),
        condition_manifests=(),
        catalog_manifests=(),
        rows_by_key={(condition.condition_id, str(case["case_id"])): row},
    )
    formal_runner._capture_canonical_direct_evidence(
        collector=collector,
        suite_root=tmp_path / "lean-native-closure",
        condition=condition,
        case_id=str(case["case_id"]),
        task=result.task_result,
        adapter_result=result,
        adapter_output_root=Path(result.output_root),
    )
    assert len(collector.evidence) == 1
    assert collector.evidence[0].independently_verified_correct is True
    assert {
        ref.source_role for ref in collector.evidence[0].verifier_checker_refs
    } == {"lean_checker_verdict", "root_checker_report"}


def test_lean_v2_online_adapter_threads_transient_secret_and_lifecycle_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_lean_paper_adapter import (
        _catalog_with_lemma_graph,
        _condition_for_case,
        _oracle_sources_by_statement,
        _real_transport_config,
    )
    from tests.support.lean_checker import RecordingLeanChecker
    from tokenshare.executors.ai_api_config import AIAPIProviderEntry
    from tokenshare.experiments import lean_paper_adapter as adapter
    import tokenshare.experiments.paper_catalog as paper_catalog_module

    events: list[str] = []
    original_resolve = AIAPIProviderEntry.resolve_api_key

    def tracked_resolve(entry):
        events.append("resolve")
        return original_resolve(entry)

    class LifecycleHook:
        def __call__(self, **_context):
            return None

        def after_prepared_dispatch(self, **_context):
            events.append("reserved")

        def before_provider_dispatch(self, **_context):
            events.append("intent")

    class OrderedTransport(adapter.ScriptedLeanPaperProofTransport):
        def post_chat_completion(self, **kwargs):
            events.append("transport")
            return super().post_chat_completion(**kwargs)

    tracked = json.loads(
        Path("benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = paper_catalog_module._current_lean_environment_manifest_without_preflight()
    object.__setattr__(manifest, "environment_digest", tracked["environment_digest"])
    original_digest = paper_catalog_module._file_digest
    oracle_source = Path(
        "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()

    def tracked_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_digest(resolved)
        source = resolved.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: manifest,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked["checker_implementation_digest"],
    )
    monkeypatch.setattr(paper_catalog_module, "_file_digest", tracked_file_digest)
    catalog = _catalog_with_lemma_graph()
    case = catalog.lean_lemma_graph_cases[0]
    selected_node_id = case["lemma_graph"]["nodes"][0]["node_id"]
    environment = adapter.default_lean_paper_environment_manifest()
    object.__setattr__(environment, "environment_digest", case["environment_digest"])
    monkeypatch.setattr(adapter, "default_lean_paper_environment_manifest", lambda: environment)
    monkeypatch.setattr(adapter, "_validate_real_transport_mode", lambda **_kwargs: None)
    monkeypatch.setenv("TOKENSHARE_REAL_TRANSPORT_GUARD_KEY", "test-only")
    monkeypatch.setattr(AIAPIProviderEntry, "resolve_api_key", tracked_resolve)

    config = _real_transport_config()
    condition = _identity_bound_real_guard_condition(
        _condition_for_case(catalog.catalog_digest, case),
        source_config=config,
    )
    result = adapter.run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lean-v2-online",
        transport=OrderedTransport(
            proof_sources_by_statement=_oracle_sources_by_statement(case),
            model="Qwen/Qwen2.5-7B-Instruct",
        ),
        real_transport=True,
        ai_api_config=config,
        entry_id="real_transport_guard",
        selected_ai_unit_id=selected_node_id,
        post_raw_output_hook=LifecycleHook(),
        checker=RecordingLeanChecker(),
    )

    assert events[:4] == ["reserved", "resolve", "intent", "transport"]
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert result.run_evidence["secret_scan_report"]["secret_checked_count"] == 1


def test_pure_exp3_trace_closes_loads_and_replays_without_current_provider_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_exp3_metrics import Exp3TraceConditionInput
    from tokenshare.experiments.paper_formal_metrics import (
        recompute_paper_formal_metrics,
    )
    from tokenshare.experiments.paper_response_bank import PaperTraceRuntimeContext

    suite, _tasks, _attempts, _dispatched, _source_entry, suite_root = (
        _run_normal_exp3_trace_condition(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            mutation_kind=None,
        )
    )

    protected = formal_runner.load_paper_traceability_replay_input_root(suite_root)
    loaded = formal_runner.load_paper_traceability_replay_inputs(suite_root)
    expected_keys = {
        "exp1_feasibility",
        "exp2_trace_scalability",
        "exp2_online_concurrency",
        "exp3_trace_robustness",
        "exp3_online_recovery",
        "exp4_ablation",
        "experiment_5",
    }
    assert suite.status is PaperStatus.COMPLETED
    assert set(loaded.direct) == expected_keys
    assert len(loaded.direct["exp3_trace_robustness"]) == 1
    assert isinstance(
        loaded.direct["exp3_trace_robustness"][0],
        Exp3TraceConditionInput,
    )
    evidence = loaded.current["canonical_runtime_evidence"]
    assert len(evidence) == 1
    root_id = evidence[0].preregistered_root_run_id
    assert evidence[0].current_provider_object_refs == ()
    assert evidence[0].source_bank_object_locators
    assert loaded.current["current_trace_wrappers_by_root"][root_id]
    assert loaded.current["eligibility_facts_by_root"][root_id]
    assert loaded.source["trace_source_bindings_by_root"][root_id]
    assert loaded.source["source_resolvers"]
    descriptor = json.loads(protected.descriptor_path.read_text(encoding="utf-8"))
    assert descriptor["current_provider_object_refs"] == []
    assert descriptor["external_source_object_refs"]

    metrics = formal_runner.recompute_paper_formal_metrics_from_runner_inputs(
        suite_root
    )
    replay_root = tmp_path / "traceability-replay"
    for relative_name, content in loaded.current_evidence_files:
        target = replay_root / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    replay = recompute_paper_formal_metrics(
        replay_root,
        loaded.direct,
        global_infrastructure_valid=loaded.current[
            "global_infrastructure_valid"
        ],
        canonical_runtime_evidence=evidence,
        requested_lineage_root_ids=loaded.current["requested_lineage_root_ids"],
        current_trace_wrappers_by_root=loaded.current[
            "current_trace_wrappers_by_root"
        ],
        trace_source_bindings_by_root=loaded.source[
            "trace_source_bindings_by_root"
        ],
        eligibility_facts_by_root=loaded.current["eligibility_facts_by_root"],
    )
    assert replay.observations_digest == metrics.observations_digest
    assert formal_runner.digest_json(
        [draft.to_dict() for draft in replay.table_drafts]
    ) == formal_runner.digest_json([draft.to_dict() for draft in metrics.table_drafts])
    assert (
        replay.lineage_source_index.index_digest
        == metrics.lineage_source_index.index_digest
    )
    assert PaperTraceRuntimeContext.current_provider_call_count == 0


def test_resume_restores_direct_evidence_from_validated_canonical_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _suite,
        _tasks,
        _attempts,
        _dispatched,
        _entry,
        suite_root,
        plan,
        catalog,
        _trace_context,
    ) = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=None,
        return_context=True,
    )
    loaded = formal_runner.load_paper_traceability_replay_inputs(suite_root)
    collector = formal_runner._build_canonical_direct_collector(
        bound_plans=((plan, plan.bound_items()),),
        catalog_manifest=catalog,
        normalized_root_filter={},
        evidence_class="real_model_trace_protocol_run",
    )

    formal_runner._restore_canonical_direct_checkpoints(
        suite_root=suite_root,
        collector=collector,
    )

    assert len(collector.evidence) == 1
    assert formal_runner._canonical_direct_evidence_digest(
        collector.evidence[0]
    ) == formal_runner._canonical_direct_evidence_digest(
        loaded.current["canonical_runtime_evidence"][0]
    )
    root_id = collector.evidence[0].preregistered_root_run_id
    assert collector.current_trace_wrappers_by_root[root_id]
    assert collector.trace_source_bindings_by_root[root_id]
    assert collector.source_resolvers


def test_trace_lineage_locator_alias_boundary_is_exact_and_fail_closed() -> None:
    from tokenshare.experiments import paper_formal_evidence

    canonicalize = paper_formal_evidence._canonical_trace_locator_digest_view
    native = {
        "request_body": "sha256:" + "1" * 64,
        "raw_output": "sha256:" + "2" * 64,
        "usage": "sha256:" + "3" * 64,
    }
    paper = {
        "request_body": "sha256:" + "1" * 64,
        "raw_output_or_provider_failure": "sha256:" + "2" * 64,
        "usage_status": "sha256:" + "3" * 64,
    }

    assert canonicalize(native) == canonicalize(paper)
    assert canonicalize({**native, "unknown_role": "sha256:" + "4" * 64}) != (
        canonicalize(paper)
    )
    assert canonicalize({key: value for key, value in native.items() if key != "usage"}) != (
        canonicalize(paper)
    )
    assert canonicalize({**native, "usage": "sha256:" + "5" * 64}) != (
        canonicalize(paper)
    )
    with pytest.raises(ValueError, match="trace locator role alias collision"):
        canonicalize(
            {
                **native,
                "raw_output_or_provider_failure": "sha256:" + "2" * 64,
            }
        )


def test_trace_resource_book_preserves_single_v1_and_reaches_all_multi_entries(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_models import ExternalBankObjectLocator

    native_store = ArtifactStore(tmp_path / "native-trace-books")
    canonical_store = ArtifactStore(tmp_path / "canonical-trace-books")
    bodies = (
        {"schema_version": "tokenshare.current_trace_wrapper.v1", "entry_id": "entry-1"},
        {"schema_version": "tokenshare.current_trace_wrapper.v1", "entry_id": "entry-2"},
    )
    native_refs = tuple(
        native_store.save_json(
            body,
            artifact_id=f"wrapper-{index}",
            artifact_type="CurrentTraceWrapper",
            artifact_schema_id="tokenshare.current_trace_wrapper.v1",
            artifact_schema_version="v1",
            source={"kind": "trace", "entry_id": body["entry_id"]},
            metadata={},
            created_at=f"2026-08-03T00:00:0{index}Z",
        )
        for index, body in enumerate(bodies, start=1)
    )
    wrappers = tuple(SimpleNamespace(to_dict=lambda body=body: dict(body)) for body in bodies)
    locators = tuple(
        ExternalBankObjectLocator(
            bank_root_id="approved-bank",
            manifest_digest="sha256:" + "1" * 64,
            entry_id=body["entry_id"],
            object_role=role,
            object_digest="sha256:" + str(index) * 64,
        )
        for index, body in enumerate(bodies, start=1)
        for role in (
            "request_body",
            "raw_output_or_provider_failure",
            "provenance",
            "usage_status",
            "latency",
            "pricing",
            "acquisition_attempt",
            "model_record",
        )
    )

    single_ref = formal_runner._persist_trace_resource_book(
        native_store=native_store,
        canonical_store=canonical_store,
        root_id="inventory:single",
        execution_id="run:single",
        task_id="task:single",
        current_wrappers=wrappers[:1],
        native_wrapper_refs=native_refs[:1],
        canonical_wrapper_refs=native_refs[:1],
        source_locators=locators[:8],
    )
    multi_ref = formal_runner._persist_trace_resource_book(
        native_store=native_store,
        canonical_store=canonical_store,
        root_id="inventory:multi",
        execution_id="run:multi",
        task_id="task:multi",
        current_wrappers=wrappers,
        native_wrapper_refs=native_refs,
        canonical_wrapper_refs=native_refs,
        source_locators=locators,
    )

    assert canonical_store.read_bytes(single_ref) == native_store.read_bytes(native_refs[0])
    assert single_ref.content_hash == native_refs[0].content_hash
    multi = json.loads(canonical_store.read_bytes(multi_ref))
    assert multi["schema_version"] == "tokenshare.paper_trace_resource_book.v2"
    assert {value["entry_id"] for value in multi["current_wrappers"]} == {
        "entry-1",
        "entry-2",
    }
    assert {value["entry_id"] for value in multi["source_bank_object_locators"]} == {
        "entry-1",
        "entry-2",
    }
    assert multi_ref.source["role"] == "trace_resource_book"


def test_trace_resource_book_selects_canonical_wrapper_after_replacement(
    tmp_path: Path,
) -> None:
    from tokenshare.executors.response_bank import CurrentTraceWrapper
    from tokenshare.experiments.paper_models import ExternalBankObjectLocator

    store = ArtifactStore(tmp_path / "replacement-wrapper-store")
    refs = tuple(
        store.save_json(
            {"attempt_id": attempt_id},
            artifact_id=f"wrapper-{attempt_id}",
            artifact_type="PreparedTraceDelivery",
            artifact_schema_id="tokenshare.prepared_trace_delivery.v1",
            artifact_schema_version="v1",
            source={"kind": "trace", "attempt_id": attempt_id},
            metadata={},
            created_at=f"2026-08-11T00:00:0{index}Z",
        )
        for index, attempt_id in enumerate(("attempt-rejected", "attempt-canonical"))
    )
    events = tuple(
        {
            "event_id": event_id,
            "event_type": "TRACE_DELIVERY_COMMITTED.v1",
            "payload": {
                "attempt_id": attempt_id,
                "current_wrapper_ref": ref.to_dict(),
            },
        }
        for event_id, attempt_id, ref in (
            ("event-rejected", "attempt-rejected", refs[0]),
            ("event-canonical", "attempt-canonical", refs[1]),
        )
    )
    current = CurrentTraceWrapper(
        current_run_id="run-1",
        current_task_id="task-1",
        current_unit_id="unit-1",
        current_attempt_id="attempt-canonical",
        attempt_ordinal=1,
        bank_root_id="bank-1",
        manifest_digest="sha256:" + "1" * 64,
        root_binding_marker_digest="sha256:" + "2" * 64,
        inference_request_digest="sha256:" + "3" * 64,
        entry_id="entry-canonical",
        locator_digests={"request_body": "sha256:" + "4" * 64},
        logical_started_at="logical:1",
        logical_finished_at="logical:2",
        source_latency_ms=1,
        current_parse_ref="parse-canonical",
        current_verifier_ref="verify-canonical",
        current_checker_ref=None,
        current_canonical_ref="canonical-canonical",
        current_ledger_ref="event-canonical",
    )

    selected = formal_runner._select_current_trace_wrapper_refs(
        events=events,
        current_wrappers=(current,),
    )

    assert selected == (refs[1],)
    locators = tuple(
        ExternalBankObjectLocator(
            bank_root_id="bank-1",
            manifest_digest="sha256:" + "1" * 64,
            entry_id=entry_id,
            object_role="request_body",
            object_digest="sha256:" + digest_digit * 64,
        )
        for entry_id, digest_digit in (
            ("entry-rejected", "5"),
            ("entry-canonical", "6"),
        )
    )
    book_ref = formal_runner._persist_trace_resource_book(
        native_store=store,
        canonical_store=store,
        root_id="inventory:replacement",
        execution_id="run-1",
        task_id="task-1",
        current_wrappers=(current,),
        native_wrapper_refs=selected,
        canonical_wrapper_refs=selected,
        source_locators=locators,
    )
    book = json.loads(store.read_bytes(book_ref))
    assert book["current_wrappers"] == [current.to_dict()]
    assert book["current_wrapper_refs"] == [refs[1].to_dict()]
    assert book["replacement_entry_ids"] == [
        "entry-canonical",
        "entry-rejected",
    ]
    with pytest.raises(ValueError, match="current trace wrapper ledger identity mismatch"):
        formal_runner._select_current_trace_wrapper_refs(
            events=events,
            current_wrappers=(replace(current, current_ledger_ref="event-rejected"),),
        )


def test_protected_formal_staging_preserves_not_started_inventory_rows_without_execution_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    _suite, tasks, _attempts, _dispatched, _entry, suite_root = (
        _run_normal_exp3_trace_condition(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            mutation_kind="root",
        )
    )
    staged = tmp_path / "staged-formal-evidence"
    shutil.copytree(suite_root, staged)
    for derived_name in ("condition_results.jsonl", "formal_runner_result.json"):
        derived = staged / derived_name
        if derived.is_file():
            derived.unlink()
    direct_rows = tuple(
        SimpleNamespace(
            experiment_id="exp3_real_ai_fault_recovery",
            condition_id="condition-exp3-current-trace",
            repeat_id=0,
            case_id=task["task_id"],
            preregistered_root_run_id=f"missing-root:{task['task_id']}",
            execution_binding=None,
            artifact_refs=(),
        )
        for task in tasks
    )

    formal_runner._prepare_protected_formal_evidence_closure(
        staged,
        suite_root=suite_root,
        canonical_direct_rows=direct_rows,
    )

    projected = _generation_records(
        staged,
        "exp3_real_ai_fault_recovery",
        "condition-exp3-current-trace",
        "per_task_results.jsonl",
    )
    assert [row["case_id"] for row in projected] == [row["task_id"] for row in tasks]
    assert [row["preregistered_root_run_id"] for row in projected] == [
        f"missing-root:{row['task_id']}" for row in tasks
    ]
    assert all(row["root_status"] in {"blocked", "not_started"} for row in projected)


@pytest.mark.parametrize("file_set", ("exact", "missing", "extra"))
def test_online_current_provider_object_closure_requires_exact_file_set(
    tmp_path: Path,
    file_set: str,
) -> None:
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs
    from tokenshare.experiments.paper_traceability import (
        TraceabilityBlockedError,
        _load_protected_replay_inputs,
    )

    descriptor, _rows = _genuine_l4_inputs(tmp_path / "online-source")
    loaded = _load_protected_replay_inputs(descriptor)
    evidence_root = tmp_path / "online-current-evidence"
    for relative_name, content in loaded.current_evidence_files:
        target = evidence_root / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    body = json.loads(descriptor.descriptor_path.read_text(encoding="utf-8"))
    current_files = {
        ref["artifact_id"]: descriptor.root_path / ref["path"]
        for ref in body["current_provider_object_refs"]
    }
    if file_set == "missing":
        current_files.pop(next(iter(current_files)))
    elif file_set == "extra":
        extra = tmp_path / "extra-provider-object.bin"
        extra.write_bytes(b"not a referenced provider object")
        current_files["extra-provider-object"] = extra
    kwargs = {
        "replay_input_root": tmp_path / f"online-{file_set}-replay-input",
        "canonical_direct_rows": loaded.direct,
        "global_infrastructure_valid": loaded.current[
            "global_infrastructure_valid"
        ],
        "canonical_runtime_evidence": loaded.current[
            "canonical_runtime_evidence"
        ],
        "requested_lineage_root_ids": loaded.current[
            "requested_lineage_root_ids"
        ],
        "current_trace_wrappers_by_root": loaded.current[
            "current_trace_wrappers_by_root"
        ],
        "trace_source_bindings_by_root": loaded.source[
            "trace_source_bindings_by_root"
        ],
        "eligibility_facts_by_root": loaded.current["eligibility_facts_by_root"],
        "source_resolvers": loaded.source["source_resolvers"],
        "current_provider_object_files": current_files,
        "current_evidence_root": evidence_root,
    }
    if file_set == "exact":
        persisted = formal_runner.persist_paper_traceability_replay_input_root(
            **kwargs
        )
        assert persisted.descriptor_path.is_file()
    else:
        with pytest.raises(TraceabilityBlockedError, match="closure is incomplete"):
            formal_runner.persist_paper_traceability_replay_input_root(**kwargs)


def test_empty_current_provider_snapshot_set_rejects_nonempty_file_map(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_traceability import _genuine_l4_inputs
    from tokenshare.experiments.paper_traceability import (
        TraceabilityBlockedError,
        _load_protected_replay_inputs,
    )

    descriptor, _rows = _genuine_l4_inputs(tmp_path / "online-source")
    loaded = _load_protected_replay_inputs(descriptor)
    evidence_root = tmp_path / "current-evidence"
    for relative_name, content in loaded.current_evidence_files:
        target = evidence_root / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    runtime_evidence = (SimpleNamespace(kind="trace-runtime-without-provider"),)
    extra = tmp_path / "unexpected-provider-object.bin"
    extra.write_bytes(b"unexpected")

    with pytest.raises(TraceabilityBlockedError, match="must be empty"):
        formal_runner.persist_paper_traceability_replay_input_root(
            replay_input_root=tmp_path / "empty-provider-replay-input",
            canonical_direct_rows={key: () for key in loaded.direct},
            canonical_runtime_evidence=runtime_evidence,
            current_provider_object_files={"unexpected": extra},
            current_evidence_root=evidence_root,
        )


@pytest.mark.parametrize("mutation_kind", ("root", "object", "role", "entry"))
def test_normal_exp3_missing_trace_dependency_blocks_without_fallback_or_later_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_kind: str,
) -> None:
    (
        suite,
        tasks,
        attempts,
        dispatched,
        _source_entry,
        _suite_root,
    ) = _run_normal_exp3_trace_condition(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutation_kind=mutation_kind,
    )

    assert suite.status is PaperStatus.BLOCKED
    assert suite.task_count == 2
    assert dispatched == ["factor_pressure_000"]
    assert [task["task_id"] for task in tasks] == [
        "factor_pressure_000",
        "factor_pressure_001",
    ]
    assert tasks[0]["root_status"] == "blocked"
    assert tasks[1]["root_status"] == "not_started"
    assert attempts[0]["provider_attempt_index"] == 0
    assert attempts[1]["provider_attempt_index"] == 0


def test_blocked_exp5_checkpoint_normalizes_decimal_provider_accounting(
    tmp_path: Path,
) -> None:
    """调用前失败也必须能持久化精确 Exp5 记账，而不能因 Decimal 再次阻断。"""

    condition = SimpleNamespace(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id="exp5-decimal-accounting",
        repeat_id=0,
    )
    blocked_error = formal_runner.PaperInfrastructureBlockedError(
        "synthetic pre-provider failure",
        evidence_integrity=formal_runner.PaperEvidenceIntegrity.INVALID,
        failure_stage="adapter_runtime",
        failure_kind="ValueError",
        condition_id=condition.condition_id,
        task_id="factor_v2_hard_001",
        diagnostics={
            "provider_accounting": {
                "provider_attempt_count": 0,
                "total_tokens": 0,
                "total_cost_estimate": Decimal("0"),
                "cost_estimate_currency": "CNY",
                "cost_estimate_status": "explicit_zero_preprovider",
            }
        },
    )
    captured: dict[str, object] = {}

    class JsonCheckpointStore:
        def checkpoint_root(self, **kwargs: object) -> None:
            # FormalEvidenceStore 写入前也只接收 JSON 值；这里精确复现该边界。
            json.dumps(kwargs["task"])
            json.dumps(kwargs["attempts"])
            captured.update(kwargs)

    formal_runner._checkpoint_dependency_outcome(
        evidence_store=JsonCheckpointStore(),
        suite_root=tmp_path,
        condition=condition,
        task_id="factor_v2_hard_001",
        root_status="blocked",
        blocked_error=blocked_error,
        execution_classification=None,
    )

    assert captured["task"]["cost_estimate"] == 0.0
    assert captured["attempts"][0]["cost_estimate"] == 0.0
