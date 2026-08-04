from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
import inspect
import json
import sys
from pathlib import Path
from threading import Barrier, Lock
from typing import Any
import weakref

import pytest

from tokenshare.executors.response_bank import (
    OBJECT_ROLES,
    ExternalBankObjectLocator,
    ResponseBankEntry,
    ResponseBankBlockedError,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ResponseBankResolver,
    ValidatedResponseBankIndex,
    canonical_digest,
    initialize_response_bank,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.executors.trace_backed import freeze_trace_source_binding
from tokenshare.experiments import paper_formal_evidence, paper_formal_runner
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelectionBatch,
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_models import PaperStatus
from tokenshare.experiments.paper_response_bank import (
    PaperFormalTraceContext,
    PaperTraceCaseBinding,
    PaperTraceRuntimeContext,
    SemanticInventoryPlan,
)
from tokenshare.storage.artifacts import ArtifactStore


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


class _ForbiddenOfflineCapturingTransport:
    tokenshare_offline_capturing_transport = True

    def post_chat_completion(self, *_args, **_kwargs):
        raise AssertionError("current provider transport must remain forbidden")


def _validated_runtime(
    *,
    entry_count: int,
    sample_slots: tuple[int, ...] | None = None,
) -> PaperTraceRuntimeContext:
    sample_slots = sample_slots or (0,) * entry_count
    assert len(sample_slots) == entry_count
    rows: list[ResponseBankInventoryRow] = []
    for index, sample_slot in enumerate(sample_slots):
        planned_ai_unit_id = (
            "factor-range-0"
            if len(set(sample_slots)) == 1
            else f"factor-range-{index}"
        )
        values: dict[str, Any] = {
            "inventory_entry_id": "",
            "semantic_slot_key": semantic_slot_key(
                case_record_digest=_DIGEST_A,
                planned_ai_unit_id=planned_ai_unit_id,
                sample_slot_index=sample_slot,
                replacement_slot=index,
                provider_config_digest=_DIGEST_B,
                prompt_profile_digest=_DIGEST_C,
                prompt_admission_profile_digest=_DIGEST_A,
                plugin_version="2.0.0",
            ),
            "case_record_digest": _DIGEST_A,
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot,
            "replacement_slot": index,
            "provider_config_digest": _DIGEST_B,
            "prompt_profile_digest": _DIGEST_C,
            "prompt_admission_profile_digest": _DIGEST_A,
            "plugin_version": "2.0.0",
            "entry_id": f"entry-{index:04d}",
            "body_digest": canonical_digest({"factor_range": index}),
            "inference_request_digest": canonical_digest(
                {"factor_range": index, "sample_slot": sample_slot}
            ),
        }
        values["inventory_entry_id"] = inventory_entry_id(values)
        rows.append(ResponseBankInventoryRow(**values))
    inventory_digest = canonical_digest(
        [row.to_dict() for row in sorted(rows, key=lambda item: item.inventory_entry_id)]
    )
    manifest = ResponseBankManifest.create(
        bank_root_id="external-factor-bank",
        profile_digest=_DIGEST_A,
        budget_digest=_DIGEST_B,
        inventory_digest=inventory_digest,
        provider_config_digest=_DIGEST_C,
        entry_ids=tuple(row.entry_id for row in rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest=_DIGEST_A,
    )
    entries: list[ResponseBankEntry] = []
    for row in rows:
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=(
                    row.body_digest
                    if role == "request_body"
                    else canonical_digest(
                        {"entry_id": row.entry_id, "object_role": role}
                    )
                ),
            )
            for role in OBJECT_ROLES
            if role != "provider_failure"
        )
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=row.inference_request_digest,
                entry_id=row.entry_id,
                sample_slot_index=row.sample_slot_index,
                replacement_slot=row.replacement_slot,
                terminal_kind="success",
                object_locators=locators,
                acquisition_state_ref=f"acquisition-{row.entry_id}",
            )
        )
    resolver = ResponseBankResolver(
        Path("external-bank-capability"),
        ValidatedResponseBankIndex.build(manifest, rows, entries),
    )
    bindings = tuple(
        freeze_trace_source_binding(
            resolver,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=next(
                row.sample_slot_index
                for row in rows
                if row.planned_ai_unit_id == planned_ai_unit_id
            ),
            entry_ids=tuple(
                row.entry_id
                for row in rows
                if row.planned_ai_unit_id == planned_ai_unit_id
            ),
        )
        for planned_ai_unit_id in dict.fromkeys(
            row.planned_ai_unit_id for row in rows
        )
    )
    return PaperTraceRuntimeContext(resolver=resolver, bindings=bindings)


