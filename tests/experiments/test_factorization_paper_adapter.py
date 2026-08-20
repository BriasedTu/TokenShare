import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

import tokenshare.experiments.factorization_paper_adapter as factorization_paper_adapter_module
import tokenshare.experiments.paper_catalog as paper_catalog_module
from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
    ExecutionSubmission,
)
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    _failed_protocol_attempts_from_events,
    _paper_task_status_from_runtime,
    _prepare_config,
    _task_failure_from_child_evidence,
    _validate_factorization_case_for_adapter,
    run_factorization_paper_case,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_case_ai_unit_bindings,
    task_unit_snapshot_commitment,
)
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_factorization_catalog import (
    generate_factorization_paper_cases,
)
from tokenshare.experiments.paper_model_identity import (
    PaperModelIdentityMismatch,
    build_model_endpoint_identity,
)
from tokenshare.experiments.paper_faults import PaperFaultRuntimeHooks
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskStatus,
)
from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges
from tokenshare.plugins.contracts import OutputContract
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ProtocolRunCoordinator,
    RawOutputContext,
    RuntimeHookObservationV1,
    SequentialWorkerBackend,
    WorkerTerminationPolicy,
    build_experiment_ablation_gate_applied_observation,
)
from tokenshare.local_runtime.contracts import PreparedTraceDelivery
from tokenshare.experiments.paper_formal_callbacks import run_scheduled_cases
from tokenshare.storage.events import EventLedger, EventType


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"


