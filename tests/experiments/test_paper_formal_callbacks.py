from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_request_identity import PreparedOutboundRequestFactory

from tokenshare.experiments.paper_formal_callbacks import (
    DeferredPaperExp5LedgerRootCallbackFactory,
    PaperExp5LedgerRootCallbackFactory,
    PaperOnlineRootCallbackFactory,
    PaperOnlineProviderEvidenceCallback,
    produce_exp3_online_recovery_evidence,
    run_exp1_normal_strategy,
    run_exp3_post_ai_strategy,
    run_exp3_worker_death_strategy,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_formal_plan import (
    FormalExecutionCoverage,
    FormalPreparedRequestInventory,
    FormalPreparedRequestRecord,
)
from tokenshare.experiments.paper_models import PaperExperimentCondition, digest_json
from tokenshare.experiments.paper_formal_runner import (
    _condition_with_frozen_case_metadata,
)
from tokenshare.experiments.paper_online_checks import (
    EXP3_RECOVERY_CHAIN_ROLES,
    freeze_paper_online_checks_plan,
)
from tokenshare.experiments.paper_budget import PaperBudgetLimits
from tokenshare.experiments.paper_budget_ledger import (
    PaperBudgetLedger,
    ReservationRequest,
)
from tokenshare.experiments.paper_resource_accounting import FrozenPricing, ProviderUsage
from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    inventory_entry_id,
    response_bank_inventory_digest,
    semantic_slot_key,
)
from tokenshare.experiments.paper_workers import WorkerDeathKillPoint
from tokenshare.storage.artifacts import ArtifactStore
from tests.experiments.test_paper_workers import _protocol_events


@dataclass(frozen=True)
class _Attempt:
    unit_id: str
    attempt_id: str
    provider: str = "siliconflow"
    model: str = "zai-org/GLM-5.2"
    entry_id: str = "glm_5_2_exp1_baseline"
    raw_output_ref: dict[str, Any] | None = None
    provenance_ref: dict[str, Any] | None = None


def _replace_unit_id(value: Any, unit_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_unit_id(item, unit_id)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_replace_unit_id(item, unit_id) for item in value)
    if isinstance(value, list):
        return [_replace_unit_id(item, unit_id) for item in value]
    return unit_id if value == "unit_lemma_join" else value


def _replace_protocol_identity(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_protocol_identity(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_replace_protocol_identity(item, replacements) for item in value)
    if isinstance(value, list):
        return [_replace_protocol_identity(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def _single_entry_config() -> dict:
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    return body


def _run_persisted_online_attempt(
    store: ArtifactStore,
    *,
    callback: PaperOnlineProviderEvidenceCallback,
    submission_id: str,
    submitted_at: str,
) -> object:
    request = dataclass_replace(
        make_ai_request(store, request_id=f"request-{submission_id}"),
        attempt_id=submission_id,
        unit_id="unit-1",
    )
    executor = AIAPIExecutor(
        executor_id="task25-official-exp3-callback",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(_single_entry_config()),
        transport=FakeSiliconFlowTransport(
            [
                FakeProviderResponse(
                    status_code=200,
                    body={
                        "id": f"response-{submission_id}",
                        "model": "Qwen/Qwen2.5-7B-Instruct",
                        "choices": [{"message": {"content": "candidate"}}],
                        "usage": {
                            "prompt_tokens": 11,
                            "completion_tokens": 13,
                            "total_tokens": 24,
                        },
                    },
                )
            ]
        ),
        post_raw_output_hook=callback,
    )
    submission = executor.execute(
        request,
        submission_id=submission_id,
        submitted_at=submitted_at,
    )
    assert submission.raw_output_ref is not None
    return callback.require_capture(submission_id)


def _callback_budget(
    tmp_path: Path,
) -> tuple[PaperBudgetLedger, ReservationRequest]:
    slot = semantic_slot_key(
        case_record_digest="case-digest",
        planned_ai_unit_id="unit-1",
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest="provider-config-digest",
        prompt_profile_digest="prompt-profile-digest",
        prompt_admission_profile_digest="admission-digest",
        plugin_version="plugin.v1",
    )
    provisional = ResponseBankInventoryRow(
        inventory_entry_id="",
        semantic_slot_key=slot,
        case_record_digest="case-digest",
        planned_ai_unit_id="unit-1",
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest="provider-config-digest",
        prompt_profile_digest="prompt-profile-digest",
        prompt_admission_profile_digest="admission-digest",
        plugin_version="plugin.v1",
        entry_id="sf_qwen",
        body_digest="body-digest",
        inference_request_digest="request-digest",
    )
    row = dataclass_replace(
        provisional,
        inventory_entry_id=inventory_entry_id(provisional),
    )
    digest = response_bank_inventory_digest((row,))
    ledger = PaperBudgetLedger(
        tmp_path / "online-budget.sqlite3",
        limits=PaperBudgetLimits(
            calls=10,
            tokens=10_000,
            cny=Decimal("100"),
            deepseek_cumulative_cny=Decimal("100"),
        ),
    )
    ledger.preregister_inventory(inventory_digest=digest, rows=(row,))
    return ledger, ReservationRequest(
        inventory_digest=digest,
        inventory_entry_id=row.inventory_entry_id,
        semantic_slot_key=row.semantic_slot_key,
        inference_request_digest=row.inference_request_digest,
        prompt_admission_profile_digest=row.prompt_admission_profile_digest,
        token_upper_bound=100,
        cost_upper_bound=Decimal("1"),
        provider_family="siliconflow",
        frozen_pricing=FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("0"),
            output_per_million_tokens=Decimal("0"),
        ),
    )


def _callback_budget_with_exact_costs(
    tmp_path: Path,
    *,
    cost_upper_bounds: tuple[Decimal, ...],
) -> tuple[PaperBudgetLedger, tuple[ReservationRequest, ...]]:
    rows: list[ResponseBankInventoryRow] = []
    for index, _cost_upper_bound in enumerate(cost_upper_bounds):
        slot = semantic_slot_key(
            case_record_digest="case-digest",
            planned_ai_unit_id=f"unit-{index}",
            sample_slot_index=0,
            replacement_slot=0,
            provider_config_digest="provider-config-digest",
            prompt_profile_digest="prompt-profile-digest",
            prompt_admission_profile_digest="admission-digest",
            plugin_version="plugin.v1",
        )
        provisional = ResponseBankInventoryRow(
            inventory_entry_id="",
            semantic_slot_key=slot,
            case_record_digest="case-digest",
            planned_ai_unit_id=f"unit-{index}",
            sample_slot_index=0,
            replacement_slot=0,
            provider_config_digest="provider-config-digest",
            prompt_profile_digest="prompt-profile-digest",
            prompt_admission_profile_digest="admission-digest",
            plugin_version="plugin.v1",
            entry_id="sf_qwen",
            body_digest=f"body-digest-{index}",
            inference_request_digest=f"request-digest-{index}",
        )
        rows.append(
            dataclass_replace(
                provisional,
                inventory_entry_id=inventory_entry_id(provisional),
            )
        )
    inventory_rows = tuple(rows)
    digest = response_bank_inventory_digest(inventory_rows)
    ledger = PaperBudgetLedger(
        tmp_path / "online-budget.sqlite3",
        limits=PaperBudgetLimits(
            calls=10,
            tokens=10_000,
            cny=Decimal("100"),
            deepseek_cumulative_cny=Decimal("100"),
        ),
    )
    ledger.preregister_inventory(inventory_digest=digest, rows=inventory_rows)
    return ledger, tuple(
        ReservationRequest(
            inventory_digest=digest,
            inventory_entry_id=row.inventory_entry_id,
            semantic_slot_key=row.semantic_slot_key,
            inference_request_digest=row.inference_request_digest,
            prompt_admission_profile_digest=row.prompt_admission_profile_digest,
            token_upper_bound=1,
            cost_upper_bound=cost_upper_bound,
            provider_family="siliconflow",
            frozen_pricing=FrozenPricing(
                currency="CNY",
                input_per_million_tokens=(
                    cost_upper_bound * Decimal(1_000_000)
                ),
                output_per_million_tokens=Decimal("0"),
            ),
        )
        for row, cost_upper_bound in zip(
            inventory_rows,
            cost_upper_bounds,
            strict=True,
        )
    )


def _synthetic_exp5_formal_authority(
    *,
    domain: str = "factorization",
    prepared_case_id: str | None = None,
    distinct_config_layers: bool = False,
    case_record: dict[str, Any] | None = None,
) -> tuple[object, object, object, object]:
    config_body = _single_entry_config()
    config_body["entries"][0]["pricing"]["input_per_million_tokens"] = 1.0
    config_body["entries"][0]["pricing"]["output_per_million_tokens"] = 2.0
    prepared_config = load_ai_api_config(config_body)
    source_config = prepared_config
    if distinct_config_layers:
        source_body = make_config_dict()
        source_body["entries"][0]["pricing"]["input_per_million_tokens"] = 1.0
        source_body["entries"][0]["pricing"]["output_per_million_tokens"] = 2.0
        source_config = load_ai_api_config(source_body)
    if case_record is None:
        case_record = {
            "case_id": "exp5-case",
            "domain": domain,
            "difficulty": "hard",
            "paper_difficulty": (
                "hard_frontier" if domain == "lean_proof" else "hard"
            ),
        }
        if domain == "lean_proof":
            case_record.update(
                {
                    "topic_family": "pure_logic",
                    "topic_family_version": "v1",
                    "construction_rule_id": (
                        "hard_frontier.pure_logic.deep_semantic_chain.v2"
                    ),
                    "oracle_package_group": (
                        "lean_lemma_graph_oracle.pure_logic.v1"
                    ),
                    "proof_assembly_shape": (
                        "recursive_lemma_dag_required_slots.v1"
                    ),
                }
            )
    catalog_case_id = str(case_record["case_id"])
    entry = prepared_config.entries[0]
    controls = {
        "stream": False,
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 128,
    }
    body = {
        "model": entry.model,
        "messages": [{"role": "user", "content": "exp5 exact identity"}],
        **controls,
    }
    if prepared_case_id is None:
        runtime_prefix = {
            "factorization": "paper_factorization_",
            "lean_proof": "paper_lean_",
        }.get(domain, "paper_unknown_")
        prepared_case_id = f"{runtime_prefix}{catalog_case_id}"
    prepared = PreparedOutboundRequestFactory.prepare(
        body_obj=body,
        base_url=entry.base_url,
        endpoint=entry.endpoint,
        provider_config_digest=prepared_config.config_digest,
        entry_id=entry.entry_id,
        configured_model=entry.model,
        effective_controls_digest=digest_json(controls),
        plugin_id="tokenshare.factorization",
        plugin_version="1.0.0",
        prompt_profile_id="factorization.worker.v1",
        prompt_serialization_schema="tokenshare.factorization_prompt.v1",
        body_serialization_schema="phase7.siliconflow_chat_body.v1",
        case_id=prepared_case_id,
        planned_ai_unit_id="unit-exp5",
        sample_slot_index=0,
        replacement_slot=0,
    )
    provider_identity = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": source_config.provider_family,
        "entry_id": entry.entry_id,
        "configured_model": entry.model,
        "requested_model": entry.model,
        "reasoning_controls": {},
        "effective_request_controls_digest": digest_json(controls),
    }
    endpoint_digest = "sha256:" + "9" * 64
    condition: Any
    if domain in {"factorization", "lean_proof"}:
        condition = PaperExperimentCondition(
            experiment_id="exp5_real_ai_model_endpoint_comparison",
            condition_id="exp5-condition",
            domain=domain,
            difficulty="hard",
            paper_difficulty=case_record.get("paper_difficulty"),
            topic_family=case_record.get("topic_family"),
            topic_family_version=None,
            construction_rule_id=None,
            oracle_package_group=None,
            proof_assembly_shape=None,
            worker_count=3,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            model_cohort_id="test-exp5-cohort",
            cohort_member_id="test-exp5-member",
            seed=271828,
            repeat_id=0,
            provider_config_id="siliconflow",
            provider_family=source_config.provider_family,
            model_entry_id=entry.entry_id,
            provider_model_id=entry.model,
            reasoning_profile_id="nonthinking",
            model_cohort_digest="sha256:" + "8" * 64,
            source_provider_config_digest=source_config.config_digest,
            model_endpoint_identity_digest=endpoint_digest,
            catalog_digest="sha256:" + "7" * 64,
        )
    else:
        condition = SimpleNamespace(
            experiment_id="exp5_real_ai_model_endpoint_comparison",
            condition_id="exp5-condition",
            condition_digest="sha256:" + "1" * 64,
            domain=domain,
            seed=271828,
            repeat_id=0,
            provider_config_id="siliconflow",
            provider_family=source_config.provider_family,
            model_entry_id=entry.entry_id,
            provider_model_id=entry.model,
            reasoning_profile_id="nonthinking",
            source_provider_config_digest=source_config.config_digest,
            model_endpoint_identity_digest=endpoint_digest,
        )
    selection = SimpleNamespace(
        selection_id="selection-exp5",
        selection_digest="sha256:" + "2" * 64,
        ordered_case_ids=(catalog_case_id,),
        split_profile_id=None,
    )
    binding = SimpleNamespace(selection=selection)
    root = SimpleNamespace(
        condition=condition,
        binding=binding,
        case_id=catalog_case_id,
        condition_digest=condition.condition_digest,
        selection_digest=selection.selection_digest,
        seed=condition.seed,
        repeat_id=condition.repeat_id,
        split_profile_id=None,
        split_profile_digest="sha256:" + "3" * 64,
        planned_ai_unit_ids=("unit-exp5",),
        plugin_id=prepared.plugin_id,
        plugin_version=prepared.plugin_version,
        endpoint_controls=SimpleNamespace(
            provider_config_id="siliconflow",
            model_entry_id=entry.entry_id,
            provider_family=source_config.provider_family,
            provider_model_id=entry.model,
            reasoning_profile_id="nonthinking",
            model_endpoint_identity_digest=endpoint_digest,
            max_tokens=128,
            timeout_seconds=30,
            max_provider_attempts=3,
            request_controls_digest=prepared.effective_controls_digest,
        ),
        case_record_digest=digest_json(case_record),
        frozen_case=dict(case_record),
    )
    coverage = object.__new__(FormalExecutionCoverage)
    for name, value in {
        "conditions": (condition,),
        "bindings": (binding,),
        "roots": (root,),
        "root_case_filter": {condition.condition_id: (root.case_id,)},
        "source_snapshot": object(),
        "source_snapshot_digest": "sha256:" + "5" * 64,
        "condition_count": 1,
        "root_run_count": 1,
        "selection_kind": "filtered",
        "selected_first_attempt_ai_unit_count": 1,
        "provider_calls_made": 0,
        "schema_version": "tokenshare.paper_formal_execution_coverage.v1",
    }.items():
        object.__setattr__(coverage, name, value)
    record = FormalPreparedRequestRecord(
        condition=condition,
        binding=binding,
        case_id=root.case_id,
        case_record_digest=root.case_record_digest,
        planned_ai_unit_id="unit-exp5",
        sample_slot_index=0,
        base_replacement_slot=0,
        replacement_slot_ids=(0,),
        replacement_policy_id="formal_attempt_budget.v1",
        source_provider_config_digest=source_config.config_digest,
        prepared_execution_config_digest=prepared_config.config_digest,
        provider_family=source_config.provider_family,
        provider_config_id="siliconflow",
        model_entry_id=entry.entry_id,
        provider_model_id=entry.model,
        reasoning_profile_id="nonthinking",
        model_endpoint_identity_digest=endpoint_digest,
        request_max_tokens=128,
        request_timeout_seconds=30,
        request_max_provider_attempts=3,
        request_controls_digest=prepared.effective_controls_digest,
        prompt_profile_digest=digest_json(
            {
                "body_digest": prepared.body_digest,
                "prompt_profile_id": prepared.prompt_profile_id,
                "prompt_serialization_schema": prepared.prompt_serialization_schema,
            }
        ),
        provider_request_identity=provider_identity,
        prepared_request=prepared,
    )
    inventory = FormalPreparedRequestInventory(
        records=(record,),
        record_count=1,
        unique_inference_request_count=1,
        provider_calls_made=0,
        source_snapshot_digest=coverage.source_snapshot_digest,
    )
    return coverage, inventory, source_config, record


def _tracked_hard_lean_case(topic_family: str) -> dict[str, Any]:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v2.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path(
            "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
        ),
    )
    matches = tuple(
        dict(case)
        for case in catalog.lean_lemma_graph_cases
        if case["paper_difficulty"] == "hard_frontier"
        and case["topic_family"] == topic_family
    )
    assert matches
    return matches[0]


