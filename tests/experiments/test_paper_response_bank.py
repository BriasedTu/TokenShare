from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequestFactory,
)
from tokenshare.executors.response_bank import (
    ResultsFirstResponseBankManifest,
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
    RepresentativeUnifiedAcquisitionPlan,
    SemanticSlotCandidate,
    build_semantic_inventory,
    create_acquisition_plan_bundle,
    create_representative_acquisition_plan_bundle,
    establish_results_first_acquisition_authorization,
    load_acquisition_plan_bundle,
    load_representative_acquisition_plan_bundle,
    preflight_inventory_before_coordinator,
    replacement_slots_for,
    results_first_response_bank_manifest_for_bundle,
)
from tokenshare.experiments.paper_resource_accounting import FrozenPricing
from tokenshare.experiments import paper_response_bank as response_bank


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
    case_id: str = "case-a",
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
) -> SemanticSlotCandidate:
    return SemanticSlotCandidate(
        experiment_id=experiment_id,
        condition_id=condition_id,
        condition_digest=digest_json({"condition_id": condition_id}),
        worker_count=worker_count,
        repeat_id=sample if repeat_id is None else repeat_id,
        fault_type=fault_type,
        ablation_mode=ablation_mode,
        case_id=case_id,
        case_record_digest=case_digest,
        planned_ai_unit_id=unit_id,
        sample_slot_index=sample,
        replacement_slot=replacement_slot,
        prompt_profile_digest=prompt_digest,
        prepared_request=_prepared(
            case_id=case_id,
            unit_id=unit_id,
            sample=sample,
            replacement_slot=replacement_slot,
            body_marker=body_marker,
            provider_digest=provider_digest,
            plugin_version=plugin_version,
        ),
        terminal_kind=terminal_kind,
        replacement_policy_id=replacement_policy_id,
    )


def _formal_exp1_candidate(
    *,
    case_id: str,
    case_digest: str,
    split_profile_digest: str,
    unit_id: str,
) -> SemanticSlotCandidate:
    return replace(
        _candidate(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_factorization_easy_w10_r0",
            worker_count=10,
            case_id=case_id,
            case_digest=case_digest,
            unit_id=unit_id,
            body_marker=f"{case_id}:{unit_id}",
        ),
        formal_authority=True,
        selection_id="exp1-factorization-easy-v1",
        selection_digest="sha256:" + "7" * 64,
        seed=271828,
        split_profile_id=None,
        split_profile_digest=split_profile_digest,
        source_snapshot_digest="sha256:" + "5" * 64,
        coverage_digest="sha256:" + "6" * 64,
        model_endpoint_identity_digest="sha256:" + "9" * 64,
        request_controls_digest="sha256:" + "a" * 64,
    )


