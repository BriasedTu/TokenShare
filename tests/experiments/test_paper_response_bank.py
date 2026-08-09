from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequestFactory,
)
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    canonical_digest,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
    terminal_bank_entry_id,
)
from tokenshare.experiments.paper_budget import (
    L3_SMALL_PAID_BUDGET_LIMITS,
    build_response_bank_budget_report,
)
from tokenshare.experiments.paper_models import PaperBudgetResult, PaperStatus, digest_json
from tokenshare.experiments.paper_response_bank import (
    AcquisitionRequest,
    FullAcquisitionBudget,
    SemanticSlotCandidate,
    build_matrix8_unified_acquisition_plan,
    build_semantic_inventory,
    create_acquisition_plan_bundle,
    load_acquisition_plan_bundle,
    preflight_inventory_before_coordinator,
    regression_smoke_sparse_replacement_slots,
    replacement_slots_for,
)
from tokenshare.experiments.paper_resource_accounting import FrozenPricing
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_runner import (
    build_gate_c_dispatch_plans,
    build_lean_3x3_matrix_plan,
)
from tokenshare.experiments.paper_smoke import (
    load_paper_smoke_profile,
    resolve_paper_smoke_execution_plan,
)
from tokenshare.experiments.paper_suite_scale import load_paper_suite_scale_profile
from tokenshare.experiments import run_paper_experiments as paper_cli


def _prepared(
    *,
    case_id: str = "case-a",
    unit_id: str = "unit-0",
    sample: int = 0,
    replacement_slot: int = 0,
    body_marker: str = "same",
    provider_digest: str = "sha256:" + "1" * 64,
    plugin_version: str = "factorization.v1",
):
    return PreparedOutboundRequestFactory.prepare(
        body_obj={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": body_marker}],
            "max_tokens": 300000,
        },
        base_url="https://api.deepseek.com",
        endpoint="/chat/completions",
        provider_config_digest=provider_digest,
        entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        effective_controls_digest="sha256:" + "2" * 64,
        plugin_id="factorization",
        plugin_version=plugin_version,
        prompt_profile_id="factorization.range_search.v2",
        prompt_serialization_schema="tokenshare.prompt.v2",
        body_serialization_schema="openai_chat_completions.v1",
        case_id=case_id,
        planned_ai_unit_id=unit_id,
        sample_slot_index=sample,
        replacement_slot=replacement_slot,
    )


def _candidate(
    *,
    condition_id: str = "condition-a",
    worker_count: int = 1,
    experiment_id: str = "exp2_real_ai_scalability",
    fault_type: str = "none",
    ablation_mode: str = "FULL",
    case_digest: str = "sha256:" + "3" * 64,
    unit_id: str = "unit-0",
    sample: int = 0,
    repeat_id: int | None = None,
    replacement_slot: int = 0,
    body_marker: str = "same",
    prompt_digest: str = "sha256:" + "4" * 64,
    provider_digest: str = "sha256:" + "1" * 64,
    plugin_version: str = "factorization.v1",
    terminal_kind: str | None = None,
    replacement_policy_id: str = "formal_complete",
    fault_rate: float = 0.0,
    dead_worker_count: int | None = None,
    kill_progress_percent: int | None = None,
) -> SemanticSlotCandidate:
    return SemanticSlotCandidate(
        experiment_id=experiment_id,
        condition_id=condition_id,
        condition_digest=digest_json({"condition_id": condition_id}),
        worker_count=worker_count,
        repeat_id=sample if repeat_id is None else repeat_id,
        fault_type=fault_type,
        ablation_mode=ablation_mode,
        case_id="case-a",
        case_record_digest=case_digest,
        planned_ai_unit_id=unit_id,
        sample_slot_index=sample,
        replacement_slot=replacement_slot,
        prompt_profile_digest=prompt_digest,
        prepared_request=_prepared(
            unit_id=unit_id,
            sample=sample,
            replacement_slot=replacement_slot,
            body_marker=body_marker,
            provider_digest=provider_digest,
            plugin_version=plugin_version,
        ),
        terminal_kind=terminal_kind,
        replacement_policy_id=replacement_policy_id,
        fault_rate=fault_rate,
        dead_worker_count=dead_worker_count,
        kill_progress_percent=kill_progress_percent,
    )


