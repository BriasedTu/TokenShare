from __future__ import annotations

import ast
from dataclasses import replace
from hashlib import sha256
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.experiments import run_paper_pipeline as pipeline
from tokenshare.experiments.paper_formal_gate import (
    PaperGateAuthorityDigests,
    PaperGateLevelAttestation,
    PaperGatePrerequisiteEnvelope,
    PaperGateSelectionEnvelope,
)
from tokenshare.experiments.paper_models import PaperSuiteResult
from tokenshare.experiments.paper_paid_authorization import PaidAuthorizationValidation
from tokenshare.experiments.run_paper_experiments import EPD027FormalServiceAuthority


DIGESTS = {
    "profile": "sha256:" + "1" * 64,
    "budget": "sha256:" + "2" * 64,
    "inventory": "sha256:" + "3" * 64,
    "plan": "sha256:" + "4" * 64,
    "admission": "sha256:" + "5" * 64,
    "receipt": "sha256:" + "6" * 64,
    "marker": "sha256:" + "7" * 64,
}
PROFILE = SimpleNamespace(
    profile_id="epd027_response_bank_paper_pipeline.v1",
    profile_digest=DIGESTS["profile"],
    budget_digest=DIGESTS["budget"],
    prompt_admission_profile_digest=DIGESTS["admission"],
)

OFFLINE_COMMANDS = (
    ("validate-profile", "offline_profile_validation", ()),
    ("plan-bank", "offline_bank_plan", ()),
    ("audit-bank", "offline_bank_audit", ("--external-bank-root", "bank")),
    (
        "run-trace",
        "real_model_trace_protocol_run",
        (
            "--external-bank-root",
            "bank",
            "--output-root",
            "trace",
            "--plan-bundle-root",
            "bank",
            "--plan-digest",
            DIGESTS["plan"],
            "--inventory-digest",
            DIGESTS["inventory"],
        ),
    ),
    ("render", "offline_render", ("--output-root", "render")),
    (
        "replay",
        "offline_replay",
        ("--output-root", "replay", "--replay-input-root", "input"),
    ),
    ("audit-cell-lineage", "offline_cell_lineage", ("--output-root", "lineage")),
    (
        "validate-formal-execution-gate",
        "offline_gate_parser_only",
        ("--output-root", "formal-gate"),
    ),
    (
        "validate-paper-publication-gate",
        "offline_gate_parser_only",
        ("--output-root", "publication-gate"),
    ),
)
PROVIDER_COMMANDS = {
    "acquire-bank": "epd027_full_bank_acquisition",
    "run-online-checks": "epd027_l3_capability_and_online_checks",
    "run-exp1-online": "exp1_full_online",
    "run-exp5-capability-smoke": "exp5_capability_smoke",
    "run-exp5-online": "exp5_full_online",
}

DEFAULT_ADAPTER_NAMES = {
    "validate-profile": "_validate_profile_adapter",
    "plan-bank": "_plan_bank_adapter",
    "acquire-bank": "_acquire_bank_adapter",
    "audit-bank": "_audit_bank_adapter",
    "run-trace": "_run_trace_adapter",
    "run-online-checks": "_run_online_checks_adapter",
    "run-exp1-online": "_run_exp1_online_adapter",
    "run-exp5-capability-smoke": "_run_exp5_capability_smoke_adapter",
    "run-exp5-online": "_run_exp5_online_adapter",
    "render": "_render_adapter",
    "replay": "_replay_adapter",
    "audit-cell-lineage": "_audit_cell_lineage_adapter",
    "validate-formal-execution-gate": "_validate_formal_execution_gate_adapter",
    "validate-paper-publication-gate": "_validate_paper_publication_gate_adapter",
}


def _delegate(command: str, seen: list[object]):
    def invoke(request):
        seen.append(request)
        public_result = {
            "status": "completed",
            "inventory_digest": DIGESTS["inventory"],
            "evidence_class": request.evidence_class,
            "provider_calls": 0,
            "service": command,
        }
        if command in pipeline._FORMAL_GATED_COMMANDS:
            return pipeline.FormalSuiteServiceResult(
                public_result=public_result,
                terminal_result=_paper_suite_result(request.output_root),
            )
        return public_result

    return invoke


def _authorization(**kwargs):
    return PaidAuthorizationValidation(
        receipt=SimpleNamespace(
            receipt_digest=DIGESTS["receipt"],
            scope=kwargs.get("requested_scope", "exp1_full_online"),
            budget_digest=kwargs.get("budget_digest", DIGESTS["budget"]),
        ),
        marker=SimpleNamespace(marker_digest=DIGESTS["marker"]),
        output_mode=kwargs["output_mode"],
        authorization_state="dispatch_authorized",
        provider_dispatch_allowed=True,
    )


def _paper_suite_result(
    output_root: Path | None,
    *,
    status: str = "completed",
) -> PaperSuiteResult:
    return PaperSuiteResult(
        suite_id="test-paper-suite",
        status=status,
        output_root=Path(output_root or ".").as_posix(),
        started_at="2026-08-03T00:00:00Z",
        ended_at="2026-08-03T00:00:01Z",
        experiment_ids=("exp1",),
        condition_count=1,
        run_count=1,
        task_count=1,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=(),
        audit_refs=(),
        error_summary=(),
    )


def _ready_formal_authority(
    command: str,
    *,
    budget_digest: str = DIGESTS["budget"],
) -> EPD027FormalServiceAuthority:
    selected = {
        "run-trace": ("exp2", "exp3", "exp4"),
        "run-exp1-online": ("exp1",),
        "run-exp5-online": ("exp5",),
    }.get(command)
    selection = None
    prerequisites = None
    publication_factory = None
    if selected is not None:
        authority = PaperGateAuthorityDigests(
            profile_digest=DIGESTS["profile"],
            contract_digest="sha256:" + "9" * 64,
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
        )
        selection = PaperGateSelectionEnvelope(
            selected_experiments=selected,
            classification="formal",
            authority=authority,
        )
        prerequisites = PaperGatePrerequisiteEnvelope(
            l1_attestation=PaperGateLevelAttestation(
                level="L1",
                status="passed",
                classification="formal",
                authority_digest=authority.authority_digest,
            ),
            l2_attestation=PaperGateLevelAttestation(
                level="L2",
                status="passed",
                classification="formal",
                authority_digest=authority.authority_digest,
            ),
        )
        publication_factory = lambda _terminal: object()
    return EPD027FormalServiceAuthority(
        command=command,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_digest=budget_digest,
        keyword_arguments={},
        gate_selection=selection,
        gate_prerequisites=prerequisites,
        publication_gate_factory=publication_factory,
    )


def _ready_gate(stage: str) -> dict[str, object]:
    return {
        "status": "ready",
        "stage": stage,
        "classification": "formal",
        "blocked_reasons": [],
        "provider_calls": 0,
    }


def _main(argv: list[str], *, command: str, seen: list[object], **overrides) -> int:
    dependencies = {
        "_PROFILE_LOADER": lambda _path: PROFILE,
        "_RECEIPT_LOADER": lambda _path: {"receipt_digest": DIGESTS["receipt"]},
        "_RECEIPT_VALIDATOR": _authorization,
        "_BUDGET_VALIDATOR": lambda **_kwargs: None,
        "_UTC_NOW": lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
        "_FORMAL_AUTHORITY_BUILDER": lambda **kwargs: _ready_formal_authority(
            kwargs["command"]
        ),
        "_validate_formal_execution_gate_adapter": (
            lambda _request: _ready_gate("formal_execution_gate")
        ),
        "_validate_paper_publication_gate_adapter": (
            lambda _request: _ready_gate("paper_publication_gate")
        ),
    }
    aliases = {
        "profile_loader": "_PROFILE_LOADER",
        "receipt_loader": "_RECEIPT_LOADER",
        "receipt_validator": "_RECEIPT_VALIDATOR",
        "budget_validator": "_BUDGET_VALIDATOR",
        "now": "_UTC_NOW",
    }
    service_input = overrides.pop("service_input", None)
    for name, value in overrides.items():
        dependencies[aliases.get(name, name)] = (
            (lambda value=value: value) if name == "now" else value
        )
    adapters = pipeline._AUTHORITATIVE_SERVICE_ADAPTERS
    original = adapters[command]
    adapters[command] = _delegate(command, seen)
    monkeypatch = pytest.MonkeyPatch()
    factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
    factories[command] = lambda _request: service_input
    try:
        for name, value in dependencies.items():
            monkeypatch.setattr(pipeline, name, value)
        monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)
        return pipeline.main(argv)
    finally:
        monkeypatch.undo()
        adapters[command] = original


