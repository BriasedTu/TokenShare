from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.executors.response_bank import OBJECT_ROLES, ResponseBankManifest
from tokenshare.experiments.paper_exp2_metrics import Exp2OnlineTracePostBankCheck
from tokenshare.experiments.paper_formal_report import FormalReportResult
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_models import VersionedPaperEvidenceEligibilityReport
from tokenshare.experiments.paper_paid_authorization import (
    PaidAuthorizationValidation,
    PaidExecutionReceipt,
    PaidOutputBindingMarker,
)
from tokenshare.experiments.paper_response_bank import FormalTraceInventoryPreflightResult
from tokenshare.experiments.paper_traceability import (
    L4_ARTIFACT_ROOT,
    PaperTraceabilityReplayResult,
)
from tokenshare.experiments import paper_formal_gate as gate


def _digest(char: str) -> str:
    return "sha256:" + char * 64


CONTRACT = load_paper_metric_contract()
AUTHORITY = gate.PaperGateAuthorityDigests(
    profile_digest=CONTRACT.pipeline_profile_digest,
    contract_digest=CONTRACT.contract_digest,
    plan_digest=_digest("3"),
    inventory_digest=_digest("4"),
)


def _attestation(level: str, *, status: str = "passed", classification: str = "formal"):
    return gate.PaperGateLevelAttestation(
        level=level,
        status=status,
        classification=classification,
        authority_digest=AUTHORITY.authority_digest,
    )


def _authorization(scope: str, selected: tuple[str, ...]) -> PaidAuthorizationValidation:
    receipt = PaidExecutionReceipt(
        schema_version="tokenshare.paid_execution_receipt.v1",
        receipt_digest=_digest(scope[0]),
        scope=scope,
        authorized_plan_digest=AUTHORITY.plan_digest,
        profile_digest=AUTHORITY.profile_digest,
        budget_digest=_digest("5"),
        inventory_digest=AUTHORITY.inventory_digest,
        prompt_admission_profile_digest=_digest("6"),
        selected_experiments=selected,
        output_root_path_digest=_digest("7"),
        not_before="2026-08-01T00:00:00Z",
        expires_at="2026-08-04T00:00:00Z",
        user_approval_reference="typed-test-authority",
    )
    marker = PaidOutputBindingMarker(
        schema_version="tokenshare.paid_output_binding.v1",
        marker_digest=_digest("8"),
        receipt_digest=receipt.receipt_digest,
        authorized_plan_digest=receipt.authorized_plan_digest,
        profile_digest=receipt.profile_digest,
        budget_digest=receipt.budget_digest,
        inventory_digest=receipt.inventory_digest,
        prompt_admission_profile_digest=receipt.prompt_admission_profile_digest,
        output_root_path_digest=receipt.output_root_path_digest,
    )
    return PaidAuthorizationValidation(
        receipt=receipt,
        marker=marker,
        output_mode="new_run",
        authorization_state="authorized",
        provider_dispatch_allowed=True,
    )


def _binding(scope: str, selected: tuple[str, ...]) -> gate.SelectedPaidAuthorizationBinding:
    return gate.SelectedPaidAuthorizationBinding(
        selected_experiments=selected,
        validation=_authorization(scope, selected),
    )


def _full_bank() -> gate.CompleteFullBankPrerequisite:
    ids = ("entry-a", "entry-b")
    manifest = ResponseBankManifest.create(
        bank_root_id="bank-root",
        profile_digest=AUTHORITY.profile_digest,
        budget_digest=_digest("5"),
        inventory_digest=AUTHORITY.inventory_digest,
        provider_config_digest=_digest("9"),
        entry_ids=ids,
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(ids),
        created_by_paid_receipt_digest=_digest("e"),
    )
    return gate.CompleteFullBankPrerequisite(
        plan_digest=AUTHORITY.plan_digest,
        inventory_digest=AUTHORITY.inventory_digest,
        manifest=manifest,
        preflight=FormalTraceInventoryPreflightResult(
            status="complete",
            blocked_records=(),
            required_inventory_entry_ids=ids,
            available_inventory_entry_ids=ids,
        ),
    )


def _selection(*experiments: str, classification: str = "formal"):
    return gate.PaperGateSelectionEnvelope(
        selected_experiments=experiments,
        classification=classification,
        authority=AUTHORITY,
    )


def _prerequisites(*bindings, full_bank=None, l1=None, l2=None):
    return gate.PaperGatePrerequisiteEnvelope(
        l1_attestation=l1 or _attestation("L1"),
        l2_attestation=l2 or _attestation("L2"),
        paid_authorizations=bindings,
        full_bank=full_bank,
    )