def _exp2_online_conditions() -> tuple[dict, ...]:
    cases = (
        ("early", "factor_v2_hard_034", 0),
        ("middle", "factor_v2_hard_122", 9),
        ("late", "factor_v2_hard_063", 1),
        ("no_factor", "factor_v2_hard_161", 44),
    )
    result = []
    for worker in (1, 3, 7, 10, 30, 50):
        for position, case_id, selection_index in cases:
            body = {
                "condition_id": f"epd027_exp2_online_w{worker}_{position}_r0",
                "experiment_id": "exp2_online_concurrency_check",
                "evidence_class": "online_real_provider",
                "worker_count": worker,
                "case_position": position,
                "case_id": case_id,
                "paper_difficulty": "hard",
                "active_selection_index": selection_index,
                "repeat_id": 0,
                "split_profile_id": "factorization.exp2_contiguous_20way.v1",
                "planned_first_ai_units": 20,
                "provider_calls_upper": 20,
                "max_concurrent_roots": 1,
            }
            result.append({**body, "condition_digest": digest_json(body)})
    return tuple(result)


def test_every_inventory_row_has_unique_inventory_entry_id_and_semantic_slot_key() -> None:
    plan = build_semantic_inventory(
        (_candidate(), _candidate(unit_id="unit-1", body_marker="unit-1"))
    )
    assert len({row.inventory_entry_id for row in plan.rows}) == len(plan.rows)
    assert len({row.semantic_slot_key for row in plan.rows}) == len(plan.rows)


def test_planner_separates_provider_entry_from_unique_terminal_bank_entry() -> None:
    candidates = (
        _candidate(unit_id="unit-0", body_marker="unit-0"),
        _candidate(unit_id="unit-1", body_marker="unit-1"),
    )
    plan = build_semantic_inventory(candidates)

    assert {candidate.prepared_request.entry_id for candidate in candidates} == {
        "deepseek_v4_pro_exp1_baseline"
    }
    assert len({row.entry_id for row in plan.rows}) == 2
    assert all(
        row.entry_id
        == terminal_bank_entry_id(
            semantic_slot_key=row.semantic_slot_key,
            inference_request_digest=row.inference_request_digest,
        )
        for row in plan.rows
    )
    assert all(row.entry_id != candidates[0].prepared_request.entry_id for row in plan.rows)
    assert plan.rows == tuple(
        sorted(plan.rows, key=lambda row: row.inventory_entry_id)
    )
    assert plan.inventory_digest == response_bank_inventory_digest(plan.rows)


def test_planner_computes_inventory_entry_id_from_canonical_row_excluding_id() -> None:
    row = build_semantic_inventory((_candidate(),)).rows[0]
    assert row.inventory_entry_id == inventory_entry_id(row)
    body = row.to_dict()
    body["inventory_entry_id"] = "ignored-by-preimage"
    assert inventory_entry_id(body) == row.inventory_entry_id


def test_create_only_bundle_round_trips_exact_prepared_bytes_and_full_budget(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(
            experiment_id="exp4_real_ai_protocol_ablation",
            unit_id="unit-0",
            replacement_slot=0,
            body_marker="initial",
        ),
        _candidate(
            experiment_id="exp4_real_ai_protocol_ablation",
            unit_id="unit-0",
            replacement_slot=1,
            body_marker="replacement",
        ),
    )
    plan = build_semantic_inventory(candidates)
    candidates_by_digest = {
        item.prepared_request.inference_request_digest: item for item in candidates
    }
    requests = tuple(
        AcquisitionRequest(
            inventory_row=row,
            prepared_request=candidates_by_digest[
                row.inference_request_digest
            ].prepared_request,
            provider_family="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_seconds=600,
            token_upper_bound=300_000,
            cost_upper_bound=Decimal("1.25"),
            frozen_pricing=FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("0.5"),
                output_per_million_tokens=Decimal("1.5"),
            ),
            requested_at="2026-08-03T00:00:00Z",
        )
        for row in reversed(plan.rows)
    )
    root = tmp_path / "bundle"

    created = create_acquisition_plan_bundle(
        root,
        authorized_plan_digest="sha256:" + "a" * 64,
        profile_digest="sha256:" + "b" * 64,
        semantic_inventory_plan=plan,
        acquisition_requests=requests,
    )
    reopened = load_acquisition_plan_bundle(root)

    assert reopened == created
    assert reopened.semantic_inventory_plan == plan
    assert reopened.inventory_rows == plan.rows
    assert tuple(
        item.prepared_request.body_bytes for item in reopened.acquisition_requests
    ) == tuple(
        candidates_by_digest[row.inference_request_digest].prepared_request.body_bytes
        for row in plan.rows
    )
    assert reopened.full_budget.calls == 2
    assert reopened.full_budget.tokens == 600_000
    assert reopened.full_budget.cny == Decimal("2.50")
    assert reopened.full_budget.budget_digest != "sha256:" + "0" * 64
    with pytest.raises(FileExistsError):
        create_acquisition_plan_bundle(
            root,
            authorized_plan_digest="sha256:" + "a" * 64,
            profile_digest="sha256:" + "b" * 64,
            semantic_inventory_plan=plan,
            acquisition_requests=reopened.acquisition_requests,
        )
    path = root / "acquisition_plan_bundle.v1.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["acquisition_requests"][0]["prepared_request"][
        "body_bytes_base64"
    ] = "dGFtcGVyZWQ="
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="prepared request body bytes mismatch"):
        load_acquisition_plan_bundle(root)