def _offline_args(tmp_path: Path, command: str, extra: tuple[str, ...]) -> list[str]:
    resolved = []
    for value in extra:
        if value in {"bank", "trace", "render", "replay", "input", "lineage", "formal-gate", "publication-gate"}:
            resolved.append(str(tmp_path / value))
        else:
            resolved.append(value)
    return [command, "--profile", "tracked-profile.json", *resolved]


def _provider_args(tmp_path: Path, command: str, *, mode: str = "--new-run") -> list[str]:
    return [
        command,
        "--profile",
        "tracked-profile.json",
        "--receipt",
        "user-receipt.json",
        "--allow-provider-calls",
        mode,
        "--output-root",
        str(tmp_path / command),
        "--plan-digest",
        DIGESTS["plan"],
        "--inventory-digest",
        DIGESTS["inventory"],
    ]


def _assert_output(capsys, *, scope: str, evidence_class: str, provider: bool) -> dict:
    body = json.loads(capsys.readouterr().out)
    assert body == {
        "schema_version": "tokenshare.paper_pipeline_cli_result.v1",
        "status": "completed",
        "scope": scope,
        "profile_digest": DIGESTS["profile"],
        "budget_digest": DIGESTS["budget"],
        "plan_digest": (
            DIGESTS["plan"] if provider or scope == "run-trace" else None
        ),
        "inventory_digest": DIGESTS["inventory"],
        "receipt_digest": DIGESTS["receipt"] if provider else None,
        "output_marker_digest": DIGESTS["marker"] if provider else None,
        "evidence_class": evidence_class,
        "provider_calls": 0,
    }
    return body


def _assert_offline_command(tmp_path, capsys, command: str, evidence_class: str, extra):
    seen: list[object] = []
    assert _main(_offline_args(tmp_path, command, extra), command=command, seen=seen) == 0
    _assert_output(capsys, scope=command, evidence_class=evidence_class, provider=False)
    assert len(seen) == 1
    assert seen[0].provider_authorization is None


def _assert_provider_command(tmp_path, capsys, command: str):
    seen: list[object] = []
    assert _main(_provider_args(tmp_path, command), command=command, seen=seen) == 0
    _assert_output(
        capsys,
        scope=PROVIDER_COMMANDS[command],
        evidence_class="online_real_provider",
        provider=True,
    )
    assert len(seen) == 1
    assert seen[0].provider_authorization.provider_dispatch_allowed is True


def test_validate_profile_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[0])


@pytest.mark.parametrize(
    ("status", "expected_exit_code"),
    (
        ("completed", 0),
        ("completed_with_failures", 0),
        ("blocked", 3),
        ("failed", 3),
    ),
)
def test_pipeline_exit_code_preserves_terminal_semantics(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_exit_code: int,
) -> None:
    command = "validate-profile"
    adapters = pipeline._AUTHORITATIVE_SERVICE_ADAPTERS
    original = adapters[command]
    adapters[command] = lambda request: {
        "status": status,
        "evidence_class": request.evidence_class,
        "provider_calls": 0,
    }
    try:
        monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
        exit_code = pipeline.main(_offline_args(tmp_path, command, ()))
    finally:
        adapters[command] = original

    assert exit_code == expected_exit_code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_default_authoritative_adapter_map_covers_every_command() -> None:
    adapters = pipeline._AUTHORITATIVE_SERVICE_ADAPTERS
    assert set(adapters) == pipeline.PAPER_PIPELINE_COMMANDS
    assert {
        command: adapter.__name__ for command, adapter in adapters.items()
    } == DEFAULT_ADAPTER_NAMES


def test_public_main_exposes_only_argv() -> None:
    assert tuple(inspect.signature(pipeline.main).parameters) == ("argv",)


def test_production_service_input_factory_map_is_fixed_and_complete() -> None:
    factories = pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES
    assert set(factories) == pipeline.PAPER_PIPELINE_COMMANDS
    with pytest.raises(TypeError):
        factories["validate-profile"] = lambda _request: None


@pytest.mark.parametrize(
    "command",
    (
        "acquire-bank",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-capability-smoke",
        "run-exp5-online",
        "render",
        "audit-cell-lineage",
    ),
)
def test_previously_unbound_commands_reach_named_official_service_by_default(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    from tokenshare.experiments import (
        paper_formal_report,
        paper_formal_runner,
        paper_response_bank,
        paper_smoke,
        paper_traceability,
    )

    calls: list[tuple[str, object]] = []
    output_name = {
        "run-trace": "trace",
        "audit-cell-lineage": "lineage",
    }.get(command, command)
    output_root = tmp_path / output_name
    provider = command in PROVIDER_COMMANDS
    if command == "acquire-bank":
        class CapturingOrchestrator:
            def __init__(self, **kwargs):
                calls.append(("ResponseBankAcquisitionOrchestrator", kwargs))

            def acquire_all(self, requests):
                calls.append(("acquire_all", tuple(requests)))
                return SimpleNamespace(
                    status="complete",
                    results=(
                        SimpleNamespace(
                            transport_invoked=False,
                            status="already_terminal",
                        ),
                    ),
                    missing_inventory_entry_ids=(),
                    ambiguous_inventory_entry_ids=(),
                )

        monkeypatch.setattr(
            paper_response_bank,
            "ResponseBankAcquisitionOrchestrator",
            CapturingOrchestrator,
        )
        service_input = pipeline.AcquisitionServiceInput(
            scope=PROVIDER_COMMANDS[command],
            orchestrator_arguments={
                "output_root": output_root,
                "inventory_digest": DIGESTS["inventory"],
            },
            acquisition_requests=(object(),),
        )
    elif command in {
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-online",
    }:
        monkeypatch.setattr(
            paper_formal_runner,
            "execute_paper_formal_suite",
            lambda **kwargs: calls.append(("execute_paper_formal_suite", kwargs))
            or _paper_suite_result(Path(kwargs["output_root"])),
        )
        scope = PROVIDER_COMMANDS.get(command, command)
        service_input = pipeline.FormalSuiteServiceInput(
            scope=scope,
            keyword_arguments={
                "dispatch_plans": (),
                "catalog_manifest": object(),
                "budget": object(),
                "budget_approval": {},
                "output_root": output_root,
                "ai_api_configs": {},
                "transport": object(),
                "real_transport": provider,
                "hard_limits": {},
                "trace_context": object() if command == "run-trace" else None,
                "suite_id": command,
            },
        )
    elif command == "run-exp5-capability-smoke":
        monkeypatch.setattr(
            paper_smoke,
            "execute_paper_smoke_suite",
            lambda **kwargs: calls.append(("execute_paper_smoke_suite", kwargs))
            or SimpleNamespace(status="completed", provider_attempt_count=0),
        )
        service_input = pipeline.FormalSuiteServiceInput(
            scope=PROVIDER_COMMANDS[command],
            keyword_arguments={
                "profile": object(),
                "execution_plan": SimpleNamespace(output_root=output_root),
                "catalog_manifest": object(),
                "budget": object(),
                "ai_api_configs": {},
                "transport": object(),
                "real_transport": True,
                "hard_limits": {},
                "launch_manifest": {},
            },
        )
    elif command == "render":
        monkeypatch.setattr(
            paper_formal_report,
            "generate_paper_formal_report",
            lambda **kwargs: calls.append(("generate_paper_formal_report", kwargs))
            or SimpleNamespace(status="completed"),
        )
        service_input = pipeline.ReportRenderServiceInput(
            metrics=object(),
            contract=object(),
        )
    else:
        monkeypatch.setattr(
            paper_traceability,
            "write_cell_lineage_audit",
            lambda **kwargs: calls.append(("write_cell_lineage_audit", kwargs))
            or (SimpleNamespace(), {"content_hash": DIGESTS["inventory"]}),
        )
        service_input = pipeline.CellLineageAuditServiceInput(
            contract=object(),
            observations=(),
        )

    args = (
        _provider_args(tmp_path, command)
        if provider
        else _offline_args(
            tmp_path,
            command,
                (
                    (
                        "--external-bank-root",
                        "bank",
                        "--output-root",
                        "trace",
                        "--plan-bundle-root",
                        "bank",
                        "--plan-digest",
                        DIGESTS["plan"],
                        "--inventory-digest",
                        DIGESTS["inventory"],
                    )
                    if command == "run-trace"
                else ("--output-root", "render")
                if command == "render"
                else ("--output-root", "lineage")
            ),
        )
    )
    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
    monkeypatch.setattr(
        pipeline,
        "_RECEIPT_LOADER",
        lambda _path: {"receipt_digest": DIGESTS["receipt"]},
    )
    monkeypatch.setattr(pipeline, "_RECEIPT_VALIDATOR", _authorization)
    monkeypatch.setattr(pipeline, "_BUDGET_VALIDATOR", lambda **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "_UTC_NOW",
        lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_AUTHORITY_BUILDER",
        lambda **kwargs: _ready_formal_authority(kwargs["command"]),
    )
    if command in pipeline._FORMAL_GATED_COMMANDS:
        monkeypatch.setattr(
            pipeline,
            "_validate_formal_execution_gate_adapter",
            lambda _request: _ready_gate("formal_execution_gate"),
        )
        monkeypatch.setattr(
            pipeline,
            "_validate_paper_publication_gate_adapter",
            lambda _request: _ready_gate("paper_publication_gate"),
        )
    factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
    factories[command] = lambda _request: service_input
    monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)
    assert pipeline.main(
        args,
    ) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "completed"
    assert body["scope"] == PROVIDER_COMMANDS.get(command, command)
    assert calls


