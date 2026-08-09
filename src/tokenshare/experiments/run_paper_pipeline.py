"""EPD-027 bounded paper-pipeline CLI 的薄路由层。

本模块只解析、校验调用边界并委派既有实验服务。它不读取 provider secret、
不构造 transport，也不实现协议状态机或 publication gate policy。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any

from tokenshare.experiments.paper_budget import validate_provider_budget_mode
from tokenshare.experiments.paper_paid_authorization import (
    PaidAuthorizationValidation,
    output_root_path_digest,
    validate_paid_execution_receipt,
)
from tokenshare.experiments.paper_pipeline_profile import (
    PaperPipelineProfile,
    load_paper_pipeline_profile,
)


RESULT_SCHEMA_VERSION = "tokenshare.paper_pipeline_cli_result.v1"
PAPER_PIPELINE_COMMANDS = frozenset(
    {
        "validate-profile",
        "plan-bank",
        "acquire-bank",
        "audit-bank",
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-capability-smoke",
        "run-exp5-online",
        "render",
        "replay",
        "audit-cell-lineage",
        "validate-formal-execution-gate",
        "validate-paper-publication-gate",
    }
)

_PROVIDER_SCOPES = {
    "acquire-bank": "epd027_full_bank_acquisition",
    "run-online-checks": "epd027_l3_capability_and_online_checks",
    "run-exp1-online": "exp1_full_online",
    "run-exp5-capability-smoke": "exp5_capability_smoke",
    "run-exp5-online": "exp5_full_online",
}
_EVIDENCE_CLASSES = {
    "validate-profile": "offline_profile_validation",
    "plan-bank": "offline_bank_plan",
    "audit-bank": "offline_bank_audit",
    "run-trace": "real_model_trace_protocol_run",
    "render": "offline_render",
    "replay": "offline_replay",
    "audit-cell-lineage": "offline_cell_lineage",
    "validate-formal-execution-gate": "offline_gate_parser_only",
    "validate-paper-publication-gate": "offline_gate_parser_only",
}
_FORMAL_GATED_COMMANDS = frozenset(
    {"run-trace", "run-exp1-online", "run-exp5-online"}
)


@dataclass(frozen=True, kw_only=True)
class ExternalBankResolverBinding:
    """只在当前进程保存 root；持久化参数视图永远不包含路径。"""

    root: Path

    def open(self) -> Any:
        from tokenshare.executors.response_bank import ResponseBankResolver

        return ResponseBankResolver.open(self.root)


@dataclass(frozen=True, kw_only=True)
class PipelineCommandRequest:
    command: str
    scope: str
    evidence_class: str
    profile: PaperPipelineProfile | Any
    provider_authorization: PaidAuthorizationValidation | Any | None
    output_root: Path | None
    replay_input_root: Path | None
    external_bank_resolver: ExternalBankResolverBinding | None
    plan_digest: str | None
    inventory_digest: str | None
    budget_mode: str
    serialized_arguments: Mapping[str, object]
    plan_bundle_root: Path | None = None
    _formal_authority: object | None = None
    _service_input: object | None = None

    def __post_init__(self) -> None:
        if self.serialized_arguments.get("results_first_matrix8") is True:
            raise ValueError(
                "results-first matrix8 pipeline authority is retired"
            )


@dataclass(frozen=True, kw_only=True)
class AcquisitionServiceInput:
    """acquisition adapter 的进程内 typed 构造输入；不会进入 CLI 输出。"""

    scope: str
    orchestrator_arguments: Mapping[str, object]
    acquisition_requests: Sequence[object]
    bundle: object | None = None
    manifest: object | None = None


@dataclass(frozen=True, kw_only=True)
class FormalSuiteServiceInput:
    """official formal runner 的进程内 keyword 输入。"""

    scope: str
    keyword_arguments: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class FormalSuiteServiceResult:
    """保留 official runner typed terminal，同时只序列化公开摘要。"""

    public_result: Mapping[str, object]
    terminal_result: object

    def to_dict(self) -> dict[str, object]:
        return dict(self.public_result)


@dataclass(frozen=True, kw_only=True)
class ReportRenderServiceInput:
    """Task 21 renderer 所需的已验证 metrics 与 contract。"""

    metrics: object
    contract: object | None = None


@dataclass(frozen=True, kw_only=True)
class CellLineageAuditServiceInput:
    """Task 20 cell-lineage writer 所需的 typed observations。"""

    contract: object
    observations: Sequence[object]


@dataclass(frozen=True, kw_only=True)
class FormalExecutionGateServiceInput:
    """execution gate 的进程内 typed authority；不从 CLI JSON 伪造。"""

    selection: object
    prerequisites: object


@dataclass(frozen=True, kw_only=True)
class PaperPublicationGateServiceInput:
    """publication gate 的进程内 terminal evidence；不触发 replay/provider。"""

    selection: object
    terminal_evidence: object


@dataclass(frozen=True, kw_only=True)
class FormalRunGateServiceInput:
    """正式 run 的同进程两阶段 gate authority；不能从 argv/JSON 构造。"""

    execution_gate: FormalExecutionGateServiceInput
    publication_gate_factory: Callable[[object], object]


Delegate = Callable[[PipelineCommandRequest], Mapping[str, object] | Any]
ServiceInputFactory = Callable[[PipelineCommandRequest], object | None]


def _UTC_NOW() -> datetime:
    return datetime.now(timezone.utc)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route bounded EPD-027 paper-pipeline operations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--profile", required=True)
        return command_parser

    common("validate-profile")
    plan_bank = common("plan-bank")
    plan_bank.add_argument("--results-first-matrix8", action="store_true")
    plan_bank.add_argument("--plan-bundle-root")

    provider_parsers: dict[str, argparse.ArgumentParser] = {}
    for name in _PROVIDER_SCOPES:
        command_parser = common(name)
        provider_parsers[name] = command_parser
        if name == "acquire-bank":
            authorization = command_parser.add_mutually_exclusive_group(
                required=True
            )
            authorization.add_argument("--receipt")
            authorization.add_argument(
                "--results-first-matrix8", action="store_true"
            )
        else:
            command_parser.add_argument("--receipt", required=True)
        command_parser.add_argument(
            "--allow-provider-calls",
            action="store_true",
            required=True,
        )
        mode = command_parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--new-run", action="store_true")
        mode.add_argument("--resume", action="store_true")
        command_parser.add_argument("--output-root", required=True)
        command_parser.add_argument("--plan-digest", required=True)
        command_parser.add_argument("--inventory-digest", required=True)
        command_parser.add_argument(
            "--budget-mode",
            choices=("bounded", "unlimited"),
            default="bounded",
        )
    provider_parsers["acquire-bank"].add_argument("--plan-bundle-root")

    audit = common("audit-bank")
    audit.add_argument("--external-bank-root", required=True)

    trace = common("run-trace")
    trace.add_argument("--external-bank-root", required=True)
    trace.add_argument("--output-root", required=True)
    trace.add_argument("--plan-bundle-root", required=True)
    trace.add_argument("--plan-digest", required=True)
    trace.add_argument("--inventory-digest", required=True)
    trace.add_argument("--full-bank-acquisition-receipt")
    trace.add_argument("--l3-online-check-receipt")
    trace.add_argument("--l3-online-check-root")
    trace.add_argument("--results-first-matrix8", action="store_true")

    render = common("render")
    render.add_argument("--output-root", required=True)

    replay = common("replay")
    replay.add_argument("--output-root", required=True)
    replay.add_argument("--replay-input-root", required=True)

    lineage = common("audit-cell-lineage")
    lineage.add_argument("--output-root", required=True)

    for name in (
        "validate-formal-execution-gate",
        "validate-paper-publication-gate",
    ):
        gate = common(name)
        gate.add_argument("--output-root", required=True)
    return parser


def _load_receipt(path: str | Path) -> Mapping[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("paid receipt must be a JSON object")
    return value


_PROFILE_LOADER = load_paper_pipeline_profile
_RECEIPT_LOADER = _load_receipt
_RECEIPT_VALIDATOR = validate_paid_execution_receipt
_BUDGET_VALIDATOR = validate_provider_budget_mode


def _load_acquisition_bundle(path: str | Path):
    from tokenshare.experiments.paper_response_bank import (
        load_acquisition_plan_bundle,
    )

    return load_acquisition_plan_bundle(path)


_ACQUISITION_BUNDLE_LOADER = _load_acquisition_bundle


def _build_formal_authority(**kwargs: object):
    from tokenshare.experiments.run_paper_experiments import (
        build_epd027_formal_service_authority,
    )

    return build_epd027_formal_service_authority(**kwargs)


_FORMAL_AUTHORITY_BUILDER = _build_formal_authority


def _paid_scope_binding(
    *,
    validation: PaidAuthorizationValidation,
    scope: str,
    authorized_plan_digest: str,
    profile_digest: str,
    budget_digest: str,
    inventory_digest: str,
    prompt_admission_profile_digest: str,
    output_root: str | Path,
):
    """把Task26已验证结果绑定到validator实际使用的scope authority。"""

    from tokenshare.experiments.paper_formal_gate import (
        PaidAuthorizationScopeAuthority,
        SelectedPaidAuthorizationBinding,
        selected_experiments_for_provider_scope,
    )

    selected = selected_experiments_for_provider_scope(scope)
    return SelectedPaidAuthorizationBinding(
        authority=PaidAuthorizationScopeAuthority(
            scope=scope,
            authorized_plan_digest=authorized_plan_digest,
            profile_digest=profile_digest,
            budget_digest=budget_digest,
            inventory_digest=inventory_digest,
            prompt_admission_profile_digest=prompt_admission_profile_digest,
            selected_experiments=selected,
            output_root_path_digest=output_root_path_digest(output_root),
        ),
        validation=validation,
    )


def _build_smoke_authority(**kwargs: object):
    from tokenshare.experiments.run_paper_experiments import (
        build_epd027_capability_smoke_service_authority,
    )

    return build_epd027_capability_smoke_service_authority(**kwargs)


_SMOKE_AUTHORITY_BUILDER = _build_smoke_authority


def _service_result(
    request: PipelineCommandRequest,
    *,
    status: object = "completed",
    provider_calls: int = 0,
    **digests: object,
) -> dict[str, object]:
    normalized_status = getattr(status, "value", status)
    return {
        "status": str(normalized_status),
        "plan_digest": request.plan_digest,
        "inventory_digest": request.inventory_digest,
        "evidence_class": request.evidence_class,
        "provider_calls": provider_calls,
        **digests,
    }


def _validate_profile_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    # profile_loader 已在 request 建立前完成权威 schema/digest 验证。
    return _service_result(request)


def _plan_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_online_checks import (
        freeze_paper_online_checks_plan,
    )

    plan = freeze_paper_online_checks_plan()
    return _service_result(
        request,
        plan_digest=plan.plan_digest,
        inventory_digest=request.inventory_digest,
    )


def _acquire_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_response_bank import (
        ResponseBankAcquisitionOrchestrator,
        finalize_acquisition_child_bank,
    )

    value = request._service_input
    if not isinstance(value, AcquisitionServiceInput):
        raise ValueError("acquire-bank requires typed acquisition service input")
    if value.scope != request.scope:
        raise ValueError("acquisition service scope mismatch")
    arguments = dict(value.orchestrator_arguments)
    if Path(str(arguments.get("output_root"))) != request.output_root:
        raise ValueError("acquisition service output root mismatch")
    if arguments.get("inventory_digest") != request.inventory_digest:
        raise ValueError("acquisition service inventory digest mismatch")
    acquisition_requests = tuple(value.acquisition_requests)
    if not acquisition_requests:
        raise ValueError("acquisition service requests are empty")
    orchestrator = ResponseBankAcquisitionOrchestrator(**arguments)
    batch = orchestrator.acquire_all(
        acquisition_requests,
        max_in_flight=1,
    )
    results = tuple(batch.results)
    provider_calls = sum(
        1 for result in results if bool(getattr(result, "transport_invoked", False))
    )
    if value.bundle is not None or value.manifest is not None:
        if value.bundle is None or value.manifest is None:
            raise ValueError("acquisition child-bank authority is partial")
        finalize_acquisition_child_bank(
            orchestrator=orchestrator,
            bundle=value.bundle,
            manifest=value.manifest,
            batch_result=batch,
        )
    status = "completed" if batch.status == "complete" else batch.status
    return _service_result(
        request,
        status=status,
        provider_calls=provider_calls,
    )


def _formal_suite_adapter(
    request: PipelineCommandRequest,
    *,
    trace_required: bool,
) -> FormalSuiteServiceResult:
    from tokenshare.experiments.paper_formal_runner import (
        execute_paper_formal_suite,
    )

    value = request._service_input
    if not isinstance(value, FormalSuiteServiceInput):
        raise ValueError(f"{request.command} requires typed formal-suite service input")
    if value.scope != request.scope:
        raise ValueError("formal-suite service scope mismatch")
    arguments = dict(value.keyword_arguments)
    if Path(str(arguments.get("output_root"))) != request.output_root:
        raise ValueError("formal-suite service output root mismatch")
    real_transport = arguments.get("real_transport")
    if trace_required:
        if real_transport is not False or arguments.get("trace_context") is None:
            raise ValueError("trace service requires trace context and no real transport")
    elif real_transport is not True or request.provider_authorization is None:
        raise ValueError("online service requires authorized real transport")
    result = execute_paper_formal_suite(**arguments)
    from tokenshare.experiments.paper_models import PaperSuiteResult

    if not isinstance(result, PaperSuiteResult):
        raise TypeError("official formal service returned invalid terminal result")
    return FormalSuiteServiceResult(
        public_result=_service_result(
            request,
            status=result.status,
            provider_calls=int(result.provider_attempt_count),
        ),
        terminal_result=result,
    )


def _run_trace_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=True)


def _run_online_checks_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _run_exp1_online_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _run_exp5_capability_smoke_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_smoke import execute_paper_smoke_suite

    value = request._service_input
    if not isinstance(value, FormalSuiteServiceInput):
        raise ValueError(
            "run-exp5-capability-smoke requires typed smoke-suite service input"
        )
    if value.scope != request.scope:
        raise ValueError("smoke-suite service scope mismatch")
    arguments = dict(value.keyword_arguments)
    execution_plan = arguments.get("execution_plan")
    if Path(str(getattr(execution_plan, "output_root", None))) != request.output_root:
        raise ValueError("smoke-suite service output root mismatch")
    if (
        arguments.get("real_transport") is not True
        or request.provider_authorization is None
    ):
        raise ValueError("smoke online service requires authorized real transport")
    result = execute_paper_smoke_suite(**arguments)
    return _service_result(
        request,
        status=getattr(result, "status", "completed"),
        provider_calls=int(getattr(result, "provider_attempt_count", 0)),
    )


def _run_exp5_online_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    return _formal_suite_adapter(request, trace_required=False)


def _audit_bank_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    resolver = request.external_bank_resolver
    if resolver is None:
        raise ValueError("audit-bank requires an external bank resolver")
    index = resolver.open().index
    return _service_result(
        request,
        inventory_digest=index.manifest.inventory_digest,
    )


def _render_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_report import (
        generate_paper_formal_report,
    )

    value = request._service_input
    if not isinstance(value, ReportRenderServiceInput):
        raise ValueError("render requires typed formal-metrics service input")
    result = generate_paper_formal_report(
        output_root=request.output_root,
        metrics=value.metrics,
        contract=value.contract,
    )
    return _service_result(request, status=getattr(result, "status", "completed"))


def _replay_adapter(request: PipelineCommandRequest) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_runner import (
        load_paper_traceability_replay_input_root,
        recompute_paper_traceability_replay,
    )

    if request.replay_input_root is None:
        raise ValueError("replay requires a runner replay-input root")
    protected_root = load_paper_traceability_replay_input_root(
        request.replay_input_root
    )
    result = recompute_paper_traceability_replay(
        output_root=request.output_root,
        replay_input_root=protected_root,
    )
    return _service_result(
        request,
        provider_calls=int(getattr(result, "provider_calls", 0)),
    )


def _audit_cell_lineage_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_traceability import write_cell_lineage_audit

    value = request._service_input
    if not isinstance(value, CellLineageAuditServiceInput):
        raise ValueError("audit-cell-lineage requires typed observation service input")
    write_cell_lineage_audit(
        output_root=request.output_root,
        contract=value.contract,
        observations=tuple(value.observations),
    )
    return _service_result(request)


def _validate_formal_execution_gate_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_gate import formal_execution_gate

    value = request._service_input
    if not isinstance(value, FormalExecutionGateServiceInput):
        raise ValueError(
            "validate-formal-execution-gate requires typed prerequisite authority"
        )
    return formal_execution_gate(value.selection, value.prerequisites).to_dict()


def _validate_paper_publication_gate_adapter(
    request: PipelineCommandRequest,
) -> Mapping[str, object]:
    from tokenshare.experiments.paper_formal_gate import paper_publication_gate

    value = request._service_input
    if not isinstance(value, PaperPublicationGateServiceInput):
        raise ValueError(
            "validate-paper-publication-gate requires typed terminal evidence"
        )
    return paper_publication_gate(
        value.selection, value.terminal_evidence
    ).to_dict()


_AUTHORITATIVE_SERVICE_ADAPTERS: dict[str, Delegate] = {
    "validate-profile": _validate_profile_adapter,
    "plan-bank": _plan_bank_adapter,
    "acquire-bank": _acquire_bank_adapter,
    "audit-bank": _audit_bank_adapter,
    "run-trace": _run_trace_adapter,
    "run-online-checks": _run_online_checks_adapter,
    "run-exp1-online": _run_exp1_online_adapter,
    "run-exp5-capability-smoke": _run_exp5_capability_smoke_adapter,
    "run-exp5-online": _run_exp5_online_adapter,
    "render": _render_adapter,
    "replay": _replay_adapter,
    "audit-cell-lineage": _audit_cell_lineage_adapter,
    "validate-formal-execution-gate": _validate_formal_execution_gate_adapter,
    "validate-paper-publication-gate": _validate_paper_publication_gate_adapter,
}


def _no_service_input(_request: PipelineCommandRequest) -> None:
    return None


def _provided_gate_service_input(request: PipelineCommandRequest) -> object:
    if request._service_input is None:
        raise ValueError("paper gate typed service input is missing")
    return request._service_input


def _acquisition_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> AcquisitionServiceInput:
    if request.plan_bundle_root is None:
        raise ValueError("acquire-bank requires --plan-bundle-root")
    if request.output_root is None:
        raise ValueError("acquire-bank requires an output root")
    from tokenshare.executors.ai_api_transport import (
        UrlLibDeepSeekTransport,
        UrlLibOpenAITransport,
        UrlLibSiliconFlowTransport,
    )
    from tokenshare.experiments.paper_budget_ledger import PaperBudgetLedger
    from tokenshare.experiments.paper_response_bank import (
        ResultsFirstAcquisitionAuthorization,
        load_acquisition_plan_bundle,
        results_first_response_bank_manifest_for_bundle,
        response_bank_manifest_for_bundle,
    )

    bundle = load_acquisition_plan_bundle(request.plan_bundle_root)
    if bundle.authorized_plan_digest != request.plan_digest:
        raise ValueError("acquisition bundle plan digest mismatch")
    if bundle.inventory_digest != request.inventory_digest:
        raise ValueError("acquisition bundle inventory digest mismatch")
    if bundle.profile_digest != request.profile.profile_digest:
        raise ValueError("acquisition bundle profile digest mismatch")
    paid_authorization = request.provider_authorization
    facility_authorization = None
    if type(paid_authorization) is PaidAuthorizationValidation:
        manifest = response_bank_manifest_for_bundle(bundle, paid_authorization)
    elif type(paid_authorization) is ResultsFirstAcquisitionAuthorization:
        facility_authorization = paid_authorization
        paid_authorization = None
        manifest = results_first_response_bank_manifest_for_bundle(
            bundle, facility_authorization
        )
    else:
        raise ValueError("acquire-bank requires an explicit acquisition authority")
    authorization_arguments = (
        {"paid_authorization": paid_authorization}
        if paid_authorization is not None
        else {"facility_authorization": facility_authorization}
    )
    ledger = PaperBudgetLedger(
        request.output_root / "acquisition_budget.v1.sqlite3",
        limits=bundle.full_budget.to_limits(),
    )
    ledger.preregister_inventory(
        inventory_digest=bundle.inventory_digest,
        rows=bundle.inventory_rows,
    )

    class OfficialTransportSet:
        _transports = {
            "deepseek": UrlLibDeepSeekTransport(),
            "openai": UrlLibOpenAITransport(),
            "siliconflow": UrlLibSiliconFlowTransport(),
        }

        def tokenshare_transport_for_provider(self, provider_family: str):
            try:
                return self._transports[provider_family]
            except KeyError as exc:
                raise ValueError("unsupported acquisition provider family") from exc

    def resolve_secret(name: str) -> str:
        import os

        return os.environ.get(name, "")

    return AcquisitionServiceInput(
        scope=request.scope,
        orchestrator_arguments={
            "output_root": request.output_root,
            "bank_root_id": manifest.bank_root_id,
            "manifest_digest": manifest.manifest_digest,
            "inventory_digest": bundle.inventory_digest,
            "inventory_rows": bundle.inventory_rows,
            "budget_ledger": ledger,
            **authorization_arguments,
            "invocation_mode": request.provider_authorization.output_mode,
            "transport": OfficialTransportSet(),
            "secret_resolver": resolve_secret,
            "now_epoch": int(_UTC_NOW().timestamp()),
        },
        acquisition_requests=bundle.acquisition_requests,
        bundle=bundle,
        manifest=manifest,
    )


def _formal_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> FormalSuiteServiceInput:
    if request.command not in {
        "run-trace",
        "run-online-checks",
        "run-exp1-online",
        "run-exp5-online",
    }:
        raise ValueError("unsupported formal service authority command")
    if request.output_root is None:
        raise ValueError(f"{request.command} requires an output root")
    if request.command != "run-trace" and type(
        request.provider_authorization
    ) is not PaidAuthorizationValidation:
        raise ValueError(f"{request.command} requires Task26 paid authorization")
    authority = request._formal_authority
    if authority is None:
        bindings = ()
        if request.provider_authorization is not None:
            receipt = request.provider_authorization.receipt
            bindings = (
                _paid_scope_binding(
                    validation=request.provider_authorization,
                    scope=request.scope,
                    authorized_plan_digest=str(request.plan_digest),
                    profile_digest=request.profile.profile_digest,
                    budget_digest=receipt.budget_digest,
                    inventory_digest=str(request.inventory_digest),
                    prompt_admission_profile_digest=(
                        request.profile.prompt_admission_profile_digest
                    ),
                    output_root=request.output_root,
                ),
            )
        authority = _FORMAL_AUTHORITY_BUILDER(
            command=request.command,
            profile=request.profile,
            output_root=request.output_root,
            resume=bool(request.serialized_arguments.get("resume", False)),
            plan_bundle_root=request.plan_bundle_root,
            external_bank_resolver=request.external_bank_resolver,
            paid_authorization_bindings=bindings,
        )
    if getattr(authority, "plan_digest", None) != request.plan_digest:
        raise ValueError("formal authority plan digest mismatch")
    if getattr(authority, "inventory_digest", None) != request.inventory_digest:
        raise ValueError("formal authority inventory digest mismatch")
    if request.command != "run-trace":
        receipt = request.provider_authorization.receipt
        if getattr(authority, "budget_digest", None) != receipt.budget_digest:
            raise ValueError("formal authority receipt budget digest mismatch")
    keyword_arguments = getattr(authority, "keyword_arguments", None)
    if not isinstance(keyword_arguments, Mapping):
        raise ValueError("formal authority keyword arguments are missing")
    resolved_arguments = dict(keyword_arguments)
    if (
        request.command == "run-online-checks"
        and "online_root_callback_factory" not in resolved_arguments
    ):
        from decimal import Decimal

        from tokenshare.experiments.paper_budget import PaperBudgetLimits
        from tokenshare.experiments.paper_budget_ledger import (
            L3_ONLINE_CHECK_CATEGORY_POLICY,
            PaperBudgetLedger,
        )
        from tokenshare.experiments.paper_formal_callbacks import (
            PaperOnlineRootCallbackFactory,
        )

        profile_budget = request.profile.budget
        ledger = PaperBudgetLedger(
            request.output_root / "online_checks_budget.v1.sqlite3",
            limits=PaperBudgetLimits(
                calls=int(profile_budget.calls_hard_limit),
                tokens=int(profile_budget.tokens_hard_limit),
                cny=Decimal(profile_budget.cny_reservation_hard_limit),
                deepseek_cumulative_cny=Decimal(
                    profile_budget.cny_absolute_hard_stop
                ),
            ),
            category_policy=L3_ONLINE_CHECK_CATEGORY_POLICY,
        )
        resolved_arguments["online_root_callback_factory"] = (
            PaperOnlineRootCallbackFactory(
                budget_ledger=ledger,
                token_upper_bound=int(profile_budget.tokens_per_call),
                cost_upper_bound=Decimal(profile_budget.cny_per_call_reservation),
                prompt_admission_profile_digest=(
                    request.profile.prompt_admission_profile_digest
                ),
            )
        )
    return FormalSuiteServiceInput(
        scope=request.scope,
        keyword_arguments=resolved_arguments,
    )


def _smoke_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> FormalSuiteServiceInput:
    if request.command != "run-exp5-capability-smoke" or request.scope != (
        "exp5_capability_smoke"
    ):
        raise ValueError("full Exp5 scope cannot use capability smoke factory")
    if request.output_root is None:
        raise ValueError("capability smoke requires an output root")
    if type(request.provider_authorization) is not PaidAuthorizationValidation:
        raise ValueError("capability smoke requires Task26 paid authorization")
    authority = _SMOKE_AUTHORITY_BUILDER(
        profile=request.profile,
        output_root=request.output_root,
        resume=bool(request.serialized_arguments.get("resume", False)),
        paid_authorization=request.provider_authorization,
    )
    if getattr(authority, "scope", None) != request.scope:
        raise ValueError("capability smoke authority scope mismatch")
    if getattr(authority, "plan_digest", None) != request.plan_digest:
        raise ValueError("capability smoke authority plan digest mismatch")
    if getattr(authority, "inventory_digest", None) != request.inventory_digest:
        raise ValueError("capability smoke authority inventory digest mismatch")
    if getattr(authority, "budget_digest", None) != (
        request.provider_authorization.receipt.budget_digest
    ):
        raise ValueError("capability smoke receipt budget digest mismatch")
    keyword_arguments = getattr(authority, "keyword_arguments", None)
    if not isinstance(keyword_arguments, Mapping):
        raise ValueError("capability smoke keyword arguments are missing")
    classification = keyword_arguments.get("execution_plan")
    if getattr(classification, "execution_plan_digest", None) != request.inventory_digest:
        raise ValueError("capability smoke execution plan digest mismatch")
    return FormalSuiteServiceInput(
        scope=request.scope,
        keyword_arguments=dict(keyword_arguments),
    )


def _render_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> ReportRenderServiceInput:
    if request.output_root is None:
        raise ValueError("render requires an output root")
    from tokenshare.experiments.paper_formal_runner import (
        recompute_paper_formal_metrics_from_runner_inputs,
    )
    from tokenshare.experiments.paper_metric_contract import (
        load_paper_metric_contract,
    )

    return ReportRenderServiceInput(
        metrics=recompute_paper_formal_metrics_from_runner_inputs(
            request.output_root
        ),
        contract=load_paper_metric_contract(),
    )


def _lineage_service_input_from_persisted_authorities(
    request: PipelineCommandRequest,
) -> CellLineageAuditServiceInput:
    if request.output_root is None:
        raise ValueError("audit-cell-lineage requires an output root")
    from tokenshare.experiments.paper_formal_runner import (
        recompute_paper_formal_metrics_from_runner_inputs,
    )
    from tokenshare.experiments.paper_metric_contract import (
        load_paper_metric_contract,
    )

    metrics = recompute_paper_formal_metrics_from_runner_inputs(request.output_root)
    return CellLineageAuditServiceInput(
        contract=load_paper_metric_contract(),
        observations=tuple(metrics.metric_observations),
    )


_PRODUCTION_SERVICE_INPUT_FACTORIES: Mapping[str, ServiceInputFactory] = MappingProxyType(
    {
        "validate-profile": _no_service_input,
        "plan-bank": _no_service_input,
        "acquire-bank": _acquisition_service_input_from_persisted_authorities,
        "audit-bank": _no_service_input,
        "run-trace": _formal_service_input_from_persisted_authorities,
        "run-online-checks": _formal_service_input_from_persisted_authorities,
        "run-exp1-online": _formal_service_input_from_persisted_authorities,
        "run-exp5-capability-smoke": _smoke_service_input_from_persisted_authorities,
        "run-exp5-online": _formal_service_input_from_persisted_authorities,
        "render": _render_service_input_from_persisted_authorities,
        "replay": _no_service_input,
        "audit-cell-lineage": _lineage_service_input_from_persisted_authorities,
        "validate-formal-execution-gate": _provided_gate_service_input,
        "validate-paper-publication-gate": _provided_gate_service_input,
    }
)


def _formal_run_gate_input(request: PipelineCommandRequest) -> FormalRunGateServiceInput:
    from tokenshare.experiments.paper_formal_gate import (
        PaperGatePrerequisiteEnvelope,
        PaperGateSelectionEnvelope,
    )

    authority = request._formal_authority
    selection = getattr(authority, "gate_selection", None)
    prerequisites = getattr(authority, "gate_prerequisites", None)
    publication_factory = getattr(authority, "publication_gate_factory", None)
    if not isinstance(selection, PaperGateSelectionEnvelope) or not isinstance(
        prerequisites, PaperGatePrerequisiteEnvelope
    ):
        raise ValueError("formal run requires process-local typed gate authority")
    if not callable(publication_factory):
        raise TypeError("formal run typed gate authority is invalid")
    return FormalRunGateServiceInput(
        execution_gate=FormalExecutionGateServiceInput(
            selection=selection,
            prerequisites=prerequisites,
        ),
        publication_gate_factory=publication_factory,
    )


def _gate_blocked_service_result(
    request: PipelineCommandRequest,
    decision: Mapping[str, object],
    *,
    child: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result = dict(child or _service_result(request))
    result.update(
        {
            "status": "blocked",
            "stage": decision.get("stage"),
            "classification": decision.get("classification"),
            "blocked_reasons": list(decision.get("blocked_reasons", ())),
        }
    )
    return result


def _execute_gated_formal_service(
    request: PipelineCommandRequest,
    *,
    service_input: object,
    gate_input: FormalRunGateServiceInput,
) -> Mapping[str, object]:
    """在同一进程以 pre gate → official service → post gate 顺序执行。"""

    execution_request = replace(
        request,
        command="validate-formal-execution-gate",
        scope="validate-formal-execution-gate",
        evidence_class=_EVIDENCE_CLASSES["validate-formal-execution-gate"],
        provider_authorization=None,
        _service_input=gate_input.execution_gate,
    )
    execution = _validate_formal_execution_gate_adapter(execution_request)
    if execution.get("status") != "ready":
        return _gate_blocked_service_result(request, execution)

    child_request = replace(request, _service_input=service_input)
    child_value = _AUTHORITATIVE_SERVICE_ADAPTERS[request.command](child_request)
    if not isinstance(child_value, FormalSuiteServiceResult):
        raise TypeError("gated formal service must preserve typed terminal result")
    child = child_value.to_dict()
    if child.get("status") not in {"completed", "completed_with_failures", "ready"}:
        return child

    terminal_evidence = gate_input.publication_gate_factory(
        child_value.terminal_result
    )
    publication_input = PaperPublicationGateServiceInput(
        selection=gate_input.execution_gate.selection,
        terminal_evidence=terminal_evidence,
    )
    publication_request = replace(
        request,
        command="validate-paper-publication-gate",
        scope="validate-paper-publication-gate",
        evidence_class=_EVIDENCE_CLASSES["validate-paper-publication-gate"],
        provider_authorization=None,
        _service_input=publication_input,
    )
    publication = _validate_paper_publication_gate_adapter(publication_request)
    if publication.get("status") != "ready":
        return _gate_blocked_service_result(request, publication, child=child)
    return {
        **child,
        "execution_gate_status": "ready",
        "publication_gate_status": "ready",
    }


def _default_delegate(request: PipelineCommandRequest) -> Mapping[str, object]:
    """通过固定生产映射调用命令唯一的 official service adapter。"""

    factory = _PRODUCTION_SERVICE_INPUT_FACTORIES[request.command]
    if request.command in _FORMAL_GATED_COMMANDS:
        gate_input = _formal_run_gate_input(request)
        return _execute_gated_formal_service(
            request,
            service_input=factory(request),
            gate_input=gate_input,
        )
    resolved = replace(request, _service_input=factory(request))
    return _AUTHORITATIVE_SERVICE_ADAPTERS[request.command](resolved)


def _validate_prompt_admission(profile: Any) -> None:
    digest = getattr(profile, "prompt_admission_profile_digest", None)
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("prepared prompt admission digest is missing")
    budget = getattr(profile, "budget", None)
    bound_digest = getattr(budget, "prompt_admission_profile_digest", digest)
    if bound_digest != digest:
        raise ValueError("prepared prompt admission budget binding mismatch")


def _json_result(
    *,
    request: PipelineCommandRequest,
    delegated: Mapping[str, object] | Any,
) -> dict[str, object]:
    if isinstance(delegated, Mapping):
        body = delegated
    elif hasattr(delegated, "to_dict"):
        body = delegated.to_dict()
    else:
        raise TypeError("pipeline delegate must return a mapping or to_dict result")
    authorization = request.provider_authorization
    paid_authorization = (
        authorization
        if type(authorization) is PaidAuthorizationValidation
        else None
    )
    receipt_digest = (
        None
        if paid_authorization is None
        else paid_authorization.receipt.receipt_digest
    )
    marker_digest = (
        None
        if authorization is None
        else authorization.marker.marker_digest
    )
    delegated_plan_digest = body.get("plan_digest", request.plan_digest)
    delegated_inventory_digest = body.get(
        "inventory_digest", request.inventory_digest
    )
    if (
        request.plan_digest is not None
        and delegated_plan_digest != request.plan_digest
    ):
        raise ValueError("pipeline service plan digest mismatch")
    if (
        request.inventory_digest is not None
        and delegated_inventory_digest != request.inventory_digest
    ):
        raise ValueError("pipeline service inventory digest mismatch")
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": str(body.get("status", "completed")),
        "scope": request.scope,
        "profile_digest": request.profile.profile_digest,
        "budget_digest": (
            request.profile.budget_digest
            if authorization is None
            else getattr(
                getattr(authorization, "receipt", authorization),
                "budget_digest",
                request.profile.budget_digest,
            )
        ),
        "plan_digest": delegated_plan_digest,
        "inventory_digest": delegated_inventory_digest,
        "receipt_digest": receipt_digest,
        "output_marker_digest": marker_digest,
        "evidence_class": str(body.get("evidence_class", request.evidence_class)),
        "provider_calls": int(body.get("provider_calls", 0)),
    }
    if authorization is not None and paid_authorization is None:
        result.update(
            {
                "facility_authorization_schema": getattr(
                    authorization, "schema_version", None
                ),
                "facility_authorization_digest": getattr(
                    authorization, "authorization_digest", None
                ),
            }
        )
    for name in (
        "stage",
        "classification",
        "blocked_reasons",
        "facility_gate_verified",
        "paper_eligible",
        "bundle_digest",
        "inventory_entry_count",
        "completed_experiment_count",
        "root_run_count",
    ):
        if name in body:
            result[name] = body[name]
    return result


def execute_typed_gate_command(
    *,
    command: str,
    profile: PaperPipelineProfile | Any,
    output_root: str | Path,
    service_input: FormalExecutionGateServiceInput
    | PaperPublicationGateServiceInput,
) -> dict[str, object]:
    """从正式 producer 的 typed authority 执行 gate，不经过 argv/JSON 造证据。"""

    expected_type = {
        "validate-formal-execution-gate": FormalExecutionGateServiceInput,
        "validate-paper-publication-gate": PaperPublicationGateServiceInput,
    }.get(command)
    if expected_type is None:
        raise ValueError("typed gate command is unsupported")
    if not isinstance(service_input, expected_type):
        raise TypeError("typed gate command service input mismatch")
    request = PipelineCommandRequest(
        command=command,
        scope=command,
        evidence_class=_EVIDENCE_CLASSES[command],
        profile=profile,
        provider_authorization=None,
        output_root=Path(output_root),
        replay_input_root=None,
        external_bank_resolver=None,
        plan_digest=None,
        inventory_digest=None,
        budget_mode="bounded",
        serialized_arguments={
            "command": command,
            "profile_digest": profile.profile_digest,
        },
        _service_input=service_input,
    )
    return _json_result(request=request, delegated=_default_delegate(request))


def main(argv: Sequence[str] | None = None) -> int:
    from tokenshare.experiments.paper_formal_runner import (
        PaperInfrastructureBlockedError,
    )

    parser = _build_argument_parser()
    command_argv = tuple(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(command_argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        if getattr(args, "results_first_matrix8", False) is True:
            raise ValueError(
                "results-first matrix8 pipeline authority is retired"
            )
        profile = _PROFILE_LOADER(args.profile)
        provider = args.command in _PROVIDER_SCOPES
        authorization = None
        formal_authority = None
        if provider:
            output_mode = "new_run" if args.new_run else "resume"
            authorization_budget_digest = profile.budget_digest
            plan_bundle_root = getattr(args, "plan_bundle_root", None)
            acquisition_bundle = None
            if args.command == "acquire-bank" and plan_bundle_root is not None:
                acquisition_bundle = _ACQUISITION_BUNDLE_LOADER(plan_bundle_root)
                if acquisition_bundle.authorized_plan_digest != args.plan_digest:
                    raise ValueError("acquisition bundle plan digest mismatch")
                if acquisition_bundle.inventory_digest != args.inventory_digest:
                    raise ValueError("acquisition bundle inventory digest mismatch")
                if acquisition_bundle.profile_digest != profile.profile_digest:
                    raise ValueError("acquisition bundle profile digest mismatch")
                if acquisition_bundle.prompt_admission_profile_digest != (
                    profile.prompt_admission_profile_digest
                ):
                    raise ValueError("acquisition bundle admission digest mismatch")
                authorization_budget_digest = (
                    acquisition_bundle.full_budget.budget_digest
                )
            if args.command in {
                "run-online-checks",
                "run-exp1-online",
                "run-exp5-online",
            }:
                formal_authority = _FORMAL_AUTHORITY_BUILDER(
                    command=args.command,
                    profile=profile,
                    output_root=Path(args.output_root),
                    resume=bool(args.resume),
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                )
                if formal_authority.plan_digest != args.plan_digest:
                    raise ValueError("formal authority plan digest mismatch")
                if formal_authority.inventory_digest != args.inventory_digest:
                    raise ValueError("formal authority inventory digest mismatch")
                authorization_budget_digest = formal_authority.budget_digest
            receipt = _RECEIPT_LOADER(args.receipt)
            from tokenshare.experiments.paper_formal_gate import (
                selected_experiments_for_provider_scope,
            )

            authorization = _RECEIPT_VALIDATOR(
                receipt=receipt,
                requested_scope=_PROVIDER_SCOPES[args.command],
                authorized_plan_digest=args.plan_digest,
                profile_digest=profile.profile_digest,
                budget_digest=authorization_budget_digest,
                inventory_digest=args.inventory_digest,
                prompt_admission_profile_digest=(
                    profile.prompt_admission_profile_digest
                ),
                selected_experiments=selected_experiments_for_provider_scope(
                    _PROVIDER_SCOPES[args.command]
                ),
                output_root=args.output_root,
                output_mode=output_mode,
                action="dispatch",
                allow_provider_calls=args.allow_provider_calls,
                now=_UTC_NOW(),
            )
            if args.command in _FORMAL_GATED_COMMANDS:
                binding = _paid_scope_binding(
                    validation=authorization,
                    scope=_PROVIDER_SCOPES[args.command],
                    authorized_plan_digest=args.plan_digest,
                    profile_digest=profile.profile_digest,
                    budget_digest=authorization_budget_digest,
                    inventory_digest=args.inventory_digest,
                    prompt_admission_profile_digest=(
                        profile.prompt_admission_profile_digest
                    ),
                    output_root=args.output_root,
                )
                formal_authority = _FORMAL_AUTHORITY_BUILDER(
                    command=args.command,
                    profile=profile,
                    output_root=Path(args.output_root),
                    resume=bool(args.resume),
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                    paid_authorization_bindings=(binding,),
                )
                if formal_authority.plan_digest != args.plan_digest:
                    raise ValueError("formal authority plan digest mismatch")
                if formal_authority.inventory_digest != args.inventory_digest:
                    raise ValueError("formal authority inventory digest mismatch")
                if formal_authority.budget_digest != authorization_budget_digest:
                    raise ValueError("formal authority budget digest changed after receipt")
            _validate_prompt_admission(profile)
            _BUDGET_VALIDATOR(provider_writing=True, budget_mode=args.budget_mode)

        external_root = getattr(args, "external_bank_root", None)
        resolver = (
            None
            if external_root is None
            else ExternalBankResolverBinding(
                root=Path(external_root).resolve(strict=False)
            )
        )
        if args.command == "run-trace":
            trace_bindings = []
            acquisition_bundle = _ACQUISITION_BUNDLE_LOADER(args.plan_bundle_root)
            if acquisition_bundle.authorized_plan_digest != args.plan_digest:
                raise ValueError("trace acquisition bundle plan digest mismatch")
            if acquisition_bundle.inventory_digest != args.inventory_digest:
                raise ValueError("trace acquisition bundle inventory digest mismatch")
            if acquisition_bundle.profile_digest != profile.profile_digest:
                raise ValueError("trace acquisition bundle profile digest mismatch")
            if acquisition_bundle.prompt_admission_profile_digest != (
                profile.prompt_admission_profile_digest
            ):
                raise ValueError("trace acquisition bundle admission digest mismatch")

            bank_receipt_path = args.full_bank_acquisition_receipt
            if bank_receipt_path is not None:
                bank_validation = _RECEIPT_VALIDATOR(
                    receipt=_RECEIPT_LOADER(bank_receipt_path),
                    requested_scope="epd027_full_bank_acquisition",
                    authorized_plan_digest=acquisition_bundle.authorized_plan_digest,
                    profile_digest=acquisition_bundle.profile_digest,
                    budget_digest=acquisition_bundle.full_budget.budget_digest,
                    inventory_digest=acquisition_bundle.inventory_digest,
                    prompt_admission_profile_digest=(
                        acquisition_bundle.prompt_admission_profile_digest
                    ),
                    selected_experiments=("exp2", "exp3", "exp4"),
                    output_root=args.external_bank_root,
                    output_mode="resume",
                    action="reconcile_close",
                    allow_provider_calls=False,
                    now=_UTC_NOW(),
                )
                trace_bindings.append(
                    _paid_scope_binding(
                        validation=bank_validation,
                        scope="epd027_full_bank_acquisition",
                        authorized_plan_digest=(
                            acquisition_bundle.authorized_plan_digest
                        ),
                        profile_digest=acquisition_bundle.profile_digest,
                        budget_digest=acquisition_bundle.full_budget.budget_digest,
                        inventory_digest=acquisition_bundle.inventory_digest,
                        prompt_admission_profile_digest=(
                            acquisition_bundle.prompt_admission_profile_digest
                        ),
                        output_root=args.external_bank_root,
                    )
                )

            l3_root = args.l3_online_check_root
            l3_receipt_path = args.l3_online_check_receipt
            if l3_receipt_path is not None and l3_root is None:
                raise ValueError("L3 online-check receipt requires its evidence root")
            if l3_receipt_path is not None:
                l3_authority = _FORMAL_AUTHORITY_BUILDER(
                    command="run-online-checks",
                    profile=profile,
                    output_root=Path(l3_root),
                    resume=True,
                    plan_bundle_root=None,
                    external_bank_resolver=None,
                )
                l3_validation = _RECEIPT_VALIDATOR(
                    receipt=_RECEIPT_LOADER(l3_receipt_path),
                    requested_scope="epd027_l3_capability_and_online_checks",
                    authorized_plan_digest=l3_authority.plan_digest,
                    profile_digest=profile.profile_digest,
                    budget_digest=l3_authority.budget_digest,
                    inventory_digest=l3_authority.inventory_digest,
                    prompt_admission_profile_digest=(
                        profile.prompt_admission_profile_digest
                    ),
                    selected_experiments=("exp2", "exp3"),
                    output_root=l3_root,
                    output_mode="resume",
                    action="reconcile_close",
                    allow_provider_calls=False,
                    now=_UTC_NOW(),
                )
                trace_bindings.append(
                    _paid_scope_binding(
                        validation=l3_validation,
                        scope="epd027_l3_capability_and_online_checks",
                        authorized_plan_digest=l3_authority.plan_digest,
                        profile_digest=profile.profile_digest,
                        budget_digest=l3_authority.budget_digest,
                        inventory_digest=l3_authority.inventory_digest,
                        prompt_admission_profile_digest=(
                            profile.prompt_admission_profile_digest
                        ),
                        output_root=l3_root,
                    )
                )
            formal_authority = _FORMAL_AUTHORITY_BUILDER(
                command=args.command,
                profile=profile,
                output_root=Path(args.output_root),
                resume=False,
                plan_bundle_root=Path(args.plan_bundle_root),
                external_bank_resolver=resolver,
                paid_authorization_bindings=tuple(trace_bindings),
                l3_online_check_root=(None if l3_root is None else Path(l3_root)),
            )
            if formal_authority.plan_digest != args.plan_digest:
                raise ValueError("formal authority plan digest mismatch")
            if formal_authority.inventory_digest != args.inventory_digest:
                raise ValueError("formal authority inventory digest mismatch")
        evidence_class = (
            "online_real_provider"
            if provider
            else _EVIDENCE_CLASSES[args.command]
        )
        serialized_arguments = {
            "command": args.command,
            "profile_digest": profile.profile_digest,
            "budget_digest": profile.budget_digest,
            "plan_digest": getattr(args, "plan_digest", None),
            "inventory_digest": getattr(args, "inventory_digest", None),
            "plan_bundle_root": getattr(args, "plan_bundle_root", None),
            "output_mode": (
                None
                if not provider
                else ("new_run" if args.new_run else "resume")
            ),
            "resume": bool(getattr(args, "resume", False)),
        }
        request = PipelineCommandRequest(
            command=args.command,
            scope=_PROVIDER_SCOPES.get(args.command, args.command),
            evidence_class=evidence_class,
            profile=profile,
            provider_authorization=authorization,
            output_root=(
                None
                if not hasattr(args, "output_root")
                else Path(args.output_root)
            ),
            replay_input_root=(
                None
                if not hasattr(args, "replay_input_root")
                else Path(args.replay_input_root)
            ),
            external_bank_resolver=resolver,
            plan_digest=getattr(args, "plan_digest", None),
            inventory_digest=getattr(args, "inventory_digest", None),
            budget_mode=getattr(args, "budget_mode", "bounded"),
            serialized_arguments=serialized_arguments,
            plan_bundle_root=(
                None
                if getattr(args, "plan_bundle_root", None) is None
                else Path(args.plan_bundle_root)
            ),
            _formal_authority=formal_authority,
        )
        result = _json_result(
            request=request,
            delegated=_default_delegate(request),
        )
    except PaperInfrastructureBlockedError as exc:
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "blocked",
                    "scope": _PROVIDER_SCOPES.get(args.command, args.command),
                    "evidence_class": (
                        "online_real_provider"
                        if args.command in _PROVIDER_SCOPES
                        else _EVIDENCE_CLASSES.get(args.command)
                    ),
                    "provider_calls": 0,
                    "message": str(exc),
                    **exc.to_summary(),
                },
                sort_keys=True,
            )
        )
        return 3
    except (OSError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "status": "blocked",
                    "scope": _PROVIDER_SCOPES.get(args.command, args.command),
                    "evidence_class": (
                        "online_real_provider"
                        if args.command in _PROVIDER_SCOPES
                        else _EVIDENCE_CLASSES.get(args.command)
                    ),
                    "provider_calls": 0,
                    "failure_kind": "pipeline_boundary_rejected",
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result["status"] in {"completed", "completed_with_failures", "ready"}
        else 3
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcquisitionServiceInput",
    "CellLineageAuditServiceInput",
    "ExternalBankResolverBinding",
    "FormalExecutionGateServiceInput",
    "FormalRunGateServiceInput",
    "FormalSuiteServiceInput",
    "FormalSuiteServiceResult",
    "PAPER_PIPELINE_COMMANDS",
    "PipelineCommandRequest",
    "PaperPublicationGateServiceInput",
    "ReportRenderServiceInput",
    "execute_typed_gate_command",
    "main",
]