def test_acquisition_bundle_rejects_semantic_plan_row_cross_binding_tamper(
    tmp_path: Path,
) -> None:
    candidates = (
        _candidate(
            experiment_id="exp4_real_ai_protocol_ablation",
            unit_id="unit-0",
            replacement_slot=0,
            body_marker="initial",
        ),
        _candidate(
            experiment_id="exp4_real_ai_protocol_ablation",
            unit_id="unit-0",
            replacement_slot=1,
            body_marker="replacement",
        ),
    )
    plan = build_semantic_inventory(candidates)
    by_digest = {
        item.prepared_request.inference_request_digest: item for item in candidates
    }
    requests = tuple(
        AcquisitionRequest(
            inventory_row=row,
            prepared_request=by_digest[row.inference_request_digest].prepared_request,
            provider_family="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_seconds=600,
            token_upper_bound=300_000,
            cost_upper_bound=Decimal("1.25"),
            frozen_pricing=FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("0.5"),
                output_per_million_tokens=Decimal("1.5"),
            ),
            requested_at="2026-08-03T00:00:00Z",
        )
        for row in plan.rows
    )
    root = tmp_path / "bundle"
    create_acquisition_plan_bundle(
        root,
        authorized_plan_digest="sha256:" + "a" * 64,
        profile_digest="sha256:" + "b" * 64,
        semantic_inventory_plan=plan,
        acquisition_requests=requests,
    )
    path = root / "acquisition_plan_bundle.v1.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["semantic_inventory_plan"]["condition_refs"][0][
        "semantic_slot_keys"
    ].pop()
    body["bundle_digest"] = canonical_digest(
        {key: value for key, value in body.items() if key != "bundle_digest"}
    )
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="do not cover"):
        load_acquisition_plan_bundle(root)


def test_full_acquisition_budget_rejects_l3_516_budget_identity() -> None:
    limits = L3_SMALL_PAID_BUDGET_LIMITS
    preimage = {
        "schema_version": "tokenshare.paper_full_acquisition_budget.v1",
        "calls": limits.calls,
        "tokens": limits.tokens,
        "cny": str(limits.cny),
        "deepseek_cumulative_cny": str(limits.deepseek_cumulative_cny),
    }
    budget = FullAcquisitionBudget(
        schema_version=str(preimage["schema_version"]),
        calls=limits.calls,
        tokens=limits.tokens,
        cny=limits.cny,
        deepseek_cumulative_cny=limits.deepseek_cumulative_cny,
        budget_digest=canonical_digest(preimage),
    )
    with pytest.raises(ValueError, match="L3 516-call budget"):
        budget.validate()


def test_planner_is_stable_for_identical_rows_and_rejects_tampered_precomputed_id() -> None:
    candidate = _candidate()
    first = build_semantic_inventory((candidate,))
    second = build_semantic_inventory((candidate,))
    assert first.rows == second.rows
    with pytest.raises(ValueError, match="precomputed inventory_entry_id"):
        build_semantic_inventory(
            (replace(candidate, precomputed_inventory_entry_id="sha256:" + "f" * 64),)
        )