def test_default_formal_proof_adapter_serializes_infrastructure_block_without_traceback(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments.paper_terminal_outcomes import PaperEvidenceIntegrity

    output_root = tmp_path / "run-online-checks"
    calls: list[str] = []

    def blocked_formal_suite(**_kwargs):
        calls.append("execute_paper_formal_suite")
        raise paper_formal_runner.PaperInfrastructureBlockedError(
            "formal disk preflight failed: insufficient disk capacity",
            evidence_integrity=PaperEvidenceIntegrity.INVALID,
            failure_stage="disk_preflight",
            failure_kind="insufficient_disk_capacity",
            diagnostics={"available_bytes": 1, "required_bytes": 2},
        )

    monkeypatch.setattr(
        paper_formal_runner,
        "execute_paper_formal_suite",
        blocked_formal_suite,
    )
    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_AUTHORITY_BUILDER",
        lambda **kwargs: _ready_formal_authority(kwargs["command"]),
    )
    monkeypatch.setattr(
        pipeline,
        "_RECEIPT_LOADER",
        lambda _path: {"receipt_digest": DIGESTS["receipt"]},
    )
    monkeypatch.setattr(pipeline, "_RECEIPT_VALIDATOR", _authorization)
    monkeypatch.setattr(pipeline, "_BUDGET_VALIDATOR", lambda **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "_UTC_NOW",
        lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
    factories["run-online-checks"] = lambda _request: pipeline.FormalSuiteServiceInput(
        scope=PROVIDER_COMMANDS["run-online-checks"],
        keyword_arguments={
            "output_root": output_root,
            "real_transport": True,
        },
    )
    monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)

    assert pipeline.main(_provider_args(tmp_path, "run-online-checks")) == 3

    captured = capsys.readouterr()
    assert captured.err == ""
    assert calls == ["execute_paper_formal_suite"]
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out) == {
        "schema_version": "tokenshare.paper_pipeline_cli_result.v1",
        "status": "blocked",
        "scope": "epd027_l3_capability_and_online_checks",
        "evidence_class": "online_real_provider",
        "provider_calls": 0,
        "message": "formal disk preflight failed: insufficient disk capacity",
        "outcome_status": "blocked_dependency",
        "evidence_integrity": "invalid",
        "failure_stage": "disk_preflight",
        "failure_kind": "insufficient_disk_capacity",
        "resource_diagnostics": {
            "available_bytes": 1,
            "required_bytes": 2,
        },
    }


def test_plan_bank_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[1])


def test_default_plan_bank_reports_plan_digest_without_fabricating_inventory(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenshare.experiments import paper_online_checks

    monkeypatch.setattr(
        paper_online_checks,
        "freeze_paper_online_checks_plan",
        lambda: SimpleNamespace(plan_digest=DIGESTS["plan"]),
    )

    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
    assert pipeline.main(["plan-bank", "--profile", "tracked-profile.json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "tokenshare.paper_pipeline_cli_result.v1",
        "status": "completed",
        "scope": "plan-bank",
        "profile_digest": DIGESTS["profile"],
        "budget_digest": DIGESTS["budget"],
        "plan_digest": DIGESTS["plan"],
        "inventory_digest": None,
        "receipt_digest": None,
        "output_marker_digest": None,
        "evidence_class": "offline_bank_plan",
        "provider_calls": 0,
    }


def test_results_first_matrix8_plan_bank_accepts_fresh_bundle_root(
    tmp_path: Path,
    capsys,
) -> None:
    seen: list[object] = []
    bundle_root = tmp_path / "matrix8-plan"

    assert _main(
        [
            "plan-bank",
            "--profile",
            "tracked-profile.json",
            "--results-first-matrix8",
            "--plan-bundle-root",
            str(bundle_root),
        ],
        command="plan-bank",
        seen=seen,
    ) == 0

    assert len(seen) == 1
    assert seen[0].plan_bundle_root == bundle_root
    assert seen[0].serialized_arguments["results_first_matrix8"] is True
    assert json.loads(capsys.readouterr().out)["provider_calls"] == 0


def test_results_first_matrix8_plan_adapter_persists_exact_bundle_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import run_paper_experiments

    bundle = SimpleNamespace(
        authorized_plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        bundle_digest="sha256:" + "9" * 64,
        inventory_rows=tuple(range(166)),
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        run_paper_experiments,
        "create_results_first_matrix8_acquisition_bundle",
        lambda **kwargs: calls.append(kwargs) or bundle,
    )
    request = pipeline.PipelineCommandRequest(
        command="plan-bank",
        scope="plan-bank",
        evidence_class="offline_bank_plan",
        profile=PROFILE,
        provider_authorization=None,
        output_root=None,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={"results_first_matrix8": True},
        plan_bundle_root=tmp_path / "bundle",
    )

    result = pipeline._plan_bank_adapter(request)

    assert calls == [
        {
            "pipeline_profile_digest": DIGESTS["profile"],
            "bundle_root": tmp_path / "bundle",
        }
    ]
    assert result["plan_digest"] == DIGESTS["plan"]
    assert result["inventory_digest"] == DIGESTS["inventory"]
    assert result["inventory_entry_count"] == 166
    assert result["provider_calls"] == 0


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("plan_digest", "pipeline service plan digest mismatch"),
        ("inventory_digest", "pipeline service inventory digest mismatch"),
    ),
)
def test_service_result_validates_plan_and_inventory_digests_independently(
    field: str, message: str
) -> None:
    request = pipeline.PipelineCommandRequest(
        command="run-exp1-online",
        scope=PROVIDER_COMMANDS["run-exp1-online"],
        evidence_class="online_real_provider",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=Path("output"),
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
    )
    delegated = {
        "status": "completed",
        "plan_digest": DIGESTS["plan"],
        "inventory_digest": DIGESTS["inventory"],
    }
    delegated[field] = "sha256:" + "9" * 64

    with pytest.raises(ValueError, match=message):
        pipeline._json_result(request=request, delegated=delegated)


def test_acquire_bank_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_provider_command(tmp_path, capsys, "acquire-bank")


def test_results_first_matrix8_acquire_requires_no_paid_receipt_but_keeps_dispatch_flag(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    validated: list[object] = []
    authorization = SimpleNamespace(
        schema_version="tokenshare.results_first_smoke_acquisition_authorization.v1",
        authorization_digest="sha256:" + "8" * 64,
        budget_digest=DIGESTS["budget"],
        marker=SimpleNamespace(marker_digest=DIGESTS["marker"]),
        output_mode="new_run",
        provider_dispatch_allowed=True,
    )
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_AUTHORITY_BUILDER",
        lambda **_kwargs: authorization,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_BUNDLE_VALIDATOR",
        lambda candidate, **_kwargs: validated.append(candidate) or candidate,
        raising=False,
    )
    args = [
        "acquire-bank",
        "--profile",
        "tracked-profile.json",
        "--results-first-matrix8",
        "--allow-provider-calls",
        "--new-run",
        "--output-root",
        str(tmp_path / "acquire"),
        "--plan-digest",
        DIGESTS["plan"],
        "--inventory-digest",
        DIGESTS["inventory"],
        "--plan-bundle-root",
        str(tmp_path / "plan"),
    ]
    bundle = SimpleNamespace(
        authorized_plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        profile_digest=DIGESTS["profile"],
        prompt_admission_profile_digest=DIGESTS["admission"],
        full_budget=SimpleNamespace(budget_digest=DIGESTS["budget"]),
    )

    assert _main(
        args,
        command="acquire-bank",
        seen=seen,
        _ACQUISITION_BUNDLE_LOADER=lambda _path: bundle,
        service_input=object(),
    ) == 0

    assert len(seen) == 1
    assert validated == [bundle]
    assert seen[0].provider_authorization is authorization
    body = json.loads(capsys.readouterr().out)
    assert body["receipt_digest"] is None
    assert body["facility_authorization_schema"] == authorization.schema_version
    assert body["facility_authorization_digest"] == authorization.authorization_digest