def _recursive_size(value: Any, seen: set[int] | None = None) -> int:
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(
            _recursive_size(key, seen) + _recursive_size(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return size + sum(_recursive_size(item, seen) for item in value)
    if hasattr(value, "__dict__"):
        return size + _recursive_size(vars(value), seen)
    return size


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            yield from _walk(item)
    elif hasattr(value, "__dict__"):
        yield from _walk(vars(value))


def test_current_path_has_no_shared_exp1_builder_or_schema() -> None:
    assert not hasattr(
        paper_formal_evidence.FormalEvidenceStore,
        "build_shared_root_reference",
    )
    callback_source = inspect.getsource(
        paper_formal_runner._FormalConditionExecutionCallback
    )
    assert "_ensure_exp3_shared_exp1_reference" not in callback_source
    assert "tokenshare.paper_exp1_shared_reference.v1" not in callback_source


def test_exp3_reference_is_same_sample_bank_not_exp1_actual() -> None:
    runtime = _validated_runtime(entry_count=2, sample_slots=(3, 3))
    reference = paper_formal_runner._paired_trace_reference_from_runtime(
        trace_runtime=runtime,
        case_id="factor-case-1",
    )
    assert reference["comparison_kind"] == "paired_trace_reference"
    assert reference["sample_slot_index"] == 3
    assert reference["source_entry_ids"] == ["entry-0000", "entry-0001"]
    assert reference["source_acquisition_state_refs"] == [
        "acquisition-entry-0000",
        "acquisition-entry-0001",
    ]
    assert set(reference["source_acquisition_state_refs"]).isdisjoint(
        reference["source_entry_ids"]
    )
    assert "source_experiment_id" not in reference
    assert "source_task_ref" not in reference
    with pytest.raises(ValueError, match="same sample slot"):
        paper_formal_runner._paired_trace_reference_from_runtime(
            trace_runtime=_validated_runtime(entry_count=2, sample_slots=(3, 4)),
            case_id="factor-case-1",
        )


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _pressure_factor_cases(count: int) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "candidate_divisor_count": 1,
            "candidate_end": "2",
            "candidate_start": "2",
            "case_id": f"factor_pressure_{index:03d}",
            "catalog_ordinal": index,
            "difficulty": "easy",
            "factor_position_quantile": "early",
            "generator_version": "tokenshare.test.factor_pressure.v1",
            "oracle_prime_factors": [
                {"prime": "2", "exponent": 1},
                {"prime": "3", "exponent": 1},
            ],
            "paper_difficulty": "easy",
            "schema_version": "tokenshare.paper_factorization_case.v1",
            "source_seed": 20260803 + index,
            "split_params": {
                "range_policy": "contiguous",
                "requested_child_count": 1,
                "strategy_id": "factorization.candidate_range_partition.v1",
            },
            "target_n": "6",
        }
        for index in range(count)
    )