def _frozen_case(coverage: Any) -> dict[str, Any]:
    return dict(coverage.roots[0].frozen_case)


def test_deferred_exp5_ledger_factory_initializes_only_for_first_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_callbacks as callbacks

    calls: list[dict[str, Any]] = []

    class ConcreteFactory:
        inventory_digest = "sha256:" + "a" * 64

        def __call__(self, **root_context: Any) -> object:
            calls.append({"root": dict(root_context)})
            return "typed-hook"

    def build_concrete_factory(**kwargs: Any) -> ConcreteFactory:
        calls.append({"factory": dict(kwargs)})
        return ConcreteFactory()

    monkeypatch.setattr(
        callbacks,
        "PaperExp5LedgerRootCallbackFactory",
        build_concrete_factory,
    )
    prepared_inventory = object()
    coverage = object()
    configs = {"siliconflow": object()}
    deferred = DeferredPaperExp5LedgerRootCallbackFactory(
        ledger_path="ledger.sqlite3",
        full_prepared_inventory=prepared_inventory,
        coverage=coverage,
        ai_api_configs=configs,
    )

    assert calls == []
    assert deferred(condition="condition", case_id="case") == "typed-hook"
    assert deferred.inventory_digest == "sha256:" + "a" * 64
    assert calls == [
        {
            "factory": {
                "ledger_path": "ledger.sqlite3",
                "full_prepared_inventory": prepared_inventory,
                "coverage": coverage,
                "ai_api_configs": configs,
            }
        },
        {"root": {"condition": "condition", "case_id": "case"}},
    ]


def test_exp5_capability_smoke_coverage_has_its_own_typed_selection_kind() -> None:
    coverage, _inventory, _config, _record = _synthetic_exp5_formal_authority(
        domain="factorization"
    )
    object.__setattr__(coverage, "selection_kind", "exp5_capability_smoke")

    with pytest.raises(TypeError, match="source_snapshot must be a FormalPlanSnapshot"):
        coverage.__post_init__()


@pytest.mark.parametrize("topic_family", ("pure_logic", "function_set", "induction"))
def test_exp5_ledger_root_accepts_tracked_lean_case_metadata_injection(
    tmp_path: Path,
    topic_family: str,
) -> None:
    case = _tracked_hard_lean_case(topic_family)
    coverage, inventory, config, record = _synthetic_exp5_formal_authority(
        domain="lean_proof",
        case_record=case,
    )
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    runtime_condition = _condition_with_frozen_case_metadata(
        condition=record.condition,
        case=case,
    )

    callback = factory(
        condition=runtime_condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=case,
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )

    assert callable(callback)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("paper_difficulty", "medium_lemma_dag"),
        ("topic_family", "function_set"),
        ("topic_family_version", "v2"),
        ("construction_rule_id", "tampered"),
        ("oracle_package_group", "tampered"),
        ("proof_assembly_shape", "tampered"),
    ),
)
def test_exp5_ledger_root_rejects_each_injected_lean_metadata_drift(
    tmp_path: Path,
    field_name: str,
    tampered_value: str,
) -> None:
    case = _tracked_hard_lean_case("pure_logic")
    coverage, inventory, config, record = _synthetic_exp5_formal_authority(
        domain="lean_proof",
        case_record=case,
    )
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    runtime_condition = _condition_with_frozen_case_metadata(
        condition=record.condition,
        case=case,
    )
    tampered_condition = dataclass_replace(
        runtime_condition,
        **{field_name: tampered_value},
    )

    with pytest.raises(ValueError, match="root/config/request authority drift"):
        factory(
            condition=tampered_condition,
            selection=record.binding.selection,
            case_id=record.case_id,
            case=case,
            ai_api_config=config,
            request_limits=dict(config.defaults),
        )