@pytest.mark.parametrize(
    "runtime_drift",
    (None, "request_envelope", "attribution_ref", "attribution_count_missing"),
    ids=(
        "valid",
        "request-envelope-drift",
        "attribution-ref-drift",
        "attribution-count-missing",
    ),
)
def test_trace_range_calls_restore_accepted_failed_submission_missing_from_stage(
    tmp_path: Path,
    runtime_drift: str | None,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    submitted_at = "2026-08-17T00:00:00Z"
    environment_ref = EnvironmentRef(
        environment_id="trace-runtime",
        environment_digest="sha256:" + "1" * 64,
        runtime="python",
        tool_versions={},
        resource_limits={},
        fixture_profile_digest="sha256:" + "2" * 64,
        seed=1,
        clock_policy="fixed",
        created_at=submitted_at,
    )
    parser_input_digest = "sha256:" + "9" * 64
    parser_input_ref = store.save_json(
        {
            "schema_version": "tokenshare.trace_parser_input.v1",
            "media_type": "application/json",
            "content_digest": parser_input_digest,
            "source_bank_object_locators": [
                {
                    "bank_root_id": "bank-1",
                    "manifest_digest": "sha256:" + "5" * 64,
                    "entry_id": "exp1-entry-2",
                    "object_role": "provenance",
                    "object_digest": "sha256:" + "6" * 64,
                }
            ],
        },
        artifact_id="parser-input-attempt-2",
        artifact_type="TraceParserInput",
        artifact_schema_id="tokenshare.trace_parser_input",
        artifact_schema_version="v1",
        source={"kind": "response_bank"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    request = ExecutionRequest(
        request_id="request-2",
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-2",
        lease_id="lease-2",
        fencing_token="fencing-2",
        plugin={"plugin_id": "factorization", "plugin_version": "1"},
        executor={"executor_id": "trace_backed", "executor_version": "1"},
        registry_snapshot_id="registry-1",
        allocation_decision={},
        capability_snapshot={},
        task_unit_snapshot={},
        input_artifact_refs={},
        output_contract=OutputContract(
            output_contract_id="factor-output",
            required_outputs=[],
            output_schema_refs={},
            raw_output_policy={},
        ),
        hard_requirements={},
        soft_hints={
            "trace_inference_request_digest": "sha256:" + "4" * 64,
            "trace_source_entry_id": "exp1-entry-2",
        },
        environment_ref=environment_ref,
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={},
        created_at=submitted_at,
        attempt_ordinal=1,
        source_binding_digest="sha256:" + "3" * 64,
    )
    delivery = PreparedTraceDelivery.create(
        current_run_id="run-1",
        task_id=request.task_id,
        unit_id=request.unit_id,
        attempt_id=request.attempt_id,
        attempt_ordinal=request.attempt_ordinal,
        binding_digest=request.source_binding_digest,
        inference_request_digest="sha256:" + "4" * 64,
        bank_root_id="bank-1",
        manifest_digest="sha256:" + "5" * 64,
        entry_id="exp1-entry-2",
        source_terminal_kind="provider_failure",
        source_bank_object_locators=(
            {
                "bank_root_id": "bank-1",
                "manifest_digest": "sha256:" + "5" * 64,
                "entry_id": "exp1-entry-2",
                "object_role": "provenance",
                "object_digest": "sha256:" + "6" * 64,
            },
        ),
        logical_start_ms=0,
        source_latency_ms=1,
        parser_input_media_type="application/json",
        parser_input_digest=parser_input_digest,
        child_worker_id="worker-1",
        child_completion_sequence=1,
    )
    delivery_ref = store.save_json(
        delivery.to_dict(),
        artifact_id="delivery-attempt-2",
        artifact_type="CurrentTraceWrapper",
        artifact_schema_id="tokenshare.current_trace_wrapper",
        artifact_schema_version="v1",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    parser_result_ref = store.save_json(
        {"failure_kind": "no_response", "message": "no response"},
        artifact_id="parser-result-attempt-2",
        artifact_type="TraceProviderFailure",
        artifact_schema_id="tokenshare.trace_provider_failure",
        artifact_schema_version="v1",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    provenance_ref = store.save_json(
        {"current_provider_call_count": 0},
        artifact_id="provenance-attempt-2",
        artifact_type="CurrentTraceProvenance",
        artifact_schema_id="tokenshare.current_trace_provenance",
        artifact_schema_version="v1",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    attribution_ref = store.save_json(
        (
            {}
            if runtime_drift == "attribution_count_missing"
            else {"current_provider_call_count": 0}
        ),
        artifact_id="attribution-attempt-2",
        artifact_type="TraceAttribution",
        artifact_schema_id="tokenshare.trace_attribution",
        artifact_schema_version="v1",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    failed_submission = ExecutionSubmission(
        submission_id="submission-attempt-2",
        request_id="request-2",
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-2",
        lease_id="lease-2",
        fencing_token="fencing-2",
        executor_id="trace_backed",
        executor_version="1",
        result_kind="failed",
        raw_output_ref=delivery_ref,
        parsed_output_ref=parser_result_ref,
        candidate_output_refs={},
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=environment_ref,
        environment_summary={"runtime": "trace_backed"},
        provenance_ref=provenance_ref,
        usage_summary={
            "provider_attempt_count": 0,
            "current_provider_call_count": 0,
        },
        error={"kind": "no_response"},
        submitted_at=submitted_at,
    )
    submission_ref = store.save_json(
        failed_submission.to_dict(),
        artifact_id="failed-submission-attempt-2",
        artifact_type="ExecutionSubmission",
        artifact_schema_id="phase3.execution_submission",
        artifact_schema_version="v1",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    request_ref = store.save_json(
        request.to_dict(),
        artifact_id="request-attempt-2",
        artifact_type="ExecutionRequest",
        artifact_schema_id="phase3.execution_request",
        artifact_schema_version="v2",
        source={"kind": "protocol_runtime"},
        metadata={"attempt_id": "attempt-2"},
        created_at=submitted_at,
    )
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append(
        event_type=EventType.EXECUTION_REQUEST_RECORDED,
        object_type=(
            "wrong_request_type"
            if runtime_drift == "request_envelope"
            else "ExecutionRequest"
        ),
        object_id=request.request_id,
        task_id=request.task_id,
        payload={
            "attempt_id": request.attempt_id,
            "request_id": request.request_id,
            "task_id": request.task_id,
            "unit_id": request.unit_id,
            "lease_id": request.lease_id,
            "request_ref": request_ref.to_dict(),
            "request_digest": request_ref.content_hash,
        },
        idempotency_key="request-attempt-2",
        occurred_at=submitted_at,
    )
    ledger.append(
        event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
        object_type="ExecutionSubmission",
        object_id=failed_submission.submission_id,
        task_id=failed_submission.task_id,
        payload={
            "attempt_id": failed_submission.attempt_id,
            "request_id": failed_submission.request_id,
            "task_id": failed_submission.task_id,
            "unit_id": failed_submission.unit_id,
            "lease_id": failed_submission.lease_id,
            "submission_id": failed_submission.submission_id,
            "result_kind": failed_submission.result_kind,
            "submission_ref": submission_ref.to_dict(),
            "submission_digest": submission_ref.content_hash,
            "acceptance_status": "accepted",
        },
        idempotency_key="accepted-failed-submission-attempt-2",
        occurred_at=submitted_at,
    )
    ledger.append(
        event_type=EventType.TRACE_DELIVERY_COMMITTED,
        object_type="TraceConsumption",
        object_id="attempt-2",
        task_id=failed_submission.task_id,
        payload={
            "attempt_id": failed_submission.attempt_id,
            "binding_digest": delivery.binding_digest,
            "current_fencing_token": request.fencing_token,
            "status": "delivered",
            "current_wrapper_ref": delivery_ref.to_dict(),
            "parser_input_ref": parser_input_ref.to_dict(),
            "parser_result_ref": parser_result_ref.to_dict(),
            "current_provenance_ref": provenance_ref.to_dict(),
            "verifier_checker_refs": [],
            "canonical_ref": None,
            "trace_attribution_refs": [
                {
                    **attribution_ref.to_dict(),
                    **(
                        {"content_hash": "sha256:" + "f" * 64}
                        if runtime_drift == "attribution_ref"
                        else {}
                    ),
                }
            ],
        },
        idempotency_key="trace-delivery-attempt-2",
        occurred_at=submitted_at,
    )

    def restore_calls():
        return factorization_paper_adapter_module._trace_range_calls_from_events(
            requests={},
            deliveries={},
            staged_submissions={},
            runtime_events=ledger.read_all(),
            request_refs_by_id={"request-2": request_ref},
            store=store,
        )

    if runtime_drift is not None:
        with pytest.raises(ValueError, match="trace"):
            restore_calls()
        return
    calls = restore_calls()

    assert len(calls) == 1
    assert calls[0].submission.result_kind == "failed"
    assert calls[0].submission.error == {"kind": "no_response"}
    assert calls[0].submission.usage_summary == {
        "provider_attempt_count": 0,
        "entry_id": "exp1-entry-2",
        "current_provider_call_count": 0,
    }


@pytest.fixture(autouse=True)
def _use_tracked_lean_catalog_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    tracked_manifest = json.loads(
        (repo_root / "benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "default_lean_fixture_project_path",
        lambda: repo_root / "fixtures/lean_proof_project",
    )
    environment_manifest = (
        paper_catalog_module._current_lean_environment_manifest_without_preflight()
    )
    object.__setattr__(
        environment_manifest,
        "environment_digest",
        tracked_manifest["environment_digest"],
    )
    oracle_source = (
        repo_root
        / "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean"
    ).resolve()
    original_file_digest = paper_catalog_module._file_digest

    def worktree_file_digest(path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved != oracle_source:
            return original_file_digest(resolved)
        logical_source = resolved.read_text(encoding="utf-8")
        logical_source = logical_source.replace("\r\n", "\n").replace("\r", "\n")
        return f"sha256:{sha256(logical_source.encode('utf-8')).hexdigest()}"

    monkeypatch.setattr(
        paper_catalog_module,
        "_current_lean_environment_manifest_without_preflight",
        lambda: environment_manifest,
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "lean_checker_implementation_digest",
        lambda: tracked_manifest["checker_implementation_digest"],
    )
    monkeypatch.setattr(
        paper_catalog_module,
        "_file_digest",
        worktree_file_digest,
    )


def test_factorization_attempt_producer_persists_strict_current_provider_count(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    request_ref = store.save_json(
        {"request": "body"},
        artifact_id="factor-provider-count-request",
        artifact_type="ExecutionRequest",
        artifact_schema_id="test.execution_request",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-11T00:00:00Z",
    )

    def produce(
        name: str,
        *,
        submission_count=1,
        persisted_count=1,
        provenance_count=1,
        provider_attempts=({"latency_ms": 1},),
        trace: bool = False,
        trace_current_calls=0,
    ):
        usage_summary = {
            "provider_family": "siliconflow",
            "model": "test-model",
            "entry_id": "test-entry",
        }
        persisted_usage = dict(usage_summary)
        if submission_count != "missing":
            usage_summary["provider_attempt_count"] = submission_count
        if persisted_count != "missing":
            persisted_usage["provider_attempt_count"] = persisted_count
        if trace:
            usage_summary.update(
                current_provider_call_count=trace_current_calls,
                source_usage_class="trace_attribution",
            )
            persisted_usage.update(
                current_provider_call_count=trace_current_calls,
                source_usage_class="trace_attribution",
            )
        usage_ref = store.save_json(
            persisted_usage,
            artifact_id=f"factor-provider-count-usage-{name}",
            artifact_type="AIUsageSummary",
            artifact_schema_id="tokenshare.paper_ai_usage",
            artifact_schema_version="v1",
            source={"kind": "test"},
            metadata={},
            created_at="2026-08-11T00:00:00Z",
        )
        provenance_ref = (
            None
            if trace
            else store.save_json(
                {
                    "attempts": list(provider_attempts),
                    **(
                        {"provider_attempt_count": provenance_count}
                        if provenance_count != "missing"
                        else {}
                    ),
                },
                artifact_id=f"factor-provider-count-provenance-{name}",
                artifact_type="AIExecutionProvenance",
                artifact_schema_id="test.ai_provenance",
                artifact_schema_version="v1",
                source={"kind": "test"},
                metadata={},
                created_at="2026-08-11T00:00:00Z",
            )
        )
        submission = SimpleNamespace(
            usage_summary=usage_summary,
            provenance_ref=provenance_ref,
            raw_output_ref=request_ref,
            parsed_output_ref=None,
            parse_failure_ref=None,
            error=None,
        )
        request = SimpleNamespace(
            task_id="factor-task",
            unit_id="factor-unit",
            attempt_id=f"factor-attempt-{name}",
        )
        return factorization_paper_adapter_module._paper_attempt_result(
            store=store,
            condition=_condition("sha256:" + "1" * 64),
            case_id="factor-case",
            index=0,
            request=request,
            request_ref=request_ref,
            submission=submission,
            usage_ref=usage_ref,
            attempt_status=PaperAttemptStatus.SUCCEEDED,
            planned_ai_unit_id="range_0",
        )

    assert produce("transport-success").provider_attempt_count == 1
    assert produce(
        "provider-failure",
        provider_attempts=({"result_kind": "provider_error"},),
    ).provider_attempt_count == 1
    assert produce(
        "pretransport-secret-missing",
        submission_count=0,
        persisted_count=0,
        provenance_count=0,
        provider_attempts=({"result_kind": "secret_missing"},),
    ).provider_attempt_count == 0
    assert produce(
        "pretransport-config-error",
        submission_count=0,
        persisted_count=0,
        provenance_count=0,
        provider_attempts=({"result_kind": "config_error"},),
    ).provider_attempt_count == 0
    assert produce(
        "trace",
        submission_count=0,
        persisted_count=0,
        provider_attempts=(),
        trace=True,
    ).provider_attempt_count == 0
    for name, kwargs in (
        ("missing", {"submission_count": "missing", "persisted_count": "missing"}),
        ("invalid", {"submission_count": True, "persisted_count": True}),
        ("usage-conflict", {"submission_count": 1, "persisted_count": 0}),
        ("provenance-missing", {"provenance_count": "missing"}),
        ("provenance-bool", {"provenance_count": True}),
        ("provenance-negative", {"provenance_count": -1}),
        ("provenance-string", {"provenance_count": "1"}),
        ("provenance-conflict", {"provenance_count": 0}),
        ("inventory-invalid", {"provider_attempts": (None,)}),
        (
            "trace-bool-calls",
            {
                "submission_count": 0,
                "persisted_count": 0,
                "provider_attempts": (),
                "trace": True,
                "trace_current_calls": False,
            },
        ),
        (
            "trace-negative-calls",
            {
                "submission_count": 0,
                "persisted_count": 0,
                "provider_attempts": (),
                "trace": True,
                "trace_current_calls": -1,
            },
        ),
        (
            "trace-string-calls",
            {
                "submission_count": 0,
                "persisted_count": 0,
                "provider_attempts": (),
                "trace": True,
                "trace_current_calls": "0",
            },
        ),
    ):
        with pytest.raises(ValueError, match="provider attempt count evidence"):
            produce(name, **kwargs)


def test_factorization_catalog_fixture_is_bound_to_current_worktree() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    assert paper_catalog_module.default_lean_fixture_project_path().resolve().is_relative_to(
        repo_root
    )


class _AlwaysServerErrorFactorizationTransport:
    """让真实 executor/coordinator 走到 retry_limit_reached 的离线 transport。"""

    def post_chat_completion(
        self,
        *,
        api_key: str,
        body_bytes: bytes,
        normalized_absolute_endpoint: str,
        content_type: str,
        timeout_seconds: int,
    ):
        del api_key, body_bytes, normalized_absolute_endpoint, content_type, timeout_seconds
        return factorization_paper_adapter_module._ProviderResponse(
            status_code=500,
            body={"error": {"message": "offline injected server error"}},
        )


class _FailBeforeProviderExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        del request, submission_id, submitted_at
        self.calls += 1
        raise RuntimeError("offline deterministic root failure")


def test_factorization_adapter_officially_parses_runtime_hook_observations() -> None:
    observation = build_experiment_ablation_gate_applied_observation(
        ablation_mode="NO_VERIFICATION",
        disabled_mechanism="verification",
        protocol_event_refs=(),
        artifact_refs=(),
        hook_input={
            "task_id": "task-1",
            "unit_id": "unit-1",
            "attempt_id": "attempt-1",
            "lease_id": "lease-1",
        },
        hook_result={"bypass": True, "stop": False},
    )

    parsed = factorization_paper_adapter_module._parse_runtime_hook_observations(
        (observation.to_dict(),)
    )

    assert parsed == (observation,)
    assert all(isinstance(item, RuntimeHookObservationV1) for item in parsed)

    tampered = observation.to_dict()
    tampered["payload"]["disabled_mechanism"] = "requeue"
    with pytest.raises(ValueError, match="observation_digest mismatch"):
        factorization_paper_adapter_module._parse_runtime_hook_observations(
            (tampered,)
        )


@pytest.mark.parametrize(
    ("mode", "mechanism", "business_result_field"),
    (
        ("NO_MERGE_GATE", "merge_gate", "premature_merge_attempted"),
        ("NO_SLOT_INTEGRITY", "slot_integrity", "slot_integrity_violation"),
    ),
)
def test_factorization_ablation_gate_alone_does_not_claim_business_result(
    mode: str,
    mechanism: str,
    business_result_field: str,
) -> None:
    observation = build_experiment_ablation_gate_applied_observation(
        ablation_mode=mode,
        disabled_mechanism=mechanism,
        protocol_event_refs=(),
        artifact_refs=(),
        hook_input={
            "task_id": "task-1",
            "unit_id": "unit-1",
            "attempt_id": "attempt-1",
            "lease_id": "lease-1",
        },
        hook_result={"bypass": True, "stop": False},
    )

    runtime = factorization_paper_adapter_module._ablation_runtime_evidence(
        mode=mode,
        attempts=[],
        merge_summary={},
        hook_observations=(observation,),
    )

    assert runtime[business_result_field] is False
    assert runtime["hook_observations"] == [observation.to_dict()]


def test_factorization_v2_all_500_cases_pass_adapter_complete_domain_preflight() -> None:
    cases = generate_factorization_paper_cases()

    for case in cases:
        _validate_factorization_case_for_adapter(case)

    assert len(cases) == 500


def test_factorization_runtime_status_projection_is_terminal_and_fail_closed() -> None:
    assert (
        _paper_task_status_from_runtime("completed", accepted_validity=True)
        == PaperTaskStatus.COMPLETED
    )
    assert (
        _paper_task_status_from_runtime("completed", accepted_validity=False)
        == PaperTaskStatus.FAILED
    )
    assert (
        _paper_task_status_from_runtime("failed", accepted_validity=True)
        == PaperTaskStatus.FAILED
    )
    for invalid_status in ("processing", "unknown"):
        with pytest.raises(ValueError, match="terminal runtime status"):
            _paper_task_status_from_runtime(
                invalid_status,
                accepted_validity=False,
            )


def test_factorization_v2_easy_semiprime_completes_parser_verifier_canonical_and_merge(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy"
        and case["factor_position_quantile"] != "no_factor"
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert len(transport.calls) == 2
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.final_prime_factors == case["oracle_prime_factors"]
    assert result.merge_summary["result_kind"] == "prime_factorization_result"
    assert all(item["verification"]["accepted"] for item in result.range_results)
    assert all(item["canonical_output_ref"] for item in result.range_results)
    assert result.run_evidence["protocol_runtime"]["event_ledger_path"] == (
        f"events/{result.task_result.task_id}.jsonl"
    )


def test_factorization_v2_hard_prime_control_merges_complete_no_factor_ranges(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "hard"
        and case["factor_position_quantile"] == "no_factor"
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert len(transport.calls) == 8
    assert all(
        call["range_result"]["result_kind"] == "no_factor_in_range"
        for call in transport.calls
    )
    assert all(item["verification"]["accepted"] for item in result.range_results)
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.final_prime_factors == [
        {"prime": case["target_n"], "exponent": 1}
    ]
    link = next(
        event["payload"]["merge_task_link"]
        for event in result.event_records
        if event["event_type"] == "MERGE_TASK_LINK_RECORDED"
    )
    assert link["readiness_reason"] == (
        "all_required_ranges_no_factor_canonical"
    )
    assert len(link["required_slot_bindings"]) == len(transport.calls)


@pytest.mark.parametrize(
    ("failure_mode", "expected_failed_attempt"),
    (
        ("provider_error", PaperAttemptStatus.PROVIDER_ERROR),
        ("verifier_rejected", PaperAttemptStatus.VERIFICATION_REJECTED),
    ),
)
def test_factorization_verified_witness_completes_despite_terminal_sibling_failure(
    tmp_path,
    failure_mode,
    expected_failed_attempt,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy"
        and case["factor_position_quantile"] != "no_factor"
    )
    transport = _WitnessWithOneFailedSiblingTransport(mode=failure_mode)

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert transport.witness_count == 1
    assert transport.failed_sibling_count == 1
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    failed_attempt = next(
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == expected_failed_attempt
    )
    assert any(
        attempt.attempt_status == expected_failed_attempt
        for attempt in result.attempt_results
    )
    event_types = [event["event_type"] for event in result.event_records]
    assert "MERGE_RECORDED" in event_types
    assert "SETTLEMENT_RECORDED" in event_types
    link_event = next(
        event
        for event in result.event_records
        if event["event_type"] == "MERGE_TASK_LINK_RECORDED"
    )
    link = link_event["payload"]["merge_task_link"]
    assert link["schema_version"] == "phase5.merge_task_link.v2"
    assert link["readiness_reason"] == "verified_factor_witness_canonical"
    assert link["readiness_decision"]["schema_version"] == (
        "tokenshare.merge_readiness_decision.v1"
    )
    assert link["readiness_decision"]["policy_id"] == (
        "factorization.factor_witness_or_all_ranges.v2"
    )
    assert len(link["required_slot_bindings"]) == 1
    # worker batch 已启动的 sibling 证据必须保留，witness completion 不做删除或改写。
    for event_type in (
        "EXECUTION_REQUEST_RECORDED",
        "EXECUTION_SUBMISSION_RECORDED",
    ):
        assert any(
            event["event_type"] == event_type
            and event["payload"]["unit_id"] == failed_attempt.unit_id
            for event in result.event_records
        )


def test_factorization_no_factor_conclusion_requires_every_range_canonical(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "hard"
        and case["factor_position_quantile"] == "no_factor"
    )
    transport = _WitnessWithOneFailedSiblingTransport(mode="provider_error")

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert transport.witness_count == 0
    assert transport.failed_sibling_count == 1
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    event_types = [event["event_type"] for event in result.event_records]
    assert "MERGE_RECORDED" not in event_types
    assert "SETTLEMENT_RECORDED" not in event_types


def test_factorization_rejected_false_negative_cannot_trigger_witness_completion(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy"
        and case["factor_position_quantile"] != "no_factor"
    )
    partition = _v2_partition(case)
    oracle_primes = {int(item["prime"]) for item in case["oracle_prime_factors"]}
    factor_child_index = next(
        item.child_index
        for item in partition.ranges
        if any(
            int(item.range_start) <= prime <= int(item.range_end)
            for prime in oracle_primes
        )
    )
    transport = ScriptedFactorizationRangeTransport(
        force_false_negative_child_indices={factor_child_index}
    )

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert any(
        attempt.attempt_status == PaperAttemptStatus.VERIFICATION_REJECTED
        for attempt in result.attempt_results
    )
    event_types = [event["event_type"] for event in result.event_records]
    assert "MERGE_RECORDED" not in event_types
    assert "SETTLEMENT_RECORDED" not in event_types


def test_factorization_forged_witness_slot_identity_is_rejected(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy"
        and case["factor_position_quantile"] != "no_factor"
    )
    transport = _ForgedWitnessIdentityTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert transport.forged_witness_count == 1
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert any(
        attempt.attempt_status == PaperAttemptStatus.VERIFICATION_REJECTED
        for attempt in result.attempt_results
    )
    event_types = [event["event_type"] for event in result.event_records]
    assert "MERGE_RECORDED" not in event_types
    assert "SETTLEMENT_RECORDED" not in event_types


def test_generic_core_does_not_encode_factorization_witness_vocabulary() -> None:
    core_root = Path(__file__).parents[2] / "src" / "tokenshare" / "core"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(core_root.glob("*.py"))
    )

    assert "found_factor" not in source


def test_factorization_v2_large_no_factor_range_uses_canonical_child_length_as_budget(
    tmp_path,
) -> None:
    case, child_index, child_length = _v2_case_with_large_no_factor_range()
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id=f"range_{child_index}",
    )

    assert child_length > 100_000
    assert transport.calls[0]["range_result"]["result_kind"] == "no_factor_in_range"
    assert result.range_results[0]["verification"]["accepted"] is True
    assert (
        result.range_results[0]["verification"]["layer_summary"]["details"]
        ["checked_divisor_count"]
        == child_length
    )


def test_factorization_v2_large_range_false_no_factor_is_rejected_after_full_recheck(
    tmp_path,
) -> None:
    case, child_index, child_length = _v2_case_with_large_factor_range()
    transport = ScriptedFactorizationRangeTransport(
        force_false_negative_child_indices={child_index}
    )

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id=f"range_{child_index}",
    )

    assert child_length > 100_000
    assert result.range_results[0]["verification"]["accepted"] is False
    assert (
        result.range_results[0]["verification"]["layer_summary"]["reason_code"]
        == "divisor_exists_in_range"
    )
    assert result.task_result.failure_stage == PaperFailureStage.VERIFICATION
    assert result.task_result.failure_kind == PaperFailureKind.VERIFIER_REJECTED


def test_factorization_paper_adapter_runs_range_children_through_ai_api_executor_and_merges(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.schema_version == "tokenshare.factorization_paper_run.v1"
    assert result.split_summary["split_strategy_id"] == "factorization.candidate_range_partition.v1"
    assert result.split_summary["range_child_count"] == case["split_params"]["requested_child_count"]
    assert len(transport.calls) == result.task_result.provider_attempt_count
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_eligible is False
    assert result.eligibility_report.paper_eligible is False
    assert "real_transport_required" in result.eligibility_report.ineligibility_reasons
    assert "unsupported_transport:scripted" in result.eligibility_report.ineligibility_reasons
    assert "secret_scan_failed" not in result.eligibility_report.ineligibility_reasons
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert result.run_evidence["secret_scan_report"]["leak_count"] == 0

    assert result.final_prime_factors == case["oracle_prime_factors"]
    assert result.merge_summary["result_kind"] == "prime_factorization_result"
    assert len(result.attempt_results) == result.split_summary["range_child_count"]
    assert sum(
        attempt.provider_attempt_count for attempt in result.attempt_results
    ) == result.task_result.provider_attempt_count
    assert len(result.range_results) == result.split_summary["range_child_count"]
    assert all(item["verification"]["accepted"] is True for item in result.range_results)
    assert all(item["canonical_output_ref"] for item in result.range_results)


def test_factorization_adapter_resolves_exp2_20way_profile_inside_plugin(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "hard"
        and case["factor_position_quantile"] == "no_factor"
        and int(case["candidate_end"]) - int(case["candidate_start"]) + 1 >= 20
    )
    case = {
        **case,
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "range_policy": "contiguous",
            "split_profile_id": "factorization.exp2_contiguous_20way.v1",
        },
    }
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert 1 <= len(transport.calls) <= 20
    assert result.split_summary["range_child_count"] == 20
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.usage_ref is not None for attempt in result.attempt_results)
    assert all(attempt.provider == "siliconflow" for attempt in result.attempt_results)
    assert all(attempt.entry_id == "factorization_paper_scripted" for attempt in result.attempt_results)
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    provider_prompt = transport.calls[0]["body_bytes"].decode("utf-8")
    assert "factorization.range_result.v1" in provider_prompt
    assert "Search divisor range" in provider_prompt
    assert "direct_factorization_answer" not in provider_prompt


def test_factorization_paper_adapter_can_execute_exactly_one_selected_ai_unit(
    tmp_path,
    monkeypatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport()
    coordinator_calls = 0
    original = ProtocolRunCoordinator.run_root

    def recording(self, request):
        nonlocal coordinator_calls
        coordinator_calls += 1
        return original(self, request)

    monkeypatch.setattr(ProtocolRunCoordinator, "run_root", recording)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id="range_0",
    )

    assert len(transport.calls) == 1
    assert coordinator_calls == 1
    assert len(result.attempt_results) == 1
    assert result.attempt_results[0].planned_ai_unit_id == "range_0"
    assert result.merge_summary["status"] == "partial"
    assert result.task_result.root_status == PaperTaskStatus.PARTIAL
    assert result.task_result.paper_eligible is False
    event_types = [event["event_type"] for event in result.event_records]
    for event_type in (
        "TASK_REGISTERED",
        "TASK_EXPANDED",
        "LEASE_STATE_CHANGED",
        "EXECUTION_REQUEST_RECORDED",
        "EXECUTION_SUBMISSION_RECORDED",
        "VERIFICATION_RECORDED",
        "CANONICAL_OUTPUTS_BOUND",
    ):
        assert event_type in event_types
    assert "MERGE_RECORDED" not in event_types
    assert "SETTLEMENT_RECORDED" not in event_types

    untouched_transport = ScriptedFactorizationRangeTransport()
    with pytest.raises(ValueError, match="selected_ai_unit_id"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path / "invalid-selection",
            transport=untouched_transport,
            real_transport=False,
            entry_id="factorization_paper_scripted",
            selected_ai_unit_id="range_99",
        )
    assert untouched_transport.calls == []


def test_factorization_paper_adapter_blocks_merge_when_verifier_rejects_range_result(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport(force_false_negative_child_indices={0})

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.task_result.failure_stage == PaperFailureStage.VERIFICATION
    assert result.task_result.failure_kind == PaperFailureKind.VERIFIER_REJECTED
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert any(item["verification"]["accepted"] is False for item in result.range_results)


def test_factorization_paper_adapter_reports_parse_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_InvalidJsonTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert any(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


@pytest.mark.parametrize(
    "mode",
    (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    ),
)
def test_factorization_exp4_ablation_modes_execute_inside_adapter_lifecycle(
    tmp_path: Path,
    mode: str,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = (
        _InvalidJsonTransport()
        if mode == "NO_PARSER_POLICY"
        else ScriptedFactorizationRangeTransport(
            force_false_negative_child_indices=(
                {0}
                if mode in {"NO_VERIFICATION", "NO_MERGE_GATE"}
                else set()
            )
        )
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / mode,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        ablation_mode=mode,
    )

    runtime = result.run_evidence["ablation_runtime"]
    assert runtime["mode"] == mode
    assert runtime["applied_before_adapter_completion"] is True
    if mode == "FULL":
        assert result.task_result.root_status == PaperTaskStatus.COMPLETED
        assert runtime["disabled_mechanism"] is None
    elif mode == "NO_VERIFICATION":
        assert any(
            item["verification"]["status"] == "passed"
            and item["verification"]["metadata"] == {
                "ablation_mode": "NO_VERIFICATION",
                "domain_verifier_invoked": False,
            }
            and item["canonical_output_ref"] is not None
            for item in result.range_results
        )
        assert any(
            observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
            and observation["payload"]["disabled_mechanism"] == "verification"
            for observation in runtime["hook_observations"]
        )
        assert result.task_result.accepted_validity is False
        native = result.run_evidence["paper_direct_native_artifacts"]
        verdict_ref = ArtifactRef.from_dict(native["independent_verdict_ref"])
        final_ref = ArtifactRef.from_dict(
            next(
                event["payload"]["merge_output_refs"]["prime_factorization_result"]
                for event in result.event_records
                if event["event_type"] == "MERGE_RECORDED"
            )
        )
        report = json.loads(
            ArtifactStore(result.output_root).read_bytes(verdict_ref).decode("utf-8")
        )
        assert verdict_ref.artifact_id != final_ref.artifact_id
        assert report["schema_version"] == (
            "tokenshare.paper_factorization_domain_verifier_report.v1"
        )
        assert report["case_id"] == case["case_id"]
        assert report["execution_id"] == result.run_evidence["protocol_runtime"][
            "run_id"
        ]
        assert report["task_id"] == result.run_evidence["protocol_runtime"]["task_id"]
        assert report["root_unit_id"] == result.run_evidence["protocol_runtime"][
            "root_unit_id"
        ]
        assert report["final_result_ref"] == final_ref.to_dict()
        assert report["environment_ref"]["environment_digest"].startswith("sha256:")
        assert report["status"] == "rejected"
        assert report["correct"] is False
        assert report["report_digest"].startswith("sha256:")
    elif mode == "NO_PARSER_POLICY":
        assert all(
            item.raw_output_ref is not None
            and item.parsed_output_ref is not None
            and item.parsed_output_ref["content_hash"]
            == item.raw_output_ref["content_hash"]
            for item in result.attempt_results
        )
        assert runtime["raw_only_exposed"] is True
        assert any(
            observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
            and observation["payload"]["disabled_mechanism"] == "parser_policy"
            for observation in runtime["hook_observations"]
        )
    elif mode == "NO_REQUEUE":
        assert runtime["replacement_attempts_allowed"] is False
        assert result.task_result.attempt_count == result.split_summary[
            "range_child_count"
        ]
    elif mode == "NO_MERGE_GATE":
        assert runtime["premature_merge_attempted"] is True
        assert runtime["root_validity_audit_passed"] is False
        assert any(
            observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
            and observation["payload"]["disabled_mechanism"] == "merge_gate"
            for observation in runtime["hook_observations"]
        )
    else:
        assert runtime["slot_integrity_violation"] is True
        assert runtime["root_validity_audit_passed"] is True
        assert result.merge_summary["slot_integrity_violation"] is True
        assert (
            result.merge_summary["slot_binding_applied_by"]
            == "local_runtime_policy"
        )
        assert any(
            observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
            and observation["payload"]["disabled_mechanism"] == "slot_integrity"
            for observation in runtime["hook_observations"]
        )


def test_factorization_paper_adapter_reports_provider_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ProviderErrorTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.failure_kind == PaperFailureKind.PROVIDER_ERROR
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )
    assert all(
        attempt.provider_attempt_count == 1 for attempt in result.attempt_results
    )
    assert all(
        attempt.to_dict()["provider_attempt_count"] == 1
        for attempt in result.attempt_results
    )


def test_factorization_paper_adapter_rejects_custom_transport_marked_real(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibSiliconFlowTransport"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=_CustomRealTransportSubclass(),
            real_transport=True,
            ai_api_config=_real_transport_config(),
        )


def test_factorization_real_transport_guard_accepts_provider_router_wrapper() -> None:
    real_transport = UrlLibSiliconFlowTransport()

    class Wrapper:
        def tokenshare_transport_for_provider(self, provider_family: str):
            assert provider_family == "siliconflow"
            return real_transport

    factorization_paper_adapter_module._validate_real_transport_mode(
        real_transport=True,
        transport=Wrapper(),
        ai_api_config=_real_transport_config(),
    )


def test_factorization_real_transport_guard_accepts_scheduled_exp5_wrapper() -> None:
    real_transport = UrlLibSiliconFlowTransport()

    class ScheduledExp5Wrapper:
        def tokenshare_real_transport_for_provider(self, provider_family: str):
            assert provider_family == "siliconflow"
            return real_transport

        def tokenshare_transport_for_provider(self, provider_family: str):
            assert provider_family == "siliconflow"
            return self

    wrapper = ScheduledExp5Wrapper()
    factorization_paper_adapter_module._validate_real_transport_mode(
        real_transport=True,
        transport=wrapper,
        ai_api_config=_real_transport_config(),
    )
    assert wrapper.tokenshare_transport_for_provider("siliconflow") is wrapper


def test_factorization_paper_adapter_accepts_openai_real_transport_through_executor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    monkeypatch.setenv("TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY", "test-key")
    transport = UrlLibOpenAITransport()
    calls = []

    def fake_openai_call(**kwargs):
        body = json.loads(kwargs["body_bytes"].decode("utf-8"))
        calls.append(
            {
                "model": body["model"],
                "api_key_seen": bool(kwargs["api_key"]),
                "body_bytes": kwargs["body_bytes"],
                "normalized_absolute_endpoint": kwargs[
                    "normalized_absolute_endpoint"
                ],
                "timeout_seconds": kwargs["timeout_seconds"],
            }
        )
        return _TransportResponse(
            status_code=200,
            body={
                "id": "fake-openai-factorization",
                    "model": body["model"],
                "choices": [
                    {
                        "message": {"content": "not-json"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )

    monkeypatch.setattr(transport, "post_chat_completion", fake_openai_call)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=True,
        ai_api_config=_openai_real_transport_config(),
        entry_id="openai_real_transport_guard",
    )

    assert calls
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert all(attempt.provider == "openai" for attempt in result.attempt_results)
    assert all(attempt.model == "gpt-5.6-sol" for attempt in result.attempt_results)
    assert all(
        attempt.entry_id == "openai_real_transport_guard"
        for attempt in result.attempt_results
    )
    assert result.run_evidence["transport_evidence"]["transport_kind"] == "ai_api"
    secret_scan = result.run_evidence["secret_scan_report"]
    assert secret_scan["status"] == "passed"
    assert secret_scan["secret_checked_count"] == 1
    assert secret_scan["leak_count"] == 0
    assert "secret_scan_failed" not in result.eligibility_report.ineligibility_reasons
    for attempt in result.attempt_results:
        request = _read_request_artifact(result.output_root, attempt.request_ref)
        assert request["capability_snapshot"]["provider_family"] == "openai"
        assert request["hard_requirements"]["provider_family"] == "openai"
        assert _registry_provider_matches(request) == ["openai"]


def test_factorization_real_transport_uses_attempt_clock_domain_and_explicit_critical_path_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    monkeypatch.setenv("TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY", "test-key")
    transport = UrlLibOpenAITransport()
    scripted = ScriptedFactorizationRangeTransport()
    monkeypatch.setattr(
        transport,
        "post_chat_completion",
        scripted.post_chat_completion,
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=True,
        ai_api_config=_openai_real_transport_config(),
        entry_id="openai_real_transport_guard",
    )

    attempts = [attempt.to_dict() for attempt in result.attempt_results]
    assert all(
        _read_request_artifact(result.output_root, attempt.request_ref)[
            "environment_ref"
        ]["clock_policy"]
        == "utc_wall_clock"
        for attempt in result.attempt_results
    )
    scheduled = run_scheduled_cases(
        ordered_case_ids=(case["case_id"],),
        worker_count=condition.worker_count,
        execute_case=lambda _case_id, _worker_count: SimpleNamespace(
            adapter_result=result
        ),
    )
    critical_path = scheduled.metrics
    observed_times = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        for value in (
            *(event["occurred_at"] for event in result.event_records),
            *(attempt["started_at"] for attempt in attempts),
            *(attempt["ended_at"] for attempt in attempts),
        )
    ]

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert (max(observed_times) - min(observed_times)).total_seconds() < 60
    assert len({event["occurred_at"] for event in result.event_records}) > 5
    assert all(
        event["occurred_at"] != factorization_paper_adapter_module.NOW
        for event in result.event_records
    )
    assert abs(
        (datetime.now(timezone.utc) - max(observed_times)).total_seconds()
    ) < 60
    assert critical_path["critical_path_ms"] is None
    assert critical_path["critical_path_evidence_status"] == "unavailable"
    assert (
        critical_path["critical_path_unavailable_reason"]
        == "missing_protocol_dependency_evidence"
    )
    event_seqs = [event["event_seq"] for event in result.event_records]
    # event_seq 只证明 ledger commit order；并发 occurred_at 不要求随其全局单调。
    assert event_seqs == sorted(event_seqs)
    assert len(event_seqs) == len(set(event_seqs))


def test_factorization_scripted_transport_keeps_fixed_protocol_clock(
    tmp_path: Path,
) -> None:
    case = generate_factorization_paper_cases()[0]

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert {
        event["occurred_at"] for event in result.event_records
    } == {factorization_paper_adapter_module.NOW}


def test_factorization_paper_adapter_rejects_openai_url_transport_without_real_flag(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibOpenAITransport"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=UrlLibOpenAITransport(),
            real_transport=False,
        )


def test_factorization_paper_adapter_rejects_same_entry_id_from_wrong_provider_before_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        provider_family="siliconflow",
        model="Qwen/Qwen3.6-27B",
        reasoning_effort=None,
    )
    transport = ScriptedFactorizationRangeTransport()
    output_root = tmp_path / "wrong-provider"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_factorization_paper_case(
                case=case,
                condition=condition,
                output_root=output_root,
                transport=transport,
                real_transport=False,
                ai_api_config=wrong_config,
                entry_id="gpt-entry",
            )
        finally:
            assert transport.calls == []
            assert not output_root.exists()


def test_factorization_fixed_entry_503_does_not_failover_to_sibling_model(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    source_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        sibling_model="gpt-5.6-sol-sibling",
        max_provider_attempts=2,
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=source_config,
    )
    transport = _RecordingProviderErrorTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "selected-entry-503",
        transport=transport,
        real_transport=False,
        ai_api_config=source_config,
        entry_id="gpt-entry",
    )

    expected_ai_units = case["split_params"]["requested_child_count"]
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.provider_attempt_count == expected_ai_units
    assert len(transport.calls) == expected_ai_units
    assert {
        json.loads(call["body_bytes"].decode("utf-8"))["model"]
        for call in transport.calls
    } == {"gpt-5.6-sol"}
    assert len({call["normalized_absolute_endpoint"] for call in transport.calls}) == 1
    for attempt in result.attempt_results:
        assert attempt.provenance_ref is not None
        provenance = json.loads(
            (Path(result.output_root) / attempt.provenance_ref["uri"]).read_text(
                encoding="utf-8"
            )
        )
        assert provenance["selection_record"]["eligible_entry_ids"] == ["gpt-entry"]
        assert provenance["selection_record"]["attempt_entry_ids"] == ["gpt-entry"]


def test_exp5_factorization_online_callback_provider_failure_has_no_protocol_retry(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        max_provider_attempts=1,
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = _RecordingProviderErrorTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "exp5-callback-provider-failure",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
        post_raw_output_hook=lambda **_context: None,
    )

    expected_ai_units = case["split_params"]["requested_child_count"]
    store = ArtifactStore(Path(result.output_root))
    ai_requests = [
        json.loads(
            store.read_bytes(
                ArtifactRef.from_dict(event["payload"]["request_ref"])
            ).decode("utf-8")
        )
        for event in result.event_records
        if event["event_type"] == "EXECUTION_REQUEST_RECORDED"
        and event["payload"].get("request_ref") is not None
    ]
    ai_requests = [
        request
        for request in ai_requests
        if request.get("soft_hints", {}).get("planned_ai_unit_id") is not None
    ]
    assert len(transport.calls) == expected_ai_units
    assert len(ai_requests) == expected_ai_units
    assert {request["attempt_ordinal"] for request in ai_requests} == {0}


def test_factorization_resolved_model_mismatch_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = _ResolvedModelMismatchFactorizationTransport(
        resolved_model="gpt-5.6-sol-versioned",
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert len(transport.calls) == result.split_summary["range_child_count"]
    assert result.task_result.attempt_count == len(transport.calls)
    assert result.task_result.provider_attempt_count == len(transport.calls)
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.task_result.paper_eligible is False
    assert result.merge_summary["status"] == "blocked"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.paper_eligible is False
    assert attempt.model_execution_record_ref is not None
    assert all(
        item.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        and item.paper_eligible is False
        for item in result.attempt_results
    )

    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    provenance = _read_artifact(result.output_root, attempt.provenance_ref)
    assert record["schema_version"] == "tokenshare.paper_model_execution_record.v2"
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["paper_eligible"] is False
    assert "resolved_model_mismatch" in record["mismatch_reasons"]
    assert record["expected_identity"]["model_endpoint_identity_digest"] == (
        condition.model_endpoint_identity_digest
    )
    assert record["actual_request_identities"][0]["configured_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["requested_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert record["resolved_model"] == "gpt-5.6-sol-versioned"
    assert record["source_provider_config_digest"] == approved_config.config_digest
    assert record["prepared_execution_config_digest"] == provenance["config_digest"]
    assert record["request_ref"] == attempt.request_ref
    assert record["provenance_ref"] == attempt.provenance_ref
    assert record["raw_output_ref"] == attempt.raw_output_ref
    assert record["usage_ref"] == attempt.usage_ref


def test_factorization_fixed_entry_matching_response_writes_matched_v2_records(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "matching-response",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(transport.calls) == len(result.attempt_results)
    assert len(transport.calls) > 1
    for attempt in result.attempt_results:
        assert attempt.model_execution_record_ref is not None
        assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
        record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
        assert record["identity_status"] == "matched"
        assert record["requested_model"] == "gpt-5.6-sol"
        assert record["resolved_model"] == "gpt-5.6-sol"
        assert record["response_model_status"] == "present"
        assert record["mismatch_reasons"] == []


def test_factorization_missing_resolved_model_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelFactorizationTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert len(transport.calls) == result.split_summary["range_child_count"]
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.merge_summary["status"] == "blocked"
    assert result.range_results[0]["range_result"] is None
    assert result.range_results[0]["verification"]["status"] == "model_identity_mismatch"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.raw_output_ref is not None
    assert attempt.provenance_ref is not None
    assert attempt.usage_ref is not None
    assert attempt.model_execution_record_ref is not None
    assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
    assert all(
        item.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        and item.paper_eligible is False
        for item in result.attempt_results
    )
    raw = _read_artifact(result.output_root, attempt.raw_output_ref)
    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    assert raw["resolved_model"] is None
    assert raw["response_model_status"] == "missing"
    assert "model" not in raw["raw_response_json"]
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["resolved_model"] is None
    assert record["response_model_status"] == "missing"
    assert record["mismatch_reasons"] == ["missing_resolved_model"]
    assert record["paper_eligible"] is False


class _CustomRealTransportSubclass(UrlLibSiliconFlowTransport):
    def post_chat_completion(self, **kwargs):
        model = json.loads(kwargs["body_bytes"].decode("utf-8"))["model"]
        return _TransportResponse(
            status_code=200,
            body={
                "id": "fake-real-subclass",
                "model": model,
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )


class _TransportResponse:
    def __init__(self, *, status_code: int, body) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


class _InvalidJsonTransport:
    def post_chat_completion(self, **kwargs):
        model = json.loads(kwargs["body_bytes"].decode("utf-8"))["model"]
        return _TransportResponse(
            status_code=200,
            body={
                "id": "invalid-json",
                "model": model,
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )


class _ProviderErrorTransport:
    def post_chat_completion(self, **_kwargs):
        return _TransportResponse(
            status_code=503,
            body={"message": "provider overloaded"},
        )


class _WitnessWithOneFailedSiblingTransport(
    ScriptedFactorizationRangeTransport
):
    """保留真实 scripted witness，只让第一个 no-factor sibling 终态失败。"""

    def __init__(self, *, mode: str) -> None:
        if mode not in {"provider_error", "verifier_rejected"}:
            raise ValueError("unsupported sibling failure mode")
        super().__init__()
        self.mode = mode
        self.witness_count = 0
        self.failed_sibling_count = 0

    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        result = self.calls[-1]["range_result"]
        if result["result_kind"] == "found_factor":
            self.witness_count += 1
            return response
        if self.failed_sibling_count:
            return response
        self.failed_sibling_count += 1
        if self.mode == "provider_error":
            return _TransportResponse(
                status_code=503,
                body={"message": "scripted sibling provider error"},
            )

        rejected = {
            **result,
            "child_index": int(result["child_index"]) + 1000,
        }
        self.calls[-1]["range_result"] = rejected
        return _TransportResponse(
            status_code=200,
            body={
                "id": "factorization-paper-scripted-rejected-sibling",
                "model": json.loads(kwargs["body_bytes"].decode("utf-8"))["model"],
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                rejected,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 70,
                    "completion_tokens": 30,
                    "total_tokens": 100,
                },
            },
        )


class _RecordingProviderErrorTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post_chat_completion(self, **kwargs):
        self.calls.append(
            {
                "api_key_seen": bool(kwargs["api_key"]),
                "body_bytes": kwargs["body_bytes"],
                "normalized_absolute_endpoint": kwargs["normalized_absolute_endpoint"],
                "timeout_seconds": kwargs["timeout_seconds"],
            }
        )
        return _TransportResponse(
            status_code=503,
            body={"message": "provider overloaded"},
        )


class _ForgedWitnessIdentityTransport(
    ScriptedFactorizationRangeTransport
):
    def __init__(self) -> None:
        super().__init__()
        self.forged_witness_count = 0

    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        result = self.calls[-1]["range_result"]
        if result["result_kind"] != "found_factor":
            return response
        self.forged_witness_count += 1
        forged = {
            **result,
            "child_index": int(result["child_index"]) + 1000,
        }
        self.calls[-1]["range_result"] = forged
        return _TransportResponse(
            status_code=200,
            body={
                "id": "factorization-paper-scripted-forged-witness",
                "model": json.loads(kwargs["body_bytes"].decode("utf-8"))["model"],
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                forged,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 70,
                    "completion_tokens": 30,
                    "total_tokens": 100,
                },
            },
        )


class _ResolvedModelMismatchFactorizationTransport(
    ScriptedFactorizationRangeTransport
):
    def __init__(self, *, resolved_model: str) -> None:
        super().__init__()
        self.resolved_model = resolved_model

    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        response.body["model"] = self.resolved_model
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response


class _MissingResolvedModelFactorizationTransport(
    ScriptedFactorizationRangeTransport
):
    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        response.body.pop("model", None)
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response


def _v2_condition(case: dict) -> PaperExperimentCondition:
    difficulty = str(case["difficulty"])
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"exp1_factorization_{difficulty}_v2_r0",
        domain="factorization",
        difficulty=difficulty,
        paper_difficulty=difficulty,
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "0" * 64,
    )


def test_factorization_worker_death_runs_through_process_lease_recovery(
    tmp_path: Path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        **{
            **_v2_condition(case).__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": "exp3_factorization_worker_death_progress_25_k1_r0",
            "fault_type": "worker_death",
        }
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
            worker_termination_policy=WorkerTerminationPolicy(
                target_planned_ai_unit_ids=("range_0",),
                kill_point="progress_25",
                total_planned_ai_unit_count=2,
            ),
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert any(
        attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
        for attempt in result.attempt_results
    )
    worker_deaths = [
        record
        for record in result.fault_records
        if record["fault_type"] == "worker_death"
    ]
    assert len(worker_deaths) == 1
    record = worker_deaths[0]
    assert record["target_ai_unit"]["metadata"]["planned_ai_unit_id"] == "range_0"
    assert record["worker_process_exitcode"] != 0
    assert record["replacement_process_exitcode"] == 0
    assert record["lease_expiry"]["trigger"] == "lease_expired"
    assert record["coordinator"]["survived"] is True


def test_factorization_worker_death_process_accepts_frozen_endpoint_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = generate_factorization_paper_cases()[0]
    profile = load_exp1_pilot_profile(
        "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
    )
    endpoint = profile.model_endpoint_identity
    monkeypatch.setenv(
        profile.source_provider_config.entries[0].api_key_env,
        "offline-process-identity-key",
    )
    condition = PaperExperimentCondition(
        **{
            **_v2_condition(case).__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": "exp3_worker_death_frozen_identity_r0",
            "fault_type": "worker_death",
            "model_policy": "fixed_entry",
            "model_cohort_id": endpoint.model_cohort_id,
            "cohort_member_id": endpoint.cohort_member_id,
            "provider_config_id": endpoint.provider_config_id,
            "model_entry_id": endpoint.selected_entry_id,
            "provider_family": endpoint.provider_family,
            "provider_model_id": endpoint.provider_model_id,
            "reasoning_profile_id": endpoint.reasoning_profile_id,
            "model_cohort_digest": endpoint.model_cohort_digest,
            "source_provider_config_digest": (
                endpoint.source_provider_config_digest
            ),
            "model_endpoint_identity_digest": (
                endpoint.model_endpoint_identity_digest
            ),
        }
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=profile.source_provider_config,
        entry_id=endpoint.selected_entry_id,
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("range_0",),
            kill_point="progress_25",
            total_planned_ai_unit_count=2,
        ),
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert any(
        attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
        for attempt in result.attempt_results
    )


def test_factorization_worker_death_retry_limit_returns_failed_projection_without_requiring_replacement(
    tmp_path: Path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        **{
            **_v2_condition(case).__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": "exp3_factorization_worker_death_retry_limit_r0",
            "fault_type": "worker_death",
        }
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_AlwaysServerErrorFactorizationTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("range_0",),
            kill_point="progress_25",
            total_planned_ai_unit_count=2,
        ),
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert any(
        attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
        for attempt in result.attempt_results
    )
    assert not any(
        attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
        for attempt in result.attempt_results
    )
    terminal_recoveries = [
        event
        for event in result.event_records
        if event["event_type"] == "RECOVERY_ACTION_RECORDED"
        and event["payload"]["recovery_action"]["retry_allowed"] is False
    ]
    assert terminal_recoveries
    assert all(
        event["payload"]["recovery_action"]["reason"] == "retry_limit_reached"
        for event in terminal_recoveries
    )
    assert len(result.fault_records) == 1
    incomplete = result.fault_records[0]
    assert incomplete["schema_version"] == (
        "tokenshare.paper_worker_death_incomplete.v1"
    )
    assert incomplete["recovery_completed"] is False
    assert incomplete["replacement_fact"] is None
    assert incomplete["failure_reason"] == "retry_limit_reached"
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"
    persisted_root = Path(result.output_root)
    assert (persisted_root / "run_manifest.json").is_file()
    assert (persisted_root / "per_task_results.jsonl").is_file()
    assert (persisted_root / "per_attempt_results.jsonl").is_file()


def _failed_protocol_projection_fixture(
    tmp_path: Path,
    *,
    experiment_id: str,
    executor_id: str = "executor_factorization_runtime",
    executor_type: str = "deterministic_local",
):
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        **{
            **_v2_condition(case).__dict__,
            "experiment_id": experiment_id,
            "condition_id": f"{experiment_id}_failed_runtime_r0",
            "provider_family": "deepseek",
            "provider_model_id": "deepseek-v4-pro",
            "model_entry_id": "deepseek_v4_pro_exp1_baseline",
        }
    )
    store = ArtifactStore(tmp_path)
    request_ref = store.save_json(
        {
            "attempt_id": "attempt-root-1",
            "unit_id": "unit-root-1",
            "executor": {
                "executor_id": executor_id,
                "executor_type": executor_type,
                "executor_version": "0.1.0",
            },
            "hard_requirements": {"executor": executor_type},
            "created_at": "2026-07-28T00:00:00Z",
        },
        artifact_id="request-root-1.json",
        artifact_type="ExecutionRequest",
        artifact_schema_id="ExecutionRequest",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-07-28T00:00:00Z",
    )
    events = (
        {
            "event_type": "EXECUTION_REQUEST_RECORDED",
            "payload": {
                "attempt_id": "attempt-root-1",
                "unit_id": "unit-root-1",
                "request_ref": request_ref.to_dict(),
            },
        },
        {
            "event_type": "ATTEMPT_STATE_CHANGED",
            "payload": {
                "attempt": {
                    "attempt_id": "attempt-root-1",
                    "state": "Failed",
                    "client_id": "worker-root-1",
                    "started_at": "2026-07-28T00:00:00Z",
                    "finished_at": "2026-07-28T00:00:01Z",
                    "failure_reason": "retry_limit_reached",
                }
            },
        },
    )
    runtime_result = SimpleNamespace(
        run_id="run-root-1",
        task_id="task-root-1",
        summary={
            "provider_attempt_count": 0,
            "runtime_observation": {
                "worker_execution_facts": [
                    {
                        "attempt_id": "attempt-root-1",
                        "worker_id": "worker-root-1",
                        "started_at": "2026-07-28T00:00:00Z",
                        "ended_at": "2026-07-28T00:00:01Z",
                        "result_kind": "executor_error",
                    }
                ]
            }
        },
    )

    return condition, runtime_result, events, store, request_ref


def test_failed_protocol_attempt_projection_preserves_runtime_executor_failure_without_provider_identity(
    tmp_path: Path,
) -> None:
    condition, runtime_result, events, store, request_ref = (
        _failed_protocol_projection_fixture(
            tmp_path,
            experiment_id="exp3_real_ai_fault_recovery",
        )
    )

    attempts = _failed_protocol_attempts_from_events(
        condition=condition,
        runtime_result=runtime_result,
        runtime_events=events,
        store=store,
    )

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.attempt_status == "executor_error"
    assert attempt.provider_attempt_index == 0
    assert attempt.provider_attempt_count == 0
    assert attempt.total_tokens == 0
    assert attempt.cost_estimate == 0.0
    assert attempt.raw_output_ref is None
    assert attempt.usage_ref is None
    assert attempt.provenance_ref is None
    assert attempt.request_ref == request_ref.to_dict()
    assert attempt.error_kind == "retry_limit_reached"
    assert attempt.provider is None
    assert attempt.model is None
    assert attempt.entry_id is None
    body = attempt.to_dict()
    assert body["schema_version"] == "tokenshare.paper_attempt_result.v2"
    assert body["executor_id"] == "executor_factorization_runtime"
    assert body["executor_type"] == "deterministic_local"
    failure_stage, failure_kind = _task_failure_from_child_evidence(
        attempts=attempts,
        range_records=[],
    )
    assert failure_stage == PaperFailureStage.REQUEST
    assert failure_kind == PaperFailureKind.INTERNAL_ERROR


def test_failed_protocol_projection_entry_rejects_exp1_before_v2_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    transport = ScriptedFactorizationRangeTransport()
    failing_executor = _FailBeforeProviderExecutor()
    projection_calls: list[str] = []

    def dispatch_failed_root(*, coordinator, request):
        return coordinator.run_root(
            replace(
                request,
                worker_backend=SequentialWorkerBackend(
                    executor=failing_executor,
                    submitted_at=lambda: "2026-07-28T00:00:00Z",
                ),
            )
        )

    def forbidden_projection(**kwargs):
        del kwargs
        projection_calls.append("called")
        raise AssertionError("v2 projection helper must not run for Exp1")

    monkeypatch.setattr(
        factorization_paper_adapter_module,
        "_failed_protocol_attempts_from_events",
        forbidden_projection,
    )

    with pytest.raises(ValueError, match="Exp3 deterministic runtime"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=transport,
            real_transport=False,
            entry_id="factorization_paper_scripted",
            protocol_run_dispatcher=dispatch_failed_root,
        )

    assert failing_executor.calls == 1
    assert transport.calls == []
    assert projection_calls == []
    assert not list(tmp_path.rglob("CURRENT.json"))
    assert not list(tmp_path.rglob("per_attempt_results.jsonl"))
    assert not any(
        "tokenshare.paper_attempt_result.v2" in path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.json")
    )


@pytest.mark.parametrize(
    ("experiment_id", "executor_id", "executor_type"),
    (
        (
            "exp1_real_ai_feasibility",
            "executor_factorization_runtime",
            "deterministic_local",
        ),
        (
            "exp2_real_ai_worker_scalability",
            "executor_factorization_runtime",
            "deterministic_local",
        ),
        (
            "exp4_real_ai_protocol_ablation",
            "executor_factorization_runtime",
            "deterministic_local",
        ),
        (
            "exp3_real_ai_fault_recovery",
            "executor_ai_api",
            "ai_api",
        ),
    ),
)
def test_failed_protocol_attempt_projection_rejects_non_exp3_or_non_runtime_executor(
    tmp_path: Path,
    experiment_id: str,
    executor_id: str,
    executor_type: str,
) -> None:
    condition, runtime_result, events, store, _ = (
        _failed_protocol_projection_fixture(
            tmp_path,
            experiment_id=experiment_id,
            executor_id=executor_id,
            executor_type=executor_type,
        )
    )

    with pytest.raises(ValueError, match="Exp3 deterministic runtime"):
        _failed_protocol_attempts_from_events(
            condition=condition,
            runtime_result=runtime_result,
            runtime_events=events,
            store=store,
        )

    assert runtime_result.summary["provider_attempt_count"] == 0
    assert not any(
        "tokenshare.paper_attempt_result.v2" in path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.json")
    )


def test_factorization_three_worker_deaths_reuse_replacements_for_two_unit_root(
    tmp_path: Path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    condition = PaperExperimentCondition(
        **{
            **_v2_condition(case).__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": "exp3_factorization_worker_death_progress_25_k3_r0",
            "fault_type": "worker_death",
        }
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
            worker_termination_policy=WorkerTerminationPolicy(
                target_planned_ai_unit_ids=("range_0", "range_1"),
                termination_count_target=3,
                kill_point="progress_25",
                total_planned_ai_unit_count=2,
            ),
    )

    dead_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
    ]
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(dead_attempts) == 3
    assert len(result.fault_records) == 3
    assert len(
        {
            record["worker_pid"]
            for record in result.fault_records
        }
    ) == 3


@pytest.mark.parametrize(
    "fault_type",
    (
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
    ),
)
def test_factorization_rate_faults_use_protocol_recovery_lifecycle(
    tmp_path: Path,
    fault_type: str,
) -> None:
    case = generate_factorization_paper_cases()[0]
    base_condition = _v2_condition(case)
    condition = PaperExperimentCondition(
        **{
            **base_condition.__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": f"exp3_factorization_{fault_type}_r0",
            "fault_type": fault_type,
            "fault_rate": 1.0,
        }
    )
    runtime_hook: PaperFaultRuntimeHooks | None = None

    def post_raw_output_hook(**context):
        nonlocal runtime_hook
        request = context["request"]
        if runtime_hook is None:
            runtime_hook = PaperFaultRuntimeHooks(
                artifact_store=context["artifact_store"],
                condition_id=condition.condition_id,
                repeat_id=condition.repeat_id,
                fault_type=fault_type,
                seed=condition.seed,
                selected_unit_ids=("range_0",),
            )
        directive = runtime_hook.after_raw_output_persisted(
            RawOutputContext(
                run_id=f"{condition.condition_id}_{case['case_id']}",
                task_id=request.task_id,
                unit_id=request.unit_id,
                experiment_unit_id=request.soft_hints.get(
                    "planned_ai_unit_id"
                ),
                attempt_id=request.attempt_id,
                worker_id=str(
                    request.allocation_decision.get(
                        "client_id", "factor-worker"
                    )
                ),
                raw_output_ref=context["raw_output_ref"],
                provenance_ref=context["provenance_ref"],
                usage_ref=context["usage_ref"],
                content_text=str(context["content_text"]),
                provider=str(context["provider_family"]),
                model=str(context["model"]),
                entry_id=str(context["entry_id"]),
                usage_summary=dict(context["usage_summary"]),
                submitted_at=str(context["submitted_at"]),
                lease_deadline_at=request.soft_hints.get(
                    "lease_deadline_at"
                ),
            )
        )
        if directive is None:
            return None
        return {
            key: value
            for key, value in {
                "content_text": directive.content_text,
                "result_kind": directive.result_kind,
            }.items()
            if value is not None
        }

    class RuntimeHookBridge(NoOpRuntimeHooks):
        def __call__(self, **context):
            return post_raw_output_hook(**context)

        def after_parsed_candidate_persisted(self, context):
            # factorization root 的确定性拆分会先产生一次非 AI 候选；
            # fault hook 只处理随后由 raw-output 回调注册的实验 AI 单元。
            if runtime_hook is None:
                return None
            return runtime_hook.after_parsed_candidate_persisted(context)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
        post_raw_output_hook=RuntimeHookBridge(),
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert runtime_hook is not None
    assert len(runtime_hook.records) == 1
    record = runtime_hook.records[0]
    affected_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.unit_id == record["unit_id"]
    ]
    assert len(affected_attempts) == 2
    assert any(
        attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
        for attempt in affected_attempts
    )
    assert ArtifactStore(result.output_root).verify(
        ArtifactRef.from_dict(record["original_raw_output_ref"])
    )
    assert any(
        event["event_type"] == "RECOVERY_ACTION_RECORDED"
        for event in result.event_records
    )


def test_factorization_trace_false_positive_runs_raw_and_parsed_hooks_before_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments import paper_formal_runner

    case = generate_factorization_paper_cases()[0]
    base_condition = _v2_condition(case)
    source_transport = ScriptedFactorizationRangeTransport()
    source = run_factorization_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / "source",
        transport=source_transport,
        real_transport=False,
    )
    target_ai_unit_id = next(
        str(attempt.planned_ai_unit_id)
        for attempt in source.attempt_results
        if attempt.planned_ai_unit_id is not None
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
        replacement_count=2,
    )
    condition = replace(
        base_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_factorization_trace_false_positive_r0",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    runtime_records: list[dict] = []
    fault_hook = paper_formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id=str(case["case_id"]),
        fault_type="false_positive",
        selected_unit_ids=(f'{case["case_id"]}:{target_ai_unit_id}',),
        reserve_unit_ids=(),
        runtime_records=runtime_records,
    )
    verifier_observations: list[dict] = []
    original_verify = FactorizationRuntimeAdapter.verify_submission

    def verify_after_fault(self, submission, *, unit):
        if unit.unit_type == "factor_search_range":
            candidate_ref = submission.candidate_output_refs["range_result"]
            candidate = json.loads(
                self._require_store().read_bytes(candidate_ref).decode("utf-8")
            )
            verifier_observations.append(
                {
                    "attempt_id": submission.attempt_id,
                    "fault_record_count": len(runtime_records),
                    "fault_type": (
                        candidate.get("paper_fault_mutation", {}).get("fault_type")
                    ),
                }
            )
        return original_verify(self, submission, unit=unit)

    monkeypatch.setattr(
        FactorizationRuntimeAdapter,
        "verify_submission",
        verify_after_fault,
    )
    trace_transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "trace",
        transport=trace_transport,
        real_transport=False,
        post_raw_output_hook=fault_hook,
        trace_context=trace_context,
    )

    assert trace_transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert all(
        attempt.provider_attempt_count == 0 for attempt in result.attempt_results
    )
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    assert len(runtime_records) == 1
    assert runtime_records[0]["hook_stage"] == (
        "after_parsed_candidate_before_submission_and_verification"
    )
    assert runtime_records[0]["original_provenance_ref"]
    assert runtime_records[0]["pre_fault_usage_ref"]
    assert any(
        observation["fault_record_count"] == 1
        and observation["fault_type"] == "false_positive"
        for observation in verifier_observations
    )
    target_statuses = {
        attempt.attempt_status
        for attempt in result.attempt_results
        if attempt.planned_ai_unit_id == target_ai_unit_id
    }
    assert PaperAttemptStatus.VERIFICATION_REJECTED in target_statuses
    assert PaperAttemptStatus.SUCCEEDED in target_statuses


def test_factorization_trace_worker_death_p75_uses_frozen_second_unit_and_recovers(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )

    def p75_target_is_not_pruned(case: dict) -> bool:
        requested = int(case["split_params"]["requested_child_count"])
        partition = partition_candidate_ranges(
            target_n=case["target_n"],
            requested_child_count=requested,
            max_children_per_unit=requested,
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        anchor = (len(partition.ranges) * 75 + 99) // 100 - 1
        factor = min(int(item["prime"]) for item in case["oracle_prime_factors"])
        factor_index = next(
            item.child_index
            for item in partition.ranges
            if int(item.range_start) <= factor <= int(item.range_end)
        )
        return factor_index >= anchor

    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy" and p75_target_is_not_pruned(case)
    )
    base_condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    planned = tuple(
        dict.fromkeys(
            str(attempt.planned_ai_unit_id)
            for attempt in source.attempt_results
            if attempt.planned_ai_unit_id is not None
        )
    )
    assert planned == ("range_0", "range_1")
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
        replacement_count=2,
    )
    condition = replace(
        base_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_factorization_trace_worker_death_p75_r0",
        fault_type="worker_death",
    )
    trace_transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "trace",
        transport=trace_transport,
        real_transport=False,
        trace_context=trace_context,
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("range_1",),
            termination_count_target=1,
            kill_point="progress_75",
            total_planned_ai_unit_count=2,
        ),
    )

    assert trace_transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    dead_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status is PaperAttemptStatus.WORKER_DIED
    ]
    assert len(dead_attempts) == 1
    assert dead_attempts[0].planned_ai_unit_id == "range_1"
    assert len(result.fault_records) == 1
    assert result.fault_records[0]["kill_progress_target_ratio"] == pytest.approx(
        0.75
    )
    assert result.fault_records[0]["kill_progress_actual_ratio"] >= 0.75


def test_factorization_trace_worker_death_requeues_frozen_target_before_later_success_merge(
    tmp_path: Path,
) -> None:
    """正式 p50 target 不能被稍晚完成的正确 sibling 提前剪枝。"""

    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )

    def formal_hard_p50_later_success(case: dict) -> bool:
        requested = int(case["split_params"]["requested_child_count"])
        partition = partition_candidate_ranges(
            target_n=case["target_n"],
            requested_child_count=requested,
            max_children_per_unit=requested,
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        if len(partition.ranges) != requested:
            return False
        anchor = (len(partition.ranges) * 50 + 99) // 100 - 1
        factor = min(int(item["prime"]) for item in case["oracle_prime_factors"])
        success_index = next(
            item.child_index
            for item in partition.ranges
            if int(item.range_start) <= factor <= int(item.range_end)
        )
        return success_index > anchor

    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "hard" and formal_hard_p50_later_success(case)
    )
    requested = int(case["split_params"]["requested_child_count"])
    anchor = (requested * 50 + 99) // 100 - 1
    target_ai_unit_id = f"range_{anchor}"
    base_condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
        replacement_count=2,
    )
    condition = replace(
        base_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_factorization_trace_worker_death_hard_p50_r0",
        fault_type="worker_death",
    )
    trace_transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "trace",
        transport=trace_transport,
        real_transport=False,
        trace_context=trace_context,
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=(target_ai_unit_id,),
            termination_count_target=1,
            kill_point="progress_50",
            total_planned_ai_unit_count=requested,
        ),
    )

    assert trace_transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    target_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.planned_ai_unit_id == target_ai_unit_id
    ]
    assert [attempt.attempt_status for attempt in target_attempts] == [
        PaperAttemptStatus.WORKER_DIED,
        PaperAttemptStatus.SUCCEEDED,
    ]
    recovery_seq = next(
        int(event["event_seq"])
        for event in result.event_records
        if event["event_type"] == EventType.RECOVERY_ACTION_RECORDED.value
        and event["payload"]["recovery_action"]["unit_id"]
        == target_attempts[0].unit_id
        and event["payload"]["recovery_action"]["retry_allowed"] is True
    )
    replacement_trace_seq = max(
        int(event["event_seq"])
        for event in result.event_records
        if event["event_type"] == EventType.TRACE_DELIVERY_COMMITTED.value
        and event["object_id"] == target_attempts[1].attempt_id
    )
    merge_or_prune_seqs = [
        int(event["event_seq"])
        for event in result.event_records
        if event["event_type"]
        in {
            EventType.MERGE_TASK_LINK_RECORDED.value,
            EventType.MERGE_RECORDED.value,
            EventType.SUBTREE_PRUNED.value,
        }
    ]
    assert recovery_seq < replacement_trace_seq < min(merge_or_prune_seqs)


def _v2_case_with_large_no_factor_range() -> tuple[dict, int, int]:
    for case in generate_factorization_paper_cases():
        partition = _v2_partition(case)
        oracle_primes = {int(item["prime"]) for item in case["oracle_prime_factors"]}
        for range_input in partition.ranges:
            start = int(range_input.range_start)
            end = int(range_input.range_end)
            child_length = end - start + 1
            if child_length > 100_000 and not any(
                start <= prime <= end for prime in oracle_primes
            ):
                return case, range_input.child_index, child_length
    raise AssertionError("generated v2 catalog must contain a >100,000 no-factor child range")


def _v2_case_with_large_factor_range() -> tuple[dict, int, int]:
    for case in generate_factorization_paper_cases():
        partition = _v2_partition(case)
        oracle_primes = {int(item["prime"]) for item in case["oracle_prime_factors"]}
        for range_input in partition.ranges:
            start = int(range_input.range_start)
            end = int(range_input.range_end)
            child_length = end - start + 1
            if child_length > 100_000 and any(
                start <= prime <= end for prime in oracle_primes
            ):
                return case, range_input.child_index, child_length
    raise AssertionError("generated v2 catalog must contain a >100,000 factor child range")


def _v2_partition(case: dict):
    requested_children = int(case["split_params"]["requested_child_count"])
    return partition_candidate_ranges(
        target_n=case["target_n"],
        requested_child_count=requested_children,
        max_children_per_unit=requested_children,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )


def _condition(catalog_digest: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_factorization_easy_w10_r0",
        domain="factorization",
        difficulty="easy",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _formal_exp5_condition(
    *,
    catalog_digest: str,
    domain: str,
    difficulty: str,
    approved_config,
) -> PaperExperimentCondition:
    identity = build_model_endpoint_identity(
        model_cohort_id="experiment_5_fixed_endpoint_cohort_v1",
        model_cohort_digest=f"sha256:{'1' * 64}",
        cohort_member_id="gpt_5_6_sol_high_openai",
        provider_config_id="openai",
        selected_entry_id="gpt-entry",
        expected_provider_family="openai",
        expected_provider_model_id="gpt-5.6-sol",
        expected_reasoning_profile_id="high",
        source_config=approved_config,
    )
    return PaperExperimentCondition(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id=f"exp5_{domain}_{difficulty}_gpt_r0",
        domain=domain,
        difficulty=difficulty,
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_cohort_id=identity.model_cohort_id,
        cohort_member_id=identity.cohort_member_id,
        provider_config_id=identity.provider_config_id,
        model_entry_id=identity.selected_entry_id,
        provider_family=identity.provider_family,
        provider_model_id=identity.provider_model_id,
        reasoning_profile_id=identity.reasoning_profile_id,
        model_cohort_digest=identity.model_cohort_digest,
        source_provider_config_digest=identity.source_provider_config_digest,
        model_endpoint_identity_digest=identity.model_endpoint_identity_digest,
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _identity_config(
    *,
    provider_family: str,
    model: str,
    reasoning_effort: str | None,
    sibling_model: str | None = None,
    max_provider_attempts: int = 1,
):
    request_overrides = {"temperature": 0.0}
    if reasoning_effort is not None:
        request_overrides["reasoning_effort"] = reasoning_effort
    base_url = (
        "https://api.openai.com/v1"
        if provider_family == "openai"
        else "https://api.siliconflow.cn/v1"
    )
    entries = [
        {
            "entry_id": "gpt-entry",
            "enabled": True,
            "base_url": base_url,
            "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
            "model": model,
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": request_overrides,
            "pricing": {
                "currency": "USD",
                "input_per_million_tokens": 1.0,
                "output_per_million_tokens": 2.0,
            },
            "tags": ["identity_test"],
        }
    ]
    if sibling_model is not None:
        entries.append(
            {
                "entry_id": "sibling-entry",
                "enabled": True,
                "base_url": base_url,
                "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
                "model": sibling_model,
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": request_overrides,
                "pricing": {
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                "tags": ["identity_test", "sibling"],
            }
        )
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": provider_family,
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 512,
                "temperature": 0.0,
                "max_provider_attempts": max_provider_attempts,
            },
            "entries": entries,
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "fixed-entry-identity-test"},
        }
    )


