from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.experiments import paper_formal_metrics
from tokenshare.experiments.paper_formal_metrics import (
    publish_paper_formal_metric_drafts,
)
from tokenshare.experiments.paper_formal_evidence import (
    CanonicalLineageInput,
    FormalEvidenceStore,
    build_canonical_lineage_inputs,
    export_lineage_source_index,
    lineage_input_identity_digest,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_metric_registry import (
    PaperMetricRegistry,
    load_paper_metric_registry,
)


def _real_canonical_rows(tmp_path: Path, *, exp1_root=None):
    from tests.experiments import test_paper_exp1_metrics as exp1
    from tests.experiments import test_paper_exp2_metrics as exp2
    from tests.experiments import test_paper_exp3_metrics as exp3
    from tests.experiments import test_paper_exp4_metrics as exp4
    from tests.experiments import test_paper_exp5_metrics as exp5
    from tests.experiments import test_paper_metric_registry as registry_fixtures
    from tokenshare.experiments.paper_exp3_metrics import Exp3OnlineRecoveryInput

    exp1_root = exp1_root or exp1._not_started_row()
    online_direct = exp2._direct_row(
        tmp_path,
        root_id="task20-online-root",
        worker=1,
        evidence_class="online_real_provider",
    )
    online_recovery = Exp3OnlineRecoveryInput(
        recovery_source="validation_replacement",
        case_id="case-1",
        repeat_id=0,
        observations=(
            exp3._observation(
                "recovery-anchor",
                member_kind="exp3_online_recovery_identity",
                case_id="case-1",
                recovery_source="validation_replacement",
                current_replacement_attempt_id="attempt-1",
                provider_object_links=(),
            ),
            exp3._root("task20-exp3-root"),
        ),
    )
    return {
        "exp1_feasibility": (exp1_root,),
        "exp2_trace_scalability": exp2._trace_pair(tmp_path),
        "exp2_online_concurrency": (exp2._online(online_direct),),
        "exp3_trace_robustness": registry_fixtures._exp3_trace_payload(),
        "exp3_online_recovery": (online_recovery,),
        "exp4_ablation": (exp4._mode("FULL", exp4._root("task20-exp4-root")),),
        "experiment_5": (exp5._repeat(0),),
    }


def _executed_formal_lineage_fixture(output_root: Path):
    from tests.experiments import test_paper_direct_results as direct_fixtures
    from tests.experiments import test_paper_formal_evidence as formal_fixtures
    from tokenshare.experiments.paper_direct_results import build_canonical_direct_evidence

    inventory_row, condition_manifest, catalog = direct_fixtures._inventory_row(
        root_id="task20-executed-root",
        condition_id=formal_fixtures.CONDITION_A,
        case_id="task20-case",
        evidence_class="online_real_provider",
    )
    execution_id = "task20-execution"
    task_id = "task20-root-task"
    unit_id = "task20-root-unit"
    artifact_store = direct_fixtures.ArtifactStore(
        output_root.parent / f"{output_root.name}-task3-source"
    )
    ledger = direct_fixtures.EventLedger(
        artifact_store.root_path / "events" / "ledger.jsonl"
    )
    final_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-final",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    parser_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-parser",
        role="parser_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    verdict_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-verdict",
        role="independent_verdict",
        execution_id=execution_id,
        task_id=task_id,
        body={
            "schema_version": "tokenshare.paper_direct_correctness_verdict.v1",
            "execution_id": execution_id,
            "task_id": task_id,
            "root_unit_id": unit_id,
            "final_artifact_id": final_ref.artifact_id,
            "final_content_hash": final_ref.content_hash,
            "final_size_bytes": final_ref.size_bytes,
            "verdict_kind": "independent_verifier",
            "correct": True,
        },
    )
    current_refs = tuple(
        direct_fixtures._save_role_artifact(
            artifact_store,
            label=f"task20-{role}",
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
    resource_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-resource",
        role="actual_resource_book",
        execution_id=execution_id,
        task_id=task_id,
    )
    base_unit = {"unit_id": unit_id, "task_id": task_id, "state": "Ready"}
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.TASK_UNIT_CREATED,
        suffix="created",
        payload={"task_unit": base_unit},
    )
    for event_type, state, suffix in (
        (direct_fixtures.EventType.EXECUTION_REQUEST_RECORDED, "Running", "request"),
        (direct_fixtures.EventType.EXECUTION_SUBMISSION_RECORDED, "Submitted", "submission"),
        (direct_fixtures.EventType.VERIFICATION_RECORDED, "Verified", "verification"),
    ):
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=event_type,
            suffix=suffix,
            payload={
                "schema_version": f"paper_direct_fixture.{suffix}.v1",
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": "task20-attempt",
                "state": state,
            },
        )
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.CANONICAL_OUTPUTS_BOUND,
        suffix="canonical",
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
        suffix="merge",
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
        suffix="terminal",
        payload={"task_unit": {**base_unit, "state": "Completed"}},
    )
    runtime_result = direct_fixtures.project_protocol_run(
        run_id=execution_id,
        task_id=task_id,
        root_unit_id=unit_id,
        event_ledger=ledger,
        artifact_store=artifact_store,
    )
    kwargs = {
        "inventory_row": inventory_row,
        "execution_id": execution_id,
        "event_ledger": ledger,
        "artifact_store": artifact_store,
        "runtime_result": runtime_result,
        "final_result_ref": final_ref,
        "parser_refs": (parser_ref,),
        "verifier_checker_refs": (verdict_ref,),
        "current_provider_object_refs": current_refs,
        "actual_resource_book_ref": resource_ref,
    }
    evidence = build_canonical_direct_evidence(**kwargs)
    direct_result = direct_fixtures._project(
        direct_fixtures._inventory_manifest(inventory_row),
        (condition_manifest,),
        (catalog,),
        {inventory_row.preregistered_root_run_id: evidence},
    ).rows[0]
    binding = direct_result.execution_binding
    assert binding is not None
    def replace_experiment_identity(value):
        if isinstance(value, dict):
            return {
                key: replace_experiment_identity(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_experiment_identity(item) for item in value]
        return "experiment_1" if value == formal_fixtures.EXPERIMENT_A else value

    bodies = replace_experiment_identity(
        formal_fixtures._suite_bodies_with_root_ids((binding.task_id,))
    )
    store = FormalEvidenceStore.initialize(
        output_root=output_root,
        **bodies,
        capturing=False,
    )
    run_artifact_root = (
        output_root
        / "experiments"
        / direct_result.experiment_id
        / "runs"
        / direct_result.condition_id
        / str(direct_result.repeat_id)
        / "artifacts"
    )
    run_artifact_root.mkdir(parents=True, exist_ok=True)
    source_refs = {
        ref.artifact_id: ref
        for ref in (
            *runtime_result.artifact_refs,
            kwargs["final_result_ref"],
            *kwargs["parser_refs"],
            *kwargs["verifier_checker_refs"],
            *kwargs.get("current_provider_object_refs", ()),
            *(() if kwargs.get("actual_resource_book_ref") is None else (kwargs["actual_resource_book_ref"],)),
        )
    }
    artifact_records = []
    for index, snapshot in enumerate(direct_result.artifact_refs):
        source_ref = source_refs[snapshot.artifact_id]
        content = artifact_store.read_bytes(source_ref)
        artifact_path = run_artifact_root / f"{index:03d}-{snapshot.artifact_id}.bin"
        artifact_path.write_bytes(content)
        artifact_records.append(
            {
                "artifact_id": snapshot.artifact_id,
                "experiment_id": direct_result.experiment_id,
                "condition_id": direct_result.condition_id,
                "repeat_id": direct_result.repeat_id,
                "task_id": binding.task_id,
                "path": artifact_path.relative_to(output_root).as_posix(),
                "content_hash": snapshot.content_hash,
                "size_bytes": snapshot.size_bytes,
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
    execution_identity["runtime_generation_identity_digest"] = formal_fixtures._sha256(
        formal_fixtures._canonical_json(runtime_identity).encode("utf-8")
    )
    task = {
        **formal_fixtures._task(
            direct_result.experiment_id,
            direct_result.condition_id,
            task_id=binding.task_id,
        ),
        "case_id": direct_result.case_id,
        "preregistered_root_run_id": direct_result.preregistered_root_run_id,
        "runtime_generation_identity": runtime_identity,
        "execution_version_identity": execution_identity,
    }
    attempt = formal_fixtures._attempt(
        direct_result.experiment_id,
        direct_result.condition_id,
        task_id=binding.task_id,
    )
    attempt["attempt_id"] = f"attempt:{direct_result.preregistered_root_run_id}"
    store.checkpoint_root(
        experiment_id=direct_result.experiment_id,
        condition=formal_fixtures._condition(
            direct_result.experiment_id,
            direct_result.condition_id,
        ),
        repeat_id=direct_result.repeat_id,
        task=task,
        attempts=(attempt,),
        faults=(),
        events=tuple(event.to_dict() for event in ledger.read_all()),
        artifact_refs=tuple(artifact_records),
    )
    return evidence, direct_result


def test_formal_metrics_delegates_without_rederiving_formula(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    received = []
    original = PaperMetricRegistry.project_all

    def recording_project_all(self, inputs, *, global_infra_invalid=False):
        received.append((self, inputs, global_infra_invalid))
        return original(
            self,
            inputs,
            global_infra_invalid=global_infra_invalid,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", recording_project_all)

    publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        global_infrastructure_valid=False,
        registry=registry,
        contract=contract,
    )

    assert received == [(registry, canonical_rows, True)]
    with pytest.raises(TypeError, match="registry must be PaperMetricRegistry"):
        publish_paper_formal_metric_drafts(
            tmp_path / "fake",
            canonical_rows,
            registry=object(),
            contract=contract,
        )
    wrong_contracts = (
        replace(contract, contract_id="wrong-contract"),
        replace(contract, contract_digest="sha256:" + "f" * 64),
        replace(contract, pipeline_profile_id="wrong-profile"),
    )
    for wrong_contract in wrong_contracts:
        with pytest.raises(ValueError, match="registry contract identity"):
            publish_paper_formal_metric_drafts(
                tmp_path / "wrong-contract",
                canonical_rows,
                registry=registry,
                contract=wrong_contract,
            )
    source = inspect.getsource(paper_formal_metrics)
    for forbidden in (
        "recompute_metric",
        "_rate(",
        "_quantile(",
    ):
        assert forbidden not in source

    from tests.experiments.test_paper_exp1_metrics import _not_started_row

    direct = _not_started_row()
    with pytest.raises(TypeError):
        CanonicalLineageInput(
            direct_result=direct,
            input_identity_digest=lineage_input_identity_digest(canonical_rows),
        )
    with pytest.raises(ValueError, match="executed|canonical factory|provenance"):
        build_canonical_lineage_inputs(
            canonical_rows,
            canonical_runtime_evidence=(),
            requested_root_ids=(direct.preregistered_root_run_id,),
        )
    with pytest.raises(TypeError, match="CanonicalLineageInput"):
        export_lineage_source_index(
            FormalEvidenceStore(tmp_path / "mapping-rejected"),
            ({"direct_result": direct},),
            input_identity_digest=lineage_input_identity_digest(canonical_rows),
        )
    exporter_source = inspect.getsource(export_lineage_source_index)
    for forbidden in ("._conditions", "_logical_lineage_records", "_typed_payloads", "_collect_direct_results"):
        assert forbidden not in exporter_source

    evidence, executed_root = _executed_formal_lineage_fixture(tmp_path / "real-formal")
    real_rows = _real_canonical_rows(
        tmp_path / "real-fixtures",
        exp1_root=executed_root,
    )
    foreign_rows = _real_canonical_rows(tmp_path / "foreign-fixtures")
    with pytest.raises(ValueError, match="reachable|canonical inputs"):
        build_canonical_lineage_inputs(
            foreign_rows,
            canonical_runtime_evidence=(evidence,),
            requested_root_ids=(executed_root.preregistered_root_run_id,),
        )
    manual_evidence = type(evidence)._from_validated(
        **{
            name: getattr(evidence, name)
            for name in evidence.__dataclass_fields__
            if name != "_producer_validated"
        }
    )
    with pytest.raises(ValueError, match="canonical factory"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(manual_evidence,),
        )
    valid_lineage_inputs = build_canonical_lineage_inputs(
        real_rows,
        canonical_runtime_evidence=(evidence,),
    )
    closure_store = FormalEvidenceStore(tmp_path / "real-formal")
    closure = closure_store.load_logical_run_records(
        experiment_id=executed_root.experiment_id,
        condition_id=executed_root.condition_id,
        repeat_id=executed_root.repeat_id,
    )
    mutated_events = {
        **closure,
        "events": [
            {**closure["events"][0], "event_hash": "sha256:" + "f" * 64},
            *closure["events"][1:],
        ],
    }
    monkeypatch.setattr(
        closure_store,
        "load_logical_run_records",
        lambda **_: mutated_events,
    )
    with pytest.raises(ValueError, match="ledger event identity"):
        export_lineage_source_index(
            closure_store,
            valid_lineage_inputs,
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    artifact_store_view = FormalEvidenceStore(tmp_path / "real-formal")
    mutated_artifact = dict(closure["artifacts"][0])
    mutated_artifact["source_artifact_ref"] = {
        **mutated_artifact["source_artifact_ref"],
        "content_hash": "sha256:" + "e" * 64,
    }
    mutated_artifacts = {
        **closure,
        "artifacts": [mutated_artifact, *closure["artifacts"][1:]],
    }
    monkeypatch.setattr(
        artifact_store_view,
        "load_logical_run_records",
        lambda **_: mutated_artifacts,
    )
    with pytest.raises(ValueError, match="artifact identity"):
        export_lineage_source_index(
            artifact_store_view,
            valid_lineage_inputs,
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    forged_lineage = CanonicalLineageInput._from_validated(
        **{
            name: getattr(valid_lineage_inputs[0], name)
            for name in valid_lineage_inputs[0].__dataclass_fields__
            if name != "_producer_validated"
        }
    )
    with pytest.raises(ValueError, match="canonical lineage factory"):
        export_lineage_source_index(
            FormalEvidenceStore(tmp_path / "real-formal"),
            (forged_lineage,),
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    from tests.experiments.test_paper_formal_evidence import _trace_classification_body
    from tokenshare.executors.response_bank import CurrentTraceWrapper
    from tokenshare.executors.trace_backed import TraceSourceBinding
    from tokenshare.experiments.paper_models import PaperEvidenceEligibilityFacts

    trace_facts = PaperEvidenceEligibilityFacts.from_mapping(_trace_classification_body())
    trace_wrapper = CurrentTraceWrapper.from_dict(trace_facts.current_lifecycle_refs[0])
    trace_binding = TraceSourceBinding.from_dict(trace_facts.trace_source_bindings[0])
    with pytest.raises(ValueError, match="crosses requested lineage root"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={"foreign-root": (trace_wrapper,)},
        )
    with pytest.raises(ValueError, match="wrappers do not match"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (
                    replace(trace_wrapper, current_task_id="cross-root-task"),
                )
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (trace_binding,)
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    with pytest.raises(ValueError, match="bindings do not match"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (trace_wrapper,)
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (
                    TraceSourceBinding.create(
                        planned_ai_unit_id="cross-root-unit",
                        sample_slot_index=trace_binding.sample_slot_index,
                        bank_root_id=trace_binding.bank_root_id,
                        manifest_digest=trace_binding.manifest_digest,
                        replacements=trace_binding.replacements,
                        source_evidence_class=trace_binding.source_evidence_class,
                    ),
                )
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    with pytest.raises(ValueError, match="evidence class mismatch"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (trace_wrapper,)
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (trace_binding,)
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    real = publish_paper_formal_metric_drafts(
        tmp_path / "real-formal",
        real_rows,
        registry=registry,
        contract=contract,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(executed_root.preregistered_root_run_id,),
    )
    replay = publish_paper_formal_metric_drafts(
        tmp_path / "real-formal",
        real_rows,
        registry=registry,
        contract=contract,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(executed_root.preregistered_root_run_id,),
    )
    assert len(real.table_drafts) == 8
    assert all(draft.rows for draft in real.table_drafts)
    assert any(
        observation.table_id == "exp3_online_recovery"
        for observation in real.metric_observations
    )
    assert len(real.metric_observations) == sum(
        len(row.cells) for draft in real.table_drafts for row in draft.rows
    )
    assert real.observations_digest == replay.observations_digest
    assert real.metrics_digest == replay.metrics_digest
    assert real.blocked_table_ids
    assert real.lineage_source_index.get(executed_root.preregistered_root_run_id) is not None


def test_retired_aliases_and_old_exp2_exp5_outputs_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    drafts = registry.project_all(canonical_rows)
    original = PaperMetricRegistry.project_all

    def missing_table(self, inputs, *, global_infra_invalid=False):
        return drafts[:-1]

    monkeypatch.setattr(PaperMetricRegistry, "project_all", missing_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "missing",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def extra_table(self, inputs, *, global_infra_invalid=False):
        return (*drafts, drafts[0])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", extra_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "extra",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def wrong_path(self, inputs, *, global_infra_invalid=False):
        return (replace(drafts[0], output_path="metrics/wrong.csv"), *drafts[1:])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", wrong_path)
    with pytest.raises(ValueError, match="output path contract drift"):
        publish_paper_formal_metric_drafts(
            tmp_path / "wrong-path",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", original)

    result = publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        registry=registry,
        contract=contract,
    )

    assert tuple(draft.table_id for draft in result.table_drafts) == tuple(
        table.table_id for table in contract.tables
    )
    published = "\n".join(ref["path"] for ref in result.output_refs)
    for retired in (
        "sensitivity",
        "all_runs",
        "views",
        "model_endpoint_comparison",
        "pairwise",
        "significance",
        "accepted_validity",
    ):
        assert retired not in published
    assert not (tmp_path / "metrics" / "exp2_sensitivity.csv").exists()
    assert not (tmp_path / "metrics" / "paper_table_model_endpoint_comparison.csv").exists()
    assert result.metric_observations == ()
    assert result.lineage_source_index.records == ()
    expected_lineage_paths = {
        "metrics/paper_lineage_source_index.v1.jsonl",
        "metrics/paper_metric_observations.v1.jsonl",
        "metrics/paper_metric_observations_manifest.v1.json",
    }
    assert expected_lineage_paths <= {ref["path"] for ref in result.output_refs}
    manifest = json.loads(
        (tmp_path / "metrics" / "paper_metric_observations_manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["lineage_source_index"]["index_digest"] == (
        result.lineage_source_index.index_digest
    )
    assert manifest["observations_digest"] == result.observations_digest
    assert manifest["provider_calls"] == 0