def test_exp5_ledger_root_rejects_frozen_case_digest_drift(
    tmp_path: Path,
) -> None:
    case = _tracked_hard_lean_case("pure_logic")
    coverage, inventory, config, record = _synthetic_exp5_formal_authority(
        domain="lean_proof",
        case_record=case,
    )
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    runtime_condition = _condition_with_frozen_case_metadata(
        condition=record.condition,
        case=case,
    )
    tampered_case = {**case, "construction_seed": 999999}

    with pytest.raises(ValueError, match="root/config/request authority drift"):
        factory(
            condition=runtime_condition,
            selection=record.binding.selection,
            case_id=record.case_id,
            case=tampered_case,
            ai_api_config=config,
            request_limits=dict(config.defaults),
        )


@pytest.mark.parametrize(
    "drift_kind",
    ("case_ref", "condition", "endpoint", "selection"),
)
def test_exp5_ledger_root_rejects_runtime_authority_drift(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    context: dict[str, Any] = {
        "condition": record.condition,
        "selection": record.binding.selection,
        "case_id": record.case_id,
        "case": _frozen_case(coverage),
        "ai_api_config": config,
        "request_limits": dict(config.defaults),
    }
    if drift_kind == "case_ref":
        context["case"] = {**context["case"], "case_id": "other-case"}
    elif drift_kind == "condition":
        context["condition"] = dataclass_replace(
            record.condition,
            seed=record.condition.seed + 1,
        )
    elif drift_kind == "endpoint":
        context["condition"] = dataclass_replace(
            record.condition,
            model_endpoint_identity_digest="sha256:" + "0" * 64,
        )
    else:
        context["selection"] = SimpleNamespace(
            selection_digest="sha256:" + "0" * 64,
        )

    with pytest.raises(ValueError, match="root/config/request authority drift"):
        factory(**context)


def test_exp5_ledger_factory_rejects_selected_endpoint_authority_drift(
    tmp_path: Path,
) -> None:
    coverage, inventory, config, _record = _synthetic_exp5_formal_authority()
    coverage.roots[0].endpoint_controls.model_endpoint_identity_digest = (
        "sha256:" + "0" * 64
    )

    with pytest.raises(ValueError, match="prepared/model/provider authority drift"):
        PaperExp5LedgerRootCallbackFactory(
            ledger_path=tmp_path / "exp5-budget.sqlite3",
            full_prepared_inventory=inventory,
            coverage=coverage,
            ai_api_configs={"siliconflow": config},
        )


@pytest.mark.parametrize(
    ("domain", "runtime_task_id"),
    (
        ("factorization", "paper_factorization_exp5-case"),
        ("lean_proof", "paper_lean_exp5-case"),
    ),
)
def test_exp5_ledger_factory_binds_formal_runtime_task_identity(
    tmp_path: Path,
    domain: str,
    runtime_task_id: str,
) -> None:
    coverage, inventory, config, _record = _synthetic_exp5_formal_authority(
        domain=domain,
        prepared_case_id=runtime_task_id,
    )

    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )

    assert len(factory.inventory_rows) == 1


@pytest.mark.parametrize(
    ("domain", "runtime_task_id"),
    (
        ("unknown_domain", "paper_unknown_exp5-case"),
        ("factorization", "paper_lean_exp5-case"),
        ("lean_proof", "paper_factorization_exp5-case"),
    ),
)
def test_exp5_ledger_factory_rejects_invalid_formal_runtime_task_identity(
    tmp_path: Path,
    domain: str,
    runtime_task_id: str,
) -> None:
    coverage, inventory, config, _record = _synthetic_exp5_formal_authority(
        domain=domain,
        prepared_case_id=runtime_task_id,
    )

    with pytest.raises(
        ValueError,
        match="prepared/model/provider authority drift",
    ):
        PaperExp5LedgerRootCallbackFactory(
            ledger_path=tmp_path / "exp5-budget.sqlite3",
            full_prepared_inventory=inventory,
            coverage=coverage,
            ai_api_configs={"siliconflow": config},
        )