def _pressure_trace_context(
    *,
    bank_root: Path,
    condition_id: str | None = None,
    condition_ids: tuple[str, ...] | None = None,
    cases: tuple[dict[str, Any], ...],
    max_concurrent_roots: int = 1,
) -> PaperFormalTraceContext:
    from tokenshare.experiments import factorization_paper_adapter as adapter

    if (condition_id is None) == (condition_ids is None):
        raise ValueError("provide exactly one pressure trace condition binding mode")
    bound_condition_ids = (
        tuple(condition_id for _case in cases)
        if condition_id is not None
        else tuple(condition_ids or ())
    )
    if len(bound_condition_ids) != len(cases):
        raise ValueError("pressure trace condition ids must cover every case")
    source_store = ArtifactStore(bank_root.parent / "source-shapes")
    rows: list[ResponseBankInventoryRow] = []
    candidates: dict[str, bytes] = {}
    for case in cases:
        root_ref = adapter._save_root_input(source_store, case)
        subject = adapter._factor_integer_subject(case=case, root_input_ref=root_ref)
        split_plan = adapter._build_split_plan(case=case, subject=subject)
        range_fields = split_plan.partition.ranges[0].to_dict()
        candidate = adapter._scripted_range_result(
            range_fields,
            force_false_negative=False,
        )
        candidate["executor_summary"] = {
            "checked_range": "2-2",
            "source": "immutable_external_trace_fixture",
        }
        case_digest = canonical_digest(case)
        entry_id = f"entry-{case['case_id']}"
        request_body = json.dumps(
            {"entry_id": entry_id, "planned_ai_unit_id": "range_0"},
            sort_keys=True,
        ).encode("utf-8")
        values: dict[str, Any] = {
            "inventory_entry_id": "",
            "semantic_slot_key": semantic_slot_key(
                case_record_digest=case_digest,
                planned_ai_unit_id="range_0",
                sample_slot_index=0,
                replacement_slot=0,
                provider_config_digest=_DIGEST_B,
                prompt_profile_digest=_DIGEST_C,
                prompt_admission_profile_digest=_DIGEST_A,
                plugin_version="2.0.0",
            ),
            "case_record_digest": case_digest,
            "planned_ai_unit_id": "range_0",
            "sample_slot_index": 0,
            "replacement_slot": 0,
            "provider_config_digest": _DIGEST_B,
            "prompt_profile_digest": _DIGEST_C,
            "prompt_admission_profile_digest": _DIGEST_A,
            "plugin_version": "2.0.0",
            "entry_id": entry_id,
            "body_digest": _digest_bytes(request_body),
            "inference_request_digest": _digest_bytes(request_body + b":inference"),
        }
        values["inventory_entry_id"] = inventory_entry_id(values)
        rows.append(ResponseBankInventoryRow(**values))
        candidates[entry_id] = json.dumps(candidate, sort_keys=True).encode("utf-8")

    inventory_digest = canonical_digest(
        [row.to_dict() for row in sorted(rows, key=lambda item: item.inventory_entry_id)]
    )
    manifest = ResponseBankManifest.create(
        bank_root_id="external-pressure-factor-bank",
        profile_digest=_DIGEST_A,
        budget_digest=_DIGEST_B,
        inventory_digest=inventory_digest,
        provider_config_digest=_DIGEST_B,
        entry_ids=tuple(row.entry_id for row in rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest=_DIGEST_C,
    )
    entries: list[ResponseBankEntry] = []
    objects: dict[str, bytes] = {}
    for row in rows:
        raw_objects = {
            "request_body": json.dumps(
                {"entry_id": row.entry_id, "planned_ai_unit_id": "range_0"},
                sort_keys=True,
            ).encode("utf-8"),
            "raw_output": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_raw_output.v1",
                    "raw_response_json": {"id": row.entry_id},
                    "content_text": candidates[row.entry_id].decode("utf-8"),
                    "reasoning_content": None,
                    "provider_response_id": row.entry_id,
                    "finish_reason": "stop",
                },
                sort_keys=True,
            ).encode("utf-8"),
            "provenance": b'{"source_transport":"approved_real_api_acquisition"}',
            "usage": b'{"usage_status":"reported","usage":{"total_tokens":7}}',
            "latency": b'{"latency_ms":1}',
            "pricing": b'{"cost_usd":"0.01"}',
            "acquisition_attempt": b'{"attempt":"approved"}',
            "model_record": b'{"model":"frozen-model"}',
        }
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=_digest_bytes(raw_objects[role]),
            )
            for role in OBJECT_ROLES
            if role != "provider_failure"
        )
        objects.update(
            {locator.object_digest: raw_objects[locator.object_role] for locator in locators}
        )
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=row.inference_request_digest,
                entry_id=row.entry_id,
                sample_slot_index=0,
                replacement_slot=0,
                terminal_kind="success",
                object_locators=locators,
                acquisition_state_ref=f"acquisition-{row.entry_id}",
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
    row_by_case_digest = {row.case_record_digest: row for row in rows}
    case_bindings: list[PaperTraceCaseBinding] = []
    case_refs_by_condition: dict[str, list[dict[str, Any]]] = {}
    slot_keys_by_condition: dict[str, list[str]] = {}
    for case, bound_condition_id in zip(cases, bound_condition_ids, strict=True):
        case_digest = canonical_digest(case)
        row = row_by_case_digest[case_digest]
        runtime = PaperTraceRuntimeContext(
            resolver=resolver,
            bindings=(
                freeze_trace_source_binding(
                    resolver,
                    planned_ai_unit_id="range_0",
                    sample_slot_index=0,
                    entry_ids=(row.entry_id,),
                ),
            ),
        )
        case_bindings.append(
            PaperTraceCaseBinding(
                condition_id=bound_condition_id,
                case_id=str(case["case_id"]),
                case_record_digest=case_digest,
                runtime=runtime,
                inventory_entry_ids=(row.inventory_entry_id,),
            )
        )
        case_refs_by_condition.setdefault(bound_condition_id, []).append(
            {
                "case_id": case["case_id"],
                "case_record_digest": case_digest,
                "semantic_slot_keys": [row.semantic_slot_key],
            }
        )
        slot_keys_by_condition.setdefault(bound_condition_id, []).append(
            row.semantic_slot_key
        )
    return PaperFormalTraceContext(
        inventory_plan=SemanticInventoryPlan(
            schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
            inventory_digest=inventory_digest,
            rows=tuple(rows),
            condition_refs=tuple(
                {
                    "condition_id": current_condition_id,
                    "semantic_slot_keys": slot_keys_by_condition[current_condition_id],
                    "case_refs": case_refs_by_condition[current_condition_id],
                }
                for current_condition_id in dict.fromkeys(bound_condition_ids)
            ),
            exp2_online_condition_refs=(),
            max_concurrent_roots=max_concurrent_roots,
            expected_slot_count=len(rows),
            terminal_provider_failure_count=0,
            terminal_success_count=len(rows),
            terminal_unacquired_count=0,
        ),
        cases=tuple(case_bindings),
    )