def _real_transport_config():
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": "siliconflow",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 512,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "real_transport_guard",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": "TOKENSHARE_REAL_TRANSPORT_GUARD_KEY",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["factorization_paper", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "real-transport-guard-test"},
        }
    )


def _openai_real_transport_config():
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": "openai",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 512,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "openai_real_transport_guard",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY",
                    "model": "gpt-5.6-sol",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {
                        "temperature": 0.0,
                        "reasoning_effort": "high",
                    },
                    "pricing": {
                        "currency": "USD",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["factorization_paper", "openai", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "openai-real-transport-guard-test"},
        }
    )


def _read_request_artifact(output_root: str, request_ref: dict) -> dict:
    return _read_artifact(output_root, request_ref)


def _read_artifact(output_root: str, artifact_ref: dict) -> dict:
    return json.loads((Path(output_root) / artifact_ref["uri"]).read_text(encoding="utf-8"))


def _request_provider_family(output_root: str, request_ref: dict) -> str:
    request = _read_request_artifact(output_root, request_ref)
    assert request["capability_snapshot"]["provider_family"] == request["hard_requirements"][
        "provider_family"
    ]
    return str(request["hard_requirements"]["provider_family"])


def _registry_provider_matches(request: dict) -> list[str]:
    registry = ExecutorRegistry()
    registry.register(
        build_ai_api_executor_descriptor(
            executor_id="executor_ai_api_siliconflow",
            provider_family="siliconflow",
        )
    )
    registry.register(
        build_ai_api_executor_descriptor(
            executor_id="executor_ai_api_openai",
            provider_family="openai",
        )
    )
    return [
        str(descriptor.capabilities["provider_family"])
        for descriptor in registry.match_available(
            executor_type="ai_api",
            hard_requirements=request["hard_requirements"],
            request_schema_version=request["schema_version"],
        )
    ]