def _eligibility(experiment: str):
    evidence_class = "online_real_provider" if experiment in {"exp1", "exp5"} else "real_model_trace_protocol_run"
    return VersionedPaperEvidenceEligibilityReport(
        paper_eligible=True,
        ineligibility_reasons=(),
        evidence_class=evidence_class,
        source_classification=("current_real_provider" if evidence_class == "online_real_provider" else "approved_real_full_acquisition"),
        executed_ai_unit_count=1,
        current_provider_call_count=1 if evidence_class == "online_real_provider" else 0,
        source_provider_call_count=0 if evidence_class == "online_real_provider" else 1,
        direct_evidence_complete=True,
        identity_consistent=True,
        source_manifest_complete=True,
    )


def _report() -> FormalReportResult:
    return FormalReportResult(
        paper_eligible=True,
        regression_only=False,
        formal_paper_table_generated=True,
        report_ref={"digest": _digest("a")},
        eligibility_report_ref={"digest": _digest("b")},
        secret_scan_report_ref={"digest": _digest("c")},
        artifact_refs=(),
        captions={},
        renderer_manifest_ref={"digest": _digest("d")},
        cell_lineage_ref={"digest": _digest("e")},
        tables_digest=_digest("a"),
        cell_lineage_digest=_digest("b"),
        observations_digest=_digest("c"),
    )


def _replay(root: Path, *, audit_level=L4_ARTIFACT_ROOT, regression_only=False):
    return PaperTraceabilityReplayResult(
        output_root=root,
        observations_digest=_digest("c"),
        tables_digest=_digest("a"),
        cell_lineage_digest=_digest("b"),
        report=_report(),
        loaded_direct_inputs={},
        loaded_current_inputs={},
        loaded_source_inputs={},
        audit_level=audit_level,
        regression_only=regression_only,
    )


def _terminal(
    tmp_path: Path,
    *experiments: str,
    l1=None,
    l2=None,
    l3=None,
    l4=None,
    exp2_inputs=None,
):
    selected = tuple(experiments)
    bindings = []
    if "exp1" in selected:
        bindings.append(_binding("exp1_full_online", ("exp1",)))
    bank_selection = tuple(
        experiment for experiment in selected if experiment in {"exp2", "exp3", "exp4"}
    )
    if bank_selection:
        bindings.append(
            _binding(
                "epd027_full_bank_acquisition", ("exp2", "exp3", "exp4")
            )
        )
    if any(experiment in {"exp2", "exp3"} for experiment in selected):
        bindings.append(
            _binding(
                "epd027_l3_capability_and_online_checks", ("exp2", "exp3")
            )
        )
    if "exp5" in selected:
        bindings.append(_binding("exp5_full_online", ("exp5",)))
    return gate.PaperGateTerminalEnvelope(
        experiment_evidence=tuple(
            gate.SelectedTerminalEvidence(
                experiment_id=experiment,
                terminal_status="completed",
                eligibility=_eligibility(experiment),
                formal_report=_report(),
            )
            for experiment in experiments
        ),
        l1_attestation=l1 or _attestation("L1"),
        l2_attestation=l2 or _attestation("L2"),
        l3_attestation=l3 or _attestation("L3"),
        l4_attestation=l4 or _attestation("L4"),
        replay_results=(_replay(tmp_path / "replay-a"), _replay(tmp_path / "replay-b")),
        exp2_post_bank_inputs=exp2_inputs,
        paid_authorizations=tuple(bindings),
        full_bank=_full_bank() if bank_selection else None,
    )


def _protected_exp2_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> gate.Exp2PostBankPublicationInputs:
    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments.paper_exp2_metrics import (
        EXP2_WORKER_COUNTS,
        Exp2OnlineHydratedRoot,
        Exp2TraceHydratedRoot,
    )
    from tokenshare.experiments.paper_traceability import ProtectedReplayInputRoot

    def root(root_type, worker: int):
        value = object.__new__(root_type)
        object.__setattr__(
            value,
            "direct_result",
            SimpleNamespace(condition_axes={"worker_count": worker}),
        )
        return value

    trace_roots = tuple(root(Exp2TraceHydratedRoot, worker) for worker in EXP2_WORKER_COUNTS)
    online_roots = tuple(root(Exp2OnlineHydratedRoot, worker) for worker in EXP2_WORKER_COUNTS)
    protected = ProtectedReplayInputRoot(
        root_path=tmp_path / "protected",
        descriptor_path=tmp_path / "protected" / "descriptor.json",
        descriptor_digest=_digest("f"),
        classification="normal_formal_artifact_root",
        _producer_validated=True,
    )
    loaded = SimpleNamespace(
        direct={
            "exp2_trace_scalability": trace_roots,
            "exp2_online_concurrency": online_roots,
        }
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_input_root",
        lambda root_path: protected
        if Path(root_path) == tmp_path
        else pytest.fail("protected root loader received a different root"),
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_inputs",
        lambda root_path: loaded
        if Path(root_path) == tmp_path
        else pytest.fail("protected closure loader received a different root"),
    )
    return gate.load_exp2_post_bank_publication_inputs(
        replay_input_root=tmp_path,
        contract=CONTRACT,
        authority=AUTHORITY,
    )