def test_semantic_slot_key_binds_all_frozen_slot_axes_and_admission_digest() -> None:
    base = _candidate()
    base_key = build_semantic_inventory((base,)).rows[0].semantic_slot_key
    variants = (
        _candidate(case_digest="sha256:" + "5" * 64),
        _candidate(unit_id="unit-1", body_marker="unit-1"),
        _candidate(sample=1),
        _candidate(provider_digest="sha256:" + "6" * 64),
        _candidate(prompt_digest="sha256:" + "7" * 64),
        _candidate(plugin_version="factorization.v2"),
    )
    assert all(
        build_semantic_inventory((item,)).rows[0].semantic_slot_key != base_key
        for item in variants
    )
    replacement_variant = semantic_slot_key(
        case_record_digest=base.case_record_digest,
        planned_ai_unit_id=base.planned_ai_unit_id,
        sample_slot_index=base.sample_slot_index,
        replacement_slot=1,
        provider_config_digest=base.prepared_request.provider_config_digest,
        prompt_profile_digest=base.prompt_profile_digest,
        prompt_admission_profile_digest=(
            base.prepared_request.prompt_admission_profile_digest
        ),
        plugin_version=base.prepared_request.plugin_version,
    )
    assert replacement_variant != base_key
    admission_variant = semantic_slot_key(
        case_record_digest=base.case_record_digest,
        planned_ai_unit_id=base.planned_ai_unit_id,
        sample_slot_index=base.sample_slot_index,
        replacement_slot=base.replacement_slot,
        provider_config_digest=base.prepared_request.provider_config_digest,
        prompt_profile_digest=base.prompt_profile_digest,
        prompt_admission_profile_digest="sha256:" + "8" * 64,
        plugin_version=base.prepared_request.plugin_version,
    )
    assert admission_variant != base_key
    condition_only = replace(base, condition_id="condition-b", worker_count=50)
    assert (
        build_semantic_inventory((condition_only,)).rows[0].semantic_slot_key
        == base_key
    )


def test_exp2_conditions_share_case_repeat_unit_slot_and_repeats_do_not() -> None:
    plan = build_semantic_inventory(
        (
            _candidate(condition_id="w1-r0", worker_count=1),
            _candidate(condition_id="w50-r0", worker_count=50),
            _candidate(condition_id="w1-r1", worker_count=1, sample=1),
        )
    )
    refs = {item["condition_id"]: item for item in plan.condition_refs}
    assert refs["w1-r0"]["semantic_slot_keys"] == refs["w50-r0"]["semantic_slot_keys"]
    assert refs["w1-r1"]["semantic_slot_keys"] != refs["w1-r0"]["semantic_slot_keys"]
    assert len(plan.rows) == 2


def test_same_repeat_cannot_map_to_multiple_sample_slots() -> None:
    with pytest.raises(ValueError, match="same repeat.*multiple sample"):
        build_semantic_inventory(
            (
                _candidate(condition_id="w1-r0", sample=0, repeat_id=0),
                _candidate(condition_id="w50-r0", sample=1, repeat_id=0),
            )
        )


def test_different_repeats_cannot_share_one_sample_slot() -> None:
    with pytest.raises(ValueError, match="different repeats.*same sample"):
        build_semantic_inventory(
            (
                _candidate(condition_id="w1-r0", sample=0, repeat_id=0),
                _candidate(condition_id="w1-r1", sample=0, repeat_id=1),
            )
        )


def test_repeat_sample_mapping_is_one_to_one_across_replacement_slots() -> None:
    def cross_replacement_candidates(
        *, first_repeat: int, first_sample: int, second_repeat: int, second_sample: int
    ) -> tuple[SemanticSlotCandidate, ...]:
        shared = {
            "experiment_id": "exp4_real_ai_protocol_ablation",
            "ablation_mode": "FULL",
        }
        return (
            _candidate(
                **shared,
                condition_id="first",
                repeat_id=first_repeat,
                sample=first_sample,
                replacement_slot=0,
            ),
            _candidate(
                **shared,
                condition_id="first",
                repeat_id=first_repeat,
                sample=first_sample,
                replacement_slot=1,
                provider_digest="sha256:" + "8" * 64,
            ),
            _candidate(
                **shared,
                condition_id="second",
                repeat_id=second_repeat,
                sample=second_sample,
                replacement_slot=1,
            ),
            _candidate(
                **shared,
                condition_id="second",
                repeat_id=second_repeat,
                sample=second_sample,
                replacement_slot=0,
                provider_digest="sha256:" + "9" * 64,
            ),
        )

    negative_cases = (
        (
            "same repeat cannot map to multiple sample slots",
            cross_replacement_candidates(
                first_repeat=0,
                first_sample=0,
                second_repeat=0,
                second_sample=1,
            ),
        ),
        (
            "different repeats cannot share the same sample slot",
            cross_replacement_candidates(
                first_repeat=0,
                first_sample=0,
                second_repeat=1,
                second_sample=0,
            ),
        ),
    )
    for error, candidates in negative_cases:
        with pytest.raises(ValueError, match=error):
            build_semantic_inventory(candidates)

    allowed = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp4_real_ai_protocol_ablation",
                condition_id="full-r0",
                ablation_mode="FULL",
                repeat_id=0,
                sample=0,
                replacement_slot=slot,
            )
            for slot in (0, 1)
        )
    )
    assert {row.replacement_slot for row in allowed.rows} == {0, 1}