def _representative_plan() -> RepresentativeUnifiedAcquisitionPlan:
    source_snapshot_digest = "sha256:" + "5" * 64
    coverage_digest = "sha256:" + "6" * 64
    candidate = replace(
        _candidate(),
        formal_authority=True,
        selection_id="formal-selection-v1",
        selection_digest="sha256:" + "7" * 64,
        seed=271828,
        split_profile_id="factorization.exp2_contiguous_20way.v1",
        split_profile_digest="sha256:" + "8" * 64,
        source_snapshot_digest=source_snapshot_digest,
        coverage_digest=coverage_digest,
        model_endpoint_identity_digest="sha256:" + "9" * 64,
        request_controls_digest="sha256:" + "a" * 64,
    )
    semantic_plan = build_semantic_inventory((candidate,))
    row = semantic_plan.rows[0]
    request = AcquisitionRequest(
        inventory_row=row,
        prepared_request=candidate.prepared_request,
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
    return RepresentativeUnifiedAcquisitionPlan(
        source_snapshot_digest=source_snapshot_digest,
        source_prepared_inventory_digest="sha256:" + "b" * 64,
        coverage_digest=coverage_digest,
        condition_candidate_count=1,
        unique_acquisition_request_count=1,
        candidates=(candidate,),
        semantic_inventory_plan=semantic_plan,
        acquisition_requests=(request,),
        max_acquisition_concurrency=10,
    )


def _single_request_bundle(tmp_path: Path):
    candidate = _candidate()
    plan = build_semantic_inventory((candidate,))
    request = AcquisitionRequest(
        inventory_row=plan.rows[0],
        prepared_request=candidate.prepared_request,
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
    return create_acquisition_plan_bundle(
        tmp_path / "bundle",
        authorized_plan_digest="sha256:" + "a" * 64,
        profile_digest="sha256:" + "b" * 64,
        semantic_inventory_plan=plan,
        acquisition_requests=(request,),
    )


def _write_legacy_results_first_marker(*, bundle, output_root: Path) -> bytes:
    output_root.mkdir(parents=True, exist_ok=True)
    selected_experiments = [
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    ]
    identity = {
        "schema_version": (
            "tokenshare.results_first_smoke_acquisition_authorization.v1"
        ),
        "authorization_kind": "user_authorized_smoke_facility",
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
        "provider_config_digest": bundle.provider_config_digest,
        "output_root_path_digest": response_bank.output_root_path_digest(output_root),
        "selected_experiments": selected_experiments,
    }
    marker_body = {
        "schema_version": "tokenshare.results_first_smoke_facility_marker.v1",
        "authorization_kind": "user_authorized_smoke_facility",
        "authorization_digest": canonical_digest(identity),
        "authorized_plan_digest": bundle.authorized_plan_digest,
        "profile_digest": bundle.profile_digest,
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            bundle.prompt_admission_profile_digest
        ),
        "provider_config_digest": bundle.provider_config_digest,
        "output_root_path_digest": response_bank.output_root_path_digest(output_root),
    }
    persisted = {
        **marker_body,
        "marker_digest": canonical_digest(marker_body),
    }
    encoded = (
        json.dumps(
            persisted,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    (
        output_root / "results_first_smoke_facility_marker.v1.json"
    ).write_bytes(encoded)
    return encoded


def test_results_first_authorization_writes_neutral_marker_and_resumes_legacy_exactly(
    tmp_path: Path,
) -> None:
    bundle = _single_request_bundle(tmp_path)
    new_root = tmp_path / "new-acquisition"

    created = establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=new_root,
        output_mode="new_run",
        allow_provider_calls=True,
    )

    assert created.schema_version == (
        "tokenshare.results_first_acquisition_authorization.v1"
    )
    assert created.authorization_kind == "user_authorized_results_first"
    assert created.authorization_state == "user_authorized_results_first"
    assert created.selected_experiments == ("exp1_real_ai_feasibility",)
    assert created.marker.schema_version == (
        "tokenshare.results_first_facility_marker.v1"
    )
    assert (new_root / "results_first_facility_marker.v1.json").is_file()
    assert not (
        new_root / "results_first_smoke_facility_marker.v1.json"
    ).exists()

    legacy_root = tmp_path / "legacy-acquisition"
    legacy_bytes = _write_legacy_results_first_marker(
        bundle=bundle,
        output_root=legacy_root,
    )
    resumed = establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=legacy_root,
        output_mode="resume",
        allow_provider_calls=True,
    )

    assert resumed.schema_version == (
        "tokenshare.results_first_smoke_acquisition_authorization.v1"
    )
    assert resumed.authorization_kind == "user_authorized_smoke_facility"
    assert resumed.authorization_state == "user_authorized_smoke_facility"
    assert (
        legacy_root / "results_first_smoke_facility_marker.v1.json"
    ).read_bytes() == legacy_bytes
    assert not (legacy_root / "results_first_facility_marker.v1.json").exists()
    assert results_first_response_bank_manifest_for_bundle(
        bundle,
        resumed,
    ).authorization_kind == "user_authorized_smoke_facility"


def test_results_first_authorization_dual_or_corrupt_new_marker_fails_closed(
    tmp_path: Path,
) -> None:
    bundle = _single_request_bundle(tmp_path)
    dual_root = tmp_path / "dual-acquisition"
    establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=dual_root,
        output_mode="new_run",
        allow_provider_calls=True,
    )
    _write_legacy_results_first_marker(bundle=bundle, output_root=dual_root)
    with pytest.raises(response_bank.AcquisitionAuthorizationError, match="both"):
        establish_results_first_acquisition_authorization(
            bundle=bundle,
            output_root=dual_root,
            output_mode="resume",
            allow_provider_calls=True,
        )

    corrupt_root = tmp_path / "corrupt-new-acquisition"
    establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=corrupt_root,
        output_mode="new_run",
        allow_provider_calls=True,
    )
    (corrupt_root / "results_first_facility_marker.v1.json").write_text(
        "{}",
        encoding="utf-8",
    )
    with pytest.raises(
        response_bank.AcquisitionAuthorizationError,
        match="identity mismatch",
    ):
        establish_results_first_acquisition_authorization(
            bundle=bundle,
            output_root=corrupt_root,
            output_mode="resume",
            allow_provider_calls=True,
        )


