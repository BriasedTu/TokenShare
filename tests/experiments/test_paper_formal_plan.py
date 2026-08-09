from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path

import pytest

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequestFactory,
)
from tokenshare.experiments.paper_budget import (
    build_exp5_v3_token_ceiling_mapping,
    load_exp1_pilot_profile,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_exp1 import EXP1_FORMAL_REQUEST_CONTROLS
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_formal_plan import (
    FormalPreparedRequestInventory,
    freeze_formal_root_prepared_requests,
    freeze_paper_formal_prepared_request_inventory,
    freeze_paper_formal_plan_snapshot,
    validate_paper_formal_budget_commitments,
    validate_paper_formal_plan_bindings,
)
import tokenshare.experiments.paper_formal_plan as formal_plan_module
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
    validate_paper_formal_suite_plan,
)
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_model_policy import (
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_runner import (
    build_gate_c_dispatch_plans,
    build_lean_3x3_matrix_plan,
)
from tokenshare.experiments.paper_suite_scale import load_paper_suite_scale_profile


EXPERIMENT_IDS = (
    "exp1_real_ai_feasibility",
    "exp2_real_ai_scalability",
    "exp3_real_ai_fault_recovery",
    "exp4_real_ai_protocol_ablation",
    "exp5_real_ai_model_endpoint_comparison",
)


@pytest.fixture(scope="module")
def formal_inputs(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("formal-plan")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        assert os.environ.get("DEEPSEEK_API_KEY") is None
        assert os.environ.get("SILICONFLOW_API_KEY") is None
        inputs = _build_formal_inputs(tmp_path)
    return inputs


@pytest.fixture(scope="module")
def formal_snapshot(formal_inputs):
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    return freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=output_root,
    )


@pytest.fixture(scope="module")
def single_root_prepared_inventory(
    formal_inputs,
    formal_snapshot,
    tmp_path_factory: pytest.TempPathFactory,
):
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]
    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path_factory.mktemp("single-root-validated"),
    )
    condition = next(
        item
        for item in formal_snapshot.conditions
        if item.condition is root.condition and item.binding is root.binding
    )
    snapshot = replace(
        formal_snapshot,
        conditions=(condition,),
        roots=(root,),
        condition_count=1,
        root_run_count=1,
        first_attempt_ai_unit_count=len(root.planned_ai_unit_ids),
    )
    inventory = FormalPreparedRequestInventory(
        records=records,
        record_count=len(records),
        unique_inference_request_count=len(
            {record.inference_request_digest for record in records}
        ),
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )
    return root, records, snapshot, inventory, ai_api_configs