def test_exp2_full_24_condition_sequence_and_max_concurrent_roots_one() -> None:
    conditions = _exp2_online_conditions()
    plan = build_semantic_inventory(
        (_candidate(),),
        exp2_online_conditions=conditions,
        max_concurrent_roots=1,
    )
    assert [item["condition_id"] for item in plan.exp2_online_condition_refs] == [
        item["condition_id"] for item in conditions
    ]
    assert len(plan.exp2_online_condition_refs) == 24
    assert plan.max_concurrent_roots == 1
    drifted = list(conditions)
    drifted[0] = {**drifted[0], "condition_id": "drifted-condition-id"}
    with pytest.raises(ValueError, match="condition digest"):
        build_semantic_inventory(
            (_candidate(),),
            exp2_online_conditions=drifted,
            max_concurrent_roots=1,
        )
    with pytest.raises(ValueError, match="max_concurrent_roots"):
        build_semantic_inventory(
            (_candidate(),),
            exp2_online_conditions=conditions,
            max_concurrent_roots=2,
        )


def test_exp3_rate_slots_zero_to_two_and_death_slots_zero_to_four() -> None:
    assert replacement_slots_for(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="false_positive",
        ablation_mode="FULL",
    ) == (0, 1, 2)
    assert replacement_slots_for(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="worker_death",
        ablation_mode="FULL",
    ) == (0, 1, 2, 3, 4)
    rate = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp3_real_ai_fault_recovery",
                condition_id="exp3-rate",
                fault_type="false_positive",
                replacement_slot=slot,
                body_marker=f"rate-{slot}",
            )
            for slot in range(3)
        )
    )
    death = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp3_real_ai_fault_recovery",
                condition_id="exp3-death",
                fault_type="worker_death",
                replacement_slot=slot,
                body_marker=f"death-{slot}",
            )
            for slot in range(5)
        )
    )
    assert {row.replacement_slot for row in rate.rows} == set(range(3))
    assert {row.replacement_slot for row in death.rows} == set(range(5))
    with pytest.raises(ValueError, match="incomplete replacement slots"):
        build_semantic_inventory(
            tuple(
                _candidate(
                    experiment_id="exp3_real_ai_fault_recovery",
                    condition_id="exp3-death",
                    fault_type="worker_death",
                    replacement_slot=slot,
                    body_marker=f"death-{slot}",
                )
                for slot in range(4)
            )
        )


def test_regression_smoke_sparse_replacements_keep_one_real_target_per_fault_root() -> None:
    rate_cases = (
        (4, 0.25, 1),
        (8, 0.25, 2),
        (8, 0.25, 2),
        (5, 0.1, 1),
        (2, 0.1, 1),
        (7, 0.1, 1),
        (7, 0.1, 1),
    )
    replacement_extra_count = 0
    for index, (count, rate, expected_target_count) in enumerate(rate_cases):
        unit_ids = tuple(f"case-{index}:unit-{unit}" for unit in range(count))
        slots = regression_smoke_sparse_replacement_slots(
            experiment_id="exp3_real_ai_fault_recovery",
            fault_type="false_positive",
            fault_rate=rate,
            ablation_mode="FULL",
            planned_ai_unit_ids=unit_ids,
        )
        targeted = tuple(
            unit_id for unit_id, unit_slots in slots.items() if unit_slots != (0,)
        )
        assert len(targeted) == expected_target_count
        assert all(slots[unit_id] == (0, 1, 2) for unit_id in targeted)
        replacement_extra_count += sum(len(value) - 1 for value in slots.values())

    worker_units = ("range_0", "range_1")
    worker_slots = regression_smoke_sparse_replacement_slots(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="worker_death",
        fault_rate=0.0,
        ablation_mode="FULL",
        planned_ai_unit_ids=worker_units,
        dead_worker_count=1,
        kill_progress_percent=50,
    )
    assert sum(value == (0, 1, 2, 3, 4) for value in worker_slots.values()) == 1
    replacement_extra_count += sum(
        len(value) - 1 for value in worker_slots.values()
    )

    assert replacement_extra_count == 22


