import json
from pathlib import Path

import tokenshare.experiments.paper_catalog as paper_catalog
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.plugins.lean_proof.checker import LeanCheckerMode
from tests.support.lean_checker import RecordingLeanChecker


def test_paper_adapter_passes_injected_checker_to_child_and_merge(
    tmp_path: Path,
) -> None:
    case = _case()
    checker = RecordingLeanChecker()

    result = run_lean_paper_case(
        case=case,
        condition=_condition("sha256:" + "a" * 64),
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
        checker=checker,
    )

    assert result.task_result.accepted_validity is True
    assert checker.modes.count(LeanCheckerMode.CHILD_PROOF) == case[
        "expected_child_count"
    ]
    assert checker.modes[-1] == LeanCheckerMode.MERGE_PROOF
    assert result.task_result.paper_eligible is False
    assert "non_production_checker_backend" in (
        result.eligibility_report.ineligibility_reasons
    )


def test_rejecting_injected_checker_cannot_produce_canonical_validity(
    tmp_path: Path,
) -> None:
    case = _case()
    checker = RecordingLeanChecker.reject_all()

    result = run_lean_paper_case(
        case=case,
        condition=_condition("sha256:" + "a" * 64),
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
        checker=checker,
    )

    assert result.task_result.accepted_validity is False
    assert result.task_result.paper_eligible is False
    assert checker.modes
    assert set(checker.modes) == {LeanCheckerMode.CHILD_PROOF}


def _condition(catalog_digest: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="lean_checker_injection",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        topic_family="pure_logic",
        topic_family_version="shallow_v1",
        construction_rule_id=None,
        oracle_package_group=None,
        proof_assembly_shape=None,
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _case() -> dict:
    first = json.loads(
        Path("benchmarks/paper/lean_catalog.v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    return paper_catalog._with_lean_v1_paper_difficulty(first)