def test_exp5_ledger_factory_binds_selected_full_inventory_before_dispatch(
    tmp_path: Path,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    callback = factory(
        condition=_condition_with_frozen_case_metadata(
            condition=record.condition,
            case=_frozen_case(coverage),
        ),
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    prepared = record.prepared_request
    callback.after_prepared_dispatch(
        submission_id="exp5-submission",
        prepared_request=prepared,
        provider_request_identity={
            **dict(record.provider_request_identity),
            "prepared_request": prepared.provenance_dict(),
        },
        provider_family=record.provider_family,
        model=record.provider_model_id,
        entry_id=record.model_entry_id,
    )

    assert factory.serialize_roots is False
    assert len(factory.inventory_rows) == 1
    reservation = factory.ledger.list_reservations()[0]
    root_authority = callback.root_hard_limit_authority
    assert root_authority.condition_id == record.condition.condition_id
    assert root_authority.condition_digest == record.condition.condition_digest
    assert root_authority.case_id == record.case_id
    assert root_authority.selection_digest == record.binding.selection.selection_digest
    assert (
        root_authority.model_endpoint_identity_digest
        == record.model_endpoint_identity_digest
    )
    assert root_authority.inventory_digest == factory.inventory_digest
    assert root_authority.provider_attempt_count == 1
    assert root_authority.provider_attempt_count == len(coverage.roots[0].planned_ai_unit_ids)
    assert root_authority.total_tokens == sum(
        row.token_upper_bound for row in factory.ledger.list_reservations()
    )
    assert root_authority.total_cost_estimate == sum(
        (row.cost_upper_bound for row in factory.ledger.list_reservations()),
        start=Decimal("0"),
    )
    assert root_authority.currency == "CNY"
    assert reservation.state == "reserved"
    assert reservation.inventory_digest == factory.inventory_digest
    audit = factory.audit_usage()
    expected = {record.condition.condition_id: 1}
    empty = {record.condition.condition_id: 0}
    assert audit.expected_provider_calls_by_condition == expected
    assert audit.current_provider_calls_by_condition == empty
    assert audit.total_provider_calls_by_condition == empty
    assert audit.current_terminal_provider_calls_by_condition == empty
    assert audit.total_terminal_provider_calls_by_condition == empty
    canonical_slot = (
        record.condition.condition_id,
        record.case_id,
        record.planned_ai_unit_id,
    )
    assert audit.prepared_canonical_slots == (canonical_slot,)
    assert audit.current_terminal_slots == ()
    assert audit.total_terminal_slots == ()


@pytest.mark.parametrize(
    "field_name",
    (
        "condition_id",
        "condition_digest",
        "case_id",
        "selection_digest",
        "model_endpoint_identity_digest",
        "inventory_digest",
    ),
)
def test_exp5_typed_root_hard_limit_authority_rejects_identity_drift(
    tmp_path: Path,
    field_name: str,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    callback = factory(
        condition=_condition_with_frozen_case_metadata(
            condition=record.condition,
            case=_frozen_case(coverage),
        ),
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    drifted = dataclass_replace(
        callback.root_hard_limit_authority,
        **{
            field_name: (
                "drifted-id"
                if field_name in {"condition_id", "case_id"}
                else "sha256:" + "f" * 64
            )
        },
    )

    with pytest.raises(ValueError, match="root hard-limit authority"):
        callback.bind_root_hard_limit_authority(
            condition=record.condition,
            selection=record.binding.selection,
            case_id=record.case_id,
            inventory_digest=factory.inventory_digest,
            authority=drifted,
        )


@pytest.mark.parametrize(
    ("domain", "runtime_task_id"),
    (
        ("factorization", "paper_factorization_exp5-case"),
        ("lean_proof", "paper_lean_exp5-case"),
    ),
)
def test_exp5_ledger_root_binds_source_config_separately_from_prepared_config(
    tmp_path: Path,
    domain: str,
    runtime_task_id: str,
) -> None:
    coverage, inventory, source_config, record = (
        _synthetic_exp5_formal_authority(
            domain=domain,
            prepared_case_id=runtime_task_id,
            distinct_config_layers=True,
        )
    )
    assert source_config.config_digest == record.source_provider_config_digest
    assert source_config.config_digest != record.prepared_execution_config_digest
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": source_config},
    )

    callback = factory(
        condition=_condition_with_frozen_case_metadata(
            condition=record.condition,
            case=_frozen_case(coverage),
        ),
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=source_config,
        request_limits=dict(source_config.defaults),
    )

    assert callable(callback)


def test_exp5_ledger_root_rejects_source_config_drift(
    tmp_path: Path,
) -> None:
    coverage, inventory, source_config, record = (
        _synthetic_exp5_formal_authority(distinct_config_layers=True)
    )
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": source_config},
    )

    with pytest.raises(ValueError, match="root/config/request authority drift"):
        factory(
            condition=record.condition,
            selection=record.binding.selection,
            case_id=record.case_id,
            case=_frozen_case(coverage),
            ai_api_config=SimpleNamespace(
                config_digest=record.prepared_execution_config_digest
            ),
            request_limits=dict(source_config.defaults),
        )


def test_exp5_ledger_factory_rejects_prepared_config_drift(
    tmp_path: Path,
) -> None:
    coverage, inventory, source_config, record = (
        _synthetic_exp5_formal_authority(distinct_config_layers=True)
    )
    drifted_record = dataclass_replace(
        record,
        prepared_execution_config_digest=source_config.config_digest,
    )
    drifted_inventory = dataclass_replace(
        inventory,
        records=(drifted_record,),
    )

    with pytest.raises(
        ValueError,
        match="prepared/model/provider authority drift",
    ):
        PaperExp5LedgerRootCallbackFactory(
            ledger_path=tmp_path / "exp5-budget.sqlite3",
            full_prepared_inventory=drifted_inventory,
            coverage=coverage,
            ai_api_configs={"siliconflow": source_config},
        )


@pytest.mark.parametrize(
    ("field_name", "wrong_value"),
    (
        ("provider_family", "deepseek"),
        ("model", "wrong-model"),
        ("entry_id", "wrong-entry"),
        ("provider_request_identity", {"schema_version": "wrong"}),
    ),
)
def test_exp5_ledger_factory_rejects_runtime_identity_drift_before_transport(
    tmp_path: Path,
    field_name: str,
    wrong_value: object,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    callback = factory(
        condition=record.condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    prepared = record.prepared_request
    context = {
        "submission_id": "exp5-mismatch",
        "prepared_request": prepared,
        "provider_request_identity": {
            **dict(record.provider_request_identity),
            "prepared_request": prepared.provenance_dict(),
        },
        "provider_family": record.provider_family,
        "model": record.provider_model_id,
        "entry_id": record.model_entry_id,
    }
    context[field_name] = wrong_value

    with pytest.raises(ValueError, match="identity drift before transport"):
        callback.after_prepared_dispatch(**context)

    assert factory.ledger.list_reservations() == ()


@pytest.mark.parametrize("drift_kind", ("body", "config", "request_controls"))
def test_exp5_ledger_factory_rejects_body_config_and_request_control_drift(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    runtime_config: object = config
    request_limits = dict(config.defaults)
    if drift_kind == "config":
        runtime_config = SimpleNamespace(config_digest="sha256:" + "0" * 64)
    if drift_kind == "request_controls":
        request_limits["max_tokens"] = int(request_limits["max_tokens"]) + 1
    if drift_kind != "body":
        with pytest.raises(ValueError, match="root/config/request authority drift"):
            factory(
                condition=record.condition,
                selection=record.binding.selection,
                case_id=record.case_id,
                case=_frozen_case(coverage),
                ai_api_config=runtime_config,
                request_limits=request_limits,
            )
        assert factory.ledger.list_reservations() == ()
        return

    callback = factory(
        condition=record.condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=request_limits,
    )
    prepared = record.prepared_request
    altered = PreparedOutboundRequestFactory.prepare(
        body_obj={
            **dict(prepared.body_obj),
            "messages": [{"role": "user", "content": "different exact body"}],
        },
        base_url=prepared.normalized_absolute_endpoint,
        endpoint="",
        provider_config_digest=prepared.provider_config_digest,
        entry_id=prepared.entry_id,
        configured_model=prepared.configured_model,
        effective_controls_digest=prepared.effective_controls_digest,
        plugin_id=prepared.plugin_id,
        plugin_version=prepared.plugin_version,
        prompt_profile_id=prepared.prompt_profile_id,
        prompt_serialization_schema=prepared.prompt_serialization_schema,
        body_serialization_schema=prepared.body_serialization_schema,
        case_id=prepared.case_id,
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=prepared.sample_slot_index,
        replacement_slot=prepared.replacement_slot,
    )
    with pytest.raises(ValueError, match="identity drift before transport"):
        callback.after_prepared_dispatch(
            submission_id="exp5-body-drift",
            prepared_request=altered,
            provider_request_identity={
                **dict(record.provider_request_identity),
                "prepared_request": altered.provenance_dict(),
            },
            provider_family=record.provider_family,
            model=record.provider_model_id,
            entry_id=record.model_entry_id,
        )
    assert factory.ledger.list_reservations() == ()


def test_exp5_ledger_resume_marks_intent_ambiguous_without_current_dispatch(
    tmp_path: Path,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    ledger_path = tmp_path / "exp5-budget.sqlite3"
    initial = PaperExp5LedgerRootCallbackFactory(
        ledger_path=ledger_path,
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    callback = initial(
        condition=record.condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    prepared = record.prepared_request
    context = {
        "submission_id": "exp5-intent",
        "prepared_request": prepared,
        "provider_request_identity": {
            **dict(record.provider_request_identity),
            "prepared_request": prepared.provenance_dict(),
        },
        "provider_family": record.provider_family,
        "model": record.provider_model_id,
        "entry_id": record.model_entry_id,
    }
    callback.after_prepared_dispatch(**context)
    callback.before_provider_dispatch(**context)

    resumed = PaperExp5LedgerRootCallbackFactory(
        ledger_path=ledger_path,
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    resumed_callback = resumed(
        condition=record.condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    resume_store = ArtifactStore(tmp_path / "resume-artifacts")
    with pytest.raises(RuntimeError, match="reconciled ambiguous"):
        resumed_callback.after_prepared_dispatch(
            **{
                **context,
                "submission_id": "exp5-resume",
                "artifact_store": resume_store,
            }
        )

    audit = resumed.audit_usage()
    assert audit.current_provider_calls == 0
    assert audit.total_provider_calls == 1
    assert audit.ambiguous_count == 1
    assert audit.current_provider_calls_by_condition == {
        record.condition.condition_id: 0
    }
    assert audit.total_provider_calls_by_condition == {
        record.condition.condition_id: 1
    }
    assert audit.total_terminal_provider_calls_by_condition == {
        record.condition.condition_id: 0
    }
    assert audit.total_spend is None
    assert audit.total_spend_missing_reason == "exp5_ledger_ambiguous"


def test_exp5_ledger_missing_usage_keeps_upper_bound_and_reports_missing(
    tmp_path: Path,
) -> None:
    coverage, inventory, config, record = _synthetic_exp5_formal_authority()
    factory = PaperExp5LedgerRootCallbackFactory(
        ledger_path=tmp_path / "exp5-budget.sqlite3",
        full_prepared_inventory=inventory,
        coverage=coverage,
        ai_api_configs={"siliconflow": config},
    )
    callback = factory(
        condition=record.condition,
        selection=record.binding.selection,
        case_id=record.case_id,
        case=_frozen_case(coverage),
        ai_api_config=config,
        request_limits=dict(config.defaults),
    )
    prepared = record.prepared_request
    context = {
        "submission_id": "exp5-missing-usage",
        "prepared_request": prepared,
        "provider_request_identity": {
            **dict(record.provider_request_identity),
            "prepared_request": prepared.provenance_dict(),
        },
        "provider_family": record.provider_family,
        "model": record.provider_model_id,
        "entry_id": record.model_entry_id,
    }
    callback.after_prepared_dispatch(**context)
    callback.before_provider_dispatch(**context)
    row = factory.inventory_rows[0]
    factory.ledger.publish_terminal(
        factory.inventory_digest,
        row.inventory_entry_id,
        terminal_ref="artifact-exp5-missing-usage",
        terminal_kind="provider_failure",
    )
    settled = factory.ledger.reconcile_terminal(
        factory.inventory_digest,
        row.inventory_entry_id,
        usage=None,
    )

    audit = factory.audit_usage()
    assert settled.usage_missing is True
    assert settled.cost_estimate == settled.cost_upper_bound
    assert audit.current_provider_calls == 1
    assert audit.current_spend is None
    assert audit.current_spend_missing_reason == "exp5_ledger_usage_missing"
    assert audit.cost_upper_bound_at_risk == settled.cost_upper_bound
    assert audit.current_terminal_provider_calls_by_condition == {
        record.condition.condition_id: 1
    }
    assert audit.total_terminal_provider_calls_by_condition == {
        record.condition.condition_id: 1
    }


def test_online_callback_settles_success_and_provider_failure_from_persisted_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()

    success_ledger, success_request = _callback_budget(tmp_path / "success")
    success_callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=success_ledger,
        reservation_request_resolver=lambda _context: success_request,
    )
    success = _run_persisted_online_attempt(
        ArtifactStore(tmp_path / "success" / "artifacts"),
        callback=success_callback,
        submission_id="attempt-success",
        submitted_at="2026-08-03T00:00:00Z",
    )
    success_row = success_ledger.get_reservation(
        success_request.inventory_digest,
        success_request.inventory_entry_id,
    )
    assert success_row.state == "settled"
    assert success_row.terminal_kind == "success"
    assert success_row.charged_tokens == 24
    assert success.raw_or_failure_ref.artifact_ref.artifact_type == "RawModelOutput"

    failure_ledger, failure_request = _callback_budget(tmp_path / "failure")
    failure_store = ArtifactStore(tmp_path / "failure" / "artifacts")
    failure_callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=failure_ledger,
        reservation_request_resolver=lambda _context: failure_request,
    )
    request = dataclass_replace(
        make_ai_request(failure_store, request_id="request-attempt-failure"),
        attempt_id="attempt-failure",
        unit_id="unit-1",
    )
    submission = AIAPIExecutor(
        executor_id="task27-official-failure-callback",
        executor_version="0.1.0",
        artifact_store=failure_store,
        config=load_ai_api_config(_single_entry_config()),
        transport=FakeSiliconFlowTransport(
            [FakeProviderResponse(status_code=503, body={"error": "unavailable"})]
        ),
        post_raw_output_hook=failure_callback,
    ).execute(
        request,
        submission_id="attempt-failure",
        submitted_at="2026-08-03T00:00:01Z",
    )
    failure = failure_callback.require_capture("attempt-failure")
    failure_row = failure_ledger.get_reservation(
        failure_request.inventory_digest,
        failure_request.inventory_entry_id,
    )
    assert submission.raw_output_ref is None
    assert failure_row.state == "settled"
    assert failure_row.terminal_kind == "provider_failure"
    assert failure_row.usage_missing is True
    assert failure.raw_or_failure_ref.artifact_ref.artifact_type == "AIProviderFailure"
    assert failure.usage_ref.artifact_ref.artifact_type == "AIProviderTerminalUsage"


def test_online_callback_rejects_explicit_provider_count_provenance_drift(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    request = make_ai_request(store, request_id="provider-count-drift")
    raw_ref = store.save_json(
        {"raw": "candidate"},
        artifact_id="provider-count-raw",
        artifact_type="RawModelOutput",
        artifact_schema_id="test.raw",
        artifact_schema_version="v1",
        source={"test": "provider-count"},
        metadata={},
        created_at="2026-08-12T00:00:00Z",
    )
    provenance_ref = store.save_json(
        {
            "attempts": [{"provider_request_identity": {"entry_id": "sf_qwen"}}],
            "provider_attempt_count": 0,
        },
        artifact_id="provider-count-provenance",
        artifact_type="AIProviderResponseProvenance",
        artifact_schema_id="phase7.ai_provider_response_provenance",
        artifact_schema_version="v1",
        source={"test": "provider-count"},
        metadata={},
        created_at="2026-08-12T00:00:00Z",
    )
    usage_ref = store.save_json(
        {"usage_summary": {"provider_attempt_count": 1}},
        artifact_id="provider-count-usage",
        artifact_type="AIProviderResponseUsage",
        artifact_schema_id="phase7.ai_provider_response_usage",
        artifact_schema_version="v1",
        source={"test": "provider-count"},
        metadata={},
        created_at="2026-08-12T00:00:00Z",
    )
    callback = PaperOnlineProviderEvidenceCallback(
        attempt_ordinal=0,
        scope_identity={"scope_kind": "exp5_online"},
    )

    with pytest.raises(ValueError, match="provider attempt count evidence drift"):
        callback(
            artifact_store=store,
            request=request,
            submission_id="provider-count-drift",
            submitted_at="2026-08-12T00:00:00Z",
            raw_output_ref=raw_ref,
            provenance_ref=provenance_ref,
            usage_ref=usage_ref,
            provider_family="siliconflow",
            model="model",
            entry_id="sf_qwen",
        )


def test_online_callback_releases_pre_intent_abort_and_reconciles_crash_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = freeze_paper_online_checks_plan()
    ledger, reservation = _callback_budget(tmp_path)
    callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=ledger,
        reservation_request_resolver=lambda _context: reservation,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    monkeypatch.delenv("SILICONFLOW_API_KEY_A", raising=False)
    request = dataclass_replace(
        make_ai_request(store, request_id="request-missing-secret"),
        attempt_id="attempt-missing-secret",
        unit_id="unit-1",
    )
    submission = AIAPIExecutor(
        executor_id="task27-official-pre-intent-abort",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(_single_entry_config()),
        transport=FakeSiliconFlowTransport([]),
        post_raw_output_hook=callback,
    ).execute(
        request,
        submission_id="attempt-missing-secret",
        submitted_at="2026-08-03T00:00:00Z",
    )
    assert submission.error is not None
    assert ledger.list_reservations() == ()

    ledger.reserve(reservation)
    assert callback.reconcile_budget_resume(
        reservation=reservation,
        artifact_store=store,
    ) == "released"
    assert ledger.list_reservations() == ()

    ledger.reserve(reservation)
    ledger.mark_dispatch_intent(
        reservation.inventory_digest,
        reservation.inventory_entry_id,
    )
    assert callback.reconcile_budget_resume(
        reservation=reservation,
        artifact_store=store,
    ) == "ambiguous"
    assert ledger.get_reservation(
        reservation.inventory_digest,
        reservation.inventory_entry_id,
    ).state == "ambiguous"

    terminal_ledger, terminal_reservation = _callback_budget(tmp_path / "terminal")
    terminal_store = ArtifactStore(tmp_path / "terminal" / "artifacts")
    terminal_callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=terminal_ledger,
        reservation_request_resolver=lambda _context: terminal_reservation,
    )
    usage_ref = terminal_store.save_json(
        {
            "schema_version": "phase7.ai_provider_response_usage.v1",
            "usage_summary": {"prompt_tokens": 3, "completion_tokens": 4},
        },
        artifact_id="resume-usage",
        artifact_type="AIProviderResponseUsage",
        artifact_schema_id="phase7.ai_provider_response_usage",
        artifact_schema_version="v1",
        source={"test": "callback-resume"},
        metadata={},
        created_at="2026-08-03T00:00:01Z",
    )
    callback_ref = terminal_store.save_json(
        {
            "schema_version": "tokenshare.paper_online_callback_record.v1",
            "object_refs": {"usage": usage_ref.to_dict()},
        },
        artifact_id="resume-callback",
        artifact_type="PaperOnlineCallbackRecord",
        artifact_schema_id="tokenshare.paper_online_callback_record",
        artifact_schema_version="v1",
        source={"test": "callback-resume"},
        metadata={},
        created_at="2026-08-03T00:00:01Z",
    )
    terminal_ledger.reserve(terminal_reservation)
    terminal_ledger.mark_dispatch_intent(
        terminal_reservation.inventory_digest,
        terminal_reservation.inventory_entry_id,
    )
    terminal_ledger.publish_terminal(
        terminal_reservation.inventory_digest,
        terminal_reservation.inventory_entry_id,
        terminal_ref=callback_ref.artifact_id,
        terminal_kind="success",
    )
    assert terminal_callback.reconcile_budget_resume(
        reservation=terminal_reservation,
        artifact_store=terminal_store,
    ) == "settled"


def test_online_callback_exception_accounting_preserves_settled_provider_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    ledger, reservation = _callback_budget(tmp_path)
    callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=ledger,
        reservation_request_resolver=lambda _context: reservation,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    _run_persisted_online_attempt(
        store,
        callback=callback,
        submission_id="exception-after-provider",
        submitted_at="2026-08-12T00:00:00Z",
    )

    accounting = callback.reconcile_exception_accounting(
        artifact_store=store,
    )

    assert accounting.provider_attempt_count == 1
    assert accounting.total_tokens == 24
    assert accounting.total_cost_estimate == 0.0
    assert accounting.cost_estimate_currency == "CNY"
    assert accounting.cost_estimate_status == "single_currency_estimate"
    assert accounting.usage_missing_count == 0
    assert accounting.ambiguous_count == 0
    assert ledger.get_reservation(
        reservation.inventory_digest,
        reservation.inventory_entry_id,
    ).state == "settled"


def test_online_callback_exception_accounting_releases_preprovider_reservation(
    tmp_path: Path,
) -> None:
    plan = freeze_paper_online_checks_plan()
    ledger, reservation = _callback_budget(tmp_path)
    callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=ledger,
        reservation_request_resolver=lambda _context: reservation,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    callback.after_prepared_dispatch(submission_id="exception-before-provider")

    accounting = callback.reconcile_exception_accounting(
        artifact_store=store,
    )

    assert accounting.provider_attempt_count == 0
    assert accounting.total_tokens == 0
    assert accounting.total_cost_estimate == 0.0
    assert accounting.cost_estimate_status == "explicit_zero_preprovider"
    assert accounting.usage_missing_count == 0
    assert accounting.ambiguous_count == 0
    assert ledger.list_reservations() == ()


def test_online_callback_exception_accounting_marks_post_intent_without_terminal_ambiguous(
    tmp_path: Path,
) -> None:
    plan = freeze_paper_online_checks_plan()
    ledger, reservation = _callback_budget(tmp_path)
    callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        plan.exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=ledger,
        reservation_request_resolver=lambda _context: reservation,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    context = {"submission_id": "exception-after-intent"}
    callback.after_prepared_dispatch(**context)
    callback.before_provider_dispatch(**context)

    accounting = callback.reconcile_exception_accounting(
        artifact_store=store,
    )

    assert accounting.provider_attempt_count == 1
    assert accounting.total_tokens is None
    assert accounting.total_cost_estimate is None
    assert accounting.cost_estimate_currency == "CNY"
    assert accounting.cost_estimate_status == "usage_missing"
    assert accounting.usage_missing_count == 1
    assert accounting.ambiguous_count == 1
    assert ledger.get_reservation(
        reservation.inventory_digest,
        reservation.inventory_entry_id,
    ).state == "ambiguous"


def test_online_callback_exception_accounting_preserves_exact_decimal_across_settled_and_ambiguous(
    tmp_path: Path,
) -> None:
    settled_cost = Decimal("0.1000000000000000000000000001")
    ambiguous_cost = Decimal("0.2000000000000000000000000002")
    ledger, reservations = _callback_budget_with_exact_costs(
        tmp_path,
        cost_upper_bounds=(settled_cost, ambiguous_cost),
    )
    reservation_by_submission = dict(
        zip(("settled", "ambiguous"), reservations, strict=True)
    )
    callback = PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
        freeze_paper_online_checks_plan().exp3_root_refs[0],
        attempt_ordinal=0,
        budget_ledger=ledger,
        reservation_request_resolver=lambda context: reservation_by_submission[
            str(context["submission_id"])
        ],
    )
    for submission_id in reservation_by_submission:
        callback.after_prepared_dispatch(submission_id=submission_id)
        callback.before_provider_dispatch(submission_id=submission_id)
    settled = reservations[0]
    ledger.publish_terminal(
        settled.inventory_digest,
        settled.inventory_entry_id,
        terminal_ref="settled-terminal",
        terminal_kind="success",
    )
    ledger.reconcile_terminal(
        settled.inventory_digest,
        settled.inventory_entry_id,
        usage=ProviderUsage(input_tokens=1, output_tokens=0),
    )

    accounting = callback.reconcile_exception_accounting(
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    exact_root_cost = settled_cost + ambiguous_cost
    assert accounting.provider_attempt_count == 2
    assert accounting.total_cost_estimate is None
    assert accounting.ambiguous_count == 1
    assert accounting.state_counts == {
        "released": 0,
        "ambiguous": 1,
        "settled": 1,
    }
    assert isinstance(accounting.conservative_total_cost_estimate, Decimal)
    assert accounting.conservative_total_cost_estimate == exact_root_cost


def test_online_root_factory_maps_canonical_runner_roots_to_authoritative_refs(
    tmp_path: Path,
) -> None:
    plan = freeze_paper_online_checks_plan()
    ledger, _reservation = _callback_budget(tmp_path)
    factory = PaperOnlineRootCallbackFactory(
        budget_ledger=ledger,
        token_upper_bound=332_768,
        cost_upper_bound=Decimal("1.898304"),
        prompt_admission_profile_digest="sha256:" + "a" * 64,
    )

    factor = factory(
        condition=SimpleNamespace(worker_count=10, repeat_id=0, fault_type="none"),
        case_id=plan.capability_calls[0].case_id,
        ai_api_config=object(),
    )
    exp2_ref = plan.exp2_condition_refs[0]
    exp2 = factory(
        condition=SimpleNamespace(
            worker_count=exp2_ref.worker_count,
            repeat_id=exp2_ref.repeat_id,
            fault_type="none",
        ),
        case_id=exp2_ref.case_id,
        ai_api_config=object(),
    )
    exp3_ref = plan.exp3_root_refs[0]
    exp3 = factory(
        condition=SimpleNamespace(
            worker_count=10,
            repeat_id=exp3_ref.repeat_id,
            fault_type=exp3_ref.check_kind,
        ),
        case_id=exp3_ref.case_id,
        ai_api_config=object(),
    )

    assert factor._scope_identity["scope_kind"] == "capability_domain"
    assert exp2._scope_identity["condition_id"] == exp2_ref.condition_id
    assert exp3._scope_identity["scope_kind"] == "exp3_online_root"
    assert factory.callbacks == (factor, exp2, exp3)


def test_exp1_strategy_consumes_complete_frozen_order() -> None:
    calls: list[str] = []

    result = run_exp1_normal_strategy(
        ordered_case_ids=("case-c", "case-a", "case-b"),
        execute_case=lambda case_id, worker_id: calls.append(case_id) or case_id,
    )

    assert calls == ["case-c", "case-a", "case-b"]
    assert result.ordered_case_ids == ("case-c", "case-a", "case-b")
    assert result.outcomes == ("case-c", "case-a", "case-b")
    assert result.metrics["observed_max_parallel_slots"] is None
    assert result.metrics["runtime_evidence_status"] == "unavailable"
    assert (
        result.metrics["runtime_evidence_unavailable_reason"]
        == "missing_worker_execution_facts"
    )


def test_scheduler_uses_persisted_runtime_observation_and_retry_attempt_identity() -> None:
    runtime_observation = {
        "schema_version": "tokenshare.protocol_runtime_observation.v1",
        "run_id": "run-case-1",
        "runtime_started_at": "2026-07-20T00:00:00.000Z",
        "runtime_ended_at": "2026-07-20T00:00:00.250Z",
        "runtime_wall_clock_ms": 250.0,
        "worker_execution_facts": [
            {
                "attempt_id": "attempt-root",
                "execution_index": 1,
                "unit_id": "root",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.010Z",
                "dependencies": [],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-a-failed",
                "execution_index": 2,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.010Z",
                "ended_at": "2026-07-20T00:00:00.110Z",
                "dependencies": ["root"],
                "result_kind": "executor_error",
            },
            {
                "attempt_id": "attempt-b",
                "execution_index": 3,
                "unit_id": "child-b",
                "started_at": "2026-07-20T00:00:00.010Z",
                "ended_at": "2026-07-20T00:00:00.160Z",
                "dependencies": ["root"],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-a-retry",
                "execution_index": 4,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.110Z",
                "ended_at": "2026-07-20T00:00:00.210Z",
                "dependencies": ["root"],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-merge",
                "execution_index": 5,
                "unit_id": "merge",
                "started_at": "2026-07-20T00:00:00.210Z",
                "ended_at": "2026-07-20T00:00:00.230Z",
                "dependencies": ["child-a", "child-b"],
                "result_kind": "succeeded",
            },
        ],
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=lambda case_id, worker_count: {
            "case_id": case_id,
            "adapter_result": {
                "run_evidence": {
                    "protocol_runtime": {
                        "runtime_observation": runtime_observation,
                    }
                }
            },
            "provider_latency_ms": 350,
            "provider_error_kind": "executor_error",
        },
    )

    assert result.metrics["runtime_evidence_status"] == "complete"
    assert result.metrics["runtime_evidence_unavailable_reason"] is None
    assert result.metrics["condition_started_at"] == "2026-07-20T00:00:00.000Z"
    assert result.metrics["condition_ended_at"] == "2026-07-20T00:00:00.250Z"
    assert result.metrics["wall_clock_ms"] == 250
    assert result.metrics["observed_max_parallel_slots"] == 2
    assert result.metrics["critical_path_ms"] == 230
    assert result.metrics["throughput_completed_units_per_second"] == 16
    assert result.metrics["critical_path_evidence_status"] == "complete"
    assert result.metrics["critical_path_unavailable_reason"] is None


def test_scheduler_does_not_invent_critical_path_without_dependency_evidence() -> None:
    runtime_observation = {
        "schema_version": "tokenshare.protocol_runtime_observation.v1",
        "run_id": "run-case-1",
        "runtime_started_at": "2026-07-20T00:00:00.000Z",
        "runtime_ended_at": "2026-07-20T00:00:00.250Z",
        "runtime_wall_clock_ms": 250.0,
        "worker_execution_facts": [
            {
                "attempt_id": "attempt-a",
                "execution_index": 1,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.200Z",
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-b",
                "execution_index": 2,
                "unit_id": "child-b",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.150Z",
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-merge",
                "execution_index": 3,
                "unit_id": "merge",
                "started_at": "2026-07-20T00:00:00.200Z",
                "ended_at": "2026-07-20T00:00:00.250Z",
                "result_kind": "succeeded",
            },
        ],
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=lambda case_id, worker_count: {
            "case_id": case_id,
            "adapter_result": {
                "run_evidence": {
                    "protocol_runtime": {
                        "runtime_observation": runtime_observation,
                    }
                }
            },
        },
    )

    assert result.metrics["runtime_evidence_status"] == "complete"
    assert result.metrics["wall_clock_ms"] == 250
    assert result.metrics["observed_max_parallel_slots"] == 2
    assert result.metrics["throughput_completed_units_per_second"] == 12
    assert result.metrics["critical_path_ms"] is None
    assert result.metrics["critical_path_evidence_status"] == "unavailable"
    assert (
        result.metrics["critical_path_unavailable_reason"]
        == "missing_protocol_dependency_evidence"
    )


def test_scheduler_marks_executed_case_without_runtime_facts_as_missing() -> None:
    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=10,
        execute_case=lambda case_id, worker_count: {"case_id": case_id},
    )

    assert result.metrics["runtime_evidence_status"] == "unavailable"
    assert (
        result.metrics["runtime_evidence_unavailable_reason"]
        == "missing_worker_execution_facts"
    )
    for field_name in (
        "observed_max_parallel_slots",
        "condition_started_at",
        "condition_ended_at",
        "wall_clock_ms",
        "critical_path_ms",
        "throughput_completed_units_per_second",
    ):
        assert result.metrics[field_name] is None


def test_exp2_scheduler_delegates_capacity_to_one_root_runtime() -> None:
    capacities: list[int] = []

    def execute(case_id: str, worker_capacity: int) -> dict[str, Any]:
        capacities.append(worker_capacity)
        return {
            "case_id": case_id,
            "provider_latency_ms": 28,
            "provider_error_kind": "rate_limited",
            "runtime_records": (
                {
                    "unit_id": "child-left",
                    "started_at": "2026-07-20T00:00:00.000Z",
                    "ended_at": "2026-07-20T00:00:00.200Z",
                    "dependencies": [],
                    "result_kind": "succeeded",
                },
                {
                    "unit_id": "child-right",
                    "started_at": "2026-07-20T00:00:00.000Z",
                    "ended_at": "2026-07-20T00:00:00.150Z",
                    "dependencies": [],
                    "result_kind": "succeeded",
                },
                {
                    "unit_id": "merge",
                    "started_at": "2026-07-20T00:00:00.200Z",
                    "ended_at": "2026-07-20T00:00:00.250Z",
                    "dependencies": ["child-left", "child-right"],
                    "result_kind": "succeeded",
                },
            ),
        }

    parallel = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=execute,
    )

    assert capacities == [2]
    assert parallel.metrics["observed_max_parallel_slots"] == 2
    assert parallel.ordered_case_ids == ("case-1",)
    assert tuple(item["case_id"] for item in parallel.outcomes) == parallel.ordered_case_ids
    assert parallel.events == ()
    assert parallel.metrics["wall_clock_ms"] == 250
    assert parallel.metrics["critical_path_ms"] == 250
    assert parallel.metrics["provider_latency_sum_ms"] == 28
    assert parallel.metrics["provider_latency_sum_ms"] != parallel.metrics["wall_clock_ms"]
    assert parallel.metrics["provider_error_count"] == 1


def test_online_scheduler_releases_each_full_outcome_after_callback() -> None:
    class TrackedOutcome:
        live = 0
        max_live = 0

        def __init__(self, case_id: str) -> None:
            self.case_id = case_id
            self.provider_attempt_count = 0
            self.provider_latency_ms = None
            self.provider_error_kind = None
            self.runtime_records = ()
            type(self).live += 1
            type(self).max_live = max(type(self).max_live, type(self).live)

        def __del__(self) -> None:
            type(self).live -= 1

    checkpointed: list[str] = []
    result = run_scheduled_cases(
        ordered_case_ids=tuple(f"case-{index}" for index in range(20)),
        worker_count=1,
        execute_case=lambda case_id, worker_count: TrackedOutcome(case_id),
        on_case_complete=lambda case_id, outcome: checkpointed.append(case_id),
        retain_outcomes=False,
    )

    assert checkpointed == list(result.ordered_case_ids)
    assert result.outcomes == ()
    assert TrackedOutcome.max_live == 1
    assert TrackedOutcome.live == 0


def test_scheduler_provider_latency_is_null_when_required_latency_is_missing() -> None:
    outcomes = {
        "case-complete": {
            "case_id": "case-complete",
            "provider_attempt_count": 1,
            "provider_latency_ms": 28,
        },
        "case-missing": {
            "case_id": "case-missing",
            "provider_attempt_count": 1,
            "provider_latency_ms": None,
        },
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-complete", "case-missing"),
        worker_count=1,
        execute_case=lambda case_id, _worker_count: outcomes[case_id],
    )

    assert result.metrics["provider_latency_sum_ms"] is None
    assert result.metrics["provider_latency_evidence_status"] == "incomplete"
    assert (
        result.metrics["provider_latency_unavailable_reason"]
        == "missing_provider_latency_evidence"
    )


def test_scheduler_zero_call_latency_is_explicitly_not_applicable() -> None:
    result = run_scheduled_cases(
        ordered_case_ids=("case-pre-provider-error",),
        worker_count=1,
        execute_case=lambda case_id, _worker_count: {
            "case_id": case_id,
            "provider_attempt_count": 0,
            "provider_latency_ms": 0,
            "provider_error_kind": "executor_error",
        },
    )

    assert result.metrics["provider_latency_sum_ms"] == 0
    assert result.metrics["provider_latency_evidence_status"] == "not_applicable"
    assert (
        result.metrics["provider_latency_unavailable_reason"]
        == "no_provider_attempts"
    )


def test_exp2_scheduler_reports_unsupported_level_without_executing() -> None:
    calls: list[str] = []

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=64,
        supported_worker_counts=(1, 2, 4),
        execute_case=lambda case_id, worker_id: calls.append(case_id),
    )

    assert result.status == "unsupported_worker_level"
    assert result.outcomes == ()
    assert calls == []


def test_exp3_post_ai_fault_is_injected_after_raw_and_uses_fixed_identity() -> None:
    order: list[str] = []
    original = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-original",
        raw_output_ref={"artifact_id": "raw-original"},
        provenance_ref={"artifact_id": "provenance-original"},
    )
    replacement = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-replacement",
        raw_output_ref={"artifact_id": "raw-replacement"},
        provenance_ref={"artifact_id": "provenance-replacement"},
    )

    def inject(attempt: _Attempt, fault_type: str) -> dict[str, Any]:
        assert attempt.raw_output_ref is not None
        assert attempt.provenance_ref is not None
        order.append("inject")
        return {
            "fault_type": fault_type,
            "original_output_ref": attempt.raw_output_ref,
            "mutated_output_ref": {"artifact_id": "mutated-output"},
            "detected": True,
            "requires_replacement": True,
        }

    def replace(attempt: _Attempt) -> _Attempt:
        order.append("replace")
        return replacement

    result = run_exp3_post_ai_strategy(
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="false_negative",
        inject_fault=inject,
        execute_replacement=replace,
        approved_identity={
            "provider": "siliconflow",
            "model": "zai-org/GLM-5.2",
            "entry_id": "glm_5_2_exp1_baseline",
        },
    )

    assert order == ["inject", "replace"]
    assert result.fault_records[0]["original_output_ref"]["artifact_id"] == "raw-original"
    assert result.fault_records[0]["mutated_output_ref"]["artifact_id"] == "mutated-output"
    assert result.replacement_attempts == (replacement,)
    assert result.events[0]["event_type"] == "EXPERIMENT_PROVIDER_RAW_OBSERVED"
    assert result.events[-1]["event_type"] == "EXPERIMENT_REPLACEMENT_ACCEPTED"


def test_ai_executor_post_raw_hook_runs_after_persistence_and_before_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="formal-post-raw-hook")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "formal-hook-response",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": "original"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                },
            )
        ]
    )
    order: list[str] = []

    def post_raw_output_hook(**context):
        assert store.verify(context["raw_output_ref"])
        assert store.verify(context["provenance_ref"])
        assert store.verify(context["usage_ref"])
        order.append("fault_hook")
        return {"content_text": "mutated-before-parser"}

    def parser(raw_text: str):
        order.append("parser")
        assert raw_text == "mutated-before-parser"
        raise ValueError("expected parser stop")

    executor = AIAPIExecutor(
        executor_id="formal-hook-test",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
        post_raw_output_hook=post_raw_output_hook,
    )

    submission = executor.execute(
        request,
        submission_id="formal-hook-submission",
        submitted_at="2026-07-20T00:00:02Z",
    )

    assert submission.result_kind == "parse_failed"
    assert order == ["fault_hook", "parser"]