def test_sparse_inventory_requires_exact_deterministic_target_slots() -> None:
    unit_ids = tuple(f"unit-{index}" for index in range(4))
    slots = regression_smoke_sparse_replacement_slots(
        experiment_id="exp3_real_ai_fault_recovery",
        fault_type="no_return",
        fault_rate=0.25,
        ablation_mode="FULL",
        planned_ai_unit_ids=unit_ids,
    )
    candidates = tuple(
        _candidate(
            experiment_id="exp3_real_ai_fault_recovery",
            condition_id="exp3-sparse-rate",
            fault_type="no_return",
            fault_rate=0.25,
            unit_id=unit_id,
            replacement_slot=replacement_slot,
            body_marker=f"{unit_id}:{replacement_slot}",
            replacement_policy_id="regression_smoke_sparse_replacements.v1",
        )
        for unit_id in unit_ids
        for replacement_slot in slots[unit_id]
    )

    plan = build_semantic_inventory(candidates)

    assert plan.expected_slot_count == 6
    missing_target_slot = next(
        candidate
        for candidate in candidates
        if candidate.replacement_slot == 2
    )
    with pytest.raises(ValueError, match="incomplete replacement slots"):
        build_semantic_inventory(
            tuple(candidate for candidate in candidates if candidate != missing_target_slot)
        )