def test_factorization_budget_commitments_use_runtime_plan_unit_snapshots(
    tmp_path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    adapter = FactorizationRuntimeAdapter(provider_family="siliconflow", seed=7)
    planned_units = adapter.plan_units(case, artifact_store=ArtifactStore(tmp_path))

    bindings = build_case_ai_unit_bindings(case, seed=7)

    assert {
        binding["unit_id"]: binding["task_unit_snapshot_commitment"]
        for binding in bindings
    } == {
        unit.unit_id: task_unit_snapshot_commitment(unit.to_dict())
        for unit in planned_units
    }


def test_factorization_full_adapter_is_coordinator_thin_shell_with_event_refs(
    tmp_path,
    monkeypatch,
) -> None:
    case = generate_factorization_paper_cases()[0]
    calls = 0
    original = ProtocolRunCoordinator.run_root

    def recording(self, request):
        nonlocal calls
        calls += 1
        return original(self, request)

    monkeypatch.setattr(ProtocolRunCoordinator, "run_root", recording)

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert calls == 1
    assert result.task_result.event_refs
    assert result.eligibility_report.paper_eligible is False


def test_factorization_full_preserves_ai_parsed_provenance_before_canonicalization(
    tmp_path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    ranges_by_unit = {item["unit_id"]: item for item in result.range_results}
    for attempt in result.attempt_results:
        assert attempt.parsed_output_ref is not None
        assert attempt.parsed_output_ref["artifact_type"] == "ParsedModelOutput"
        canonical_ref = ranges_by_unit[attempt.unit_id]["canonical_output_ref"]
        assert canonical_ref is not None
        assert canonical_ref["artifact_type"] == "canonical_output"
        assert canonical_ref["artifact_id"] != attempt.parsed_output_ref["artifact_id"]
        canonical_body_ref = next(
            ref
            for ref in result.task_result.artifact_refs
            if ref["artifact_id"] == canonical_ref["artifact_id"]
        )
        source = canonical_body_ref["source"]
        assert source["parsed_output_ref"] == attempt.parsed_output_ref
        assert source["raw_output_ref"] == attempt.raw_output_ref
        assert source["candidate_output_ref"]["artifact_type"] == "CandidateOutput"


def test_factorization_full_task_artifacts_include_stable_attempt_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    result = run_factorization_paper_case(
        case=case,
        condition=_formal_exp5_condition(
            catalog_digest=f"sha256:{'3' * 64}",
            domain="factorization",
            difficulty=str(case["difficulty"]),
            approved_config=approved_config,
        ),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    task_keys = [
        (ref["artifact_id"], ref["content_hash"])
        for ref in result.task_result.artifact_refs
    ]
    assert len(task_keys) == len(set(task_keys))
    task_key_set = set(task_keys)
    for attempt in result.attempt_results:
        required_refs = (
            attempt.request_ref,
            attempt.raw_output_ref,
            attempt.parsed_output_ref,
            attempt.provenance_ref,
            attempt.usage_ref,
            attempt.model_execution_record_ref,
        )
        assert all(ref is not None for ref in required_refs)
        assert {
            (ref["artifact_id"], ref["content_hash"])
            for ref in required_refs
            if ref is not None
        }.issubset(task_key_set)


def test_factorization_coordinator_restores_captured_model_record_by_attempt_id(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    original_project = factorization_paper_adapter_module.project_paper_protocol_run

    def project_without_model_records(**kwargs):
        projection = original_project(**kwargs)
        return replace(
            projection,
            attempt_results=tuple(
                replace(item, model_execution_record_ref=None)
                for item in projection.attempt_results
            ),
            task_result=replace(projection.task_result, artifact_refs=[]),
        )

    monkeypatch.setattr(
        factorization_paper_adapter_module,
        "project_paper_protocol_run",
        project_without_model_records,
    )
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    result = run_factorization_paper_case(
        case=case,
        condition=_formal_exp5_condition(
            catalog_digest=f"sha256:{'4' * 64}",
            domain="factorization",
            difficulty=str(case["difficulty"]),
            approved_config=approved_config,
        ),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    task_record_refs = {
        (item["artifact_id"], item["content_hash"])
        for item in result.task_result.artifact_refs
        if item.get("artifact_type") == "PaperModelExecutionRecord"
    }
    assert result.attempt_results
    for attempt in result.attempt_results:
        assert attempt.provider_attempt_count == 1
        assert attempt.model_execution_record_ref is not None
        record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
        assert record["attempt_id"] == attempt.attempt_id
        assert (
            attempt.model_execution_record_ref["artifact_id"],
            attempt.model_execution_record_ref["content_hash"],
        ) in task_record_refs


def test_factorization_fixed_identity_process_result_rejects_duplicate_attempt_id() -> None:
    executor_type = factorization_paper_adapter_module._FixedIdentityRangeExecutor
    executor = executor_type.__new__(executor_type)
    executor._calls_lock = Lock()
    executor.secret_collector = (
        factorization_paper_adapter_module._TransientSecretCollector()
    )
    captured = factorization_paper_adapter_module._CapturedRangeCall(
        request=SimpleNamespace(attempt_id="attempt-a"),
        submission=SimpleNamespace(),
        request_ref=SimpleNamespace(),
        usage_ref=SimpleNamespace(),
        model_execution_record=None,
        model_execution_record_ref=None,
        model_execution_record_required=False,
    )
    executor.calls = [captured, captured]

    with pytest.raises(ValueError, match="exactly one captured request"):
        executor.export_process_result(captured.request, captured.submission)

    executor.calls = [captured]
    with pytest.raises(ValueError, match="duplicated captured request"):
        executor.ingest_process_result(captured)


def test_fixed_identity_is_request_policy_and_mismatch_is_fatal_submission(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=f"sha256:{'2' * 64}",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        approved_config=approved_config,
    )
    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ResolvedModelMismatchFactorizationTransport(
            resolved_model="gpt-5.6-sol-versioned"
        ),
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    attempt = result.attempt_results[0]
    request = _read_artifact(result.output_root, attempt.request_ref)
    assert request["hard_requirements"] == {
        "executor": "ai_api",
        "provider_family": "openai",
        "provider_config_id": "openai",
        "source_provider_config_digest": approved_config.config_digest,
        "prepared_execution_config_digest": _prepare_config(
            approved_config,
            entry_id="gpt-entry",
            max_tokens=512,
            timeout_seconds=30,
        ).config_digest,
        "selected_entry_id": "gpt-entry",
        "provider_model_id": "gpt-5.6-sol",
        "reasoning_profile_id": "high",
        "model_endpoint_identity_digest": condition.model_endpoint_identity_digest,
    }
    serialized_requirements = json.dumps(request["hard_requirements"], sort_keys=True)
    assert "api_key" not in serialized_requirements
    assert "prompt" not in serialized_requirements
    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    events = ledger.read_all()
    submission_event = next(
        event
        for event in events
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["request_id"] == request["request_id"]
    )
    submission = _read_artifact(
        result.output_root,
        submission_event.payload["submission_ref"],
    )
    assert submission["result_kind"] == "fatal_executor_error"
    assert submission["candidate_output_refs"] == {}
    assert not any(
        event.event_type == EventType.VERIFICATION_RECORDED
        and event.payload["unit_id"] == attempt.unit_id
        for event in events
    )
    assert not any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and event.payload["unit_id"] == attempt.unit_id
        for event in events
    )
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"


def test_fixed_identity_request_mismatch_is_fatal_without_provider_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=f"sha256:{'4' * 64}",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        approved_config=approved_config,
    )
    transport = ScriptedFactorizationRangeTransport()
    executor_type = factorization_paper_adapter_module._FixedIdentityRangeExecutor
    original_init = executor_type.__init__

    def initialize_with_drifted_requirement(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.executor_requirements = {
            **self.executor_requirements,
            "provider_model_id": "wrong-model",
        }

    monkeypatch.setattr(
        executor_type,
        "__init__",
        initialize_with_drifted_requirement,
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert transport.calls == []
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.attempt_results
    expected_status_by_error_kind = {
        "executor_requirement_mismatch": PaperAttemptStatus.EXECUTOR_ERROR,
        "missing_submission_event": PaperAttemptStatus.PROVIDER_ERROR,
    }
    assert len(result.attempt_results) == len(expected_status_by_error_kind)
    assert {
        attempt.error_kind: attempt.attempt_status
        for attempt in result.attempt_results
    } == expected_status_by_error_kind
    mismatch_attempt = next(
        attempt
        for attempt in result.attempt_results
        if attempt.error_kind == "executor_requirement_mismatch"
    )
    provenance = _read_artifact(result.output_root, mismatch_attempt.provenance_ref)
    assert provenance["provider_attempt_count"] == 0
    assert provenance["attempts"] == []
    assert provenance["final_result_kind"] == "fatal_executor_error"
    assert all(
        attempt.model_execution_record_ref is None
        and attempt.paper_eligible is False
        for attempt in result.attempt_results
    )
    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    submission_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["unit_id"] in {
            attempt.unit_id for attempt in result.attempt_results
        }
    ]
    assert len(submission_events) == 1
    for event in submission_events:
        submission = _read_artifact(
            result.output_root,
            event.payload["submission_ref"],
        )
        assert submission["result_kind"] == "fatal_executor_error"
        assert submission["candidate_output_refs"] == {}
        assert submission["provenance_ref"] == mismatch_attempt.provenance_ref


def test_trace_success_and_provider_failure_stage_current_wrapper_with_complete_role_locators(
    tmp_path,
) -> None:
    from types import SimpleNamespace

    from tests.executors.test_trace_backed import _bank, _binding, _bound_request
    from tests.local_runtime.test_trace_delivery_parent_commit import (
        _completion,
        _lease,
        _stores,
    )
    from tokenshare.executors.trace_backed import (
        TraceBackedExecutor,
        TraceBackedParentStager,
    )
    from tokenshare.local_runtime.coordinator import commit_prepared_delivery
    from tokenshare.plugins.factorization.validator import verify_range_result

    success_stores = _stores(tmp_path / "success")
    case = generate_factorization_paper_cases()[0]
    runtime = FactorizationRuntimeAdapter(provider_family="siliconflow", seed=7)
    units = runtime.plan_units(case, artifact_store=success_stores.artifact_store)
    range_unit = next(unit for unit in units if runtime.planned_ai_unit_id(unit) is not None)
    range_input = runtime.range_input_for_unit(range_unit.unit_id)
    raw_result = factorization_paper_adapter_module._scripted_range_result(
        range_input.to_dict(), force_false_negative=False
    )
    success_resolver = _bank(
        tmp_path / "success-bank", contents=(json.dumps(raw_result, sort_keys=True),)
    )
    success_binding = _binding(success_resolver)
    success_request = factorization_paper_adapter_module.bind_factorization_trace_request(
        _bound_request(success_binding), success_binding
    )
    success_delivery = TraceBackedExecutor(
        resolver=success_resolver,
        bindings=(success_binding,),
        current_run_id="run_trace",
    ).execute(success_request, submission_id="ignored", submitted_at="ignored")
    success_delivery = success_delivery.with_child_completion(
        child_worker_id="worker-1", child_completion_sequence=1
    )
    lease = replace(
        _lease(),
        metadata={"binding_digest": success_binding.binding_digest, "attempt_ordinal": 0},
    )
    success_record = commit_prepared_delivery(
        delivery=success_delivery,
        worker_completion=SimpleNamespace(
            fact=_completion(), request=success_request, failure_kind=None
        ),
        killed_worker_ids=(), active_lease=lease,
        current_fencing_token="fence_trace",
        current_stores=replace(
            success_stores,
            trace_delivery_stager=TraceBackedParentStager(
                resolver=success_resolver,
                bindings=(success_binding,),
                domain_stage=factorization_paper_adapter_module.FactorizationTraceDomainStage(runtime),
            ),
        ),
    )
    wrapper = json.loads(
        success_stores.artifact_store.read_bytes(
            success_record.core.current_wrapper_ref
        ).decode("utf-8")
    )
    assert {item["object_role"] for item in wrapper["source_bank_object_locators"]} == {
        "request_body", "raw_output", "provenance", "usage", "latency",
        "pricing", "acquisition_attempt", "model_record",
    }
    canonical = json.loads(
        success_stores.artifact_store.read_bytes(
            success_record.core.canonical_ref
        ).decode("utf-8")
    )
    assert verify_range_result(canonical, child_input=range_input).status == "passed"

    failure_stores = _stores(tmp_path / "failure")
    failure_resolver = _bank(
        tmp_path / "failure-bank",
        terminal_kinds=("provider_failure",),
        contents=("upstream unavailable",),
    )
    failure_binding = _binding(failure_resolver)
    failure_request = factorization_paper_adapter_module.bind_factorization_trace_request(
        _bound_request(failure_binding), failure_binding
    )
    failure_delivery = TraceBackedExecutor(
        resolver=failure_resolver,
        bindings=(failure_binding,),
        current_run_id="run_trace",
    ).execute(failure_request, submission_id="ignored", submitted_at="ignored")
    failure_delivery = failure_delivery.with_child_completion(
        child_worker_id="worker-1", child_completion_sequence=1
    )
    failure_record = commit_prepared_delivery(
        delivery=failure_delivery,
        worker_completion=SimpleNamespace(
            fact=_completion(), request=failure_request, failure_kind=None
        ),
        killed_worker_ids=(),
        active_lease=replace(
            _lease(),
            metadata={"binding_digest": failure_binding.binding_digest, "attempt_ordinal": 0},
        ),
        current_fencing_token="fence_trace",
        current_stores=replace(
            failure_stores,
            trace_delivery_stager=TraceBackedParentStager(
                resolver=failure_resolver,
                bindings=(failure_binding,),
                domain_stage=factorization_paper_adapter_module.FactorizationTraceDomainStage(runtime),
            ),
        ),
    )
    failure_body = json.loads(
        failure_stores.artifact_store.read_bytes(
            failure_record.core.parser_result_ref
        ).decode("utf-8")
    )
    assert failure_record.core.canonical_ref is None
    assert failure_body["failure_kind"] == "http_error"
    assert failure_body["current_provider_call_count"] == 0
    failure_wrapper = json.loads(
        failure_stores.artifact_store.read_bytes(
            failure_record.core.current_wrapper_ref
        ).decode("utf-8")
    )
    assert {item["object_role"] for item in failure_wrapper["source_bank_object_locators"]} == {
        "request_body", "provider_failure", "provenance", "usage", "latency",
        "pricing", "acquisition_attempt", "model_record",
    }


def test_factorization_trace_replays_four_ordinary_attempts_then_fault_redelivery(
    tmp_path: Path,
) -> None:
    """生产 ProtocolEngine 必须按 binding 容量消费完整 Exp1 attempt 序列。"""

    from tests.executors.test_trace_backed import _bank
    from tokenshare.executors.trace_backed import (
        freeze_projected_trace_source_binding,
    )
    from tokenshare.experiments import paper_formal_runner
    from tokenshare.experiments.paper_response_bank import PaperTraceRuntimeContext

    case = next(
        item
        for item in generate_factorization_paper_cases()
        if item["factor_position_quantile"] != "no_factor"
    )
    requested = int(case["split_params"]["requested_child_count"])
    partition = partition_candidate_ranges(
        target_n=case["target_n"],
        requested_child_count=requested,
        max_children_per_unit=requested,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )
    factor = min(int(item["prime"]) for item in case["oracle_prime_factors"])
    child_index = next(
        item.child_index
        for item in partition.ranges
        if int(item.range_start) <= factor <= int(item.range_end)
    )
    planned_ai_unit_id = f"range_{child_index}"
    base_condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        selected_ai_unit_id=planned_ai_unit_id,
    )
    wrong_source = run_factorization_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / "wrong-source",
        transport=ScriptedFactorizationRangeTransport(
            force_false_negative_child_indices={child_index}
        ),
        real_transport=False,
        selected_ai_unit_id=planned_ai_unit_id,
    )
    source_attempt = next(
        attempt
        for attempt in source.attempt_results
        if attempt.planned_ai_unit_id == planned_ai_unit_id
    )
    wrong_source_attempt = next(
        attempt
        for attempt in wrong_source.attempt_results
        if attempt.planned_ai_unit_id == planned_ai_unit_id
    )
    assert source_attempt.parsed_output_ref is not None
    assert wrong_source_attempt.parsed_output_ref is not None
    terminal_candidate = _read_artifact(
        source.output_root,
        source_attempt.parsed_output_ref,
    )
    wrong_candidate = _read_artifact(
        wrong_source.output_root,
        wrong_source_attempt.parsed_output_ref,
    )
    resolver = _bank(
        tmp_path / "exp1-bank",
        terminal_kinds=("success", "success", "success", "success"),
        contents=(
            json.dumps(wrong_candidate, sort_keys=True),
            json.dumps(wrong_candidate, sort_keys=True),
            json.dumps(wrong_candidate, sort_keys=True),
            json.dumps(terminal_candidate, sort_keys=True),
        ),
        planned_ai_unit_id=planned_ai_unit_id,
    )
    binding = freeze_projected_trace_source_binding(
        resolver,
        current_planned_ai_unit_id=planned_ai_unit_id,
        current_sample_slot_index=0,
        source_entry_ids_by_current_attempt=(
            "entry-0",
            "entry-1",
            "entry-2",
            "entry-3",
            "entry-3",
        ),
        delivery_kinds_by_current_attempt=(
            "ordinary_attempt",
            "ordinary_attempt",
            "ordinary_attempt",
            "ordinary_attempt",
            "fault_redelivery",
        ),
        redelivery_reasons_by_current_attempt=(
            None,
            None,
            None,
            None,
            "false_positive_requeue",
        ),
    )
    trace_context = PaperTraceRuntimeContext(
        resolver=resolver,
        bindings=(binding,),
        inventory_rows=resolver.index.inventory_rows,
    )
    condition = replace(
        base_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id="exp3_factorization_four_source_attempts_redelivery_r0",
        fault_type="false_positive",
        fault_rate=1.0,
    )
    runtime_records: list[dict] = []
    fault_hook = paper_formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id=str(case["case_id"]),
        fault_type="false_positive",
        selected_unit_ids=(f'{case["case_id"]}:{planned_ai_unit_id}',),
        reserve_unit_ids=(),
        runtime_records=runtime_records,
    )

    class ProviderBomb:
        def __init__(self) -> None:
            self.calls = 0

        def post_chat_completion(self, **_kwargs):
            self.calls += 1
            raise AssertionError("trace replay must not call provider transport")

    provider_bomb = ProviderBomb()
    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "trace",
        transport=provider_bomb,
        real_transport=False,
        selected_ai_unit_id=planned_ai_unit_id,
        post_raw_output_hook=fault_hook,
        trace_context=trace_context,
    )

    target_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.planned_ai_unit_id == planned_ai_unit_id
    ]
    assert provider_bomb.calls == 0
    assert result.task_result.provider_attempt_count == 0
    persisted_requests = [
        _read_artifact(result.output_root, attempt.request_ref)
        for attempt in target_attempts
    ]
    assert [
        request["soft_hints"]["trace_source_entry_id"]
        for request in persisted_requests
    ] == [
        "entry-0",
        "entry-1",
        "entry-2",
        "entry-3",
        "entry-3",
    ]
    assert [
        request["soft_hints"]["trace_delivery_kind"]
        for request in persisted_requests
    ] == [
        "ordinary_attempt",
        "ordinary_attempt",
        "ordinary_attempt",
        "ordinary_attempt",
        "fault_redelivery",
    ]
    assert all(attempt.provider_attempt_count == 0 for attempt in target_attempts)
    assert len(runtime_records) == 1
    assert result.task_result.root_status is PaperTaskStatus.PARTIAL