def test_results_first_matrix8_acquire_still_requires_allow_provider_calls(
    tmp_path: Path,
) -> None:
    assert pipeline.main(
        [
            "acquire-bank",
            "--profile",
            "tracked-profile.json",
            "--results-first-matrix8",
            "--new-run",
            "--output-root",
            str(tmp_path / "acquire"),
            "--plan-digest",
            DIGESTS["plan"],
            "--inventory-digest",
            DIGESTS["inventory"],
            "--plan-bundle-root",
            str(tmp_path / "plan"),
        ]
    ) == 2


def test_acquire_bank_validates_bundle_full_budget_before_service_factory(
    tmp_path: Path,
    capsys,
) -> None:
    seen: list[object] = []
    receipt_arguments: list[dict[str, object]] = []
    full_budget_digest = "sha256:" + "8" * 64
    bundle = SimpleNamespace(
        authorized_plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        profile_digest=DIGESTS["profile"],
        prompt_admission_profile_digest=DIGESTS["admission"],
        full_budget=SimpleNamespace(budget_digest=full_budget_digest),
    )

    def validate_receipt(**kwargs):
        receipt_arguments.append(kwargs)
        return SimpleNamespace(
            receipt=SimpleNamespace(
                receipt_digest=DIGESTS["receipt"],
                budget_digest=kwargs["budget_digest"],
            ),
            marker=SimpleNamespace(marker_digest=DIGESTS["marker"]),
            output_mode=kwargs["output_mode"],
            provider_dispatch_allowed=True,
        )

    argv = [
        *_provider_args(tmp_path, "acquire-bank"),
        "--plan-bundle-root",
        str(tmp_path / "plan-bundle"),
    ]
    assert (
        _main(
            argv,
            command="acquire-bank",
            seen=seen,
            _ACQUISITION_BUNDLE_LOADER=lambda _path: bundle,
            receipt_validator=validate_receipt,
            service_input=object(),
        )
        == 0
    )
    assert receipt_arguments[0]["budget_digest"] == full_budget_digest
    assert seen[0].plan_bundle_root == tmp_path / "plan-bundle"
    assert json.loads(capsys.readouterr().out)["budget_digest"] == full_budget_digest


def test_production_acquisition_factory_loads_exact_bundle_and_task26_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from decimal import Decimal

    from tokenshare.executors.ai_api_request_identity import (
        PreparedOutboundRequestFactory,
    )
    from tokenshare.executors.response_bank import (
        ResponseBankInventoryRow,
        inventory_entry_id,
        response_bank_inventory_digest,
        semantic_slot_key,
        terminal_bank_entry_id,
    )
    from tokenshare.experiments.paper_paid_authorization import (
        compute_receipt_digest,
        output_root_path_digest,
        validate_paid_execution_receipt,
    )
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionRequest,
        SemanticInventoryPlan,
        create_acquisition_plan_bundle,
        establish_results_first_acquisition_authorization,
    )

    prepared = PreparedOutboundRequestFactory.prepare(
        body_obj={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "factory"}],
            "max_tokens": 300000,
        },
        base_url="https://api.deepseek.com",
        endpoint="/chat/completions",
        provider_config_digest="sha256:" + "a" * 64,
        entry_id="deepseek_v4_pro_exp1_baseline",
        configured_model="deepseek-v4-pro",
        effective_controls_digest="sha256:" + "b" * 64,
        plugin_id="factorization",
        plugin_version="factorization.v1",
        prompt_profile_id="factorization.range_search.v2",
        prompt_serialization_schema="tokenshare.prompt.v2",
        body_serialization_schema="deepseek.chat_completions.v1",
        case_id="case-factory",
        planned_ai_unit_id="unit-factory",
        sample_slot_index=0,
        replacement_slot=0,
    )
    slot = semantic_slot_key(
        case_record_digest="sha256:" + "c" * 64,
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:" + "d" * 64,
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
    )
    provisional = ResponseBankInventoryRow(
        inventory_entry_id="",
        semantic_slot_key=slot,
        case_record_digest="sha256:" + "c" * 64,
        planned_ai_unit_id=prepared.planned_ai_unit_id,
        sample_slot_index=0,
        replacement_slot=0,
        provider_config_digest=prepared.provider_config_digest,
        prompt_profile_digest="sha256:" + "d" * 64,
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        plugin_version=prepared.plugin_version,
        entry_id=terminal_bank_entry_id(
            semantic_slot_key=slot,
            inference_request_digest=prepared.inference_request_digest,
        ),
        body_digest=prepared.body_digest,
        inference_request_digest=prepared.inference_request_digest,
    )
    row = replace(
        provisional,
        inventory_entry_id=inventory_entry_id(provisional),
    )
    acquisition_request = AcquisitionRequest(
        inventory_row=row,
        prepared_request=prepared,
        provider_family="deepseek",
        api_key_env="UNREAD_FACTORY_SECRET",
        timeout_seconds=600,
        token_upper_bound=300000,
        cost_upper_bound=Decimal("1"),
        frozen_pricing=FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("0.5"),
            output_per_million_tokens=Decimal("1.5"),
        ),
        requested_at="2026-08-03T00:00:00Z",
    )
    bundle = create_acquisition_plan_bundle(
        tmp_path / "bundle",
        authorized_plan_digest=DIGESTS["plan"],
        profile_digest=DIGESTS["profile"],
        semantic_inventory_plan=SemanticInventoryPlan(
            schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
            inventory_digest=response_bank_inventory_digest((row,)),
            rows=(row,),
            condition_refs=(
                {
                    "condition_id": "condition-0",
                    "condition_digest": "sha256:" + "e" * 64,
                    "experiment_id": "exp1_real_ai_feasibility",
                    "worker_count": 1,
                    "repeat_id": 0,
                    "fault_type": "none",
                    "ablation_mode": "FULL",
                    "semantic_slot_keys": [row.semantic_slot_key],
                    "case_refs": [
                        {
                            "case_id": "case-0",
                            "case_record_digest": row.case_record_digest,
                            "semantic_slot_keys": [row.semantic_slot_key],
                        }
                    ],
                },
            ),
            exp2_online_condition_refs=(),
            max_concurrent_roots=1,
            expected_slot_count=1,
            terminal_provider_failure_count=0,
            terminal_success_count=0,
            terminal_unacquired_count=1,
        ),
        acquisition_requests=(acquisition_request,),
    )
    from tokenshare.experiments import run_paper_experiments

    self_consistent_but_nonofficial = SimpleNamespace(
        combined_profile_digest="sha256:" + "f" * 64,
        condition_candidate_count=1,
        semantic_inventory_plan=bundle.semantic_inventory_plan,
        acquisition_requests=(acquisition_request,) * 166,
    )
    monkeypatch.setattr(
        run_paper_experiments,
        "_build_results_first_matrix8_acquisition_plan",
        lambda **_kwargs: self_consistent_but_nonofficial,
    )
    with pytest.raises(ValueError, match="official plan digest mismatch"):
        run_paper_experiments.validate_results_first_matrix8_acquisition_bundle(
            bundle=bundle,
            bundle_root=tmp_path / "bundle",
        )
    output_root = tmp_path / "acquisition"
    receipt = {
        "schema_version": "tokenshare.paid_execution_receipt.v1",
        "receipt_digest": "",
        "scope": "epd027_full_bank_acquisition",
        "authorized_plan_digest": DIGESTS["plan"],
        "profile_digest": DIGESTS["profile"],
        "budget_digest": bundle.full_budget.budget_digest,
        "inventory_digest": bundle.inventory_digest,
        "prompt_admission_profile_digest": (
            prepared.prompt_admission_profile_digest
        ),
        "selected_experiments": ["exp2", "exp3", "exp4"],
        "output_root_path_digest": output_root_path_digest(output_root),
        "not_before": "2026-08-01T00:00:00Z",
        "expires_at": "2026-08-04T00:00:00Z",
        "user_approval_reference": "factory-test",
    }
    receipt["receipt_digest"] = compute_receipt_digest(receipt)
    authorization = validate_paid_execution_receipt(
        receipt=receipt,
        requested_scope="epd027_full_bank_acquisition",
        authorized_plan_digest=DIGESTS["plan"],
        profile_digest=DIGESTS["profile"],
        budget_digest=bundle.full_budget.budget_digest,
        inventory_digest=bundle.inventory_digest,
        prompt_admission_profile_digest=prepared.prompt_admission_profile_digest,
        selected_experiments=("exp2", "exp3", "exp4"),
        output_root=output_root,
        output_mode="new_run",
        action="dispatch",
        allow_provider_calls=True,
        now=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    request = pipeline.PipelineCommandRequest(
        command="acquire-bank",
        scope="epd027_full_bank_acquisition",
        evidence_class="online_real_provider",
        profile=PROFILE,
        provider_authorization=authorization,
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=bundle.inventory_digest,
        budget_mode="bounded",
        serialized_arguments={},
        plan_bundle_root=tmp_path / "bundle",
    )

    service = pipeline._acquisition_service_input_from_persisted_authorities(
        request
    )

    assert service.bundle == bundle
    assert service.acquisition_requests[0].prepared_request.body_bytes == (
        prepared.body_bytes
    )
    assert service.orchestrator_arguments["paid_authorization"] is authorization
    assert service.orchestrator_arguments["budget_ledger"].limits == (
        bundle.full_budget.to_limits()
    )
    assert [path.name for path in output_root.glob("*paid_output_binding*")] == [
        "paid_output_binding.v1.json"
    ]

    facility_root = tmp_path / "facility-acquisition"
    facility = establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=facility_root,
        output_mode="new_run",
        allow_provider_calls=True,
    )
    facility_service = pipeline._acquisition_service_input_from_persisted_authorities(
        replace(
            request,
            scope="results_first_matrix8_acquisition",
            evidence_class="results_first_real_provider_acquisition",
            provider_authorization=facility,
            output_root=facility_root,
            serialized_arguments={"results_first_matrix8": True},
        )
    )
    facility_manifest = facility_service.manifest

    assert facility_service.orchestrator_arguments["facility_authorization"] is facility
    assert "paid_authorization" not in facility_service.orchestrator_arguments
    assert facility_manifest.authorization_kind == "user_authorized_smoke_facility"
    assert not hasattr(facility_manifest, "created_by_paid_receipt_digest")
    assert [path.name for path in facility_root.glob("*facility_marker*")] == [
        "results_first_smoke_facility_marker.v1.json"
    ]


