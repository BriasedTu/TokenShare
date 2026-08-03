from __future__ import annotations

import json
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.experiments import run_paper_pipeline as pipeline
from tokenshare.experiments.paper_paid_authorization import PaidAuthorizationValidation


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
        return {
            "status": "completed",
            "inventory_digest": DIGESTS["inventory"],
            "evidence_class": request.evidence_class,
            "provider_calls": 0,
            "service": command,
        }

    return invoke


def _authorization(**kwargs):
    return SimpleNamespace(
        receipt=SimpleNamespace(receipt_digest=DIGESTS["receipt"]),
        marker=SimpleNamespace(marker_digest=DIGESTS["marker"]),
        output_mode=kwargs["output_mode"],
        provider_dispatch_allowed=True,
    )


def _main(argv: list[str], *, command: str, seen: list[object], **overrides) -> int:
    dependencies = {
        "_PROFILE_LOADER": lambda _path: PROFILE,
        "_RECEIPT_LOADER": lambda _path: {"receipt_digest": DIGESTS["receipt"]},
        "_RECEIPT_VALIDATOR": _authorization,
        "_BUDGET_VALIDATOR": lambda **_kwargs: None,
        "_UTC_NOW": lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
        "_FORMAL_AUTHORITY_BUILDER": lambda **_kwargs: SimpleNamespace(
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
            budget_digest=DIGESTS["budget"],
            keyword_arguments={},
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
        "run-trace",
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
            or SimpleNamespace(status="completed", provider_attempt_count=0),
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
        lambda **_kwargs: SimpleNamespace(
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
            budget_digest=DIGESTS["budget"],
            keyword_arguments={},
        ),
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
        lambda **_kwargs: SimpleNamespace(
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
            budget_digest=DIGESTS["budget"],
            keyword_arguments={},
        ),
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
        "selected_experiments": ["epd027_full_bank_acquisition"],
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
        selected_experiments=("epd027_full_bank_acquisition",),
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


def test_audit_bank_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[2])


def test_run_trace_delegates_exact_service(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[3])


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
        (
            "validate-formal-execution-gate",
            ("--output-root", "formal-gate"),
        ),
        (
            "validate-paper-publication-gate",
            ("--output-root", "publication-gate"),
        ),
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


def test_validate_formal_execution_gate_is_parser_only(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[7])


def test_validate_paper_publication_gate_is_parser_only(tmp_path: Path, capsys) -> None:
    _assert_offline_command(tmp_path, capsys, *OFFLINE_COMMANDS[8])


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

    def build_authority(**_kwargs):
        events.append(("authority", None))
        return SimpleNamespace(
            plan_digest=DIGESTS["plan"],
            inventory_digest=DIGESTS["inventory"],
            budget_digest=actual_budget_digest,
            keyword_arguments={},
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
    assert events == [
        ("authority", None),
        ("receipt_marker", actual_budget_digest),
    ]


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
