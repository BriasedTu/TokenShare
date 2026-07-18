from __future__ import annotations

import json

import pytest

from tokenshare.experiments import paper_models
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    PaperConditionResult,
    PaperExecutionContext,
    PaperExperimentModule,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_models import (
    PaperExperimentCondition,
    PaperStatus,
)


CATALOG_DIGEST = "sha256:" + "1" * 64


def test_frozen_case_selection_digest_is_stable_and_order_sensitive() -> None:
    selection = _selection(ordered_case_ids=("case_a", "case_b"))
    same_selection = _selection(ordered_case_ids=("case_a", "case_b"))
    reversed_selection = _selection(ordered_case_ids=("case_b", "case_a"))
    changed_metadata = _selection(
        ordered_case_ids=("case_a", "case_b"),
        catalog_version="v2",
    )

    assert selection.selection_digest == same_selection.selection_digest
    assert selection.selection_digest != reversed_selection.selection_digest
    assert selection.selection_digest != changed_metadata.selection_digest

    first = {
        "metadata": {"b": 2, "a": 1},
        "ordered_case_ids": ["case_a", "case_b"],
    }
    same_with_different_dict_order = {
        "ordered_case_ids": ["case_a", "case_b"],
        "metadata": {"a": 1, "b": 2},
    }
    reordered_ids = {
        "metadata": {"a": 1, "b": 2},
        "ordered_case_ids": ["case_b", "case_a"],
    }
    assert canonical_contract_digest(first) == canonical_contract_digest(
        same_with_different_dict_order
    )
    assert canonical_contract_digest(first) != canonical_contract_digest(
        reordered_ids
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"selection_id": ""}, "selection_id"),
        ({"ordered_case_ids": ("case_a", "")}, "case_id"),
        ({"ordered_case_ids": ("case_a", "case_a")}, "duplicate"),
        ({"ordered_case_ids": {"case_a", "case_b"}}, "list or tuple"),
        ({"catalog_digest": "sha256:not-a-complete-digest"}, "catalog_digest"),
        ({"ordered_case_ids": (), "expected_ai_unit_count": 0}, "executable"),
        (
            {"blocked_reason": "missing_formal_catalog"},
            "blocked selection must not declare AI units",
        ),
        ({"paper_eligible_required": "yes"}, "paper_eligible_required"),
    ],
)
def test_frozen_case_selection_rejects_invalid_contracts(
    changes: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _selection(**changes)


def test_structured_blocked_selection_has_zero_calls_without_synthetic_fallback() -> None:
    selection = _selection(
        ordered_case_ids=(),
        expected_ai_unit_count=0,
        blocked_reason="missing_formal_catalog",
    )

    body = selection.to_dict()

    assert selection.is_blocked is True
    assert selection.is_executable is False
    assert body["execution_status"] == "structured_blocked"
    assert body["ordered_case_ids"] == []
    assert body["expected_ai_unit_count"] == 0
    assert body["provider_calls_made"] == 0
    assert body["paper_eligible_possible"] is False
    assert "synthetic" not in json.dumps(body, ensure_ascii=False).lower()


def test_frozen_case_selection_roundtrip_rejects_digest_drift() -> None:
    selection = _selection(ordered_case_ids=("case_a", "case_b"))
    body = selection.to_dict()

    restored = FrozenCaseSelection.from_dict(body)
    drifted = dict(body)
    drifted["ordered_case_ids"] = ["case_b", "case_a"]

    assert restored.to_dict() == body
    with pytest.raises(ValueError, match="selection_digest mismatch"):
        FrozenCaseSelection.from_dict(drifted)
    for missing_or_invalid_digest in (None, "", 123):
        unsealed = dict(body)
        if missing_or_invalid_digest is None:
            unsealed.pop("selection_digest")
        else:
            unsealed["selection_digest"] = missing_or_invalid_digest
        with pytest.raises(ValueError, match="selection_digest"):
            FrozenCaseSelection.from_dict(unsealed)


def test_execution_context_only_holds_shared_references_and_callback() -> None:
    catalog_ref = {"catalog_digest": CATALOG_DIGEST}
    endpoint_ref = {"model_endpoint_identity_digest": "sha256:" + "2" * 64}
    artifact_store = object()
    event_store = object()

    def callback(*args, **kwargs) -> PaperConditionResult:
        return PaperConditionResult(
            condition_id="cond1",
            status=PaperStatus.BLOCKED,
            repeat_count=0,
            task_count=0,
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=1,
            provider_attempt_count=0,
            metrics_ref=None,
        )

    context = PaperExecutionContext(
        context_id="gate_b_context",
        catalog=catalog_ref,
        approved_endpoint_binding=endpoint_ref,
        request_limits={"max_tokens": 1024},
        hard_limits={"max_total_provider_attempts": 0},
        output_root="outputs/experiments/gate_b",
        artifact_store=artifact_store,
        event_store=event_store,
        execution_callback=callback,
    )

    assert context.catalog is catalog_ref
    assert context.approved_endpoint_binding is endpoint_ref
    assert context.artifact_store is artifact_store
    assert context.event_store is event_store
    assert context.execution_callback() == callback()
    with pytest.raises(ValueError, match="approved_endpoint_binding"):
        PaperExecutionContext(
            context_id="bad_context",
            catalog=catalog_ref,
            approved_endpoint_binding=None,
            request_limits={},
            hard_limits={},
            output_root="outputs/experiments/gate_b",
            artifact_store=artifact_store,
            event_store=event_store,
            execution_callback=callback,
        )
    with pytest.raises(ValueError, match="execution_callback"):
        PaperExecutionContext(
            context_id="bad_context",
            catalog=catalog_ref,
            approved_endpoint_binding=endpoint_ref,
            request_limits={},
            hard_limits={},
            output_root="outputs/experiments/gate_b",
            artifact_store=artifact_store,
            event_store=event_store,
            execution_callback=None,
        )


def test_condition_result_is_reexported_and_summary_rows_roundtrip() -> None:
    rows = ExperimentSummaryRows(
        experiment_id="exp1_real_ai_feasibility",
        rows=(
            {
                "domain": "lean_proof",
                "paper_difficulty": "simple",
                "case_count": 15,
                "transport_kind": "scripted",
                "paper_eligible": False,
            },
        ),
    )
    body = rows.to_dict()

    assert PaperConditionResult is paper_models.PaperConditionResult
    assert body["row_count"] == 1
    assert body["summary_digest"].startswith("sha256:")
    assert ExperimentSummaryRows.from_dict(body).to_dict() == body
    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        ExperimentSummaryRows(
            experiment_id="exp1_real_ai_feasibility",
            rows=(
                {
                    "transport_kind": "scripted",
                    "paper_eligible": True,
                },
            ),
        )


def test_paper_experiment_module_protocol_conformance() -> None:
    class DemoExperimentModule:
        def expand_conditions(
            self,
            context: PaperExecutionContext,
        ) -> tuple[PaperExperimentCondition, ...]:
            return (_condition(),)

        def freeze_case_selections(
            self,
            context: PaperExecutionContext,
            conditions: tuple[PaperExperimentCondition, ...],
        ) -> tuple[FrozenCaseSelection, ...]:
            return (_selection(),)

        def run_condition(
            self,
            context: PaperExecutionContext,
            condition: PaperExperimentCondition,
            selection: FrozenCaseSelection,
        ) -> PaperConditionResult:
            return PaperConditionResult(
                condition_id=condition.condition_id,
                status=PaperStatus.BLOCKED,
                repeat_count=1,
                task_count=len(selection.ordered_case_ids),
                completed_root_count=0,
                failed_root_count=0,
                blocked_root_count=len(selection.ordered_case_ids),
                provider_attempt_count=0,
                metrics_ref=None,
            )

        def summarize(self, evidence) -> ExperimentSummaryRows:
            return ExperimentSummaryRows(
                experiment_id="exp1_real_ai_feasibility",
                rows=({"paper_eligible": False},),
            )

    assert isinstance(DemoExperimentModule(), PaperExperimentModule)


def _selection(**changes) -> FrozenCaseSelection:
    body = {
        "selection_id": "exp1_lean_simple_pure_logic_v1",
        "experiment_id": "exp1_real_ai_feasibility",
        "suite_version": "paper_v1",
        "catalog_version": "v1",
        "domain": "lean_proof",
        "paper_difficulty": "simple",
        "topic_family": "pure_logic",
        "ordered_case_ids": ("case_a", "case_b"),
        "catalog_digest": CATALOG_DIGEST,
        "expected_ai_unit_count": 4,
        "paper_eligible_required": True,
    }
    body.update(changes)
    return FrozenCaseSelection(**body)


def _condition() -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_simple_pure_logic_repeat0",
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