def test_matrix8_unified_planner_freezes_166_exact_requests_without_provider(
    tmp_path: Path,
) -> None:
    catalog = paper_cli._load_default_paper_catalogs()
    lean_matrix = build_lean_3x3_matrix_plan(catalog_manifest=catalog)
    planning_profile = load_exp1_pilot_profile(
        paper_cli.DEFAULT_EXP1_PILOT_PROFILE
    )
    scale_profile = load_paper_suite_scale_profile(
        paper_cli.DEFAULT_PAPER_SUITE_SCALE_PROFILE
    )
    executions = []
    for experiment_number in range(1, 5):
        profile = load_paper_smoke_profile(
            Path(
                f"benchmarks/paper/paper_smoke_exp{experiment_number}_matrix8_profile.v1.json"
            )
        )
        dispatch = build_gate_c_dispatch_plans(
            catalog_manifest=catalog,
            lean_3x3_matrix=lean_matrix,
            experiment_ids=profile.experiment_ids,
            baseline_endpoint_binding=paper_cli._baseline_endpoint_binding(
                planning_profile
            ),
            model_endpoint_cohort_preflight=None,
            paper_suite_scale_profile=scale_profile,
            output_root=tmp_path / f"canonical-exp{experiment_number}",
        )
        dispatch = paper_cli._with_exp2_regression_smoke_lean_plan(
            dispatch_plans=dispatch,
            profile=profile,
            catalog_manifest=catalog,
        )
        dispatch = paper_cli._with_exp3_regression_smoke_lean_plan(
            dispatch_plans=dispatch,
            profile=profile,
            catalog_manifest=catalog,
        )
        execution = resolve_paper_smoke_execution_plan(
            profile=profile,
            dispatch_plans=dispatch,
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.catalog_version,
            catalog_digest=catalog.catalog_digest,
            output_root=tmp_path / f"smoke-exp{experiment_number}",
        )
        executions.append(
            paper_cli._with_matrix8_unified_factor_seed_execution_plan(
                execution_plan=execution,
                profile=profile,
            )
        )
    config = load_ai_api_config(
        json.loads(
            Path("benchmarks/paper/exp1_baseline_provider_config.v3.json")
            .read_text(encoding="utf-8")
        )
    )

    result = build_matrix8_unified_acquisition_plan(
        execution_plans=tuple(executions),
        catalog_manifest=catalog,
        ai_api_config=config,
        entry_id="deepseek_v4_pro_exp1_baseline",
        planning_artifact_root=tmp_path / "planning-artifacts",
        requested_at="2026-08-09T00:00:00Z",
        token_upper_bound=304_096,
        cost_upper_bound=Decimal("0.05"),
        frozen_pricing=FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("3.0"),
            output_per_million_tokens=Decimal("6.0"),
        ),
    )
    assert result.provider_call_count == 0
    assert result.condition_candidate_count == 280
    assert result.semantic_inventory_plan.expected_slot_count == 166
    assert len(result.acquisition_requests) == 166
    assert len(result.case_identity_mappings) == 8
    assert {item["root_case_id"] for item in result.case_identity_mappings} == {
        item.case_id for item in executions[0].items
    }
    exp2_factor = tuple(
        candidate
        for candidate in result.candidates
        if candidate.experiment_id == "exp2_real_ai_scalability"
        and candidate.prepared_request.plugin_id == "factorization"
        and candidate.replacement_slot == 0
    )
    assert len(exp2_factor) == 80
    replacement_identity_by_experiment = {
        experiment_id: {
            (
                candidate.case_record_digest,
                candidate.planned_ai_unit_id,
                candidate.sample_slot_index,
                candidate.replacement_slot,
                candidate.prepared_request.provider_config_digest,
                candidate.prompt_profile_digest,
                candidate.prepared_request.prompt_admission_profile_digest,
                candidate.prepared_request.plugin_version,
                candidate.prepared_request.body_digest,
                candidate.prepared_request.inference_request_digest,
            )
            for candidate in result.candidates
            if candidate.experiment_id == experiment_id
            and candidate.replacement_slot > 0
        }
        for experiment_id in (
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        )
    }
    shared_replacement_identities = (
        replacement_identity_by_experiment["exp3_real_ai_fault_recovery"]
        & replacement_identity_by_experiment["exp4_real_ai_protocol_ablation"]
    )
    shared_replacement_rows = {
        identity: (candidate.case_id, candidate.planned_ai_unit_id)
        for candidate in result.candidates
        if candidate.experiment_id == "exp3_real_ai_fault_recovery"
        and candidate.replacement_slot > 0
        and (
            candidate.case_record_digest,
            candidate.planned_ai_unit_id,
            candidate.sample_slot_index,
            candidate.replacement_slot,
            candidate.prepared_request.provider_config_digest,
            candidate.prompt_profile_digest,
            candidate.prepared_request.prompt_admission_profile_digest,
            candidate.prepared_request.plugin_version,
            candidate.prepared_request.body_digest,
            candidate.prepared_request.inference_request_digest,
        )
        in shared_replacement_identities
        for identity in (
            (
                candidate.case_record_digest,
                candidate.planned_ai_unit_id,
                candidate.sample_slot_index,
                candidate.replacement_slot,
                candidate.prepared_request.provider_config_digest,
                candidate.prompt_profile_digest,
                candidate.prepared_request.prompt_admission_profile_digest,
                candidate.prepared_request.plugin_version,
                candidate.prepared_request.body_digest,
                candidate.prepared_request.inference_request_digest,
            ),
        )
    }
    assert len(shared_replacement_identities) == 7
    assert set(shared_replacement_rows.values()) == {
        ("factor_v2_easy_109", "range_0"),
        ("factor_v2_hard_063", "range_3"),
        ("factor_v2_hard_063", "range_4"),
        ("factor_v2_medium_033", "range_3"),
        ("lean_easy_01", "child_1"),
        (
            "lean_v2_hard_frontier_pure_logic_checker_02",
            "pure_hard_mid_b_02",
        ),
        ("lean_v2_medium_lemma_dag_01", "pure_medium_leaf_bc_01"),
    }
    for case_id in (
        "factor_v2_easy_109",
        "factor_v2_medium_033",
        "factor_v2_hard_034",
        "factor_v2_hard_063",
    ):
        shared_bodies = {
            candidate.prepared_request.body_digest
            for candidate in result.candidates
            if candidate.case_id == case_id
            and candidate.replacement_slot == 0
            and candidate.experiment_id
            in {
                "exp1_real_ai_feasibility",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            }
        }
        ordinary_unit_count = sum(
            candidate.experiment_id == "exp1_real_ai_feasibility"
            and candidate.case_id == case_id
            and candidate.replacement_slot == 0
            for candidate in result.candidates
        )
        assert len(shared_bodies) == ordinary_unit_count
    assert all(
        request.prepared_request.body_digest
        == request.inventory_row.body_digest
        for request in result.acquisition_requests
    )