def test_audit_bank_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[2])


@pytest.mark.parametrize(
    ("receipt_mode", "expected_exit", "expected_dispatches"),
    (("none", 3, 0), ("bank", 3, 1), ("bank_and_l3", 0, 1)),
)
def test_trace_gate_phases_require_bank_before_dispatch_and_l3_only_for_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    receipt_mode: str,
    expected_exit: int,
    expected_dispatches: int,
) -> None:
    bank_inventory = DIGESTS["inventory"]
    l3_inventory = "sha256:" + "a" * 64
    l3_budget = "sha256:" + "b" * 64
    bundle = SimpleNamespace(
        authorized_plan_digest=DIGESTS["plan"],
        profile_digest=DIGESTS["profile"],
        inventory_digest=bank_inventory,
        prompt_admission_profile_digest=DIGESTS["admission"],
        full_budget=SimpleNamespace(budget_digest=DIGESTS["budget"]),
    )
    built_bindings = ()

    def build_authority(**kwargs):
        nonlocal built_bindings
        if kwargs["command"] == "run-online-checks":
            return SimpleNamespace(
                plan_digest=DIGESTS["plan"],
                inventory_digest=l3_inventory,
                budget_digest=l3_budget,
            )
        built_bindings = tuple(kwargs.get("paid_authorization_bindings", ()))
        base = _ready_formal_authority("run-trace")
        prerequisites = replace(
            base.gate_prerequisites,
            paid_authorizations=built_bindings,
        )
        scopes = frozenset(binding.authority.scope for binding in built_bindings)
        return replace(
            base,
            gate_prerequisites=prerequisites,
            publication_gate_factory=lambda _terminal: scopes,
        )

    def pre_gate(request):
        scopes = {
            binding.authority.scope
            for binding in request._service_input.prerequisites.paid_authorizations
        }
        ready = "epd027_full_bank_acquisition" in scopes
        return {
            **_ready_gate("formal_execution_gate"),
            "status": "ready" if ready else "blocked",
            "blocked_reasons": [] if ready else ["missing_paid_authorization:epd027_full_bank_acquisition"],
        }

    def post_gate(request):
        scopes = request._service_input.terminal_evidence
        ready = "epd027_l3_capability_and_online_checks" in scopes
        return {
            **_ready_gate("paper_publication_gate"),
            "status": "ready" if ready else "blocked",
            "blocked_reasons": [] if ready else ["missing_paid_authorization:epd027_l3_capability_and_online_checks"],
        }

    args = _offline_args(tmp_path, "run-trace", OFFLINE_COMMANDS[3][2])
    args.extend(
        ["--full-bank-acquisition-receipt", "bank-receipt.json"]
        if receipt_mode in {"bank", "bank_and_l3"}
        else []
    )
    if receipt_mode == "bank_and_l3":
        args.extend(
            [
                "--l3-online-check-receipt",
                "l3-receipt.json",
                "--l3-online-check-root",
                str(tmp_path / "l3"),
            ]
        )
    seen: list[object] = []
    validations: list[dict[str, object]] = []

    def validate(**kwargs):
        validations.append(kwargs)
        return _authorization(**kwargs)

    exit_code = _main(
        args,
        command="run-trace",
        seen=seen,
        receipt_validator=validate,
        _ACQUISITION_BUNDLE_LOADER=lambda _root: bundle,
        _FORMAL_AUTHORITY_BUILDER=build_authority,
        _validate_formal_execution_gate_adapter=pre_gate,
        _validate_paper_publication_gate_adapter=post_gate,
    )

    assert exit_code == expected_exit
    assert len(seen) == expected_dispatches
    assert all(
        call["output_mode"] == "resume"
        and call["action"] == "reconcile_close"
        and call["allow_provider_calls"] is False
        for call in validations
    )
    if receipt_mode == "bank_and_l3":
        assert [binding.authority.inventory_digest for binding in built_bindings] == [
            bank_inventory,
            l3_inventory,
        ]
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == ("completed" if expected_exit == 0 else "blocked")


def test_results_first_matrix8_trace_bypasses_only_facility_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    request = pipeline.PipelineCommandRequest(
        command="run-trace",
        scope="run-trace",
        evidence_class="real_model_trace_protocol_run",
        profile=PROFILE,
        provider_authorization=None,
        output_root=tmp_path / "trace",
        replay_input_root=None,
        external_bank_resolver=SimpleNamespace(),
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={"results_first_matrix8": True},
        plan_bundle_root=tmp_path / "plan",
    )
    factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
    factories["run-trace"] = lambda _request: object()
    monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)
    adapters = pipeline._AUTHORITATIVE_SERVICE_ADAPTERS
    original = adapters["run-trace"]
    adapters["run-trace"] = lambda resolved: calls.append("trace") or {
        "status": "completed_with_failures",
        "plan_digest": resolved.plan_digest,
        "inventory_digest": resolved.inventory_digest,
        "provider_calls": 0,
        "paper_eligible": False,
    }
    monkeypatch.setattr(
        pipeline,
        "_validate_formal_execution_gate_adapter",
        lambda _request: calls.append("execution_gate") or _ready_gate("execution"),
    )
    monkeypatch.setattr(
        pipeline,
        "_validate_paper_publication_gate_adapter",
        lambda _request: calls.append("publication_gate") or _ready_gate("publication"),
    )
    try:
        result = pipeline._default_delegate(request)
    finally:
        adapters["run-trace"] = original

    assert calls == ["trace"]
    assert result["provider_calls"] == 0
    assert result["paper_eligible"] is False