def test_execution_gate_checks_only_prerequisites_available_before_selected_run() -> None:
    decision = gate.formal_execution_gate(
        _selection("exp1"),
        _prerequisites(_binding("exp1_full_online", ("exp1",))),
    )
    assert decision.stage == "formal_execution_gate"
    assert decision.status == "ready"
    assert decision.blocked_reasons == ()
    assert decision.provider_calls == 0


def test_execution_gate_never_requires_l4_outputs_or_post_bank_severe() -> None:
    prerequisites = _prerequisites(
        _binding("epd027_full_bank_acquisition", ("exp2", "exp3", "exp4")),
        full_bank=_full_bank(),
    )
    assert not hasattr(prerequisites, "l4_attestation")
    assert not hasattr(prerequisites, "post_bank_check")
    assert gate.formal_execution_gate(_selection("exp2", "exp3", "exp4"), prerequisites).status == "ready"


def test_exp1_execution_requires_common_offline_and_exp1_receipt_not_bank_or_exp5() -> None:
    missing = gate.formal_execution_gate(_selection("exp1"), _prerequisites())
    assert missing.blocked_reasons == ("missing_paid_authorization:exp1_full_online",)
    ready = gate.formal_execution_gate(
        _selection("exp1"), _prerequisites(_binding("exp1_full_online", ("exp1",)))
    )
    assert ready.status == "ready"


def test_exp2_to_exp4_execution_requires_full_bank_receipt_plan_and_complete_inventory_not_future_metrics() -> None:
    selection = _selection("exp2", "exp3", "exp4")
    missing = gate.formal_execution_gate(selection, _prerequisites())
    assert missing.blocked_reasons == (
        "missing_paid_authorization:epd027_full_bank_acquisition",
        "missing_complete_full_bank",
    )
    ready = gate.formal_execution_gate(
        selection,
        _prerequisites(
            _binding("epd027_full_bank_acquisition", ("exp2", "exp3", "exp4")),
            full_bank=_full_bank(),
        ),
    )
    assert ready.status == "ready"
    selected_exp2_only = gate.formal_execution_gate(
        _selection("exp2"),
        _prerequisites(
            _binding("epd027_full_bank_acquisition", ("exp2", "exp3", "exp4")),
            full_bank=_full_bank(),
        ),
    )
    assert selected_exp2_only.status == "ready"


def test_exp5_capability_and_full_execution_scopes_are_distinct() -> None:
    capability = gate.formal_execution_gate(
        _selection("exp5_capability", classification="facility"),
        _prerequisites(_binding("exp5_capability_smoke", ("exp5_capability",))),
    )
    assert capability.status == "ready" and capability.classification == "facility"
    full = gate.formal_execution_gate(
        _selection("exp5"),
        _prerequisites(_binding("exp5_capability_smoke", ("exp5_capability",))),
    )
    assert full.blocked_reasons == ("missing_paid_authorization:exp5_full_online",)


def test_publication_gate_runs_only_after_terminal_outputs(tmp_path: Path) -> None:
    decision = gate.paper_publication_gate(
        _selection("exp1"),
        gate.PaperGateTerminalEnvelope(
            experiment_evidence=(), l3_attestation=_attestation("L3"),
            l4_attestation=_attestation("L4"), replay_results=(),
        ),
    )
    assert decision.status == "blocked"
    assert decision.blocked_reasons[0] == "selected_terminal_evidence_missing:exp1"


def test_publication_gate_requires_formal_cell_audit_and_post_bank_intersection_severe_for_exp2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(gate, "build_exp2_trace_observations", lambda roots, contract: calls.append("trace") or SimpleNamespace())
    monkeypatch.setattr(
        gate, "build_exp2_online_observations",
        lambda roots, contract, **kwargs: calls.append("online") or SimpleNamespace(
            post_bank_check=Exp2OnlineTracePostBankCheck(
                status="evaluated_post_bank", same_case_intersection_count_by_worker={
                    3: 3, 7: 3, 10: 3, 30: 3, 50: 3,
                },
                severe_worker_counts=(), severe_reasons_by_worker={},
            )
        ),
    )
    inputs = _protected_exp2_inputs(tmp_path, monkeypatch)
    decision = gate.paper_publication_gate(
        _selection("exp2", "exp3", "exp4"),
        _terminal(tmp_path, "exp2", "exp3", "exp4", exp2_inputs=inputs),
    )
    assert decision.status == "ready"
    assert calls == ["trace", "online"]