def test_exp3_worker_death_strategy_projects_runtime_evidence_without_fake_protocol(
    tmp_path: Path,
) -> None:
    result = run_exp3_worker_death_strategy(
        artifact_root=tmp_path,
        condition_id="condition-worker-death",
        repeat_id=0,
        task_id="case-1",
        ai_unit_ids=("unit-1",),
        dead_worker_count=1,
        kill_point=WorkerDeathKillPoint.PROGRESS_25,
        started_at="2026-07-20T00:00:00Z",
        process_tick_seconds=0.005,
        runtime_observations=(
            {
                "worker_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": "attempt_initial",
                    "lease_id": "lease_initial",
                    "worker_id": "process-worker-1",
                        "worker_pid": 1001,
                        "process_exitcode": -15,
                        "result_kind": "worker_terminated",
                        "kill_point": "progress_25",
                        "kill_progress_target_ratio": 0.25,
                        "kill_progress_completed_ai_unit_count": 1,
                        "kill_progress_total_ai_unit_count": 1,
                        "kill_progress_actual_ratio": 1.0,
                        "kill_progress_observed_at": "2026-07-20T00:00:01Z",
                        "kill_progress_error": None,
                    "started_at": "2026-07-20T00:00:00Z",
                    "ended_at": "2026-07-20T00:00:01Z",
                },
                "replacement_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": "attempt_replacement",
                    "lease_id": "lease_replacement",
                    "worker_id": "process-worker-2",
                    "worker_pid": 1002,
                    "process_exitcode": 0,
                    "result_kind": "succeeded",
                    "started_at": "2026-07-20T00:00:10Z",
                    "ended_at": "2026-07-20T00:00:11Z",
                },
                "protocol_events": _replace_unit_id(_protocol_events(), "unit-1"),
                "coordinator_pid": 999,
                "created_at": "2026-07-20T00:00:11Z",
            },
        ),
    )

    record = result.fault_records[0]
    assert record["worker_process_exitcode"] not in (0, None)
    assert record["replacement_process_exitcode"] == 0
    assert record["coordinator"]["survived"] is True
    assert record["worker_pid"] != record["replacement_worker_pid"]
    assert [event["event_type"] for event in result.events] == [
        "EXPERIMENT_WORKER_DEATH_PLAN_FROZEN",
        "EXPERIMENT_WORKER_DEATH_OBSERVED",
    ]
    assert result.events[-1]["protocol_event_refs"] == record["protocol_event_refs"]