def test_results_first_matrix8_trace_adapter_runs_exactly_three_eight_root_smokes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_smoke

    calls: list[dict[str, object]] = []
    trace_context = object()
    batches = tuple(
        {
            "profile": object(),
            "execution_plan": SimpleNamespace(items=tuple(range(8))),
            "catalog_manifest": object(),
            "budget": object(),
            "ai_api_configs": {},
            "transport": object(),
            "real_transport": False,
            "hard_limits": {},
            "launch_manifest": {},
            "trace_context": trace_context,
        }
        for _ in range(3)
    )
    monkeypatch.setattr(
        paper_smoke,
        "execute_paper_smoke_suite",
        lambda **kwargs: calls.append(kwargs)
        or SimpleNamespace(status="completed", provider_attempt_count=0),
    )
    request = pipeline.PipelineCommandRequest(
        command="run-trace",
        scope="run-trace",
        evidence_class="real_model_trace_protocol_run",
        profile=PROFILE,
        provider_authorization=None,
        output_root=tmp_path,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={"results_first_matrix8": True},
        _service_input=pipeline.Matrix8TraceServiceInput(
            scope="run-trace",
            batches=batches,
        ),
    )

    result = pipeline._run_trace_adapter(request)

    assert len(calls) == 3
    assert all(call["trace_context"] is trace_context for call in calls)
    assert result["provider_calls"] == 0
    assert result["completed_experiment_count"] == 3
    assert result["root_run_count"] == 24


def test_run_online_checks_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_provider_command(tmp_path, capsys, "run-online-checks")


def test_run_exp1_online_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_provider_command(tmp_path, capsys, "run-exp1-online")


def test_run_exp5_capability_smoke_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_provider_command(tmp_path, capsys, "run-exp5-capability-smoke")


def test_run_exp5_online_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_provider_command(tmp_path, capsys, "run-exp5-online")


def test_render_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[4])


def test_replay_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[5])


def test_default_replay_loads_official_typed_handle_before_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenshare.experiments import paper_formal_runner

    calls: list[tuple[str, object]] = []
    typed_handle = object()
    source_root = tmp_path / "source"
    replay_root = tmp_path / "replay"

    def load_handle(value):
        calls.append(("load", value))
        return typed_handle

    def replay(*, output_root, replay_input_root):
        calls.append(("replay", replay_input_root))
        assert output_root == replay_root
        return SimpleNamespace(provider_calls=0)

    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_input_root",
        load_handle,
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "recompute_paper_traceability_replay",
        replay,
    )
    request = pipeline.PipelineCommandRequest(
        command="replay",
        scope="replay",
        evidence_class="offline_replay",
        profile=PROFILE,
        provider_authorization=None,
        output_root=replay_root,
        replay_input_root=source_root,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={},
    )

    assert pipeline._default_delegate(request)["provider_calls"] == 0
    assert calls == [("load", source_root), ("replay", typed_handle)]


@pytest.mark.parametrize(
    ("command", "extra"),
    (
        ("validate-profile", ()),
        ("audit-bank", ("--external-bank-root", "bank")),
    ),
)
def test_remaining_commands_use_default_named_adapters_without_delegates(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    extra: tuple[str, ...],
) -> None:
    if command == "audit-bank":
        monkeypatch.setattr(
            pipeline.ExternalBankResolverBinding,
            "open",
            lambda _self: SimpleNamespace(
                index=SimpleNamespace(
                    manifest=SimpleNamespace(
                        inventory_digest=DIGESTS["inventory"]
                    )
                )
            ),
        )
    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
    assert pipeline.main(_offline_args(tmp_path, command, extra)) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "completed"
    assert body["scope"] == command


def test_audit_cell_lineage_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[6])


def test_validate_formal_execution_gate_consumes_typed_policy_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenshare.experiments import paper_formal_gate

    selection = object()
    prerequisites = object()
    monkeypatch.setattr(
        paper_formal_gate,
        "formal_execution_gate",
        lambda selected, evidence: SimpleNamespace(
            to_dict=lambda: {
                "status": "ready",
                "stage": "formal_execution_gate",
                "classification": "formal",
                "blocked_reasons": [],
                "provider_calls": 0,
            }
        )
        if (selected, evidence) == (selection, prerequisites)
        else pytest.fail("execution gate received different typed authority"),
    )
    request = pipeline.PipelineCommandRequest(
        command="validate-formal-execution-gate",
        scope="validate-formal-execution-gate",
        evidence_class="offline_gate_parser_only",
        profile=PROFILE,
        provider_authorization=None,
        output_root=tmp_path,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={},
        _service_input=pipeline.FormalExecutionGateServiceInput(
            selection=selection,
            prerequisites=prerequisites,
        ),
    )

    assert pipeline._default_delegate(request)["status"] == "ready"


def test_public_typed_gate_entry_injects_authority_without_private_delegate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(pipeline, "execute_typed_gate_command"), (
        "pipeline must expose a normal typed gate execution entry"
    )
    from tokenshare.experiments import paper_formal_gate

    selection = object()
    prerequisites = object()
    monkeypatch.setattr(
        paper_formal_gate,
        "formal_execution_gate",
        lambda selected, evidence: paper_formal_gate.PaperGateDecision(
            stage="formal_execution_gate",
            status="ready",
            classification="formal",
            blocked_reasons=(),
        )
        if (selected, evidence) == (selection, prerequisites)
        else pytest.fail("public gate entry lost typed authority"),
    )

    result = pipeline.execute_typed_gate_command(
        command="validate-formal-execution-gate",
        profile=PROFILE,
        output_root=tmp_path,
        service_input=pipeline.FormalExecutionGateServiceInput(
            selection=selection,
            prerequisites=prerequisites,
        ),
    )

    assert result["status"] == "ready"
    assert result["stage"] == "formal_execution_gate"
    assert result["provider_calls"] == 0


def test_validate_paper_publication_gate_consumes_typed_policy_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenshare.experiments import paper_formal_gate

    selection = object()
    terminal = object()
    monkeypatch.setattr(
        paper_formal_gate,
        "paper_publication_gate",
        lambda selected, evidence: SimpleNamespace(
            to_dict=lambda: {
                "status": "blocked",
                "stage": "paper_publication_gate",
                "classification": "formal",
                "blocked_reasons": ["selected_terminal_evidence_missing:exp1"],
                "provider_calls": 0,
            }
        )
        if (selected, evidence) == (selection, terminal)
        else pytest.fail("publication gate received different typed authority"),
    )
    request = pipeline.PipelineCommandRequest(
        command="validate-paper-publication-gate",
        scope="validate-paper-publication-gate",
        evidence_class="offline_gate_parser_only",
        profile=PROFILE,
        provider_authorization=None,
        output_root=tmp_path,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={},
        _service_input=pipeline.PaperPublicationGateServiceInput(
            selection=selection,
            terminal_evidence=terminal,
        ),
    )

    assert pipeline._default_delegate(request)["status"] == "blocked"