def test_formal_snapshot_freezes_every_current_full_plan_root_without_provider_calls(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    snapshot = freeze_paper_formal_plan_snapshot(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        ai_api_configs=ai_api_configs,
        output_root=output_root,
    )

    assert snapshot.condition_count == 324
    assert snapshot.root_run_count == 6_384
    assert snapshot.first_attempt_ai_unit_count == 40_520
    assert snapshot.provider_calls_made == 0
    assert snapshot.snapshot_digest.startswith("sha256:")
    snapshot_body = snapshot.to_dict()
    persisted_digest = snapshot_body.pop("snapshot_digest")
    assert persisted_digest == digest_json(snapshot_body)
    assert len(snapshot.conditions) == snapshot.condition_count
    assert len(snapshot.roots) == snapshot.root_run_count
    assert snapshot.conditions[0].condition is plans[0].conditions[0]
    assert (
        snapshot.conditions[0].binding
        is plans[0].condition_selection_bindings[0]
    )
    assert all(root.planned_ai_unit_ids for root in snapshot.roots)
    assert all(root.split_profile_digest.startswith("sha256:") for root in snapshot.roots)
    assert all(root.plugin_id in {"factorization", "lean_proof"} for root in snapshot.roots)
    assert all(root.seed == root.condition.seed for root in snapshot.roots)
    assert all(root.repeat_id == root.condition.repeat_id for root in snapshot.roots)
    assert all(root.condition_digest == root.condition.condition_digest for root in snapshot.roots)
    assert all(root.selection_digest == root.binding.selection.selection_digest for root in snapshot.roots)
    exp5_root = next(
        root
        for root in snapshot.roots
        if root.condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    assert exp5_root.endpoint_controls.provider_config_id == "siliconflow"
    assert exp5_root.endpoint_controls.model_entry_id == exp5_root.condition.model_entry_id
    assert exp5_root.endpoint_controls.provider_model_id == exp5_root.condition.provider_model_id
    assert exp5_root.endpoint_controls.max_provider_attempts == 1
    assert exp5_root.endpoint_controls.max_tokens == 32_768
    assert exp5_root.endpoint_controls.timeout_seconds == 600


def test_formal_root_preparation_uses_runtime_adapter_and_freezes_exact_request(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    root = formal_snapshot.roots[0]

    records = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "single-root-plan",
    )

    assert tuple(record.planned_ai_unit_id for record in records) == (
        root.planned_ai_unit_ids
    )
    assert all(record.condition is root.condition for record in records)
    assert all(record.binding is root.binding for record in records)
    assert all(record.sample_slot_index == root.repeat_id for record in records)
    assert all(record.base_replacement_slot == 0 for record in records)
    assert all(record.replacement_slot_ids == (0,) for record in records)
    assert all(
        record.replacement_policy_id == "formal_attempt_budget.v1"
        for record in records
    )
    assert all(record.prepared_request.body_bytes for record in records)
    assert all(record.body_digest == record.prepared_request.body_digest for record in records)
    assert all(
        record.inference_request_digest
        == record.prepared_request.inference_request_digest
        for record in records
    )
    assert all(
        record.source_provider_config_digest
        == root.endpoint_controls.source_provider_config_digest
        for record in records
    )
    assert all(
        record.prepared_request.provider_config_digest
        == record.prepared_execution_config_digest
        for record in records
    )
    assert all(
        record.prepared_request.entry_id == root.endpoint_controls.model_entry_id
        for record in records
    )
    assert all(
        record.request_max_tokens == root.endpoint_controls.max_tokens
        for record in records
    )
    assert all(record.request_timeout_seconds == 600 for record in records)
    assert all(record.provider_calls_made == 0 for record in records)
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


def test_formal_root_preparation_is_deterministic_across_planning_roots(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]

    first = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "deterministic-a",
    )
    second = freeze_formal_root_prepared_requests(
        root=root,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "deterministic-b",
    )

    assert tuple(item.request_identity_digest for item in first) == tuple(
        item.request_identity_digest for item in second
    )
    assert tuple(item.prepared_request for item in first) == tuple(
        item.prepared_request for item in second
    )


def test_formal_root_preparation_rejects_snapshot_endpoint_control_tamper(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    root = formal_snapshot.roots[0]
    drifted = replace(
        root,
        endpoint_controls=replace(
            root.endpoint_controls,
            source_provider_config_digest="sha256:" + "f" * 64,
        ),
    )

    with pytest.raises(ValueError, match="formal root endpoint controls drift"):
        freeze_formal_root_prepared_requests(
            root=drifted,
            catalog_manifest=catalog,
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "tampered-endpoint",
        )


def test_formal_prepared_record_digest_revalidates_nested_request_body(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    record = freeze_formal_root_prepared_requests(
        root=formal_snapshot.roots[0],
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "nested-request-tamper",
    )[0]
    drifted_body = dict(record.prepared_request.body_obj)
    drifted_body["model"] = "drifted-model"
    drifted = replace(
        record,
        prepared_request=replace(
            record.prepared_request,
            body_obj=drifted_body,
        ),
    )

    with pytest.raises(ValueError, match="prepared request body bytes mismatch"):
        _ = drifted.request_identity_digest


def test_formal_prepared_record_validator_rejects_top_level_provider_model_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )

    with pytest.raises(ValueError, match="provider_model_id"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=replace(records[0], provider_model_id="drifted-model"),
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-provider-model",
        )


def test_formal_prepared_record_validator_rejects_in_place_identity_mapping_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    drifted_identity = dict(records[0].provider_request_identity)
    drifted = replace(records[0], provider_request_identity=drifted_identity)
    drifted_identity["configured_model"] = "drifted-model"

    with pytest.raises(ValueError, match="provider request identity"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=drifted,
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-provider-identity",
        )


def test_formal_prepared_record_validator_rejects_coherently_resigned_body_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
) -> None:
    root, records, _snapshot, _inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    record = records[0]
    original = record.prepared_request
    drifted_body = deepcopy(original.body_obj)
    drifted_body["messages"][0]["content"] += "\ncoherent-drift"
    resigned = PreparedOutboundRequestFactory.prepare(
        body_obj=drifted_body,
        base_url=original.normalized_absolute_endpoint,
        endpoint="",
        provider_config_digest=original.provider_config_digest,
        entry_id=original.entry_id,
        configured_model=original.configured_model,
        effective_controls_digest=original.effective_controls_digest,
        plugin_id=original.plugin_id,
        plugin_version=original.plugin_version,
        prompt_profile_id=original.prompt_profile_id,
        prompt_serialization_schema=original.prompt_serialization_schema,
        body_serialization_schema=original.body_serialization_schema,
        case_id=original.case_id,
        planned_ai_unit_id=original.planned_ai_unit_id,
        sample_slot_index=original.sample_slot_index,
        replacement_slot=original.replacement_slot,
    )
    drifted = replace(
        record,
        prepared_request=resigned,
        prompt_profile_digest=digest_json(
            {
                "body_digest": resigned.body_digest,
                "prompt_profile_id": resigned.prompt_profile_id,
                "prompt_serialization_schema": resigned.prompt_serialization_schema,
            }
        ),
    )

    with pytest.raises(ValueError, match="independent runtime template"):
        formal_plan_module.validate_formal_prepared_request_record(
            record=drifted,
            root=root,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / "audit-coherent-body",
        )


@pytest.mark.parametrize("mutation", ("count", "record", "source_digest"))
def test_formal_prepared_inventory_validator_rejects_internal_tamper(
    single_root_prepared_inventory,
    formal_inputs,
    tmp_path: Path,
    mutation: str,
) -> None:
    _root, records, snapshot, inventory, ai_api_configs = (
        single_root_prepared_inventory
    )
    if mutation == "count":
        drifted = replace(inventory, record_count=inventory.record_count + 1)
    elif mutation == "record":
        drifted = replace(
            inventory,
            records=(
                replace(records[0], provider_family="drifted-provider"),
                *records[1:],
            ),
        )
    else:
        drifted = replace(
            inventory,
            source_snapshot_digest="sha256:" + "f" * 64,
        )

    with pytest.raises(ValueError, match="formal prepared inventory"):
        formal_plan_module.validate_formal_prepared_request_inventory(
            inventory=drifted,
            snapshot=snapshot,
            catalog_manifest=formal_inputs[1],
            ai_api_configs=ai_api_configs,
            planning_artifact_root=tmp_path / f"audit-inventory-{mutation}",
        )


def test_full_prepared_inventory_covers_every_first_attempt_without_provider_calls(
    formal_inputs,
    formal_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plans, catalog, _budget, ai_api_configs, _output_root = formal_inputs
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    inventory = freeze_paper_formal_prepared_request_inventory(
        snapshot=formal_snapshot,
        catalog_manifest=catalog,
        ai_api_configs=ai_api_configs,
        planning_artifact_root=tmp_path / "full-inventory-plan",
    )

    assert inventory.record_count == formal_snapshot.first_attempt_ai_unit_count == 40_520
    assert len(inventory.records) == inventory.record_count
    assert inventory.provider_calls_made == 0
    assert inventory.inventory_digest.startswith("sha256:")
    assert inventory.unique_inference_request_count <= inventory.record_count
    exp5_records = tuple(
        record
        for record in inventory.records
        if record.condition.experiment_id
        == "exp5_real_ai_model_endpoint_comparison"
    )
    assert len(exp5_records) == 4_992
    assert {
        record.prepared_request.configured_model for record in exp5_records
    } == {
        "zai-org/GLM-5.2",
        "Qwen/Qwen3-14B",
        "MiniMaxAI/MiniMax-M2.5",
        "Pro/deepseek-ai/DeepSeek-V3",
    }
    exp3_worker_death = next(
        record
        for record in inventory.records
        if record.condition.experiment_id == "exp3_real_ai_fault_recovery"
        and record.condition.fault_type == "worker_death"
    )
    assert exp3_worker_death.replacement_slot_ids == (0, 1, 2, 3, 4)
    assert exp3_worker_death.replacement_policy_id == "formal_attempt_budget.v1"
    assert "smoke" not in exp3_worker_death.replacement_policy_id
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


def test_formal_suite_plan_public_validator_returns_no_internal_bound_plan(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs

    assert validate_paper_formal_suite_plan(
        dispatch_plans=plans,
        catalog_manifest=catalog,
        budget=budget,
        output_root=output_root,
        ai_api_configs=ai_api_configs,
        hard_limits={
            "max_total_provider_attempts": budget.max_provider_attempts,
            "max_total_tokens": budget.token_upper_bound,
            "max_cost_estimate": budget.cost_upper_bound,
        },
    ) is None


@pytest.mark.parametrize(
    ("experiment_index", "omitted_axis"),
    (
        (0, "first_condition"),
        (1, "worker_slice"),
        (4, "endpoint_condition_slice"),
    ),
)
def test_formal_snapshot_rejects_self_consistent_partial_formal_plan(
    formal_inputs,
    experiment_index: int,
    omitted_axis: str,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    target = plans[experiment_index]
    if omitted_axis == "first_condition":
        omitted_ids = {target.conditions[0].condition_id}
    elif omitted_axis == "worker_slice":
        omitted_worker_count = target.conditions[0].worker_count
        omitted_ids = {
            condition.condition_id
            for condition in target.conditions
            if condition.worker_count == omitted_worker_count
        }
    else:
        omitted_ids = {target.conditions[0].condition_id}
    partial_target = replace(
        target,
        conditions=tuple(
            condition
            for condition in target.conditions
            if condition.condition_id not in omitted_ids
        ),
        condition_selection_bindings=tuple(
            binding
            for binding in target.condition_selection_bindings
            if binding.condition_id not in omitted_ids
        ),
    )
    partial_plans = (
        *plans[:experiment_index],
        partial_target,
        *plans[experiment_index + 1 :],
    )
    partial_budget = _replan_budget(
        plans=partial_plans,
        catalog=catalog,
        source_budget=budget,
    )

    with pytest.raises(ValueError, match="canonical formal plan mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=partial_plans,
            catalog_manifest=catalog,
            budget=partial_budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_budget_planner_rejects_missing_exp5_endpoint_member(
    formal_inputs,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    exp5 = plans[4]
    omitted_member_id = exp5.conditions[0].cohort_member_id
    omitted_ids = {
        condition.condition_id
        for condition in exp5.conditions
        if condition.cohort_member_id == omitted_member_id
    }
    partial_exp5 = replace(
        exp5,
        conditions=tuple(
            condition
            for condition in exp5.conditions
            if condition.condition_id not in omitted_ids
        ),
        condition_selection_bindings=tuple(
            binding
            for binding in exp5.condition_selection_bindings
            if binding.condition_id not in omitted_ids
        ),
    )

    with pytest.raises(ValueError, match="exact four-member cohort"):
        _replan_budget(
            plans=(*plans[:4], partial_exp5),
            catalog=catalog,
            source_budget=budget,
        )


@pytest.mark.parametrize("mutation", ("missing", "reordered", "duplicate"))
def test_formal_snapshot_rejects_noncanonical_experiment_collection(
    formal_inputs,
    mutation: str,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    if mutation == "missing":
        drifted = plans[:-1]
    elif mutation == "reordered":
        drifted = (plans[1], plans[0], *plans[2:])
    else:
        drifted = (*plans, plans[-1])

    with pytest.raises(ValueError, match="canonical formal experiment order mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=drifted,
            catalog_manifest=catalog,
            budget=budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_snapshot_rejects_exp4_identity_derived_from_drifted_conditions(
    formal_inputs,
) -> None:
    plans, catalog, budget, ai_api_configs, output_root = formal_inputs
    exp4 = plans[3]
    drifted_conditions = tuple(
        replace(
            condition,
            reasoning_profile_id="drifted_profile",
            model_cohort_id="tokenshare.paper.drifted_baseline.v1",
            model_cohort_digest="sha256:" + "e" * 64,
            cohort_member_id="drifted_baseline_member",
            model_endpoint_identity_digest="sha256:" + "d" * 64,
        )
        for condition in exp4.conditions
    )
    selections_by_id = {
        binding.condition_id: binding.selection
        for binding in exp4.condition_selection_bindings
    }
    drifted_exp4 = replace(
        exp4,
        conditions=drifted_conditions,
        condition_selection_bindings=tuple(
            FrozenConditionSelectionBinding.from_condition(
                condition,
                selections_by_id[condition.condition_id],
            )
            for condition in drifted_conditions
        ),
    )
    drifted_plans = (*plans[:3], drifted_exp4, plans[4])
    drifted_budget = _replan_budget(
        plans=drifted_plans,
        catalog=catalog,
        source_budget=budget,
    )

    with pytest.raises(ValueError, match="canonical formal plan mismatch"):
        freeze_paper_formal_plan_snapshot(
            dispatch_plans=drifted_plans,
            catalog_manifest=catalog,
            budget=drifted_budget,
            ai_api_configs=ai_api_configs,
            output_root=output_root,
        )


def test_formal_binding_validator_rejects_condition_digest_mismatch(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = plan.conditions[0]
    binding = plan.condition_selection_bindings[0]
    drifted = FrozenConditionSelectionBinding(
        condition_id=binding.condition_id,
        condition_digest="sha256:" + "f" * 64,
        selection=binding.selection,
    )

    with pytest.raises(ValueError, match="condition digest mismatch"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(drifted,),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_selection_case_absent_from_catalog(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = plan.conditions[0]
    binding = plan.condition_selection_bindings[0]
    drifted_selection = replace(
        binding.selection,
        ordered_case_ids=("missing_formal_case",),
        expected_ai_unit_count=1,
    )
    drifted = FrozenConditionSelectionBinding.from_condition(
        condition,
        drifted_selection,
    )

    with pytest.raises(ValueError, match="absent from formal catalog"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(drifted,),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_duplicate_condition_binding(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]

    with pytest.raises(ValueError, match="duplicate condition binding"):
        validate_paper_formal_plan_bindings(
            conditions=(plan.conditions[0],),
            bindings=(
                plan.condition_selection_bindings[0],
                plan.condition_selection_bindings[0],
            ),
            catalog_manifest=formal_inputs[1],
        )


def test_formal_binding_validator_rejects_unknown_lean_catalog_schema(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = next(item for item in plan.conditions if item.domain == "lean_proof")
    binding = next(
        item
        for item in plan.condition_selection_bindings
        if item.condition_id == condition.condition_id
    )
    catalog = _catalog_with_case_changes(
        formal_inputs[1],
        binding.selection.ordered_case_ids[0],
        {"schema_version": "tokenshare.paper_unknown_case.v1"},
    )

    with pytest.raises(ValueError, match="unsupported formal catalog case schema"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(binding,),
            catalog_manifest=catalog,
        )


def test_formal_binding_validator_rejects_wrong_case_difficulty(
    formal_inputs,
) -> None:
    plan = formal_inputs[0][0]
    condition = next(
        item
        for item in plan.conditions
        if item.domain == "factorization" and item.paper_difficulty == "easy"
    )
    binding = next(
        item
        for item in plan.condition_selection_bindings
        if item.condition_id == condition.condition_id
    )
    catalog = _catalog_with_case_changes(
        formal_inputs[1],
        binding.selection.ordered_case_ids[0],
        {"difficulty": "hard", "paper_difficulty": "hard"},
    )

    with pytest.raises(ValueError, match="difficulty mismatch"):
        validate_paper_formal_plan_bindings(
            conditions=(condition,),
            bindings=(binding,),
            catalog_manifest=catalog,
        )


def test_formal_budget_commitments_reject_frozen_selection_order_tamper(
    formal_inputs,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    quota = deepcopy(budget.quota_preflight)
    frozen = quota["budget_commitments"]["frozen_selections"][0]
    frozen["ordered_case_ids"] = list(reversed(frozen["ordered_case_ids"]))

    with pytest.raises(ValueError, match="frozen selection mismatch"):
        validate_paper_formal_budget_commitments(
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=replace(budget, quota_preflight=quota),
        )


@pytest.mark.parametrize(
    "tamper",
    ("planned_ai_unit_ids", "split_profile_digest", "commitment_digest"),
)
def test_formal_budget_commitments_reject_ai_unit_commitment_tamper(
    formal_inputs,
    tamper: str,
) -> None:
    plans, catalog, budget, _configs, _root = formal_inputs
    quota = deepcopy(budget.quota_preflight)
    commitments = quota["budget_commitments"]["ai_unit_commitments"]
    target = next(item for item in commitments if len(item["planned_ai_unit_ids"]) > 1)
    if tamper == "planned_ai_unit_ids":
        target[tamper] = list(reversed(target[tamper]))
    else:
        target[tamper] = "sha256:" + "f" * 64

    with pytest.raises(ValueError, match="AI-unit commitment"):
        validate_paper_formal_budget_commitments(
            dispatch_plans=plans,
            catalog_manifest=catalog,
            budget=replace(budget, quota_preflight=quota),
        )


def _build_formal_inputs(tmp_path: Path):
    catalog = load_paper_catalogs(
        factorization_path="benchmarks/paper/factorization_catalog.v2.jsonl",
        lean_path="benchmarks/paper/lean_catalog.v1.jsonl",
        lean_lemma_graph_path="benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    )
    baseline_profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    baseline_identity = baseline_profile.model_endpoint_identity.to_dict()
    baseline_binding = {
        **baseline_identity,
        "model_entry_id": baseline_identity["selected_entry_id"],
        "request_controls": dict(EXP1_FORMAL_REQUEST_CONTROLS),
    }
    exp5_configs = load_provider_config_map(
        {"siliconflow": "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"}
    )
    exp5_preflight = build_model_endpoint_cohort_preflight(
        cohort=load_model_endpoint_cohort(
            "benchmarks/paper/model_comparison_cohort.v3.json"
        ),
        entry_map=load_model_entry_map(
            "benchmarks/paper/model_comparison_entry_map.v3.json"
        ),
        provider_configs=exp5_configs,
        require_smoke_evidence=False,
        smoke_evidence_bundle=None,
    )
    member_plans = exp5_preflight["member_plans"]
    expected_secret_reasons = {"missing_api_key_env"}
    assert exp5_preflight["ineligible_members"]
    assert all(
        set(item["blocked_reasons"]) == expected_secret_reasons
        for item in exp5_preflight["ineligible_members"]
    )
    for member_plan in member_plans.values():
        assert set(member_plan["blocked_reasons"]) == expected_secret_reasons
        member_plan["api_key_env"] = "SILICONFLOW_API_KEY"
        member_plan["status"] = "planned"
        member_plan["blocked_reasons"] = []
    exp5_preflight.update(
        {
            "status": "planned",
            "paper_eligible_possible": True,
            "blocked_reason": None,
            "ineligibility_reasons": [],
            "ineligible_members": [],
        }
    )
    readiness = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    plans = build_gate_c_dispatch_plans(
        catalog_manifest=catalog,
        lean_3x3_matrix=readiness,
        experiment_ids=EXPERIMENT_IDS,
        baseline_endpoint_binding=baseline_binding,
        model_endpoint_cohort_preflight=exp5_preflight,
        paper_suite_scale_profile=load_paper_suite_scale_profile(
            "benchmarks/paper/paper_suite_scale_profile.v1.json"
        ),
        output_root=tmp_path,
    )
    conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    frozen_selections = tuple(
        {
            **binding.selection.to_dict(),
            "condition_id": binding.condition_id,
            "condition_digest": binding.condition_digest,
        }
        for plan in plans
        for binding in plan.condition_selection_bindings
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=304_096,
        token_upper_bound_by_endpoint_identity_digest=(
            build_exp5_v3_token_ceiling_mapping(exp5_preflight)
        ),
        cost_upper_bound_per_provider_attempt=0.05,
        plan_only=True,
        lean_3x3_matrix=readiness,
        model_endpoint_cohort_preflight=exp5_preflight,
        frozen_selections=frozen_selections,
        endpoint_identity={
            "baseline": baseline_binding,
            "model_endpoint_cohort_preflight": exp5_preflight,
        },
        request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
    )
    ai_api_configs = {
        baseline_identity["provider_config_id"]: baseline_profile.source_provider_config,
        **exp5_configs,
        APPROVED_ENDPOINT_BINDINGS_KEY: {
            "exp5_real_ai_model_endpoint_comparison": exp5_preflight
        },
    }

    return plans, catalog, budget, ai_api_configs, tmp_path


def _catalog_with_case_changes(catalog, case_id: str, changes: dict):
    fields = (
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
    )
    updates = {}
    found = False
    for field_name in fields:
        values = getattr(catalog, field_name)
        changed = []
        for case in values:
            if case["case_id"] == case_id:
                changed.append({**case, **changes})
                found = True
            else:
                changed.append(case)
        updates[field_name] = tuple(changed)
    assert found
    return replace(catalog, **updates)


def _replan_budget(*, plans, catalog, source_budget):
    commitments = source_budget.quota_preflight["budget_commitments"]
    endpoint_identity = commitments["endpoint_identity"]
    exp5_preflight = endpoint_identity["model_endpoint_cohort_preflight"]
    conditions = tuple(
        condition
        for plan in plans
        for condition in plan.conditions
    )
    frozen_selections = tuple(
        {
            **binding.selection.to_dict(),
            "condition_id": binding.condition_id,
            "condition_digest": binding.condition_digest,
        }
        for plan in plans
        for binding in plan.condition_selection_bindings
    )
    return plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=304_096,
        token_upper_bound_by_endpoint_identity_digest=(
            build_exp5_v3_token_ceiling_mapping(exp5_preflight)
        ),
        cost_upper_bound_per_provider_attempt=0.05,
        plan_only=True,
        lean_3x3_matrix=build_lean_3x3_matrix_plan(catalog_manifest=catalog),
        model_endpoint_cohort_preflight=exp5_preflight,
        frozen_selections=frozen_selections,
        endpoint_identity=endpoint_identity,
        request_limits=dict(commitments["request_limits"]),
    )