def test_results_first_manifest_round_trips_new_and_legacy_authorization_kinds() -> None:
    common = {
        "bank_root_id": "sha256:" + "1" * 64,
        "profile_digest": "sha256:" + "2" * 64,
        "budget_digest": "sha256:" + "3" * 64,
        "inventory_digest": "sha256:" + "4" * 64,
        "provider_config_digest": "sha256:" + "5" * 64,
        "entry_ids": ("entry-a",),
        "object_role_schema": ("raw_response",),
        "terminal_entry_count": 1,
        "authorization_digest": "sha256:" + "6" * 64,
    }
    current = ResultsFirstResponseBankManifest.create(**common)
    legacy = ResultsFirstResponseBankManifest.create(
        **common,
        authorization_kind="user_authorized_smoke_facility",
    )

    assert current.authorization_kind == "user_authorized_results_first"
    assert ResultsFirstResponseBankManifest.from_dict(current.to_dict()) == current
    assert ResultsFirstResponseBankManifest.from_dict(legacy.to_dict()) == legacy

    missing = current.to_dict()
    missing.pop("authorization_kind")
    with pytest.raises(ValueError, match="fields mismatch"):
        ResultsFirstResponseBankManifest.from_dict(missing)
    unknown = current.to_dict()
    unknown["authorization_kind"] = "unknown"
    with pytest.raises(ValueError, match="authorization kind"):
        ResultsFirstResponseBankManifest.from_dict(unknown)


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
    assert reopened.schema_version == "tokenshare.paper_acquisition_plan_bundle.v2"
    assert (root / "acquisition_plan_bundle.v2.json").is_file()
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
    assert reopened.source_snapshot_digest is None
    assert reopened.source_prepared_inventory_digest is None
    assert reopened.coverage_digest is None
    assert reopened.representative_plan_digest is None
    with pytest.raises(FileExistsError):
        create_acquisition_plan_bundle(
            root,
            authorized_plan_digest="sha256:" + "a" * 64,
            profile_digest="sha256:" + "b" * 64,
            semantic_inventory_plan=plan,
            acquisition_requests=reopened.acquisition_requests,
        )
    path = root / "acquisition_plan_bundle.v2.json"
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
    path = root / "acquisition_plan_bundle.v2.json"
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


def test_representative_bundle_round_trips_exact_plan_lineage(tmp_path: Path) -> None:
    plan = _representative_plan()
    root = tmp_path / "representative-bundle"

    created = create_representative_acquisition_plan_bundle(root, plan=plan)
    reopened = load_representative_acquisition_plan_bundle(root, plan=plan)

    assert reopened == created
    assert reopened.source_snapshot_digest == plan.source_snapshot_digest
    assert (
        reopened.source_prepared_inventory_digest
        == plan.source_prepared_inventory_digest
    )
    assert reopened.coverage_digest == plan.coverage_digest
    assert reopened.representative_plan_digest == plan.plan_digest
    with pytest.raises(ValueError, match="requires fresh-plan validation"):
        load_acquisition_plan_bundle(root)


def test_representative_bundle_rejects_resigned_lineage_and_fresh_plan_mismatch(
    tmp_path: Path,
) -> None:
    plan = _representative_plan()
    root = tmp_path / "representative-bundle-tamper"
    create_representative_acquisition_plan_bundle(root, plan=plan)
    path = root / "acquisition_plan_bundle.v2.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["source_snapshot_digest"] = "sha256:" + "f" * 64
    body["authorized_plan_digest"] = body["source_snapshot_digest"]
    body["bundle_digest"] = canonical_digest(
        {key: value for key, value in body.items() if key != "bundle_digest"}
    )
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="representative acquisition lineage"):
        load_representative_acquisition_plan_bundle(root, plan=plan)

    fresh_root = tmp_path / "representative-bundle-mismatch"
    create_representative_acquisition_plan_bundle(fresh_root, plan=plan)
    mismatched_plan = replace(
        plan,
        source_prepared_inventory_digest="sha256:" + "e" * 64,
    )
    with pytest.raises(ValueError, match="representative acquisition lineage"):
        load_representative_acquisition_plan_bundle(
            fresh_root,
            plan=mismatched_plan,
        )