@pytest.mark.parametrize(
    ("mode", "expected_flag"),
    [
        ("FULL", "default_protocol_behavior"),
        ("NO_VERIFICATION", "wrong_canonical_exposed"),
        ("NO_PARSER_POLICY", "raw_only_exposed"),
        ("NO_REQUEUE", "stuck_after_rejection"),
        ("NO_MERGE_GATE", "premature_merge_attempted"),
    ],
)
def test_exp4_strategy_changes_only_the_selected_boundary(
    mode: str,
    expected_flag: str,
) -> None:
    result = run_exp4_ablation_strategy(
        mode=mode,
        adapter_observation={
            "candidate_rejected": True,
            "parse_failed": True,
            "replacement_created": True,
            "merge_gate_blocked": True,
            "slot_binding_valid": True,
            "deterministic_validity": False,
        },
    )

    assert result.mode == mode
    assert result.runtime_flags[expected_flag] is True
    assert result.runtime_flags["deterministic_validity_audit_retained"] is True
    if mode != "FULL":
        assert result.metrics["applicable"] is True


def test_exp4_formal_strategy_rejects_retired_slot_integrity_mode() -> None:
    with pytest.raises(ValueError, match="unsupported Experiment 4 mode"):
        run_exp4_ablation_strategy(
            mode="NO_SLOT_INTEGRITY",
            adapter_observation={},
        )
        assert result.metrics["exposed_error_count"] == 1


