from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import inspect
import json
import os
import pickle
import socket
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
import urllib.request
from unittest.mock import patch

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
from tokenshare.experiments.paper_experiment_contracts import formal_runtime_task_id
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
)
from tokenshare.experiments.paper_model_policy import (
    EXP5_PRICING_FRESHNESS_AS_OF,
)
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
    "run-results-first": "_representative_full_plan_smoke_adapter",
    "representative-full-plan-smoke": "_representative_full_plan_smoke_adapter",
    "audit-results-first-metric-merge": "_audit_results_first_metric_merge_adapter",
    "render-results-first-combined": "_render_results_first_combined_adapter",
}


def test_counting_transport_preserves_real_provider_identity_for_formal_guards() -> None:
    from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
    from tokenshare.experiments import factorization_paper_adapter
    from tokenshare.experiments import lean_paper_adapter

    inner = UrlLibSiliconFlowTransport()
    transport = pipeline._CountingRepresentativeTransport(inner)
    config = SimpleNamespace(provider_family="siliconflow")

    assert transport.tokenshare_transport_for_provider("siliconflow") is inner
    factorization_paper_adapter._validate_real_transport_mode(
        real_transport=True,
        transport=transport,
        ai_api_config=config,
    )
    lean_paper_adapter._validate_real_transport_mode(
        real_transport=True,
        transport=transport,
        ai_api_config=config,
    )


def test_counting_transport_propagates_only_explicit_offline_capture_marker() -> None:
    class ApprovedOfflineTransport:
        tokenshare_offline_capturing_transport = True

    class TruthyButUnapprovedTransport:
        tokenshare_offline_capturing_transport = 1

    approved = pipeline._CountingRepresentativeTransport(ApprovedOfflineTransport())
    unapproved = pipeline._CountingRepresentativeTransport(
        TruthyButUnapprovedTransport()
    )

    assert approved.tokenshare_offline_capturing_transport is True
    assert unapproved.tokenshare_offline_capturing_transport is False


def test_counting_transport_keeps_wrong_provider_and_ordinary_fake_fail_closed(
    guard_module_name: str,
) -> None:
    from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
    from tokenshare.experiments import factorization_paper_adapter
    from tokenshare.experiments import lean_paper_adapter

    guard_module = {
        "factorization_paper_adapter": factorization_paper_adapter,
        "lean_paper_adapter": lean_paper_adapter,
    }[guard_module_name]
    wrong_provider = pipeline._CountingRepresentativeTransport(
        UrlLibSiliconFlowTransport()
    )
    ordinary_fake = pipeline._CountingRepresentativeTransport(object())

    with pytest.raises(ValueError, match="UrlLibDeepSeekTransport"):
        guard_module._validate_real_transport_mode(
            real_transport=True,
            transport=wrong_provider,
            ai_api_config=SimpleNamespace(provider_family="deepseek"),
        )
    with pytest.raises(ValueError, match="UrlLibSiliconFlowTransport"):
        guard_module._validate_real_transport_mode(
            real_transport=True,
            transport=ordinary_fake,
            ai_api_config=SimpleNamespace(provider_family="siliconflow"),
        )


def test_counting_transport_dispatches_and_observes_exactly_once() -> None:
    from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport

    sentinel = object()
    calls = 0
    inner = UrlLibSiliconFlowTransport()

    def recording_post(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return sentinel

    inner.post_chat_completion = recording_post
    transport = pipeline._CountingRepresentativeTransport(inner)
    resolved = transport.tokenshare_transport_for_provider("siliconflow")

    result = resolved.post_chat_completion(
        normalized_absolute_endpoint="https://api.siliconflow.cn/v1/chat/completions",
        body_bytes=json.dumps({"model": "Qwen/Qwen3-14B"}).encode("utf-8"),
    )

    assert result is sentinel
    assert resolved is inner
    assert calls == 1
    assert transport.provider_calls == 1
    assert transport.observations == (
        (
            "https://api.siliconflow.cn/v1/chat/completions",
            "Qwen/Qwen3-14B",
        ),
    )


def test_canonical_and_legacy_results_first_cli_share_factory_and_adapter() -> None:
    parser = pipeline._build_argument_parser()
    canonical = parser.parse_args(
        [
            "run-results-first",
            "--selection",
            "full",
            "--output-root",
            "output",
            "--planning-artifact-root",
            "planning",
            "--plan-bundle-root",
            "bundle",
            "--new-run",
            "--plan-only",
        ]
    )
    legacy = parser.parse_args(
        [
            "representative-full-plan-smoke",
            "--output-root",
            "output",
            "--planning-artifact-root",
            "planning",
            "--plan-bundle-root",
            "bundle",
            "--new-run",
            "--plan-only",
        ]
    )

    assert canonical.selection == "full"
    assert legacy.selection == "representative"
    assert (
        pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES["run-results-first"]
        is pipeline._PRODUCTION_SERVICE_INPUT_FACTORIES[
            "representative-full-plan-smoke"
        ]
    )
    assert (
        pipeline._AUTHORITATIVE_SERVICE_ADAPTERS["run-results-first"]
        is pipeline._AUTHORITATIVE_SERVICE_ADAPTERS[
            "representative-full-plan-smoke"
        ]
    )
    assert "_run_representative_cli" not in inspect.getsource(pipeline.main)


def test_canonical_results_first_cli_uses_exact_roots_and_typed_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    output_root = tmp_path / "exact-output"
    planning_root = tmp_path / "exact-planning"
    bundle_root = tmp_path / "exact-bundle"
    authority = SimpleNamespace(
        output_root=output_root,
        coverage=SimpleNamespace(
            source_snapshot_digest="sha256:" + "1" * 64,
            coverage_digest="sha256:" + "2" * 64,
            condition_count=324,
            root_run_count=6_384,
        ),
        source_validation_digest="sha256:" + "3" * 64,
        full_budget=SimpleNamespace(budget_digest="sha256:" + "4" * 64),
        bundle=SimpleNamespace(profile_digest="sha256:" + "5" * 64),
    )
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(pipeline, "ResultsFirstExecutionAuthority", SimpleNamespace)

    def build(**kwargs: object):
        observed.append(dict(kwargs))
        return authority

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        build,
    )

    exit_code = pipeline.main(
        [
            "run-results-first",
            "--selection",
            "full",
            "--output-root",
            str(output_root),
            "--planning-artifact-root",
            str(planning_root),
            "--plan-bundle-root",
            str(bundle_root),
            "--new-run",
            "--plan-only",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert observed == [
        {
            "selection": "full",
            "output_root": output_root,
            "planning_artifact_root": planning_root,
            "plan_bundle_root": bundle_root,
            "resume": False,
        }
    ]
    assert body["status"] == "ready"
    assert body["scope"] == "run-results-first"
    assert body["selection"] == "full"
    assert body["provider_calls"] == 0


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
        "provider_calls_missing_reason": None,
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

            def acquire_all(self, requests, **kwargs):
                calls.append(("acquire_all", (tuple(requests), kwargs)))
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
            max_in_flight=7,
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
    if command == "acquire-bank":
        assert calls[1][0] == "acquire_all"
        assert calls[1][1][1] == {"max_in_flight": 7}


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
        "provider_calls": None,
        "total_current_provider_calls": None,
        "provider_calls_missing_reason": (
            "pipeline_boundary_provider_accounting_unavailable"
        ),
        "total_current_spend": None,
        "total_spend_missing_reason": "pipeline_boundary_spend_accounting_unavailable",
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
        "provider_calls_missing_reason": None,
    }


def test_pipeline_source_has_no_matrix8_authority_or_flags() -> None:
    source = inspect.getsource(pipeline)

    assert "results_first_matrix8" not in source
    assert "--results-first-matrix8" not in source


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


def test_service_result_never_coerces_missing_provider_accounting_to_zero() -> None:
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

    result = pipeline._json_result(
        request=request,
        delegated={
            "status": "blocked",
            "plan_digest": DIGESTS["plan"],
            "inventory_digest": DIGESTS["inventory"],
        },
    )

    assert result["provider_calls"] is None
    assert result["provider_calls_missing_reason"] == (
        "delegated_provider_call_accounting_missing"
    )


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


def test_production_acquisition_factory_loads_exact_bundle_and_paid_binding(
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
    assert body["provider_calls"] is None
    assert body["total_current_provider_calls"] is None
    assert body["provider_calls_missing_reason"] == (
        "pipeline_boundary_provider_accounting_unavailable"
    )
    assert body["total_current_spend"] is None
    assert body["total_spend_missing_reason"] == (
        "pipeline_boundary_spend_accounting_unavailable"
    )
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


def test_capability_smoke_factory_requires_and_forwards_scope_preparation_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = _authorization(
        requested_scope="exp5_capability_smoke",
        output_mode="new_run",
        budget_digest=DIGESTS["budget"],
    )
    request = pipeline.PipelineCommandRequest(
        command="run-exp5-capability-smoke",
        scope="exp5_capability_smoke",
        evidence_class="online_real_provider",
        profile=PROFILE,
        provider_authorization=authorization,
        output_root=tmp_path / "capability-smoke",
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={"resume": False},
    )
    with pytest.raises(ValueError, match="scope preparation root"):
        pipeline._smoke_service_input_from_persisted_authorities(request)

    captured: dict[str, object] = {}
    authority = SimpleNamespace(
        scope="exp5_capability_smoke",
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_digest=DIGESTS["budget"],
        keyword_arguments={
            "execution_plan": SimpleNamespace(
                execution_plan_digest=DIGESTS["inventory"]
            )
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_SMOKE_AUTHORITY_BUILDER",
        lambda **kwargs: captured.update(kwargs) or authority,
    )
    monkeypatch.setattr(
        pipeline,
        "_EXP5_CAPABILITY_KEY_INJECTOR",
        lambda path: captured.update(local_config_path=path),
    )
    service = pipeline._smoke_service_input_from_persisted_authorities(
        replace(
            request,
            serialized_arguments={
                "resume": False,
                "results_first_scope_preparation_root": "scope-preparation",
                "local_ai_api_config": "local-config.json",
            },
        )
    )

    assert captured["results_first_scope_preparation_root"] == "scope-preparation"
    assert captured["local_config_path"] == "local-config.json"
    assert service.scope == "exp5_capability_smoke"


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
    assert body["provider_calls"] is None
    assert body["total_current_provider_calls"] is None
    assert body["provider_calls_missing_reason"] == (
        "pipeline_boundary_provider_accounting_unavailable"
    )
    assert body["total_current_spend"] is None
    assert body["total_spend_missing_reason"] == (
        "pipeline_boundary_spend_accounting_unavailable"
    )
    assert body["failure_kind"] == "pipeline_boundary_rejected"
    assert missing_receipt.name in body["message"]


_FORMAL_EXP5_ENDPOINTS = (
    ("glm_5_2_exp5_v3", "zai-org/GLM-5.2"),
    ("qwen3_14b_exp5_v3", "Qwen/Qwen3-14B"),
    ("minimax_m2_5_exp5_v3", "MiniMaxAI/MiniMax-M2.5"),
    ("deepseek_v3_pro_exp5_v3", "Pro/deepseek-ai/DeepSeek-V3"),
)


def _representative_pipeline_authority(
    tmp_path: Path,
    *,
    exp5_condition_count: int = 16,
    exp5_slots_per_condition: int = 1,
    selection_kind: str = "representative_exp1_exp3_exp5",
    trace_experiment_ids: tuple[str, ...] = (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
    ),
):
    experiment_ids = trace_experiment_ids
    trace_conditions = tuple(
        SimpleNamespace(
            experiment_id=experiment_id,
            condition_id=f"{experiment_id}-c0",
            model_endpoint_identity_digest="sha256:" + str(index + 1) * 64,
        )
        for index, experiment_id in enumerate(experiment_ids)
    )
    exp5_conditions = tuple(
        SimpleNamespace(
            experiment_id="exp5_real_ai_model_endpoint_comparison",
            condition_id=f"exp5-c{index}",
            model_endpoint_identity_digest=(
                "sha256:" + str((index % 4) + 5) * 64
            ),
            provider_config_id="siliconflow",
            model_entry_id=_FORMAL_EXP5_ENDPOINTS[index % 4][0],
            provider_family="siliconflow",
            provider_model_id=_FORMAL_EXP5_ENDPOINTS[index % 4][1],
        )
        for index in range(exp5_condition_count)
    )
    conditions = (*trace_conditions, *exp5_conditions)
    root_filter = {
        condition.condition_id: (f"case-{index}",)
        for index, condition in enumerate(trace_conditions)
    } | {
        condition.condition_id: tuple(
            f"case-exp5-{index}-{slot}"
            for slot in range(exp5_slots_per_condition)
        )
        for index, condition in enumerate(exp5_conditions)
    }
    coverage = SimpleNamespace(
        conditions=conditions,
        roots=tuple(SimpleNamespace(condition=condition) for condition in conditions),
        root_case_filter=root_filter,
        condition_count=len(conditions),
        root_run_count=sum(len(case_ids) for case_ids in root_filter.values()),
        selection_kind=selection_kind,
        coverage_digest="sha256:" + "a" * 64,
        source_snapshot_digest="sha256:" + "b" * 64,
        provider_calls_made=0,
    )
    authority = object.__new__(pipeline.ResultsFirstExecutionAuthority)
    values = {
        "source_validation_digest": "sha256:" + "c" * 64,
        "full_dispatch_plans": tuple(
            SimpleNamespace(experiment_id=experiment_id)
            for experiment_id in (*experiment_ids, "exp5_real_ai_model_endpoint_comparison")
        ),
        "catalog_manifest": object(),
        "full_snapshot": object(),
        "full_prepared_inventory": object(),
        "full_budget": SimpleNamespace(budget_digest="sha256:" + "d" * 64),
        "ai_api_configs": {
            "baseline": object(),
            "siliconflow": SimpleNamespace(
                entries=tuple(
                    SimpleNamespace(
                        entry_id=_FORMAL_EXP5_ENDPOINTS[index][0],
                        enabled=True,
                        base_url="https://api.example.test/v1",
                        endpoint="/chat/completions",
                        model=_FORMAL_EXP5_ENDPOINTS[index][1],
                    )
                    for index in range(4)
                )
            ),
        },
        "coverage": coverage,
        "execution_budget_projection": SimpleNamespace(
            hard_limits={"max_total_provider_attempts": 99}
        ),
        "bundle": SimpleNamespace(
            source_snapshot_digest=coverage.source_snapshot_digest,
            coverage_digest=coverage.coverage_digest,
            semantic_inventory_plan=object(),
        ),
        "bundle_root": tmp_path / "bundle",
        "output_root": tmp_path,
        "resume": False,
        "policy": pipeline._RESULTS_FIRST_EXECUTION_POLICY,
        "provider_calls_made": 0,
    }
    for name, value in values.items():
        object.__setattr__(authority, name, value)
    return authority


def _clone_unchecked_results_first_authority(authority, **changes):
    clone = object.__new__(pipeline.ResultsFirstExecutionAuthority)
    for name in pipeline.ResultsFirstExecutionAuthority.__dataclass_fields__:
        object.__setattr__(clone, name, changes.get(name, getattr(authority, name)))
    return clone


@pytest.fixture(autouse=True)
def _project_synthetic_parent_exp5_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """为绕过 typed constructor 的局部 fixture 保持真实 parent→Exp5 投影语义。"""

    original = pipeline._selection_exact_exp5_execution_projection

    def project(authority):
        if type(getattr(authority, "full_snapshot", None)) is not object:
            return original(authority)
        parent = authority.coverage
        conditions = tuple(
            condition
            for condition in parent.conditions
            if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
        )
        if not conditions:
            return original(authority)
        root_case_filter = {
            condition.condition_id: parent.root_case_filter[condition.condition_id]
            for condition in conditions
        }
        coverage = SimpleNamespace(
            conditions=conditions,
            roots=tuple(
                root for root in parent.roots if root.condition in conditions
            ),
            root_case_filter=root_case_filter,
            condition_count=len(conditions),
            root_run_count=sum(
                len(case_ids) for case_ids in root_case_filter.values()
            ),
            selection_kind="filtered",
            coverage_digest="sha256:" + "f" * 64,
            source_snapshot_digest=parent.source_snapshot_digest,
            provider_calls_made=0,
        )
        return SimpleNamespace(coverage=coverage)

    monkeypatch.setattr(
        pipeline,
        "_selection_exact_exp5_execution_projection",
        project,
    )


def _install_minimal_typed_results_first_trace_fixture(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: tuple[pipeline.ResultsFirstExecutionAuthority, ...],
):
    from tokenshare.experiments import paper_exp1_trace_reuse
    from tokenshare.experiments.paper_exp1_trace_reuse import (
        build_exp1_attempt_history_authority,
        persist_exp1_attempt_history_authority,
    )
    from tests.experiments.test_exp1_trace_reuse_semantics import (
        _SettledLedger,
        _multi_sample_response_bank,
        _source_plan_for_resolver,
    )

    execution_roots = tuple(authority.output_root for authority in authorities)
    snapshot_authority = _closure_snapshot_authority(
        tmp_path / "typed-closure-authority"
    )
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=snapshot_authority,
        output_root=snapshot_authority.output_root,
    )
    typed_snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=snapshot_authority.output_root,
    )
    expected_roots = {
        Path(root).resolve(strict=False) for root in execution_roots
    }

    def load_snapshot(*, output_root, suite_root=None, expected_ref=None):
        assert Path(output_root).resolve(strict=False) in expected_roots
        assert suite_root is None
        assert expected_ref is None
        return typed_snapshot

    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        load_snapshot,
    )
    case_digest = "sha256:" + "5" * 64
    resolver = _multi_sample_response_bank(
        tmp_path / "strict-minimal-response-bank",
        case_digest=case_digest,
        sample_slots=(0,),
        attempts_per_sample=1,
    )
    source_plan = _source_plan_for_resolver(
        resolver,
        case_id="factor-q1",
        case_digest=case_digest,
    )
    attempt_history = build_exp1_attempt_history_authority(
        resolver=resolver,
        source_exp1_inventory_plan=source_plan,
        budget_ledger=_SettledLedger(resolver),
    )
    persist_exp1_attempt_history_authority(
        resolver_root=resolver.root_path,
        authority=attempt_history,
    )
    for authority in authorities:
        authority.bundle.semantic_inventory_plan = source_plan

    def finalize_protocol_outcomes(**kwargs):
        assert kwargs["authority"] == attempt_history
        assert kwargs["authority_root"] in execution_roots
        return (
            attempt_history,
            Path(kwargs["authority_root"])
            / "exp1_protocol_classified_attempt_history_authority.v1.json",
        )

    monkeypatch.setattr(
        paper_exp1_trace_reuse,
        "finalize_exp1_protocol_outcomes_from_formal_trace",
        finalize_protocol_outcomes,
    )
    for root in execution_roots:
        for relative_path in (
            Path("exp1_attempt_history_authority.final.v1.json"),
            Path("results_first_trace_router_authority.v1.json"),
            Path("exp2-projected-response-bank")
            / "exp2_projection_derivation.v1.json",
        ):
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")

    def minimal_trace_closure_preflight(*, suite_root: Path):
        selected_authority = next(
            (
                authority
                for authority in authorities
                if Path(suite_root).resolve(strict=False)
                == (
                    authority.output_root
                    / (
                        "exp1-exp4-trace"
                        if tuple(
                            experiment_id
                            for experiment_id in pipeline._results_first_experiment_ids_for_coverage(
                                coverage=authority.coverage
                            )
                            if experiment_id
                            != "exp5_real_ai_model_endpoint_comparison"
                        )
                        == pipeline._REPRESENTATIVE_TRACE_EXPERIMENT_IDS
                        else "exp1-exp3-trace"
                    )
                ).resolve(strict=False)
            ),
            None,
        )
        if selected_authority is None:
            pytest.fail("minimal trace fixture audited an unexpected suite root")
        trace_conditions = tuple(
            condition
            for condition in selected_authority.coverage.conditions
            if condition.experiment_id != "exp5_real_ai_model_endpoint_comparison"
        )
        root_count = sum(
            len(selected_authority.coverage.root_case_filter[condition.condition_id])
            for condition in trace_conditions
        )
        return pipeline.ResultsFirstTraceClosurePreflight(
            condition_count=len(trace_conditions),
            root_count=root_count,
            checkpoint_count=root_count,
            attempt_count=root_count,
            current_provider_attempt_count=0,
            legacy_attempt_row_count=0,
            positive_provider_attempt_ordinal_count=root_count,
            source_consumption_count=root_count,
            checkpoint_inventory_digest="sha256:" + "4" * 64,
        )

    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        minimal_trace_closure_preflight,
    )
    return resolver


class _NoopExp5Transport:
    def post_chat_completion(self, **_kwargs):
        return object()


def _install_recording_closure_snapshot_persister(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authority: object,
) -> list[dict[str, object]]:
    from tokenshare.experiments.paper_models import digest_json

    calls: list[dict[str, object]] = []

    def persist(*, authority: object, output_root: Path, suite_id: str):
        assert authority is expected_authority
        assert Path(output_root) == expected_authority.output_root
        assert suite_id == "results_first_exp1_exp4_trace"
        assert expected_authority.provider_calls_made == 0
        ref_body = {
            "schema_version": "tokenshare.results_first_closure_replay_snapshot_ref.v1",
            "descriptor_path": (
                "closure-replay-snapshot/closure_replay_snapshot.v1.json"
            ),
            "descriptor_digest": "sha256:" + "1" * 64,
            "pickle_path": (
                "closure-replay-snapshot/closure_replay_snapshot.v1.pickle"
            ),
            "snapshot_digest": "sha256:" + "2" * 64,
            "pickle_sha256": "sha256:" + "3" * 64,
        }
        ref = {**ref_body, "ref_digest": digest_json(ref_body)}
        ref_path = (
            Path(output_root)
            / "closure-replay-snapshot"
            / "closure_replay_snapshot_ref.v1.json"
        )
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(
            json.dumps(ref, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        calls.append(
            {
                "authority": authority,
                "output_root": Path(output_root),
                "suite_id": suite_id,
                "ref_path": ref_path,
            }
        )
        return ref

    expected_authority = authority
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_CLOSURE_SNAPSHOT_PERSISTER",
        persist,
    )
    return calls


def _install_fake_exp5_ledger_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authority: object,
    current_calls: int,
    current_spend: float | None,
    missing_reason: str | None = None,
    total_calls: int | None = None,
    current_condition_ids: tuple[str, ...] | None = None,
) -> object:
    exp5_condition_ids = tuple(
        condition.condition_id
        for condition in authority.coverage.conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )

    def distribute(
        call_count: int,
        *,
        selected_condition_ids: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        selected = (
            exp5_condition_ids
            if selected_condition_ids is None
            else selected_condition_ids
        )
        if call_count > len(selected):
            raise ValueError("fake Exp5 ledger call distribution is incomplete")
        dispatched = frozenset(selected[:call_count])
        return {
            condition_id: int(condition_id in dispatched)
            for condition_id in exp5_condition_ids
        }

    total = current_calls if total_calls is None else total_calls
    expected_by_condition = {condition_id: 1 for condition_id in exp5_condition_ids}
    current_by_condition = distribute(
        current_calls,
        selected_condition_ids=current_condition_ids,
    )
    total_by_condition = distribute(total)
    prepared_slots = tuple(
        (condition_id, f"case-{condition_id}", f"unit-{condition_id}")
        for condition_id in exp5_condition_ids
    )
    current_terminal_slots = tuple(
        slot
        for slot in prepared_slots
        if current_by_condition[slot[0]] == 1
    )
    total_terminal_slots = tuple(
        slot
        for slot in prepared_slots
        if total_by_condition[slot[0]] == 1
    )
    factory = SimpleNamespace(
        serialize_roots=False,
        audit_usage=lambda: SimpleNamespace(
            current_provider_calls=current_calls,
            total_provider_calls=total,
            current_spend=(
                None if current_spend is None else Decimal(str(current_spend))
            ),
            current_spend_missing_reason=missing_reason,
            total_spend=(
                None if current_spend is None else Decimal(str(current_spend))
            ),
            total_spend_missing_reason=missing_reason,
            ambiguous_count=0,
            usage_missing_count=int(current_spend is None and current_calls > 0),
            expected_provider_calls_by_condition=expected_by_condition,
            current_provider_calls_by_condition=current_by_condition,
            total_provider_calls_by_condition=total_by_condition,
            current_terminal_provider_calls_by_condition=current_by_condition,
            total_terminal_provider_calls_by_condition=total_by_condition,
            prepared_canonical_slots=prepared_slots,
            current_terminal_slots=current_terminal_slots,
            total_terminal_slots=total_terminal_slots,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda **_kwargs: factory,
    )
    return factory


def _invoke_fake_exp5_transport(
    *,
    transport,
    conditions,
    call_count: int,
    omit_endpoint: bool,
    observation_override: tuple[str, str] | None = None,
) -> None:
    endpoint_conditions = tuple(
        dict.fromkeys(
            condition.model_endpoint_identity_digest for condition in conditions
        )
    )
    allowed = endpoint_conditions[:-1] if omit_endpoint else endpoint_conditions
    condition_by_digest = {
        condition.model_endpoint_identity_digest: condition
        for condition in conditions
    }
    for index in range(call_count):
        condition = condition_by_digest[allowed[index % len(allowed)]]
        endpoint, model = (
            observation_override
            if index == 0 and observation_override is not None
            else (
                "https://api.example.test/v1/chat/completions",
                condition.provider_model_id,
            )
        )
        transport.post_chat_completion(
            body_bytes=json.dumps({"model": model}).encode("utf-8"),
            normalized_absolute_endpoint=endpoint,
        )


@pytest.mark.parametrize(
    ("condition_attempts", "transport_calls", "omit_endpoint", "accepted"),
    (
        (0, 0, False, False),
        (1, 15, False, False),
        (1, 16, True, False),
        (1, 16, False, True),
    ),
)
def test_representative_exp5_requires_all_condition_calls_and_four_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    condition_attempts: int,
    transport_calls: int,
    omit_endpoint: bool,
    accepted: bool,
) -> None:
    authority = _representative_pipeline_authority(tmp_path)
    _install_fake_exp5_ledger_factory(
        monkeypatch,
        authority=authority,
        current_calls=transport_calls,
        current_spend=(0.0 if transport_calls == 0 else 1.0),
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    invocations = 0

    def fake_execute(**kwargs):
        nonlocal invocations
        invocations += 1
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        active_experiments = tuple(
            dict.fromkeys(
                condition.experiment_id for condition in active_conditions
            )
        )
        if invocations == 2:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=transport_calls,
                omit_endpoint=omit_endpoint,
            )
        attempts = 0 if invocations == 1 else condition_attempts
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=active_experiments,
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=(
                0 if invocations == 1 else attempts * len(selected)
            ),
            total_cost_estimate=0.0 if invocations == 1 else 1.0,
            total_cost_estimate_status=(
                "not_applicable"
                if invocations == 1
                else "single_currency_estimate"
            ),
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": attempts,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )

    invoke = lambda: pipeline.execute_representative_full_plan_smoke(
        authority=authority,
        response_bank_resolver=response_bank_resolver,
        exp5_transport=_NoopExp5Transport(),
    )
    if accepted:
        result = invoke()
        assert result.exp5_current_provider_calls == 16
    else:
        with pytest.raises(pipeline.RepresentativeSmokeExecutionError):
            invoke()


@pytest.mark.parametrize(
    ("prior_indices", "current_calls", "accepted", "expected_spend"),
    (
        (tuple(range(16)), 0, True, 0.0),
        (tuple(range(8)), 8, True, 0.5),
        (tuple(range(8)), 7, False, None),
        (tuple(index for index in range(16) if index % 4 != 0), 4, True, 0.25),
    ),
)
def test_representative_exp5_resume_uses_current_attempt_and_cost_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_indices: tuple[int, ...],
    current_calls: int,
    accepted: bool,
    expected_spend: float | None,
) -> None:
    from tokenshare.experiments.paper_formal_runner import (
        PaperFormalResumeBaseline,
    )

    authority = _clone_unchecked_results_first_authority(
        _representative_pipeline_authority(tmp_path),
        resume=True,
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    exp5_suite_root = authority.output_root / "exp5-online"
    exp5_suite_root.mkdir(parents=True, exist_ok=True)
    (exp5_suite_root / "suite_manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "load_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: object(),
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    exp5_conditions = tuple(
        condition
        for condition in conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    prior_conditions = tuple(exp5_conditions[index] for index in prior_indices)
    prior_ids = {condition.condition_id for condition in prior_conditions}
    current_conditions = tuple(
        condition
        for condition in exp5_conditions
        if condition.condition_id not in prior_ids
    )
    exp5_factory = _install_fake_exp5_ledger_factory(
        monkeypatch,
        authority=authority,
        current_calls=current_calls,
        total_calls=16,
        current_spend=expected_spend,
        missing_reason=(
            "exp5_ledger_usage_missing"
            if current_calls and expected_spend is None
            else None
        ),
        current_condition_ids=tuple(
            condition.condition_id for condition in current_conditions
        ),
    )
    baseline_cost = len(prior_conditions) / len(exp5_conditions)
    baseline = PaperFormalResumeBaseline(
        provider_attempts_by_condition={
            condition.condition_id: 1 for condition in prior_conditions
        },
        provider_attempt_count=len(prior_conditions),
        total_cost_estimate=baseline_cost,
        cost_estimate_by_currency={"CNY": baseline_cost},
        total_cost_estimate_status="single_currency_estimate",
    )
    loaded_roots: list[Path] = []
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_FORMAL_RESUME_BASELINE_LOADER",
        lambda root: loaded_roots.append(Path(root)) or baseline,
        raising=False,
    )
    invocations = 0

    def fake_execute(**kwargs):
        nonlocal invocations
        invocations += 1
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        active_experiments = tuple(
            dict.fromkeys(
                condition.experiment_id for condition in active_conditions
            )
        )
        if invocations == 2 and current_calls:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=current_conditions,
                call_count=current_calls,
                omit_endpoint=False,
            )
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=active_experiments,
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=0 if invocations == 1 else 16,
            total_cost_estimate=0.0 if invocations == 1 else 1.0,
            cost_estimate_by_currency=(
                None if invocations == 1 else {"CNY": 1.0}
            ),
            total_cost_estimate_status=(
                "not_applicable"
                if invocations == 1
                else "single_currency_estimate"
            ),
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": 1,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())

    invoke = lambda: pipeline.execute_representative_full_plan_smoke(
        authority=authority,
        response_bank_resolver=response_bank_resolver,
        exp5_transport=_NoopExp5Transport(),
    )
    if accepted:
        result = invoke()
        assert result.exp5_current_provider_calls == current_calls
        assert result.exp5_current_spend == expected_spend
        assert loaded_roots == [authority.output_root / "exp5-online"]
        if current_calls == 0:
            audit = exp5_factory.audit_usage()
            assert audit.current_terminal_slots == ()
            condition_by_id = {
                condition.condition_id: condition
                for condition in exp5_conditions
            }
            assert {
                condition_by_id[slot[0]].model_entry_id
                for slot in audit.total_terminal_slots
            } == {entry_id for entry_id, _model in _FORMAL_EXP5_ENDPOINTS}
    else:
        with pytest.raises(pipeline.RepresentativeSmokeExecutionError):
            invoke()


@pytest.mark.parametrize(
    ("trace_manifest_exists", "exp5_manifest_exists"),
    ((False, False), (True, True), (False, True), (True, False)),
)
def test_results_first_resume_is_scoped_to_each_existing_suite_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trace_manifest_exists: bool,
    exp5_manifest_exists: bool,
) -> None:
    from tokenshare.experiments import paper_exp1_trace_reuse

    authority = _clone_unchecked_results_first_authority(
        _representative_pipeline_authority(tmp_path),
        resume=True,
    )
    authority.bundle.semantic_inventory_plan = SimpleNamespace(
        inventory_digest="sha256:" + "3" * 64
    )
    trace_root = authority.output_root / "exp1-exp4-trace"
    exp5_root = authority.output_root / "exp5-online"
    for root, exists in (
        (trace_root, trace_manifest_exists),
        (exp5_root, exp5_manifest_exists),
    ):
        if exists:
            root.mkdir(parents=True, exist_ok=True)
            (root / "suite_manifest.json").write_text("{}\n", encoding="utf-8")

    resolver = SimpleNamespace(
        root_path=tmp_path / "response-bank",
        index=SimpleNamespace(
            manifest=SimpleNamespace(manifest_digest="sha256:" + "1" * 64)
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: SimpleNamespace(snapshot_digest="sha256:" + "2" * 64),
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    monkeypatch.setattr(
        paper_exp1_trace_reuse,
        "load_exp1_attempt_history_authority",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        paper_exp1_trace_reuse,
        "finalize_exp1_protocol_outcomes_from_formal_trace",
        lambda **_kwargs: object(),
    )

    executor_calls: list[tuple[str, bool]] = []

    def fake_execute(**kwargs):
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in authority.coverage.conditions
            if condition.condition_id in selected
        )
        executor_calls.append((str(kwargs["suite_id"]), bool(kwargs["resume"])))
        return SimpleNamespace(
            status="completed",
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(
                len(authority.coverage.root_case_filter[condition_id])
                for condition_id in selected
            ),
            provider_attempt_count=0,
            total_cost_estimate=0.0,
            total_cost_estimate_status="not_applicable",
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": 0,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    baseline_roots: list[Path] = []
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_FORMAL_RESUME_BASELINE_LOADER",
        lambda root: baseline_roots.append(Path(root))
        or pipeline._zero_formal_resume_baseline(),
    )
    ledger_audit = SimpleNamespace(
        current_provider_calls=0,
        current_spend=Decimal("0"),
        current_spend_missing_reason=None,
    )
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda **_kwargs: SimpleNamespace(audit_usage=lambda: ledger_audit),
    )
    monkeypatch.setattr(
        pipeline,
        "_validate_representative_exp5_terminal",
        lambda **_kwargs: None,
    )

    pipeline.execute_results_first_experiments(
        authority=authority,
        response_bank_resolver=resolver,
        exp5_transport=_NoopExp5Transport(),
    )

    assert executor_calls == [
        ("results_first_exp1_exp4_trace", trace_manifest_exists),
        ("results_first_exp5_online", exp5_manifest_exists),
    ]
    assert baseline_roots == ([exp5_root] if exp5_manifest_exists else [])


def test_results_first_excluded_exp4_runs_exp1_to_exp3_then_exp5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """partial scope 不能调度 Exp4 或把它作为 Exp5 的终态前置。"""

    trace_experiment_ids = (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
    )
    authority = _representative_pipeline_authority(
        tmp_path,
        trace_experiment_ids=trace_experiment_ids,
    )
    resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    _install_fake_exp5_ledger_factory(
        monkeypatch,
        authority=authority,
        current_calls=16,
        current_spend=1.0,
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    executed: list[tuple[str, tuple[str, ...]]] = []

    def fake_execute(**kwargs):
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in authority.coverage.conditions
            if condition.condition_id in selected
        )
        executed.append((str(kwargs["suite_id"]), selected))
        if kwargs["real_transport"]:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=16,
                omit_endpoint=False,
            )
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(
                len(authority.coverage.root_case_filter[condition_id])
                for condition_id in selected
            ),
            provider_attempt_count=16 if kwargs["real_transport"] else 0,
            total_cost_estimate=1.0 if kwargs["real_transport"] else 0.0,
            total_cost_estimate_status=(
                "single_currency_estimate"
                if kwargs["real_transport"]
                else "not_applicable"
            ),
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": 1,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)

    pipeline.execute_results_first_experiments(
        authority=authority,
        response_bank_resolver=resolver,
        exp5_transport=_NoopExp5Transport(),
    )

    assert [suite_id for suite_id, _selected in executed] == [
        "results_first_exp1_exp3_trace",
        "results_first_exp5_online",
    ]
    trace_selected = executed[0][1]
    assert len(trace_selected) == 3
    assert all("exp4" not in condition_id for condition_id in trace_selected)


@pytest.mark.parametrize("incomplete_suite", ("trace", "exp5"))
def test_results_first_resume_rejects_existing_suite_without_manifest_before_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    incomplete_suite: str,
) -> None:
    authority = _clone_unchecked_results_first_authority(
        _representative_pipeline_authority(tmp_path),
        resume=True,
    )
    incomplete_root = authority.output_root / (
        "exp1-exp4-trace" if incomplete_suite == "trace" else "exp5-online"
    )
    incomplete_root.mkdir(parents=True)
    resolver = SimpleNamespace(
        root_path=tmp_path / "response-bank",
        index=SimpleNamespace(
            manifest=SimpleNamespace(manifest_digest="sha256:" + "1" * 64)
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: SimpleNamespace(snapshot_digest="sha256:" + "2" * 64),
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    executor_calls: list[str] = []

    def forbidden_executor(**kwargs):
        executor_calls.append(str(kwargs["suite_id"]))
        raise AssertionError("incomplete suite reached formal executor")

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", forbidden_executor)

    acquisition_usage = pipeline.RepresentativeCurrentProviderUsage(
        provider_calls=3,
        spend=0.75,
    )
    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_results_first_experiments(
            authority=authority,
            response_bank_resolver=resolver,
            exp5_transport=_NoopExp5Transport(),
            acquisition_usage=acquisition_usage,
        )

    summary = caught.value.to_summary()
    assert executor_calls == []
    assert summary["failure_stage"] == "suite_resume"
    assert summary["provider_calls"] == 3
    assert summary["total_current_provider_calls"] == 3
    assert summary["acquisition_current_provider_calls"] == 3
    assert summary["acquisition_current_spend"] == 0.75
    assert summary["trace_current_provider_calls"] == 0
    assert summary["trace_current_spend"] == 0.0
    assert summary["exp5_current_provider_calls"] == 0
    assert summary["exp5_current_spend"] == 0.0


def test_representative_resume_baseline_failure_stops_before_exp5_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _clone_unchecked_results_first_authority(
        _representative_pipeline_authority(tmp_path),
        resume=True,
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    exp5_suite_root = authority.output_root / "exp5-online"
    exp5_suite_root.mkdir(parents=True, exist_ok=True)
    (exp5_suite_root / "suite_manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "load_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: object(),
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    invocations = 0

    def fake_execute(**kwargs):
        nonlocal invocations
        invocations += 1
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=0,
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_FORMAL_RESUME_BASELINE_LOADER",
        lambda _root: (_ for _ in ()).throw(OSError("baseline unreadable")),
    )

    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport=_NoopExp5Transport(),
        )

    summary = caught.value.to_summary()
    assert invocations == 1
    assert summary["failure_stage"] == "exp5_resume_baseline"
    assert summary["provider_calls"] == 0
    assert summary["exp5_current_provider_calls"] == 0
    assert summary["exp5_current_spend"] == 0.0


def test_results_first_trace_closure_preflight_rejects_before_exp5_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trace closure source 漂移时不得构造任何 Exp5 live 资源。"""

    authority = _representative_pipeline_authority(
        tmp_path,
        selection_kind="representative_exp1_exp3_exp5",
        trace_experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
        ),
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    executor_calls: list[str] = []

    def fake_execute(**kwargs):
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        executor_calls.append(str(kwargs["suite_id"]))
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=0,
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    audited_roots: list[Path] = []

    def reject_trace_closure(*, suite_root: Path):
        audited_roots.append(Path(suite_root))
        raise ValueError("injected trace closure source drift")

    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        reject_trace_closure,
    )
    ledger_factory_calls: list[object] = []
    transport_factory_calls: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda **kwargs: ledger_factory_calls.append(kwargs)
        or pytest.fail("trace preflight constructed an Exp5 ledger factory"),
    )

    def forbidden_transport_factory() -> object:
        transport_factory_calls.append(object())
        pytest.fail("trace preflight constructed an Exp5 transport")

    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_results_first_experiments(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport_factory=forbidden_transport_factory,
        )

    summary = caught.value.to_summary()
    assert executor_calls == ["results_first_exp1_exp3_trace"]
    assert audited_roots == [authority.output_root / "exp1-exp3-trace"]
    assert ledger_factory_calls == []
    assert transport_factory_calls == []
    assert summary["failure_stage"] == "trace_closure_source_preflight"
    assert summary["trace_current_provider_calls"] == 0
    assert summary["exp5_current_provider_calls"] == 0
    assert summary["total_current_provider_calls"] == 0


def test_results_first_exp1_outcome_finalizer_rejects_before_exp5_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exp1 outcome authority 未闭合时，不能构造任何 Exp5 live 资源。"""

    from tokenshare.experiments import paper_exp1_trace_reuse

    authority = _representative_pipeline_authority(
        tmp_path,
        selection_kind="representative_exp1_exp3_exp5",
        trace_experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
        ),
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    executor_calls: list[str] = []

    def fake_execute(**kwargs):
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        executor_calls.append(str(kwargs["suite_id"]))
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=0,
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    monkeypatch.setattr(
        paper_exp1_trace_reuse,
        "finalize_exp1_protocol_outcomes_from_formal_trace",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("injected Exp1 outcome authority drift")
        ),
    )
    ledger_factory_calls: list[object] = []
    transport_factory_calls: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda **kwargs: ledger_factory_calls.append(kwargs)
        or pytest.fail("Exp1 finalizer failure constructed an Exp5 ledger factory"),
    )

    def forbidden_transport_factory() -> object:
        transport_factory_calls.append(object())
        pytest.fail("Exp1 finalizer failure constructed an Exp5 transport")

    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_results_first_experiments(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport_factory=forbidden_transport_factory,
        )

    summary = caught.value.to_summary()
    assert executor_calls == ["results_first_exp1_exp3_trace"]
    assert ledger_factory_calls == []
    assert transport_factory_calls == []
    assert summary["failure_stage"] == "exp1_protocol_outcome_authority"
    assert summary["trace_current_provider_calls"] == 0
    assert summary["exp5_current_provider_calls"] == 0
    assert summary["total_current_provider_calls"] == 0


def test_representative_formal_bundle_uses_typed_create_or_fresh_plan_load_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_response_bank import _representative_plan

    plan = _representative_plan()
    bundle = SimpleNamespace(
        source_snapshot_digest=plan.source_snapshot_digest,
        source_prepared_inventory_digest=plan.source_prepared_inventory_digest,
        coverage_digest=plan.coverage_digest,
        representative_plan_digest=plan.plan_digest,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "_CREATE_REPRESENTATIVE_BUNDLE",
        lambda root, *, plan: calls.append(f"create:{Path(root).name}") or bundle,
    )
    monkeypatch.setattr(
        pipeline,
        "_LOAD_REPRESENTATIVE_BUNDLE",
        lambda root, *, plan: calls.append(f"load:{Path(root).name}") or bundle,
    )
    monkeypatch.setattr(
        pipeline,
        "_ACQUISITION_BUNDLE_LOADER",
        lambda *_args, **_kwargs: pytest.fail("generic loader must reject formal bundles"),
    )

    assert pipeline.prepare_representative_formal_bundle(
        bundle_root=tmp_path / "new-bundle", plan=plan, resume=False
    ) is bundle
    assert pipeline.prepare_representative_formal_bundle(
        bundle_root=tmp_path / "resume-bundle", plan=plan, resume=True
    ) is bundle
    assert calls == ["create:new-bundle", "load:resume-bundle"]


@pytest.mark.parametrize(
    "selection_kind",
    ("representative_exp1_exp3_exp5", "full_exp1_exp3_exp5"),
)
def test_representative_service_routes_full_authority_to_trace_and_all_exp5_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection_kind: str,
) -> None:
    authority = _representative_pipeline_authority(
        tmp_path,
        selection_kind=selection_kind,
    )
    resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    monkeypatch.setattr(
        pipeline,
        "persist_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: None,
    )
    conditions = authority.coverage.conditions
    root_filter = authority.coverage.root_case_filter
    exp5_counts = {
        condition.condition_id: 1
        for condition in conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    }
    exp5_slots = tuple(
        (condition_id, f"case-{condition_id}", f"unit-{condition_id}")
        for condition_id in exp5_counts
    )
    captured: list[dict[str, object]] = []
    exp5_factory = SimpleNamespace(
        serialize_roots=False,
        audit_usage=lambda: SimpleNamespace(
            current_provider_calls=16,
            total_provider_calls=16,
            current_spend=Decimal("1.25"),
            current_spend_missing_reason=None,
            total_spend=Decimal("1.25"),
            total_spend_missing_reason=None,
            ambiguous_count=0,
            usage_missing_count=0,
            expected_provider_calls_by_condition=exp5_counts,
            current_provider_calls_by_condition=exp5_counts,
            total_provider_calls_by_condition=exp5_counts,
            current_terminal_provider_calls_by_condition=exp5_counts,
            total_terminal_provider_calls_by_condition=exp5_counts,
            prepared_canonical_slots=exp5_slots,
            current_terminal_slots=exp5_slots,
            total_terminal_slots=exp5_slots,
        ),
    )
    factory_inputs: list[tuple[object, Path, object]] = []

    def build_exp5_factory(*, authority, ledger_path, coverage):
        ledger = Path(ledger_path)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.touch()
        factory_inputs.append((authority, ledger, coverage))
        return exp5_factory

    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        build_exp5_factory,
    )

    def fake_execute(**kwargs):
        captured.append(kwargs)
        if len(captured) == 2:
            exp5_evidence_root = Path(kwargs["output_root"])
            assert not exp5_evidence_root.exists() or not any(
                exp5_evidence_root.iterdir()
            )
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        active = tuple(
            dict.fromkeys(
                condition.experiment_id for condition in active_conditions
            )
        )
        if len(captured) == 2:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=16,
                omit_endpoint=False,
            )
        return replace(
            _paper_suite_result(
                Path(kwargs["output_root"]),
                status=(
                    "completed_with_failures" if len(captured) == 2 else "completed"
                ),
            ),
            experiment_ids=active,
            condition_count=len(selected),
            task_count=sum(len(root_filter[item]) for item in selected),
            provider_attempt_count=0 if len(captured) == 1 else 16,
            total_cost_estimate=0.0 if len(captured) == 1 else 1.25,
            total_cost_estimate_status=(
                "not_applicable"
                if len(captured) == 1
                else "single_currency_estimate"
            ),
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": 1,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(
        pipeline,
        "_TRACE_CONTEXT_BUILDER",
        lambda **_kwargs: object(),
    )
    result = pipeline.execute_representative_full_plan_smoke(
        authority=authority,
        response_bank_resolver=resolver,
        exp5_transport=_NoopExp5Transport(),
    )

    assert result.status == "completed_with_failures"
    assert result.expected_condition_count == authority.coverage.condition_count
    assert result.expected_root_count == authority.coverage.root_run_count
    assert result.acquisition_current_provider_calls == 0
    assert result.trace_current_provider_calls == 0
    assert result.exp5_current_provider_calls == 16
    assert result.total_current_provider_calls == 16
    assert result.acquisition_current_spend == 0.0
    assert result.trace_current_spend == 0.0
    assert result.exp5_current_spend == 1.25
    assert result.total_current_spend == 1.25
    assert len(captured) == 2
    trace, online = captured
    assert trace["dispatch_plans"] is authority.full_dispatch_plans
    assert trace["budget"] is authority.full_budget
    assert trace["real_transport"] is False
    assert trace["trace_context"] is not None
    assert online["dispatch_plans"] is authority.full_dispatch_plans
    assert online["budget"] is authority.full_budget
    assert online["real_transport"] is True
    assert online["trace_context"] is None
    assert online["online_root_callback_factory"] is exp5_factory
    assert len(factory_inputs) == 1
    assert factory_inputs[0][0] is authority
    assert factory_inputs[0][1] == (
        authority.output_root
        / "exp5-online-ledger"
        / "exp5_online_budget.v1.sqlite3"
    )
    assert factory_inputs[0][2].selection_kind == "filtered"
    exp5_conditions = tuple(
        condition
        for condition in conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    assert set(online["selected_condition_ids"]) == {
        condition.condition_id for condition in exp5_conditions
    }
    assert len({item.model_endpoint_identity_digest for item in exp5_conditions}) == 4


def test_results_first_exp5_rejects_capability_pilot_before_factory_or_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R38 的 8-root capability pilot 不能进入正式 Exp5 results-first 路径。"""

    authority = _representative_pipeline_authority(
        tmp_path,
        exp5_condition_count=8,
        selection_kind="exp5_capability_smoke",
        trace_experiment_ids=(),
    )
    factory_calls: list[object] = []
    transport_calls: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda **kwargs: factory_calls.append(kwargs),
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        lambda: transport_calls.append(object()),
    )

    with pytest.raises(ValueError, match="capability.*pilot.*formal Exp5"):
        pipeline._execute_results_first_service_input(
            request=SimpleNamespace(output_root=authority.output_root),
            value=pipeline.ResultsFirstServiceInput(
                authority=authority,
                selection="representative_exp1_exp3_exp5",
                plan_only=False,
                allow_provider_calls=False,
                external_bank_root=None,
                resume=False,
            ),
        )

    assert factory_calls == []
    assert transport_calls == []


@pytest.mark.parametrize("selection_kind", ("full", "filtered"))
def test_results_first_exp5_rejects_mixed_direct_coverage_before_factory_or_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection_kind: str,
) -> None:
    """直接 Exp5 coverage 不得混入 trace conditions。"""

    authority = _representative_pipeline_authority(
        tmp_path,
        selection_kind=selection_kind,
    )
    with pytest.raises(ValueError, match="direct.*Exp5.*non-Exp5"):
        pipeline._results_first_formal_exp5_coverage(authority)


def test_results_first_exp5_parent_projection_is_single_source_for_factory_dispatch_and_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parent scope 的 Exp5 子 coverage 必须是所有在线边界的同一对象。"""

    authority = _representative_pipeline_authority(
        tmp_path,
        selection_kind="representative_exp1_exp3_exp5",
        trace_experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
        ),
    )
    parent_coverage = authority.coverage
    exp5_conditions = tuple(
        condition
        for condition in parent_coverage.conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    exp5_root_filter = {
        condition.condition_id: parent_coverage.root_case_filter[
            condition.condition_id
        ]
        for condition in exp5_conditions
    }
    exp5_coverage = SimpleNamespace(
        conditions=exp5_conditions,
        roots=tuple(
            root
            for root in parent_coverage.roots
            if root.condition in exp5_conditions
        ),
        root_case_filter=exp5_root_filter,
        condition_count=len(exp5_conditions),
        root_run_count=sum(len(case_ids) for case_ids in exp5_root_filter.values()),
        selection_kind="filtered",
        coverage_digest="sha256:" + "e" * 64,
        source_snapshot_digest=parent_coverage.source_snapshot_digest,
        provider_calls_made=0,
    )
    projection_calls: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_selection_exact_exp5_execution_projection",
        lambda value: (
            projection_calls.append(value)
            or SimpleNamespace(coverage=exp5_coverage)
        ),
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )
    monkeypatch.setattr(
        pipeline,
        "persist_results_first_post_acquisition_trace_seal",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())

    audit = SimpleNamespace(
        current_provider_calls=0,
        current_spend=Decimal("0"),
        current_spend_missing_reason=None,
    )
    factory_coverages: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        lambda *, authority, ledger_path, coverage: (
            factory_coverages.append(coverage)
            or SimpleNamespace(serialize_roots=False, audit_usage=lambda: audit)
        ),
    )
    dispatch_coverages: list[object] = []

    def fake_subset(*, coverage, conditions, **_kwargs):
        dispatch_coverages.append(coverage)
        return SimpleNamespace(
            status="completed",
            condition_count=len(conditions),
            task_count=sum(
                len(coverage.root_case_filter[condition.condition_id])
                for condition in conditions
            ),
            experiment_ids=tuple(
                dict.fromkeys(condition.experiment_id for condition in conditions)
            ),
            provider_attempt_count=0,
            total_cost_estimate=0.0,
            total_cost_estimate_status="not_applicable",
            condition_results=(),
        )

    monkeypatch.setattr(pipeline, "_run_results_first_formal_subset", fake_subset)
    terminal_coverages: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_validate_representative_terminal",
        lambda *, coverage, **_kwargs: terminal_coverages.append(coverage),
    )
    exp5_terminal_coverages: list[object] = []
    monkeypatch.setattr(
        pipeline,
        "_validate_representative_exp5_terminal",
        lambda *, coverage, **_kwargs: exp5_terminal_coverages.append(coverage),
    )

    pipeline.execute_results_first_experiments(
        authority=authority,
        response_bank_resolver=response_bank_resolver,
        exp5_transport=object(),
    )

    assert projection_calls == [authority]
    assert factory_coverages == [exp5_coverage]
    assert dispatch_coverages == [parent_coverage, exp5_coverage]
    assert terminal_coverages == [parent_coverage, exp5_coverage]
    assert exp5_terminal_coverages == [exp5_coverage]


def test_results_first_exp5_uses_full_slot_authority_and_rejects_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _representative_pipeline_authority(
        tmp_path,
        exp5_condition_count=16,
        exp5_slots_per_condition=7,
        selection_kind="representative_exp1_exp3_exp5",
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    exp5_conditions = tuple(
        condition
        for condition in conditions
        if condition.experiment_id == "exp5_real_ai_model_endpoint_comparison"
    )
    assert authority.coverage.selection_kind == "representative_exp1_exp3_exp5"
    assert len(exp5_conditions) == 16
    assert {
        (condition.model_entry_id, condition.provider_model_id)
        for condition in exp5_conditions
    } == set(_FORMAL_EXP5_ENDPOINTS)
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )

    class RecordingTransport:
        def __init__(self) -> None:
            self.models: list[str] = []

        def post_chat_completion(self, *, body_bytes: bytes, **_kwargs):
            self.models.append(str(json.loads(body_bytes)["model"]))
            return object()

    def run(*, corruption: str | None):
        prepared_by_condition = {
            condition.condition_id: len(roots[condition.condition_id])
            for condition in exp5_conditions
        }
        prepared_slots = tuple(
            (
                condition.condition_id,
                case_id,
                f"planned-{case_id}",
            )
            for condition in exp5_conditions
            for case_id in roots[condition.condition_id]
        )
        actual_slots = tuple(
            slot
            for slot in prepared_slots
            if slot[1] == roots[slot[0]][0]
        )
        condition_by_id = {
            condition.condition_id: condition for condition in exp5_conditions
        }
        if corruption == "duplicate":
            actual_slots = (*actual_slots, actual_slots[0])
        elif corruption == "unknown":
            actual_slots = (
                (actual_slots[0][0], "unknown-case", "unknown-unit"),
                *actual_slots[1:],
            )
        elif corruption == "missing_endpoint":
            missing_entry_id = _FORMAL_EXP5_ENDPOINTS[-1][0]
            actual_slots = tuple(
                slot
                for slot in actual_slots
                if condition_by_id[slot[0]].model_entry_id != missing_entry_id
            )
        elif corruption == "missing_condition":
            missing_condition_id = exp5_conditions[0].condition_id
            actual_slots = tuple(
                slot for slot in actual_slots if slot[0] != missing_condition_id
            )
        actual_by_condition = {
            condition.condition_id: sum(
                slot[0] == condition.condition_id for slot in actual_slots
            )
            for condition in exp5_conditions
        }
        terminal_by_condition = dict(actual_by_condition)
        if corruption == "overbound":
            condition_id = exp5_conditions[0].condition_id
            terminal_by_condition[condition_id] = (
                prepared_by_condition[condition_id] + 1
            )
        transport_calls = len(actual_slots)
        terminal_calls = sum(terminal_by_condition.values())
        audit = SimpleNamespace(
            current_provider_calls=transport_calls,
            total_provider_calls=transport_calls,
            current_spend=Decimal("1.25"),
            current_spend_missing_reason=None,
            total_spend=Decimal("1.25"),
            total_spend_missing_reason=None,
            ambiguous_count=0,
            usage_missing_count=0,
            cost_upper_bound_at_risk=Decimal("0"),
            expected_provider_calls_by_condition=prepared_by_condition,
            current_provider_calls_by_condition=actual_by_condition,
            total_provider_calls_by_condition=actual_by_condition,
            current_terminal_provider_calls_by_condition=actual_by_condition,
            total_terminal_provider_calls_by_condition=actual_by_condition,
            prepared_canonical_slots=prepared_slots,
            current_terminal_slots=actual_slots,
            total_terminal_slots=actual_slots,
        )
        factory = SimpleNamespace(
            serialize_roots=False,
            audit_usage=lambda: audit,
        )
        factory_bindings: list[tuple[object, object]] = []
        monkeypatch.setattr(
            pipeline,
            "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
            lambda *, authority, ledger_path, coverage: (
                factory_bindings.append(
                    (authority.full_prepared_inventory, coverage)
                )
                or factory
            ),
        )
        invocations = 0

        def fake_execute(**kwargs):
            nonlocal invocations
            invocations += 1
            selected = tuple(kwargs["selected_condition_ids"])
            active_conditions = tuple(
                condition
                for condition in conditions
                if condition.condition_id in selected
            )
            active_experiments = tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            )
            if invocations == 2:
                dispatched_conditions = tuple(
                    condition
                    for condition in active_conditions
                    if actual_by_condition.get(condition.condition_id, 0) > 0
                )
                _invoke_fake_exp5_transport(
                    transport=kwargs["transport"],
                    conditions=dispatched_conditions,
                    call_count=transport_calls,
                    omit_endpoint=False,
                    observation_override=(
                        ("https://wrong.example.test/v1/chat", "wrong-model")
                        if corruption == "model_endpoint_mismatch"
                        else None
                    ),
                )
            condition_results = tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": terminal_by_condition[
                        condition.condition_id
                    ],
                }
                for condition in active_conditions
            ) if invocations == 2 else ()
            return replace(
                _paper_suite_result(
                    Path(kwargs["output_root"]),
                    status=(
                        "completed_with_failures"
                        if invocations == 2
                        else "completed"
                    ),
                ),
                experiment_ids=active_experiments,
                condition_count=len(selected),
                task_count=sum(len(roots[item]) for item in selected),
                provider_attempt_count=(
                    0 if invocations == 1 else terminal_calls
                ),
                total_cost_estimate=(0.0 if invocations == 1 else 1.25),
                total_cost_estimate_status=(
                    "not_applicable"
                    if invocations == 1
                    else "single_currency_estimate"
                ),
                condition_results=condition_results,
            )

        monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
        monkeypatch.setattr(
            pipeline,
            "_TRACE_CONTEXT_BUILDER",
            lambda **_kwargs: object(),
        )
        transport = RecordingTransport()
        result = pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport=transport,
        )
        return result, transport, audit, factory_bindings

    result, transport, audit, factory_bindings = run(corruption=None)
    summaries = tuple(result.exp5_terminal.condition_results)
    assert len(summaries) == 16
    assert len({row["condition_id"] for row in summaries}) == 16
    assert len(transport.models) == 16
    assert {
        model: transport.models.count(model) for model in set(transport.models)
    } == {model: 4 for _entry_id, model in _FORMAL_EXP5_ENDPOINTS}
    assert len(audit.prepared_canonical_slots) == 112
    assert len(audit.total_terminal_slots) == 16
    assert audit.current_provider_calls == audit.total_provider_calls == 16
    assert audit.ambiguous_count == audit.usage_missing_count == 0
    assert factory_bindings[0][0] is authority.full_prepared_inventory
    assert factory_bindings[0][1].selection_kind == "filtered"
    assert tuple(factory_bindings[0][1].conditions) == exp5_conditions

    for corruption, message in (
        ("duplicate", "duplicate"),
        ("unknown", "outside prepared"),
        ("overbound", "exceed prepared"),
        ("missing_endpoint", "selected condition|actual terminal endpoint"),
        ("missing_condition", "selected condition"),
        ("model_endpoint_mismatch", "endpoint/model execution"),
    ):
        with pytest.raises(ValueError, match=message):
            run(corruption=corruption)


def test_results_first_wrong_factor_response_stays_in_shared_metric_denominators(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_smoke_report import _with_row_metric_fields

    case = generate_factorization_paper_cases()[0]
    transport = ScriptedFactorizationRangeTransport(
        force_false_negative_child_indices={0}
    )
    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path / "wrong-factor",
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert any(
        item["verification"]["accepted"] is False
        for item in result.range_results
    )
    assert result.task_result.accepted_validity is False
    assert result.final_prime_factors == []
    assert result.task_result.provider_attempt_count == len(transport.calls)
    assert all(
        attempt.provider_attempt_count == 1
        for attempt in result.attempt_results
    )
    root_status = result.task_result.root_status
    smoke_execution_status = (
        "completed"
        if getattr(root_status, "value", root_status) == "completed"
        else "failed"
    )
    metric_row = _with_row_metric_fields(
        {
            "accepted_validity": result.task_result.accepted_validity,
            "accepted_validity_unavailable_reason": None,
            "smoke_execution_status": smoke_execution_status,
        }
    )
    assert metric_row["correctness_numerator"] == 0
    assert metric_row["correctness_denominator"] == 1
    assert metric_row["correctness_missing_count"] == 0
    assert metric_row["completion_numerator"] == 0
    assert metric_row["completion_denominator"] == 1


def test_results_first_policy_and_entrypoint_are_identical_for_representative_and_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    representative = _representative_pipeline_authority(tmp_path / "representative")
    representative_conditions = representative.coverage.conditions
    full_root_filter = {
        condition.condition_id: (f"full-{index}-a", f"full-{index}-b")
        for index, condition in enumerate(representative_conditions)
    }
    full_coverage = SimpleNamespace(
        conditions=representative_conditions,
        roots=tuple(
            SimpleNamespace(condition=condition, case_id=case_id)
            for condition in representative_conditions
            for case_id in full_root_filter[condition.condition_id]
        ),
        root_case_filter=full_root_filter,
        condition_count=len(representative_conditions),
        root_run_count=sum(len(case_ids) for case_ids in full_root_filter.values()),
        selection_kind="full_exp1_exp3_exp5",
        coverage_digest="sha256:" + "e" * 64,
        source_snapshot_digest=representative.coverage.source_snapshot_digest,
        provider_calls_made=0,
    )
    full = _clone_unchecked_results_first_authority(
        representative,
        coverage=full_coverage,
        bundle=SimpleNamespace(
            source_snapshot_digest=full_coverage.source_snapshot_digest,
            coverage_digest=full_coverage.coverage_digest,
            semantic_inventory_plan=object(),
        ),
        output_root=tmp_path / "full",
        execution_budget_projection=SimpleNamespace(
            hard_limits={"max_total_provider_attempts": 198}
        ),
    )
    captured: list[dict[str, object]] = []
    trace_builder_calls: list[dict[str, object]] = []
    built_factories: list[object] = []

    class FakeExp5LedgerFactory:
        serialize_roots = False

        def __init__(self, *, authority, ledger_path, coverage) -> None:
            self.coverage = coverage
            self.ledger_path = ledger_path

        def audit_usage(self):
            calls_by_condition = {
                condition.condition_id: 1
                for condition in self.coverage.conditions
                if condition.experiment_id
                == "exp5_real_ai_model_endpoint_comparison"
            }
            calls = sum(calls_by_condition.values())
            slots = tuple(
                (condition_id, f"case-{condition_id}", f"unit-{condition_id}")
                for condition_id in calls_by_condition
            )
            return SimpleNamespace(
                current_provider_calls=calls,
                total_provider_calls=calls,
                current_spend=Decimal("1"),
                current_spend_missing_reason=None,
                total_spend=Decimal("1"),
                total_spend_missing_reason=None,
                ambiguous_count=0,
                usage_missing_count=0,
                expected_provider_calls_by_condition=calls_by_condition,
                current_provider_calls_by_condition=calls_by_condition,
                total_provider_calls_by_condition=calls_by_condition,
                current_terminal_provider_calls_by_condition=calls_by_condition,
                total_terminal_provider_calls_by_condition=calls_by_condition,
                prepared_canonical_slots=slots,
                current_terminal_slots=slots,
                total_terminal_slots=slots,
            )

    def fake_ledger_builder(**kwargs):
        factory = FakeExp5LedgerFactory(**kwargs)
        built_factories.append(factory)
        return factory

    def fake_execute(**kwargs):
        captured.append(kwargs)
        authority = representative if len(captured) <= 2 else full
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in authority.coverage.conditions
            if condition.condition_id in selected
        )
        if kwargs["real_transport"]:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=len(active_conditions),
                omit_endpoint=False,
            )
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(
                len(authority.coverage.root_case_filter[condition_id])
                for condition_id in selected
            ),
            provider_attempt_count=(len(active_conditions) if kwargs["real_transport"] else 0),
            total_cost_estimate=(1.0 if kwargs["real_transport"] else 0.0),
            total_cost_estimate_status=(
                "single_currency_estimate"
                if kwargs["real_transport"]
                else "not_applicable"
            ),
            condition_results=tuple(
                {
                    "condition_id": condition.condition_id,
                    "provider_attempt_count": 1,
                }
                for condition in active_conditions
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    def fake_trace_router(**kwargs):
        trace_builder_calls.append(kwargs)
        return object()

    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", fake_trace_router)
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_EXP5_LEDGER_FACTORY_BUILDER",
        fake_ledger_builder,
    )
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(representative, full),
    )

    for authority in (representative, full):
        assert type(authority) is pipeline.ResultsFirstExecutionAuthority
        result = pipeline.execute_results_first_experiments(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport=_NoopExp5Transport(),
        )
        assert result.expected_root_count == authority.coverage.root_run_count

    assert representative.execution_policy is full.execution_policy
    policy = representative.execution_policy
    assert type(policy) is pipeline.ResultsFirstExecutionPolicy
    assert policy.trace_evidence_class == "real_model_trace_protocol_run"
    assert policy.online_evidence_class == "online_real_provider"
    assert policy.authorization_kind == "user_authorized_results_first"
    assert policy.adapter_chain == (
        "paper_formal_runner.execute_paper_formal_suite",
        "ProtocolEngine",
        "registered_domain_adapter",
    )
    assert len(captured) == 4
    assert trace_builder_calls == [
        {
            "authority": representative,
            "resolver": trace_builder_calls[0]["resolver"],
            "output_root": representative.output_root,
        },
        {
            "authority": full,
            "resolver": trace_builder_calls[1]["resolver"],
            "output_root": full.output_root,
        },
    ]
    assert len(built_factories) == 2
    assert type(built_factories[0]) is type(built_factories[1])
    assert built_factories[0].serialize_roots is built_factories[1].serialize_roots is False
    for representative_call, full_call in zip(captured[:2], captured[2:], strict=True):
        for call in (representative_call, full_call):
            assert call["budget_approval"]["approval_mode"] == "results_first_execution"
            assert call["enforce_publication_closure"] is False
            assert call["enable_metric_closure"] is True
            assert call["bypass_nonmetric_facility_gates"] is True
        assert representative_call["real_transport"] == full_call["real_transport"]
        assert bool(representative_call["trace_context"]) == bool(full_call["trace_context"])
        assert representative_call["dispatch_plans"] is full_call["dispatch_plans"]
        assert representative_call["catalog_manifest"] is full_call["catalog_manifest"]
        assert representative_call["budget"] is full_call["budget"]
        assert representative_call["ai_api_configs"] is full_call["ai_api_configs"]
        assert representative_call["selected_condition_ids"] == full_call["selected_condition_ids"]
        if representative_call["real_transport"]:
            assert representative_call["online_root_callback_factory"] is built_factories[0]
            assert full_call["online_root_callback_factory"] is built_factories[1]
        assert representative_call["root_case_filter"] != full_call["root_case_filter"]
        assert representative_call["hard_limits"] != full_call["hard_limits"]
        assert representative_call["output_root"] != full_call["output_root"]


def test_representative_invalid_exp5_terminal_preserves_current_provider_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _representative_pipeline_authority(tmp_path)
    _install_fake_exp5_ledger_factory(
        monkeypatch,
        authority=authority,
        current_calls=4,
        current_spend=1.25,
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    calls = 0

    def fake_execute(**kwargs):
        nonlocal calls
        calls += 1
        selected = tuple(kwargs["selected_condition_ids"])
        active = tuple(
            dict.fromkeys(
                condition.experiment_id
                for condition in conditions
                if condition.condition_id in selected
            )
        )
        if calls == 2:
            active_conditions = tuple(
                condition
                for condition in conditions
                if condition.condition_id in selected
            )
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=4,
                omit_endpoint=False,
            )
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=active,
            condition_count=len(selected),
            task_count=(
                sum(len(roots[item]) for item in selected) if calls == 1 else 0
            ),
            provider_attempt_count=0 if calls == 1 else 4,
            total_cost_estimate=0.0 if calls == 1 else 1.25,
            total_cost_estimate_status=(
                "not_applicable" if calls == 1 else "single_currency_estimate"
            ),
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())

    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=object(),
            exp5_transport=_NoopExp5Transport(),
        )

    summary = caught.value.to_summary()
    assert summary["exp5_current_provider_calls"] == 4
    assert summary["exp5_current_spend"] == 1.25
    assert summary["provider_calls"] == 4


def test_representative_terminal_usage_never_converts_missing_spend_to_zero(
    tmp_path: Path,
) -> None:
    terminal = replace(
        _paper_suite_result(tmp_path, status="completed"),
        provider_attempt_count=4,
        total_cost_estimate=0.0,
        total_cost_estimate_status="usage_missing",
    )

    usage = pipeline._current_provider_usage_from_terminal(
        terminal=terminal,
        force_zero_spend_when_no_calls=True,
    )

    assert usage.provider_calls == 4
    assert usage.spend is None
    assert usage.spend_missing_reason == "usage_missing"


@pytest.mark.parametrize("status", ("blocked", "incomplete", "failed"))
def test_representative_service_rejects_partial_or_fake_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    authority = _representative_pipeline_authority(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_SUITE_EXECUTOR",
        lambda **kwargs: replace(
            _paper_suite_result(Path(kwargs["output_root"]), status=status),
            experiment_ids=tuple(
                plan.experiment_id for plan in authority.full_dispatch_plans
            ),
        ),
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())

    with pytest.raises(ValueError, match="terminal|denominator"):
        pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=object(),
            exp5_transport=object(),
        )


def test_representative_service_rejects_completed_terminal_with_partial_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _representative_pipeline_authority(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_SUITE_EXECUTOR",
        lambda **kwargs: replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=("exp1_real_ai_feasibility",),
            condition_count=1,
            task_count=0,
            provider_attempt_count=0,
        ),
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    response_bank_resolver = _install_minimal_typed_results_first_trace_fixture(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        authorities=(authority,),
    )

    with pytest.raises(ValueError, match="denominator"):
        pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=response_bank_resolver,
            exp5_transport=object(),
        )


def test_representative_cli_builds_full_atomic_authority_before_any_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    calls: list[str] = []

    def blocked_builder(**_kwargs):
        calls.append("full-preflight")
        raise ValueError("representative coverage misses formal condition")

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        blocked_builder,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SMOKE_EXECUTOR",
        lambda **_kwargs: pytest.fail("provider/execution must follow full preflight"),
    )

    exit_code = pipeline.main(
        [
            "representative-full-plan-smoke",
            "--output-root",
            str(tmp_path / "output"),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "bundle"),
            "--new-run",
            "--plan-only",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert calls == ["full-preflight"]
    assert body["status"] == "blocked"
    assert body["provider_calls"] is None
    assert body["provider_calls_missing_reason"] == (
        "pipeline_boundary_provider_accounting_unavailable"
    )
    assert "coverage" in body["message"]


def _representative_authority_with_typed_bundle(tmp_path: Path):
    from tests.experiments.test_paper_response_bank import _representative_plan
    from tokenshare.experiments.paper_response_bank import (
        build_semantic_inventory,
        create_representative_acquisition_plan_bundle,
    )

    plan = _representative_plan()
    exp1_candidate = replace(
        plan.candidates[0],
        experiment_id="exp1_real_ai_feasibility",
    )
    exp1_semantic_plan = build_semantic_inventory((exp1_candidate,))
    exp1_request = replace(
        plan.acquisition_requests[0],
        inventory_row=exp1_semantic_plan.rows[0],
        prepared_request=exp1_candidate.prepared_request,
    )
    plan = replace(
        plan,
        candidates=(exp1_candidate,),
        semantic_inventory_plan=exp1_semantic_plan,
        acquisition_requests=(exp1_request,),
    )
    bundle = create_representative_acquisition_plan_bundle(
        tmp_path / "typed-bundle",
        plan=plan,
    )
    base = _representative_pipeline_authority(tmp_path / "execution")
    coverage = SimpleNamespace(
        **{
            **vars(base.coverage),
            "source_snapshot_digest": plan.source_snapshot_digest,
            "coverage_digest": plan.coverage_digest,
        }
    )
    return _clone_unchecked_results_first_authority(
        base,
        coverage=coverage,
        bundle=bundle,
    )


def test_representative_typed_acquisition_finalizes_child_bank_and_resumes_without_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_response_bank_acquisition import (
        ScriptedExactTransport,
        _success_response,
    )

    authority = _representative_authority_with_typed_bundle(tmp_path)
    first_transport = ScriptedExactTransport([_success_response("representative")])
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        lambda: first_transport,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SECRET_RESOLVER",
        lambda _name: "fake-secret",
    )

    acquired = pipeline._acquire_representative_response_bank(
        authority=authority,
        resume=False,
    )

    assert acquired.max_in_flight == 10
    assert acquired.usage.provider_calls == 1
    assert acquired.usage.spend == pytest.approx(0.0000125)
    assert len(first_transport.calls) == 1
    assert acquired.resolver.index.manifest.inventory_digest == (
        authority.bundle.inventory_digest
    )
    attempt_history_path = (
        acquired.resolver.root_path
        / "exp1_attempt_history_authority.v1.json"
    )
    assert attempt_history_path.is_file()
    first_attempt_history = attempt_history_path.read_bytes()
    ledger_path = (
        authority.output_root
        / "acquisition"
        / "acquisition_budget.v1.sqlite3"
    )
    assert ledger_path.is_file()

    resume_transport = ScriptedExactTransport([])
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        lambda: resume_transport,
    )
    resumed = pipeline._acquire_representative_response_bank(
        authority=authority,
        resume=True,
    )
    assert resumed.usage.provider_calls == 0
    assert resumed.usage.spend == 0.0
    assert resume_transport.calls == []
    assert resumed.resolver.index.manifest.manifest_digest == (
        acquired.resolver.index.manifest.manifest_digest
    )
    assert attempt_history_path.read_bytes() == first_attempt_history


def test_representative_resume_reconciles_ambiguous_without_blind_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_response_bank_acquisition import (
        ScriptedExactTransport,
        _success_response,
    )

    authority = _representative_authority_with_typed_bundle(tmp_path)
    first_transport = ScriptedExactTransport([_success_response("ambiguous")])
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        lambda: first_transport,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SECRET_RESOLVER",
        lambda _name: "fake-secret",
    )

    def crash_after_transport(stage: str) -> None:
        if stage == "transport_sent":
            raise RuntimeError("simulated ambiguous dispatch")

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_CRASH_HOOK",
        crash_after_transport,
    )
    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as first:
        pipeline._acquire_representative_response_bank(
            authority=authority,
            resume=False,
        )
    assert first.value.to_summary()["provider_calls"] == 1
    assert first.value.to_summary()["acquisition_current_spend"] is None

    resume_transport = ScriptedExactTransport([])
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        lambda: resume_transport,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_CRASH_HOOK",
        None,
    )
    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as resumed:
        pipeline._acquire_representative_response_bank(
            authority=authority,
            resume=True,
        )
    summary = resumed.value.to_summary()
    assert summary["failure_stage"] == "acquisition_terminal"
    assert summary["provider_calls"] == 0
    assert summary["acquisition_current_spend"] == 0.0
    assert resume_transport.calls == []


@pytest.mark.parametrize("terminal_ok", (True, False))
def test_representative_cli_reports_exp5_calls_on_success_and_terminal_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    terminal_ok: bool,
) -> None:
    authority = _representative_authority_with_typed_bundle(tmp_path)
    snapshot_calls = _install_recording_closure_snapshot_persister(
        monkeypatch,
        authority=authority,
    )
    zero = pipeline.RepresentativeCurrentProviderUsage(
        provider_calls=0,
        spend=0.0,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        lambda **_kwargs: authority,
    )
    def acquire_after_snapshot(**_kwargs):
        assert len(snapshot_calls) == 1
        return pipeline.RepresentativeAcquisitionStageResult(
            resolver=object(),
            usage=zero,
            max_in_flight=10,
        )

    monkeypatch.setattr(
        pipeline,
        "_acquire_representative_response_bank",
        acquire_after_snapshot,
    )

    def exp5_transport_after_snapshot():
        assert len(snapshot_calls) == 1
        return object()

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        exp5_transport_after_snapshot,
    )
    if terminal_ok:
        terminal = SimpleNamespace(
            status="completed",
            expected_condition_count=authority.coverage.condition_count,
            expected_root_count=authority.coverage.root_run_count,
            acquisition_current_provider_calls=0,
            acquisition_current_spend=0.0,
            acquisition_spend_missing_reason=None,
            trace_current_provider_calls=0,
            trace_current_spend=0.0,
            trace_spend_missing_reason=None,
            exp5_current_provider_calls=4,
            exp5_current_spend=1.25,
            exp5_spend_missing_reason=None,
            total_current_provider_calls=4,
            total_current_spend=1.25,
            total_spend_missing_reason=None,
        )
        monkeypatch.setattr(
            pipeline,
            "_REPRESENTATIVE_SMOKE_EXECUTOR",
            lambda **_kwargs: terminal,
        )
    else:
        failure = pipeline.RepresentativeSmokeExecutionError(
            "invalid Exp5 terminal denominator",
            failure_stage="exp5_terminal",
            acquisition_usage=zero,
            trace_usage=zero,
            exp5_usage=pipeline.RepresentativeCurrentProviderUsage(
                provider_calls=4,
                spend=1.25,
            ),
        )

        def fail(**_kwargs):
            raise failure

        monkeypatch.setattr(pipeline, "_REPRESENTATIVE_SMOKE_EXECUTOR", fail)

    exit_code = pipeline.main(
        [
            "representative-full-plan-smoke",
            "--output-root",
            str(tmp_path / "execution"),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "bundle"),
            "--new-run",
            "--allow-provider-calls",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == (0 if terminal_ok else 3)
    assert body["provider_calls"] == 4
    assert body["total_current_provider_calls"] == 4
    assert body["exp5_current_provider_calls"] == 4
    assert body["exp5_current_spend"] == 1.25
    assert len(snapshot_calls) == 1


@pytest.mark.parametrize("failure_point", ("usage", "finalize"))
def test_representative_cli_preserves_calls_after_post_acquisition_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    failure_point: str,
) -> None:
    from tests.experiments.test_paper_response_bank_acquisition import (
        ScriptedExactTransport,
        _success_response,
    )
    from tokenshare.experiments import paper_response_bank

    authority = _representative_authority_with_typed_bundle(tmp_path)
    snapshot_calls = _install_recording_closure_snapshot_persister(
        monkeypatch,
        authority=authority,
    )
    transport = ScriptedExactTransport([_success_response(failure_point)])
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        lambda **_kwargs: authority,
    )
    def acquisition_transport_after_snapshot():
        assert len(snapshot_calls) == 1
        return transport

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        acquisition_transport_after_snapshot,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SECRET_RESOLVER",
        lambda _name: "fake-secret",
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SMOKE_EXECUTOR",
        lambda **_kwargs: pytest.fail("trace/Exp5 must follow child-bank terminal"),
    )
    if failure_point == "usage":
        monkeypatch.setattr(
            pipeline,
            "_acquisition_current_usage",
            lambda **_kwargs: (_ for _ in ()).throw(
                ValueError("simulated post-acquisition usage failure")
            ),
        )
    else:
        monkeypatch.setattr(
            paper_response_bank,
            "finalize_acquisition_child_bank",
            lambda **_kwargs: (_ for _ in ()).throw(
                OSError("simulated child-bank finalize failure")
            ),
        )

    exit_code = pipeline.main(
        [
            "representative-full-plan-smoke",
            "--output-root",
            str(authority.output_root),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "typed-bundle"),
            "--new-run",
            "--allow-provider-calls",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert len(transport.calls) == 1
    assert body["provider_calls"] == 1
    assert body["total_current_provider_calls"] == 1
    assert body["acquisition_current_provider_calls"] == 1
    assert body["acquisition_current_spend"] == pytest.approx(0.0000125)
    assert body["exp5_current_provider_calls"] == 0
    assert len(snapshot_calls) == 1


@pytest.mark.parametrize(
    "reason",
    ("post_acquisition_usage_missing", "child_bank_finalize_failed"),
)
def test_representative_post_dispatch_fallback_keeps_missing_spend_explicit(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    monkeypatch.setattr(
        pipeline,
        "_acquisition_current_usage_from_ledger",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("ledger unreadable")),
    )

    usage = pipeline._recover_acquisition_current_usage(
        batch=object(),
        ledger=object(),
        inventory_digest="sha256:" + "a" * 64,
        current_provider_calls=1,
        missing_spend_reason=reason,
    )

    assert usage.provider_calls == 1
    assert usage.spend is None
    assert usage.spend_missing_reason == reason


def test_representative_cli_preserves_acquisition_calls_when_exp5_factory_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    authority = _representative_authority_with_typed_bundle(tmp_path)
    snapshot_calls = _install_recording_closure_snapshot_persister(
        monkeypatch,
        authority=authority,
    )
    acquisition_usage = pipeline.RepresentativeCurrentProviderUsage(
        provider_calls=3,
        spend=0.75,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        lambda **_kwargs: authority,
    )
    def acquire_after_snapshot(**_kwargs):
        assert len(snapshot_calls) == 1
        return pipeline.RepresentativeAcquisitionStageResult(
            resolver=object(),
            usage=acquisition_usage,
            max_in_flight=10,
        )

    monkeypatch.setattr(
        pipeline,
        "_acquire_representative_response_bank",
        acquire_after_snapshot,
    )

    def fail_exp5_factory_after_snapshot():
        assert len(snapshot_calls) == 1
        raise OSError("factory unavailable")

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        fail_exp5_factory_after_snapshot,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SMOKE_EXECUTOR",
        lambda **_kwargs: pytest.fail("executor must not run without Exp5 transport"),
    )

    exit_code = pipeline.main(
        [
            "representative-full-plan-smoke",
            "--output-root",
            str(authority.output_root),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "bundle"),
            "--new-run",
            "--allow-provider-calls",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert body["failure_stage"] == "exp5_transport_factory"
    assert body["provider_calls"] == 3
    assert body["acquisition_current_provider_calls"] == 3
    assert body["acquisition_current_spend"] == 0.75
    assert body["exp5_current_provider_calls"] == 0
    assert len(snapshot_calls) == 1


def test_representative_cli_preserves_acquisition_calls_when_executor_raises_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    authority = _representative_authority_with_typed_bundle(tmp_path)
    snapshot_calls = _install_recording_closure_snapshot_persister(
        monkeypatch,
        authority=authority,
    )
    acquisition_usage = pipeline.RepresentativeCurrentProviderUsage(
        provider_calls=3,
        spend=0.75,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        lambda **_kwargs: authority,
    )
    def acquire_after_snapshot(**_kwargs):
        assert len(snapshot_calls) == 1
        return pipeline.RepresentativeAcquisitionStageResult(
            resolver=object(),
            usage=acquisition_usage,
            max_in_flight=10,
        )

    monkeypatch.setattr(
        pipeline,
        "_acquire_representative_response_bank",
        acquire_after_snapshot,
    )

    def exp5_transport_after_snapshot():
        assert len(snapshot_calls) == 1
        return _NoopExp5Transport()

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        exp5_transport_after_snapshot,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SMOKE_EXECUTOR",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("executor failed")),
    )

    exit_code = pipeline.main(
        [
            "representative-full-plan-smoke",
            "--output-root",
            str(authority.output_root),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "bundle"),
            "--new-run",
            "--allow-provider-calls",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert body["failure_stage"] == "representative_smoke_executor"
    assert body["provider_calls"] == 3
    assert body["acquisition_current_provider_calls"] == 3
    assert body["acquisition_current_spend"] == 0.75
    assert body["exp5_current_provider_calls"] == 0
    assert len(snapshot_calls) == 1


def test_representative_exp5_raw_runner_failure_preserves_actual_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _representative_pipeline_authority(tmp_path)
    _install_fake_exp5_ledger_factory(
        monkeypatch,
        authority=authority,
        current_calls=2,
        current_spend=None,
        missing_reason="exp5_ledger_terminal_incomplete",
    )
    conditions = authority.coverage.conditions
    roots = authority.coverage.root_case_filter
    invocations = 0

    def fake_execute(**kwargs):
        nonlocal invocations
        invocations += 1
        selected = tuple(kwargs["selected_condition_ids"])
        active_conditions = tuple(
            condition
            for condition in conditions
            if condition.condition_id in selected
        )
        if invocations == 2:
            _invoke_fake_exp5_transport(
                transport=kwargs["transport"],
                conditions=active_conditions,
                call_count=2,
                omit_endpoint=False,
            )
            raise RuntimeError("runner stopped after dispatch")
        return replace(
            _paper_suite_result(Path(kwargs["output_root"]), status="completed"),
            experiment_ids=tuple(
                dict.fromkeys(
                    condition.experiment_id for condition in active_conditions
                )
            ),
            condition_count=len(selected),
            task_count=sum(len(roots[item]) for item in selected),
            provider_attempt_count=0,
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_execute)
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())

    with pytest.raises(pipeline.RepresentativeSmokeExecutionError) as caught:
        pipeline.execute_representative_full_plan_smoke(
            authority=authority,
            response_bank_resolver=object(),
            exp5_transport=_NoopExp5Transport(),
            acquisition_usage=pipeline.RepresentativeCurrentProviderUsage(
                provider_calls=3,
                spend=0.75,
            ),
        )

    summary = caught.value.to_summary()
    assert summary["failure_stage"] == "exp5_runner"
    assert summary["provider_calls"] == 5
    assert summary["acquisition_current_provider_calls"] == 3
    assert summary["exp5_current_provider_calls"] == 2
    assert summary["exp5_current_spend"] is None
    assert summary["exp5_spend_missing_reason"] == (
        "exp5_ledger_terminal_incomplete"
    )
    assert summary["total_current_spend"] is None


def _trace_closure_preflight_suite(
    tmp_path: Path,
    *,
    provider_attempt_count: object = 0,
    trace_current_provider_call_count: object = 0,
    attempt_status: str = "succeeded",
    include_execution_request: bool = True,
    include_trace_commit: bool = True,
    unlinked_ledger_event_type: str | None = None,
) -> Path:
    import pickle

    from tests.experiments.test_paper_formal_evidence import (
        CONDITION_A,
        EXPERIMENT_A,
        _attempt,
        _event,
        _suite_bodies,
        _task,
    )
    from tokenshare.experiments.paper_catalog import load_paper_catalogs
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_experiment_contracts import (
        FrozenCaseSelection,
        FrozenConditionSelectionBinding,
    )
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
    from tokenshare.experiments.paper_models import PaperExperimentCondition
    from tokenshare.storage.events import EventLedger

    suite_root = tmp_path / "trace-suite"
    catalog = load_paper_catalogs(
        factorization_path="benchmarks/paper/factorization_catalog.v2.jsonl",
        lean_path="benchmarks/paper/lean_catalog.v1.jsonl",
        lean_lemma_graph_path="benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    )
    case_id = str(catalog.factorization_cases[0]["case_id"])
    protocol_task_id = formal_runtime_task_id("factorization", case_id)
    condition = PaperExperimentCondition(
        experiment_id=EXPERIMENT_A,
        condition_id=CONDITION_A,
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
    )
    selection = FrozenCaseSelection(
        selection_id="trace-closure-selection-v1",
        experiment_id=EXPERIMENT_A,
        suite_version="paper_v1",
        catalog_version=catalog.catalog_version,
        domain="factorization",
        paper_difficulty="easy",
        topic_family=None,
        ordered_case_ids=(case_id,),
        catalog_digest=catalog.catalog_digest,
        expected_ai_unit_count=1,
        paper_eligible_required=True,
    )
    binding = FrozenConditionSelectionBinding.from_condition(condition, selection)
    plan = PaperExperimentDispatchPlan(
        experiment_id=EXPERIMENT_A,
        output_root=f"experiments/{EXPERIMENT_A}",
        conditions=(condition,),
        condition_selection_bindings=(binding,),
    )
    bodies = _suite_bodies()
    bodies["suite"] = {
        "schema_version": "tokenshare.paper_suite.v1",
        "suite_id": "formal-suite-1",
        "experiment_ids": [EXPERIMENT_A],
        "root_case_filter": {CONDITION_A: [case_id]},
    }
    bodies["dispatch"] = {
        "schema_version": "tokenshare.paper_dispatch.v1",
        "plans": [plan.to_dict()],
    }
    bodies["catalog"] = catalog.to_dict()
    store = FormalEvidenceStore.initialize(
        output_root=suite_root,
        **bodies,
        capturing=False,
    )
    formal_conditions, formal_rows = pipeline._closure_formal_root_inventory(
        root=suite_root,
        frozen_suite=bodies["suite"],
        frozen_dispatch=bodies["dispatch"],
    )
    assert formal_conditions
    inventory_row = formal_rows[(EXPERIMENT_A, CONDITION_A, "0", case_id)]
    root_id = str(inventory_row["preregistered_root_run_id"])
    checkpoint_root = suite_root.with_name(
        suite_root.name + ".canonical_direct_evidence"
    ) / sha256(root_id.encode("utf-8")).hexdigest()
    ledger = EventLedger(checkpoint_root / "events" / "event_log.jsonl")
    ledger.append(
        event_type="TASK_CREATED",
        object_type="TaskUnit",
        object_id=protocol_task_id,
        task_id=protocol_task_id,
        payload={"run_id": "trace-execution-1"},
        idempotency_key="trace-closure-preflight-event-1",
    )
    attempt = {
        **_attempt(EXPERIMENT_A, CONDITION_A, task_id=case_id),
        "schema_version": "tokenshare.paper_attempt_result.v3",
        "record_scope": "protocol",
        "provider": "deepseek",
        "provider_attempt_count": provider_attempt_count,
        "attempt_status": attempt_status,
        # ordinal 是 trace/fault replacement 的历史事实，不是 current call。
        "provider_attempt_index": 1,
    }
    if include_execution_request:
        ledger.append(
            event_type="EXECUTION_REQUEST_RECORDED",
            object_type="ExecutionRequest",
            object_id="trace-request-1",
            task_id=protocol_task_id,
            payload={"attempt_id": attempt["attempt_id"]},
            idempotency_key="trace-closure-preflight-request-1",
        )
    trace_commit = None
    if include_trace_commit:
        trace_commit = ledger.append(
            event_type="TRACE_DELIVERY_COMMITTED.v1",
            object_type="ExecutionSubmission",
            object_id="trace-delivery-1",
            task_id=protocol_task_id,
            payload={"attempt_id": attempt["attempt_id"]},
            idempotency_key="trace-closure-preflight-commit-1",
        )
    if unlinked_ledger_event_type is not None:
        if unlinked_ledger_event_type not in {
            "EXECUTION_REQUEST_RECORDED",
            "TRACE_DELIVERY_COMMITTED.v1",
        }:
            raise AssertionError("unsupported unlinked trace ledger event")
        ledger.append(
            event_type=unlinked_ledger_event_type,
            object_type="ExecutionRequest",
            object_id="unlinked-trace-request-1",
            task_id=protocol_task_id,
            payload={"attempt_id": "unlinked-attempt-1"},
            idempotency_key="trace-closure-preflight-unlinked-event-1",
        )
    ledger_digest = ledger.read_verified_snapshot().ledger_events_digest
    task = {
        **_task(EXPERIMENT_A, CONDITION_A, task_id=case_id),
        "provider_attempt_count": provider_attempt_count,
        "protocol_task_id": protocol_task_id,
        "runtime_generation_identity": {
            "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
            "run_id": "trace-execution-1",
            "task_id": protocol_task_id,
            "root_unit_id": "trace-root-unit-1",
            "ledger_digest": ledger_digest,
        },
        "trace_source_usage": {
            "schema_version": "tokenshare.paper_trace_source_usage.v1",
            "attribution_kind": "immutable_response_bank",
            "committed_consumption_count": int(trace_commit is not None),
            "consumptions": (
                [
                    {
                        "consumption_id": trace_commit.event_id,
                        "current_attempt_id": attempt["attempt_id"],
                    }
                ]
                if trace_commit is not None
                else []
            ),
            "current_provider_call_count": trace_current_provider_call_count,
            "current_provider_spend_cny": "0",
        },
    }
    run_root = (
        suite_root
        / "experiments"
        / EXPERIMENT_A
        / "runs"
        / CONDITION_A
        / "0"
    )
    artifact = run_root / "artifacts" / "result.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"trace-result")
    store.checkpoint_root(
        experiment_id=EXPERIMENT_A,
        condition=condition.to_dict(),
        repeat_id=0,
        task=task,
        attempts=[attempt],
        faults=[],
        events=[_event(EXPERIMENT_A, CONDITION_A, task_id=case_id)],
        artifact_refs=[
            {
                "experiment_id": EXPERIMENT_A,
                "condition_id": CONDITION_A,
                "repeat_id": 0,
                "task_id": case_id,
                "path": artifact.relative_to(suite_root).as_posix(),
                "content_hash": "sha256:" + sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
    )
    store.checkpoint_condition_tail_events(
        experiment_id=EXPERIMENT_A,
        condition=condition.to_dict(),
        repeat_id=0,
        anchor_task_id=case_id,
        events=[],
    )
    store.compact_condition_snapshot(
        experiment_id=EXPERIMENT_A,
        condition=condition.to_dict(),
        repeat_id=0,
    )
    store._refresh_evidence_manifest()

    adjunct = checkpoint_root / "canonical_direct_adjunct.pickle"
    adjunct.write_bytes(
        pickle.dumps(
            {
                "producer_facts": {
                    "task": {
                        "condition_id": CONDITION_A,
                        "repeat_id": 0,
                        "task_id": case_id,
                        "protocol_task_id": protocol_task_id,
                    },
                    "run_evidence": {
                        "protocol_runtime": {
                            "run_id": "trace-execution-1",
                            "task_id": protocol_task_id,
                            "root_unit_id": "trace-root-unit-1",
                        }
                    },
                    "ledger_root_clock": {
                        "run_id": "trace-execution-1",
                        "task_id": protocol_task_id,
                        "root_unit_id": "trace-root-unit-1",
                    },
                }
            },
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    )
    checkpoint = {
        "schema_version": "tokenshare.canonical_direct_checkpoint.v1",
        "preregistered_root_run_id": root_id,
        "inventory_row_digest": inventory_row["inventory_row_digest"],
        "execution_id": "trace-execution-1",
        "task_id": protocol_task_id,
        "root_unit_id": "trace-root-unit-1",
        "ledger_path": "events/event_log.jsonl",
        "final_artifact_id": None,
        "parser_artifact_ids": [],
        "verifier_artifact_ids": [],
        "provider_artifact_ids": [],
        "source_bank_object_locators": [],
        "actual_resource_artifact_id": None,
        "trace_resource_artifact_id": None,
        "evidence_digest": "sha256:" + "a" * 64,
        "adjunct_path": adjunct.name,
        "adjunct_digest": "sha256:" + sha256(adjunct.read_bytes()).hexdigest(),
    }
    (checkpoint_root / "canonical_direct_checkpoint.json").write_text(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return suite_root


def _only_trace_closure_checkpoint(suite_root: Path) -> Path:
    return next(
        suite_root.with_name(
            suite_root.name + ".canonical_direct_evidence"
        ).glob("*/canonical_direct_checkpoint.json")
    )


def _mutate_trace_closure_adjunct(
    suite_root: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    checkpoint_path = _only_trace_closure_checkpoint(suite_root)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    adjunct_path = checkpoint_path.parent / str(checkpoint["adjunct_path"])
    adjunct = pickle.loads(adjunct_path.read_bytes())
    assert isinstance(adjunct, dict)
    mutate(adjunct)
    adjunct_path.write_bytes(pickle.dumps(adjunct, protocol=pickle.HIGHEST_PROTOCOL))
    checkpoint["adjunct_digest"] = "sha256:" + sha256(
        adjunct_path.read_bytes()
    ).hexdigest()
    checkpoint_path.write_text(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _rewrite_trace_closure_generation_refs(suite_root: Path) -> None:
    from tests.experiments.test_paper_formal_evidence import (
        _evidence_entry,
        _read_json,
        _rebuild_generation_manifest,
        _rewrite_current_digest,
        _write_json,
    )
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    condition_path = next((suite_root / "experiments").rglob("condition_manifest.json"))
    run_root = condition_path.parent
    current = _read_json(run_root / "CURRENT.json")
    generation_root = run_root / ".generations" / str(current["generation_id"])
    generation_manifest = _rebuild_generation_manifest(generation_root)
    _rewrite_current_digest(run_root, generation_manifest)

    condition = _read_json(condition_path)
    condition["current_ref"] = _evidence_entry(
        suite_root,
        run_root / "CURRENT.json",
    )
    condition["generation_manifest_ref"] = _evidence_entry(
        suite_root,
        generation_root / "generation_manifest.json",
    )
    condition["run_manifest_ref"] = _evidence_entry(
        suite_root,
        generation_root / "run_manifest.json",
    )
    _write_json(condition_path, condition)
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    evidence_path = suite_root / "evidence_manifest.json"
    evidence = _read_json(evidence_path)
    evidence["conditions"][0]["condition_manifest_ref"] = _evidence_entry(
        suite_root,
        condition_path,
    )
    _write_json(evidence_path, evidence)


def test_trace_closure_preflight_accepts_case_producer_and_protocol_runtime_identities(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)

    result = pipeline.audit_results_first_trace_closure_source(
        suite_root=suite_root,
    )

    assert result.condition_count == 1
    assert result.root_count == 1
    assert result.checkpoint_count == 1
    assert result.attempt_count == 1
    assert result.current_provider_attempt_count == 0
    assert result.legacy_attempt_row_count == 0
    assert result.positive_provider_attempt_ordinal_count == 1
    assert result.source_consumption_count == 1
    assert result.checkpoint_inventory_digest.startswith("sha256:")


def test_trace_closure_preflight_rejects_producer_case_identity_drift(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)

    def mutate(adjunct: dict[str, object]) -> None:
        facts = adjunct["producer_facts"]
        assert isinstance(facts, dict)
        task = facts["task"]
        assert isinstance(task, dict)
        task["task_id"] = "drifted-catalog-case"

    _mutate_trace_closure_adjunct(suite_root, mutate)

    with pytest.raises(ValueError, match="canonical checkpoint adjunct execution binding"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_protocol_runtime_identity_drift(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)

    def mutate(adjunct: dict[str, object]) -> None:
        facts = adjunct["producer_facts"]
        assert isinstance(facts, dict)
        evidence = facts["run_evidence"]
        assert isinstance(evidence, dict)
        runtime = evidence["protocol_runtime"]
        assert isinstance(runtime, dict)
        runtime["task_id"] = "drifted-protocol-runtime"

    _mutate_trace_closure_adjunct(suite_root, mutate)

    with pytest.raises(ValueError, match="canonical checkpoint adjunct execution binding"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_committed_delivery_without_execution_request(
    tmp_path: Path,
) -> None:
    """已消费的 trace delivery 必须回链到唯一的 current request。"""

    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        include_execution_request=False,
    )

    with pytest.raises(ValueError, match="committed trace delivery request"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_accepts_uncommitted_provider_error_without_source_consumption(
    tmp_path: Path,
) -> None:
    """R49 形状：请求已记录但没有 delivery commit，仍保留 attempt 分母。"""

    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        attempt_status="provider_error",
        include_execution_request=True,
        include_trace_commit=False,
    )

    result = pipeline.audit_results_first_trace_closure_source(
        suite_root=suite_root,
    )

    assert result.attempt_count == 1
    assert result.current_provider_attempt_count == 0
    assert result.source_consumption_count == 0


def test_trace_closure_preflight_allows_unlinked_execution_request(
    tmp_path: Path,
) -> None:
    """R49 ledger 可保留不进入当前 metric 分母的 protocol request。"""

    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        unlinked_ledger_event_type="EXECUTION_REQUEST_RECORDED",
    )

    result = pipeline.audit_results_first_trace_closure_source(
        suite_root=suite_root,
    )

    assert result.attempt_count == 1
    assert result.source_consumption_count == 1


def test_trace_closure_preflight_rejects_unlinked_trace_delivery_commit(
    tmp_path: Path,
) -> None:
    """消费型 commit 不得脱离 per-attempt 分母。"""

    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        unlinked_ledger_event_type="TRACE_DELIVERY_COMMITTED.v1",
    )

    with pytest.raises(ValueError, match="trace source ledger attempt binding"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_missing_checkpoint(tmp_path: Path) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)
    _only_trace_closure_checkpoint(suite_root).unlink()

    with pytest.raises(ValueError, match="checkpoint"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_duplicate_root(tmp_path: Path) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)
    checkpoint_path = _only_trace_closure_checkpoint(suite_root)
    duplicate_root = checkpoint_path.parents[1] / ("f" * 64)
    duplicate_root.mkdir()
    (duplicate_root / checkpoint_path.name).write_bytes(checkpoint_path.read_bytes())

    with pytest.raises(ValueError, match="duplicate.*root"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_current_generation_drift(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)
    current_path = next((suite_root / "experiments").rglob("CURRENT.json"))
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["generation_manifest_digest"] = "sha256:" + "0" * 64
    current_path.write_text(json.dumps(current) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_invalid_explicit_provider_count(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        provider_attempt_count=True,
    )

    with pytest.raises(ValueError, match="provider attempt evidence"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_source_current_domain_mixing(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(
        tmp_path,
        provider_attempt_count=1,
        trace_current_provider_call_count=1,
    )

    with pytest.raises(ValueError, match="source/current"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_condition_absent_from_formal_inventory(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_formal_evidence import _write_jsonl
    from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

    suite_root = _trace_closure_preflight_suite(tmp_path)
    conditions_path = suite_root / "conditions.jsonl"
    conditions = [
        json.loads(line)
        for line in conditions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    conditions[0] = {**conditions[0], "condition_id": "undeclared-condition"}
    _write_jsonl(conditions_path, conditions)
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()

    with pytest.raises(ValueError, match="formal condition inventory"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_checkpoint_root_inventory_drift(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)
    checkpoint_path = _only_trace_closure_checkpoint(suite_root)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    forged_root_id = "paper-direct-root:sha256:" + "d" * 64
    checkpoint["preregistered_root_run_id"] = forged_root_id
    checkpoint["inventory_row_digest"] = "sha256:" + "e" * 64
    forged_root = checkpoint_path.parents[1] / sha256(
        forged_root_id.encode("utf-8")
    ).hexdigest()
    checkpoint_path.parent.rename(forged_root)
    (forged_root / checkpoint_path.name).write_text(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="formal root inventory"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


@pytest.mark.parametrize(
    "mutation",
    ("duplicate_attempt_id", "orphan_attempt_task", "run_manifest_task_drift"),
)
def test_trace_closure_preflight_rejects_non_bijective_task_attempt_inventory(
    tmp_path: Path,
    mutation: str,
) -> None:
    from tests.experiments.test_paper_formal_evidence import (
        _read_json,
        _write_json,
        _write_jsonl,
    )

    suite_root = _trace_closure_preflight_suite(tmp_path)
    run_root = next((suite_root / "experiments").rglob("condition_manifest.json")).parent
    current = _read_json(run_root / "CURRENT.json")
    generation_root = run_root / ".generations" / str(current["generation_id"])
    attempts_path = generation_root / "per_attempt_results.jsonl"
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if mutation == "duplicate_attempt_id":
        attempts.append(dict(attempts[0]))
        _write_jsonl(attempts_path, attempts)
    elif mutation == "orphan_attempt_task":
        attempts.append(
            {
                **attempts[0],
                "attempt_id": "orphan-attempt",
                "task_id": "orphan-task",
                "attempt_status": "worker_died",
            }
        )
        _write_jsonl(attempts_path, attempts)
    else:
        run_manifest_path = generation_root / "run_manifest.json"
        run_manifest = _read_json(run_manifest_path)
        run_manifest["task_ids"] = ["undeclared-task"]
        run_manifest["completed_task_ids"] = ["undeclared-task"]
        _write_json(run_manifest_path, run_manifest)
    _rewrite_trace_closure_generation_refs(suite_root)

    with pytest.raises(
        ValueError,
        match="task/attempt inventory|task identity|task inventory",
    ):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_unindexed_static_file(
    tmp_path: Path,
) -> None:
    suite_root = _trace_closure_preflight_suite(tmp_path)
    (suite_root / "unindexed-static.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="static.*inventory"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def test_trace_closure_preflight_rejects_task_only_provider_count_drift(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_formal_evidence import _write_jsonl

    suite_root = _trace_closure_preflight_suite(tmp_path)
    task_path = next((suite_root / "experiments").rglob("per_task_results.jsonl"))
    tasks = [
        json.loads(line)
        for line in task_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tasks[0]["provider_attempt_count"] = 1
    _write_jsonl(task_path, tasks)
    _rewrite_trace_closure_generation_refs(suite_root)

    with pytest.raises(ValueError, match="provider.*accounting"):
        pipeline.audit_results_first_trace_closure_source(suite_root=suite_root)


def _closure_snapshot_authority(
    tmp_path: Path,
    *,
    condition_repeat_ids: tuple[int, ...] | None = None,
):
    """构造不触发 full freeze 的轻量 exact-typed snapshot authority。"""

    from tokenshare.executors.ai_api_config import (
        AIAPIExecutorConfig,
        AIAPIProviderEntry,
        CONFIG_SCHEMA_VERSION,
    )
    from tokenshare.executors.response_bank import canonical_digest
    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_experiment_contracts import (
        FrozenCaseSelection,
        FrozenConditionSelectionBinding,
    )
    from tokenshare.experiments.paper_formal_plan import (
        FormalConditionSnapshot,
        FormalEndpointControls,
        FormalExecutionCoverage,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
        FormalPreparedRequestRecord,
        FormalRootSnapshot,
    )
    from tokenshare.executors.ai_api_request_identity import (
        PreparedOutboundRequestFactory,
    )
    from tokenshare.experiments.paper_formal_runner import (
        APPROVED_ENDPOINT_BINDINGS_KEY,
    )
    from tokenshare.experiments.paper_models import (
        PaperBudgetResult,
        PaperExperimentCondition,
        digest_json,
    )
    from tests.experiments.test_paper_response_bank import _single_request_bundle

    catalog_digest = "sha256:" + "1" * 64
    provider_digest = "sha256:" + "2" * 64
    endpoint_digest = "sha256:" + "3" * 64
    controls_digest = "sha256:" + "4" * 64
    factorization_cases = tuple(
        {"case_id": f"snapshot-case-{index}"} for index in range(5)
    )
    catalog = PaperInputCatalogManifest(
        catalog_id="snapshot-test-catalog",
        catalog_version="v1",
        catalog_digest=catalog_digest,
        generator_version="snapshot-test",
        case_count=5,
        domain_counts={"factorization": 5, "lean_proof": 0},
        difficulty_counts={},
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-08-11T00:00:00Z",
        source_files=[],
        factorization_cases=factorization_cases,
        lean_cases=(),
    )
    experiment_ids = (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
        "exp5_real_ai_model_endpoint_comparison",
    )
    repeat_ids = condition_repeat_ids or (0,) * len(experiment_ids)
    if len(repeat_ids) != len(experiment_ids):
        raise ValueError("synthetic condition repeat inventory is invalid")
    conditions = []
    bindings = []
    roots = []
    condition_snapshots = []
    plans = []
    for index, experiment_id in enumerate(experiment_ids):
        exp5_identity = (
            {
                "model_cohort_id": "snapshot-cohort",
                "model_cohort_digest": "sha256:" + "9" * 64,
                "cohort_member_id": "snapshot-member",
                "provider_config_id": "deepseek",
                "model_entry_id": "snapshot-entry",
                "provider_family": "deepseek",
                "provider_model_id": "snapshot-model",
                "reasoning_profile_id": "snapshot-reasoning",
                "source_provider_config_digest": provider_digest,
                "model_endpoint_identity_digest": endpoint_digest,
            }
            if experiment_id == experiment_ids[-1]
            else {}
        )
        condition = PaperExperimentCondition(
            experiment_id=experiment_id,
            condition_id=f"snapshot-condition-{index}",
            domain="factorization",
            difficulty="easy",
            paper_difficulty="easy",
            worker_count=1,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=repeat_ids[index],
            seed=index,
            catalog_digest=catalog_digest,
            **exp5_identity,
        )
        selection = FrozenCaseSelection(
            selection_id=f"snapshot-selection-{index}",
            experiment_id=condition.experiment_id,
            suite_version="v1",
            catalog_version="v1",
            domain=condition.domain,
            paper_difficulty="easy",
            topic_family=None,
            ordered_case_ids=(f"snapshot-case-{index}",),
            catalog_digest=catalog_digest,
            expected_ai_unit_count=1,
            paper_eligible_required=True,
        )
        binding = FrozenConditionSelectionBinding.from_condition(
            condition,
            selection,
        )
        endpoint = FormalEndpointControls(
            provider_config_id="deepseek",
            model_entry_id="snapshot-entry",
            provider_family="deepseek",
            provider_model_id="snapshot-model",
            source_provider_config_digest=provider_digest,
            model_endpoint_identity_digest=endpoint_digest,
            reasoning_profile_id="snapshot-reasoning",
            max_tokens=100,
            timeout_seconds=60,
            max_provider_attempts=1,
            stream=False,
            request_controls_digest=controls_digest,
        )
        root = FormalRootSnapshot(
            condition=condition,
            binding=binding,
            case_id=selection.ordered_case_ids[0],
            case_record_digest=digest_json(factorization_cases[index]),
            condition_digest=condition.condition_digest,
            selection_digest=selection.selection_digest,
            seed=condition.seed,
            repeat_id=condition.repeat_id,
            split_profile_id="snapshot-split",
            split_profile_digest="sha256:" + "6" * 64,
            planned_ai_unit_ids=(
                (f"snapshot-unit-{index}",) if index < 2 else ()
            ),
            plugin_id="factorization",
            plugin_version="factorization.v1",
            endpoint_controls=endpoint,
        )
        conditions.append(condition)
        bindings.append(binding)
        roots.append(root)
        condition_snapshots.append(
            FormalConditionSnapshot(
                condition=condition,
                binding=binding,
                endpoint_controls=endpoint,
            )
        )
        plans.append(
            PaperExperimentDispatchPlan(
                experiment_id=experiment_id,
                output_root=str(tmp_path / "plans" / experiment_id),
                conditions=(condition,),
                condition_selection_bindings=(binding,),
            )
        )
    budget = PaperBudgetResult(
        budget_digest="sha256:" + "7" * 64,
        planned_experiments=experiment_ids,
        planned_conditions=5,
        planned_root_runs=5,
        planned_ai_units=2,
        max_provider_attempts=5,
        token_upper_bound=500,
        cost_upper_bound=5.0,
        wall_clock_estimate=5.0,
        quota_preflight={},
        rate_limit_preflight={},
        disk_estimate={"forecast_bytes": 500},
        status="planned",
        budget_mode="bounded",
        approval_required=False,
        approval_mode="results_first_execution",
        authorization_source="snapshot-test",
        hard_limits={
            "max_total_provider_attempts": 5,
            "max_total_tokens": 500,
            "max_cost_estimate": 5.0,
            "max_disk_bytes": 500,
        },
    )
    snapshot = FormalPlanSnapshot(
        conditions=tuple(condition_snapshots),
        roots=tuple(roots),
        condition_count=5,
        root_run_count=5,
        first_attempt_ai_unit_count=2,
        provider_calls_made=0,
        budget_digest=budget.budget_digest,
    )
    coverage = FormalExecutionCoverage(
        conditions=tuple(conditions),
        bindings=tuple(bindings),
        roots=tuple(roots),
        root_case_filter={
            condition.condition_id: (root.case_id,)
            for condition, root in zip(conditions, roots, strict=True)
        },
        source_snapshot=snapshot,
        source_snapshot_digest=snapshot.snapshot_digest,
        condition_count=5,
        root_run_count=5,
    )
    hard_limits = {
        "max_total_provider_attempts": 5,
        "max_total_tokens": 500,
        "max_cost_estimate": 5.0,
        "max_disk_bytes": 500,
    }
    projection = PaperExecutionBudgetProjection(
        source_budget=budget,
        source_snapshot=snapshot,
        coverage=coverage,
        source_budget_digest=budget.budget_digest,
        source_snapshot_digest=snapshot.snapshot_digest,
        coverage_digest=coverage.coverage_digest,
        selection_kind=coverage.selection_kind,
        condition_count=5,
        root_run_count=5,
        first_attempt_ai_unit_count=2,
        protocol_replacement_reserve=0,
        provider_attempt_upper_bound=5,
        token_upper_bound=500,
        cost_upper_bound=5.0,
        disk_upper_bound_bytes=500,
        disk_estimate={"forecast_bytes": 500},
        hard_limits=hard_limits,
    )
    config = AIAPIExecutorConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        executor_id="snapshot-executor",
        provider_family="deepseek",
        selection_policy={},
        defaults={},
        entries=[
            AIAPIProviderEntry(
                entry_id="snapshot-entry",
                enabled=True,
                base_url="https://example.invalid",
                api_key_env="DEEPSEEK_API_KEY",
                model="snapshot-model",
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={},
                pricing={
                    "currency": "CNY",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 1.0,
                },
                tags=[],
            )
        ],
        local_concurrency={},
        metadata={},
    )
    prepared_records = []
    for index, root in enumerate(roots[:2]):
        prepared_config_digest = "sha256:" + f"{index + 10:x}" * 64
        body = {
            "model": "snapshot-model",
            "messages": [
                {"role": "user", "content": root.planned_ai_unit_ids[0]}
            ],
            "max_tokens": 100,
            "stream": False,
        }
        effective_controls_digest = digest_json(
            {"stream": False, "max_tokens": 100}
        )
        prepared = PreparedOutboundRequestFactory.prepare(
            body_obj=body,
            base_url="https://example.invalid",
            endpoint="/chat/completions",
            provider_config_digest=prepared_config_digest,
            entry_id="snapshot-entry",
            configured_model="snapshot-model",
            effective_controls_digest=effective_controls_digest,
            plugin_id=root.plugin_id,
            plugin_version=root.plugin_version,
            prompt_profile_id="snapshot-prompt",
            prompt_serialization_schema="phase3.prompt_package.v1",
            body_serialization_schema="openai.chat_completions.v1",
            case_id=formal_runtime_task_id(root.condition.domain, root.case_id),
            planned_ai_unit_id=root.planned_ai_unit_ids[0],
            sample_slot_index=root.repeat_id,
            replacement_slot=0,
        )
        prepared_records.append(
            FormalPreparedRequestRecord(
                condition=root.condition,
                binding=root.binding,
                case_id=root.case_id,
                case_record_digest=root.case_record_digest,
                planned_ai_unit_id=root.planned_ai_unit_ids[0],
                sample_slot_index=root.repeat_id,
                base_replacement_slot=0,
                replacement_slot_ids=(0,),
                replacement_policy_id="formal_attempt_budget.v1",
                source_provider_config_digest=provider_digest,
                prepared_execution_config_digest=prepared_config_digest,
                provider_family=root.endpoint_controls.provider_family,
                provider_config_id=root.endpoint_controls.provider_config_id,
                model_entry_id=root.endpoint_controls.model_entry_id,
                provider_model_id=root.endpoint_controls.provider_model_id,
                reasoning_profile_id=root.endpoint_controls.reasoning_profile_id,
                model_endpoint_identity_digest=(
                    root.endpoint_controls.model_endpoint_identity_digest
                ),
                request_max_tokens=root.endpoint_controls.max_tokens,
                request_timeout_seconds=root.endpoint_controls.timeout_seconds,
                request_max_provider_attempts=(
                    root.endpoint_controls.max_provider_attempts
                ),
                request_controls_digest=(
                    root.endpoint_controls.request_controls_digest
                ),
                prompt_profile_digest=digest_json(
                    {
                        "body_digest": prepared.body_digest,
                        "prompt_profile_id": prepared.prompt_profile_id,
                        "prompt_serialization_schema": (
                            prepared.prompt_serialization_schema
                        ),
                    }
                ),
                provider_request_identity={
                    "schema_version": "phase7.provider_request_identity.v2",
                    "provider_family": root.endpoint_controls.provider_family,
                    "entry_id": prepared.entry_id,
                    "configured_model": prepared.configured_model,
                    "requested_model": body["model"],
                    "reasoning_controls": {},
                    "effective_request_controls_digest": (
                        effective_controls_digest
                    ),
                },
                prepared_request=prepared,
            )
        )
    prepared_inventory = FormalPreparedRequestInventory(
        records=tuple(prepared_records),
        record_count=2,
        unique_inference_request_count=2,
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )
    bundle = replace(
        _single_request_bundle(tmp_path),
        authorized_plan_digest=snapshot.snapshot_digest,
        profile_digest=coverage.coverage_digest,
        source_snapshot_digest=snapshot.snapshot_digest,
        source_prepared_inventory_digest=prepared_inventory.inventory_digest,
        coverage_digest=coverage.coverage_digest,
        representative_plan_digest="sha256:" + "c" * 64,
    )
    source_plan = replace(
        bundle.semantic_inventory_plan,
        condition_refs=tuple(
            {
                **ref,
                "experiment_id": "exp1_real_ai_feasibility",
            }
            for ref in bundle.semantic_inventory_plan.condition_refs
        ),
    )
    bundle = replace(
        bundle,
        semantic_inventory_plan=source_plan,
    )
    bundle = replace(
        bundle,
        bundle_digest=canonical_digest(bundle.digest_preimage()),
    )
    bundle.validate()
    authority = object.__new__(pipeline.ResultsFirstExecutionAuthority)
    values = {
        "source_validation_digest": "sha256:" + "8" * 64,
        "full_dispatch_plans": tuple(plans),
        "catalog_manifest": catalog,
        "full_snapshot": snapshot,
        "full_prepared_inventory": prepared_inventory,
        "full_budget": budget,
        "ai_api_configs": {
            "deepseek": config,
            APPROVED_ENDPOINT_BINDINGS_KEY: {
                "exp5_real_ai_model_endpoint_comparison": {
                    "snapshot": "official-binding"
                }
            },
        },
        "coverage": coverage,
        "execution_budget_projection": projection,
        "bundle": bundle,
        "bundle_root": tmp_path / "bundle",
        "output_root": tmp_path / "execution",
        "resume": False,
        "policy": pipeline._RESULTS_FIRST_EXECUTION_POLICY,
        "provider_calls_made": 0,
    }
    for name, value in values.items():
        object.__setattr__(authority, name, value)
    return authority


def _production_projectable_paid_authority(
    tmp_path: Path,
    *,
    condition_repeat_ids: tuple[int, ...] | None = None,
    default_ai_units: int = 1,
):
    """构造由 production budget/projector 支配的小型 paid restore authority。"""

    from tokenshare.executors.ai_api_config import (
        AIAPIExecutorConfig,
        AIAPIProviderEntry,
        CONFIG_SCHEMA_VERSION,
    )
    from tokenshare.executors.ai_api_request_identity import (
        PreparedOutboundRequestFactory,
    )
    from tokenshare.executors.response_bank import canonical_digest
    from tokenshare.experiments.paper_budget import (
        plan_paper_suite,
        project_paper_execution_budget,
    )
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_experiment_contracts import (
        FrozenCaseSelection,
        FrozenConditionSelectionBinding,
    )
    from tokenshare.experiments.paper_formal_plan import (
        FormalConditionSnapshot,
        FormalEndpointControls,
        FormalPlanSnapshot,
        FormalPreparedRequestInventory,
        FormalPreparedRequestRecord,
        FormalRootSnapshot,
        derive_paper_formal_execution_coverage,
    )
    from tokenshare.experiments.paper_formal_runner import (
        APPROVED_ENDPOINT_BINDINGS_KEY,
    )
    from tokenshare.experiments.paper_models import (
        PaperExperimentCondition,
        digest_json,
    )
    from tokenshare.experiments.paper_response_bank import replacement_slots_for
    from tests.experiments.test_paper_budget import _exp5_v3_budget_fixture
    from tests.experiments.test_paper_response_bank import _single_request_bundle

    (
        source_catalog,
        source_exp5_conditions,
        _source_exp5_selections,
        exp5_preflight,
        endpoint_token_ceilings,
    ) = _exp5_v3_budget_fixture()
    deepseek_config_digest = AIAPIExecutorConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        executor_id="paid-projector-deepseek",
        provider_family="deepseek",
        selection_policy={},
        defaults={},
        entries=[
            AIAPIProviderEntry(
                entry_id="snapshot-entry",
                enabled=True,
                base_url="https://example.invalid",
                api_key_env="DEEPSEEK_API_KEY",
                model="snapshot-model",
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={},
                pricing={
                    "currency": "CNY",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 1.0,
                },
                tags=[],
            )
        ],
        local_concurrency={},
        metadata={},
    ).config_digest
    source_case = source_catalog.factorization_cases[0]
    case = {
        **source_case,
        "case_id": "paid-projector-case",
        "target_n": max(10_019, (default_ai_units + 2) ** 2 + 1),
        "candidate_start": 2,
        "candidate_end": max(21, default_ai_units + 1),
        "candidate_divisor_count": max(20, default_ai_units),
        "oracle_prime_factors": source_case["oracle_prime_factors"],
        "factor_position_quantile": source_case["factor_position_quantile"],
        "split_params": {
            **source_case["split_params"],
            "requested_child_count": default_ai_units,
        },
    }
    catalog_digest = digest_json(
        {
            "schema_version": "tokenshare.test.paid_projector_catalog.v1",
            "case": case,
        }
    )
    catalog = replace(
        source_catalog,
        catalog_id="paid-projector-catalog",
        catalog_version="v1",
        catalog_digest=catalog_digest,
        case_count=1,
        domain_counts={"factorization": 1, "lean_proof": 0},
        factorization_cases=(case,),
        lean_cases=(),
        lean_lemma_graph_cases=(),
    )
    experiment_ids = pipeline._REPRESENTATIVE_EXPERIMENT_IDS
    core_conditions = tuple(
        PaperExperimentCondition(
            schema_version="tokenshare.paper_condition.v2",
            experiment_id=experiment_id,
            condition_id=f"paid-projector-{experiment_id}",
            domain="factorization",
            difficulty="hard",
            paper_difficulty="hard",
            worker_count=1,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=index,
            catalog_digest=catalog_digest,
        )
        for index, experiment_id in enumerate(experiment_ids[:4])
    )
    exp5_conditions = tuple(
        replace(condition, catalog_digest=catalog_digest)
        for condition in source_exp5_conditions
    )
    conditions = core_conditions + exp5_conditions
    repeats = condition_repeat_ids or (0,) * len(conditions)
    if len(repeats) != len(conditions):
        raise ValueError("paid projector repeat inventory is invalid")
    conditions = tuple(
        replace(condition, repeat_id=repeat_id)
        for condition, repeat_id in zip(conditions, repeats, strict=True)
    )
    frozen_budget_selections = []
    selections = []
    bindings = []
    for index, condition in enumerate(conditions):
        expected_ai_units = (
            20
            if condition.experiment_id == experiment_ids[1]
            else default_ai_units
        )
        budget_selection = {
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
            "ordered_case_ids": [case["case_id"]],
            "case_expected_ai_unit_counts": {
                case["case_id"]: expected_ai_units
            },
        }
        if condition.experiment_id == experiment_ids[1]:
            budget_selection["split_profile_id"] = (
                "factorization.exp2_contiguous_20way.v1"
            )
        frozen_budget_selections.append(budget_selection)
        selection = FrozenCaseSelection(
            selection_id=f"paid-projector-selection-{index}",
            experiment_id=condition.experiment_id,
            suite_version="v1",
            catalog_version=catalog.catalog_version,
            domain=condition.domain,
            paper_difficulty=condition.paper_difficulty,
            topic_family=condition.topic_family,
            ordered_case_ids=(case["case_id"],),
            catalog_digest=catalog_digest,
            expected_ai_unit_count=expected_ai_units,
            paper_eligible_required=True,
        )
        selections.append(selection)
        bindings.append(
            FrozenConditionSelectionBinding.from_condition(condition, selection)
        )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        token_upper_bound_by_endpoint_identity_digest=endpoint_token_ceilings,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        model_endpoint_cohort_preflight=exp5_preflight,
        frozen_selections=frozen_budget_selections,
    )
    commitments = {
        (row["condition_id"], row["case_id"]): row
        for row in budget.quota_preflight["budget_commitments"][
            "ai_unit_commitments"
        ]
    }
    roots = []
    condition_snapshots = []
    for condition, binding in zip(conditions, bindings, strict=True):
        endpoint_digest = (
            condition.model_endpoint_identity_digest
            or "sha256:" + "3" * 64
        )
        endpoint = FormalEndpointControls(
            provider_config_id=condition.provider_config_id or "deepseek",
            model_entry_id=condition.model_entry_id or "snapshot-entry",
            provider_family=condition.provider_family or "deepseek",
            provider_model_id=condition.provider_model_id or "snapshot-model",
            source_provider_config_digest=(
                condition.source_provider_config_digest
                or deepseek_config_digest
            ),
            model_endpoint_identity_digest=endpoint_digest,
            reasoning_profile_id=(
                condition.reasoning_profile_id or "snapshot-reasoning"
            ),
            max_tokens=endpoint_token_ceilings.get(endpoint_digest, 100),
            timeout_seconds=600,
            max_provider_attempts=1,
            stream=False,
            request_controls_digest=digest_json(
                {
                    "condition_id": condition.condition_id,
                    "max_tokens": endpoint_token_ceilings.get(
                        endpoint_digest,
                        100,
                    ),
                    "timeout_seconds": 600,
                    "stream": False,
                }
            ),
        )
        commitment = commitments[(condition.condition_id, case["case_id"])]
        roots.append(
            FormalRootSnapshot(
                condition=condition,
                binding=binding,
                case_id=case["case_id"],
                case_record_digest=commitment["case_digest"],
                condition_digest=condition.condition_digest,
                selection_digest=binding.selection.selection_digest,
                seed=condition.seed,
                repeat_id=condition.repeat_id,
                split_profile_id=(
                    "factorization.exp2_contiguous_20way.v1"
                    if condition.experiment_id == experiment_ids[1]
                    else str(case["split_params"]["strategy_id"])
                ),
                split_profile_digest=commitment["split_profile_digest"],
                planned_ai_unit_ids=tuple(commitment["planned_ai_unit_ids"]),
                plugin_id="factorization",
                plugin_version="factorization.v1",
                endpoint_controls=endpoint,
            )
        )
        condition_snapshots.append(
            FormalConditionSnapshot(
                condition=condition,
                binding=binding,
                endpoint_controls=endpoint,
            )
        )
    roots = tuple(roots)
    snapshot = FormalPlanSnapshot(
        conditions=tuple(condition_snapshots),
        roots=roots,
        condition_count=len(conditions),
        root_run_count=len(roots),
        first_attempt_ai_unit_count=sum(
            len(root.planned_ai_unit_ids) for root in roots
        ),
        provider_calls_made=0,
        budget_digest=budget.budget_digest,
    )
    plans = tuple(
        PaperExperimentDispatchPlan(
            experiment_id=experiment_id,
            output_root=str(tmp_path / "plans" / experiment_id),
            conditions=tuple(
                condition
                for condition in conditions
                if condition.experiment_id == experiment_id
            ),
            condition_selection_bindings=tuple(
                binding
                for condition, binding in zip(conditions, bindings, strict=True)
                if condition.experiment_id == experiment_id
            ),
        )
        for experiment_id in experiment_ids
    )
    coverage = derive_paper_formal_execution_coverage(
        snapshot=snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog,
        selected_condition_ids=tuple(
            condition.condition_id for condition in conditions
        ),
        root_case_filter={
            condition.condition_id: (case["case_id"],)
            for condition in conditions
        },
        selection_kind="filtered",
    )
    projection = project_paper_execution_budget(
        snapshot=snapshot,
        budget=budget,
        coverage=coverage,
    )
    prepared_records = []
    for record_index, root in enumerate(roots, start=1):
        for planned_ai_unit_id in root.planned_ai_unit_ids:
            prepared_config_digest = "sha256:" + f"{record_index:064x}"
            body = {
                "model": root.endpoint_controls.provider_model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"{root.condition.condition_id}:{planned_ai_unit_id}"
                        ),
                    }
                ],
                "max_tokens": root.endpoint_controls.max_tokens,
                "stream": False,
            }
            effective_controls_digest = digest_json(
                {
                    "stream": False,
                    "max_tokens": root.endpoint_controls.max_tokens,
                }
            )
            prepared = PreparedOutboundRequestFactory.prepare(
                body_obj=body,
                base_url="https://example.invalid",
                endpoint="/chat/completions",
                provider_config_digest=prepared_config_digest,
                entry_id=root.endpoint_controls.model_entry_id,
                configured_model=root.endpoint_controls.provider_model_id,
                effective_controls_digest=effective_controls_digest,
                plugin_id=root.plugin_id,
                plugin_version=root.plugin_version,
                prompt_profile_id="paid-projector-prompt",
                prompt_serialization_schema="phase3.prompt_package.v1",
                body_serialization_schema="openai.chat_completions.v1",
                case_id=formal_runtime_task_id(
                    root.condition.domain,
                    root.case_id,
                ),
                planned_ai_unit_id=planned_ai_unit_id,
                sample_slot_index=root.repeat_id,
                replacement_slot=0,
            )
            prepared_records.append(
                FormalPreparedRequestRecord(
                    condition=root.condition,
                    binding=root.binding,
                    case_id=root.case_id,
                    case_record_digest=root.case_record_digest,
                    planned_ai_unit_id=planned_ai_unit_id,
                    sample_slot_index=root.repeat_id,
                    base_replacement_slot=0,
                    replacement_slot_ids=replacement_slots_for(
                        experiment_id=root.condition.experiment_id,
                        fault_type=str(root.condition.fault_type),
                        ablation_mode=str(root.condition.ablation_mode),
                    ),
                    replacement_policy_id="formal_attempt_budget.v1",
                    source_provider_config_digest=(
                        root.endpoint_controls.source_provider_config_digest
                    ),
                    prepared_execution_config_digest=prepared_config_digest,
                    provider_family=root.endpoint_controls.provider_family,
                    provider_config_id=root.endpoint_controls.provider_config_id,
                    model_entry_id=root.endpoint_controls.model_entry_id,
                    provider_model_id=root.endpoint_controls.provider_model_id,
                    reasoning_profile_id=(
                        root.endpoint_controls.reasoning_profile_id
                    ),
                    model_endpoint_identity_digest=(
                        root.endpoint_controls.model_endpoint_identity_digest
                    ),
                    request_max_tokens=root.endpoint_controls.max_tokens,
                    request_timeout_seconds=root.endpoint_controls.timeout_seconds,
                    request_max_provider_attempts=(
                        root.endpoint_controls.max_provider_attempts
                    ),
                    request_controls_digest=(
                        root.endpoint_controls.request_controls_digest
                    ),
                    prompt_profile_digest=digest_json(
                        {
                            "body_digest": prepared.body_digest,
                            "prompt_profile_id": prepared.prompt_profile_id,
                            "prompt_serialization_schema": (
                                prepared.prompt_serialization_schema
                            ),
                        }
                    ),
                    provider_request_identity={
                        "schema_version": "phase7.provider_request_identity.v2",
                        "provider_family": root.endpoint_controls.provider_family,
                        "entry_id": prepared.entry_id,
                        "configured_model": prepared.configured_model,
                        "requested_model": body["model"],
                        "reasoning_controls": {},
                        "effective_request_controls_digest": (
                            effective_controls_digest
                        ),
                    },
                    prepared_request=prepared,
                )
            )
            record_index += 1
    prepared_records = tuple(prepared_records)
    inventory = FormalPreparedRequestInventory(
        records=prepared_records,
        record_count=len(prepared_records),
        unique_inference_request_count=len(
            {record.inference_request_digest for record in prepared_records}
        ),
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )

    def config(provider_family: str, entries: tuple[AIAPIProviderEntry, ...]):
        return AIAPIExecutorConfig(
            schema_version=CONFIG_SCHEMA_VERSION,
            executor_id=f"paid-projector-{provider_family}",
            provider_family=provider_family,
            selection_policy={},
            defaults={},
            entries=list(entries),
            local_concurrency={},
            metadata={},
        )

    deepseek_config = config(
        "deepseek",
        (
            AIAPIProviderEntry(
                entry_id="snapshot-entry",
                enabled=True,
                base_url="https://example.invalid",
                api_key_env="DEEPSEEK_API_KEY",
                model="snapshot-model",
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={},
                pricing={
                    "currency": "CNY",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 1.0,
                },
                tags=[],
            ),
        ),
    )
    exp5_member_plans = exp5_preflight["member_plans"]
    siliconflow_config = config(
        "siliconflow",
        tuple(
            AIAPIProviderEntry(
                entry_id=plan["selected_entry_id"],
                enabled=True,
                base_url="https://example.invalid",
                api_key_env="SILICONFLOW_API_KEY",
                model=plan["provider_model_id"],
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides=dict(
                    plan["request_controls"]["provider_specific_reasoning"]
                ),
                pricing=dict(plan["pricing_snapshot"]),
                tags=[],
            )
            for plan in exp5_member_plans.values()
        ),
    )
    configs = {
        "deepseek": deepseek_config,
        "siliconflow": siliconflow_config,
        APPROVED_ENDPOINT_BINDINGS_KEY: {
            "exp5_real_ai_model_endpoint_comparison": exp5_preflight
        },
    }
    bundle = replace(
        _single_request_bundle(tmp_path),
        authorized_plan_digest=snapshot.snapshot_digest,
        profile_digest=coverage.coverage_digest,
        source_snapshot_digest=snapshot.snapshot_digest,
        source_prepared_inventory_digest=inventory.inventory_digest,
        coverage_digest=coverage.coverage_digest,
        representative_plan_digest="sha256:" + "c" * 64,
    )
    source_plan = replace(
        bundle.semantic_inventory_plan,
        condition_refs=tuple(
            {
                **ref,
                "experiment_id": "exp1_real_ai_feasibility",
            }
            for ref in bundle.semantic_inventory_plan.condition_refs
        ),
    )
    bundle = replace(bundle, semantic_inventory_plan=source_plan)
    bundle = replace(bundle, bundle_digest=canonical_digest(bundle.digest_preimage()))
    bundle.validate()
    return pipeline.ResultsFirstExecutionAuthority(
        source_validation_digest="sha256:" + "8" * 64,
        full_dispatch_plans=plans,
        catalog_manifest=catalog,
        full_snapshot=snapshot,
        full_prepared_inventory=inventory,
        full_budget=budget,
        ai_api_configs=configs,
        coverage=coverage,
        execution_budget_projection=projection,
        bundle=bundle,
        bundle_root=tmp_path / "bundle",
        output_root=tmp_path / "execution",
        resume=False,
        policy=pipeline._RESULTS_FIRST_EXECUTION_POLICY,
        provider_calls_made=0,
    )


def _small_full_paid_restore_authority(tmp_path: Path):
    from tokenshare.executors.response_bank import canonical_digest
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_full_coverage,
    )

    authority = _production_projectable_paid_authority(
        tmp_path,
        # 该 service-boundary fixture 只有一个 Exp1 sample slot；所有 replay
        # 条件必须引用同一合法 slot，不能伪造缺失的 Exp1 repeat=1 authority。
        condition_repeat_ids=(0, 0, 0, 0, 0, 0, 0, 0),
    )
    coverage = derive_paper_formal_full_coverage(
        snapshot=authority.full_snapshot,
        dispatch_plans=authority.full_dispatch_plans,
        catalog_manifest=authority.catalog_manifest,
    )
    projection = pipeline._paid_restore_project_paper_execution_budget(
        snapshot=authority.full_snapshot,
        budget=authority.full_budget,
        coverage=coverage,
    )
    bundle = replace(
        authority.bundle,
        profile_digest=coverage.coverage_digest,
        coverage_digest=coverage.coverage_digest,
    )
    bundle = replace(
        bundle,
        bundle_digest=canonical_digest(bundle.digest_preimage()),
    )
    bundle.validate()
    return replace(
        authority,
        coverage=coverage,
        execution_budget_projection=projection,
        bundle=bundle,
    )


def _full_current_pricing_authority_for_test(*, source_config, current_config, approval_digest="sha256:" + "a" * 64) -> object:
    """构造只允许价格刷新的 Full Exp1 authority；不涉及 provider 调用。"""

    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        FullCurrentAcquisitionPricingAuthority,
    )
    from tokenshare.experiments.run_paper_experiments import (
        _pricing_refresh_execution_identity,
    )

    current_entries = tuple(entry for entry in current_config.entries if entry.enabled)
    assert len(current_entries) == 1
    source_identity = _pricing_refresh_execution_identity(source_config)
    current_identity = _pricing_refresh_execution_identity(current_config)
    body = {
        "schema_version": "tokenshare.newfullrun_exp1_current_pricing_authority.v1",
        "provider_config_id": "deepseek",
        "provider_config_digest": current_config.config_digest,
        "source_provider_config_digest": source_config.config_digest,
        "source_execution_identity_digest": source_identity,
        "execution_identity_digest": current_identity,
        "pricing_by_entry": {
            current_entries[0].entry_id: dict(current_entries[0].pricing)
        },
        "provider_calls_made": 0,
    }
    return FullCurrentAcquisitionPricingAuthority(
        selection_kind="full_exp1_exp3_exp5",
        provider_config_id="deepseek",
        source_provider_config_digest=source_config.config_digest,
        source_execution_identity_digest=source_identity,
        current_provider_config_digest=current_config.config_digest,
        current_execution_identity_digest=current_identity,
        pricing_by_entry={
            current_entries[0].entry_id: FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal(
                    str(
                        current_entries[0].pricing["input_per_million_tokens"]
                        if "input_per_million_tokens" in current_entries[0].pricing
                        else current_entries[0].pricing[
                            "uncached_input_per_million_tokens"
                        ]
                    )
                ),
                output_per_million_tokens=Decimal(
                    str(current_entries[0].pricing["output_per_million_tokens"])
                ),
            )
        },
        current_pricing_authority_digest=digest_json(body),
        full_budget_approval_authority_digest=approval_digest,
        approved_exp1_budget_digest="sha256:" + "b" * 64,
    )


def _current_priced_config_for_test(*, source_config, pricing: dict, model: str | None = None):
    """仅测试用：从 frozen config 派生当前 config，避免碰真实 provider 配置。"""

    source_entries = tuple(entry for entry in source_config.entries if entry.enabled)
    assert len(source_entries) == 1
    current_entry = replace(
        source_entries[0],
        pricing=pricing,
        model=source_entries[0].model if model is None else model,
    )
    return replace(source_config, entries=[current_entry])


def test_full_current_pricing_authority_allows_only_approved_pricing_drift(
    tmp_path: Path,
) -> None:
    """Full 的新价格不得覆盖 frozen prepared provenance。"""

    from tokenshare.experiments.paper_response_bank import (
        materialize_results_first_unified_acquisition_plan,
        prepare_results_first_acquisition_authority,
    )
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing

    base = _small_full_paid_restore_authority(tmp_path)
    source_config = base.ai_api_configs["deepseek"]
    current_pricing = {
        "currency": "CNY",
        "input_per_million_tokens": 2.0,
        "output_per_million_tokens": 3.0,
    }
    current_config = _current_priced_config_for_test(
        source_config=source_config,
        pricing=current_pricing,
    )
    current_configs = {**base.ai_api_configs, "deepseek": current_config}
    current_authority = _full_current_pricing_authority_for_test(
        source_config=source_config,
        current_config=current_config,
    )

    atomic = prepare_results_first_acquisition_authority(
        full_snapshot=base.full_snapshot,
        full_prepared_inventory=base.full_prepared_inventory,
        full_budget=base.full_budget,
        coverage=base.coverage,
        execution_budget_projection=base.execution_budget_projection,
        catalog_manifest=base.catalog_manifest,
        ai_api_configs=current_configs,
        source_ai_api_configs=base.ai_api_configs,
        full_current_pricing_authority=current_authority,
        planning_artifact_root=tmp_path / "full-current-pricing-plan",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("2.0"),
                output_per_million_tokens=Decimal("3.0"),
            )
        },
        requested_at="2026-08-20T00:00:00Z",
    )

    materialized = materialize_results_first_unified_acquisition_plan(
        plan=atomic.plan,
    )

    assert materialized.acquisition_requests
    assert {
        request.frozen_pricing for request in materialized.acquisition_requests
    } == {
        FrozenPricing(
            currency="CNY",
            input_per_million_tokens=Decimal("2.0"),
            output_per_million_tokens=Decimal("3.0"),
        )
    }
    assert {
        request.prepared_request.provider_config_digest
        for request in materialized.acquisition_requests
    } == {
        record.prepared_execution_config_digest
        for record in base.full_prepared_inventory.records
        if record.condition.experiment_id == "exp1_real_ai_feasibility"
    }


def test_representative_acquisition_rejects_unapproved_current_pricing_drift(
    tmp_path: Path,
) -> None:
    """representative 仍严格消费 R13 frozen pricing，不能偷用 Full 例外。"""

    from tokenshare.experiments.paper_response_bank import (
        materialize_results_first_unified_acquisition_plan,
        prepare_results_first_acquisition_authority,
    )
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing

    base = _production_projectable_paid_authority(tmp_path)
    source_config = base.ai_api_configs["deepseek"]
    current_config = _current_priced_config_for_test(
        source_config=source_config,
        pricing={
            "currency": "CNY",
            "input_per_million_tokens": 2.0,
            "output_per_million_tokens": 3.0,
        },
    )
    atomic = prepare_results_first_acquisition_authority(
        full_snapshot=base.full_snapshot,
        full_prepared_inventory=base.full_prepared_inventory,
        full_budget=base.full_budget,
        coverage=base.coverage,
        execution_budget_projection=base.execution_budget_projection,
        catalog_manifest=base.catalog_manifest,
        ai_api_configs={**base.ai_api_configs, "deepseek": current_config},
        planning_artifact_root=tmp_path / "representative-old-pricing-plan",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("2.0"),
                output_per_million_tokens=Decimal("3.0"),
            )
        },
        requested_at="2026-08-20T00:00:00Z",
    )

    with pytest.raises(
        ValueError,
        match="representative acquisition provider authority drift",
    ):
        materialize_results_first_unified_acquisition_plan(plan=atomic.plan)


def test_full_current_pricing_authority_rejects_execution_identity_drift(
    tmp_path: Path,
) -> None:
    """Full 的显式价格例外不能放宽 model/endpoint/request-control 身份。"""

    from tokenshare.experiments.paper_response_bank import (
        materialize_results_first_unified_acquisition_plan,
        prepare_results_first_acquisition_authority,
    )
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing

    base = _small_full_paid_restore_authority(tmp_path)
    source_config = base.ai_api_configs["deepseek"]
    current_config = _current_priced_config_for_test(
        source_config=source_config,
        pricing={
            "currency": "CNY",
            "input_per_million_tokens": 2.0,
            "output_per_million_tokens": 3.0,
        },
        model="identity-drift-model",
    )
    current_authority = _full_current_pricing_authority_for_test(
        source_config=source_config,
        current_config=current_config,
    )
    atomic = prepare_results_first_acquisition_authority(
        full_snapshot=base.full_snapshot,
        full_prepared_inventory=base.full_prepared_inventory,
        full_budget=base.full_budget,
        coverage=base.coverage,
        execution_budget_projection=base.execution_budget_projection,
        catalog_manifest=base.catalog_manifest,
        ai_api_configs={**base.ai_api_configs, "deepseek": current_config},
        source_ai_api_configs=base.ai_api_configs,
        full_current_pricing_authority=current_authority,
        planning_artifact_root=tmp_path / "full-identity-drift-plan",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("2.0"),
                output_per_million_tokens=Decimal("3.0"),
            )
        },
        requested_at="2026-08-20T00:00:00Z",
    )

    with pytest.raises(
        ValueError,
        match="Full current pricing execution identity drift",
    ):
        materialize_results_first_unified_acquisition_plan(plan=atomic.plan)


def test_results_first_representative_and_full_share_production_call_graph() -> None:
    """selection 不得分叉到简化 runner/router/metrics 路径。"""

    service_source = inspect.getsource(pipeline._execute_results_first_service_input)
    wrapper_source = inspect.getsource(
        pipeline.execute_representative_full_plan_smoke
    )
    execution_source = inspect.getsource(pipeline.execute_results_first_experiments)
    subset_source = inspect.getsource(pipeline._run_results_first_formal_subset)
    formal_source = inspect.getsource(pipeline._execute_formal_suite)

    assert "_REPRESENTATIVE_SMOKE_EXECUTOR(" in service_source
    assert "execute_results_first_experiments(" in wrapper_source
    assert "_TRACE_CONTEXT_BUILDER(" in execution_source
    assert "_run_results_first_formal_subset(" in execution_source
    assert "_FORMAL_SUITE_EXECUTOR(" in subset_source
    assert "trace_context=trace_context" in subset_source
    assert "enable_metric_closure=policy.enable_metric_closure" in subset_source
    assert "execute_paper_formal_suite(**kwargs)" in formal_source
    assert pipeline._REPRESENTATIVE_SMOKE_EXECUTOR is (
        pipeline.execute_representative_full_plan_smoke
    )
    assert pipeline._FORMAL_SUITE_EXECUTOR is pipeline._execute_formal_suite
    assert pipeline._TRACE_CONTEXT_BUILDER is pipeline._build_trace_context


def _write_closure_snapshot_suite(*, authority, suite_root: Path) -> None:
    suite_root.mkdir(parents=True)
    bodies = {
        "paper_dispatch_plans.json": pipeline._closure_snapshot_dispatch_body(
            authority.full_dispatch_plans,
            active_experiment_ids=(
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            ),
        ),
        "input_catalog_manifest.json": authority.catalog_manifest.to_dict(),
        "run_budget.json": authority.full_budget.to_dict(),
    }
    for name, body in bodies.items():
        (suite_root / name).write_text(
            json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _rewrite_closure_snapshot_descriptor(
    *, output_root: Path, mutate
) -> dict[str, object]:
    from tokenshare.experiments.paper_models import digest_json

    path = output_root / "closure-replay-snapshot" / "closure_replay_snapshot.v1.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body.pop("descriptor_digest")
    mutate(body)
    body["descriptor_digest"] = digest_json(body)
    path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ref_path = (
        output_root
        / "closure-replay-snapshot"
        / "closure_replay_snapshot_ref.v1.json"
    )
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    ref.pop("ref_digest")
    ref["descriptor_digest"] = body["descriptor_digest"]
    ref["snapshot_digest"] = body["snapshot_digest"]
    ref["pickle_sha256"] = body["pickle_sha256"]
    ref["ref_digest"] = digest_json(ref)
    ref_path.write_text(
        json.dumps(ref, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ref


def test_formal_prepared_inventory_stream_round_trip_uses_no_rebuild_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_plan
    from tokenshare.experiments.paper_formal_plan import (
        FormalPreparedRequestInventory,
        FormalPreparedRequestRecord,
        load_formal_prepared_request_inventory,
        persist_formal_prepared_request_inventory,
    )

    authority = _closure_snapshot_authority(tmp_path)
    inventory_root = tmp_path / "prepared-inventory"

    manifest = persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )

    for name in (
        "freeze_paper_formal_prepared_request_inventory",
        "_prepare_formal_root_templates",
        "_independently_prepare_formal_snapshot_templates",
    ):
        monkeypatch.setattr(
            paper_formal_plan,
            name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"load called forbidden rebuild path: {_name}")
            ),
        )
    index_calls = 0
    original_index = paper_formal_plan._formal_prepared_inventory_root_index

    def counted_index(snapshot):
        nonlocal index_calls
        index_calls += 1
        return original_index(snapshot)

    monkeypatch.setattr(
        paper_formal_plan,
        "_formal_prepared_inventory_root_index",
        counted_index,
    )
    loaded = load_formal_prepared_request_inventory(
        output_root=inventory_root,
        snapshot=authority.full_snapshot,
        expected_manifest=manifest,
    )

    assert loaded == authority.full_prepared_inventory
    assert index_calls == 1
    assert FormalPreparedRequestInventory.from_dict(
        authority.full_prepared_inventory.to_dict(),
        snapshot=authority.full_snapshot,
    ) == authority.full_prepared_inventory
    assert manifest["record_count"] == 2
    assert manifest["source_snapshot_digest"] == authority.full_snapshot.snapshot_digest
    records_path = inventory_root / manifest["records_path"]
    lines = records_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(
        line == json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for line in lines
    )

    first = authority.full_prepared_inventory.records[0]
    with pytest.raises(ValueError, match="fields"):
        FormalPreparedRequestRecord.from_dict(
            {**first.to_dict(), "unexpected": True},
            snapshot=authority.full_snapshot,
        )
    reversed_inventory = replace(
        authority.full_prepared_inventory,
        records=tuple(reversed(authority.full_prepared_inventory.records)),
    )
    with pytest.raises(ValueError, match="ordered keyset"):
        persist_formal_prepared_request_inventory(
            inventory=reversed_inventory,
            snapshot=authority.full_snapshot,
            output_root=tmp_path / "reversed-inventory",
        )


@pytest.mark.parametrize("layer", ("record", "inventory", "manifest"))
@pytest.mark.parametrize("value", (False, 0.0, "0"))
def test_formal_prepared_inventory_persistence_rejects_non_integer_zero(
    tmp_path: Path,
    layer: str,
    value: object,
) -> None:
    from tokenshare.experiments import paper_formal_plan
    from tokenshare.experiments.paper_formal_plan import (
        FormalPreparedRequestInventory,
        FormalPreparedRequestRecord,
    )
    from tokenshare.experiments.paper_models import digest_json

    authority = _closure_snapshot_authority(tmp_path)
    if layer == "record":
        body = authority.full_prepared_inventory.records[0].to_dict()
        body["provider_calls_made"] = value
        with pytest.raises(ValueError, match="provider_calls_made"):
            FormalPreparedRequestRecord.from_dict(
                body,
                snapshot=authority.full_snapshot,
            )
        return
    if layer == "inventory":
        body = authority.full_prepared_inventory.to_dict()
        body["provider_calls_made"] = value
        with pytest.raises(ValueError, match="provider_calls_made"):
            FormalPreparedRequestInventory.from_dict(
                body,
                snapshot=authority.full_snapshot,
            )
        return
    inventory_root = tmp_path / "inventory"
    paper_formal_plan.persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )
    manifest_path = (
        inventory_root / paper_formal_plan.FORMAL_PREPARED_INVENTORY_MANIFEST_NAME
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider_calls_made"] = value
    manifest.pop("manifest_digest")
    manifest["manifest_digest"] = digest_json(manifest)
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provider_calls_made"):
        paper_formal_plan.load_formal_prepared_request_inventory(
            output_root=inventory_root,
            snapshot=authority.full_snapshot,
        )


def test_formal_prepared_inventory_commit_failure_leaves_no_partial_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_plan

    authority = _closure_snapshot_authority(tmp_path)
    inventory_root = tmp_path / "inventory"
    original_replace = paper_formal_plan.os.replace

    def fail_manifest_replace(source, target):
        if Path(target).name == paper_formal_plan.FORMAL_PREPARED_INVENTORY_MANIFEST_NAME:
            raise OSError("synthetic manifest replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(paper_formal_plan.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="synthetic manifest"):
        paper_formal_plan.persist_formal_prepared_request_inventory(
            inventory=authority.full_prepared_inventory,
            snapshot=authority.full_snapshot,
            output_root=inventory_root,
        )
    assert not inventory_root.exists()

    monkeypatch.setattr(paper_formal_plan.os, "replace", original_replace)
    paper_formal_plan.persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )
    assert inventory_root.is_dir()

    existing_root = tmp_path / "existing"
    existing_root.mkdir()
    sentinel = existing_root / "user-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="partial"):
        paper_formal_plan.persist_formal_prepared_request_inventory(
            inventory=authority.full_prepared_inventory,
            snapshot=authority.full_snapshot,
            output_root=existing_root,
        )
    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    "tamper",
    (
        "manifest_extra",
        "manifest_missing",
        "records_sha",
        "records_size",
        "record_count",
        "source_snapshot",
        "inventory_digest",
        "ordered_keyset",
        "record_extra",
        "record_missing",
        "record_duplicate",
        "record_reorder",
    ),
)
def test_formal_prepared_inventory_loader_rejects_resigned_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    from tokenshare.experiments import paper_formal_plan
    from tokenshare.experiments.paper_models import digest_json

    authority = _closure_snapshot_authority(tmp_path)
    inventory_root = tmp_path / tamper
    paper_formal_plan.persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )
    manifest_path = (
        inventory_root / paper_formal_plan.FORMAL_PREPARED_INVENTORY_MANIFEST_NAME
    )
    records_path = (
        inventory_root / paper_formal_plan.FORMAL_PREPARED_INVENTORY_RECORDS_NAME
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "manifest_extra":
        manifest["unexpected"] = True
    elif tamper == "manifest_missing":
        manifest.pop("unique_inference_request_count")
    elif tamper == "records_sha":
        manifest["records_sha256"] = "sha256:" + "0" * 64
    elif tamper == "records_size":
        manifest["records_size_bytes"] += 1
    elif tamper == "record_count":
        manifest["record_count"] += 1
    elif tamper == "source_snapshot":
        manifest["source_snapshot_digest"] = "sha256:" + "0" * 64
    elif tamper == "inventory_digest":
        manifest["inventory_digest"] = "sha256:" + "0" * 64
    elif tamper == "ordered_keyset":
        manifest["ordered_keyset_digest"] = "sha256:" + "0" * 64
    else:
        rows = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
        ]
        if tamper == "record_extra":
            rows[0]["unexpected"] = True
        elif tamper == "record_missing":
            rows[0].pop("prompt_profile_digest")
        elif tamper == "record_duplicate":
            rows[1] = rows[0]
        elif tamper == "record_reorder":
            rows.reverse()
        records_bytes = b"".join(
            (
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            for row in rows
        )
        records_path.write_bytes(records_bytes)
        manifest["records_sha256"] = (
            "sha256:" + sha256(records_bytes).hexdigest()
        )
        manifest["records_size_bytes"] = len(records_bytes)
    manifest.pop("manifest_digest", None)
    manifest["manifest_digest"] = digest_json(manifest)
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises((TypeError, ValueError)):
        paper_formal_plan.load_formal_prepared_request_inventory(
            output_root=inventory_root,
            snapshot=authority.full_snapshot,
        )


def _install_synthetic_paid_projector(monkeypatch: pytest.MonkeyPatch):
    calls = []
    production_projector = pipeline._paid_restore_project_paper_execution_budget

    def projector(*, snapshot, budget, coverage):
        calls.append((snapshot, budget, coverage))
        return production_projector(
            snapshot=snapshot,
            budget=budget,
            coverage=coverage,
        )

    monkeypatch.setattr(pipeline, "_PAID_RESTORE_BUDGET_PROJECTOR", projector)
    return calls


def _synthetic_paid_restore_preflight(authority, *, snapshot_digest: str):
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_execution_coverage,
    )
    from tokenshare.experiments.paper_models import digest_json

    def file_inventory(paths):
        result = {}
        for path in paths:
            payload = path.read_bytes()
            result[path.resolve().as_posix()] = {
                "sha256": "sha256:" + sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        return result

    projection = authority.execution_budget_projection.to_dict()
    projection.update(
        {
            "time_upper_bound": None,
            "time_missing_reason": (
                "tokenshare.paper_execution_budget_projection.v1_has_no_time_upper_bound"
            ),
        }
    )
    experiment_rows = []
    root_counts = {}
    for experiment_id in pipeline._REPRESENTATIVE_EXPERIMENT_IDS:
        conditions = tuple(
            item
            for item in authority.coverage.conditions
            if item.experiment_id == experiment_id
        )
        roots = tuple(
            item
            for item in authority.coverage.roots
            if item.condition.experiment_id == experiment_id
        )
        root_counts[experiment_id] = len(roots)
        selected_condition_ids = tuple(
            condition.condition_id for condition in conditions
        )
        subset_coverage = derive_paper_formal_execution_coverage(
            snapshot=authority.full_snapshot,
            dispatch_plans=authority.full_dispatch_plans,
            catalog_manifest=authority.catalog_manifest,
            selected_condition_ids=selected_condition_ids,
            root_case_filter={
                condition_id: authority.coverage.root_case_filter[condition_id]
                for condition_id in selected_condition_ids
            },
            selection_kind="filtered",
        )
        subset = pipeline._paid_restore_project_paper_execution_budget(
            snapshot=authority.full_snapshot,
            budget=authority.full_budget,
            coverage=subset_coverage,
        ).to_dict()
        subset.update(
            {
                "time_upper_bound": None,
                "time_missing_reason": (
                    "tokenshare.paper_execution_budget_projection.v1_has_no_time_upper_bound"
                ),
            }
        )
        experiment_rows.append(
            {"experiment_id": experiment_id, "projection": subset}
        )
    condition_rows = []
    for condition in authority.coverage.conditions:
        roots = tuple(
            root
            for root in authority.coverage.roots
            if root.condition is condition
        )
        condition_rows.append(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "experiment_id": condition.experiment_id,
                "repeat_id": condition.repeat_id,
                "seed": condition.seed,
                "planned_root_run_count": len(roots),
                "planned_first_attempt_ai_unit_count": sum(
                    len(root.planned_ai_unit_ids) for root in roots
                ),
                "provider_attempt_upper_bound": None,
                "token_upper_bound": None,
                "cost_upper_bound": None,
                "disk_upper_bound_bytes": None,
                "time_upper_bound": None,
                "projection_missing_reason": (
                    "condition_breakdown_is_coverage_planned_counts_only"
                ),
            }
        )
    closure_root = authority.output_root / "closure-replay-snapshot"
    source_paths = (
        closure_root / pipeline._CLOSURE_REPLAY_DESCRIPTOR_NAME,
        closure_root / pipeline._CLOSURE_REPLAY_PICKLE_NAME,
        closure_root / pipeline._CLOSURE_REPLAY_REF_NAME,
    )
    repeat_ids = []
    for condition in authority.coverage.conditions:
        if condition.repeat_id not in repeat_ids:
            repeat_ids.append(condition.repeat_id)
    selection_kind = (
        "full"
        if authority.coverage.selection_kind == "full"
        else "representative"
    )
    body = {
        "schema_version": "tokenshare.paid_representative_projection_preflight.v1",
        "source": {
            "execution_root": authority.output_root.resolve().as_posix(),
            "snapshot_digest": snapshot_digest,
            "full_snapshot_digest": authority.full_snapshot.snapshot_digest,
            "coverage_digest": authority.coverage.coverage_digest,
            "full_budget_digest": authority.full_budget.budget_digest,
            "catalog_digest": authority.catalog_manifest.catalog_digest,
            "source_validation_digest": authority.source_validation_digest,
            "provider_config_digests": pipeline._closure_snapshot_config_digests(
                authority.ai_api_configs
            ),
            "source_files": file_inventory(source_paths),
        },
        "production_source_files": file_inventory(
            tuple(
                sorted(
                    pipeline._paid_restore_production_source_paths(),
                    key=lambda path: path.as_posix(),
                )
            )
        ),
        "selection": {
            "kind": selection_kind,
            "condition_count": authority.coverage.condition_count,
            "root_run_count": authority.coverage.root_run_count,
            "repeat_ids": repeat_ids,
        },
        "root_counts_by_experiment": root_counts,
        "projection": projection,
        "experiment_projections": experiment_rows,
        "condition_projections": condition_rows,
        "api_key_status": {
            "schema_version": "tokenshare.provider_key_presence_authority.v1",
            "scope": "provider_zero_preparation",
            "scope_provenance": "fixed_unset_without_secret_resolution",
            "statuses": {
                "DEEPSEEK_API_KEY": "UNSET",
                "SILICONFLOW_API_KEY": "UNSET",
            },
        },
        "tripwires": {
            "authority_rebuilds": 0,
            "inventory_rebuilds": 0,
            "provider_calls": 0,
            "network_calls": 0,
            "acquisition_calls": 0,
            "adapter_calls": 0,
            "checker_calls": 0,
            "verifier_calls": 0,
        },
        "source_unchanged": True,
    }
    return {**body, "report_digest": digest_json(body)}


def _persist_synthetic_paid_restore_fixture(
    tmp_path: Path,
    *,
    authority=None,
    fresh_warm_output: bool = False,
):
    from tokenshare.experiments import paper_formal_plan
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        materialize_results_first_acquisition_bundle,
        prepare_results_first_acquisition_authority,
    )

    authority = authority or _production_projectable_paid_authority(tmp_path)
    atomic = prepare_results_first_acquisition_authority(
        full_snapshot=authority.full_snapshot,
        full_prepared_inventory=authority.full_prepared_inventory,
        full_budget=authority.full_budget,
        coverage=authority.coverage,
        execution_budget_projection=authority.execution_budget_projection,
        catalog_manifest=authority.catalog_manifest,
        ai_api_configs=authority.ai_api_configs,
        planning_artifact_root=tmp_path / "paid-planning",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("1.0"),
                output_per_million_tokens=Decimal("1.0"),
            )
        },
        requested_at="2026-08-09T00:00:00Z",
        max_acquisition_concurrency=10,
    )
    corrected_bundle_root = tmp_path / "prepared-corrected-exp1-bundle"
    corrected_bundle = materialize_results_first_acquisition_bundle(
        plan=atomic.plan,
        bundle_root=corrected_bundle_root,
        resume=False,
    )
    authority = replace(
        authority,
        source_validation_digest=atomic.validation_digest,
        bundle=corrected_bundle,
        bundle_root=corrected_bundle_root,
    )
    authority.__post_init__()
    snapshot_ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    inventory_root = tmp_path / "frozen-prepared-inventory"
    inventory_manifest = paper_formal_plan.persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )
    preflight = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=snapshot_ref["snapshot_digest"],
    )
    preflight_path = tmp_path / "expected-preflight.json"
    preflight_path.write_bytes(pipeline._closure_snapshot_json_bytes(preflight))
    frozen_snapshot_root = authority.output_root
    if fresh_warm_output:
        authority = replace(authority, output_root=tmp_path / "reserved-execution")
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
        persist_results_first_provider_budget,
    )

    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
    )
    persist_results_first_provider_budget(
        output_root=tmp_path / "paid-planning",
        authority=provider_budget,
    )
    authority = replace(authority, provider_budget_authority=provider_budget)
    warm_evidence = pipeline.persist_results_first_paid_restore_binding_v2(
        authority=authority,
        frozen_snapshot_root=frozen_snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        planning_artifact_root=tmp_path / "paid-planning",
        prepared_inventory_manifest=inventory_manifest,
        expected_preflight=preflight_path,
    )
    assert type(warm_evidence) is pipeline.PaidRestoreCanonicalDerivationEvidence
    return authority, inventory_root, preflight_path, warm_evidence


def _repersist_synthetic_paid_restore_binding(
    *,
    authority,
    inventory_root: Path,
    preflight_path: Path,
    warm_evidence,
):
    return pipeline.persist_results_first_paid_restore_binding_v2(
        authority=authority,
        frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        planning_artifact_root=warm_evidence.planning_artifact_root,
        prepared_inventory_manifest=warm_evidence.prepared_inventory_manifest,
        expected_preflight=preflight_path,
    )


@pytest.mark.parametrize("budget_failure", ("missing", "drift"))
def test_paid_restore_budget_failure_occurs_before_binding_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_failure: str,
) -> None:
    pair_writes: list[str] = []
    original_write = pipeline._atomic_replace_bytes

    def track_pair_write(path: Path, value: bytes) -> None:
        if path.name in {
            pipeline._PAID_RESTORE_BINDING_NAME,
            pipeline._PAID_RESTORE_BINDING_REF_NAME,
        }:
            pair_writes.append(path.name)
        original_write(path, value)

    def reject_budget(*_args, **_kwargs):
        raise ValueError(f"provider budget {budget_failure}")

    def bomb_write_once(path: Path, _value: bytes) -> None:
        pair_writes.append(path.name)
        raise AssertionError("binding/ref write occurred before budget validation")

    monkeypatch.setattr(pipeline, "_atomic_replace_bytes", track_pair_write)
    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", bomb_write_once)
    monkeypatch.setattr(
        pipeline,
        "_load_results_first_provider_budget_exact",
        reject_budget,
    )

    with pytest.raises(ValueError, match=f"provider budget {budget_failure}"):
        _persist_synthetic_paid_restore_fixture(tmp_path)

    assert pair_writes == []


def test_paid_restore_evidence_validation_occurs_before_binding_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_writes: list[str] = []
    original_write = pipeline._atomic_replace_bytes

    def track_pair_write(path: Path, value: bytes) -> None:
        if path.name in {
            pipeline._PAID_RESTORE_BINDING_NAME,
            pipeline._PAID_RESTORE_BINDING_REF_NAME,
        }:
            pair_writes.append(path.name)
        original_write(path, value)

    def reject_evidence(*_args, **_kwargs):
        raise ValueError("warm evidence lineage drift")

    def bomb_write_once(path: Path, _value: bytes) -> None:
        pair_writes.append(path.name)
        raise AssertionError("binding/ref write occurred before evidence validation")

    monkeypatch.setattr(pipeline, "_atomic_replace_bytes", track_pair_write)
    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", bomb_write_once)
    monkeypatch.setattr(
        pipeline,
        "_mint_paid_restore_canonical_derivation_evidence",
        reject_evidence,
    )

    with pytest.raises(ValueError, match="warm evidence lineage drift"):
        _persist_synthetic_paid_restore_fixture(tmp_path)

    assert pair_writes == []


@pytest.mark.parametrize("survivor", ("binding", "ref"))
def test_paid_restore_partial_binding_pair_is_incomplete_and_preserved(
    tmp_path: Path,
    survivor: str,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    closure_root = (
        warm_evidence.frozen_snapshot_root / pipeline._CLOSURE_REPLAY_DIRECTORY
    )
    binding_path = closure_root / pipeline._PAID_RESTORE_BINDING_NAME
    ref_path = closure_root / pipeline._PAID_RESTORE_BINDING_REF_NAME
    removed_path = ref_path if survivor == "binding" else binding_path
    kept_path = binding_path if survivor == "binding" else ref_path
    kept_bytes = kept_path.read_bytes()
    removed_path.unlink()

    with pytest.raises(ValueError, match="binding publication is incomplete"):
        _repersist_synthetic_paid_restore_binding(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )

    assert kept_path.read_bytes() == kept_bytes
    assert not removed_path.exists()


def test_paid_restore_ref_is_last_write_once_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    closure_root = (
        warm_evidence.frozen_snapshot_root / pipeline._CLOSURE_REPLAY_DIRECTORY
    )
    binding_path = closure_root / pipeline._PAID_RESTORE_BINDING_NAME
    ref_path = closure_root / pipeline._PAID_RESTORE_BINDING_REF_NAME
    binding_path.unlink()
    ref_path.unlink()
    original_write_once = getattr(
        pipeline,
        "_atomic_write_once_bytes",
        pipeline._atomic_replace_bytes,
    )

    def fail_ref(path: Path, value: bytes) -> None:
        if path.name == pipeline._PAID_RESTORE_BINDING_REF_NAME:
            raise OSError("simulated ref commit failure")
        original_write_once(path, value)

    monkeypatch.setattr(
        pipeline,
        "_atomic_write_once_bytes",
        fail_ref,
        raising=False,
    )

    with pytest.raises(OSError, match="simulated ref commit failure"):
        _repersist_synthetic_paid_restore_binding(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )

    assert binding_path.is_file()
    assert not ref_path.exists()
    with pytest.raises(ValueError, match="binding publication is incomplete"):
        _repersist_synthetic_paid_restore_binding(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )


def _warm_reload_synthetic_paid_restore(
    *,
    authority,
    inventory_root: Path,
    preflight_path: Path,
    warm_evidence,
    expected_corrected_bundle_digest: str | None = None,
):
    return pipeline.load_results_first_paid_execution_authority_v2(
        frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=preflight_path,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=warm_evidence.planning_artifact_root,
        output_root=authority.output_root,
        selection="representative",
        resume=False,
        expected_corrected_bundle_digest=(
            expected_corrected_bundle_digest or authority.bundle.bundle_digest
        ),
        expected_provider_budget_digest=(
            authority.provider_budget_authority.authority_digest
        ),
        warm_derivation_evidence=warm_evidence,
    )


def test_paid_restore_warm_evidence_rejects_process_local_copies(
    tmp_path: Path,
) -> None:
    _authority, _inventory_root, _preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )

    with pytest.raises(TypeError):
        copy.copy(warm_evidence)
    with pytest.raises(TypeError):
        copy.deepcopy(warm_evidence)
    with pytest.raises(TypeError):
        pickle.dumps(warm_evidence)


def test_paid_restore_warm_evidence_is_consumed_exactly_once(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )

    assert (
        _warm_reload_synthetic_paid_restore(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )
        is authority
    )
    with pytest.raises(TypeError, match="one-shot|registered"):
        _warm_reload_synthetic_paid_restore(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )


def test_paid_restore_failed_warm_validation_still_consumes_evidence(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )

    with pytest.raises(ValueError, match="warm expected authority digest drift"):
        _warm_reload_synthetic_paid_restore(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
            expected_corrected_bundle_digest="sha256:" + "0" * 64,
        )
    with pytest.raises(TypeError, match="one-shot|registered"):
        _warm_reload_synthetic_paid_restore(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            warm_evidence=warm_evidence,
        )


def test_paid_restore_loader_accepts_only_exact_provided_corrected_bundle(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_formal_plan import (
        persist_formal_prepared_request_inventory,
    )
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionPlanBundle,
        materialize_results_first_acquisition_bundle,
        prepare_results_first_acquisition_authority,
    )

    base = _production_projectable_paid_authority(tmp_path)
    atomic = prepare_results_first_acquisition_authority(
        full_snapshot=base.full_snapshot,
        full_prepared_inventory=base.full_prepared_inventory,
        full_budget=base.full_budget,
        coverage=base.coverage,
        execution_budget_projection=base.execution_budget_projection,
        catalog_manifest=base.catalog_manifest,
        ai_api_configs=base.ai_api_configs,
        planning_artifact_root=tmp_path / "strict-restore-planning",
        api_key_env_by_provider_family={"deepseek": "DEEPSEEK_API_KEY"},
        frozen_pricing_by_provider_family={
            "deepseek": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("1.0"),
                output_per_million_tokens=Decimal("1.0"),
            )
        },
        requested_at="2026-08-09T00:00:00Z",
        max_acquisition_concurrency=10,
    )
    planning_root = tmp_path / "strict-restore-planning"
    corrected_bundle_root = tmp_path / "strict-corrected-exp1-bundle"
    corrected_bundle = materialize_results_first_acquisition_bundle(
        plan=atomic.plan,
        bundle_root=corrected_bundle_root,
        resume=False,
    )
    authority = replace(
        base,
        source_validation_digest=atomic.validation_digest,
        bundle=corrected_bundle,
        bundle_root=corrected_bundle_root,
    )
    snapshot_ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    inventory_root = tmp_path / "strict-frozen-inventory"
    manifest = persist_formal_prepared_request_inventory(
        inventory=authority.full_prepared_inventory,
        snapshot=authority.full_snapshot,
        output_root=inventory_root,
    )
    preflight = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=snapshot_ref["snapshot_digest"],
    )
    preflight_path = tmp_path / "strict-preflight.json"
    preflight_path.write_text(
        json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
        persist_results_first_provider_budget,
    )

    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=corrected_bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
    )
    persist_results_first_provider_budget(
        output_root=planning_root,
        authority=provider_budget,
    )
    authority = replace(authority, provider_budget_authority=provider_budget)
    pipeline.persist_results_first_paid_restore_binding_v2(
        authority=authority,
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        planning_artifact_root=planning_root,
        prepared_inventory_manifest=manifest,
        expected_preflight=preflight_path,
    )
    loaded = pipeline.load_results_first_paid_execution_authority_v2(
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=preflight_path,
        plan_bundle_root=corrected_bundle_root,
        planning_artifact_root=planning_root,
        output_root=tmp_path / "strict-execution",
        selection="representative",
        resume=False,
        expected_corrected_bundle_digest=corrected_bundle.bundle_digest,
        expected_provider_budget_digest=provider_budget.authority_digest,
    )

    assert type(loaded.bundle) is AcquisitionPlanBundle
    assert loaded.bundle.to_dict() == corrected_bundle.to_dict()
    assert all(
        ref["experiment_id"] == "exp1_real_ai_feasibility"
        for ref in loaded.bundle.semantic_inventory_plan.condition_refs
    )
    assert loaded.provider_calls_made == 0
    assert loaded.provider_budget_authority == provider_budget


def test_paid_restore_binding_v2_persists_exp1_source_plan_derivation(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_models import digest_json

    authority, _inventory_root, _preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    binding_path = (
        authority.output_root
        / pipeline._CLOSURE_REPLAY_DIRECTORY
        / pipeline._PAID_RESTORE_BINDING_NAME
    )
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    source_plan = authority.bundle.to_dict()["semantic_inventory_plan"]
    derivation = binding["acquisition_derivation"]

    assert binding["base_snapshot_ref"]["snapshot_digest"]
    assert binding["source_exp1_inventory_plan"] == source_plan
    assert binding["source_exp1_inventory_plan_digest"] == digest_json(source_plan)
    assert derivation == {
        "schema_version": "tokenshare.results_first_paid_restore_derivation.v1",
        "source_snapshot_digest": authority.bundle.source_snapshot_digest,
        "source_prepared_inventory_digest": (
            authority.bundle.source_prepared_inventory_digest
        ),
        "coverage_digest": authority.bundle.coverage_digest,
        "representative_plan_digest": authority.bundle.representative_plan_digest,
        "acquisition_bundle_digest": authority.bundle.bundle_digest,
        "provider_calls_made": 0,
        "derivation_digest": derivation["derivation_digest"],
    }
    assert derivation["derivation_digest"] == digest_json(
        {
            key: value
            for key, value in derivation.items()
            if key != "derivation_digest"
        }
    )
    assert binding["provider_calls_made"] == 0


def test_paid_restore_warm_reload_skips_inventory_parse_and_rematerialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_plan, paper_response_bank
    from tokenshare.experiments.paper_formal_plan import FormalPreparedRequestRecord

    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )
    calls: list[str] = []

    def bomb(*_args, **_kwargs):
        calls.append("forbidden")
        raise AssertionError("warm reload reconstructed the full inventory or plan")

    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        bomb,
    )
    identity_calls = 0
    original_identity = FormalPreparedRequestRecord.request_identity_digest

    def counted_identity(self) -> str:
        nonlocal identity_calls
        identity_calls += 1
        return original_identity.fget(self)

    monkeypatch.setattr(
        FormalPreparedRequestRecord,
        "request_identity_digest",
        property(counted_identity),
    )

    loaded = pipeline.load_results_first_paid_execution_authority_v2(
        frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=preflight_path,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=tmp_path / "paid-planning",
        output_root=authority.output_root,
        selection="representative",
        resume=False,
        expected_corrected_bundle_digest=authority.bundle.bundle_digest,
        expected_provider_budget_digest=(
            authority.provider_budget_authority.authority_digest
        ),
        warm_derivation_evidence=warm_evidence,
    )

    assert loaded is authority
    assert calls == []
    assert identity_calls == len(warm_evidence.inventory.records)
    assert loaded.provider_calls_made == 0


def test_paid_restore_warm_reload_rejects_inventory_byte_drift(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_formal_plan import (
        FORMAL_PREPARED_INVENTORY_RECORDS_NAME,
    )

    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )
    records_path = inventory_root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME
    payload = bytearray(records_path.read_bytes())
    payload[0] = ord("[") if payload[0] != ord("[") else ord("{")
    records_path.write_bytes(bytes(payload))

    with pytest.raises(ValueError, match="inventory records digest or count drift"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=preflight_path,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=tmp_path / "paid-planning",
            output_root=authority.output_root,
            selection="representative",
            resume=False,
            expected_corrected_bundle_digest=authority.bundle.bundle_digest,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
            warm_derivation_evidence=warm_evidence,
        )


def test_paid_restore_warm_reload_rechecks_mutable_inventory_identity(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )
    mutable_identity = warm_evidence.inventory.records[0].provider_request_identity
    assert type(mutable_identity) is dict
    mutable_identity["round10_drift"] = True

    with pytest.raises(ValueError, match="identity|provider"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=preflight_path,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=tmp_path / "paid-planning",
            output_root=authority.output_root,
            selection="representative",
            resume=False,
            expected_corrected_bundle_digest=authority.bundle.bundle_digest,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
            warm_derivation_evidence=warm_evidence,
        )


def test_paid_restore_warm_evidence_cannot_be_minted_by_external_constructor(
    tmp_path: Path,
) -> None:
    _authority, _inventory_root, _preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )

    with pytest.raises(TypeError, match="persist mint"):
        pipeline.PaidRestoreCanonicalDerivationEvidence()
    with pytest.raises(TypeError, match="persist mint"):
        replace(warm_evidence)


@pytest.mark.parametrize("drift", ("missing", "byte"))
def test_paid_restore_warm_reload_rejects_persisted_preflight_byte_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )
    if drift == "missing":
        preflight_path.unlink()
    else:
        preflight_path.write_bytes(preflight_path.read_bytes() + b" ")

    with pytest.raises(ValueError, match="preflight.*unreadable|preflight.*byte"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=preflight_path,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=tmp_path / "paid-planning",
            output_root=authority.output_root,
            selection="representative",
            resume=False,
            expected_corrected_bundle_digest=authority.bundle.bundle_digest,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
            warm_derivation_evidence=warm_evidence,
        )


def test_paid_restore_warm_manifest_requires_loaded_snapshot_lineage() -> None:
    snapshot = SimpleNamespace(
        coverage=SimpleNamespace(
            source_snapshot=SimpleNamespace(snapshot_digest="sha256:" + "1" * 64)
        )
    )

    with pytest.raises(ValueError, match="manifest snapshot lineage"):
        pipeline._paid_restore_validate_inventory_snapshot_lineage(
            manifest={"source_snapshot_digest": "sha256:" + "2" * 64},
            snapshot=snapshot,
        )


def test_paid_restore_warm_new_run_rechecks_fresh_output_root(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path, fresh_warm_output=True)
    )
    assert not authority.output_root.exists()
    authority.output_root.mkdir()

    with pytest.raises(ValueError, match="warm new-run output target must be fresh"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=warm_evidence.frozen_snapshot_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=preflight_path,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=tmp_path / "paid-planning",
            output_root=authority.output_root,
            selection="representative",
            resume=False,
            expected_corrected_bundle_digest=authority.bundle.bundle_digest,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
            warm_derivation_evidence=warm_evidence,
        )


@pytest.mark.parametrize("allow_provider_calls", (False, True))
def test_paid_restore_service_pins_expected_provider_budget_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_provider_calls: bool,
) -> None:
    expected_digest = "sha256:" + "a" * 64
    captured: list[object] = []

    def reject_mismatched_budget(**kwargs: object):
        captured.append(kwargs.get("expected_provider_budget_digest"))
        raise ValueError("results-first provider budget authority digest drift")

    loader_name = (
        "load_results_first_paid_execution_authority_v2"
        if allow_provider_calls
        else "load_results_first_paid_plan_only_authority_v2"
    )
    monkeypatch.setattr(pipeline, loader_name, reject_mismatched_budget)
    for name in ("snapshot", "inventory", "bundle", "planning"):
        (tmp_path / name).mkdir()
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text("{}\n", encoding="utf-8")
    output_root = tmp_path / "output"
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={
            "selection": "representative",
            "planning_artifact_root": str(tmp_path / "planning"),
            "plan_bundle_root": str(tmp_path / "bundle"),
            "frozen_closure_snapshot_root": str(tmp_path / "snapshot"),
            "frozen_prepared_inventory_root": str(tmp_path / "inventory"),
            "expected_preflight": str(preflight_path),
            "expected_provider_budget_digest": expected_digest,
            "plan_only": not allow_provider_calls,
            "allow_provider_calls": allow_provider_calls,
            "resume": False,
        },
    )

    with pytest.raises(ValueError, match="provider budget authority digest drift"):
        pipeline._results_first_service_input_from_cli_authority(request)

    assert captured == [expected_digest]
    assert not output_root.exists()


def _install_paid_restore_bombs(monkeypatch: pytest.MonkeyPatch):
    from tokenshare.experiments import paper_formal_plan

    bomb_calls = []

    def bomb(*args, **kwargs):
        bomb_calls.append((args, kwargs))
        raise AssertionError("paid restore called a forbidden builder or side effect")

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        bomb,
    )
    monkeypatch.setattr(
        paper_formal_plan,
        "freeze_paper_formal_prepared_request_inventory",
        bomb,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        bomb,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        bomb,
    )
    monkeypatch.setattr(pipeline, "_REPRESENTATIVE_SMOKE_EXECUTOR", bomb)
    return bomb_calls


@pytest.mark.parametrize(
    ("plan_only", "allow_provider_calls", "expected_message"),
    (
        (False, False, "exactly one execution mode"),
        (True, False, "frozen restore authority"),
        (False, True, "frozen restore authority"),
        (True, True, "exactly one execution mode"),
    ),
)
def test_results_first_execution_requires_restore_authority_before_any_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_only: bool,
    allow_provider_calls: bool,
    expected_message: str,
) -> None:
    bomb_calls = _install_paid_restore_bombs(monkeypatch)
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=tmp_path / "output",
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={
            "selection": "representative",
            "planning_artifact_root": str(tmp_path / "planning"),
            "plan_bundle_root": str(tmp_path / "bundle"),
            "plan_only": plan_only,
            "allow_provider_calls": allow_provider_calls,
            "resume": False,
        },
    )

    with pytest.raises(ValueError, match=expected_message):
        pipeline._results_first_service_input_from_cli_authority(request)

    assert bomb_calls == []


def test_results_first_paid_restore_plan_only_uses_frozen_typed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(
            tmp_path,
            authority=_production_projectable_paid_authority(
                tmp_path,
                default_ai_units=28,
            ),
        )
    )
    bomb_calls = _install_paid_restore_bombs(monkeypatch)
    output_root = tmp_path / "paid-output"
    planning_root = tmp_path / "paid-planning"
    corrected_bundle_root = authority.bundle_root
    captured = []

    original_adapter = pipeline._AUTHORITATIVE_SERVICE_ADAPTERS[
        "run-results-first"
    ]

    def capture_corrected_plan(request):
        from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

        value = request._service_input
        assert type(value.authority.bundle) is AcquisitionPlanBundle
        assert value.authority.bundle_root == corrected_bundle_root
        assert value.authority.provider_budget_authority is not None
        captured.append(value.authority)
        return original_adapter(request)
    monkeypatch.setitem(
        pipeline._AUTHORITATIVE_SERVICE_ADAPTERS,
        "run-results-first",
        capture_corrected_plan,
    )

    exit_code = pipeline.main(
        [
            "run-results-first",
            "--selection",
            "representative",
            "--output-root",
            str(output_root),
            "--planning-artifact-root",
            str(planning_root),
            "--plan-bundle-root",
            str(corrected_bundle_root),
            "--frozen-closure-snapshot-root",
            str(authority.output_root),
            "--frozen-prepared-inventory-root",
            str(inventory_root),
            "--expected-preflight",
            str(preflight_path),
            "--expected-provider-budget-digest",
            authority.provider_budget_authority.authority_digest,
            "--new-run",
            "--plan-only",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0, result
    assert result["status"] == "ready"
    assert result["provider_calls"] == 0
    assert result["source_snapshot_digest"] == authority.full_snapshot.snapshot_digest
    assert result["coverage_digest"] == authority.coverage.coverage_digest
    assert result["provider_budget"]["exp1"]["calls"] == 28
    assert result["provider_budget"]["exp2_4"] == {
        "current_provider_calls": 0,
        "current_tokens": 0,
        "current_cny": "0",
    }
    assert result["provider_budget"]["exp5"]["calls"] == 112
    assert result["provider_budget"]["exp5"]["tokens"] == 6_881_280
    assert result["provider_budget"]["exp5"]["cny"] == "79.249408"
    budget_path = planning_root / "results_first_provider_budget.v1.json"
    assert budget_path.is_file()
    assert json.loads(budget_path.read_text(encoding="utf-8"))[
        "authority_digest"
    ] == result["provider_budget"]["authority_digest"]
    assert len(captured) == 1
    assert bomb_calls == []
    assert corrected_bundle_root.exists()
    assert not output_root.exists()


def test_post_acquisition_trace_seal_binds_all_resume_authorities_and_fails_closed(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "paid-output"
    output_root.mkdir()
    authority_paths = {
        "attempt_history": output_root / "exp1_attempt_history_authority.final.v1.json",
        "router_authority": output_root / "results_first_trace_router_authority.v1.json",
        "exp2_derivation": (
            output_root
            / "exp2-projected-response-bank"
            / "exp2_projection_derivation.v1.json"
        ),
    }
    for index, path in enumerate(authority_paths.values(), start=1):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"index": index}) + "\n", encoding="utf-8")

    seal = pipeline.persist_results_first_post_acquisition_trace_seal(
        output_root=output_root,
        closure_snapshot_digest="sha256:" + "1" * 64,
        source_manifest_digest="sha256:" + "2" * 64,
        attempt_history_path=authority_paths["attempt_history"],
        router_authority_path=authority_paths["router_authority"],
        exp2_derivation_path=authority_paths["exp2_derivation"],
    )
    loaded = pipeline.load_results_first_post_acquisition_trace_seal(
        output_root=output_root,
        expected_closure_snapshot_digest="sha256:" + "1" * 64,
        expected_source_manifest_digest="sha256:" + "2" * 64,
    )
    assert loaded == seal

    authority_paths["exp2_derivation"].unlink()
    with pytest.raises(ValueError, match="post-acquisition|missing"):
        pipeline.load_results_first_post_acquisition_trace_seal(
            output_root=output_root,
            expected_closure_snapshot_digest="sha256:" + "1" * 64,
            expected_source_manifest_digest="sha256:" + "2" * 64,
        )


def test_closure_resume_validates_post_acquisition_seal_before_scratch_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    snapshot = SimpleNamespace(snapshot_digest="sha256:" + "1" * 64)
    resolver = SimpleNamespace(
        index=SimpleNamespace(
            manifest=SimpleNamespace(manifest_digest="sha256:" + "2" * 64)
        )
    )
    monkeypatch.setattr(
        pipeline,
        "_closure_replay_reject_reparse_path",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: (calls.append("snapshot") or snapshot),
    )

    def reject_seal(**kwargs: object) -> object:
        calls.append("seal")
        assert kwargs["expected_closure_snapshot_digest"] == snapshot.snapshot_digest
        assert kwargs["expected_source_manifest_digest"] == resolver.index.manifest.manifest_digest
        raise ValueError("focused stale seal")

    monkeypatch.setattr(
        pipeline, "load_results_first_post_acquisition_trace_seal", reject_seal
    )
    monkeypatch.setattr(
        pipeline,
        "_closure_replay_require_fresh_target",
        lambda **_kwargs: pytest.fail("scratch validation ran before seal"),
    )
    monkeypatch.setattr(
        pipeline.tempfile,
        "mkdtemp",
        lambda **_kwargs: pytest.fail("scratch directory created before seal"),
    )
    monkeypatch.setattr(
        pipeline.shutil,
        "copytree",
        lambda *_args, **_kwargs: pytest.fail("source copied before seal"),
    )

    with pytest.raises(ValueError, match="focused stale seal"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=tmp_path / "source",
            snapshot_root=tmp_path / "authority",
            scratch_output_root=tmp_path / "scratch",
            response_bank_resolver=resolver,
            transport=object(),
        )
    assert calls == ["snapshot", "seal"]


def test_results_first_full_exp5_budget_uses_selection_exact_projection(
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )

    authority = _production_projectable_paid_authority(
        tmp_path,
        default_ai_units=1_248,
    )
    projection = pipeline._selection_exact_exp5_execution_projection(authority)
    budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=projection,
    )

    assert budget.exp5["calls"] == 4_992
    assert budget.exp5["tokens"] == 306_708_480
    assert budget.exp5["cny"] == "3532.259328"
    assert budget.exp2_4["current_provider_calls"] == 0
    assert budget.exp1["new_paid"] is True
    assert dict(budget.global_new_paid) == {
        "calls": budget.exp1["calls"] + budget.exp5["calls"],
        "tokens": budget.exp1["tokens"] + budget.exp5["tokens"],
        "cny": format(
            Decimal(str(budget.exp1["cny"])) + Decimal(str(budget.exp5["cny"])),
            "f",
        ),
    }


def test_results_first_paid_restore_plan_only_module_cli_preserves_snapshot_type_identity(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "paid-output"
    planning_root = tmp_path / "paid-planning"
    corrected_bundle_root = authority.bundle_root
    network_marker = tmp_path / "network-tripwire-triggered.txt"
    hook_root = tmp_path / "network-tripwire-hook"
    hook_root.mkdir()
    (hook_root / "sitecustomize.py").write_text(
        """import os
import socket
from pathlib import Path

_marker = Path(os.environ["TOKENSHARE_TEST_NETWORK_MARKER"])
_socket_type = socket.socket

def _blocked(*_args, **_kwargs):
    _marker.write_text("network attempted", encoding="utf-8")
    raise AssertionError("network tripwire triggered")

class _GuardedSocket(_socket_type):
    def connect(self, *_args, **_kwargs):
        return _blocked()

    def connect_ex(self, *_args, **_kwargs):
        return _blocked()

socket.socket = _GuardedSocket
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
""",
        encoding="utf-8",
    )
    environment = {
        name: os.environ[name]
        for name in (
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "WINDIR",
        )
        if name in os.environ
    }
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                (
                    str(hook_root),
                    str(Path(pipeline.__file__).resolve().parents[2]),
                )
            ),
            "TOKENSHARE_TEST_NETWORK_MARKER": str(network_marker),
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tokenshare.experiments.run_paper_pipeline",
            "run-results-first",
            "--selection",
            "representative",
            "--output-root",
            str(output_root),
            "--planning-artifact-root",
            str(planning_root),
            "--plan-bundle-root",
            str(corrected_bundle_root),
            "--frozen-closure-snapshot-root",
            str(authority.output_root),
            "--frozen-prepared-inventory-root",
            str(inventory_root),
            "--expected-preflight",
            str(preflight_path),
            "--expected-provider-budget-digest",
            authority.provider_budget_authority.authority_digest,
            "--new-run",
            "--plan-only",
        ],
        cwd=Path(pipeline.__file__).resolve().parents[3],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "ready"
    assert result["provider_calls"] == 0
    assert not network_marker.exists()
    assert not output_root.exists()
    assert planning_root.is_dir()
    assert corrected_bundle_root.is_dir()


@pytest.mark.parametrize(
    "drift",
    (
        "missing_grouped_flag",
        "missing_path",
        "legacy_v1",
        "selection",
        "snapshot",
        "coverage",
        "budget",
        "projection",
        "hard_limits",
        "inventory_manifest",
        "bundle",
        "provider_budget",
    ),
)
def test_results_first_paid_restore_drift_fails_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    drift: str,
) -> None:
    from tokenshare.experiments.paper_formal_plan import (
        FORMAL_PREPARED_INVENTORY_MANIFEST_NAME,
    )
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_response_bank import (
        ACQUISITION_PLAN_BUNDLE_FILENAME,
    )

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "paid-output"
    planning_root = tmp_path / "paid-planning"
    arguments = [
        "run-results-first",
        "--selection",
        "representative",
        "--output-root",
        str(output_root),
        "--planning-artifact-root",
        str(planning_root),
        "--plan-bundle-root",
        str(authority.bundle_root),
        "--frozen-closure-snapshot-root",
        str(authority.output_root),
        "--frozen-prepared-inventory-root",
        str(inventory_root),
        "--expected-preflight",
        str(preflight_path),
        "--expected-provider-budget-digest",
        authority.provider_budget_authority.authority_digest,
        "--new-run",
        "--plan-only",
    ]
    if drift == "missing_grouped_flag":
        index = arguments.index("--expected-preflight")
        del arguments[index : index + 2]
    elif drift == "missing_path":
        arguments[arguments.index(str(preflight_path))] = str(
            tmp_path / "missing-preflight.json"
        )
    elif drift == "legacy_v1":
        snapshot_root = authority.output_root / "closure-replay-snapshot"
        (snapshot_root / pipeline._PAID_RESTORE_BINDING_REF_NAME).unlink()
        (snapshot_root / pipeline._PAID_RESTORE_BINDING_NAME).unlink()
    elif drift == "selection":
        arguments[arguments.index("representative")] = "full"
    elif drift in {"snapshot", "coverage", "budget", "projection", "hard_limits"}:
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        if drift == "snapshot":
            preflight["source"]["full_snapshot_digest"] = "sha256:" + "0" * 64
        elif drift == "coverage":
            preflight["source"]["coverage_digest"] = "sha256:" + "1" * 64
        elif drift == "budget":
            preflight["source"]["full_budget_digest"] = "sha256:" + "2" * 64
        elif drift == "projection":
            preflight["projection"]["projection_digest"] = "sha256:" + "3" * 64
        else:
            preflight["projection"]["hard_limits"] = {"provider_calls": 999}
        preflight.pop("report_digest")
        preflight["report_digest"] = digest_json(preflight)
        preflight_path.write_text(
            json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif drift == "inventory_manifest":
        manifest_path = inventory_root / FORMAL_PREPARED_INVENTORY_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["records_size_bytes"] += 1
        manifest.pop("manifest_digest")
        manifest["manifest_digest"] = digest_json(manifest)
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif drift == "bundle":
        bundle_path = authority.bundle_root / ACQUISITION_PLAN_BUNDLE_FILENAME
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["bundle_digest"] = "sha256:" + "4" * 64
        bundle_path.write_text(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        budget_path = planning_root / "results_first_provider_budget.v1.json"
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
        budget["authority_digest"] = "sha256:" + "5" * 64
        budget_path.write_text(
            json.dumps(budget, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    bomb_calls = _install_paid_restore_bombs(monkeypatch)

    exit_code = pipeline.main(arguments)

    result = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert result["status"] == "blocked"
    assert bomb_calls == []
    assert not output_root.exists()
    assert planning_root.is_dir()


@pytest.mark.parametrize(
    "mutation",
    (
        "false",
        "float",
        "string",
        "numeric_cost",
        "unknown",
        "missing",
        "prepared_body_type",
    ),
)
def test_paid_restore_bundle_reader_rejects_parser_normalization(
    tmp_path: Path,
    mutation: str,
) -> None:
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_response_bank import (
        ACQUISITION_PLAN_BUNDLE_FILENAME,
        _load_acquisition_plan_bundle_raw,
    )

    authority = _closure_snapshot_authority(tmp_path)
    path = authority.bundle_root / ACQUISITION_PLAN_BUNDLE_FILENAME
    value = authority.bundle.to_dict()
    request = value["acquisition_requests"][0]
    if mutation == "false":
        request["api_key_env"] = False
    elif mutation == "float":
        request["timeout_seconds"] = 1.0
    elif mutation == "string":
        request["timeout_seconds"] = "1"
    elif mutation == "numeric_cost":
        request["cost_upper_bound"] = 1.25
    elif mutation == "unknown":
        value["unknown"] = "drift"
    elif mutation == "missing":
        request.pop("provider_family")
    else:
        estimated = request["prepared_request"]["estimated_prompt_tokens"]
        request["prepared_request"]["estimated_prompt_tokens"] = float(estimated)
    value.pop("bundle_digest")
    normalized = json.loads(json.dumps(value))
    normalized_request = normalized["acquisition_requests"][0]
    if mutation == "false":
        normalized_request["api_key_env"] = "False"
    elif mutation in {"float", "string"}:
        normalized_request["timeout_seconds"] = 1
    elif mutation == "numeric_cost":
        normalized_request["cost_upper_bound"] = "1.25"
    value["bundle_digest"] = digest_json(normalized)
    path.write_bytes(
        pipeline._closure_snapshot_json_bytes(value)
    )

    if mutation in {
        "false",
        "float",
        "string",
        "numeric_cost",
        "prepared_body_type",
    }:
        raw_bundle = _load_acquisition_plan_bundle_raw(authority.bundle_root)
        assert raw_bundle.bundle_digest == value["bundle_digest"]
        if mutation != "prepared_body_type":
            assert pipeline._closure_snapshot_json_bytes(
                raw_bundle.to_dict()
            ) != pipeline._closure_snapshot_json_bytes(value)
    else:
        with pytest.raises((TypeError, ValueError)):
            _load_acquisition_plan_bundle_raw(authority.bundle_root)

    with pytest.raises((TypeError, ValueError)):
        pipeline._load_paid_restore_acquisition_bundle_exact(
            authority.bundle_root
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown",
        "secret",
        "tripwire_float",
        "tripwire_nonzero",
        "tripwire_missing",
        "source_changed",
        "api_unknown",
        "api_invalid",
        "condition_guess",
        "condition_count",
        "root_count_float",
        "experiment_count",
        "experiment_self_consistent",
    ),
)
def test_paid_restore_preflight_rejects_resigned_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority = _production_projectable_paid_authority(tmp_path)
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root
    )
    value = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=ref["snapshot_digest"],
    )
    if mutation == "unknown":
        value["unknown"] = "drift"
    elif mutation == "secret":
        value["api_key"] = "must-not-be-accepted"
    elif mutation == "tripwire_float":
        value["tripwires"]["provider_calls"] = 0.0
    elif mutation == "tripwire_nonzero":
        value["tripwires"]["provider_calls"] = 1
    elif mutation == "tripwire_missing":
        value["tripwires"].pop("provider_calls")
    elif mutation == "source_changed":
        value["source_unchanged"] = False
    elif mutation == "api_unknown":
        value["api_key_status"]["OTHER_API_KEY"] = "UNSET"
    elif mutation == "api_invalid":
        value["api_key_status"]["DEEPSEEK_API_KEY"] = "MISSING"
    elif mutation == "condition_guess":
        value["condition_projections"][0]["token_upper_bound"] = 1
    elif mutation == "condition_count":
        value["condition_projections"][0][
            "planned_root_run_count"
        ] += 1
    elif mutation == "root_count_float":
        experiment_id = pipeline._REPRESENTATIVE_EXPERIMENT_IDS[0]
        value["root_counts_by_experiment"][experiment_id] = 1.0
    elif mutation == "experiment_count":
        value["experiment_projections"][0]["projection"][
            "condition_count"
        ] += 1
    else:
        projection = value["experiment_projections"][0]["projection"]
        projection["cost_upper_bound"] += 1.0
        projection["hard_limits"]["max_cost_estimate"] = projection[
            "cost_upper_bound"
        ]
        projection.pop("projection_digest")
        projection_body = dict(projection)
        projection_body.pop("time_upper_bound")
        projection_body.pop("time_missing_reason")
        projection["projection_digest"] = digest_json(projection_body)
    value.pop("report_digest")
    value["report_digest"] = digest_json(value)

    with pytest.raises((TypeError, ValueError)):
        pipeline._validate_results_first_paid_preflight(
            value,
            snapshot=snapshot,
            selection="representative",
            frozen_snapshot_root=authority.output_root,
        )


def test_paid_restore_preflight_accepts_complete_production_schema(
    tmp_path: Path,
) -> None:
    authority = _production_projectable_paid_authority(tmp_path)
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root
    )
    value = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=ref["snapshot_digest"],
    )

    assert (
        pipeline._PAID_RESTORE_BUDGET_PROJECTOR
        is pipeline._paid_restore_project_paper_execution_budget
    )
    assert pipeline._validate_results_first_paid_preflight(
        value,
        snapshot=snapshot,
        selection="representative",
        frozen_snapshot_root=authority.output_root,
    ) == value["report_digest"]


def test_paid_restore_preflight_projector_spy_delegates_default_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_synthetic_paid_projector(monkeypatch)
    authority = _production_projectable_paid_authority(tmp_path)
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root
    )
    value = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=ref["snapshot_digest"],
    )

    assert pipeline._validate_results_first_paid_preflight(
        value,
        snapshot=snapshot,
        selection="representative",
        frozen_snapshot_root=authority.output_root,
    ) == value["report_digest"]
    assert tuple(
        coverage.conditions[0].experiment_id
        for _snapshot, _budget, coverage in calls
    ) == pipeline._REPRESENTATIVE_EXPERIMENT_IDS
    assert all(
        source_snapshot is snapshot.coverage.source_snapshot
        and source_budget is snapshot.full_budget
        and coverage.roots
        == tuple(
            root
            for root in snapshot.coverage.roots
            if root.condition.experiment_id
            == coverage.conditions[0].experiment_id
        )
        for source_snapshot, source_budget, coverage in calls
    )


@pytest.mark.parametrize(
    "repeat_drift",
    (None, "wrong", "missing", "duplicate"),
)
def test_paid_restore_preflight_full_repeat_inventory_is_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repeat_drift: str | None,
) -> None:
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority = _small_full_paid_restore_authority(tmp_path)
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root
    )
    value = _synthetic_paid_restore_preflight(
        authority,
        snapshot_digest=ref["snapshot_digest"],
    )
    assert value["selection"]["repeat_ids"] == [0]
    if repeat_drift == "wrong":
        value["selection"]["repeat_ids"] = [1]
    elif repeat_drift == "missing":
        value["selection"]["repeat_ids"] = []
    elif repeat_drift == "duplicate":
        value["selection"]["repeat_ids"] = [0, 0]
    if repeat_drift is not None:
        value.pop("report_digest")
        value["report_digest"] = digest_json(value)

    if repeat_drift is None:
        assert pipeline._validate_results_first_paid_preflight(
            value,
            snapshot=snapshot,
            selection="full",
            frozen_snapshot_root=authority.output_root,
        ) == value["report_digest"]
    else:
        with pytest.raises(ValueError, match="selection"):
            pipeline._validate_results_first_paid_preflight(
                value,
                snapshot=snapshot,
                selection="full",
                frozen_snapshot_root=authority.output_root,
            )


@pytest.mark.parametrize("selection", ("representative", "full"))
def test_paid_restore_allow_provider_reaches_service_with_loaded_limits_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    selection: str,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    source_authority = (
        _small_full_paid_restore_authority(tmp_path)
        if selection == "full"
        else _production_projectable_paid_authority(tmp_path)
    )
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(
            tmp_path,
            authority=source_authority,
        )
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_paid_plan_only_authority_v2",
        lambda **_kwargs: pytest.fail(
            "allow-provider must not load a plan-only certificate"
        ),
        raising=False,
    )
    bomb_calls = _install_paid_restore_bombs(monkeypatch)
    output_root = tmp_path / "paid-output"
    planning_root = tmp_path / "paid-planning"
    captured = []

    def capture_before_provider(request):
        service_input = request._service_input
        assert type(service_input) is pipeline.ResultsFirstServiceInput
        assert service_input.allow_provider_calls is True
        assert service_input.plan_only is False
        assert service_input.selection == selection
        restored = service_input.authority
        assert type(restored) is pipeline.ResultsFirstExecutionAuthority
        assert restored.execution_budget_projection.to_dict() == (
            authority.execution_budget_projection.to_dict()
        )
        assert dict(restored.hard_limits) == dict(authority.hard_limits)
        assert restored.full_prepared_inventory == authority.full_prepared_inventory
        assert not output_root.exists()
        assert planning_root.is_dir()
        captured.append(restored)
        return {
            "status": "ready",
            "provider_calls": 0,
            "source_snapshot_digest": restored.full_snapshot.snapshot_digest,
            "coverage_digest": restored.coverage.coverage_digest,
        }

    monkeypatch.setitem(
        pipeline._AUTHORITATIVE_SERVICE_ADAPTERS,
        "run-results-first",
        capture_before_provider,
    )

    exit_code = pipeline.main(
        [
            "run-results-first",
            "--selection",
            selection,
            "--output-root",
            str(output_root),
            "--planning-artifact-root",
            str(planning_root),
            "--plan-bundle-root",
            str(authority.bundle_root),
            "--frozen-closure-snapshot-root",
            str(authority.output_root),
            "--frozen-prepared-inventory-root",
            str(inventory_root),
            "--expected-preflight",
            str(preflight_path),
            "--expected-provider-budget-digest",
            authority.provider_budget_authority.authority_digest,
            "--new-run",
            "--allow-provider-calls",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0, result
    assert result["status"] == "ready"
    assert len(captured) == 1
    assert bomb_calls == []
    assert not output_root.exists()
    assert planning_root.is_dir()


@pytest.mark.parametrize("selection", ("representative", "full"))
def test_paid_restore_plan_only_factory_uses_fast_typed_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    from tokenshare.experiments import paper_formal_plan, paper_response_bank

    _install_synthetic_paid_projector(monkeypatch)
    source_authority = (
        _small_full_paid_restore_authority(tmp_path)
        if selection == "full"
        else _production_projectable_paid_authority(tmp_path)
    )
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(
            tmp_path,
            authority=source_authority,
        )
    )
    forbidden_calls: list[str] = []
    stream_calls = 0
    original_stream_verify = pipeline._paid_restore_verify_inventory_files_once

    def bomb(label: str):
        def reject(*_args, **_kwargs):
            forbidden_calls.append(label)
            raise AssertionError(f"plan-only invoked forbidden {label}")

        return reject

    def count_stream_verify(**kwargs):
        nonlocal stream_calls
        stream_calls += 1
        return original_stream_verify(**kwargs)

    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        bomb("full inventory parse"),
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        bomb("acquisition prepare"),
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        bomb("acquisition materialize"),
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        bomb("acquisition transport"),
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        bomb("Exp5 transport"),
    )
    monkeypatch.setattr(
        pipeline,
        "_paid_restore_verify_inventory_files_once",
        count_stream_verify,
    )
    monkeypatch.setattr(
        pipeline,
        "_atomic_replace_bytes",
        bomb("atomic replace"),
    )
    monkeypatch.setattr(
        pipeline,
        "_atomic_write_once_bytes",
        bomb("atomic write once"),
    )
    output_root = tmp_path / "plan-only-output"
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={
            "selection": selection,
            "planning_artifact_root": str(tmp_path / "paid-planning"),
            "plan_bundle_root": str(authority.bundle_root),
            "frozen_closure_snapshot_root": str(authority.output_root),
            "frozen_prepared_inventory_root": str(inventory_root),
            "expected_preflight": str(preflight_path),
            "expected_provider_budget_digest": (
                authority.provider_budget_authority.authority_digest
            ),
            "plan_only": True,
            "allow_provider_calls": False,
            "resume": False,
        },
    )

    service_input = pipeline._results_first_service_input_from_cli_authority(request)

    assert type(service_input.authority) is pipeline.ResultsFirstPaidPlanOnlyAuthority
    assert service_input.authority.selection == selection
    assert service_input.authority.provider_budget_authority == (
        authority.provider_budget_authority
    )
    assert service_input.authority.condition_count == authority.coverage.condition_count
    assert service_input.authority.root_run_count == authority.coverage.root_run_count
    assert stream_calls == 1
    assert forbidden_calls == []
    assert not output_root.exists()


def _load_synthetic_paid_plan_only_certificate(
    *,
    authority,
    inventory_root: Path,
    preflight_path: Path,
    output_root: Path,
    selection: str = "representative",
    expected_provider_budget_digest: str | None = None,
):
    return pipeline.load_results_first_paid_plan_only_authority_v2(
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=preflight_path,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=authority.bundle_root.parent / "paid-planning",
        output_root=output_root,
        selection=selection,
        resume=False,
        expected_provider_budget_digest=(
            expected_provider_budget_digest
            or authority.provider_budget_authority.authority_digest
        ),
    )


def _stale_synthetic_paid_restore_source_inventory(
    *,
    authority,
    preflight_path: Path,
) -> dict[str, object]:
    """模拟由旧 production source bytes 正式签发的 immutable preflight。"""

    from tokenshare.experiments.paper_models import digest_json

    current = json.loads(preflight_path.read_text(encoding="utf-8"))
    old = json.loads(json.dumps(current))
    historical_only_names = {
        "paper_exp1_trace_reuse.py",
        "response_bank.py",
    }
    historical_new_paths = tuple(
        raw_path
        for raw_path in old["production_source_files"]
        if Path(raw_path).name in historical_only_names
    )
    assert {Path(raw_path).name for raw_path in historical_new_paths} == (
        historical_only_names
    )
    for raw_path in historical_new_paths:
        del old["production_source_files"][raw_path]
    old.pop("report_digest")
    old["report_digest"] = digest_json(old)
    preflight_path.write_bytes(pipeline._closure_snapshot_json_bytes(old))

    closure_root = authority.output_root / pipeline._CLOSURE_REPLAY_DIRECTORY
    binding_path = closure_root / pipeline._PAID_RESTORE_BINDING_NAME
    ref_path = closure_root / pipeline._PAID_RESTORE_BINDING_REF_NAME
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding.pop("binding_digest")
    binding["preflight_report_digest"] = old["report_digest"]
    binding["binding_digest"] = digest_json(binding)
    binding_path.write_bytes(pipeline._closure_snapshot_json_bytes(binding))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    ref.pop("ref_digest")
    ref["binding_digest"] = binding["binding_digest"]
    ref["preflight_report_digest"] = old["report_digest"]
    ref["ref_digest"] = digest_json(ref)
    ref_path.write_bytes(pipeline._closure_snapshot_json_bytes(ref))
    return current


def _persist_synthetic_paid_source_rebind(
    *,
    authority,
    inventory_root: Path,
    old_preflight_path: Path,
    fresh_preflight: Mapping[str, object],
    source_rebind_root: Path,
):
    return pipeline.persist_results_first_paid_source_rebind_v1(
        source_rebind_root=source_rebind_root,
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        old_preflight=old_preflight_path,
        fresh_preflight=fresh_preflight,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=authority.bundle_root.parent / "paid-planning",
        selection="representative",
        expected_provider_budget_digest=(
            authority.provider_budget_authority.authority_digest
        ),
    )


def test_paid_source_rebind_refreshes_only_source_inventory_and_fast_plan_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_plan, paper_response_bank

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    closure_root = authority.output_root / pipeline._CLOSURE_REPLAY_DIRECTORY
    historical_paths = (
        old_preflight_path,
        closure_root / pipeline._PAID_RESTORE_BINDING_NAME,
        closure_root / pipeline._PAID_RESTORE_BINDING_REF_NAME,
    )
    historical_bytes = {path: path.read_bytes() for path in historical_paths}
    historical_preflight = json.loads(old_preflight_path.read_text(encoding="utf-8"))
    assert len(historical_preflight["production_source_files"]) == 6
    assert len(fresh_preflight["production_source_files"]) == 8
    output_root = tmp_path / "plan-only-output"
    with pytest.raises(ValueError, match="production source inventory"):
        _load_synthetic_paid_plan_only_certificate(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=old_preflight_path,
            output_root=output_root,
        )

    writes: list[str] = []
    original_write = pipeline._atomic_write_once_bytes

    def track_write(path: Path, value: bytes) -> None:
        writes.append(path.name)
        original_write(path, value)

    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", track_write)
    rebind_root = tmp_path / "fresh-source-rebind"
    evidence = _persist_synthetic_paid_source_rebind(
        authority=authority,
        inventory_root=inventory_root,
        old_preflight_path=old_preflight_path,
        fresh_preflight=fresh_preflight,
        source_rebind_root=rebind_root,
    )
    assert evidence["schema_version"] == (
        "tokenshare.results_first_paid_source_rebind_ref.v1"
    )
    assert writes == [
        pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME,
        pipeline._PAID_SOURCE_REBIND_BINDING_NAME,
        pipeline._PAID_SOURCE_REBIND_REF_NAME,
    ]
    assert {path: path.read_bytes() for path in historical_paths} == historical_bytes
    loaded_preflight, _loaded_binding, _loaded_ref = (
        pipeline._load_results_first_paid_source_rebind_v1(rebind_root)
    )
    assert len(loaded_preflight["production_source_files"]) == 8

    def bomb(*_args, **_kwargs):
        raise AssertionError("source rebind plan-only invoked full inventory rebuild")

    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        bomb,
    )
    certificate = pipeline.load_results_first_paid_plan_only_authority_v2(
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=old_preflight_path,
        source_rebind_root=rebind_root,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=authority.bundle_root.parent / "paid-planning",
        output_root=output_root,
        selection="representative",
        resume=False,
        expected_provider_budget_digest=(
            authority.provider_budget_authority.authority_digest
        ),
    )
    assert type(certificate) is pipeline.ResultsFirstPaidPlanOnlyAuthority
    assert certificate.preflight_report_digest == fresh_preflight["report_digest"]
    _loaded_preflight, loaded_rebind, loaded_rebind_ref = (
        pipeline._load_results_first_paid_source_rebind_v1(rebind_root)
    )
    assert certificate.binding_digest == loaded_rebind["binding_digest"]
    assert certificate.binding_ref_digest == loaded_rebind_ref["ref_digest"]
    assert certificate.provider_calls_made == 0
    assert not output_root.exists()


@pytest.mark.parametrize(
    "source_drift",
    ("missing_response_bank", "wrong_eighth", "sha", "size"),
)
def test_paid_source_rebind_rejects_current_eight_source_drift_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_drift: str,
) -> None:
    from tokenshare.experiments import paper_formal_plan, paper_response_bank
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    source_files = fresh_preflight["production_source_files"]
    response_bank_path = next(
        raw_path
        for raw_path in source_files
        if Path(raw_path).name == "response_bank.py"
    )
    if source_drift == "missing_response_bank":
        del source_files[response_bank_path]
    elif source_drift == "wrong_eighth":
        del source_files[response_bank_path]
        wrong_path = (
            Path(pipeline.__file__).resolve().with_name("paper_catalog.py")
        )
        payload = wrong_path.read_bytes()
        source_files[wrong_path.as_posix()] = {
            "sha256": f"sha256:{sha256(payload).hexdigest()}",
            "size_bytes": len(payload),
        }
    elif source_drift == "sha":
        source_files[response_bank_path]["sha256"] = "sha256:" + "0" * 64
    else:
        source_files[response_bank_path]["size_bytes"] += 1
    fresh_preflight.pop("report_digest")
    fresh_preflight["report_digest"] = digest_json(fresh_preflight)

    forbidden_calls: list[str] = []

    def bomb(name: str):
        def reject(*_args, **_kwargs):
            forbidden_calls.append(name)
            raise AssertionError(f"source rebind invoked {name}")

        return reject

    monkeypatch.setattr(
        paper_formal_plan,
        "freeze_paper_formal_prepared_request_inventory",
        bomb("full prepared inventory builder"),
    )
    monkeypatch.setattr(
        paper_formal_plan,
        "freeze_paper_formal_plan_snapshot",
        bomb("full snapshot builder"),
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        bomb("full acquisition authority builder"),
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        bomb("full acquisition plan builder"),
    )
    monkeypatch.setattr(socket, "create_connection", bomb("network"))
    monkeypatch.setattr(urllib.request, "urlopen", bomb("provider"))
    writes: list[str] = []

    def bomb_write(path: Path, _value: bytes) -> None:
        writes.append(path.name)
        raise AssertionError("current source drift wrote before validation")

    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", bomb_write)
    rebind_root = tmp_path / "rejected-current-source-rebind"
    with pytest.raises(ValueError, match="inventory|immutable|file set|drift"):
        _persist_synthetic_paid_source_rebind(
            authority=authority,
            inventory_root=inventory_root,
            old_preflight_path=old_preflight_path,
            fresh_preflight=fresh_preflight,
            source_rebind_root=rebind_root,
        )
    assert writes == []
    assert forbidden_calls == []
    assert not rebind_root.exists()


@pytest.mark.parametrize(
    "published_names",
    (
        (),
        ("paid_source_rebind_preflight.v1.json",),
        ("results_first_paid_source_rebind.v1.json",),
        ("results_first_paid_source_rebind_ref.v1.json",),
        (
            "paid_source_rebind_preflight.v1.json",
            "results_first_paid_source_rebind.v1.json",
        ),
        (
            "paid_source_rebind_preflight.v1.json",
            "results_first_paid_source_rebind_ref.v1.json",
        ),
        (
            "results_first_paid_source_rebind.v1.json",
            "results_first_paid_source_rebind_ref.v1.json",
        ),
    ),
)
def test_paid_source_rebind_partial_publication_fails_closed(
    tmp_path: Path,
    published_names: tuple[str, ...],
) -> None:
    root = tmp_path / "partial-rebind"
    if published_names:
        root.mkdir()
    for name in published_names:
        (root / name).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete|missing"):
        pipeline._load_results_first_paid_source_rebind_v1(root)


@pytest.mark.parametrize("drift", ("body", "source_toctou", "budget"))
def test_paid_source_rebind_validates_everything_before_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    expected_budget_digest = authority.provider_budget_authority.authority_digest
    if drift == "body":
        fresh_preflight["source_unchanged"] = False
        fresh_preflight.pop("report_digest")
        fresh_preflight["report_digest"] = digest_json(fresh_preflight)
    elif drift == "budget":
        expected_budget_digest = "sha256:" + "f" * 64
    else:
        original_inventory = pipeline._paid_restore_file_inventory
        source_reads = 0

        def fail_second_source_read(value, *, label, expected_paths=None):
            nonlocal source_reads
            result = original_inventory(
                value,
                label=label,
                expected_paths=expected_paths,
            )
            if label == "paid restore production source inventory":
                source_reads += 1
                if source_reads == 2:
                    raise ValueError("production source TOCTOU drift")
            return result

        monkeypatch.setattr(
            pipeline,
            "_paid_restore_file_inventory",
            fail_second_source_read,
        )

    writes: list[str] = []

    def bomb_write(path: Path, _value: bytes) -> None:
        writes.append(path.name)
        raise AssertionError("source rebind wrote before complete validation")

    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", bomb_write)
    with pytest.raises((TypeError, ValueError), match="drift|TOCTOU|immutable"):
        pipeline.persist_results_first_paid_source_rebind_v1(
            source_rebind_root=tmp_path / "fresh-source-rebind",
            frozen_snapshot_root=authority.output_root,
            frozen_prepared_inventory_root=inventory_root,
            old_preflight=old_preflight_path,
            fresh_preflight=fresh_preflight,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=authority.bundle_root.parent / "paid-planning",
            selection="representative",
            expected_provider_budget_digest=expected_budget_digest,
        )
    assert writes == []
    assert not (tmp_path / "fresh-source-rebind").exists()


def test_paid_source_rebind_rejects_warm_evidence_before_any_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warm_calls = 0

    def warm_bomb(**_kwargs):
        nonlocal warm_calls
        warm_calls += 1
        raise AssertionError("warm loader ran before rebind/warm exclusion")

    monkeypatch.setattr(
        pipeline,
        "_load_results_first_paid_execution_authority_v2_warm",
        warm_bomb,
    )
    with pytest.raises(ValueError, match="warm.*source rebind|source rebind.*warm"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=tmp_path / "snapshot",
            frozen_prepared_inventory_root=tmp_path / "inventory",
            expected_preflight=tmp_path / "old-preflight.json",
            source_rebind_root=tmp_path / "rebind",
            plan_bundle_root=tmp_path / "bundle",
            planning_artifact_root=tmp_path / "planning",
            output_root=tmp_path / "output",
            selection="representative",
            resume=False,
            expected_provider_budget_digest="sha256:" + "a" * 64,
            warm_derivation_evidence=object(),
        )
    assert warm_calls == 0


def test_results_first_execution_authority_rejects_boolean_provider_call_count(
    tmp_path: Path,
) -> None:
    authority = _production_projectable_paid_authority(tmp_path)
    with pytest.raises(ValueError, match="provider"):
        replace(authority, provider_calls_made=False)


def test_paid_plan_only_authority_rejects_boolean_provider_call_count(
    tmp_path: Path,
) -> None:
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=tmp_path / "plan-only-output",
    )
    values = {
        name: getattr(certificate, name)
        for name in pipeline.ResultsFirstPaidPlanOnlyAuthority.__dataclass_fields__
        if name != "_mint_token"
    }
    values["provider_calls_made"] = False
    with pytest.raises(ValueError, match="provider"):
        pipeline._mint_results_first_paid_plan_only_authority(**values)


def test_paid_restore_warm_evidence_rejects_boolean_provider_call_count(
    tmp_path: Path,
) -> None:
    _authority, _inventory_root, _preflight_path, warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    values = {
        name: getattr(warm_evidence, name)
        for name in pipeline.PaidRestoreCanonicalDerivationEvidence.__dataclass_fields__
        if name != "_mint_token"
    }
    values["provider_calls_made"] = False
    with pytest.raises(ValueError, match="provider|authority drift"):
        pipeline._mint_paid_restore_canonical_derivation_evidence(**values)


@pytest.mark.parametrize("field_owner", ("binding", "ref"))
def test_paid_source_rebind_rejects_boolean_provider_call_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_owner: str,
) -> None:
    from hashlib import sha256
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    rebind_root = tmp_path / "fresh-source-rebind"
    _persist_synthetic_paid_source_rebind(
        authority=authority,
        inventory_root=inventory_root,
        old_preflight_path=old_preflight_path,
        fresh_preflight=fresh_preflight,
        source_rebind_root=rebind_root,
    )
    binding_path = rebind_root / pipeline._PAID_SOURCE_REBIND_BINDING_NAME
    ref_path = rebind_root / pipeline._PAID_SOURCE_REBIND_REF_NAME
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    if field_owner == "binding":
        binding.pop("binding_digest")
        binding["provider_calls_made"] = False
        binding["binding_digest"] = digest_json(binding)
        binding_bytes = pipeline._closure_snapshot_json_bytes(binding)
        binding_path.write_bytes(binding_bytes)
        ref.pop("ref_digest")
        ref["binding_digest"] = binding["binding_digest"]
        ref["binding_file_sha256"] = (
            "sha256:" + sha256(binding_bytes).hexdigest()
        )
        ref["binding_file_size_bytes"] = len(binding_bytes)
        ref["ref_digest"] = digest_json(ref)
    else:
        ref.pop("ref_digest")
        ref["provider_calls_made"] = False
        ref["ref_digest"] = digest_json(ref)
    ref_path.write_bytes(pipeline._closure_snapshot_json_bytes(ref))

    with pytest.raises(ValueError, match="provider|authority drift"):
        pipeline._load_results_first_paid_source_rebind_v1(rebind_root)


def test_paid_source_rebind_allow_provider_still_uses_cold_inventory_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_plan

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    rebind_root = tmp_path / "fresh-source-rebind"
    _persist_synthetic_paid_source_rebind(
        authority=authority,
        inventory_root=inventory_root,
        old_preflight_path=old_preflight_path,
        fresh_preflight=fresh_preflight,
        source_rebind_root=rebind_root,
    )

    def cold_parse(*_args, **_kwargs):
        raise RuntimeError("COLD_FULL_INVENTORY_PARSE_REACHED")

    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        cold_parse,
    )
    with pytest.raises(RuntimeError, match="COLD_FULL_INVENTORY_PARSE_REACHED"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=authority.output_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=old_preflight_path,
            source_rebind_root=rebind_root,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=authority.bundle_root.parent / "paid-planning",
            output_root=tmp_path / "paid-output",
            selection="representative",
            resume=False,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
        )


def test_paid_source_rebind_cold_authority_pins_identity_and_rejects_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hashlib import sha256
    from tokenshare.experiments.paper_models import digest_json

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, old_preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    fresh_preflight = _stale_synthetic_paid_restore_source_inventory(
        authority=authority,
        preflight_path=old_preflight_path,
    )
    rebind_root = tmp_path / "fresh-source-rebind"
    _persist_synthetic_paid_source_rebind(
        authority=authority,
        inventory_root=inventory_root,
        old_preflight_path=old_preflight_path,
        fresh_preflight=fresh_preflight,
        source_rebind_root=rebind_root,
    )
    _preflight, rebind, rebind_ref = (
        pipeline._load_results_first_paid_source_rebind_v1(rebind_root)
    )
    with pytest.raises(ValueError, match="base/current preflight drift"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=authority.output_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=(
                rebind_root / pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME
            ),
            source_rebind_root=rebind_root,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=authority.bundle_root.parent / "paid-planning",
            output_root=tmp_path / "paid-output-current-as-base",
            selection="representative",
            resume=False,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
        )
    loaded = pipeline.load_results_first_paid_execution_authority_v2(
        frozen_snapshot_root=authority.output_root,
        frozen_prepared_inventory_root=inventory_root,
        expected_preflight=old_preflight_path,
        source_rebind_root=rebind_root,
        plan_bundle_root=authority.bundle_root,
        planning_artifact_root=authority.bundle_root.parent / "paid-planning",
        output_root=tmp_path / "paid-output",
        selection="representative",
        resume=False,
        expected_provider_budget_digest=(
            authority.provider_budget_authority.authority_digest
        ),
    )
    assert loaded.source_rebind_binding_digest == rebind["binding_digest"]
    assert loaded.source_rebind_ref_digest == rebind_ref["ref_digest"]

    binding_path = rebind_root / pipeline._PAID_SOURCE_REBIND_BINDING_NAME
    ref_path = rebind_root / pipeline._PAID_SOURCE_REBIND_REF_NAME
    drifted_binding = json.loads(binding_path.read_text(encoding="utf-8"))
    drifted_binding.pop("binding_digest")
    drifted_binding["provider_budget_authority_digest"] = "sha256:" + "f" * 64
    drifted_binding["binding_digest"] = digest_json(drifted_binding)
    binding_bytes = pipeline._closure_snapshot_json_bytes(drifted_binding)
    binding_path.write_bytes(binding_bytes)
    drifted_ref = json.loads(ref_path.read_text(encoding="utf-8"))
    drifted_ref.pop("ref_digest")
    drifted_ref["binding_digest"] = drifted_binding["binding_digest"]
    drifted_ref["binding_file_sha256"] = (
        "sha256:" + sha256(binding_bytes).hexdigest()
    )
    drifted_ref["binding_file_size_bytes"] = len(binding_bytes)
    drifted_ref["ref_digest"] = digest_json(drifted_ref)
    ref_path.write_bytes(pipeline._closure_snapshot_json_bytes(drifted_ref))

    with pytest.raises(ValueError, match="source rebind authority drift"):
        pipeline.load_results_first_paid_execution_authority_v2(
            frozen_snapshot_root=authority.output_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=old_preflight_path,
            source_rebind_root=rebind_root,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=authority.bundle_root.parent / "paid-planning",
            output_root=tmp_path / "paid-output-drift",
            selection="representative",
            resume=False,
            expected_provider_budget_digest=(
                authority.provider_budget_authority.authority_digest
            ),
        )


def test_paid_source_rebind_cli_routes_optional_root_to_plan_only_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    roots = {
        name: tmp_path / name
        for name in ("snapshot", "inventory", "bundle", "planning", "rebind")
    }
    for root in roots.values():
        root.mkdir()
    preflight = tmp_path / "old-preflight.json"
    preflight.write_text("{}\n", encoding="utf-8")
    output_root = tmp_path / "output"
    captured: dict[str, object] = {}
    sentinel = object()

    def capture_loader(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        pipeline,
        "load_results_first_paid_plan_only_authority_v2",
        capture_loader,
    )
    monkeypatch.setitem(
        pipeline._AUTHORITATIVE_SERVICE_ADAPTERS,
        "run-results-first",
        lambda request: {
            "status": "ready",
            "provider_calls": 0,
            "authority_identity": request._service_input.authority is sentinel,
        },
    )
    exit_code = pipeline.main(
        [
            "run-results-first",
            "--selection",
            "representative",
            "--output-root",
            str(output_root),
            "--planning-artifact-root",
            str(roots["planning"]),
            "--plan-bundle-root",
            str(roots["bundle"]),
            "--frozen-closure-snapshot-root",
            str(roots["snapshot"]),
            "--frozen-prepared-inventory-root",
            str(roots["inventory"]),
            "--expected-preflight",
            str(preflight),
            "--source-rebind-root",
            str(roots["rebind"]),
            "--expected-provider-budget-digest",
            "sha256:" + "a" * 64,
            "--new-run",
            "--plan-only",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0, result
    assert result["status"] == "ready"
    assert captured["source_rebind_root"] == roots["rebind"]
    assert not output_root.exists()


def test_paid_cli_routes_base_preflight_to_cold_loader_before_secret_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    roots = {
        name: tmp_path / name
        for name in (
            "snapshot",
            "inventory",
            "bundle",
            "planning",
            "rebind",
            "reference",
        )
    }
    for root in roots.values():
        root.mkdir()
    base_preflight = tmp_path / "base-preflight.json"
    base_preflight.write_text("{}\n", encoding="utf-8")
    current_preflight = (
        roots["rebind"] / pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME
    )
    current_preflight.write_text("{}\n", encoding="utf-8")
    key_config = tmp_path / "must-not-read-secret-config.json"
    key_config.write_text("{}\n", encoding="utf-8")
    captured: dict[str, object] = {}
    events: list[str] = []

    def capture_loader(**kwargs: object) -> object:
        captured.update(kwargs)
        events.append("cold_loader")
        return object()

    def stop_after_cold(*_args: object, **_kwargs: object) -> object:
        events.append("lock")
        raise ValueError("STOP_AFTER_COLD_AUTHORITY")

    def secret_bomb(*_args: object, **_kwargs: object) -> object:
        events.append("secret")
        raise AssertionError("paid key config was read before the cold gate")

    monkeypatch.setattr(
        pipeline,
        "_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER",
        capture_loader,
    )
    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        stop_after_cold,
    )
    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        secret_bomb,
    )
    exit_code = pipeline.main(
        [
            "run-results-first",
            "--selection",
            "representative",
            "--output-root",
            str(tmp_path / "output"),
            "--planning-artifact-root",
            str(roots["planning"]),
            "--plan-bundle-root",
            str(roots["bundle"]),
            "--frozen-closure-snapshot-root",
            str(roots["snapshot"]),
            "--frozen-prepared-inventory-root",
            str(roots["inventory"]),
            "--expected-preflight",
            str(base_preflight),
            "--source-rebind-root",
            str(roots["rebind"]),
            "--paid-reference-binding-root",
            str(roots["reference"]),
            "--expected-provider-budget-digest",
            "sha256:" + "a" * 64,
            "--paid-launch-key-config",
            str(key_config),
            "--paid-launch-attestation-root",
            str(tmp_path / "attestation"),
            "--new-run",
            "--allow-provider-calls",
        ]
    )
    rendered = json.loads(capsys.readouterr().out)
    assert exit_code == 3, rendered
    assert rendered["message"] == "STOP_AFTER_COLD_AUTHORITY"
    assert events == ["cold_loader", "lock"]
    assert Path(str(captured["expected_preflight"])).resolve() == (
        base_preflight.resolve()
    )
    assert Path(str(captured["source_rebind_root"])).resolve() == (
        roots["rebind"].resolve()
    )


@pytest.mark.parametrize(
    "drift",
    (
        "inventory_byte",
        "inventory_size",
        "inventory_count",
        "snapshot_descriptor",
        "snapshot_pickle",
        "binding_only",
        "ref_only",
        "preflight_bytes",
        "corrected_body",
        "corrected_source_plan",
        "selection",
        "provider_budget_digest",
    ),
)
def test_paid_restore_plan_only_certificate_fails_closed_on_authority_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from tokenshare.experiments import paper_formal_plan, paper_response_bank
    from tokenshare.experiments.paper_formal_plan import (
        FORMAL_PREPARED_INVENTORY_RECORDS_NAME,
    )
    from tokenshare.experiments.paper_response_bank import (
        ACQUISITION_PLAN_BUNDLE_FILENAME,
    )

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    closure_root = authority.output_root / pipeline._CLOSURE_REPLAY_DIRECTORY
    records_path = inventory_root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME
    bundle_path = authority.bundle_root / ACQUISITION_PLAN_BUNDLE_FILENAME
    expected_budget_digest = authority.provider_budget_authority.authority_digest
    selection = "representative"
    if drift == "inventory_byte":
        payload = bytearray(records_path.read_bytes())
        payload[0] = ord("[") if payload[0] != ord("[") else ord("{")
        records_path.write_bytes(bytes(payload))
    elif drift == "inventory_size":
        records_path.write_bytes(records_path.read_bytes() + b" ")
    elif drift == "inventory_count":
        first_line = records_path.read_bytes().splitlines(keepends=True)[0]
        records_path.write_bytes(records_path.read_bytes() + first_line)
    elif drift == "snapshot_descriptor":
        path = closure_root / pipeline._CLOSURE_REPLAY_DESCRIPTOR_NAME
        value = json.loads(path.read_text(encoding="utf-8"))
        value["snapshot_digest"] = "sha256:" + "0" * 64
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif drift == "snapshot_pickle":
        path = closure_root / pipeline._CLOSURE_REPLAY_PICKLE_NAME
        path.write_bytes(path.read_bytes() + b"0")
    elif drift == "binding_only":
        (closure_root / pipeline._PAID_RESTORE_BINDING_REF_NAME).unlink()
    elif drift == "ref_only":
        (closure_root / pipeline._PAID_RESTORE_BINDING_NAME).unlink()
    elif drift == "preflight_bytes":
        preflight_path.write_bytes(preflight_path.read_bytes() + b" ")
    elif drift == "corrected_body":
        bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
    elif drift == "corrected_source_plan":
        value = json.loads(bundle_path.read_text(encoding="utf-8"))
        value["semantic_inventory_plan"]["condition_refs"][0][
            "experiment_id"
        ] = "exp2_real_ai_scalability"
        bundle_path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif drift == "selection":
        selection = "full"
    else:
        expected_budget_digest = "sha256:" + "0" * 64

    def bomb(*_args, **_kwargs):
        raise AssertionError("plan-only drift path invoked a cold builder or writer")

    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        bomb,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        bomb,
    )
    monkeypatch.setattr(pipeline, "_atomic_replace_bytes", bomb)
    monkeypatch.setattr(pipeline, "_atomic_write_once_bytes", bomb)
    output_root = tmp_path / "plan-only-output"

    with pytest.raises((TypeError, ValueError)):
        _load_synthetic_paid_plan_only_certificate(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            output_root=output_root,
            selection=selection,
            expected_provider_budget_digest=expected_budget_digest,
        )

    assert not output_root.exists()


def test_paid_restore_plan_only_service_accepts_only_certificate_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments import paper_formal_runner

    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "plan-only-output"
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=output_root,
    )

    def bomb(*_args, **_kwargs):
        raise AssertionError("plan-only service constructed execution state")

    monkeypatch.setattr(
        pipeline,
        "_materialize_results_first_service_authority",
        bomb,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        bomb,
    )
    monkeypatch.setattr(pipeline, "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY", bomb)
    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", bomb)
    monkeypatch.setattr(pipeline, "_REPRESENTATIVE_SMOKE_EXECUTOR", bomb)
    monkeypatch.setattr(
        pipeline,
        "_RESULTS_FIRST_CLOSURE_SNAPSHOT_PERSISTER",
        bomb,
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "recompute_paper_formal_metrics_from_runner_inputs",
        bomb,
    )
    monkeypatch.setattr(paper_formal_runner, "_write_json", bomb)
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
    )
    service_input = pipeline.ResultsFirstServiceInput(
        authority=certificate,
        selection="representative",
        plan_only=True,
        allow_provider_calls=False,
        external_bank_root=None,
        resume=False,
    )

    result = pipeline._execute_results_first_service_input(
        request=request,
        value=service_input,
    )

    assert result["status"] == "ready"
    assert result["evidence_class"] == "results_first_plan_only_boundary"
    assert result["execution_performed"] is False
    assert result["protocol_calls"] == 0
    assert result["checker_calls"] == 0
    assert result["verifier_calls"] == 0
    assert result["metrics_calls"] == 0
    assert result["provider_calls"] == 0
    assert result["metrics_eligible"] is False
    assert result["publication_eligible"] is False
    assert result["fixed_denominator_validated"] is True
    assert result["provider_budget"] == (
        authority.provider_budget_authority.to_dict()
    )
    assert not output_root.exists()


def test_paid_restore_allow_provider_rejects_plan_only_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "plan-only-output"
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=output_root,
    )
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
    )

    with pytest.raises(TypeError, match="plan-only certificate"):
        pipeline._execute_results_first_service_input(
            request=request,
            value=pipeline.ResultsFirstServiceInput(
                authority=certificate,
                selection="representative",
                plan_only=False,
                allow_provider_calls=True,
                external_bank_root=None,
                resume=False,
            ),
        )

    correct_result = pipeline._execute_results_first_service_input(
        request=request,
        value=pipeline.ResultsFirstServiceInput(
            authority=certificate,
            selection="representative",
            plan_only=True,
            allow_provider_calls=False,
            external_bank_root=None,
            resume=False,
            scope_preparation_root=(
                root
                / "local"
                / "paid-representative-20260818-r31-exp1-exp3-exp5-preparation"
            ),
        ),
    )
    assert correct_result["status"] == "ready"
    assert not output_root.exists()


def test_paid_restore_plan_only_allow_provider_rejects_before_consuming_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "plan-only-output"
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=output_root,
    )
    request = SimpleNamespace(output_root=output_root)

    with pytest.raises(ValueError, match="cannot allow provider calls"):
        pipeline._execute_results_first_service_input(
            request=request,
            value=pipeline.ResultsFirstServiceInput(
                authority=certificate,
                selection="representative",
                plan_only=True,
                allow_provider_calls=True,
                external_bank_root=None,
                resume=False,
            ),
        )

    result = pipeline._execute_results_first_service_input(
        request=request,
        value=pipeline.ResultsFirstServiceInput(
            authority=certificate,
            selection="representative",
            plan_only=True,
            allow_provider_calls=False,
            external_bank_root=None,
            resume=False,
        ),
    )
    assert result["status"] == "ready"
    assert not output_root.exists()


def test_paid_restore_plan_only_certificate_is_unforgeable_and_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "plan-only-output"
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=output_root,
    )
    with pytest.raises(TypeError, match="loader mint"):
        pipeline.ResultsFirstPaidPlanOnlyAuthority()
    with pytest.raises(TypeError, match="loader mint"):
        replace(certificate)
    with pytest.raises(TypeError):
        copy.copy(certificate)
    with pytest.raises(TypeError):
        copy.deepcopy(certificate)
    with pytest.raises(TypeError):
        pickle.dumps(certificate)
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_formal_experiments",
        evidence_class="paper_eligible_results_first",
        profile=PROFILE,
        provider_authorization=_authorization(output_mode="new_run"),
        output_root=output_root,
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=DIGESTS["plan"],
        inventory_digest=DIGESTS["inventory"],
        budget_mode="bounded",
        serialized_arguments={},
    )
    service_input = pipeline.ResultsFirstServiceInput(
        authority=certificate,
        selection="representative",
        plan_only=True,
        allow_provider_calls=False,
        external_bank_root=None,
        resume=False,
    )
    assert pipeline._execute_results_first_service_input(
        request=request,
        value=service_input,
    )["status"] == "ready"
    with pytest.raises(TypeError, match="one-shot|registered"):
        pipeline._execute_results_first_service_input(
            request=request,
            value=service_input,
        )


def test_paid_restore_plan_only_failed_validation_consumes_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    output_root = tmp_path / "plan-only-output"
    certificate = _load_synthetic_paid_plan_only_certificate(
        authority=authority,
        inventory_root=inventory_root,
        preflight_path=preflight_path,
        output_root=output_root,
    )
    wrong_request = SimpleNamespace(output_root=tmp_path / "wrong-output")
    correct_request = SimpleNamespace(output_root=output_root)
    service_input = pipeline.ResultsFirstServiceInput(
        authority=certificate,
        selection="representative",
        plan_only=True,
        allow_provider_calls=False,
        external_bank_root=None,
        resume=False,
    )
    with pytest.raises(ValueError, match="mode drift"):
        pipeline._execute_results_first_service_input(
            request=wrong_request,
            value=service_input,
        )
    with pytest.raises(TypeError, match="one-shot|registered"):
        pipeline._execute_results_first_service_input(
            request=correct_request,
            value=service_input,
        )


@pytest.mark.parametrize("streamed_record_count", (0, 999))
def test_paid_restore_plan_only_certificate_rejects_inventory_denominator_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    streamed_record_count: int,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    original_verify = pipeline._paid_restore_verify_inventory_files_once

    def drift_count(**kwargs):
        manifest, digest, size, _count = original_verify(**kwargs)
        return manifest, digest, size, streamed_record_count

    monkeypatch.setattr(
        pipeline,
        "_paid_restore_verify_inventory_files_once",
        drift_count,
    )
    with pytest.raises(ValueError, match="fixed denominator"):
        _load_synthetic_paid_plan_only_certificate(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            output_root=tmp_path / "plan-only-output",
        )


@pytest.mark.parametrize("field", ("condition_count", "root_run_count"))
def test_paid_restore_plan_only_certificate_rejects_minted_denominator_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )
    original_mint = getattr(
        pipeline,
        "_mint_results_first_paid_plan_only_authority",
        None,
    )

    def drift_mint(**values):
        if original_mint is None:
            raise AssertionError("plan-only loader did not use its private mint")
        values[field] += 1
        return original_mint(**values)

    monkeypatch.setattr(
        pipeline,
        "_mint_results_first_paid_plan_only_authority",
        drift_mint,
        raising=False,
    )
    with pytest.raises(ValueError, match="frozen lineage|fixed denominator"):
        _load_synthetic_paid_plan_only_certificate(
            authority=authority,
            inventory_root=inventory_root,
            preflight_path=preflight_path,
            output_root=tmp_path / "plan-only-output",
        )


def test_paid_restore_plan_only_certificate_requires_expected_budget_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_paid_projector(monkeypatch)
    authority, inventory_root, preflight_path, _warm_evidence = (
        _persist_synthetic_paid_restore_fixture(tmp_path)
    )

    with pytest.raises(ValueError, match="provider budget digest is invalid"):
        pipeline.load_results_first_paid_plan_only_authority_v2(
            frozen_snapshot_root=authority.output_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=preflight_path,
            plan_bundle_root=authority.bundle_root,
            planning_artifact_root=tmp_path / "paid-planning",
            output_root=tmp_path / "plan-only-output",
            selection="representative",
            resume=False,
            expected_provider_budget_digest=None,
        )


def test_results_first_closure_replay_snapshot_round_trips_typed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
    from tokenshare.experiments.paper_budget import PaperExecutionBudgetProjection
    from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
    from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
    from tokenshare.experiments.paper_formal_plan import FormalExecutionCoverage
    from tokenshare.experiments.paper_models import PaperBudgetResult
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstReplaySemanticAuthority,
        SemanticInventoryPlan,
    )

    secret = "snapshot-secret-must-not-persist"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    authority = _closure_snapshot_authority(tmp_path)
    assert tuple(
        plan.experiment_id for plan in authority.full_dispatch_plans
    ) == (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp4_real_ai_protocol_ablation",
        "exp5_real_ai_model_endpoint_comparison",
    )
    suite_root = tmp_path / "suite"
    _write_closure_snapshot_suite(authority=authority, suite_root=suite_root)

    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
        suite_id="results_first_exp1_exp4_trace",
    )
    loaded = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root,
        expected_ref=ref,
        suite_root=suite_root,
    )

    assert type(loaded) is pipeline.ResultsFirstClosureReplaySnapshot
    assert all(
        type(plan) is PaperExperimentDispatchPlan
        for plan in loaded.full_dispatch_plans
    )
    assert type(loaded.catalog_manifest) is PaperInputCatalogManifest
    assert type(loaded.full_budget) is PaperBudgetResult
    assert type(loaded.ai_api_configs["deepseek"]) is AIAPIExecutorConfig
    assert type(loaded.coverage) is FormalExecutionCoverage
    assert type(loaded.semantic_inventory_plan) is SemanticInventoryPlan
    assert type(loaded.source_exp1_inventory_plan) is SemanticInventoryPlan
    assert (
        type(loaded.replay_semantic_authority)
        is ResultsFirstReplaySemanticAuthority
    )
    assert (
        loaded.replay_semantic_authority.semantic_inventory_plan
        is loaded.semantic_inventory_plan
    )
    assert all(
        ref["experiment_id"] == "exp1_real_ai_feasibility"
        for ref in loaded.source_exp1_inventory_plan.condition_refs
    )
    assert (
        type(loaded.execution_budget_projection)
        is PaperExecutionBudgetProjection
    )
    assert loaded.coverage_digest == authority.coverage.coverage_digest
    assert loaded.full_budget_digest == authority.full_budget.budget_digest
    assert loaded.hard_limits == authority.execution_budget_projection.hard_limits
    assert loaded.provider_calls_made == 0
    persisted = b"".join(
        path.read_bytes()
        for path in (authority.output_root / "closure-replay-snapshot").iterdir()
    )
    assert secret.encode("utf-8") not in persisted


def test_closure_snapshot_restarts_from_persisted_ref_only(tmp_path: Path) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    suite_root = tmp_path / "suite"
    _write_closure_snapshot_suite(authority=authority, suite_root=suite_root)

    persisted = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
        suite_id="results_first_exp1_exp4_trace",
    )
    ref_path = (
        authority.output_root
        / "closure-replay-snapshot"
        / "closure_replay_snapshot_ref.v1.json"
    )
    assert ref_path.is_file()
    assert json.loads(ref_path.read_text(encoding="utf-8")) == dict(persisted)

    loaded = pipeline.load_results_first_closure_replay_snapshot(
        output_root=authority.output_root,
        suite_root=suite_root,
    )

    assert loaded.snapshot_digest == persisted["snapshot_digest"]


def test_closure_snapshot_restart_rejects_tampered_existing_handle(
    tmp_path: Path,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    pickle_path = (
        authority.output_root
        / "closure-replay-snapshot"
        / "closure_replay_snapshot.v1.pickle"
    )
    pickle_path.write_bytes(pickle_path.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="pickle digest"):
        pipeline.persist_results_first_closure_replay_snapshot(
            authority=authority,
            output_root=authority.output_root,
        )


def test_results_first_service_persists_snapshot_before_any_dispatch_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.paper_response_bank import AcquisitionPlanBundle

    authority = _closure_snapshot_authority(tmp_path)
    assert type(authority.bundle) is AcquisitionPlanBundle
    zero = pipeline.RepresentativeCurrentProviderUsage(
        provider_calls=0,
        spend=0.0,
    )
    snapshot_ref_path = (
        authority.output_root
        / "closure-replay-snapshot"
        / "closure_replay_snapshot_ref.v1.json"
    )
    observed: list[str] = []

    def require_snapshot(stage: str) -> None:
        assert snapshot_ref_path.is_file(), f"snapshot missing before {stage}"
        observed.append(stage)

    def fake_acquire(*, authority, resume):
        require_snapshot("acquisition")
        assert type(authority.bundle) is AcquisitionPlanBundle
        return pipeline.RepresentativeAcquisitionStageResult(
            resolver=object(),
            usage=zero,
            max_in_flight=1,
        )

    monkeypatch.setattr(
        pipeline,
        "_acquire_representative_response_bank",
        fake_acquire,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        lambda: require_snapshot("exp5_transport") or object(),
    )

    def fake_execute(**kwargs):
        require_snapshot("trace_exp5_executor")
        assert kwargs["authority"].bundle is authority.bundle
        return SimpleNamespace(
            status="completed",
            expected_condition_count=authority.coverage.condition_count,
            expected_root_count=authority.coverage.root_run_count,
            acquisition_current_provider_calls=0,
            acquisition_current_spend=0.0,
            acquisition_spend_missing_reason=None,
            trace_current_provider_calls=0,
            trace_current_spend=0.0,
            trace_spend_missing_reason=None,
            exp5_current_provider_calls=0,
            exp5_current_spend=0.0,
            exp5_spend_missing_reason=None,
            total_current_provider_calls=0,
            total_current_spend=0.0,
            total_spend_missing_reason=None,
        )

    monkeypatch.setattr(pipeline, "_REPRESENTATIVE_SMOKE_EXECUTOR", fake_execute)
    request = SimpleNamespace(output_root=authority.output_root)

    def invoke(*, resume: bool) -> dict[str, object]:
        return pipeline._execute_results_first_service_input(
            request=request,
            value=pipeline.ResultsFirstServiceInput(
                authority=_clone_unchecked_results_first_authority(
                    authority,
                    resume=resume,
                ),
                selection="representative",
                plan_only=False,
                allow_provider_calls=True,
                external_bank_root=None,
                resume=resume,
            ),
        )

    first = invoke(resume=False)
    snapshot_files = {
        path.name: path.read_bytes()
        for path in snapshot_ref_path.parent.iterdir()
        if path.is_file()
    }
    second = invoke(resume=True)

    assert observed == [
        "acquisition",
        "exp5_transport",
        "trace_exp5_executor",
    ] * 2
    assert first["provider_calls"] == second["provider_calls"] == 0
    assert first["closure_replay_snapshot_ref"] == second[
        "closure_replay_snapshot_ref"
    ]
    assert Path(first["closure_replay_snapshot_ref_path"]) == snapshot_ref_path
    assert {
        path.name: path.read_bytes()
        for path in snapshot_ref_path.parent.iterdir()
        if path.is_file()
    } == snapshot_files


def test_paid_results_first_rederives_budget_from_approved_exp5_binding_before_provider_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """paid loader 必须在 provider boundary 前读取已批准的 Exp5 pricing authority。"""

    from tokenshare.experiments import paper_budget
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )

    authority = _small_full_paid_restore_authority(tmp_path)
    approved_exp5_binding = authority.ai_api_configs[
        APPROVED_ENDPOINT_BINDINGS_KEY
    ]["exp5_real_ai_model_endpoint_comparison"]
    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
        exp5_pricing_authority=approved_exp5_binding,
    )
    authority = replace(authority, provider_budget_authority=provider_budget)
    captured: dict[str, object] = {}

    def capture_rederivation(**kwargs: object) -> object:
        captured.update(kwargs)
        return provider_budget

    monkeypatch.setattr(
        paper_budget,
        "derive_results_first_provider_budget",
        capture_rederivation,
    )
    # 本测试只覆盖 budget 边界；正式 Exp5 coverage 的完整验证已由专门测试覆盖。
    monkeypatch.setattr(
        pipeline,
        "_results_first_formal_exp5_coverage",
        lambda _authority: object(),
    )

    with pytest.raises(ValueError, match="requires --allow-provider-calls"):
        pipeline._execute_results_first_service_input(
            request=SimpleNamespace(output_root=authority.output_root),
            value=pipeline.ResultsFirstServiceInput(
                authority=authority,
                selection="representative_exp1_exp3_exp5",
                plan_only=False,
                allow_provider_calls=False,
                external_bank_root=None,
                resume=False,
            ),
        )

    assert captured["exp5_pricing_authority"] is approved_exp5_binding
    assert captured["exp1_acquisition_budget"] is authority.bundle.full_budget


def test_results_first_provider_budget_excludes_reused_exp1_from_new_paid_total(
    tmp_path: Path,
) -> None:
    """外部 R13 bank 是历史输入，不能计入本轮 new-paid 总额。"""

    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )

    authority = _small_full_paid_restore_authority(tmp_path)
    budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
        exp1_acquisition_is_new=False,
    )

    assert dict(budget.exp1)["calls"] > 0
    assert dict(budget.exp1)["new_paid"] is False
    assert dict(budget.global_new_paid) == {
        "calls": dict(budget.exp5)["calls"],
        "tokens": dict(budget.exp5)["tokens"],
        "cny": dict(budget.exp5)["cny"],
    }


def test_paid_results_first_marks_external_representative_bank_as_not_new_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """paid loader 必须把 R13 external bank 的 Exp1 reuse 传给预算 authority。"""

    from tokenshare.experiments import paper_budget
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )

    authority = _small_full_paid_restore_authority(tmp_path)
    approved_exp5_binding = authority.ai_api_configs[
        APPROVED_ENDPOINT_BINDINGS_KEY
    ]["exp5_real_ai_model_endpoint_comparison"]
    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
        exp5_pricing_authority=approved_exp5_binding,
    )
    authority = replace(authority, provider_budget_authority=provider_budget)
    captured: dict[str, object] = {}

    def capture_rederivation(**kwargs: object) -> object:
        captured.update(kwargs)
        return provider_budget

    monkeypatch.setattr(
        paper_budget,
        "derive_results_first_provider_budget",
        capture_rederivation,
    )
    monkeypatch.setattr(
        pipeline,
        "_results_first_formal_exp5_coverage",
        lambda _authority: object(),
    )

    with pytest.raises(ValueError, match="requires --allow-provider-calls"):
        pipeline._execute_results_first_service_input(
            request=SimpleNamespace(output_root=authority.output_root),
            value=pipeline.ResultsFirstServiceInput(
                authority=authority,
                selection="representative_exp1_exp3_exp5",
                plan_only=False,
                allow_provider_calls=False,
                external_bank_root=tmp_path / "r13-external-bank",
                resume=False,
            ),
        )

    assert captured["exp1_acquisition_is_new"] is False


def test_closure_snapshot_dispatch_body_matches_trace_subset_wrapper() -> None:
    plans = tuple(
        SimpleNamespace(
            experiment_id=experiment_id,
            to_dict=lambda experiment_id=experiment_id: {
                "experiment_id": experiment_id
            },
        )
        for experiment_id in (
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
            "exp5_real_ai_model_endpoint_comparison",
        )
    )

    body = pipeline._closure_snapshot_dispatch_body(
        plans,
        active_experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        ),
    )

    assert body == {
        "schema_version": "tokenshare.paper_dispatch.v1",
        "plans": [
            {"experiment_id": plan.experiment_id} for plan in plans[:4]
        ],
    }


@pytest.mark.parametrize("mutation", ("missing", "reordered", "duplicate"))
def test_closure_snapshot_rejects_noncanonical_full_experiment_dispatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    plans = list(authority.full_dispatch_plans)
    if mutation == "missing":
        plans.pop()
    elif mutation == "reordered":
        plans[0], plans[1] = plans[1], plans[0]
    else:
        plans[-1] = plans[0]
    drifted = _clone_unchecked_results_first_authority(
        authority,
        full_dispatch_plans=tuple(plans),
    )

    with pytest.raises(ValueError, match="exact full experiment dispatch order"):
        pipeline.persist_results_first_closure_replay_snapshot(
            authority=drifted,
            output_root=drifted.output_root,
        )


@pytest.mark.parametrize(
    "kind",
    (
        "pickle",
        "ref_digest",
        "ref_schema",
        "missing_ref",
        "expected_ref_mismatch",
        "descriptor_digest",
        "dispatch_suite_body",
        "dispatch_wrapper_schema",
        "dispatch_wrapper_missing_plans",
        "catalog_suite_body",
        "budget_suite_body",
        "coverage_lineage",
        "inventory_lineage",
        "projection_lineage",
        "hard_limits_lineage",
        "unknown_schema",
        "missing_handle",
    ),
)
def test_results_first_closure_replay_snapshot_rejects_tamper_and_missing_inputs(
    tmp_path: Path,
    kind: str,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    suite_root = tmp_path / "suite"
    _write_closure_snapshot_suite(authority=authority, suite_root=suite_root)
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
        suite_id="results_first_exp1_exp4_trace",
    )
    snapshot_root = authority.output_root / "closure-replay-snapshot"
    if kind == "pickle":
        path = snapshot_root / "closure_replay_snapshot.v1.pickle"
        path.write_bytes(path.read_bytes() + b"tamper")
    elif kind in {"ref_digest", "ref_schema"}:
        from tokenshare.experiments.paper_models import digest_json

        path = snapshot_root / "closure_replay_snapshot_ref.v1.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        if kind == "ref_digest":
            body["ref_digest"] = "sha256:" + "0" * 64
        else:
            body.pop("ref_digest")
            body["schema_version"] = "tokenshare.unknown.ref.v999"
            body["ref_digest"] = digest_json(body)
        path.write_text(json.dumps(body), encoding="utf-8")
    elif kind == "missing_ref":
        (snapshot_root / "closure_replay_snapshot_ref.v1.json").unlink()
    elif kind == "expected_ref_mismatch":
        ref = {**dict(ref), "snapshot_digest": "sha256:" + "0" * 64}
    elif kind == "descriptor_digest":
        path = snapshot_root / "closure_replay_snapshot.v1.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        body["suite_id"] = "tampered-suite"
        path.write_text(json.dumps(body), encoding="utf-8")
    elif kind.endswith("_suite_body"):
        name = {
            "dispatch_suite_body": "paper_dispatch_plans.json",
            "catalog_suite_body": "input_catalog_manifest.json",
            "budget_suite_body": "run_budget.json",
        }[kind]
        path = suite_root / name
        body = json.loads(path.read_text(encoding="utf-8"))
        if name == "paper_dispatch_plans.json":
            body["plans"][0]["status"] = "blocked"
        else:
            body["schema_version"] = "tampered"
        path.write_text(json.dumps(body), encoding="utf-8")
    elif kind.startswith("dispatch_wrapper_"):
        path = suite_root / "paper_dispatch_plans.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        if kind == "dispatch_wrapper_schema":
            body["schema_version"] = "tokenshare.paper_dispatch.unknown"
        else:
            body.pop("plans")
        path.write_text(json.dumps(body), encoding="utf-8")
    elif kind == "missing_handle":
        (snapshot_root / "closure_replay_snapshot.v1.pickle").unlink()
    else:
        key = {
            "coverage_lineage": "coverage_digest",
            "inventory_lineage": "semantic_inventory_digest",
            "projection_lineage": "execution_budget_projection_digest",
            "hard_limits_lineage": "hard_limits_digest",
        }.get(kind)

        def mutate(body: dict[str, object]) -> None:
            if kind == "unknown_schema":
                body["schema_version"] = "tokenshare.unknown.v999"
            else:
                body["lineage"][key] = "sha256:" + "0" * 64

        ref = _rewrite_closure_snapshot_descriptor(
            output_root=authority.output_root,
            mutate=mutate,
        )

    with pytest.raises((OSError, TypeError, ValueError)):
        pipeline.load_results_first_closure_replay_snapshot(
            output_root=authority.output_root,
            expected_ref=ref,
            suite_root=suite_root,
        )


def _closure_resume_source(
    *,
    authority,
    source_root: Path,
    include_pending: bool = False,
) -> tuple[Path, Path, Path]:
    _write_closure_snapshot_suite(authority=authority, suite_root=source_root)
    run_root = (
        source_root
        / "experiments"
        / "exp1_real_ai_feasibility"
        / "runs"
        / "condition-1"
        / "0"
    )
    generation_root = run_root / ".generations" / "generation-1"
    generation_root.mkdir(parents=True)
    current_path = run_root / "CURRENT.json"
    current_path.write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation_root.name,
                "generation_manifest_digest": "sha256:" + "1" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    generation_path = generation_root / "generation_manifest.json"
    generation_path.write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_checkpoint_generation.v3",
                "generation_id": generation_root.name,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if include_pending:
        (run_root / "PENDING.json").write_text(
            '{"publication_kind":"root_delta"}\n',
            encoding="utf-8",
        )
    (source_root / "formal_runner_result.json").write_text(
        '{"status":"completed"}\n',
        encoding="utf-8",
    )
    (source_root / "traceability_replay_input_root.handle.pickle").write_bytes(
        b"derived-handle"
    )
    suite_manifest = {
        "schema_version": "tokenshare.paper_suite_evidence.v1",
        "suite_id": "results_first_exp1_exp4_trace",
        "suite_identity": {
            "dispatch": {
                "body": json.loads(
                    (source_root / "paper_dispatch_plans.json").read_text(
                        encoding="utf-8"
                    )
                )
            }
        },
        "traceability_replay_input_root_ref": {
            "handle_path": "traceability_replay_input_root.handle.pickle"
        },
    }
    (source_root / "suite_manifest.json").write_text(
        json.dumps(suite_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checkpoint_root = source_root.with_name(
        source_root.name + ".canonical_direct_evidence"
    )
    checkpoint_path = checkpoint_root / "root-1" / "canonical_direct_checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        '{"schema_version":"tokenshare.canonical_direct_checkpoint.v1"}\n',
        encoding="utf-8",
    )
    replay_inputs = source_root.with_name(
        source_root.name + ".traceability_replay_inputs"
    )
    replay_inputs.mkdir()
    (replay_inputs / "derived.json").write_text("{}\n", encoding="utf-8")
    return current_path, generation_path, checkpoint_path


class _ClosureReplayFailFastTransport:
    def post_chat_completion(self, **_kwargs):
        pytest.fail("closure replay attempted provider transport")


def _install_closure_top_level_pending(
    *,
    suite_root: Path,
    publication_kind: str,
) -> dict[Path, str]:
    from tokenshare.experiments import paper_formal_runner as formal_runner

    if publication_kind == "suite_terminal_commit":
        result_path = suite_root / "formal_runner_result.json"
        suite_path = suite_root / "suite_manifest.json"
        result_content = json.dumps(
            {"status": "completed", "repaired_terminal": True},
            sort_keys=True,
        ) + "\n"
        suite_body = json.loads(suite_path.read_text(encoding="utf-8"))
        suite_body["status"] = "completed"
        suite_body["repaired_terminal"] = True
        suite_content = json.dumps(suite_body, sort_keys=True) + "\n"
        pending = {
            "schema_version": "tokenshare.paper_suite_finalization_pending.v1",
            "publication_kind": publication_kind,
            "formal_runner_result_target": formal_runner._formal_finalization_target(
                suite_root=suite_root,
                path=result_path,
                content=result_content,
            ),
            "suite_manifest_target": formal_runner._formal_finalization_target(
                suite_root=suite_root,
                path=suite_path,
                content=suite_content,
            ),
        }
        expected = {
            result_path: result_content,
            suite_path: suite_content,
        }
    elif publication_kind == "experiment_terminal_commit":
        experiment_id = "exp1_real_ai_feasibility"
        rows_path = suite_root / "condition_results.jsonl"
        manifest_path = (
            suite_root
            / "experiments"
            / experiment_id
            / "experiment_manifest.json"
        )
        rows_content = json.dumps(
            {
                "experiment_id": experiment_id,
                "condition_id": "condition-1",
                "status": "completed",
            },
            sort_keys=True,
        ) + "\n"
        manifest_content = json.dumps(
            {
                "experiment_id": experiment_id,
                "status": "completed",
                "repaired_terminal": True,
            },
            sort_keys=True,
        ) + "\n"
        pending = {
            "schema_version": "tokenshare.paper_experiment_finalization_pending.v1",
            "publication_kind": publication_kind,
            "experiment_id": experiment_id,
            "condition_results_target": formal_runner._formal_finalization_target(
                suite_root=suite_root,
                path=rows_path,
                content=rows_content,
            ),
            "experiment_manifest_target": formal_runner._formal_finalization_target(
                suite_root=suite_root,
                path=manifest_path,
                content=manifest_content,
            ),
        }
        expected = {
            rows_path: rows_content,
            manifest_path: manifest_content,
        }
    else:
        raise AssertionError(publication_kind)
    (suite_root / "PENDING.json").write_text(
        json.dumps(pending, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return expected


def _closure_source_file_digests(source_root: Path) -> dict[str, str]:
    return {
        path.relative_to(source_root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    }


def _closure_replay_test_preflight() -> pipeline.ResultsFirstTraceClosurePreflight:
    return pipeline.ResultsFirstTraceClosurePreflight(
        condition_count=4,
        root_count=4,
        checkpoint_count=4,
        attempt_count=4,
        current_provider_attempt_count=0,
        legacy_attempt_row_count=0,
        positive_provider_attempt_ordinal_count=4,
        source_consumption_count=4,
        checkpoint_inventory_digest="sha256:" + "2" * 64,
    )


@pytest.mark.parametrize(
    "publication_kind",
    ("suite_terminal_commit", "experiment_terminal_commit"),
)
def test_closure_replay_repairs_real_top_level_pending_before_derived_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication_kind: str,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    expected_source_paths = _install_closure_top_level_pending(
        suite_root=source_root,
        publication_kind=publication_kind,
    )
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    source_digests = _closure_source_file_digests(source_root)
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: _closure_replay_test_preflight(),
    )
    monkeypatch.setattr(pipeline, "_TRACE_CONTEXT_BUILDER", lambda **_kwargs: object())
    scratch_root = tmp_path / "scratch-trace"
    observed_repair: list[str] = []
    real_remove_derived = pipeline._closure_replay_remove_derived

    def observe_before_derived_cleanup(**kwargs):
        staged_root = kwargs["scratch_root"]
        assert not (staged_root / "PENDING.json").exists()
        for source_path, content in expected_source_paths.items():
            staged_path = staged_root / source_path.relative_to(source_root)
            assert staged_path.read_text(encoding="utf-8") == content
        observed_repair.append(publication_kind)
        return real_remove_derived(**kwargs)

    monkeypatch.setattr(
        pipeline,
        "_closure_replay_remove_derived",
        observe_before_derived_cleanup,
    )
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_SUITE_EXECUTOR",
        lambda **_kwargs: replace(
            _paper_suite_result(scratch_root, status="completed"),
            suite_id="results_first_exp1_exp4_trace",
            experiment_ids=(
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            ),
            condition_count=4,
            task_count=4,
            provider_attempt_count=0,
        ),
    )

    result = pipeline.resume_results_first_trace_closure_from_snapshot(
        source_trace_root=source_root,
        snapshot_root=authority.output_root,
        scratch_output_root=scratch_root,
        response_bank_resolver=object(),
        transport=_ClosureReplayFailFastTransport(),
    )

    assert result.task_count == 4
    assert observed_repair == [publication_kind]
    assert not (scratch_root / "PENDING.json").exists()
    assert _closure_source_file_digests(source_root) == source_digests


def test_closure_replay_failed_top_level_repair_cleans_partial_without_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    _install_closure_top_level_pending(
        suite_root=source_root,
        publication_kind="suite_terminal_commit",
    )
    pending_path = source_root / "PENDING.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["formal_runner_result_target"]["target_digest"] = "sha256:" + "0" * 64
    pending_path.write_text(json.dumps(pending, sort_keys=True) + "\n", encoding="utf-8")
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    source_digests = _closure_source_file_digests(source_root)
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: _closure_replay_test_preflight(),
    )
    scratch_root = tmp_path / "scratch-trace"

    with pytest.raises(ValueError, match="target digest"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=source_root,
            snapshot_root=authority.output_root,
            scratch_output_root=scratch_root,
            response_bank_resolver=object(),
            transport=_ClosureReplayFailFastTransport(),
        )

    assert not scratch_root.exists()
    assert not scratch_root.with_name(
        scratch_root.name + ".canonical_direct_evidence"
    ).exists()
    assert not tuple(tmp_path.glob(".scratch-trace.closure-replay-partial-*"))
    assert _closure_source_file_digests(source_root) == source_digests


def test_closure_replay_uses_production_resume_without_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    source_paths = _closure_resume_source(
        authority=authority,
        source_root=source_root,
        include_pending=True,
    )
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    source_digests = {
        path: sha256(path.read_bytes()).hexdigest() for path in source_paths
    }
    scratch_root = tmp_path / "scratch-trace"
    trace_context = object()
    response_bank_resolver = object()
    executor_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: pipeline.ResultsFirstTraceClosurePreflight(
            condition_count=4,
            root_count=4,
            checkpoint_count=4,
            attempt_count=4,
            current_provider_attempt_count=0,
            legacy_attempt_row_count=0,
            positive_provider_attempt_ordinal_count=4,
            source_consumption_count=4,
            checkpoint_inventory_digest="sha256:" + "2" * 64,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_TRACE_CONTEXT_BUILDER",
        lambda **kwargs: (
            trace_context
            if type(kwargs["closure_snapshot"])
            is pipeline.ResultsFirstClosureReplaySnapshot
            and kwargs["closure_snapshot"].source_exp1_inventory_plan.inventory_digest
            == authority.bundle.semantic_inventory_plan.inventory_digest
            and kwargs["closure_snapshot"].replay_semantic_authority
            .semantic_inventory_plan
            is kwargs["closure_snapshot"].semantic_inventory_plan
            and kwargs["resolver"] is response_bank_resolver
            and kwargs["output_root"] == authority.output_root
            else pytest.fail("closure replay bypassed the persisted trace router authority")
        ),
    )

    def fake_executor(**kwargs):
        executor_calls.append(kwargs)
        assert kwargs["resume"] is True
        assert kwargs["trace_context"] is trace_context
        assert kwargs["real_transport"] is False
        assert (scratch_root / "experiments" / "exp1_real_ai_feasibility" / "runs" / "condition-1" / "0" / "PENDING.json").is_file()
        assert not (scratch_root / "formal_runner_result.json").exists()
        assert not (
            scratch_root / "traceability_replay_input_root.handle.pickle"
        ).exists()
        assert not scratch_root.with_name(
            scratch_root.name + ".traceability_replay_inputs"
        ).exists()
        manifest = json.loads(
            (scratch_root / "suite_manifest.json").read_text(encoding="utf-8")
        )
        assert "traceability_replay_input_root_ref" not in manifest
        return replace(
            _paper_suite_result(scratch_root, status="completed"),
            suite_id="results_first_exp1_exp4_trace",
            experiment_ids=(
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            ),
            condition_count=4,
            task_count=4,
            provider_attempt_count=0,
        )

    monkeypatch.setattr(pipeline, "_FORMAL_SUITE_EXECUTOR", fake_executor)
    result = pipeline.resume_results_first_trace_closure_from_snapshot(
        source_trace_root=source_root,
        snapshot_root=authority.output_root,
        scratch_output_root=scratch_root,
        response_bank_resolver=response_bank_resolver,
        transport=_ClosureReplayFailFastTransport(),
    )

    assert result.task_count == 4
    assert result.provider_attempt_count == 0
    assert len(executor_calls) == 1
    assert all(
        sha256(path.read_bytes()).hexdigest() == source_digests[path]
        for path in source_paths
    )
    assert (
        scratch_root.with_name(scratch_root.name + ".canonical_direct_evidence")
        / "root-1"
        / "canonical_direct_checkpoint.json"
    ).is_file()


@pytest.mark.parametrize(
    "target_kind",
    ("same", "nonfresh", "inside_source", "checkpoint_nonfresh"),
)
def test_closure_replay_rejects_unsafe_scratch_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: pytest.fail("unsafe targets must fail before audit"),
    )
    if target_kind == "same":
        scratch_root = source_root
    elif target_kind == "inside_source":
        scratch_root = source_root / "nested-scratch"
    else:
        scratch_root = tmp_path / "scratch-trace"
        occupied = (
            scratch_root
            if target_kind == "nonfresh"
            else scratch_root.with_name(
                scratch_root.name + ".canonical_direct_evidence"
            )
        )
        occupied.mkdir()

    with pytest.raises(ValueError, match="scratch|fresh|source"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=source_root,
            snapshot_root=authority.output_root,
            scratch_output_root=scratch_root,
            response_bank_resolver=object(),
            transport=_ClosureReplayFailFastTransport(),
        )


def test_closure_replay_rejects_noncanonical_derived_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    (source_root / "formal_runner_result.json").unlink()
    (source_root / "formal_runner_result.json").mkdir()
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: pipeline.ResultsFirstTraceClosurePreflight(
            condition_count=4,
            root_count=4,
            checkpoint_count=4,
            attempt_count=4,
            current_provider_attempt_count=0,
            legacy_attempt_row_count=0,
            positive_provider_attempt_ordinal_count=4,
            source_consumption_count=4,
            checkpoint_inventory_digest="sha256:" + "2" * 64,
        ),
    )

    with pytest.raises(ValueError, match="derived"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=source_root,
            snapshot_root=authority.output_root,
            scratch_output_root=tmp_path / "scratch-trace",
            response_bank_resolver=object(),
            transport=_ClosureReplayFailFastTransport(),
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_closure_replay_rejects_source_junction_descendant_before_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    junction_probe = source_root / "junction-probe"
    junction_probe.mkdir()
    real_is_junction = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == junction_probe or real_is_junction(self),
    )
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: pytest.fail("junction must fail before source audit"),
    )
    scratch_root = tmp_path / "scratch-trace"

    with pytest.raises(ValueError, match="reparse"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=source_root,
            snapshot_root=authority.output_root,
            scratch_output_root=scratch_root,
            response_bank_resolver=object(),
            transport=_ClosureReplayFailFastTransport(),
        )
    assert not scratch_root.exists()


def test_closure_replay_second_copy_failure_leaves_no_final_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _closure_snapshot_authority(tmp_path)
    source_root = tmp_path / "source-trace"
    _closure_resume_source(authority=authority, source_root=source_root)
    pipeline.persist_results_first_closure_replay_snapshot(
        authority=authority,
        output_root=authority.output_root,
    )
    preflight = pipeline.ResultsFirstTraceClosurePreflight(
        condition_count=4,
        root_count=4,
        checkpoint_count=4,
        attempt_count=4,
        current_provider_attempt_count=0,
        legacy_attempt_row_count=0,
        positive_provider_attempt_ordinal_count=4,
        source_consumption_count=4,
        checkpoint_inventory_digest="sha256:" + "2" * 64,
    )
    monkeypatch.setattr(
        pipeline,
        "audit_results_first_trace_closure_source",
        lambda **_kwargs: preflight,
    )
    monkeypatch.setattr(
        pipeline,
        "_TRACE_CONTEXT_BUILDER",
        lambda **_kwargs: object(),
    )
    scratch_root = tmp_path / "scratch-trace"
    checkpoint_root = scratch_root.with_name(
        scratch_root.name + ".canonical_direct_evidence"
    )
    original_copytree = pipeline.shutil.copytree
    copy_calls = 0

    def fail_second_copy(*args, **kwargs):
        nonlocal copy_calls
        copy_calls += 1
        if copy_calls == 2:
            raise OSError("injected canonical checkpoint copy failure")
        return original_copytree(*args, **kwargs)

    monkeypatch.setattr(pipeline.shutil, "copytree", fail_second_copy)
    with pytest.raises(OSError, match="injected canonical checkpoint"):
        pipeline.resume_results_first_trace_closure_from_snapshot(
            source_trace_root=source_root,
            snapshot_root=authority.output_root,
            scratch_output_root=scratch_root,
            response_bank_resolver=object(),
            transport=_ClosureReplayFailFastTransport(),
        )
    assert not scratch_root.exists()
    assert not checkpoint_root.exists()
    assert not tuple(
        tmp_path.glob(".scratch-trace.closure-replay-partial-*")
    )

    monkeypatch.setattr(pipeline.shutil, "copytree", original_copytree)
    monkeypatch.setattr(
        pipeline,
        "_FORMAL_SUITE_EXECUTOR",
        lambda **_kwargs: replace(
            _paper_suite_result(scratch_root, status="completed"),
            suite_id="results_first_exp1_exp4_trace",
            experiment_ids=(
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            ),
            condition_count=4,
            task_count=4,
            provider_attempt_count=0,
        ),
    )
    result = pipeline.resume_results_first_trace_closure_from_snapshot(
        source_trace_root=source_root,
        snapshot_root=authority.output_root,
        scratch_output_root=scratch_root,
        response_bank_resolver=object(),
        transport=_ClosureReplayFailFastTransport(),
    )
    assert result.task_count == 4
    assert scratch_root.is_dir()
    assert checkpoint_root.is_dir()


def _paid_launch_red_authority(tmp_path: Path):
    authority = _representative_pipeline_authority(tmp_path / "paid-output")
    budget = SimpleNamespace(
        authority_digest="sha256:" + "9" * 64,
        provider_calls_made=0,
    )
    return _clone_unchecked_results_first_authority(
        authority,
        provider_budget_authority=budget,
    )


def _rejected_paid_launch_cli_carries_only_local_config_path_and_fresh_attestation_root(
    tmp_path: Path,
) -> None:
    parser = pipeline._build_argument_parser()
    key_config = tmp_path / "ai_api_smoke.local.json"
    attestation_root = tmp_path / "paid-launch-attestation"
    args = parser.parse_args(
        [
            "run-results-first",
            "--output-root",
            str(tmp_path / "paid-output"),
            "--planning-artifact-root",
            str(tmp_path / "planning"),
            "--plan-bundle-root",
            str(tmp_path / "bundle"),
            "--new-run",
            "--allow-provider-calls",
            "--frozen-closure-snapshot-root",
            str(tmp_path / "snapshot"),
            "--frozen-prepared-inventory-root",
            str(tmp_path / "inventory"),
            "--expected-preflight",
            str(tmp_path / "provider-zero-preflight.json"),
            "--expected-provider-budget-digest",
            "sha256:" + "9" * 64,
            "--selection",
            "representative",
            "--paid-launch-key-config",
            str(key_config),
            "--paid-launch-attestation-root",
            str(attestation_root),
        ]
    )

    assert Path(args.paid_launch_key_config) == key_config
    assert Path(args.paid_launch_attestation_root) == attestation_root
    assert not hasattr(args, "deepseek_api_key")
    assert not hasattr(args, "siliconflow_api_key")


def _rejected_paid_launch_attestation_reads_both_keys_in_same_process_after_cold_and_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from tests.phase7_fixtures import make_config_dict
    from tokenshare.executors.ai_api_local_config import load_local_ai_api_config

    authority = _paid_launch_red_authority(tmp_path)
    key_config = tmp_path / "ai_api_smoke.local.json"
    deepseek_marker = "unit-deepseek-value-never-persist"
    siliconflow_marker = "unit-siliconflow-value-never-persist"
    config_body = make_config_dict()
    entry_template = config_body["entries"][0]
    config_body["entries"] = [
        {
            **entry_template,
            "entry_id": "deepseek_injection_only",
            "enabled": False,
            "api_key_env": "DEEPSEEK_API_KEY",
            "api_key": deepseek_marker,
        },
        {
            **entry_template,
            "entry_id": "siliconflow_injection_only",
            "enabled": False,
            "api_key_env": "SILICONFLOW_API_KEY",
            "api_key": siliconflow_marker,
        },
    ]
    key_config.write_text(
        json.dumps(config_body, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None
    events: list[str] = []
    persisted: list[dict[str, object]] = []

    def cold_validator(**kwargs: object) -> None:
        events.append("cold_authority_budget_output")
        assert kwargs["authority"] is authority
        assert kwargs["expected_provider_budget_digest"] == "sha256:" + "9" * 64
        assert not authority.output_root.exists()

    def acquire_lock(*, attestation_root: Path) -> object:
        events.append("single_instance_lock")
        assert not Path(attestation_root).exists()
        return object()

    def load_local_config(path: Path) -> object:
        events.append("same_process_secret_read")
        assert Path(path) == key_config
        return load_local_ai_api_config(path)

    def write_attestation(
        *, output_root: Path, body: dict[str, object]
    ) -> Path:
        events.append("sanitized_attestation_write")
        payload = json.dumps(body, sort_keys=True)
        assert deepseek_marker not in payload
        assert siliconflow_marker not in payload
        assert body == {
            "schema_version": "tokenshare.paid_launch_key_readiness.v1",
            "scope": "results_first_paid_representative",
            "statuses": {
                "DEEPSEEK_API_KEY": "SET",
                "SILICONFLOW_API_KEY": "SET",
            },
            "provider_calls_made": 0,
            "attestation_digest": body["attestation_digest"],
        }
        Path(output_root).mkdir(parents=True, exist_ok=False)
        path = Path(output_root) / "paid_launch_key_readiness.v1.json"
        path.write_text(payload + "\n", encoding="utf-8")
        persisted.append(dict(body))
        return path

    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_COLD_AUTHORITY_VALIDATOR",
        cold_validator,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        acquire_lock,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        load_local_config,
        raising=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_ATTESTATION_WRITER",
        write_attestation,
        raising=False,
    )
    readiness = pipeline.prepare_results_first_paid_launch_key_readiness(
        authority=authority,
        key_config_path=key_config,
        attestation_root=tmp_path / "paid-launch-attestation",
        expected_provider_budget_digest="sha256:" + "9" * 64,
    )
    events.append("readiness_minted")
    pipeline.consume_results_first_paid_launch_key_readiness(readiness)
    events.append("first_network_boundary")

    assert events == [
        "cold_authority_budget_output",
        "single_instance_lock",
        "same_process_secret_read",
        "sanitized_attestation_write",
        "readiness_minted",
        "first_network_boundary",
    ]
    assert len(persisted) == 1
    with pytest.raises(TypeError, match="consumed|one-shot"):
        pipeline.consume_results_first_paid_launch_key_readiness(readiness)
    with pytest.raises(TypeError, match="copy"):
        copy.copy(readiness)
    rendered = capsys.readouterr()
    evidence = json.dumps(persisted, sort_keys=True)
    argv = ("--paid-launch-key-config", str(key_config))
    for secret in (deepseek_marker, siliconflow_marker):
        assert secret not in rendered.out
        assert secret not in rendered.err
        assert secret not in evidence
        assert secret not in argv


def _rejected_paid_launch_cold_failure_does_not_lock_read_secret_write_or_consume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _paid_launch_red_authority(tmp_path)
    calls: list[str] = []

    def reject_cold(**_kwargs: object) -> None:
        calls.append("cold")
        raise ValueError("cold authority rejected")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        calls.append("forbidden")
        raise AssertionError("cold failure crossed paid launch boundary")

    monkeypatch.setattr(
        pipeline,
        "_PAID_LAUNCH_COLD_AUTHORITY_VALIDATOR",
        reject_cold,
        raising=False,
    )
    for name in (
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        "_PAID_LAUNCH_ATTESTATION_WRITER",
    ):
        monkeypatch.setattr(pipeline, name, forbidden, raising=False)

    with pytest.raises(ValueError, match="cold authority rejected"):
        pipeline.prepare_results_first_paid_launch_key_readiness(
            authority=authority,
            key_config_path=tmp_path / "ignored-local.json",
            attestation_root=tmp_path / "paid-launch-attestation",
            expected_provider_budget_digest="sha256:" + "9" * 64,
        )
    assert calls == ["cold"]
    assert not (tmp_path / "paid-launch-attestation").exists()


def _r13_exact_provider_budget_authority():
    from tokenshare.experiments.paper_budget import (
        ResultsFirstProviderBudgetAuthority,
    )

    root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-planning"
    )
    authority = pipeline._load_results_first_provider_budget_exact(
        planning_artifact_root=root,
        expected_authority_digest=(
            "sha256:9f3e0ffbb7f27a5c970a27a1b9c5d62ceb5dd0634dd2dc1a308c114471540d8e"
        ),
    )
    assert type(authority) is ResultsFirstProviderBudgetAuthority
    assert authority.exp1["calls"] == 90
    assert authority.exp1["tokens"] == 27_504_469
    assert authority.exp1["cny"] == "163.513407"
    assert dict(authority.exp2_4) == {
        "current_provider_calls": 0,
        "current_tokens": 0,
        "current_cny": "0",
    }
    assert authority.exp5["calls"] == 112
    assert authority.exp5["tokens"] == 6_881_280
    assert authority.exp5["cny"] == "79.249408"
    assert dict(authority.global_new_paid) == {
        "calls": 202,
        "tokens": 34_385_749,
        "cny": "242.762815",
    }
    return authority, root


def test_r13_stage_a_cache_is_shared_by_distinct_consumer_identities(
    tmp_path: Path,
) -> None:
    consumers = (ModuleType("consumer_a"), ModuleType("consumer_b"))
    for consumer in consumers:
        exec(
            "from tests.support.r13_full_stage_a import SingleAttemptStageACache",
            consumer.__dict__,
        )
    assert consumers[0].SingleAttemptStageACache is consumers[1].SingleAttemptStageACache

    cache = consumers[0].SingleAttemptStageACache()
    build_calls: list[Path] = []

    def builder(base: Path) -> dict[str, object]:
        build_calls.append(base)
        return {"identity": object()}

    first = cache.get(tmp_path, builder=builder)
    second = cache.get(tmp_path, builder=builder)

    consumers[0].stage_a = first
    consumers[1].stage_a = second
    assert consumers[0] is not consumers[1]
    assert consumers[0].stage_a is consumers[1].stage_a
    assert build_calls == [tmp_path]
    assert cache.build_count == 1


def test_r13_stage_a_cache_failed_first_attempt_cannot_rebuild(
    tmp_path: Path,
) -> None:
    from tests.support.r13_full_stage_a import SingleAttemptStageACache

    cache = SingleAttemptStageACache()
    first_calls = 0
    retry_calls = 0

    def failing_builder(_base: Path) -> dict[str, object]:
        nonlocal first_calls
        first_calls += 1
        raise RuntimeError("expected first-attempt failure")

    def forbidden_retry(_base: Path) -> dict[str, object]:
        nonlocal retry_calls
        retry_calls += 1
        return {"unexpected": True}

    with pytest.raises(RuntimeError, match="expected first-attempt failure"):
        cache.get(tmp_path, builder=failing_builder)
    with pytest.raises(AssertionError, match="already attempted without a result"):
        cache.get(tmp_path, builder=forbidden_retry)

    assert first_calls == 1
    assert retry_calls == 0
    assert cache.build_count == 1


from tests.support.r13_full_stage_a import (
    get_r13_full_stage_a,
    r13_full_stage_a_build_count,
)


@pytest.fixture(scope="session")
def r13_full_stage_a(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    return get_r13_full_stage_a(tmp_path_factory.getbasetemp())


def _r13_create_fresh_source_rebind(
    *,
    module: ModuleType,
    tmp_path: Path,
    source_v2_root: Path,
    inventory_root: Path,
    old_preflight: Path,
    bundle_root: Path,
    planning_root: Path,
    expected_provider_budget_digest: str,
    dependencies: object,
) -> Mapping[str, object]:
    return module.create_paid_source_rebind(
        paths=module.PaidSourceRebindPaths(
            source_v2_root=source_v2_root,
            inventory_root=inventory_root,
            old_preflight=old_preflight,
            corrected_bundle_root=bundle_root,
            planning_root=planning_root,
            source_rebind_root=tmp_path / "fresh-current-source-rebind",
            execution_root=tmp_path / "reserved-source-rebind-execution",
        ),
        selection="representative",
        expected_provider_budget_digest=expected_provider_budget_digest,
        dependencies=dependencies,
    )


def _r13_persist_real_reference_binding(
    tmp_path: Path,
    r13_full_stage_a: dict[str, object],
):
    import importlib.util

    script = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid_representative_postfix_restore_overlay.py"
    )
    spec = importlib.util.spec_from_file_location(
        "r13_paid_reference_binding_fixture",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    factory = getattr(module, "build_provider_zero_reference_binding_authority", None)
    persist = getattr(module, "persist_provider_zero_reference_binding", None)
    assert callable(factory), "typed provider-zero reference factory is absent"
    assert callable(persist), "typed provider-zero reference persister is absent"

    source_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260814-r5-restore-binding"
    )
    snapshot = module._load_legacy_v1_strict(
        output_root=source_root,
        expected_file_sha256=module.DEFAULT_DIGESTS.source_v1_sha256,
    )
    stage_a_paths = r13_full_stage_a["paths"]
    inventory_root = Path(stage_a_paths.output_inventory_root)
    from tokenshare.experiments.paper_formal_plan import (
        load_formal_prepared_request_inventory,
    )

    inventory_manifest = json.loads(
        (inventory_root / module.INVENTORY_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    inventory = load_formal_prepared_request_inventory(
        output_root=inventory_root,
        snapshot=snapshot.coverage.source_snapshot,
        expected_manifest=inventory_manifest,
    )
    assert inventory == r13_full_stage_a["inventory"]
    assert inventory.inventory_digest == r13_full_stage_a["inventory"].inventory_digest
    bundle_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-corrected-exp1-bundle"
    )
    bundle = pipeline._load_paid_restore_acquisition_bundle_exact(bundle_root)
    provider_budget, planning_root = _r13_exact_provider_budget_authority()
    source_v2_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-v2-binding"
    )
    old_preflight = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-preflight\paid_representative_projection_preflight.v1.json"
    )
    source_terminal = _r13_create_fresh_source_rebind(
        module=module,
        tmp_path=tmp_path,
        source_v2_root=source_v2_root,
        inventory_root=inventory_root,
        old_preflight=old_preflight,
        bundle_root=bundle_root,
        planning_root=planning_root,
        expected_provider_budget_digest=provider_budget.authority_digest,
        dependencies=module._build_actual_source_rebind_dependencies(),
    )
    source_rebind_root = tmp_path / "fresh-current-source-rebind"
    current_preflight_path = (
        source_rebind_root / "paid_source_rebind_preflight.v1.json"
    )
    assert source_terminal["status"] == "READY_PROVIDER_ZERO_SOURCE_REBIND"
    assert source_terminal["provider_calls"] == 0
    assert not (tmp_path / "reserved-source-rebind-execution").exists()
    assert source_rebind_root.is_dir()
    assert current_preflight_path.is_file()
    source_rebind_binding = json.loads(
        (source_rebind_root / "results_first_paid_source_rebind.v1.json").read_text(
            encoding="utf-8"
        )
    )
    source_rebind_ref = json.loads(
        (source_rebind_root / "results_first_paid_source_rebind_ref.v1.json").read_text(
            encoding="utf-8"
        )
    )
    current_preflight = json.loads(current_preflight_path.read_text(encoding="utf-8"))
    from tokenshare.experiments.paper_models import digest_json

    expected_source_plan_digest = digest_json(
        bundle.to_dict()["semantic_inventory_plan"]
    )
    assert source_rebind_binding["base_binding_digest"] == (
        "sha256:7807731aaefd9d8200cc75e2b668b82c091c6a9bd78adab01180c623a72173ad"
    )
    assert source_rebind_binding["base_binding_ref_digest"] == (
        "sha256:47c4c4a740702995d91bef8dd89c7918812dd794942fd5e48244093f6ee7c045"
    )
    assert source_rebind_binding["snapshot_digest"] == (
        "sha256:ca5079181aa7e8accdfbf57d9fedc9e8a0be681d1d135ab1b20ef9bd3adb38b1"
    )
    assert source_rebind_binding["prepared_inventory_digest"] == inventory.inventory_digest
    assert source_rebind_binding["prepared_inventory_manifest_digest"] == (
        inventory_manifest["manifest_digest"]
    )
    assert source_rebind_binding["acquisition_bundle_digest"] == bundle.bundle_digest
    assert source_rebind_binding["source_exp1_inventory_plan_digest"] == (
        expected_source_plan_digest
    )
    assert source_rebind_binding["provider_budget_authority_digest"] == (
        provider_budget.authority_digest
    )
    assert source_rebind_binding["provider_calls_made"] == 0
    assert len(source_rebind_binding["production_source_files"]) == 7
    assert current_preflight["source_unchanged"] is True
    assert current_preflight["api_key_status"]["statuses"] == {
        "DEEPSEEK_API_KEY": "UNSET",
        "SILICONFLOW_API_KEY": "UNSET",
    }
    reserved_execution_root = tmp_path / "reserved-execution"
    authority = factory(
        legacy_source_v1_snapshot=snapshot,
        legacy_source_v1_root=source_root,
        paid_restore_v2_root=Path(
            r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-v2-binding"
        ),
        sealed_inventory=inventory,
        sealed_inventory_root=inventory_root,
        sealed_inventory_manifest=inventory_manifest,
        corrected_bundle=bundle,
        corrected_bundle_root=bundle_root,
        provider_budget=provider_budget,
        planning_artifact_root=planning_root,
        current_source_rebind_root=source_rebind_root,
        current_provider_zero_preflight_path=current_preflight_path,
        selection="representative",
        reserved_execution_root=reserved_execution_root,
    )
    reference_root = tmp_path / "provider-zero-reference-binding"
    persist(authority=authority, output_root=reference_root)
    reference_binding = json.loads(
        (reference_root / module.PROVIDER_ZERO_REFERENCE_BINDING_NAME).read_text(
            encoding="utf-8"
        )
    )
    reference_ref = json.loads(
        (reference_root / module.PROVIDER_ZERO_REFERENCE_BINDING_REF_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert reference_root.is_dir()
    assert not reserved_execution_root.exists()
    return (
        reference_root,
        inventory_root,
        inventory.inventory_digest,
        bundle_root,
        planning_root,
        provider_budget,
        source_rebind_root,
        current_preflight_path,
        reference_binding["binding_digest"],
        reference_ref["ref_digest"],
        source_rebind_binding["binding_digest"],
        source_rebind_ref["ref_digest"],
        current_preflight["report_digest"],
    )


def test_r13_fresh_source_rebind_helper_uses_provider_zero_production_seam(
    tmp_path: Path,
) -> None:
    import importlib.util

    script = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid_representative_postfix_restore_overlay.py"
    )
    spec = importlib.util.spec_from_file_location("r13_source_rebind_helper_red", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    source_v2_root = tmp_path / "source-v2"
    closure_root = source_v2_root / module.V1_DIRECTORY_NAME
    closure_root.mkdir(parents=True)
    digest = "sha256:" + "1" * 64
    (closure_root / "closure_replay_snapshot_ref.v1.json").write_text(
        json.dumps({"snapshot_digest": digest}) + "\n",
        encoding="utf-8",
    )
    (closure_root / "closure_replay_paid_restore_binding.v2.json").write_text(
        json.dumps({"coverage_digest": digest}) + "\n",
        encoding="utf-8",
    )
    inventory_root = tmp_path / "inventory"
    bundle_root = tmp_path / "bundle"
    planning_root = tmp_path / "planning"
    for root in (inventory_root, bundle_root, planning_root):
        root.mkdir()
    old_preflight = tmp_path / "old-preflight.json"
    old_preflight.write_text("{}\n", encoding="utf-8")
    events: list[str] = []
    tripwires = {name: 0 for name in module.TRIPWIRE_NAMES}

    def build_preflight(**kwargs: object) -> Mapping[str, object]:
        events.append("build_current_preflight")
        assert kwargs["source_execution_root"] == source_v2_root
        return {
            "api_key_status": {
                "schema_version": "tokenshare.provider_key_presence_authority.v1",
                "scope": "provider_zero_preparation",
                "scope_provenance": "fixed_unset_without_secret_resolution",
                "statuses": {
                    "DEEPSEEK_API_KEY": "UNSET",
                    "SILICONFLOW_API_KEY": "UNSET",
                },
            },
            "tripwires": tripwires,
            "source_unchanged": True,
            "report_digest": digest,
        }

    def persist_rebind(**kwargs: object) -> Mapping[str, object]:
        events.append("persist_production_rebind")
        assert kwargs["frozen_prepared_inventory_root"] == inventory_root
        assert kwargs["plan_bundle_root"] == bundle_root
        assert kwargs["planning_artifact_root"] == planning_root
        return {
            "schema_version": "tokenshare.results_first_paid_source_rebind_ref.v1",
            "ref_digest": digest,
            "provider_calls_made": 0,
        }

    terminal = _r13_create_fresh_source_rebind(
        module=module,
        tmp_path=tmp_path,
        source_v2_root=source_v2_root,
        inventory_root=inventory_root,
        old_preflight=old_preflight,
        bundle_root=bundle_root,
        planning_root=planning_root,
        expected_provider_budget_digest=digest,
        dependencies=module.PaidSourceRebindDependencies(
            build_preflight=build_preflight,
            persist_rebind=persist_rebind,
            tripwire_snapshot=lambda: tripwires,
        ),
    )
    assert terminal["status"] == "READY_PROVIDER_ZERO_SOURCE_REBIND"
    assert terminal["provider_calls"] == 0
    assert events == ["build_current_preflight", "persist_production_rebind"]


def test_old_r12_preflight_rejects_live_source_drift_before_paid_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-v2-binding"
    )
    r12_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r12-source-rebind"
    )
    binding, _binding_ref = pipeline._load_results_first_paid_restore_binding_v2(v2_root)
    snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=v2_root,
        expected_ref=binding["base_snapshot_ref"],
    )
    stale_preflight = json.loads(
        (r12_root / pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME).read_text(
            encoding="utf-8"
        )
    )
    boundary_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        boundary_calls.append("forbidden")
        raise AssertionError("stale preflight crossed paid boundary")

    for name in (
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        "_PAID_LAUNCH_ATTESTATION_WRITER",
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
    ):
        monkeypatch.setattr(pipeline, name, forbidden, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    with pytest.raises(ValueError, match="production source"):
        pipeline._validate_results_first_paid_preflight(
            stale_preflight,
            snapshot=snapshot,
            selection="representative",
            frozen_snapshot_root=v2_root,
        )
    assert boundary_calls == []
    assert not (tmp_path / "attestation").exists()
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


def test_paid_reference_cold_loader_routes_strict_restore_through_v2_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "1" * 64
    preflight_sha256 = "sha256:" + sha256(b"preflight").hexdigest()
    legacy_root = tmp_path / "legacy-v1"
    paid_restore_v2_root = tmp_path / "paid-restore-v2"
    inventory_root = tmp_path / "inventory"
    bundle_root = tmp_path / "bundle"
    planning_root = tmp_path / "planning"
    rebind_root = tmp_path / "rebind"
    base_preflight_path = tmp_path / "base-preflight.json"
    current_preflight_path = (
        rebind_root / pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME
    )
    reference = {
        "schema_version": "tokenshare.provider_zero_reference_binding.v2",
        "legacy_source_v1_root": str(legacy_root.resolve()),
        "legacy_closure_snapshot_digest": digest,
        "paid_restore_v2_root": str(paid_restore_v2_root.resolve()),
        "paid_restore_v2_binding_digest": digest,
        "paid_restore_v2_binding_ref_digest": digest,
        "paid_restore_v2_closure_snapshot_digest": digest,
        "corrected_bundle_root": str(bundle_root.resolve()),
        "corrected_bundle_digest": digest,
        "planning_artifact_root": str(planning_root.resolve()),
        "provider_budget_authority_digest": digest,
        "selection": "representative",
        "lineage": {
            "sealed_inventory": {
                "root": str(inventory_root.resolve()),
                "inventory_digest": digest,
                "manifest_digest": digest,
            },
            "current_source_rebind": {
                "root": str(rebind_root.resolve()),
                "binding_digest": digest,
                "ref_digest": digest,
            },
            "current_provider_zero_preflight": {
                "path": str(current_preflight_path.resolve()),
                "report_digest": digest,
                "file_sha256": preflight_sha256,
            },
        },
        "source_validation_digest": digest,
        "source_snapshot_digest": digest,
        "coverage_digest": digest,
        "full_budget_digest": digest,
        "projection_digest": digest,
        "hard_limits_digest": digest,
        "catalog_digest": digest,
        "provider_config_digests": {"exp1": digest},
        "source_exp1_inventory_plan_digest": digest,
        "provider_calls_made": 0,
        "binding_digest": digest,
    }
    reference_ref = {"ref_digest": digest}
    monkeypatch.setattr(
        pipeline,
        "_load_provider_zero_reference_publication",
        lambda _root: (reference, reference_ref),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_results_first_paid_source_rebind_v1",
        lambda _root: (
            {"report_digest": digest},
            {
                "binding_digest": digest,
                "base_binding_digest": digest,
                "base_binding_ref_digest": digest,
                "snapshot_digest": digest,
            },
            {"ref_digest": digest},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_results_first_paid_restore_binding_v2",
        lambda _root: (
            {"binding_digest": digest},
            {"ref_digest": digest, "snapshot_digest": digest},
        ),
    )
    current_preflight_reads: list[Path] = []

    def read_current_preflight(path: Path) -> bytes:
        current_preflight_reads.append(path.resolve())
        return b"preflight"

    monkeypatch.setattr(Path, "read_bytes", read_current_preflight)

    class ExpectedV2Route(RuntimeError):
        pass

    def v2_loader(**kwargs: object) -> object:
        assert Path(str(kwargs["frozen_snapshot_root"])).resolve() == (
            paid_restore_v2_root.resolve()
        )
        assert Path(str(kwargs["expected_preflight"])).resolve() == (
            base_preflight_path.resolve()
        )
        raise ExpectedV2Route("strict v2 loader reached")

    monkeypatch.setattr(
        pipeline,
        "load_results_first_paid_execution_authority_v2",
        v2_loader,
    )
    with pytest.raises(ExpectedV2Route, match="strict v2 loader reached"):
        pipeline._load_paid_reference_execution_authority(
            paid_reference_binding_root=tmp_path / "reference",
            frozen_snapshot_root=paid_restore_v2_root,
            frozen_prepared_inventory_root=inventory_root,
            expected_preflight=base_preflight_path,
            source_rebind_root=rebind_root,
            plan_bundle_root=bundle_root,
            planning_artifact_root=planning_root,
            output_root=tmp_path / "output",
            selection="representative",
            resume=False,
            expected_provider_budget_digest=digest,
        )
    assert current_preflight_reads == [current_preflight_path.resolve()]


@pytest.mark.parametrize(
    "drift_field",
    ("hard_limits_digest", "source_exp1_inventory_plan_digest"),
)
def test_paid_reference_cold_loader_validates_hard_limits_before_paid_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_field: str,
) -> None:
    from tokenshare.experiments.paper_budget import (
        derive_results_first_provider_budget,
    )
    from tokenshare.experiments.paper_models import digest_json

    authority = _small_full_paid_restore_authority(tmp_path / "authority")
    provider_budget = derive_results_first_provider_budget(
        exp1_acquisition_budget=authority.bundle.full_budget,
        exp5_execution_projection=(
            pipeline._selection_exact_exp5_execution_projection(authority)
        ),
    )
    authority = replace(authority, provider_budget_authority=provider_budget)
    identity_digest = "sha256:" + "1" * 64
    paid_restore_v2_root = tmp_path / "paid-restore-v2"
    inventory_root = tmp_path / "inventory"
    bundle_root = tmp_path / "bundle"
    planning_root = tmp_path / "planning"
    rebind_root = tmp_path / "rebind"
    base_preflight_path = tmp_path / "base-preflight.json"
    current_preflight_path = (
        rebind_root / pipeline._PAID_SOURCE_REBIND_PREFLIGHT_NAME
    )
    current_preflight_path.parent.mkdir()
    current_preflight_bytes = b'{"provider_calls_made":0}\n'
    current_preflight_path.write_bytes(current_preflight_bytes)
    current_preflight_sha256 = (
        "sha256:" + sha256(current_preflight_bytes).hexdigest()
    )
    current_preflight_report_digest = "sha256:" + "2" * 64
    reference = {
        "paid_restore_v2_root": str(paid_restore_v2_root.resolve()),
        "paid_restore_v2_binding_digest": identity_digest,
        "paid_restore_v2_binding_ref_digest": identity_digest,
        "paid_restore_v2_closure_snapshot_digest": identity_digest,
        "corrected_bundle_root": str(bundle_root.resolve()),
        "corrected_bundle_digest": authority.bundle.bundle_digest,
        "planning_artifact_root": str(planning_root.resolve()),
        "provider_budget_authority_digest": provider_budget.authority_digest,
        "selection": "representative",
        "lineage": {
            "sealed_inventory": {
                "root": str(inventory_root.resolve()),
                "inventory_digest": authority.full_prepared_inventory.inventory_digest,
            },
            "current_source_rebind": {
                "root": str(rebind_root.resolve()),
                "binding_digest": identity_digest,
                "ref_digest": identity_digest,
            },
            "current_provider_zero_preflight": {
                "path": str(current_preflight_path.resolve()),
                "report_digest": current_preflight_report_digest,
                "file_sha256": current_preflight_sha256,
            },
        },
        "source_validation_digest": authority.source_validation_digest,
        "source_snapshot_digest": authority.full_snapshot.snapshot_digest,
        "coverage_digest": authority.coverage.coverage_digest,
        "full_budget_digest": authority.full_budget.budget_digest,
        "projection_digest": authority.execution_budget_projection.projection_digest,
        "hard_limits_digest": digest_json(dict(authority.hard_limits)),
        "catalog_digest": authority.catalog_manifest.catalog_digest,
        "provider_config_digests": pipeline._closure_snapshot_config_digests(
            authority.ai_api_configs
        ),
        "source_exp1_inventory_plan_digest": digest_json(
            authority.bundle.to_dict()["semantic_inventory_plan"]
        ),
        "binding_digest": identity_digest,
    }
    monkeypatch.setattr(
        pipeline,
        "_load_provider_zero_reference_publication",
        lambda _root: (reference, {"ref_digest": identity_digest}),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_results_first_paid_source_rebind_v1",
        lambda _root: (
            {
                "report_digest": current_preflight_report_digest,
                "file_sha256": current_preflight_sha256,
            },
            {
                "binding_digest": identity_digest,
                "base_binding_digest": identity_digest,
                "base_binding_ref_digest": identity_digest,
                "snapshot_digest": identity_digest,
            },
            {"ref_digest": identity_digest},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_results_first_paid_restore_binding_v2",
        lambda _root: (
            {"binding_digest": identity_digest},
            {"ref_digest": identity_digest, "snapshot_digest": identity_digest},
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_paid_execution_authority_v2",
        lambda **_kwargs: authority,
    )
    boundary_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        boundary_calls.append("forbidden")
        raise AssertionError("pre-provider cold loader crossed the paid boundary")

    for name in (
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        "_PAID_LAUNCH_ATTESTATION_WRITER",
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
    ):
        monkeypatch.setattr(pipeline, name, forbidden, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    output_root = tmp_path / "reserved-output"
    kwargs = {
        "paid_reference_binding_root": tmp_path / "reference",
        "frozen_snapshot_root": paid_restore_v2_root,
        "frozen_prepared_inventory_root": inventory_root,
        "expected_preflight": base_preflight_path,
        "source_rebind_root": rebind_root,
        "plan_bundle_root": bundle_root,
        "planning_artifact_root": planning_root,
        "output_root": output_root,
        "selection": "representative",
        "resume": False,
        "expected_provider_budget_digest": provider_budget.authority_digest,
    }

    loaded = pipeline._load_paid_reference_execution_authority(**kwargs)
    assert type(loaded) is pipeline.ResultsFirstExecutionAuthority
    assert boundary_calls == []
    assert not output_root.exists()
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None

    reference[drift_field] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="returned authority lineage drift"):
        pipeline._load_paid_reference_execution_authority(**kwargs)
    assert boundary_calls == []
    assert not output_root.exists()
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None


def test_paid_launch_real_cli_cold_gate_then_same_process_attestation_then_first_network_bomb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    r13_full_stage_a: dict[str, object],
) -> None:
    from tests.phase7_fixtures import make_config_dict

    (
        reference_root,
        fresh_inventory_root,
        fresh_inventory_digest,
        bundle_root,
        planning_root,
        provider_budget,
        source_rebind_root,
        current_preflight_path,
        reference_binding_digest,
        reference_binding_ref_digest,
        source_rebind_binding_digest,
        source_rebind_ref_digest,
        current_preflight_report_digest,
    ) = _r13_persist_real_reference_binding(tmp_path, r13_full_stage_a)
    assert r13_full_stage_a_build_count() == 1
    key_path = tmp_path / "ai_api_smoke.local.json"
    deepseek_marker = "r13-deepseek-secret-never-render"
    siliconflow_marker = "r13-siliconflow-secret-never-render"
    config = make_config_dict()
    template = config["entries"][0]
    config["entries"] = [
        {
            **template,
            "entry_id": "deepseek_injection_only",
            "enabled": False,
            "api_key_env": "DEEPSEEK_API_KEY",
            "api_key": deepseek_marker,
        },
        {
            **template,
            "entry_id": "siliconflow_injection_only",
            "enabled": False,
            "api_key_env": "SILICONFLOW_API_KEY",
            "api_key": siliconflow_marker,
        },
    ]
    key_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    events: list[str] = []
    network_boundary_calls: list[int] = []
    required_callbacks = (
        "_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER",
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        "_PAID_LAUNCH_ATTESTATION_WRITER",
        "_mint_results_first_paid_launch_key_readiness",
        "consume_results_first_paid_launch_key_readiness",
    )
    assert all(callable(getattr(pipeline, name, None)) for name in required_callbacks)
    originals = {name: getattr(pipeline, name) for name in required_callbacks}

    def cold_loader_spy(*args: object, **kwargs: object) -> object:
        authority = originals["_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER"](
            *args,
            **kwargs,
        )
        assert type(authority) is pipeline.ResultsFirstExecutionAuthority
        assert authority.full_prepared_inventory.inventory_digest == fresh_inventory_digest
        assert Path(kwargs["paid_reference_binding_root"]).resolve() == (
            reference_root.resolve()
        )
        assert Path(kwargs["frozen_prepared_inventory_root"]).resolve() == (
            fresh_inventory_root.resolve()
        )
        assert authority.provider_zero_reference_binding_digest == reference_binding_digest
        assert (
            authority.provider_zero_reference_binding_ref_digest
            == reference_binding_ref_digest
        )
        assert authority.source_rebind_binding_digest == source_rebind_binding_digest
        assert authority.source_rebind_ref_digest == source_rebind_ref_digest
        assert (
            authority.current_provider_zero_preflight_report_digest
            == current_preflight_report_digest
        )
        events.append("cold_typed_authority")
        return authority

    def lock_spy(*args: object, **kwargs: object) -> object:
        assert events == ["cold_typed_authority"]
        lock = originals["_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER"](
            *args,
            **kwargs,
        )
        events.append("exclusive_lock")
        return lock

    def local_config_spy(*args: object, **kwargs: object) -> object:
        assert events == ["cold_typed_authority", "exclusive_lock"]
        loaded = originals["_PAID_LAUNCH_LOCAL_CONFIG_LOADER"](*args, **kwargs)
        events.append("same_process_local_config")
        return loaded

    def attestation_writer_spy(*args: object, **kwargs: object) -> object:
        assert events == [
            "cold_typed_authority",
            "exclusive_lock",
            "same_process_local_config",
        ]
        result = originals["_PAID_LAUNCH_ATTESTATION_WRITER"](*args, **kwargs)
        events.append("sanitized_attestation_persist")
        return result

    def mint_spy(*args: object, **kwargs: object) -> object:
        assert events[-1] == "sanitized_attestation_persist"
        result = originals["_mint_results_first_paid_launch_key_readiness"](
            *args,
            **kwargs,
        )
        events.append("registry_mint")
        return result

    def consume_spy(*args: object, **kwargs: object) -> object:
        assert events[-1] == "registry_mint"
        result = originals["consume_results_first_paid_launch_key_readiness"](
            *args,
            **kwargs,
        )
        events.append("registry_consume")
        return result

    for name, replacement in (
        ("_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER", cold_loader_spy),
        ("_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER", lock_spy),
        ("_PAID_LAUNCH_LOCAL_CONFIG_LOADER", local_config_spy),
        ("_PAID_LAUNCH_ATTESTATION_WRITER", attestation_writer_spy),
        ("_mint_results_first_paid_launch_key_readiness", mint_spy),
        ("consume_results_first_paid_launch_key_readiness", consume_spy),
    ):
        monkeypatch.setattr(pipeline, name, replacement)

    class FirstNetworkBomb:
        def post_chat_completion(self, **_kwargs: object) -> object:
            network_boundary_calls.append(1)
            assert events == [
                "cold_typed_authority",
                "exclusive_lock",
                "same_process_local_config",
                "sanitized_attestation_persist",
                "registry_mint",
                "registry_consume",
            ]
            assert not pipeline._PAID_LAUNCH_READINESS_REGISTRY
            assert pipeline.paid_launch_lock_is_held(fresh_attestation_root)
            assert os.environ["DEEPSEEK_API_KEY"] == deepseek_marker
            assert os.environ["SILICONFLOW_API_KEY"] == siliconflow_marker
            attestation_path = (
                fresh_attestation_root / pipeline.PAID_LAUNCH_ATTESTATION_NAME
            )
            assert attestation_path.is_file()
            raise OSError("provider network bomb after paid readiness consume")

    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_ACQUISITION_TRANSPORT_FACTORY",
        lambda: FirstNetworkBomb(),
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_EXP5_TRANSPORT_FACTORY",
        lambda: pytest.fail("Exp5 cannot start after Exp1 first-network bomb"),
    )
    common = [
        "run-results-first",
        "--planning-artifact-root",
        str(planning_root),
        "--plan-bundle-root",
        str(bundle_root),
        "--new-run",
        "--allow-provider-calls",
        "--frozen-closure-snapshot-root",
        str(Path(r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-v2-binding")),
        "--frozen-prepared-inventory-root",
        str(fresh_inventory_root),
        "--expected-preflight",
        str(
            Path(
                r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-preflight\paid_representative_projection_preflight.v1.json"
            )
        ),
        "--source-rebind-root",
        str(source_rebind_root),
        "--paid-reference-binding-root",
        str(reference_root),
        "--expected-provider-budget-digest",
        provider_budget.authority_digest,
        "--selection",
        "representative",
        "--paid-launch-key-config",
        str(key_path),
    ]

    occupied_output = tmp_path / "occupied-paid-output"
    occupied_output.mkdir()
    cold_attestation_root = tmp_path / "cold-rejected-attestation"
    first_exit = pipeline.main(
        [
            *common,
            "--output-root",
            str(occupied_output),
            "--paid-launch-attestation-root",
            str(cold_attestation_root),
        ]
    )
    first_rendered = capsys.readouterr()
    assert first_exit == 3
    assert events == []
    assert network_boundary_calls == []
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None
    assert not cold_attestation_root.exists()
    assert not pipeline._PAID_LAUNCH_READINESS_REGISTRY

    fresh_output = tmp_path / "fresh-paid-output"
    fresh_attestation_root = tmp_path / "fresh-paid-attestation"
    paid_argv = [
        *common,
        "--output-root",
        str(fresh_output),
        "--paid-launch-attestation-root",
        str(fresh_attestation_root),
    ]
    second_exit = pipeline.main(paid_argv)
    second_rendered = capsys.readouterr()

    assert second_exit == 3
    if not network_boundary_calls:
        def sanitized_rendered_json(value: object) -> Mapping[str, object]:
            raw = str(getattr(value, "out", "") or getattr(value, "err", ""))
            try:
                body = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return {"json_parse_failed": True, "rendered_size": len(raw)}
            if not isinstance(body, Mapping):
                return {"json_type": type(body).__name__}
            allowed = (
                "failure_kind",
                "message",
                "status",
                "evidence_class",
                "provider_calls",
                "total_current_provider_calls",
            )
            return {key: body.get(key) for key in allowed if key in body}

        pytest.fail(
            json.dumps(
                {
                    "sanitized_second_rendered": sanitized_rendered_json(second_rendered),
                    "events": events,
                    "fresh_output_exists": fresh_output.exists(),
                    "fresh_attestation_exists": fresh_attestation_root.exists(),
                    "network_boundary_calls": len(network_boundary_calls),
                },
                sort_keys=True,
            )
        )
    assert events[-1] == "registry_consume"
    assert not pipeline._PAID_LAUNCH_READINESS_REGISTRY
    attestation_path = fresh_attestation_root / pipeline.PAID_LAUNCH_ATTESTATION_NAME
    lock_path = fresh_attestation_root / pipeline.PAID_LAUNCH_LOCK_NAME
    assert attestation_path.is_file()
    assert lock_path.is_file()
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    assert attestation["schema_version"] == "tokenshare.paid_launch_key_readiness.v1"
    assert attestation["scope"] == "results_first_paid_representative"
    assert attestation["statuses"] == {
        "DEEPSEEK_API_KEY": "SET",
        "SILICONFLOW_API_KEY": "SET",
    }
    assert type(attestation["provider_calls_made"]) is int
    assert attestation["provider_calls_made"] == 0
    rendered = (
        first_rendered.out
        + first_rendered.err
        + second_rendered.out
        + second_rendered.err
        + json.dumps(attestation, sort_keys=True)
        + " ".join(common)
    )
    assert deepseek_marker not in rendered
    assert siliconflow_marker not in rendered
    assert dict(provider_budget.exp2_4)["current_provider_calls"] == 0
    assert dict(provider_budget.global_new_paid) == {
        "calls": 202,
        "tokens": 34_385_749,
        "cny": "242.762815",
    }
    cli_text = " ".join(paid_argv)
    assert str(fresh_inventory_root) in cli_text
    assert str(reference_root) in cli_text
    assert str(fresh_attestation_root) in cli_text
    assert str(source_rebind_root) in cli_text
    assert str(current_preflight_path) not in cli_text


@pytest.mark.parametrize(
    "overlap_kind",
    (
        "attestation_under_reference",
        "attestation_under_inventory",
        "output_under_reference",
        "attestation_under_output",
    ),
)
def test_paid_launch_rejects_reference_inventory_output_attestation_overlap_before_cold_or_key_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap_kind: str,
) -> None:
    topology_validator = getattr(
        pipeline,
        "_validate_results_first_paid_launch_topology",
        None,
    )
    assert callable(topology_validator), "paid launch topology validator is absent"
    reference_root = tmp_path / "reference-binding"
    inventory_root = tmp_path / "fresh-inventory"
    output_root = tmp_path / "fresh-paid-output"
    attestation_root = tmp_path / "fresh-paid-attestation"
    if overlap_kind == "attestation_under_reference":
        attestation_root = reference_root / "attestation"
    elif overlap_kind == "attestation_under_inventory":
        attestation_root = inventory_root / "attestation"
    elif overlap_kind == "output_under_reference":
        output_root = reference_root / "paid-output"
    else:
        attestation_root = output_root / "attestation"

    events: list[str] = []

    def topology_spy(*args: object, **kwargs: object) -> object:
        events.append("topology")
        return topology_validator(*args, **kwargs)

    def forbidden(name: str):
        def callback(*_args: object, **_kwargs: object) -> object:
            events.append(name)
            raise AssertionError(f"unsafe paid topology reached {name}")

        return callback

    monkeypatch.setattr(
        pipeline,
        "_validate_results_first_paid_launch_topology",
        topology_spy,
    )
    for name in (
        "_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER",
        "_PAID_LAUNCH_SINGLE_INSTANCE_ACQUIRER",
        "_PAID_LAUNCH_LOCAL_CONFIG_LOADER",
        "_PAID_LAUNCH_ATTESTATION_WRITER",
        "_mint_results_first_paid_launch_key_readiness",
    ):
        monkeypatch.setattr(pipeline, name, forbidden(name), raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    source_rebind_root = Path(
        r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r12-source-rebind"
    )
    exit_code = pipeline.main(
        [
            "run-results-first",
            "--planning-artifact-root",
            r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-planning",
            "--plan-bundle-root",
            r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260816-r10d-corrected-exp1-bundle",
            "--output-root",
            str(output_root),
            "--new-run",
            "--allow-provider-calls",
            "--frozen-closure-snapshot-root",
            r"E:\TokenEcnomic\TokenShareWorktrees\exp-full-run\local\paid-representative-20260814-r5-restore-binding",
            "--frozen-prepared-inventory-root",
            str(inventory_root),
            "--expected-preflight",
            str(source_rebind_root / "paid_source_rebind_preflight.v1.json"),
            "--source-rebind-root",
            str(source_rebind_root),
            "--paid-reference-binding-root",
            str(reference_root),
            "--expected-provider-budget-digest",
            "sha256:9f3e0ffbb7f27a5c970a27a1b9c5d62ceb5dd0634dd2dc1a308c114471540d8e",
            "--selection",
            "representative",
            "--paid-launch-key-config",
            str(tmp_path / "must-not-read-secret-config.json"),
            "--paid-launch-attestation-root",
            str(attestation_root),
        ]
    )

    assert exit_code == 3
    assert events == ["topology"]
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("SILICONFLOW_API_KEY") is None
    assert not getattr(pipeline, "_PAID_LAUNCH_READINESS_REGISTRY", {})
    assert not output_root.exists()
    assert not attestation_root.exists()
def test_resume_acquisition_consumes_supervised_no_response_closure_before_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_response_bank_acquisition import (
        ScriptedExactTransport,
        _crash_at,
        _orchestrator,
        _prepared,
        _request,
        _row,
        _supervised_no_response_stop_evidence,
    )
    from tokenshare.experiments import paper_response_bank

    prepared = tuple(
        _prepared(unit=f"resume-authority-{index}", marker=str(index))
        for index in range(90)
    )
    rows = tuple(_row(value) for value in prepared)
    requests = tuple(
        _request(row, value)
        for row, value in zip(rows, prepared, strict=True)
    )
    target_requests = (requests[11], requests[73])
    target_ids = tuple(
        request.inventory_row.inventory_entry_id for request in target_requests
    )
    transport = ScriptedExactTransport([])
    acquisition_root = tmp_path / "acquisition"
    crashed, ledger = _orchestrator(
        acquisition_root,
        rows,
        transport,
        crash_hook=_crash_at("dispatch_intent"),
    )
    for request in target_requests:
        with pytest.raises(RuntimeError, match="crash:dispatch_intent"):
            crashed.acquire(request)
    resumed, _ = _orchestrator(
        acquisition_root,
        rows,
        transport,
        mode="resume",
        ledger=ledger,
        secret_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("closure routing cannot resolve provider secret")
        ),
    )
    closure_root = tmp_path / "supervised-no-response-closure"
    paper_response_bank.persist_supervised_no_response_closure_authority(
        closure_root=closure_root,
        orchestrator=resumed,
        requests=target_requests,
        target_inventory_entry_ids=target_ids,
        stop_evidence=_supervised_no_response_stop_evidence(target_ids),
    )
    primary_calls: list[tuple[tuple[object, ...], int, tuple[str, ...]]] = []

    def record_primary(values, *, max_in_flight):
        values = tuple(values)
        remaining = tuple(
            request.inventory_row.inventory_entry_id
            for request in values
            if resumed._load_entry(request.inventory_row.inventory_entry_id) is None
        )
        primary_calls.append((values, max_in_flight, remaining))
        return SimpleNamespace(status="complete", results=())

    monkeypatch.setattr(resumed, "acquire_all", record_primary)

    batch = pipeline._acquire_response_bank_after_supervised_no_response_closure(
        orchestrator=resumed,
        requests=requests,
        max_in_flight=3,
        resume=True,
        supervised_no_response_closure_root=closure_root,
    )

    assert batch.status == "complete"
    assert len(primary_calls) == 1
    assert primary_calls[0][:2] == (requests, 3)
    assert len(primary_calls[0][2]) == 88
    assert not set(primary_calls[0][2]).intersection(target_ids)
    assert transport.calls == []
    assert all(
        resumed._load_entry(inventory_entry_id).terminal_kind == "provider_failure"
        for inventory_entry_id in target_ids
    )
    with pytest.raises(ValueError, match="missing"):
        pipeline._acquire_response_bank_after_supervised_no_response_closure(
            orchestrator=resumed,
            requests=tuple(
                request
                for request in requests
                if request.inventory_row.inventory_entry_id != target_ids[0]
            ),
            max_in_flight=3,
            resume=True,
            supervised_no_response_closure_root=closure_root,
        )
    with pytest.raises(ValueError, match="duplicate"):
        pipeline._acquire_response_bank_after_supervised_no_response_closure(
            orchestrator=resumed,
            requests=(*requests, target_requests[0]),
            max_in_flight=3,
            resume=True,
            supervised_no_response_closure_root=closure_root,
        )
    assert "_acquire_response_bank_after_supervised_no_response_closure" in inspect.getsource(
        pipeline._acquire_bank_adapter
    )
    assert "_acquire_response_bank_after_supervised_no_response_closure" in inspect.getsource(
        pipeline._acquire_representative_response_bank
    )

    with pytest.raises(ValueError, match="requires resume"):
        pipeline._acquire_response_bank_after_supervised_no_response_closure(
            orchestrator=resumed,
            requests=requests,
            max_in_flight=3,
            resume=False,
            supervised_no_response_closure_root=closure_root,
        )


def test_provider_zero_supervised_no_response_producer_is_production_reachable_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.executors.response_bank import canonical_digest
    from tokenshare.experiments import paper_response_bank
    from tokenshare.experiments.paper_budget import ResultsFirstProviderBudgetAuthority
    from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
    from tokenshare.experiments.paper_response_bank import (
        AcquisitionRequest,
        FrozenPricing,
        build_semantic_inventory,
        create_acquisition_plan_bundle,
        establish_results_first_acquisition_authorization,
    )
    from tests.experiments.test_paper_response_bank import _candidate

    candidates = tuple(
        _candidate(
            experiment_id="exp1_real_ai_feasibility",
            condition_id=f"exp1-condition-{index}",
            case_id=f"case-{index}",
            case_digest="sha256:" + str(index + 1) * 64,
            unit_id=f"unit-{index}",
            body_marker=f"body-{index}",
        )
        for index in range(2)
    )
    plan = build_semantic_inventory(candidates)
    by_digest = {
        candidate.prepared_request.inference_request_digest: candidate
        for candidate in candidates
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
            requested_at="2026-08-16T00:00:00Z",
        )
        for row in plan.rows
    )
    bundle = create_acquisition_plan_bundle(
        tmp_path / "bundle",
        authorized_plan_digest="sha256:" + "a" * 64,
        profile_digest="sha256:" + "b" * 64,
        semantic_inventory_plan=plan,
        acquisition_requests=requests,
    )
    paid_output = tmp_path / "paid-output"
    establish_results_first_acquisition_authorization(
        bundle=bundle,
        output_root=paid_output / "acquisition",
        output_mode="new_run",
        allow_provider_calls=True,
    )
    budget_body = {
        "schema_version": "tokenshare.results_first_provider_budget.v1",
        "exp1": {"calls": 2, "tokens": 600_000, "cny": "2.50"},
        "exp2_4": {
            "current_provider_calls": 0,
            "current_tokens": 0,
            "current_cny": "0",
        },
        "exp5": {"calls": 0, "tokens": 0, "cny": "0"},
        "global_new_paid": {"calls": 2, "tokens": 600_000, "cny": "2.50"},
        "provider_calls_made": 0,
    }
    budget = ResultsFirstProviderBudgetAuthority(
        exp1=budget_body["exp1"],
        exp2_4=budget_body["exp2_4"],
        exp5=budget_body["exp5"],
        global_new_paid=budget_body["global_new_paid"],
        authority_digest=canonical_digest(budget_body),
    )
    authority = object.__new__(pipeline.ResultsFirstExecutionAuthority)
    for name, value in {
        "bundle": bundle,
        "provider_budget_authority": budget,
        "output_root": paid_output,
        "resume": True,
        "provider_calls_made": 0,
    }.items():
        object.__setattr__(authority, name, value)
    loader_calls: list[dict[str, object]] = []

    def load_authority(**kwargs: object):
        loader_calls.append(dict(kwargs))
        return authority

    persisted_requests: list[tuple[object, ...]] = []
    closure = SimpleNamespace(
        authority_digest="sha256:" + "c" * 64,
        stop_evidence_digest="sha256:" + "d" * 64,
        target_inventory_entry_ids=tuple(
            request.inventory_row.inventory_entry_id for request in requests
        ),
        provider_calls_made=0,
    )

    def persist(**kwargs: object):
        persisted_requests.append(tuple(kwargs["requests"]))
        assert kwargs["target_inventory_entry_ids"] == closure.target_inventory_entry_ids
        assert kwargs["orchestrator"].invocation_mode == "resume"
        return closure

    monkeypatch.setattr(
        pipeline,
        "_PAID_REFERENCE_EXECUTION_AUTHORITY_LOADER",
        load_authority,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "persist_supervised_no_response_closure_authority",
        persist,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "load_supervised_no_response_closure_authority",
        lambda _root: closure,
    )
    target_ids = list(closure.target_inventory_entry_ids)
    stop_evidence_path = tmp_path / "stop-evidence.json"
    stop_evidence_path.write_text(
        json.dumps({"target_inventory_entry_ids": target_ids}),
        encoding="utf-8",
    )
    closure_root = tmp_path / "closure"

    def paid_output_snapshot() -> dict[str, bytes | None]:
        return {
            path.relative_to(paid_output).as_posix(): (
                None if path.is_dir() else path.read_bytes()
            )
            for path in sorted(paid_output.rglob("*"))
        }

    missing_snapshot = paid_output_snapshot()
    with pytest.raises(ValueError, match="existing read-only acquisition budget ledger"):
        pipeline.create_provider_zero_supervised_no_response_closure(
            frozen_snapshot_root=tmp_path / "snapshot",
            frozen_prepared_inventory_root=tmp_path / "inventory",
            expected_preflight=tmp_path / "preflight.json",
            source_rebind_root=tmp_path / "source-rebind",
            plan_bundle_root=tmp_path / "plan-bundle",
            planning_artifact_root=tmp_path / "planning",
            paid_reference_binding_root=tmp_path / "reference",
            paid_output_root=paid_output,
            stop_evidence_path=stop_evidence_path,
            fresh_closure_root=closure_root,
            selection="representative",
            expected_provider_budget_digest=budget.authority_digest,
        )
    assert paid_output_snapshot() == missing_snapshot
    assert not closure_root.exists()
    ledger_path = paid_output / "acquisition" / "acquisition_budget.v1.sqlite3"
    ledger_path.write_bytes(b"not-a-sqlite-ledger")
    corrupt_snapshot = paid_output_snapshot()
    with pytest.raises(ValueError, match="existing read-only acquisition budget ledger"):
        pipeline.create_provider_zero_supervised_no_response_closure(
            frozen_snapshot_root=tmp_path / "snapshot",
            frozen_prepared_inventory_root=tmp_path / "inventory",
            expected_preflight=tmp_path / "preflight.json",
            source_rebind_root=tmp_path / "source-rebind",
            plan_bundle_root=tmp_path / "plan-bundle",
            planning_artifact_root=tmp_path / "planning",
            paid_reference_binding_root=tmp_path / "reference",
            paid_output_root=paid_output,
            stop_evidence_path=stop_evidence_path,
            fresh_closure_root=closure_root,
            selection="representative",
            expected_provider_budget_digest=budget.authority_digest,
        )
    assert paid_output_snapshot() == corrupt_snapshot
    assert not closure_root.exists()
    ledger_path.unlink()
    valid_ledger = PaperBudgetLedger(ledger_path, limits=bundle.full_budget.to_limits())
    valid_ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )

    terminal = pipeline.create_provider_zero_supervised_no_response_closure(
        frozen_snapshot_root=tmp_path / "snapshot",
        frozen_prepared_inventory_root=tmp_path / "inventory",
        expected_preflight=tmp_path / "preflight.json",
        source_rebind_root=tmp_path / "source-rebind",
        plan_bundle_root=tmp_path / "plan-bundle",
        planning_artifact_root=tmp_path / "planning",
        paid_reference_binding_root=tmp_path / "reference",
        paid_output_root=paid_output,
        stop_evidence_path=stop_evidence_path,
        fresh_closure_root=closure_root,
        selection="representative",
        expected_provider_budget_digest=budget.authority_digest,
    )

    assert len(loader_calls) == 3
    assert all(call["resume"] is True for call in loader_calls)
    assert all(
        call["expected_provider_budget_digest"] == budget.authority_digest
        for call in loader_calls
    )
    assert persisted_requests == [requests]
    assert terminal == {
        "schema_version": "tokenshare.provider_zero_supervised_no_response_terminal.v1",
        "status": "READY_PROVIDER_ZERO_SUPERVISED_NO_RESPONSE",
        "authority_digest": closure.authority_digest,
        "stop_evidence_digest": closure.stop_evidence_digest,
        "target_inventory_entry_ids": target_ids,
        "provider_calls": 0,
    }
    assert not closure_root.exists()
def test_paid_restore_production_source_paths_include_trace_projection_builder() -> None:
    paths = pipeline._paid_restore_production_source_paths()
    repo_root = Path(__file__).resolve().parents[2]

    assert len(paths) == 8
    assert set(paths) == {
        repo_root / "src/tokenshare/executors/response_bank.py",
        repo_root / "src/tokenshare/experiments/paper_budget.py",
        repo_root / "src/tokenshare/experiments/paper_exp1_trace_reuse.py",
        repo_root / "src/tokenshare/experiments/paper_formal_plan.py",
        repo_root / "src/tokenshare/experiments/paper_formal_runner.py",
        repo_root / "src/tokenshare/experiments/paper_models.py",
        repo_root / "src/tokenshare/experiments/paper_response_bank.py",
        repo_root / "src/tokenshare/experiments/run_paper_pipeline.py",
    }
    assert all(path.is_file() and not path.is_symlink() for path in paths)


def test_exp4_excluded_scope_loader_derives_representative_authority_without_provider(
    tmp_path: Path,
) -> None:
    """115-root scope 必须由已冻结的 R13 输入重新派生，而不是重用旧 authority。"""

    root = Path(__file__).resolve().parents[2]
    authority = pipeline.load_exp4_excluded_results_first_execution_authority_v1(
        scope_preparation_root=(
            root
            / "local"
            / "paid-representative-20260818-r31-exp1-exp3-exp5-preparation"
        ),
        plan_bundle_root=(
            root
            / "local"
            / "paid-representative-20260818-r31-exp1-exp3-exp5-bundle"
        ),
        planning_artifact_root=(
            root
            / "local"
            / "paid-representative-20260818-r31-exp1-exp3-exp5-planning"
        ),
        output_root=tmp_path / "fresh-small-output",
        selection="representative_exp1_exp3_exp5",
        resume=False,
    )

    assert authority.coverage.condition_count == 115
    assert authority.coverage.root_run_count == 115
    assert tuple(
        dict.fromkeys(condition.experiment_id for condition in authority.coverage.conditions)
    ) == (
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
        "exp3_real_ai_fault_recovery",
        "exp5_real_ai_model_endpoint_comparison",
    )
    assert authority.provider_budget_authority.to_dict()["provider_calls_made"] == 0
    assert not authority.output_root.exists()
    result = pipeline._execute_results_first_service_input(
        request=SimpleNamespace(output_root=authority.output_root, profile=PROFILE),
        value=pipeline.ResultsFirstServiceInput(
            authority=authority,
            selection="representative_exp1_exp3_exp5",
            plan_only=True,
            allow_provider_calls=False,
            external_bank_root=None,
            resume=False,
        ),
    )
    assert result["status"] == "ready"
    assert result["condition_count"] == 115
    assert result["root_run_count"] == 115
    assert result["provider_calls"] == 0


def test_exp4_excluded_scope_loader_refreshes_r13_exp5_pricing_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13 bank 可复用，但其旧 Exp5 binding 不得进入新 execution authority。"""

    root = Path(__file__).resolve().parents[2]
    preparation_root = (
        root
        / "local"
        / "paid-representative-20260818-r31-exp1-exp3-exp5-preparation"
    )
    preparation = json.loads(
        (preparation_root / "exp4_excluded_scope_preparation.v1.json").read_text(
            encoding="utf-8"
        )
    )
    source_snapshot = pipeline.load_results_first_closure_replay_snapshot(
        output_root=preparation["source_snapshot_root"],
    )
    stale_binding = source_snapshot.ai_api_configs[
        APPROVED_ENDPOINT_BINDINGS_KEY
    ]["exp5_real_ai_model_endpoint_comparison"]
    assert stale_binding.get("pricing_freshness_authority") is None

    # 保留 loader 的真实 manifest/snapshot/source-root 校验；只替换大 inventory
    # 解析和最终 authority 物化，避免这个单测重建 40k 条历史 inventory。
    from tokenshare.experiments import paper_budget, paper_formal_plan
    from tokenshare.experiments.paper_formal_plan import (
        derive_paper_formal_exp4_excluded_coverage,
    )
    from tokenshare.experiments import paper_response_bank
    from tokenshare.experiments.paper_exp1_trace_reuse import select_exp1_source_roots

    coverage = derive_paper_formal_exp4_excluded_coverage(
        snapshot=source_snapshot.coverage.source_snapshot,
        dispatch_plans=source_snapshot.full_dispatch_plans,
        catalog_manifest=source_snapshot.catalog_manifest,
        selection=str(preparation["base_selection"]),
    )
    source_roots = select_exp1_source_roots(
        full_roots=source_snapshot.coverage.source_snapshot.roots,
        selected_roots=coverage.roots,
    )
    records = [
        SimpleNamespace(
            condition=root.condition,
            case_id=root.case_id,
            planned_ai_unit_id=planned_ai_unit_id,
            provider_config_id="exp1_baseline_deepseek",
            model_entry_id="deepseek_v4_pro_exp1_baseline",
            provider_family="deepseek",
        )
        for root in source_roots
        for planned_ai_unit_id in root.planned_ai_unit_ids
    ]
    synthetic_inventory = SimpleNamespace(
        inventory_digest=preparation["prepared_inventory_digest"],
        records=tuple(records),
    )
    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        lambda **_kwargs: synthetic_inventory,
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: source_snapshot,
    )
    captured: dict[str, Mapping[str, object]] = {}

    def fake_prepare(**kwargs: object) -> object:
        captured["prepare"] = kwargs["ai_api_configs"]
        return object()

    def fake_build(**kwargs: object) -> object:
        captured["build"] = kwargs["ai_api_configs"]
        return SimpleNamespace(
            ai_api_configs=kwargs["ai_api_configs"],
            bundle=SimpleNamespace(acquisition_requests=()),
        )

    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        fake_prepare,
    )
    monkeypatch.setattr(
        pipeline,
        "build_results_first_execution_authority",
        fake_build,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        lambda **_kwargs: SimpleNamespace(
            acquisition_requests=(SimpleNamespace(),),
        ),
    )
    monkeypatch.setattr(
        paper_response_bank.FullAcquisitionBudget,
        "create",
        classmethod(lambda _cls, _requests: object()),
    )
    monkeypatch.setattr(
        paper_budget,
        "derive_results_first_provider_budget",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        pipeline,
        "_selection_exact_exp5_execution_projection",
        lambda _authority: object(),
    )
    monkeypatch.setattr(
        pipeline,
        "replace",
        lambda authority, **changes: SimpleNamespace(
            **{**vars(authority), **changes}
        ),
    )

    authority = pipeline.load_exp4_excluded_results_first_execution_authority_v1(
        scope_preparation_root=preparation_root,
        plan_bundle_root=(
            root
            / "local"
            / "paid-representative-20260818-r31-exp1-exp3-exp5-bundle"
        ),
        planning_artifact_root=(
            root
            / "local"
            / "paid-representative-20260818-r31-exp1-exp3-exp5-planning"
        ),
        output_root=tmp_path / "refreshed-pricing-output",
        selection="representative_exp1_exp3_exp5",
        resume=False,
    )

    refreshed_binding = authority.ai_api_configs[APPROVED_ENDPOINT_BINDINGS_KEY][
        "exp5_real_ai_model_endpoint_comparison"
    ]
    pricing_authority = refreshed_binding["pricing_freshness_authority"]
    assert refreshed_binding != stale_binding
    assert pricing_authority["pricing_freshness_as_of"] == EXP5_PRICING_FRESHNESS_AS_OF
    assert isinstance(pricing_authority["authority_digest"], str)
    assert authority.ai_api_configs["exp1_baseline_deepseek"] == source_snapshot.ai_api_configs[
        "exp1_baseline_deepseek"
    ]
    assert captured["prepare"] is authority.ai_api_configs
    assert captured["build"] is authority.ai_api_configs


def test_full_scope_loader_uses_approved_exp5_pricing_binding_for_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full 需在零 provider 的 authority 重建中消费已批准的 Exp5 定价绑定。"""

    from tokenshare.executors import ai_api_config
    from tokenshare.experiments import (
        paper_budget,
        paper_exp1_trace_reuse,
        paper_formal_plan,
        paper_model_policy,
        paper_response_bank,
        run_paper_experiments,
    )
    from tokenshare.experiments.paper_models import digest_json
    from tokenshare.experiments.paper_resource_accounting import FrozenPricing

    exp5_pricing_binding = {"binding": "approved-current-exp5-pricing"}
    exp5_budget = {
        "calls": 4,
        "tokens": 400,
        "cny": "4.00",
        "pricing_snapshot_digest_by_member": {"member": "sha256:pricing"},
        "pricing_authority_digest": "sha256:authority",
    }
    total_budget = {"calls": 5, "tokens": 500, "cny": "5.00"}
    approval_body = {
        "schema_version": "tokenshare.newfullrun_full_budget_approval_authority.v1",
        "selection": "full_exp1_exp3_exp5",
        "source_snapshot_digest": "sha256:snapshot",
        "prepared_inventory_digest": "sha256:inventory",
        "coverage_digest": "sha256:coverage",
        "provider_calls_made": 0,
        "exp5_online_budget": exp5_budget,
        "budget": {"total": total_budget},
        "exp1_acquisition_authority": {
            "budget": {"budget_digest": "sha256:exp1-budget"}
        },
    }
    approval = {
        **approval_body,
        "authority_digest": digest_json(approval_body),
    }
    preparation = {
        "schema_version": "tokenshare.exp4_excluded_results_first_preparation.v1",
        "selection": "full_exp1_exp3_exp5",
        "base_selection": "full",
        "included_experiment_ids": [
            "exp1_real_ai_feasibility",
            "exp2_real_ai_scalability",
            "exp3_real_ai_fault_recovery",
            "exp5_real_ai_model_endpoint_comparison",
        ],
        "excluded_experiment_ids": ["exp4_real_ai_protocol_ablation"],
        "exp4_excluded": True,
        "condition_count": 1,
        "root_run_count": 1,
        "selected_first_attempt_ai_unit_count": 1,
        "coverage_digest": "sha256:coverage",
        "source_snapshot_digest": "sha256:snapshot",
        "source_snapshot_root": str(tmp_path / "snapshot"),
        "source_prepared_inventory_root": str(tmp_path / "inventory"),
        "prepared_inventory_digest": "sha256:inventory",
        "source_bank_manifest_digest": "sha256:bank",
        "source_bank_terminal_entry_count": 1,
        "execution_projection_digest": "sha256:projection",
        "execution_projection": {"projection": "exact"},
        "preparation_plan_digest": "sha256:preparation-plan",
        "provider_calls_made": 0,
        "status": "ready_provider_zero_scope_prepared",
    }
    plan = {
        "selection": "full_exp1_exp3_exp5",
        "coverage_digest": "sha256:coverage",
        "source_snapshot_digest": "sha256:snapshot",
    }
    preparation["preparation_plan_digest"] = digest_json(plan)
    source_bank_root = tmp_path / "immutable-bank"
    bank_binding = {
        "selection": "full_exp1_exp3_exp5",
        "coverage_digest": "sha256:coverage",
        "source_bank_manifest_digest": "sha256:bank",
        "provider_calls_made": 0,
        "source_bank_root": str(source_bank_root),
    }
    bank_manifest = {
        "manifest_digest": "sha256:bank",
        "terminal_entry_count": 1,
    }
    source_entry = SimpleNamespace(
        enabled=True,
        entry_id="deepseek_v4_pro_exp1_baseline",
        api_key_env="DEEPSEEK_API_KEY",
        pricing={
            "currency": "CNY",
            "input_per_million_tokens": "2.0",
            "output_per_million_tokens": "3.0",
        },
    )
    source_config = SimpleNamespace(entries=(source_entry,))
    source_root = SimpleNamespace(
        condition=SimpleNamespace(condition_id="condition"),
        case_id="case",
        planned_ai_unit_ids=("unit",),
    )
    full_snapshot = SimpleNamespace(
        snapshot_digest="sha256:snapshot",
        roots=(source_root,),
        ai_api_configs={"exp1_baseline_deepseek": source_config},
    )
    coverage = SimpleNamespace(
        coverage_digest="sha256:coverage",
        condition_count=1,
        root_run_count=1,
        roots=(source_root,),
    )
    inventory = SimpleNamespace(
        inventory_digest="sha256:inventory",
        records=(
            SimpleNamespace(
                condition=source_root.condition,
                case_id="case",
                planned_ai_unit_id="unit",
                provider_config_id="exp1_baseline_deepseek",
                model_entry_id="deepseek_v4_pro_exp1_baseline",
                provider_family="deepseek",
            ),
        ),
    )
    projection = SimpleNamespace(
        projection_digest="sha256:projection",
        to_dict=lambda: {"projection": "exact"},
    )
    closure_snapshot = SimpleNamespace(
        coverage=SimpleNamespace(source_snapshot=full_snapshot),
        full_dispatch_plans=object(),
        catalog_manifest=object(),
        full_budget=object(),
        ai_api_configs=full_snapshot.ai_api_configs,
    )
    full_current_pricing_authority = SimpleNamespace(
        provider_config_id="exp1_baseline_deepseek",
        pricing_by_entry={
            "deepseek_v4_pro_exp1_baseline": FrozenPricing(
                currency="CNY",
                input_per_million_tokens=Decimal("2.0"),
                output_per_million_tokens=Decimal("3.0"),
            )
        },
    )
    provider_budget = SimpleNamespace(
        exp5=exp5_budget,
        global_new_paid=total_budget,
    )
    captured: dict[str, object] = {}

    def fake_read(path: Path, *, label: str) -> Mapping[str, object]:
        by_name = {
            "full-approval.json": approval,
            "exp4_excluded_scope_preparation.v1.json": preparation,
            "scope_preparation_plan.v1.json": plan,
            "source_bank_binding.v1.json": bank_binding,
            "manifest.v1.json": bank_manifest,
        }
        return by_name[path.name]

    def fake_derive_budget(**kwargs: object) -> object:
        captured["exp5_pricing_authority"] = kwargs["exp5_pricing_authority"]
        return provider_budget

    monkeypatch.setattr(pipeline, "_closure_snapshot_read_json", fake_read)
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: closure_snapshot,
    )
    monkeypatch.setattr(
        paper_formal_plan,
        "derive_paper_formal_exp4_excluded_coverage",
        lambda **_kwargs: coverage,
    )
    monkeypatch.setattr(
        paper_formal_plan,
        "load_formal_prepared_request_inventory",
        lambda **_kwargs: inventory,
    )
    monkeypatch.setattr(
        paper_exp1_trace_reuse,
        "select_exp1_source_roots",
        lambda **_kwargs: (source_root,),
    )
    monkeypatch.setattr(
        paper_budget,
        "project_paper_execution_budget",
        lambda **_kwargs: projection,
    )
    monkeypatch.setattr(
        paper_budget,
        "derive_results_first_provider_budget",
        fake_derive_budget,
    )
    monkeypatch.setattr(
        paper_model_policy,
        "load_model_endpoint_cohort",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        paper_model_policy,
        "load_model_entry_map",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        paper_model_policy,
        "load_provider_config_map",
        lambda _paths: object(),
    )
    monkeypatch.setattr(
        run_paper_experiments,
        "refresh_results_first_exp5_execution_authority",
        lambda **_kwargs: {
            APPROVED_ENDPOINT_BINDINGS_KEY: {
                "exp5_real_ai_model_endpoint_comparison": exp5_pricing_binding
            }
        },
    )
    monkeypatch.setattr(ai_api_config, "load_ai_api_config", lambda _body: source_config)
    monkeypatch.setattr(
        pipeline,
        "_full_current_exp1_acquisition_pricing_authority",
        lambda **_kwargs: full_current_pricing_authority,
    )
    monkeypatch.setattr(
        paper_response_bank,
        "prepare_results_first_acquisition_authority",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        pipeline,
        "build_results_first_execution_authority",
        lambda **kwargs: SimpleNamespace(
            ai_api_configs=kwargs["ai_api_configs"],
            bundle=SimpleNamespace(acquisition_requests=(object(),)),
        ),
    )
    monkeypatch.setattr(
        paper_response_bank,
        "materialize_results_first_unified_acquisition_plan",
        lambda **_kwargs: SimpleNamespace(acquisition_requests=(object(),)),
    )
    monkeypatch.setattr(
        paper_response_bank.FullAcquisitionBudget,
        "create",
        classmethod(
            lambda _cls, _requests: SimpleNamespace(
                budget_digest="sha256:exp1-budget"
            )
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_selection_exact_exp5_execution_projection",
        lambda _authority: object(),
    )
    monkeypatch.setattr(
        pipeline,
        "replace",
        lambda authority, **changes: SimpleNamespace(
            **{**vars(authority), **changes}
        ),
    )

    authority = pipeline.load_exp4_excluded_results_first_execution_authority_v1(
        scope_preparation_root=tmp_path / "scope-preparation",
        plan_bundle_root=tmp_path / "bundle",
        planning_artifact_root=tmp_path / "planning",
        output_root=tmp_path / "output",
        selection="full_exp1_exp3_exp5",
        resume=False,
        full_budget_approval_authority=tmp_path / "full-approval.json",
    )

    assert captured["exp5_pricing_authority"] == exp5_pricing_binding
    assert authority.provider_budget_authority is provider_budget


def test_results_first_exp4_excluded_selection_uses_its_scope_loader_before_legacy_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新 selection 不得落回旧 representative restore 或通用 builder。"""

    sentinel = object()
    captured: list[dict[str, object]] = []

    def scope_loader(**kwargs: object) -> object:
        captured.append(dict(kwargs))
        return sentinel

    monkeypatch.setattr(
        pipeline,
        "load_exp4_excluded_results_first_execution_authority_v1",
        scope_loader,
    )
    monkeypatch.setattr(
        pipeline,
        "_REPRESENTATIVE_SERVICE_AUTHORITY_BUILDER",
        lambda **_kwargs: pytest.fail("legacy builder must not receive Exp4-excluded scope"),
    )
    request = pipeline.PipelineCommandRequest(
        command="run-results-first",
        scope="results_first_execution",
        evidence_class="results_first_execution",
        profile=PROFILE,
        provider_authorization=None,
        output_root=tmp_path / "paid-output",
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={
            "selection": "representative_exp1_exp3_exp5",
            "planning_artifact_root": str(tmp_path / "planning"),
            "plan_bundle_root": str(tmp_path / "bundle"),
            "results_first_scope_preparation_root": str(tmp_path / "scope-preparation"),
            "plan_only": True,
            "allow_provider_calls": False,
            "resume": False,
        },
    )

    service_input = pipeline._results_first_service_input_from_cli_authority(request)

    assert service_input.authority is sentinel
    assert service_input.selection == "representative_exp1_exp3_exp5"
    assert captured == [
        {
            "scope_preparation_root": tmp_path / "scope-preparation",
            "plan_bundle_root": tmp_path / "bundle",
            "planning_artifact_root": tmp_path / "planning",
            "output_root": tmp_path / "paid-output",
            "selection": "representative_exp1_exp3_exp5",
            "resume": False,
        }
    ]


def test_results_first_combined_metric_merge_audit_requires_exact_115_root_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """115-root results-first audit 只接受已冻结的 trace/Exp5 closure。"""

    from tests.experiments.test_paper_formal_runner import _exp1_direct_metric_fixture
    from tokenshare.experiments import paper_formal_runner

    partition = (
        ("exp1_real_ai_feasibility", 12, "real_model_trace_protocol_run"),
        ("exp2_real_ai_scalability", 6, "real_model_trace_protocol_run"),
        ("exp3_real_ai_fault_recovery", 81, "real_model_trace_protocol_run"),
        ("exp5_real_ai_model_endpoint_comparison", 16, "online_real_provider"),
    )

    def root_id(
        *, experiment_id: str, condition_id: str, case_id: str, repeat_id: int
    ) -> str:
        return "paper-direct-root:" + pipeline._closure_digest(
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "case_id": case_id,
                "repeat_id": repeat_id,
            }
        )

    def build_rows(
        *,
        base: object,
        experiment_id: str,
        count: int,
        evidence_class: str,
    ) -> tuple[object, ...]:
        rows = []
        for index in range(count):
            condition_id = f"{experiment_id}-condition-{index}"
            case_id = f"{experiment_id}-case-{index}"
            rows.append(
                replace(
                    base,
                    preregistered_root_run_id=root_id(
                        experiment_id=experiment_id,
                        condition_id=condition_id,
                        case_id=case_id,
                        repeat_id=0,
                    ),
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    case_id=case_id,
                    evidence_class=evidence_class,
                    _factory_token=paper_formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
                )
            )
        return tuple(rows)

    trace_base = _exp1_direct_metric_fixture(
        tmp_path / "trace-base",
        evidence_class="real_model_trace_protocol_run",
    )
    exp5_base = _exp1_direct_metric_fixture(
        tmp_path / "exp5-base",
        evidence_class="online_real_provider",
    )
    rows_by_experiment = {
        experiment_id: build_rows(
            base=(exp5_base if experiment_id.startswith("exp5_") else trace_base),
            experiment_id=experiment_id,
            count=count,
            evidence_class=evidence_class,
        )
        for experiment_id, count, evidence_class in partition
    }
    conditions = tuple(
        SimpleNamespace(
            experiment_id=row.experiment_id,
            condition_id=row.condition_id,
        )
        for experiment_id, _count, _evidence_class in partition
        for row in rows_by_experiment[experiment_id]
    )
    coverage = SimpleNamespace(
        conditions=conditions,
        roots=tuple(
            SimpleNamespace(
                condition=condition,
                case_id=row.case_id,
                repeat_id=row.repeat_id,
            )
            for condition, row in zip(
                conditions,
                (
                    row
                    for experiment_id, _count, _evidence_class in partition
                    for row in rows_by_experiment[experiment_id]
                ),
                strict=True,
            )
        ),
        condition_count=115,
        root_run_count=115,
        provider_calls_made=0,
        selection_kind="representative_exp1_exp3_exp5",
        coverage_digest="sha256:" + "a" * 64,
    )
    snapshot = SimpleNamespace(
        coverage=coverage,
        suite_id="results_first_exp1_exp3_trace",
        snapshot_digest="sha256:" + "b" * 64,
        source_validation_digest="sha256:" + "c" * 64,
        provider_calls_made=0,
    )
    empty_inputs = {
        key: () for key in paper_formal_runner._DIRECT_METRIC_INPUT_KEYS
    }
    trace_inputs = {
        **empty_inputs,
        "exp1_feasibility": rows_by_experiment["exp1_real_ai_feasibility"],
        "exp2_trace_scalability": rows_by_experiment["exp2_real_ai_scalability"],
        "exp3_trace_robustness": rows_by_experiment[
            "exp3_real_ai_fault_recovery"
        ],
    }
    exp5_inputs = {
        **empty_inputs,
        "experiment_5": rows_by_experiment[
            "exp5_real_ai_model_endpoint_comparison"
        ],
    }
    inputs_by_suite = {
        "exp1-exp3-trace": SimpleNamespace(
            direct=trace_inputs,
            current={},
            source={},
        ),
        "exp5-online": SimpleNamespace(
            direct=exp5_inputs,
            current={},
            source={},
        ),
    }

    def load_inputs(path: str | Path, **_kwargs: object) -> object:
        return inputs_by_suite[Path(path).name]

    def load_handle(path: str | Path, **_kwargs: object) -> object:
        suite = Path(path)
        return SimpleNamespace(
            root_path=suite / "protected-inputs",
            descriptor_path=suite / "protected-inputs" / "descriptor.json",
            descriptor_digest="sha256:" + ("d" if suite.name == "exp1-exp3-trace" else "e") * 64,
            classification="normal_formal_artifact_root",
        )

    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_inputs",
        load_inputs,
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_input_root",
        load_handle,
    )

    output_root = tmp_path / "results-first-output"
    output_root.mkdir()
    for suite_name, handle_digit in (("exp1-exp3-trace", "f"), ("exp5-online", "0")):
        suite_root = output_root / suite_name
        suite_root.mkdir()
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "traceability_replay_input_root_ref": {
                        "schema_version": (
                            "tokenshare.paper_traceability_replay_input_ref.v1"
                        ),
                        "handle_path": "traceability_replay_input_root.handle.pickle",
                        "handle_digest": "sha256:" + handle_digit * 64,
                        "descriptor_path": (
                            suite_root / "protected-inputs" / "descriptor.json"
                        ).as_posix(),
                        "descriptor_digest": "sha256:"
                        + ("d" if suite_name == "exp1-exp3-trace" else "e") * 64,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def invoke(name: str, *, selection: str = "representative_exp1_exp3_exp5"):
        audit_root = tmp_path / name
        exit_code = pipeline.main(
            (
                "audit-results-first-metric-merge",
                "--output-root",
                str(output_root),
                "--selection",
                selection,
                "--audit-output-root",
                str(audit_root),
            )
        )
        captured = capsys.readouterr()
        return exit_code, captured, audit_root

    exit_code, captured, audit_root = invoke("audit-ok")

    assert exit_code == 0
    result = json.loads(captured.out)
    assert result["status"] == "verified"
    assert result["provider_calls"] == 0
    artifact = json.loads(
        (audit_root / "results_first_combined_metric_merge.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["status"] == "verified"
    assert artifact["selection"] == "representative_exp1_exp3_exp5"
    assert artifact["provider_calls_made"] == 0
    assert artifact["root_partition"] == {
        "expected": {"exp1": 12, "exp2": 6, "exp3": 81, "exp5": 16},
        "observed": {"exp1": 12, "exp2": 6, "exp3": 81, "exp5": 16},
    }
    assert set(artifact["protected_replay_inputs"]) == {"trace", "exp5"}

    inputs_by_suite["exp1-exp3-trace"] = SimpleNamespace(
        direct={
            **trace_inputs,
            "exp3_trace_robustness": trace_inputs["exp3_trace_robustness"][:-1],
        },
        current={},
        source={},
    )
    exit_code, _captured, audit_root = invoke("audit-missing")
    assert exit_code == 3
    assert not audit_root.exists()

    inputs_by_suite["exp1-exp3-trace"] = SimpleNamespace(
        direct=trace_inputs,
        current={},
        source={},
    )
    inputs_by_suite["exp5-online"] = SimpleNamespace(
        direct={
            **exp5_inputs,
            "experiment_5": (
                trace_inputs["exp1_feasibility"][0],
                *exp5_inputs["experiment_5"][1:],
            ),
        },
        current={},
        source={},
    )
    exit_code, _captured, audit_root = invoke("audit-duplicate")
    assert exit_code == 3
    assert not audit_root.exists()

    inputs_by_suite["exp5-online"] = SimpleNamespace(
        direct=exp5_inputs,
        current={},
        source={},
    )
    exp4_row = replace(
        trace_inputs["exp1_feasibility"][0],
        experiment_id="exp4_real_ai_protocol_ablation",
        _factory_token=paper_formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
    )
    inputs_by_suite["exp1-exp3-trace"] = SimpleNamespace(
        direct={
            **trace_inputs,
            "exp1_feasibility": (
                exp4_row,
                *trace_inputs["exp1_feasibility"][1:],
            ),
        },
        current={},
        source={},
    )
    exit_code, _captured, audit_root = invoke("audit-exp4")
    assert exit_code == 3
    assert not audit_root.exists()

    inputs_by_suite["exp1-exp3-trace"] = SimpleNamespace(
        direct=trace_inputs,
        current={},
        source={},
    )
    wrong_class_row = replace(
        exp5_inputs["experiment_5"][0],
        evidence_class="real_model_trace_protocol_run",
        _factory_token=paper_formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
    )
    inputs_by_suite["exp5-online"] = SimpleNamespace(
        direct={
            **exp5_inputs,
            "experiment_5": (
                wrong_class_row,
                *exp5_inputs["experiment_5"][1:],
            ),
        },
        current={},
        source={},
    )
    exit_code, _captured, audit_root = invoke("audit-class")
    assert exit_code == 3
    assert not audit_root.exists()

    exit_code, _captured, audit_root = invoke("audit-selection", selection="full")
    assert exit_code != 0
    assert not audit_root.exists()
    assert (
        pipeline.main(
            (
                "audit-results-first-metric-merge",
                "--output-root",
                str(output_root),
                "--selection",
                "representative_exp1_exp3_exp5",
                "--audit-output-root",
                str(tmp_path / "audit-forbidden-flag"),
                "--receipt",
                "forbidden",
            )
        )
        != 0
    )


def test_results_first_combined_render_requires_verified_115_merge_audit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """combined renderer 只接受已核验的 115-root 两 closure 审计。"""

    from tokenshare.experiments import paper_formal_report
    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments import paper_metric_contract

    partition = (
        ("exp1_real_ai_feasibility", "exp1", 12),
        ("exp2_real_ai_scalability", "exp2", 6),
        ("exp3_real_ai_fault_recovery", "exp3", 81),
        ("exp5_real_ai_model_endpoint_comparison", "exp5", 16),
    )

    roots: list[object] = []
    conditions: list[object] = []
    for experiment_id, _partition, count in partition:
        for index in range(count):
            condition = SimpleNamespace(
                experiment_id=experiment_id,
                condition_id=f"{experiment_id}-condition-{index}",
            )
            conditions.append(condition)
            roots.append(
                SimpleNamespace(
                    condition=condition,
                    case_id=f"{experiment_id}-case-{index}",
                    repeat_id=0,
                )
            )
    expected_root_ids = tuple(
        pipeline._metric_merge_root_id(
            experiment_id=root.condition.experiment_id,
            condition_id=root.condition.condition_id,
            case_id=root.case_id,
            repeat_id=root.repeat_id,
        )
        for root in roots
    )
    source_binding = {
        "snapshot_digest": "sha256:" + "a" * 64,
        "source_validation_digest": "sha256:" + "b" * 64,
        "coverage_digest": "sha256:" + "c" * 64,
    }
    snapshot = SimpleNamespace(
        coverage=SimpleNamespace(
            conditions=tuple(conditions),
            roots=tuple(roots),
            condition_count=115,
            root_run_count=115,
            provider_calls_made=0,
            selection_kind="representative_exp1_exp3_exp5",
            coverage_digest=source_binding["coverage_digest"],
        ),
        suite_id="results_first_exp1_exp3_trace",
        snapshot_digest=source_binding["snapshot_digest"],
        source_validation_digest=source_binding["source_validation_digest"],
        provider_calls_made=0,
    )

    output_root = tmp_path / "paid-output"
    output_root.mkdir()
    suites: dict[str, dict[str, object]] = {}
    for suite_name, digit in (("exp1-exp3-trace", "d"), ("exp5-online", "e")):
        suite_root = output_root / suite_name
        protected_root = suite_root / "protected-inputs"
        descriptor_path = protected_root / "descriptor.json"
        handle_path = "traceability_replay_input_root.handle.pickle"
        handle_digest = "sha256:" + digit * 64
        descriptor_digest = "sha256:" + digit.upper() * 64
        suite_root.mkdir()
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "traceability_replay_input_root_ref": {
                        "schema_version": (
                            "tokenshare.paper_traceability_replay_input_ref.v1"
                        ),
                        "handle_path": handle_path,
                        "handle_digest": handle_digest,
                        "descriptor_path": descriptor_path.as_posix(),
                        "descriptor_digest": descriptor_digest,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        suites[suite_name] = {
            "suite_root": suite_root.as_posix(),
            "handle": {
                "path": handle_path,
                "digest": handle_digest,
                "root_path": protected_root.as_posix(),
            },
            "descriptor": {
                "path": descriptor_path.as_posix(),
                "digest": descriptor_digest,
                "classification": "normal_formal_artifact_root",
            },
        }

    def audit_body() -> dict[str, object]:
        return {
            "schema_version": "tokenshare.results_first_combined_metric_merge.v1",
            "status": "verified",
            "selection": "representative_exp1_exp3_exp5",
            "provider_calls_made": 0,
            "source_binding": dict(source_binding),
            "protected_replay_inputs": {
                "trace": copy.deepcopy(suites["exp1-exp3-trace"]),
                "exp5": copy.deepcopy(suites["exp5-online"]),
            },
            "expected_root_count": 115,
            "observed_root_count": 115,
            "expected_root_ids_digest": pipeline._closure_digest(expected_root_ids),
            "observed_root_ids_digest": pipeline._closure_digest(expected_root_ids),
            "root_partition": {
                "expected": {name: count for _id, name, count in partition},
                "observed": {name: count for _id, name, count in partition},
            },
        }

    audit_root = tmp_path / "verified-merge-audit"

    def write_audit(body: Mapping[str, object]) -> None:
        audit_root.mkdir(exist_ok=True)
        (audit_root / "results_first_combined_metric_merge.v1.json").write_text(
            json.dumps(body, sort_keys=True),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_inputs",
        lambda _path: SimpleNamespace(direct={}, current={}, source={}),
    )

    def load_handle(path: str | Path) -> object:
        suite = Path(path)
        binding = suites[suite.name]
        return SimpleNamespace(
            root_path=Path(binding["handle"]["root_path"]),
            descriptor_path=Path(binding["descriptor"]["path"]),
            descriptor_digest=binding["descriptor"]["digest"],
            classification="normal_formal_artifact_root",
        )

    monkeypatch.setattr(
        paper_formal_runner,
        "load_paper_traceability_replay_input_root",
        load_handle,
    )
    forbidden_calls: list[str] = []

    def forbidden(name: str):
        def reject(*_args: object, **_kwargs: object) -> object:
            forbidden_calls.append(name)
            pytest.fail(f"offline combined renderer invoked {name}")

        return reject

    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", forbidden("profile"))
    monkeypatch.setattr(pipeline, "_RECEIPT_LOADER", forbidden("receipt"))

    class ForbiddenTransport:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            forbidden_calls.append("transport")
            pytest.fail("offline combined renderer constructed transport")

    monkeypatch.setattr(pipeline, "_CountingRepresentativeTransport", ForbiddenTransport)
    runner_calls: list[dict[str, object]] = []

    def recompute(**kwargs: object) -> object:
        runner_calls.append(dict(kwargs))
        publication_root = Path(kwargs["publication_root"])
        assert not publication_root.exists()
        (publication_root / "metrics").mkdir(parents=True)
        (publication_root / "metrics" / "paper_metric_drafts.v1.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            provider_calls=0,
            metrics_digest="sha256:" + "f" * 64,
            output_refs=(
                    {
                        "path": "metrics/paper_metric_drafts.v1.json",
                        "content_digest": "sha256:" + "1" * 64,
                },
            ),
            paper_eligible=False,
        )

    monkeypatch.setattr(
        paper_formal_runner,
        "recompute_paper_formal_metrics_from_runner_input_roots",
        recompute,
    )
    contract = object()
    monkeypatch.setattr(
        paper_metric_contract,
        "load_paper_metric_contract",
        lambda: contract,
    )
    report_calls: list[dict[str, object]] = []

    def render(**kwargs: object) -> object:
        report_calls.append(dict(kwargs))
        publication_root = Path(kwargs["output_root"])
        (publication_root / "audit").mkdir(parents=True, exist_ok=True)
        (publication_root / "audit" / "paper_formal_report_manifest.v2.json").write_text(
            '{"provider_calls": 0}\n',
            encoding="utf-8",
        )
        return SimpleNamespace(
            paper_eligible=False,
            artifact_refs=(
                {
                    "path": "audit/paper_formal_report_manifest.v2.json",
                    "content_hash": "sha256:" + "2" * 64,
                },
            ),
        )

    monkeypatch.setattr(paper_formal_report, "generate_paper_formal_report", render)

    def invoke(
        *,
        selection: str = "representative_exp1_exp3_exp5",
        publication_root: Path | None = None,
        extra: tuple[str, ...] = (),
    ) -> int:
        return pipeline.main(
            (
            "render-results-first-combined",
            "--output-root",
            str(output_root),
            "--selection",
            selection,
            "--metric-merge-audit-root",
            str(audit_root),
            "--combined-output-root",
            str(publication_root or (tmp_path / "combined-publication")),
            *extra,
        )
        )

    invalid_cases = (
        ("observed-root-digest", lambda body: body.update({"observed_root_ids_digest": "sha256:" + "8" * 64})),
        ("source-binding", lambda body: body["source_binding"].update({"coverage_digest": "sha256:" + "0" * 64})),
        ("protected-reference", lambda body: body["protected_replay_inputs"]["exp5"]["descriptor"].update({"digest": "sha256:" + "9" * 64})),
        ("partition", lambda body: body["root_partition"]["observed"].update({"exp5": 15})),
    )
    for _name, mutate in invalid_cases:
        body = audit_body()
        mutate(body)
        write_audit(body)
        assert invoke(publication_root=tmp_path / f"invalid-{_name}") != 0
        assert not (tmp_path / f"invalid-{_name}").exists()
        assert not runner_calls
        capsys.readouterr()

    write_audit(audit_body())
    assert invoke(selection="full", publication_root=tmp_path / "invalid-selection") != 0
    assert not (tmp_path / "invalid-selection").exists()
    assert not runner_calls
    capsys.readouterr()

    existing_publication_root = tmp_path / "already-published"
    existing_publication_root.mkdir()
    assert invoke(publication_root=existing_publication_root) != 0
    assert not runner_calls
    capsys.readouterr()

    assert invoke(publication_root=output_root) != 0
    assert not runner_calls
    capsys.readouterr()

    for forbidden_argv in (
        ("--receipt", "forbidden"),
        ("--api-key", "forbidden"),
        ("--resume",),
        ("--allow-provider-calls",),
    ):
        assert invoke(
            publication_root=tmp_path / f"invalid-{forbidden_argv[0][2:]}",
            extra=forbidden_argv,
        ) != 0
        assert not runner_calls
        capsys.readouterr()

    publication_root = tmp_path / "combined-publication"
    exit_code = invoke(publication_root=publication_root)

    assert exit_code == 0
    assert runner_calls == [
        {
            "publication_root": publication_root.resolve(strict=False),
            "trace_suite_root": (output_root / "exp1-exp3-trace").resolve(strict=False),
            "exp5_suite_root": (output_root / "exp5-online").resolve(strict=False),
            "expected_root_ids": expected_root_ids,
        }
    ]
    assert len(report_calls) == 1
    assert report_calls[0]["output_root"] == publication_root.resolve(strict=False)
    assert report_calls[0]["metrics"].provider_calls == 0
    assert report_calls[0]["contract"] is contract
    assert not forbidden_calls
    assert (publication_root / "audit" / "paper_formal_report_manifest.v2.json").is_file()
    combined_audit = json.loads(
        (
            publication_root
            / "audit"
            / "results_first_combined_metrics_input.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert combined_audit["schema_version"] == (
        "tokenshare.results_first_combined_metrics_input.v1"
    )
    assert combined_audit["status"] == "verified"
    assert combined_audit["provider_calls_made"] == 0
    assert combined_audit["metrics_digest"] == "sha256:" + "f" * 64
    assert combined_audit["metric_merge_audit"] == {
        "path": (
            audit_root / "results_first_combined_metric_merge.v1.json"
        ).as_posix(),
        "digest": "sha256:"
        + sha256(
            (
                audit_root / "results_first_combined_metric_merge.v1.json"
            ).read_bytes()
        ).hexdigest(),
    }
    assert combined_audit["protected_replay_inputs"] == {
        "trace": suites["exp1-exp3-trace"],
        "exp5": suites["exp5-online"],
    }
    assert combined_audit["metrics_output_refs"] == [
        {
            "path": "metrics/paper_metric_drafts.v1.json",
            "content_digest": "sha256:" + "1" * 64,
        }
    ]
    assert combined_audit["report_output_refs"] == [
        {
            "path": "audit/paper_formal_report_manifest.v2.json",
            "content_hash": "sha256:" + "2" * 64,
        }
    ]


def test_results_first_combined_render_reads_two_actual_protected_roots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双 closure 必须经真实 handle/descriptor loader 后才可进入 combined 路由。"""

    from tests.experiments.test_paper_traceability import _genuine_l4_inputs
    from tokenshare.experiments import paper_formal_report
    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments import paper_metric_contract
    from tokenshare.experiments.paper_models import ArtifactIdentitySnapshot
    from tokenshare.experiments.paper_traceability import (
        _CURRENT_PROVIDER_ROLES,
        _load_protected_replay_inputs,
        _walk_instances,
    )

    source_descriptor, _rows = _genuine_l4_inputs(tmp_path / "genuine-source")
    source_inputs = _load_protected_replay_inputs(source_descriptor)
    base_direct = source_inputs.direct["exp1_feasibility"][0].direct_result
    output_root = tmp_path / "paid-output"
    output_root.mkdir()
    partition = (
        ("exp1_real_ai_feasibility", "exp1", 12, "real_model_trace_protocol_run"),
        ("exp2_real_ai_scalability", "exp2", 6, "real_model_trace_protocol_run"),
        ("exp3_real_ai_fault_recovery", "exp3", 81, "real_model_trace_protocol_run"),
        (
            "exp5_real_ai_model_endpoint_comparison",
            "exp5",
            16,
            "online_real_provider",
        ),
    )

    roots: list[object] = []
    rows_by_experiment: dict[str, tuple[object, ...]] = {}
    for experiment_id, short_name, count, evidence_class in partition:
        rows = []
        for index in range(count):
            condition_id = f"{short_name}-condition-{index}"
            case_id = f"{short_name}-case-{index}"
            root_id = pipeline._metric_merge_root_id(
                experiment_id=experiment_id,
                condition_id=condition_id,
                case_id=case_id,
                repeat_id=0,
            )
            rows.append(
                replace(
                    base_direct,
                    preregistered_root_run_id=root_id,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    case_id=case_id,
                    evidence_class=evidence_class,
                    _factory_token=paper_formal_runner._DIRECT_RESULT_FACTORY_TOKEN,
                )
            )
            roots.append(
                SimpleNamespace(
                    condition=SimpleNamespace(
                        experiment_id=experiment_id,
                        condition_id=condition_id,
                    ),
                    case_id=case_id,
                    repeat_id=0,
                )
            )
        rows_by_experiment[experiment_id] = tuple(rows)

    expected_root_ids = tuple(
        pipeline._metric_merge_root_id(
            experiment_id=root.condition.experiment_id,
            condition_id=root.condition.condition_id,
            case_id=root.case_id,
            repeat_id=root.repeat_id,
        )
        for root in roots
    )
    empty_direct = {
        key: () for key in paper_formal_runner._DIRECT_METRIC_INPUT_KEYS
    }
    trace_direct = {
        **empty_direct,
        "exp1_feasibility": rows_by_experiment["exp1_real_ai_feasibility"],
        "exp2_trace_scalability": rows_by_experiment[
            "exp2_real_ai_scalability"
        ],
        "exp3_trace_robustness": rows_by_experiment[
            "exp3_real_ai_fault_recovery"
        ],
    }
    exp5_direct = {
        **empty_direct,
        "experiment_5": rows_by_experiment[
            "exp5_real_ai_model_endpoint_comparison"
        ],
    }
    source_provider_object_files = {
        ref["artifact_id"]: source_descriptor.root_path / ref["path"]
        for ref in json.loads(
            source_descriptor.descriptor_path.read_text(encoding="utf-8")
        )["current_provider_object_refs"]
    }
    required_provider_object_ids = {
        value.artifact_id
        for value in _walk_instances(
            (trace_direct, exp5_direct), ArtifactIdentitySnapshot
        )
        if value.source_role in _CURRENT_PROVIDER_ROLES
    }
    provider_object_files = {
        artifact_id: source_provider_object_files[artifact_id]
        for artifact_id in required_provider_object_ids
    }

    def persist_suite(
        *,
        name: str,
        direct: Mapping[str, object],
        root_ids: tuple[str, ...],
    ) -> Path:
        suite_root = output_root / name
        suite_root.mkdir()
        protected = paper_formal_runner.persist_paper_traceability_replay_input_root(
            replay_input_root=output_root / f"{name}.traceability_replay_inputs",
            canonical_direct_rows=direct,
            global_infrastructure_valid=True,
            canonical_runtime_evidence=tuple(
                SimpleNamespace(preregistered_root_run_id=root_id)
                for root_id in root_ids
            ),
            requested_lineage_root_ids=root_ids,
            current_trace_wrappers_by_root={root_id: () for root_id in root_ids},
            trace_source_bindings_by_root={root_id: () for root_id in root_ids},
            eligibility_facts_by_root={root_id: {} for root_id in root_ids},
            source_resolvers=source_inputs.source["source_resolvers"],
            current_provider_object_files=provider_object_files,
            current_evidence_root=source_descriptor.root_path / "current_evidence",
        )
        handle_path = suite_root / "traceability_replay_input_root.handle.pickle"
        handle_bytes = pickle.dumps(protected, protocol=5)
        handle_path.write_bytes(handle_bytes)
        (suite_root / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "traceability_replay_input_root_ref": {
                        "schema_version": (
                            "tokenshare.paper_traceability_replay_input_ref.v1"
                        ),
                        "handle_path": handle_path.name,
                        "handle_digest": "sha256:" + sha256(handle_bytes).hexdigest(),
                        "descriptor_path": protected.descriptor_path.as_posix(),
                        "descriptor_digest": protected.descriptor_digest,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return suite_root

    trace_root = persist_suite(
        name="exp1-exp3-trace",
        direct=trace_direct,
        root_ids=expected_root_ids[:99],
    )
    exp5_root = persist_suite(
        name="exp5-online",
        direct=exp5_direct,
        root_ids=expected_root_ids[99:],
    )
    source_binding = {
        "snapshot_digest": "sha256:" + "a" * 64,
        "source_validation_digest": "sha256:" + "b" * 64,
        "coverage_digest": "sha256:" + "c" * 64,
    }
    snapshot = SimpleNamespace(
        coverage=SimpleNamespace(
            conditions=tuple(root.condition for root in roots),
            roots=tuple(roots),
            condition_count=115,
            root_run_count=115,
            provider_calls_made=0,
            selection_kind="representative_exp1_exp3_exp5",
            coverage_digest=source_binding["coverage_digest"],
        ),
        suite_id="results_first_exp1_exp3_trace",
        snapshot_digest=source_binding["snapshot_digest"],
        source_validation_digest=source_binding["source_validation_digest"],
        provider_calls_made=0,
    )
    monkeypatch.setattr(
        pipeline,
        "load_results_first_closure_replay_snapshot",
        lambda **_kwargs: snapshot,
    )
    forbidden_calls: list[str] = []

    def forbidden(name: str):
        def reject(*_args: object, **_kwargs: object) -> object:
            forbidden_calls.append(name)
            pytest.fail(f"offline combined path invoked {name}")

        return reject

    monkeypatch.setattr(pipeline, "_PROFILE_LOADER", forbidden("profile"))
    monkeypatch.setattr(pipeline, "_RECEIPT_LOADER", forbidden("receipt"))

    class ForbiddenTransport:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            forbidden_calls.append("transport")
            pytest.fail("offline combined path constructed transport")

    monkeypatch.setattr(pipeline, "_CountingRepresentativeTransport", ForbiddenTransport)
    audit_root = tmp_path / "verified-merge-audit"
    assert pipeline.main(
        (
            "audit-results-first-metric-merge",
            "--output-root",
            str(output_root),
            "--selection",
            "representative_exp1_exp3_exp5",
            "--audit-output-root",
            str(audit_root),
        )
    ) == 0
    capsys.readouterr()
    merge_audit = json.loads(
        (audit_root / "results_first_combined_metric_merge.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert merge_audit["provider_calls_made"] == 0
    assert merge_audit["root_partition"]["observed"] == {
        "exp1": 12,
        "exp2": 6,
        "exp3": 81,
        "exp5": 16,
    }
    assert merge_audit["observed_root_ids_digest"] == pipeline._closure_digest(
        expected_root_ids
    )
    assert paper_formal_runner.load_paper_traceability_replay_input_root(trace_root)
    assert paper_formal_runner.load_paper_traceability_replay_input_root(exp5_root)

    runner_calls: list[dict[str, object]] = []
    renderer_calls: list[dict[str, object]] = []

    def recompute(**kwargs: object) -> object:
        runner_calls.append(dict(kwargs))
        publication_root = Path(kwargs["publication_root"])
        (publication_root / "metrics").mkdir(parents=True)
        (publication_root / "metrics" / "paper_metric_drafts.v1.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            provider_calls=0,
            metrics_digest="sha256:" + "d" * 64,
            output_refs=(
                {
                    "path": "metrics/paper_metric_drafts.v1.json",
                    "content_digest": "sha256:" + "e" * 64,
                },
            ),
        )

    def render(**kwargs: object) -> object:
        renderer_calls.append(dict(kwargs))
        publication_root = Path(kwargs["output_root"])
        (publication_root / "audit").mkdir(parents=True, exist_ok=True)
        (publication_root / "audit" / "paper_formal_report_manifest.v2.json").write_text(
            '{"provider_calls": 0}\n',
            encoding="utf-8",
        )
        return SimpleNamespace(
            paper_eligible=False,
            artifact_refs=(
                {
                    "path": "audit/paper_formal_report_manifest.v2.json",
                    "content_hash": "sha256:" + "f" * 64,
                },
            ),
        )

    monkeypatch.setattr(
        paper_formal_runner,
        "recompute_paper_formal_metrics_from_runner_input_roots",
        recompute,
    )
    monkeypatch.setattr(paper_formal_report, "generate_paper_formal_report", render)
    monkeypatch.setattr(paper_metric_contract, "load_paper_metric_contract", object)

    def invoke(publication_root: Path) -> int:
        return pipeline.main(
            (
                "render-results-first-combined",
                "--output-root",
                str(output_root),
                "--selection",
                "representative_exp1_exp3_exp5",
                "--metric-merge-audit-root",
                str(audit_root),
                "--combined-output-root",
                str(publication_root),
            )
        )

    original_exp5_handle = (
        exp5_root / "traceability_replay_input_root.handle.pickle"
    ).read_bytes()
    (exp5_root / "traceability_replay_input_root.handle.pickle").write_bytes(
        b"corrupted handle"
    )
    corrupted_publication = tmp_path / "corrupted-publication"
    assert invoke(corrupted_publication) == 3
    assert not corrupted_publication.exists()
    assert not runner_calls
    assert not renderer_calls
    capsys.readouterr()
    (exp5_root / "traceability_replay_input_root.handle.pickle").write_bytes(
        original_exp5_handle
    )

    publication_root = tmp_path / "combined-publication"
    assert invoke(publication_root) == 0
    assert len(runner_calls) == 1
    assert runner_calls[0]["trace_suite_root"] == trace_root.resolve(strict=False)
    assert runner_calls[0]["exp5_suite_root"] == exp5_root.resolve(strict=False)
    assert runner_calls[0]["expected_root_ids"] == expected_root_ids
    assert len(renderer_calls) == 1
    assert not forbidden_calls
    combined_audit = json.loads(
        (
            publication_root
            / "audit"
            / "results_first_combined_metrics_input.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert combined_audit["provider_calls_made"] == 0
    assert combined_audit["metrics_output_refs"][0]["content_digest"] == (
        "sha256:" + "e" * 64
    )
    assert combined_audit["report_output_refs"][0]["content_hash"] == (
        "sha256:" + "f" * 64
    )