@pytest.mark.parametrize(
    "command", (
        "validate-formal-execution-gate",
        "validate-paper-publication-gate",
    )
)
def test_gate_cli_without_typed_authority_fails_closed(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", lambda _path: PROFILE)
    extra = (
        "--output-root",
        str(tmp_path / ("formal-gate" if "execution" in command else "publication-gate")),
    )
    assert pipeline.main(_offline_args(tmp_path, command, extra)) == 3
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "blocked"
    assert body["provider_calls"] == 0
    assert body["message"] == "paper gate typed service input is missing"


@pytest.mark.parametrize("command", tuple(PROVIDER_COMMANDS))
@pytest.mark.parametrize("missing", ("receipt", "allow", "mode"))
def test_provider_commands_require_receipt_allow_flag_and_output_mode(
    tmp_path: Path, command: str, missing: str
) -> None:
    args = _provider_args(tmp_path, command)
    if missing == "receipt":
        index = args.index("--receipt")
        del args[index : index + 2]
    elif missing == "allow":
        args.remove("--allow-provider-calls")
    else:
        args.remove("--new-run")
    output_root = tmp_path / command
    assert pipeline.main(args) == 2
    assert not output_root.exists()


def test_exp5_capability_and_full_scopes_are_not_interchangeable(
    tmp_path: Path,
) -> None:
    observed: list[str] = []

    def reject_wrong_scope(**kwargs):
        observed.append(kwargs["requested_scope"])
        raise ValueError("receipt scope mismatch")

    assert _main(
        _provider_args(tmp_path, "run-exp5-capability-smoke"),
        command="run-exp5-capability-smoke",
        seen=[],
        receipt_validator=reject_wrong_scope,
    ) == 3
    assert observed == ["exp5_capability_smoke"]
    observed.clear()
    assert _main(
        _provider_args(tmp_path, "run-exp5-online"),
        command="run-exp5-online",
        seen=[],
        receipt_validator=reject_wrong_scope,
    ) == 3
    assert observed == ["exp5_full_online"]


@pytest.mark.parametrize(
    ("command", "expected_selected"),
    (
        ("acquire-bank", ("exp2", "exp3", "exp4")),
        ("run-online-checks", ("exp2", "exp3")),
        ("run-exp1-online", ("exp1",)),
        ("run-exp5-capability-smoke", ("exp5_capability",)),
        ("run-exp5-online", ("exp5",)),
    ),
)
def test_provider_receipt_validation_uses_selected_experiment_identity(
    tmp_path: Path,
    command: str,
    expected_selected: tuple[str, ...],
) -> None:
    observed: list[tuple[str, ...]] = []

    def reject_after_capture(**kwargs):
        observed.append(tuple(kwargs["selected_experiments"]))
        raise ValueError("captured selected experiments")

    assert _main(
        _provider_args(tmp_path, command),
        command=command,
        seen=[],
        receipt_validator=reject_after_capture,
    ) == 3
    assert observed == [expected_selected]


def test_external_bank_root_is_process_local_resolver_binding(
    tmp_path: Path, capsys
) -> None:
    seen: list[object] = []
    root = tmp_path / "private-bank-root"
    args = _offline_args(
        tmp_path, "audit-bank", ("--external-bank-root", str(root))
    )
    assert _main(args, command="audit-bank", seen=seen) == 0
    output = capsys.readouterr().out
    assert str(root) not in output
    assert seen[0].external_bank_resolver.root == root.resolve(strict=False)
    assert seen[0].serialized_arguments.get("external_bank_root") is None


@pytest.mark.parametrize("command", tuple(PROVIDER_COMMANDS))
def test_unlimited_budget_rejected_for_provider_commands(
    tmp_path: Path, command: str
) -> None:
    calls: list[str] = []

    def reject_unlimited(**kwargs):
        calls.append(kwargs["budget_mode"])
        raise ValueError("unlimited budget is forbidden for provider-writing mode")

    args = [*_provider_args(tmp_path, command), "--budget-mode", "unlimited"]
    assert _main(
        args,
        command=command,
        seen=[],
        budget_validator=reject_unlimited,
    ) == 3
    assert calls == ["unlimited"]


@pytest.mark.parametrize(
    "command", ("run-online-checks", "run-exp1-online", "run-exp5-online")
)
def test_online_formal_authority_precedes_receipt_marker_and_binds_actual_budget(
    tmp_path: Path,
    command: str,
) -> None:
    events: list[tuple[str, object]] = []
    actual_budget_digest = "sha256:" + "8" * 64

    def build_authority(**kwargs):
        events.append(("authority", None))
        return _ready_formal_authority(
            kwargs["command"],
            budget_digest=actual_budget_digest,
        )

    def validate_receipt(**kwargs):
        events.append(("receipt_marker", kwargs["budget_digest"]))
        return _authorization(**kwargs)

    assert _main(
        _provider_args(tmp_path, command),
        command=command,
        seen=[],
        _FORMAL_AUTHORITY_BUILDER=build_authority,
        receipt_validator=validate_receipt,
    ) == 0
    assert events == (
        [
            ("authority", None),
            ("receipt_marker", actual_budget_digest),
            ("authority", None),
        ]
        if command in pipeline._FORMAL_GATED_COMMANDS
        else [
            ("authority", None),
            ("receipt_marker", actual_budget_digest),
        ]
    )


def test_online_checks_factory_consumes_same_authority_and_paid_receipt(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_formal_callbacks import (
        PaperOnlineRootCallbackFactory,
    )

    output_root = tmp_path / "online-checks"
    authorization = PaidAuthorizationValidation(
        receipt=SimpleNamespace(budget_digest=DIGESTS["budget"]),
        marker=SimpleNamespace(),
        output_mode="new_run",
        authorization_state="authorized",
        provider_dispatch_allowed=True,
    )
    authority = SimpleNamespace(
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_digest=DIGESTS["budget"],
        keyword_arguments={
            "output_root": output_root,
            "real_transport": True,
        },
    )
    profile = SimpleNamespace(
        budget=SimpleNamespace(
            calls_hard_limit=516,
            tokens_hard_limit=171_708_288,
            cny_reservation_hard_limit="979.524864",
            cny_absolute_hard_stop="1000.0",
            tokens_per_call=332_768,
            cny_per_call_reservation="1.898304",
        ),
        prompt_admission_profile_digest=DIGESTS["admission"],
    )
    request = pipeline.PipelineCommandRequest(
        command="run-online-checks",
        scope="epd027_l3_capability_and_online_checks",
        evidence_class="online_real_provider",
        profile=profile,
        provider_authorization=authorization,
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
        _formal_authority=authority,
    )

    service = pipeline._formal_service_input_from_persisted_authorities(request)

    assert service.scope == "epd027_l3_capability_and_online_checks"
    assert service.keyword_arguments["output_root"] == output_root
    assert service.keyword_arguments["real_transport"] is True
    assert isinstance(
        service.keyword_arguments["online_root_callback_factory"],
        PaperOnlineRootCallbackFactory,
    )
    assert (output_root / "online_checks_budget.v1.sqlite3").is_file()


def test_capability_smoke_factory_rejects_full_exp5_command() -> None:
    request = pipeline.PipelineCommandRequest(
        command="run-exp5-online",
        scope="exp5_full_online",
        evidence_class="online_real_provider",
        profile=PROFILE,
        provider_authorization=None,
        output_root=Path("exp5-full"),
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
    )

    with pytest.raises(ValueError, match="full Exp5 scope"):
        pipeline._smoke_service_input_from_persisted_authorities(request)


def test_pipeline_owned_formal_orchestration_orders_typed_gates_and_propagates_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_gate

    assert hasattr(pipeline, "FormalRunGateServiceInput")
    module_tree = ast.parse(inspect.getsource(pipeline))
    top_level_imports = {
        node.module
        for node in module_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert "tokenshare.experiments.run_paper_experiments" not in top_level_imports

    authority = _ready_formal_authority("run-exp1-online")
    selection = authority.gate_selection
    prerequisites = authority.gate_prerequisites
    terminal = object()
    service_input = pipeline.FormalSuiteServiceInput(
        scope="exp1_full_online",
        keyword_arguments={},
    )
    scenarios = (
        ("blocked", "completed", "ready", ["pre"], "blocked"),
        ("ready", "blocked", "ready", ["pre", "child"], "blocked"),
        ("ready", "completed", "blocked", ["pre", "child", "post"], "blocked"),
        ("ready", "completed", "ready", ["pre", "child", "post"], "completed"),
    )
    for (
        pre_status,
        child_status,
        post_status,
        expected_events,
        expected_status,
    ) in scenarios:
        events: list[str] = []

        def pre_gate(selected, evidence):
            assert (selected, evidence) == (selection, prerequisites)
            events.append("pre")
            return paper_formal_gate.PaperGateDecision(
                stage="formal_execution_gate",
                status=pre_status,
                classification="formal",
                blocked_reasons=("pre-blocked",) if pre_status == "blocked" else (),
            )

        def child(_request):
            events.append("child")
            return pipeline.FormalSuiteServiceResult(
                public_result={
                    "status": child_status,
                    "provider_calls": 2,
                    "evidence_class": "online_real_provider",
                },
                terminal_result=_paper_suite_result(
                    tmp_path,
                    status=child_status,
                ),
            )

        def post_gate(selected, evidence):
            assert (selected, evidence) == (selection, terminal)
            events.append("post")
            return paper_formal_gate.PaperGateDecision(
                stage="paper_publication_gate",
                status=post_status,
                classification="formal",
                blocked_reasons=("post-blocked",) if post_status == "blocked" else (),
            )

        monkeypatch.setattr(paper_formal_gate, "formal_execution_gate", pre_gate)
        monkeypatch.setattr(paper_formal_gate, "paper_publication_gate", post_gate)
        monkeypatch.setitem(
            pipeline._AUTHORITATIVE_SERVICE_ADAPTERS,
            "run-exp1-online",
            child,
        )
        factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
        factories["run-exp1-online"] = lambda _request: service_input
        monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)
        request = pipeline.PipelineCommandRequest(
            command="run-exp1-online",
            scope="exp1_full_online",
            evidence_class="online_real_provider",
            profile=PROFILE,
            provider_authorization=SimpleNamespace(),
            output_root=tmp_path,
            replay_input_root=None,
            external_bank_resolver=None,
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
            budget_mode="bounded",
            serialized_arguments={},
            _formal_authority=EPD027FormalServiceAuthority(
                command=authority.command,
                plan_digest=authority.plan_digest,
                inventory_digest=authority.inventory_digest,
                budget_digest=authority.budget_digest,
                keyword_arguments=authority.keyword_arguments,
                gate_selection=selection,
                gate_prerequisites=prerequisites,
                publication_gate_factory=lambda _child: terminal,
            ),
        )

        result = pipeline._default_delegate(request)

        assert events == expected_events
        assert result["status"] == expected_status
        if expected_status == "blocked":
            assert result.get("blocked_reasons") or child_status == "blocked"


def test_pipeline_formal_orchestration_requires_process_local_typed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_called = False

    def child(_request):
        nonlocal child_called
        child_called = True
        return {"status": "completed", "provider_calls": 0}

    factories = dict(pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES)
    factories["run-exp1-online"] = lambda _request: pipeline.FormalSuiteServiceInput(
        scope="exp1_full_online",
        keyword_arguments={},
    )
    monkeypatch.setattr(pipeline, "_PRODUCTION_SERVICE_INPUT_FACTORIES", factories)
    monkeypatch.setitem(
        pipeline._AUTHORITATIVE_SERVICE_ADAPTERS,
        "run-exp1-online",
        child,
    )
    request = pipeline.PipelineCommandRequest(
        command="run-exp1-online",
        scope="exp1_full_online",
        evidence_class="online_real_provider",
        profile=PROFILE,
        provider_authorization=SimpleNamespace(),
        output_root=tmp_path,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
        _formal_authority=SimpleNamespace(keyword_arguments={}),
    )

    with pytest.raises(ValueError, match="process-local typed gate authority"):
        pipeline._default_delegate(request)
    assert child_called is False


@pytest.mark.parametrize(
    ("command", "scope", "selected_experiments"),
    (
        ("run-exp1-online", "exp1_full_online", ("exp1",)),
        ("run-exp5-online", "exp5_full_online", ("exp5",)),
    ),
)
def test_default_builder_normal_path_binds_validated_receipt_before_formal_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    scope: str,
    selected_experiments: tuple[str, ...],
) -> None:
    from copy import deepcopy

    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments import paper_catalog as paper_catalog_module
    from tokenshare.experiments import paper_runner
    from tokenshare.experiments import run_paper_experiments as paper_cli
    from tokenshare.experiments.paper_models import PaperSuiteResult, digest_json
    from tokenshare.experiments.paper_paid_authorization import (
        compute_receipt_digest,
        output_root_path_digest,
    )
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    assert pipeline._FORMAL_AUTHORITY_BUILDER is pipeline._build_formal_authority
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    repo_root = Path(__file__).resolve().parents[2]
    tracked = json.loads(
        (repo_root / "benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    environment = (
        paper_catalog_module._current_lean_environment_manifest_without_preflight()
    )
    object.__setattr__(environment, "environment_digest", tracked["environment_digest"])
    oracle_source = (
        repo_root / "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()
    original_file_digest = paper_catalog_module._file_digest

    def worktree_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_file_digest(resolved)
        source = resolved.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{sha256(source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: environment,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked["checker_implementation_digest"],
    )
    monkeypatch.setattr(paper_catalog_module, "_file_digest", worktree_file_digest)
    monkeypatch.setattr(
        paper_catalog_module,
        "default_lean_paper_environment_manifest",
        lambda: environment,
    )
    tracked_readiness = json.loads(
        (repo_root / "benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )

    def tracked_matrix_plan(*, catalog_manifest, **_kwargs):
        matrix = paper_runner._bind_lean_matrix_to_catalog(
            deepcopy(tracked_readiness),
            catalog_manifest=catalog_manifest,
        )
        matrix["schema_version"] = "tokenshare.lean_3x3_matrix_plan.v1"
        matrix_digest = digest_json(paper_runner._lean_matrix_digest_body(matrix))
        matrix["matrix_digest"] = matrix_digest
        matrix["task15_budget_input"]["matrix_digest"] = matrix_digest
        return matrix

    monkeypatch.setattr(paper_cli, "build_lean_3x3_matrix_plan", tracked_matrix_plan)
    profile = load_paper_pipeline_profile()
    output_root = tmp_path / command
    authority = pipeline._FORMAL_AUTHORITY_BUILDER(
        command=command,
        profile=profile,
        output_root=output_root,
        resume=False,
        plan_bundle_root=None,
        external_bank_resolver=None,
    )
    receipt = {
        "schema_version": "tokenshare.paid_execution_receipt.v1",
        "receipt_digest": "pending",
        "scope": scope,
        "authorized_plan_digest": authority.plan_digest,
        "profile_digest": profile.profile_digest,
        "budget_digest": authority.budget_digest,
        "inventory_digest": authority.inventory_digest,
        "prompt_admission_profile_digest": profile.prompt_admission_profile_digest,
        "selected_experiments": list(selected_experiments),
        "output_root_path_digest": output_root_path_digest(output_root),
        "not_before": "2026-01-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "user_approval_reference": "test-user-supplied-receipt",
    }
    receipt["receipt_digest"] = compute_receipt_digest(receipt)
    receipt_path = tmp_path / f"{command}-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    terminal_calls: list[dict[str, object]] = []

    def execute_terminal(**kwargs):
        terminal_calls.append(kwargs)
        return PaperSuiteResult(
            suite_id=str(kwargs["suite_id"]),
            status="completed",
            output_root=Path(kwargs["output_root"]).as_posix(),
            started_at="2026-08-04T00:00:00Z",
            ended_at="2026-08-04T00:00:01Z",
            experiment_ids=tuple(
                plan.experiment_id for plan in kwargs["dispatch_plans"]
            ),
            condition_count=1,
            run_count=1,
            task_count=1,
            provider_attempt_count=1,
            total_tokens=1,
            total_cost_estimate=0.0,
            paper_eligible=True,
            eligibility_report_ref=None,
            budget_ref=None,
            metrics_refs=(),
            audit_refs=(),
            error_summary=(),
        )

    monkeypatch.setattr(
        paper_formal_runner,
        "execute_paper_formal_suite",
        execute_terminal,
    )

    exit_code = pipeline.main(
        [
            command,
            "--profile",
            "benchmarks/paper/epd027_pipeline_profile.v1.json",
            "--receipt",
            str(receipt_path),
            "--allow-provider-calls",
            "--new-run",
            "--output-root",
            str(output_root),
            "--plan-digest",
            authority.plan_digest,
            "--inventory-digest",
            authority.inventory_digest,
            "--budget-mode",
            "bounded",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert terminal_calls, "default authority must pass pre-gate and reach official service"
    assert body["receipt_digest"] == receipt["receipt_digest"]
    assert body["stage"] == "paper_publication_gate"
    assert body["status"] == "blocked"
    assert exit_code == 3
    if command == "run-exp1-online":
        monkeypatch.undo()
        _assert_native_formal_authority_reaches_missing_receipt(
            tmp_path=tmp_path,
            capsys=capsys,
        )


def _assert_native_formal_authority_reaches_missing_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tokenshare.experiments.paper_pipeline_profile import (
        load_paper_pipeline_profile,
    )

    profile = load_paper_pipeline_profile()
    output_root = tmp_path / "native-formal"
    authority = pipeline._FORMAL_AUTHORITY_BUILDER(
        command="run-exp1-online",
        profile=profile,
        output_root=output_root,
        resume=False,
        plan_bundle_root=None,
        external_bank_resolver=None,
    )
    missing_receipt = tmp_path / "missing-paid-receipt.json"

    exit_code = pipeline.main(
        [
            "run-exp1-online",
            "--profile",
            "benchmarks/paper/epd027_pipeline_profile.v1.json",
            "--receipt",
            str(missing_receipt),
            "--allow-provider-calls",
            "--new-run",
            "--output-root",
            str(output_root),
            "--plan-digest",
            authority.plan_digest,
            "--inventory-digest",
            authority.inventory_digest,
            "--budget-mode",
            "bounded",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert body["status"] == "blocked"
    assert body["provider_calls"] == 0
    assert body["failure_kind"] == "pipeline_boundary_rejected"
    assert missing_receipt.name in body["message"]