def test_exp5_strategy_rejects_failover_and_requires_persisted_model_records(
    tmp_path: Path,
) -> None:
    approved = {
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "cohort_member_id": "glm_5_2_siliconflow",
    }
    attempt = _Attempt(unit_id="unit-1", attempt_id="attempt-1")

    shared = {
        "approved_identity": approved,
        "condition_id": "condition-exp5",
        "cohort_member_id": "glm_5_2_siliconflow",
        "adapter_root": tmp_path,
        "task": {},
        "transport_kind": "offline_capture",
        "model_policy": "fixed_entry",
        "pilot_only": True,
    }
    with pytest.raises(ValueError, match="model_execution_record_ref"):
        run_exp5_identity_strategy(attempts=(attempt,), **shared)

    drifted = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-failover",
        model="other-model",
    )
    with pytest.raises(ValueError, match="failover"):
        run_exp5_identity_strategy(
            attempts=(drifted,),
            **shared,
        )


def test_runner_uses_logical_scheduler_for_trace_and_real_clock_for_online(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_formal_callbacks import runtime_timing_policy
    from tokenshare.core.models import ArtifactRef
    from tokenshare.local_runtime.contracts import PreparedTraceDelivery
    from tokenshare.storage.artifacts import ArtifactStore

    trace = runtime_timing_policy(evidence_class="real_model_trace_protocol_run")
    online = runtime_timing_policy(evidence_class="online_real_provider")

    assert trace.trace_delay_policy == "logical_source_latency_1x"
    assert trace.logical_scheduler is not None
    assert online.trace_delay_policy == "online_real_time"
    assert online.logical_scheduler is None

    case = generate_factorization_paper_cases()[0]
    condition, source_config = _identity_bound_factor_trace_fixture(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=source_config,
        entry_id="factorization_paper_scripted",
    )
    runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
    )
    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=source_config,
        entry_id="factorization_paper_scripted",
        max_tokens=512,
        timeout_seconds=30,
        trace_context=runtime,
    )
    assert result.task_result.wall_clock_ms > 0
    trace_store = ArtifactStore(Path(result.output_root))
    deliveries = [
        PreparedTraceDelivery.from_dict(
            json.loads(
                trace_store.read_bytes(
                    ArtifactRef.from_dict(event["payload"]["current_wrapper_ref"])
                ).decode("utf-8")
            )
        )
        for event in result.event_records
        if event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
    ]
    assert {delivery.source_latency_ms for delivery in deliveries} == {10}