def test_exp4_full_and_ablations_share_zero_to_one() -> None:
    modes = (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    )
    for mode in modes:
        assert replacement_slots_for(
            experiment_id="exp4_real_ai_protocol_ablation",
            fault_type="none",
            ablation_mode=mode,
        ) == (0, 1)
    plan = build_semantic_inventory(
        tuple(
            _candidate(
                experiment_id="exp4_real_ai_protocol_ablation",
                condition_id=f"exp4-{mode}",
                ablation_mode=mode,
                replacement_slot=slot,
                body_marker=f"shared-{slot}",
            )
            for mode in modes
            for slot in (0, 1)
        )
    )
    assert len(plan.rows) == 2
    refs = {item["condition_id"]: item for item in plan.condition_refs}
    assert len({tuple(item["semantic_slot_keys"]) for item in refs.values()}) == 1


def test_same_semantic_slot_cannot_preregister_two_inference_digests() -> None:
    with pytest.raises(ValueError, match="one semantic slot"):
        build_semantic_inventory(
            (_candidate(body_marker="first"), _candidate(body_marker="second"))
        )


def test_early_stop_does_not_prune_inventory() -> None:
    candidates = tuple(
        _candidate(unit_id=f"unit-{index}", body_marker=f"unit-{index}")
        for index in range(20)
    )
    full = build_semantic_inventory(candidates)
    stopped = build_semantic_inventory(
        candidates,
        observed_early_stop_slot_keys=(full.rows[0].semantic_slot_key,),
    )
    assert stopped.rows == full.rows
    assert stopped.expected_slot_count == 20


def test_missing_slot_writes_preflight_blocked_record_and_zero_protocol_engine_events() -> None:
    plan = build_semantic_inventory(
        (_candidate(), _candidate(unit_id="unit-1", body_marker="unit-1"))
    )
    calls = []
    result = preflight_inventory_before_coordinator(
        plan=plan,
        available_inventory_entry_ids=(plan.rows[0].inventory_entry_id,),
        coordinator_factory=lambda: calls.append("constructed"),
    )
    assert result.status == "blocked"
    assert len(result.blocked_records) == 1
    assert result.blocked_records[0].schema_version == "tokenshare.response_bank_preflight_blocked.v1"
    assert result.protocol_engine_event_count == 0
    assert result.provider_call_count == 0
    assert result.coordinator_constructed is False
    assert calls == []


def test_terminal_provider_failure_counts_present() -> None:
    plan = build_semantic_inventory(
        (
            _candidate(),
            _candidate(
                unit_id="unit-1",
                body_marker="unit-1",
                terminal_kind="provider_failure",
            ),
        )
    )
    budget = PaperBudgetResult(
        budget_digest="sha256:" + "a" * 64,
        planned_experiments=["exp2_real_ai_scalability"],
        planned_conditions=1,
        planned_root_runs=1,
        planned_ai_units=2,
        max_provider_attempts=4,
        token_upper_bound=1200,
        cost_upper_bound=2.5,
        wall_clock_estimate=1.0,
        quota_preflight={"provider_calls_made": 0},
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={"bytes": 4096},
        status=PaperStatus.PLANNED,
    )
    report = build_response_bank_budget_report(
        budget=budget,
        inventory_plan=plan,
        model_ids=("deepseek-v4-pro",),
    )
    assert report == {
        "schema_version": "tokenshare.response_bank_budget_report.v1",
        "planned_root_runs": 1,
        "planned_first_attempt_ai_units": 2,
        "semantic_slot_count": 2,
        "model_ids": ["deepseek-v4-pro"],
        "max_concurrent_roots": 1,
        "provider_calls_upper": 2,
        "token_upper_bound": 600,
        "cost_upper_bound_cny": 1.25,
        "disk_estimate": {"bytes": 4096},
        "terminal_provider_failure_count": 1,
        "terminal_success_count": 0,
        "terminal_unacquired_count": 1,
    }


def test_conflicting_terminal_kinds_fail_closed_independent_of_input_order() -> None:
    terminal_pairs = (
        (None, "success"),
        (None, "provider_failure"),
        ("success", "provider_failure"),
    )
    for first_kind, second_kind in terminal_pairs:
        first = _candidate(terminal_kind=first_kind)
        second = _candidate(terminal_kind=second_kind)
        for ordered in ((first, second), (second, first)):
            with pytest.raises(ValueError, match="conflicting terminal_kind"):
                build_semantic_inventory(ordered)