def test_exp2_publication_inputs_require_official_protected_replay_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert hasattr(gate, "load_exp2_post_bank_publication_inputs"), (
        "Exp2 publication inputs must come from the official protected replay loader"
    )
    inputs = _protected_exp2_inputs(tmp_path, monkeypatch)
    assert inputs.contract_digest == AUTHORITY.contract_digest
    assert inputs.profile_digest == AUTHORITY.profile_digest
    assert inputs.protected_replay_descriptor_digest == _digest("f")
    with pytest.raises(ValueError, match="official protected replay factory"):
        gate.Exp2PostBankPublicationInputs(
            contract=CONTRACT,
            trace_roots=inputs.trace_roots,
            online_roots=inputs.online_roots,
            contract_digest=AUTHORITY.contract_digest,
            profile_digest=AUTHORITY.profile_digest,
            authority_digest=AUTHORITY.authority_digest,
            protected_replay_descriptor_digest=_digest("f"),
            worker_levels_complete=True,
        )
    monkeypatch.setattr(
        gate,
        "build_exp2_trace_observations",
        lambda _roots, _contract: SimpleNamespace(),
    )
    monkeypatch.setattr(
        gate,
        "build_exp2_online_observations",
        lambda _roots, _contract, **_kwargs: SimpleNamespace(
            post_bank_check=Exp2OnlineTracePostBankCheck(
                status="evaluated_post_bank",
                same_case_intersection_count_by_worker={3: 3},
                severe_worker_counts=(),
                severe_reasons_by_worker={},
            )
        ),
    )
    decision = gate.paper_publication_gate(
        _selection("exp2"),
        _terminal(tmp_path, "exp2", exp2_inputs=inputs),
    )
    assert "exp2_post_bank_worker_levels_incomplete" in decision.blocked_reasons


def test_exp3_publication_requires_online_check_receipt_scope(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path, "exp3")
    without_online_receipt = replace(
        terminal,
        paid_authorizations=tuple(
            binding
            for binding in terminal.paid_authorizations
            if binding.validation.receipt.scope
            != "epd027_l3_capability_and_online_checks"
        ),
    )
    decision = gate.paper_publication_gate(
        _selection("exp3"), without_online_receipt
    )
    assert decision.blocked_reasons == (
        "missing_paid_authorization:epd027_l3_capability_and_online_checks",
    )


def test_unselected_experiment_approval_is_not_required() -> None:
    decision = gate.formal_execution_gate(
        _selection("exp1"), _prerequisites(_binding("exp1_full_online", ("exp1",)))
    )
    assert decision.status == "ready"
    assert all("exp5" not in reason for reason in decision.blocked_reasons)


def test_p0_full_is_the_only_formal_selection_that_aggregates_all_scopes() -> None:
    selected = ("exp1", "exp2", "exp3", "exp4", "exp5")
    decision = gate.formal_execution_gate(
        _selection(*selected),
        _prerequisites(
            _binding("exp1_full_online", ("exp1",)),
            _binding("epd027_full_bank_acquisition", ("exp2", "exp3", "exp4")),
            _binding("exp5_full_online", ("exp5",)),
            full_bank=_full_bank(),
        ),
    )
    assert decision.status == "ready"
    assert decision.blocked_reasons == ()


def test_facility_l1_mini_gate_cannot_become_formal_publication_pass(tmp_path: Path) -> None:
    terminal = _terminal(
        tmp_path, "exp1",
        l3=_attestation("L3", classification="facility"),
        l4=_attestation("L4", classification="facility"),
    )
    decision = gate.paper_publication_gate(_selection("exp1"), terminal)
    assert decision.status == "blocked"
    assert "publication_requires_formal_l3" in decision.blocked_reasons


@pytest.mark.parametrize("level", ("L3", "L4"))
def test_blocked_l3_or_l4_never_passes_publication(tmp_path: Path, level: str) -> None:
    terminal = _terminal(
        tmp_path, "exp1",
        l3=_attestation("L3", status="blocked") if level == "L3" else None,
        l4=_attestation("L4", status="blocked") if level == "L4" else None,
    )
    assert gate.paper_publication_gate(_selection("exp1"), terminal).status == "blocked"