def test_full_acquisition_budget_accepts_l3_values_as_ordinary_hard_ceilings() -> None:
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


def test_exp1_condition_keeps_distinct_case_split_profile_authority() -> None:
    case_064 = _formal_exp1_candidate(
        case_id="factor_v2_easy_064",
        case_digest="sha256:" + "b" * 64,
        split_profile_digest="sha256:" + "c" * 64,
        unit_id="range_0",
    )
    case_093 = _formal_exp1_candidate(
        case_id="factor_v2_easy_093",
        case_digest="sha256:" + "d" * 64,
        split_profile_digest="sha256:" + "e" * 64,
        unit_id="range_0",
    )

    plan = build_semantic_inventory((case_064, case_093))

    assert len(plan.condition_refs) == 1
    condition_ref = plan.condition_refs[0]
    assert condition_ref["condition_id"] == "exp1_factorization_easy_w10_r0"
    assert condition_ref["experiment_id"] == "exp1_real_ai_feasibility"
    assert "split_profile_id" not in condition_ref
    assert "split_profile_digest" not in condition_ref
    case_refs = {ref["case_id"]: ref for ref in condition_ref["case_refs"]}
    assert case_refs["factor_v2_easy_064"]["split_profile_id"] is None
    assert case_refs["factor_v2_easy_064"]["split_profile_digest"] == (
        "sha256:" + "c" * 64
    )
    assert case_refs["factor_v2_easy_093"]["split_profile_id"] is None
    assert case_refs["factor_v2_easy_093"]["split_profile_digest"] == (
        "sha256:" + "e" * 64
    )
    rows_by_slot = {row.semantic_slot_key: row for row in plan.rows}
    assert {
        ref["case_id"]: sorted(
            {
                rows_by_slot[slot_key].replacement_slot
                for slot_key in ref["semantic_slot_keys"]
            }
        )
        for ref in condition_ref["case_refs"]
    } == {
        "factor_v2_easy_064": [0],
        "factor_v2_easy_093": [0],
    }


def test_exp1_same_case_split_profile_drift_fails_closed() -> None:
    base = _formal_exp1_candidate(
        case_id="factor_v2_easy_064",
        case_digest="sha256:" + "b" * 64,
        split_profile_digest="sha256:" + "c" * 64,
        unit_id="range_0",
    )
    drift = replace(base, split_profile_digest="sha256:" + "d" * 64)

    with pytest.raises(ValueError, match="case split profile authority drift"):
        build_semantic_inventory((base, drift))


def test_target_fault_replacement_policy_does_not_expand_exp1_source_slots() -> None:
    source = _formal_exp1_candidate(
        case_id="factor_v2_easy_064",
        case_digest="sha256:" + "b" * 64,
        split_profile_digest="sha256:" + "c" * 64,
        unit_id="range_0",
    )
    target_candidates = tuple(
        _candidate(
            experiment_id="exp3_real_ai_fault_recovery",
            condition_id="exp3-worker-death",
            fault_type="worker_death",
            case_id="factor_v2_easy_064",
            case_digest="sha256:" + "b" * 64,
            unit_id="range_0",
            replacement_slot=slot,
            body_marker="factor_v2_easy_064:range_0",
        )
        for slot in range(5)
    )

    plan = build_semantic_inventory((source, *target_candidates))

    refs = {ref["condition_id"]: ref for ref in plan.condition_refs}
    rows_by_slot = {row.semantic_slot_key: row for row in plan.rows}
    source_slots = {
        rows_by_slot[slot_key].replacement_slot
        for slot_key in refs[source.condition_id]["semantic_slot_keys"]
    }
    target_slots = {
        rows_by_slot[slot_key].replacement_slot
        for slot_key in refs["exp3-worker-death"]["semantic_slot_keys"]
    }
    assert source_slots == {0}
    assert target_slots == {0, 1, 2, 3, 4}


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


def test_regression_sparse_replacement_authority_is_removed() -> None:
    assert not hasattr(response_bank, "regression_smoke_sparse_replacement_slots")


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