def test_worker_death_parent_commit_and_ordinal_replacement_flow(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_models import PaperAttemptStatus
    from tokenshare.local_runtime import WorkerTerminationPolicy

    case = generate_factorization_paper_cases()[0]
    condition, source_config = _identity_bound_factor_trace_fixture(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=source_config,
        entry_id="factorization_paper_scripted",
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
        replacement_count=2,
    )
    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=source_config,
        entry_id="factorization_paper_scripted",
        max_tokens=512,
        timeout_seconds=30,
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("range_0",),
            kill_point="progress_25",
            total_planned_ai_unit_count=2,
        ),
        trace_context=trace_context,
    )

    dead = [
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
    ]
    replacement = [
        attempt
        for attempt in result.attempt_results
        if attempt.planned_ai_unit_id == "range_0"
        and attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
    ]
    committed_attempt_ids = {
        event["payload"]["attempt_id"]
        for event in result.event_records
        if event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
    }
    assert len(dead) == 1
    assert len(replacement) == 1
    assert dead[0].attempt_id not in committed_attempt_ids
    assert replacement[0].attempt_id in committed_attempt_ids
    assert replacement[0].entry_id == "entry-range_0-1"


def _identity_bound_factor_trace_fixture(case: dict):
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        _default_scripted_config,
    )
    from tokenshare.experiments.paper_model_identity import (
        build_model_endpoint_identity,
    )

    source_config = _default_scripted_config("factorization_paper_scripted")
    identity = build_model_endpoint_identity(
        model_cohort_id="test_factor_trace_source_cohort",
        model_cohort_digest="sha256:" + "7" * 64,
        cohort_member_id="test_factor_trace_source_member",
        provider_config_id="factorization_paper_scripted",
        selected_entry_id="factorization_paper_scripted",
        expected_provider_family="siliconflow",
        expected_provider_model_id="TokenShare/Scripted-Factorization-Range",
        expected_reasoning_profile_id="default",
        source_config=source_config,
    )
    return (
        dataclass_replace(
            _v2_condition(case),
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
        ),
        source_config,
    )


def test_exp3_producer_emits_fault_death_before_distinct_new_attempt_provider_raw_provenance_usage_model_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    condition_ref = plan.exp3_root_refs[0]
    store = ArtifactStore(tmp_path)
    original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=0
        ),
        submission_id="attempt-initial",
        submitted_at="2026-08-03T00:00:00Z",
    )
    replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=1
        ),
        submission_id="attempt-replacement",
        submitted_at="2026-08-03T00:00:03Z",
    )
    strategy = run_exp3_post_ai_strategy(
        artifact_root=tmp_path,
        condition_ref=condition_ref,
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="false_positive",
        inject_fault=lambda attempt, fault_type: {
            "fault_type": fault_type,
            "original_output_ref": attempt.raw_output_ref,
            "mutated_output_ref": attempt.raw_output_ref,
            "requires_replacement": True,
        },
        execute_replacement=lambda attempt: replacement,
        approved_identity={
            "provider": "siliconflow",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "entry_id": "sf_qwen",
        },
    )

    evidence = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=replacement,
    )

    assert evidence.recovery_source == "persisted_fault_event"
    assert evidence.initial_source_raw_ref == original.raw_or_failure_ref
    assert evidence.original_attempt_id != evidence.replacement_attempt_id
    assert evidence.replacement_attempt_ordinal == evidence.original_attempt_ordinal + 1
    assert tuple(item.role for item in evidence.ordered_replacement_evidence) == (
        EXP3_RECOVERY_CHAIN_ROLES
    )
    refs = [item.ref.artifact_ref for item in evidence.ordered_replacement_evidence]
    assert all(store.verify(ref) for ref in refs)
    assert len({ref.artifact_id for ref in refs}) == len(refs)
    assert evidence.eligibility_input.value is True

    wrong_kind = run_exp3_post_ai_strategy(
        artifact_root=tmp_path / "wrong-kind",
        condition_ref=condition_ref,
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="worker_death",
        inject_fault=lambda attempt, fault_type: {
            "fault_type": fault_type,
            "requires_replacement": True,
        },
        execute_replacement=lambda attempt: replacement,
        approved_identity={
            "provider": "siliconflow",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "entry_id": "sf_qwen",
        },
    )
    crossed = produce_exp3_online_recovery_evidence(
        artifact_store=ArtifactStore(tmp_path / "wrong-kind"),
        condition_ref=condition_ref,
        strategy_result=wrong_kind,
        original_attempt=original,
        replacement_attempt=replacement,
    )
    assert crossed.eligibility_input.blocked is True


def test_exp3_producer_records_actual_usage_cost_and_wasted_inputs_without_computing_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    condition_ref = plan.exp3_root_refs[1]
    store = ArtifactStore(tmp_path)
    original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=0
        ),
        submission_id="attempt-dead",
        submitted_at="2026-08-03T00:00:00Z",
    )
    replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=1
        ),
        submission_id="attempt-reassigned",
        submitted_at="2026-08-03T00:00:03Z",
    )
    strategy = run_exp3_worker_death_strategy(
        artifact_root=tmp_path,
        condition_id=condition_ref.condition_id,
        repeat_id=0,
        task_id=condition_ref.case_id,
        ai_unit_ids=("unit-1", "unit-2"),
        dead_worker_count=1,
        kill_point=WorkerDeathKillPoint.PROGRESS_50,
        started_at="2026-08-03T00:00:01Z",
        runtime_observations=(
            {
                "worker_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": original.attempt_id,
                    "lease_id": "lease-dead",
                    "worker_id": "process-worker-1",
                    "worker_pid": 1001,
                    "process_exitcode": -15,
                    "result_kind": "worker_terminated",
                    "kill_point": "progress_50",
                    "kill_progress_target_ratio": 0.5,
                    "kill_progress_completed_ai_unit_count": 1,
                    "kill_progress_total_ai_unit_count": 2,
                    "kill_progress_actual_ratio": 0.5,
                    "kill_progress_observed_at": "2026-08-03T00:00:01Z",
                    "kill_progress_error": None,
                    "started_at": "2026-08-03T00:00:00Z",
                    "ended_at": "2026-08-03T00:00:01Z",
                },
                "replacement_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": replacement.attempt_id,
                    "attempt_ordinal": 1,
                    "lease_id": "lease-replacement",
                    "worker_id": "process-worker-2",
                    "worker_pid": 1002,
                    "process_exitcode": 0,
                    "result_kind": "succeeded",
                    "started_at": "2026-08-03T00:00:02Z",
                    "ended_at": "2026-08-03T00:00:04Z",
                },
                "protocol_events": _replace_protocol_identity(
                    _replace_unit_id(_protocol_events(), "unit-1"),
                    {
                        "attempt_initial": original.attempt_id,
                        "lease_initial": "lease-dead",
                        "attempt_replacement": replacement.attempt_id,
                        "lease_replacement": "lease-replacement",
                    },
                ),
                "coordinator_pid": 999,
                "created_at": "2026-08-03T00:00:04Z",
            },
        ),
    )

    evidence = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=replacement,
    )

    assert [item.actual_provider_call.value for item in evidence.provider_inputs] == [
        1,
        1,
    ]
    assert [item.total_tokens.value for item in evidence.provider_inputs] == [24, 24]
    assert [item.cost_estimate_cny.value for item in evidence.provider_inputs] == [
        Decimal("0.0"),
        Decimal("0.0"),
    ]
    assert len(evidence.discarded_current_inputs) == 1
    assert evidence.discarded_current_inputs[0].attempt_id == original.attempt_id
    assert evidence.discarded_current_inputs[0].total_tokens.value == 24

    incomplete = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=dataclass_replace(
            replacement, pricing_ref=original.pricing_ref
        ),
    )
    assert incomplete.eligibility_input.value is None
    assert incomplete.eligibility_input.blocked is True
    assert incomplete.provider_inputs[-1].cost_estimate_cny.value is None
    assert incomplete.provider_inputs[-1].cost_estimate_cny.blocked is True

    wrong_ordinal = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=dataclass_replace(
            replacement, attempt_ordinal=2
        ),
    )
    assert wrong_ordinal.eligibility_input.blocked is True

    false_positive_condition = plan.exp3_root_refs[0]
    false_original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            false_positive_condition, attempt_ordinal=0
        ),
        submission_id="attempt-false-cross-initial",
        submitted_at="2026-08-03T00:00:05Z",
    )
    false_replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            false_positive_condition, attempt_ordinal=1
        ),
        submission_id="attempt-false-cross-replacement",
        submitted_at="2026-08-03T00:00:08Z",
    )
    crossed_death = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=false_positive_condition,
        strategy_result=strategy,
        original_attempt=false_original,
        replacement_attempt=false_replacement,
    )
    assert crossed_death.eligibility_input.blocked is True