def _pressure_ai_config():
    from tests.experiments.test_paper_formal_runner import _ai_config
    from tokenshare.experiments.paper_exp1 import (
        EXP1_BASELINE_ENTRY_ID,
        EXP1_BASELINE_PROVIDER_FAMILY,
        EXP1_BASELINE_PROVIDER_MODEL_ID,
        EXP1_FORMAL_REQUEST_CONTROLS,
    )

    base_config = _ai_config()
    return replace(
        base_config,
        provider_family=EXP1_BASELINE_PROVIDER_FAMILY,
        defaults=dict(EXP1_FORMAL_REQUEST_CONTROLS),
        entries=[
            replace(
                base_config.entries[0],
                entry_id=EXP1_BASELINE_ENTRY_ID,
                model=EXP1_BASELINE_PROVIDER_MODEL_ID,
                request_overrides={},
            )
        ],
    )


def test_two_worker_trace_roots_overlap_on_deterministic_condition_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _budget,
        _formal_execution_kwargs,
        _generation_records,
        _planned_dispatch_plan,
    )
    from tokenshare.experiments.paper_formal_callbacks import (
        ScheduledConditionAccumulator,
    )
    from tokenshare.experiments.paper_exp1 import (
        EXP1_BASELINE_ENTRY_ID,
        EXP1_BASELINE_PROVIDER_CONFIG_ID,
        EXP1_BASELINE_PROVIDER_FAMILY,
        EXP1_BASELINE_PROVIDER_MODEL_ID,
        EXP1_BASELINE_REASONING_PROFILE_ID,
    )

    cases = _pressure_factor_cases(3)
    case_ids = tuple(str(case["case_id"]) for case in cases)
    condition_id = "condition-pressure-workers-2"
    suite_root = tmp_path / "suite"
    config = _pressure_ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    base_condition, base_selection = plan.bound_items()[0]
    condition = replace(
        base_condition,
        condition_id=condition_id,
        worker_count=2,
        provider_config_id=EXP1_BASELINE_PROVIDER_CONFIG_ID,
        model_entry_id=EXP1_BASELINE_ENTRY_ID,
        provider_family=EXP1_BASELINE_PROVIDER_FAMILY,
        provider_model_id=EXP1_BASELINE_PROVIDER_MODEL_ID,
        reasoning_profile_id=EXP1_BASELINE_REASONING_PROFILE_ID,
        source_provider_config_digest=config.config_digest,
    )
    selection = replace(
        base_selection,
        selection_id="selection-pressure-workers-2",
        ordered_case_ids=case_ids,
        expected_ai_unit_count=3,
    )
    plan = replace(
        plan,
        conditions=(condition,),
        condition_selection_bindings=(
            FrozenConditionSelectionBinding.from_condition(condition, selection),
        ),
    )
    trace_context = _pressure_trace_context(
        bank_root=tmp_path / "external-bank",
        condition_id=condition_id,
        cases=cases,
        max_concurrent_roots=2,
    )

    class PressureFormalModule:
        def expand_conditions(self, context):
            raise AssertionError("pressure run consumes its frozen formal plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(experiment_id=plan.experiment_id, rows=())

    monkeypatch.setitem(
        paper_formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((plan.experiment_id, PressureFormalModule()),),
    )

    callback_barrier = Barrier(2)
    callback_lock = Lock()
    callback_entries = 0
    active_callbacks = 0
    observed_callback_overlap = 0
    original_rebase = paper_formal_runner._condition_scoped_trace_runtime

    def interleaved_rebase(*args, **kwargs):
        nonlocal callback_entries, active_callbacks, observed_callback_overlap
        with callback_lock:
            callback_entries += 1
            entry_ordinal = callback_entries
            active_callbacks += 1
            observed_callback_overlap = max(observed_callback_overlap, active_callbacks)
        try:
            if entry_ordinal <= 2:
                callback_barrier.wait(timeout=10)
            return original_rebase(*args, **kwargs)
        finally:
            with callback_lock:
                active_callbacks -= 1

    monkeypatch.setattr(
        paper_formal_runner,
        "_condition_scoped_trace_runtime",
        interleaved_rebase,
    )

    def concurrent_scheduler(
        *,
        ordered_case_ids,
        worker_count,
        execute_case,
        on_case_complete,
        retain_outcomes,
        **_kwargs,
    ):
        assert worker_count == 2
        assert retain_outcomes is False
        ordered = tuple(ordered_case_ids)
        accumulator = ScheduledConditionAccumulator.create(worker_count)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(execute_case, ordered[0], 0),
                executor.submit(execute_case, ordered[1], 1),
            )
            first_wave = tuple(future.result(timeout=30) for future in futures)
        last = execute_case(ordered[2], 0)
        for case_id, outcome in zip(ordered, (*first_wave, last), strict=True):
            accumulator.observe(case_id, outcome)
            on_case_complete(case_id, outcome)
        return accumulator.finish(outcomes=())

    monkeypatch.setattr(
        paper_formal_runner,
        "run_scheduled_cases",
        concurrent_scheduler,
    )
    kwargs = _formal_execution_kwargs(tmp_path=suite_root, config=config, plan=plan)
    kwargs.update(
        {
            "ai_api_configs": {EXP1_BASELINE_PROVIDER_CONFIG_ID: config},
            "catalog_manifest": {
                "catalog_digest": condition.catalog_digest,
                "factorization_cases": cases,
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": _budget(
                planned_conditions=1,
                planned_root_runs=3,
                planned_ai_units=3,
            ),
            "trace_context": trace_context,
            "transport": _ForbiddenOfflineCapturingTransport(),
        }
    )

    suite = paper_formal_runner.execute_paper_formal_suite(**kwargs)

    assert suite.status is PaperStatus.COMPLETED
    assert observed_callback_overlap == 2
    result = json.loads(
        (suite_root / "condition_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert result["metrics_ref"]["observed_max_parallel_slots"] == 2
    assert result["metrics_ref"]["wall_clock_ms"] == 2.0
    tasks = _generation_records(
        suite_root,
        plan.experiment_id,
        condition_id,
        "per_task_results.jsonl",
    )
    attempts = _generation_records(
        suite_root,
        plan.experiment_id,
        condition_id,
        "per_attempt_results.jsonl",
    )
    assert {
        (
            task["runtime_observation"]["runtime_started_at"],
            task["runtime_observation"]["runtime_ended_at"],
        )
        for task in tasks
    } == {("2026-07-14T00:00:00Z", "2026-07-14T00:00:00.001000Z")}
    for attempt in attempts:
        artifact_root = (
            suite_root
            / "experiments"
            / plan.experiment_id
            / "runs"
            / condition_id
            / "0"
            / "artifacts"
            / attempt["task_id"]
        )
        digest = attempt["raw_output_ref"]["content_hash"].removeprefix("sha256:")
        wrapper_path = next(artifact_root.glob(f"{digest}-*"))
        wrapper = json.loads(wrapper_path.read_text(encoding="utf-8"))
        assert wrapper["logical_start_ms"] == 0
        assert wrapper["logical_finish_ms"] == 1


def test_500_distinct_factor_roots_stream_through_formal_trace_and_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _budget,
        _formal_execution_kwargs,
        _generation_records,
        _planned_dispatch_plan,
    )
    from tokenshare.experiments.paper_exp1 import (
        EXP1_BASELINE_ENTRY_ID,
        EXP1_BASELINE_PROVIDER_CONFIG_ID,
        EXP1_BASELINE_PROVIDER_FAMILY,
        EXP1_BASELINE_PROVIDER_MODEL_ID,
        EXP1_BASELINE_REASONING_PROFILE_ID,
    )

    cases = _pressure_factor_cases(500)
    case_ids = tuple(str(case["case_id"]) for case in cases)
    suite_root = tmp_path / "suite"
    bank_root = tmp_path / "external-bank"
    config = _pressure_ai_config()
    plan = _planned_dispatch_plan(suite_root, config=config)
    base_condition, base_selection = plan.bound_items()[0]
    conditions = tuple(
        replace(
            base_condition,
            condition_id=f"condition-pressure-{group_index}",
            provider_config_id=EXP1_BASELINE_PROVIDER_CONFIG_ID,
            model_entry_id=EXP1_BASELINE_ENTRY_ID,
            provider_family=EXP1_BASELINE_PROVIDER_FAMILY,
            provider_model_id=EXP1_BASELINE_PROVIDER_MODEL_ID,
            reasoning_profile_id=EXP1_BASELINE_REASONING_PROFILE_ID,
            source_provider_config_digest=config.config_digest,
            seed=group_index + 1,
        )
        for group_index in range(5)
    )
    selections = tuple(
        replace(
            base_selection,
            selection_id=f"selection-pressure-{group_index}",
            ordered_case_ids=case_ids[group_index * 100 : (group_index + 1) * 100],
            expected_ai_unit_count=100,
        )
        for group_index in range(5)
    )
    plan = replace(
        plan,
        conditions=conditions,
        condition_selection_bindings=tuple(
            FrozenConditionSelectionBinding.from_condition(condition, selection)
            for condition, selection in zip(conditions, selections, strict=True)
        ),
    )
    condition_ids = tuple(
        condition.condition_id
        for condition in conditions
        for _case_id in range(100)
    )
    trace_context = _pressure_trace_context(
        bank_root=bank_root,
        condition_ids=condition_ids,
        cases=cases,
    )
    frozen_replacements = tuple(
        replacement
        for case_binding in trace_context.cases
        for binding in case_binding.runtime.bindings
        for replacement in binding.replacements
    )
    assert len(frozen_replacements) == 500
    assert {replacement.replacement_slot for replacement in frozen_replacements} == {0}
    assert len({replacement.entry_id for replacement in frozen_replacements}) == 500
    source_hashes = {
        path.relative_to(bank_root).as_posix(): _digest_bytes(
            path.read_bytes()
        )
        for path in bank_root.rglob("*")
        if path.is_file()
    }
    live_refs: list[weakref.ReferenceType[Any]] = []
    max_live_full_outcomes = 0
    max_resident_adapter_payload_bytes = 0
    original_checkpoint = paper_formal_runner._FormalConditionExecutionCallback._checkpoint_adapter_result

    def measured_checkpoint(self, **kwargs):
        nonlocal max_live_full_outcomes, max_resident_adapter_payload_bytes
        adapter_result = kwargs["adapter_result"]
        live_refs.append(weakref.ref(adapter_result))
        max_live_full_outcomes = max(
            max_live_full_outcomes,
            sum(reference() is not None for reference in live_refs),
        )
        max_resident_adapter_payload_bytes = max(
            max_resident_adapter_payload_bytes,
            _recursive_size(adapter_result),
        )
        return original_checkpoint(self, **kwargs)

    monkeypatch.setattr(
        paper_formal_runner._FormalConditionExecutionCallback,
        "_checkpoint_adapter_result",
        measured_checkpoint,
    )

    class PressureFormalModule:
        def expand_conditions(self, context):
            raise AssertionError("pressure run consumes its frozen formal plan")

        def freeze_case_selections(self, context, conditions):
            return FrozenCaseSelectionBatch(())

        def run_condition(self, context, condition, selection):
            return context.execution_callback(
                context=context,
                condition=condition,
                selection=selection,
            )

        def summarize(self, evidence):
            return ExperimentSummaryRows(
                experiment_id=plan.experiment_id,
                rows=(),
            )

    monkeypatch.setitem(
        paper_formal_runner.dispatch_paper_condition.__globals__,
        "_MODULES",
        ((plan.experiment_id, PressureFormalModule()),),
    )
    kwargs = _formal_execution_kwargs(tmp_path=suite_root, config=config, plan=plan)
    pressure_budget = _budget(
        planned_conditions=5,
        planned_root_runs=500,
        planned_ai_units=500,
    )
    from tokenshare.experiments import paper_budget

    pressure_budget = replace(
        pressure_budget,
        disk_estimate=paper_budget._paper_disk_estimate(
            planned_conditions=5,
            planned_root_runs=500,
            planned_ai_units=500,
            provider_attempt_upper_bound=500,
            max_tokens=1024,
            max_condition_root_runs=100,
            max_condition_ai_units=100,
            max_condition_provider_attempts=100,
        ),
    )
    kwargs.update(
        {
            "ai_api_configs": {EXP1_BASELINE_PROVIDER_CONFIG_ID: config},
            "catalog_manifest": {
                "catalog_digest": conditions[0].catalog_digest,
                "factorization_cases": cases,
                "lean_cases": (),
                "lean_lemma_graph_cases": (),
            },
            "budget": pressure_budget,
            "trace_context": trace_context,
            "transport": _ForbiddenOfflineCapturingTransport(),
        }
    )

    suite = paper_formal_runner.execute_paper_formal_suite(**kwargs)

    tasks = [
        task
        for condition in conditions
        for task in _generation_records(
            suite_root,
            plan.experiment_id,
            condition.condition_id,
            "per_task_results.jsonl",
        )
    ]
    attempts = [
        attempt
        for condition in conditions
        for attempt in _generation_records(
            suite_root,
            plan.experiment_id,
            condition.condition_id,
            "per_attempt_results.jsonl",
        )
    ]
    assert suite.status is PaperStatus.COMPLETED
    assert suite.task_count == 500
    assert suite.provider_attempt_count == 0
    assert len(tasks) == len(attempts) == 500
    assert tuple(task["task_id"] for task in tasks) == case_ids
    assert len(set(task["task_id"] for task in tasks)) == 500
    assert max_live_full_outcomes <= 1
    assert 0 < max_resident_adapter_payload_bytes < 5 * 1024 * 1024
    assert {task["provider_attempt_count"] for task in tasks} == {0}
    assert {attempt["provider_attempt_count"] for attempt in attempts} == {0}
    assert {attempt["raw_output_ref"]["source"]["kind"] for attempt in attempts} == {
        "external_response_bank_locator"
    }
    wrapper_entry_ids: list[str] = []
    for attempt in attempts:
        task_artifact_root = (
            suite_root
            / "experiments"
            / plan.experiment_id
            / "runs"
            / attempt["condition_id"]
            / "0"
            / "artifacts"
            / attempt["task_id"]
        )
        content_digest = attempt["raw_output_ref"]["content_hash"].removeprefix(
            "sha256:"
        )
        wrapper_paths = tuple(task_artifact_root.glob(f"{content_digest}-*"))
        assert len(wrapper_paths) == 1
        wrapper = json.loads(wrapper_paths[0].read_text(encoding="utf-8"))
        wrapper_entry_ids.append(wrapper["entry_id"])
        assert wrapper["logical_start_ms"] == 0
        assert wrapper["logical_finish_ms"] == 1
        assert {
            locator["entry_id"]
            for locator in wrapper["source_bank_object_locators"]
        } == {wrapper["entry_id"]}
        assert not any(
            key in {"payload", "object_bytes", "raw_response_json", "content_text"}
            for key in wrapper
        )
    assert tuple(wrapper_entry_ids) == tuple(
        f"entry-{case_id}" for case_id in case_ids
    )
    assert len(set(wrapper_entry_ids)) == 500

    condition_results = tuple(
        json.loads(line)
        for line in (suite_root / "condition_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    assert len(condition_results) == 5
    assert sum(result["task_count"] for result in condition_results) == 500
    assert sum(result["completed_root_count"] for result in condition_results) == 500
    assert {result["task_count"] for result in condition_results} == {100}
    assert {result["completed_root_count"] for result in condition_results} == {100}
    assert {result["status"] for result in condition_results} == {"completed"}
    assert {result["metrics_ref"]["wall_clock_ms"] for result in condition_results} == {
        100.0
    }
    assert {
        result["metrics_ref"]["observed_max_parallel_slots"]
        for result in condition_results
    } == {1}
    for condition in conditions:
        condition_manifest = json.loads(
            (
                suite_root
                / "experiments"
                / plan.experiment_id
                / "runs"
                / condition.condition_id
                / "0"
                / "condition_manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert condition_manifest["terminal"] is True
        assert condition_manifest["expected_root_count"] == 100
        assert condition_manifest["observed_root_count"] == 100
        assert condition_manifest["terminal_root_count"] == 100
        assert condition_manifest["head_generation_kind"] == "snapshot"
    assert PaperTraceRuntimeContext.current_provider_call_count == 0
    assert all(
        datetime.fromisoformat(attempt["started_at"].replace("Z", "+00:00"))
        <= datetime.fromisoformat(attempt["ended_at"].replace("Z", "+00:00"))
        for attempt in attempts
    )
    assert {
        path.relative_to(bank_root).as_posix(): _digest_bytes(
            path.read_bytes()
        )
        for path in bank_root.rglob("*")
        if path.is_file()
    } == source_hashes
    assert not any((suite_root / plan.experiment_id / "runs").rglob("objects"))
    assert not any(isinstance(value, (bytes, bytearray)) for value in _walk(trace_context))
    assert _recursive_size(trace_context.cases[0].runtime.resolver.index) < 20 * 1024 * 1024
    assert pressure_budget.disk_estimate["inputs"]["max_condition_root_runs"] == 100
    assert pressure_budget.disk_estimate["inputs"]["max_condition_ai_units"] == 100
    assert (
        pressure_budget.disk_estimate["inputs"]["max_condition_provider_attempts"]
        == 100
    )
