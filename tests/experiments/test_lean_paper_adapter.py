import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter
import tokenshare.experiments.paper_catalog as paper_catalog_module
import tokenshare.experiments.paper_formal_runner as paper_formal_runner
import tokenshare.plugins.lean_proof.runtime_adapter as lean_runtime_adapter
from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.contracts import EnvironmentRef, ExecutionSubmission
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_model_identity import (
    PaperModelIdentityMismatch,
    build_model_endpoint_identity,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskStatus,
)
from tokenshare.experiments.paper_runner import _planned_ai_unit_ids
from tokenshare.local_runtime import (
    ParsedCandidateContext,
    ParsedCandidateDirective,
    ProtocolRunCoordinator,
    RuntimeHookObservationV1,
    WorkerTerminationPolicy,
    build_experiment_ablation_gate_applied_observation,
)
from tokenshare.storage.artifacts import ArtifactStore
from tests.support.lean_checker import RecordingLeanChecker
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    check_lean_proof as real_lean_checker,
)


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"
LEAN_LEMMA_GRAPH_CATALOG = "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"


@pytest.fixture(autouse=True)
def _use_recording_checker_for_adapter_regressions(
    monkeypatch: pytest.MonkeyPatch,
) -> RecordingLeanChecker:
    checker = RecordingLeanChecker()
    repo_root = Path(__file__).resolve().parents[2]
    tracked_manifest = json.loads(
        (repo_root / "benchmarks/paper/lean_checker_preflight.v1.json").read_text(
            encoding="utf-8"
        )
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
    monkeypatch.setattr(lean_paper_adapter, "check_lean_proof", checker)
    monkeypatch.setattr(
        lean_paper_adapter,
        "default_lean_paper_environment_manifest",
        lambda: environment_manifest,
    )
    return checker


def test_lean_adapter_officially_parses_runtime_hook_observations() -> None:
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

    parsed = lean_paper_adapter._parse_runtime_hook_observations(
        (observation.to_dict(),)
    )

    assert parsed == (observation,)
    assert all(isinstance(item, RuntimeHookObservationV1) for item in parsed)

    tampered = observation.to_dict()
    tampered["payload"]["disabled_mechanism"] = "requeue"
    with pytest.raises(ValueError, match="observation_digest mismatch"):
        lean_paper_adapter._parse_runtime_hook_observations((tampered,))


def test_lean_fault_candidate_is_replaced_before_fixed_checker(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    parsed_ref = store.save_json(
        {"proof_candidate_id": "candidate-1", "proof_source": "by exact h"},
        artifact_id="parsed_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    mutated_ref = store.save_json(
        {
            "proof_candidate_id": "candidate-1:false_negative",
            "proof_source": "",
            "proof_suppressed": True,
        },
        artifact_id="fault_mutated_candidate",
        artifact_type="FaultMutatedCandidate",
        artifact_schema_id="tokenshare.paper_fault_mutated_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    environment_ref = EnvironmentRef(
        environment_id="lean-test",
        environment_digest="sha256:" + "1" * 64,
        runtime="lean",
        tool_versions={},
        resource_limits={},
        fixture_profile_digest="sha256:" + "2" * 64,
        seed=1,
        clock_policy="fixed",
        created_at="2026-08-09T00:00:00Z",
    )
    submission = ExecutionSubmission(
        submission_id="submission-1",
        request_id="request-1",
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        fencing_token="token-1",
        executor_id="ai_api",
        executor_version="1",
        result_kind="succeeded",
        raw_output_ref=parsed_ref,
        parsed_output_ref=parsed_ref,
        candidate_output_refs={"proof_artifact": parsed_ref},
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=environment_ref,
        environment_summary={},
        provenance_ref=None,
        usage_summary={"provider_attempt_count": 1},
        error=None,
        submitted_at="2026-08-09T00:00:00Z",
    )
    observed = []

    class _ParsedFaultHook:
        def after_parsed_candidate_persisted(self, context):
            observed.append(context)
            return ParsedCandidateDirective(
                replacement_candidate_output_refs={"proof_artifact": mutated_ref}
            )

    request = SimpleNamespace(
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        allocation_decision={"worker_id": "worker-1"},
        soft_hints={"planned_ai_unit_id": "lemma-node-1"},
    )

    checker_input = lean_paper_adapter._apply_pre_checker_parsed_candidate_hook(
        post_raw_output_hook=_ParsedFaultHook(),
        run_id="exp3-false-negative_lean-case-1",
        request=request,
        submission=submission,
    )

    assert len(observed) == 1
    assert observed[0].original_parsed_output_ref == parsed_ref
    assert observed[0].candidate_output_refs == {"proof_artifact": parsed_ref}
    assert observed[0].experiment_unit_id == "lemma-node-1"
    assert checker_input.parsed_output_ref == parsed_ref
    assert checker_input.candidate_output_refs == {"proof_artifact": mutated_ref}
    checker_candidate = json.loads(
        store.read_bytes(checker_input.candidate_output_refs["proof_artifact"]).decode(
            "utf-8"
        )
    )
    assert checker_candidate["proof_source"] == ""


def test_exp3_lean_fault_hook_is_typed_noop_after_prechecker_mutation(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = store.save_json(
        {"content": "provider response"},
        artifact_id="raw_output",
        artifact_type="RawOutput",
        artifact_schema_id="test.raw_output",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    provenance_ref = store.save_json(
        {"attempts": [{"latency_ms": 5}]},
        artifact_id="provenance",
        artifact_type="Provenance",
        artifact_schema_id="test.provenance",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    usage_ref = store.save_json(
        {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        artifact_id="usage",
        artifact_type="Usage",
        artifact_schema_id="test.usage",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    parsed_ref = store.save_json(
        {"proof_candidate_id": "candidate-1", "proof_source": "by exact h"},
        artifact_id="parsed_candidate",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    )
    request = SimpleNamespace(
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        allocation_decision={"worker_id": "worker-1"},
        soft_hints={"planned_ai_unit_id": "lemma-node-1"},
    )
    records = []
    bridge = paper_formal_runner._Exp3RuntimeHookBridge(
        condition=SimpleNamespace(
            condition_id="exp3-false-negative",
            repeat_id=0,
            seed=7,
        ),
        case_id="lean-case-1",
        fault_type="false_negative",
        selected_unit_ids=("lean-case-1:lemma-node-1",),
        reserve_unit_ids=(),
        runtime_records=records,
    )
    bridge(
        artifact_store=store,
        request=request,
        submission_id="submission-1",
        raw_output_ref=raw_ref,
        provenance_ref=provenance_ref,
        usage_ref=usage_ref,
        provider_family="deepseek",
        model="deepseek-v4-pro",
        entry_id="deepseek-v4-pro",
        content_text="provider response",
        usage_summary={
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
        },
        submitted_at="2026-08-09T00:00:00Z",
    )
    submission = SimpleNamespace(
        task_id="task-1",
        unit_id="unit-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        raw_output_ref=raw_ref,
        parsed_output_ref=parsed_ref,
        candidate_output_refs={"proof_artifact": parsed_ref},
        submitted_at="2026-08-09T00:00:00Z",
    )

    directive = bridge.after_parsed_candidate_persisted(
        ParsedCandidateContext(
            run_id="exp3-false-negative_lean-case-1",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            lease_id=submission.lease_id,
            worker_id="worker-1",
            raw_output_ref=submission.raw_output_ref,
            original_parsed_output_ref=submission.parsed_output_ref,
            candidate_output_refs=dict(submission.candidate_output_refs),
            submitted_at=submission.submitted_at,
            experiment_unit_id="lemma-node-1",
        )
    )
    assert directive is not None
    assert records and records[0]["fault_type"] == "false_negative"

    coordinator_repeat = bridge.after_parsed_candidate_persisted(
        ParsedCandidateContext(
            run_id="exp3-false-negative_lean-case-1",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            lease_id=submission.lease_id,
            worker_id="worker-1",
            raw_output_ref=submission.raw_output_ref,
            original_parsed_output_ref=submission.parsed_output_ref,
            candidate_output_refs=dict(directive.replacement_candidate_output_refs),
            submitted_at=submission.submitted_at,
            experiment_unit_id="lemma-node-1",
        )
    )
    assert coordinator_repeat is None
    assert len(records) == 1


def test_lean_native_provider_sources_exclude_only_incomplete_fault_attempts(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.save_json(
        {"kind": "provider-evidence"},
        artifact_id="provider_evidence",
        artifact_type="ProviderEvidence",
        artifact_schema_id="test.provider_evidence",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    ).to_dict()
    complete_actual = SimpleNamespace(
        provider_attempt_count=1,
        request_ref=ref,
        raw_output_ref=ref,
        parse_failure_ref=None,
        provenance_ref=ref,
        usage_ref=ref,
        model_execution_record_ref=ref,
        fault_injection_ref=None,
    )
    incomplete_fault = SimpleNamespace(
        provider_attempt_count=1,
        request_ref=ref,
        raw_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        model_execution_record_ref=None,
        fault_injection_ref=ref,
    )

    sources = lean_paper_adapter._native_online_provider_sources(
        (complete_actual, incomplete_fault)
    )

    assert set(sources) == {
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    }
    assert all(len(refs) == 1 for refs in sources.values())
    incomplete_nonfault = SimpleNamespace(
        **{**vars(incomplete_fault), "fault_injection_ref": None}
    )
    with pytest.raises(ValueError, match="actual provider evidence is incomplete"):
        lean_paper_adapter._native_online_provider_sources(
            (complete_actual, incomplete_nonfault)
        )


def test_lean_native_provider_sources_ignore_zero_call_protocol_attempt(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.save_json(
        {"kind": "provider-evidence"},
        artifact_id="provider_evidence",
        artifact_type="ProviderEvidence",
        artifact_schema_id="test.provider_evidence",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-08-09T00:00:00Z",
    ).to_dict()
    complete_actual = SimpleNamespace(
        provider_attempt_count=1,
        request_ref=ref,
        raw_output_ref=ref,
        parse_failure_ref=None,
        provenance_ref=ref,
        usage_ref=ref,
        model_execution_record_ref=ref,
        fault_injection_ref=None,
    )
    zero_call = SimpleNamespace(
        provider_attempt_count=0,
        request_ref=ref,
        raw_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        model_execution_record_ref=None,
        fault_injection_ref=None,
    )

    sources = lean_paper_adapter._native_online_provider_sources(
        (complete_actual, zero_call)
    )

    assert all(len(refs) == 1 for refs in sources.values())


@pytest.mark.parametrize(
    ("mode", "mechanism", "business_result_field"),
    (
        ("NO_MERGE_GATE", "merge_gate", "premature_merge_attempted"),
        ("NO_SLOT_INTEGRITY", "slot_integrity", "slot_integrity_violation"),
    ),
)
def test_lean_ablation_gate_alone_does_not_claim_business_result(
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

    runtime = lean_paper_adapter._lean_ablation_runtime_evidence(
        mode=mode,
        attempts=[],
        merge_summary={},
        root_validity=None,
        hook_observations=(observation,),
    )

    assert runtime[business_result_field] is False
    assert runtime["hook_observations"] == [observation.to_dict()]


def test_lean_paper_adapter_runs_split_children_through_ai_api_checker_and_merge(
    tmp_path,
    _use_recording_checker_for_adapter_regressions: RecordingLeanChecker,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedLeanPaperProofTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.schema_version == "tokenshare.lean_paper_run.v1"
    assert result.split_summary["split_status"] == "succeeded"
    assert result.split_summary["child_count"] == case["expected_child_count"]
    assert len(transport.calls) == result.task_result.provider_attempt_count
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_difficulty == "simple"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.topic_family_version == "shallow_v1"
    assert result.task_result.construction_rule_id is None
    assert result.task_result.oracle_package_group is None
    assert result.task_result.proof_assembly_shape is None
    assert result.task_result.paper_eligible is False
    assert result.eligibility_report.paper_eligible is False
    assert "real_transport_required" in result.eligibility_report.ineligibility_reasons
    assert "unsupported_transport:scripted" in result.eligibility_report.ineligibility_reasons
    assert "secret_scan_failed" not in result.eligibility_report.ineligibility_reasons
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert result.run_evidence["secret_scan_report"]["leak_count"] == 0

    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["environment_digest"] == case["environment_digest"]
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]
    assert len(result.child_results) == case["expected_child_count"]
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert all(
        item["checker"]["environment_digest"] == case["environment_digest"]
        for item in result.child_results
    )
    assert all(item["checker"]["report_ref"] for item in result.child_results)
    assert all(item["checker"]["proof_artifact_ref"] for item in result.child_results)
    checker_spy = _use_recording_checker_for_adapter_regressions
    assert checker_spy.modes.count(LeanCheckerMode.CHILD_PROOF) == case[
        "expected_child_count"
    ]
    assert checker_spy.modes[-1] == LeanCheckerMode.MERGE_PROOF
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.usage_ref is not None for attempt in result.attempt_results)
    assert all(attempt.provider == "siliconflow" for attempt in result.attempt_results)
    assert all(attempt.entry_id == "lean_paper_scripted" for attempt in result.attempt_results)
    assert all(
        attempt.paper_difficulty == "simple"
        and attempt.topic_family == "pure_logic"
        and attempt.topic_family_version == "shallow_v1"
        and attempt.construction_rule_id is None
        and attempt.oracle_package_group is None
        and attempt.proof_assembly_shape is None
        for attempt in result.attempt_results
    )
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    provider_prompt = transport.calls[0]["body_bytes"].decode("utf-8")
    assert "lean_proof.proof_candidate.v1" in provider_prompt
    assert "Do not return a split plan" in provider_prompt
    assert "claim_checker_success" in provider_prompt


def test_lean_no_slot_integrity_binds_wrong_slot_inside_local_runtime(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    base_condition = _condition(catalog.catalog_digest)
    condition = PaperExperimentCondition(
        **{
            **base_condition.__dict__,
            "experiment_id": "exp4_real_ai_protocol_ablation",
            "condition_id": "exp4_lean_no_slot_integrity_r0",
            "ablation_mode": "NO_SLOT_INTEGRITY",
        }
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
        ablation_mode="NO_SLOT_INTEGRITY",
    )

    assert result.merge_summary["slot_integrity_violation"] is True
    assert (
        result.merge_summary["slot_binding_applied_by"]
        == "local_runtime_policy"
    )
    assert result.run_evidence["ablation_runtime"][
        "slot_integrity_violation"
    ] is True
    assert any(
        observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
        and observation["payload"]["disabled_mechanism"] == "slot_integrity"
        for observation in result.run_evidence["ablation_runtime"][
            "hook_observations"
        ]
    )


def test_lean_no_slot_integrity_wrong_binding_is_rejected_by_real_checker(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    base_condition = _condition(catalog.catalog_digest)
    condition = PaperExperimentCondition(
        **{
            **base_condition.__dict__,
            "experiment_id": "exp4_real_ai_protocol_ablation",
            "condition_id": "exp4_lean_no_slot_integrity_real_checker_r0",
            "ablation_mode": "NO_SLOT_INTEGRITY",
        }
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
        ablation_mode="NO_SLOT_INTEGRITY",
        checker=real_lean_checker,
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.merge_summary["slot_integrity_violation"] is True
    assert (
        result.merge_summary["slot_binding_applied_by"]
        == "local_runtime_policy"
    )


def test_lean_paper_adapter_can_execute_exactly_one_selected_ai_unit(
    tmp_path,
    monkeypatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedLeanPaperProofTransport()
    coordinator_calls = 0
    original = ProtocolRunCoordinator.run_root

    def recording(self, request):
        nonlocal coordinator_calls
        coordinator_calls += 1
        return original(self, request)

    monkeypatch.setattr(ProtocolRunCoordinator, "run_root", recording)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
        selected_ai_unit_id="child_0",
    )

    assert len(transport.calls) == 1
    assert coordinator_calls == 1
    assert len(result.attempt_results) == 1
    assert result.attempt_results[0].planned_ai_unit_id == "child_0"
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

    untouched_transport = ScriptedLeanPaperProofTransport()
    with pytest.raises(ValueError, match="selected_ai_unit_id"):
        run_lean_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path / "invalid-selection",
            transport=untouched_transport,
            real_transport=False,
            entry_id="lean_paper_scripted",
            selected_ai_unit_id="child_99",
        )
    assert untouched_transport.calls == []


def test_lean_paper_adapter_runs_v2_medium_pure_logic_lemma_dag_nodes_through_ai_api_checker_and_merge(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert case["expected_split_kind"] == "recursive_lemma_dag"
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.provider_attempt_count == case["expected_ai_unit_count"]
    assert len(transport.calls) == case["expected_ai_unit_count"]
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_difficulty == "medium_lemma_dag"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.construction_rule_id == case["construction_rule_id"]
    assert result.task_result.oracle_package_group == case["oracle_package_group"]
    assert result.task_result.proof_assembly_shape == case["proof_assembly_shape"]
    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]
    assert result.merge_summary["merge_result_ref"]

    node_ids = {node["node_id"] for node in case["lemma_graph"]["nodes"]}
    assert {item["lemma_node_id"] for item in result.child_results} == node_ids
    assert all(item["unit_type"] == "lean_proof_lemma_node" for item in result.child_results)
    assert all(item["slot_key"].endswith(":lean_proof_artifact") for item in result.child_results)
    assert all(item["dependency_path"] for item in result.child_results)
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert all(item["candidate_output_ref"] for item in result.child_results)
    assert all(item["raw_output_ref"] for item in result.child_results)
    assert all(item["parsed_output_ref"] for item in result.child_results)
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(
        attempt.to_dict()["paper_difficulty"] == "medium_lemma_dag"
        for attempt in result.attempt_results
    )
    assert all(
        attempt.to_dict()["topic_family"] == "pure_logic"
        for attempt in result.attempt_results
    )
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    prompt = transport.calls[0]["body_bytes"].decode("utf-8")
    assert "Do not return a split plan" in prompt
    assert "Do not propose child tasks" in prompt


def test_lean_lemma_dag_worker_death_uses_protocol_lease_recovery(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    base_condition = _condition_for_case(catalog.catalog_digest, case)
    condition = PaperExperimentCondition(
        **{
            **base_condition.__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": "exp3_lean_worker_death_progress_25_k1_r0",
            "fault_type": "worker_death",
        }
    )
    selected_node_id = str(case["merge_plan_shape"]["dependency_order"][1])

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(
            proof_sources_by_statement=_oracle_sources_by_statement(case)
        ),
        real_transport=False,
        entry_id="lean_paper_scripted",
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=(selected_node_id,),
            kill_point="progress_25",
            total_planned_ai_unit_count=len(case["lemma_graph"]["nodes"]),
        ),
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    dead_attempts = [
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
    ]
    assert len(dead_attempts) == 1
    assert dead_attempts[0].planned_ai_unit_id == selected_node_id
    assert any(
        attempt.unit_id == dead_attempts[0].unit_id
        and attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
        for attempt in result.attempt_results
    )
    assert len(result.fault_records) == 1
    assert (
        result.fault_records[0]["target_ai_unit"]["metadata"][
            "planned_ai_unit_id"
        ]
        == selected_node_id
    )
    assert result.fault_records[0]["lease_expiry"]["trigger"] == "lease_expired"


@pytest.mark.parametrize(
    ("case_id", "topic_family"),
    (
        ("lean_v2_medium_lemma_dag_01", "pure_logic"),
        ("lean_v2_medium_function_set_dx_subset_chain_01", "function_set"),
        ("lean_v2_medium_induction_nat_predicate_chain_01", "induction"),
    ),
)
def test_lean_medium_dead3_p75_records_three_real_process_deaths(
    tmp_path,
    case_id: str,
    topic_family: str,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, case_id)
    base_condition = _condition_for_case(catalog.catalog_digest, case)
    condition = PaperExperimentCondition(
        **{
            **base_condition.__dict__,
            "experiment_id": "exp3_real_ai_fault_recovery",
            "condition_id": (
                f"exp3_worker_death_lean__{topic_family}__dead3__p75__rep0"
            ),
            "fault_type": "worker_death",
        }
    )
    planned = tuple(_planned_ai_unit_ids(case))
    anchor = (len(planned) * 75 + 99) // 100 - 1
    formal_targets = planned[anchor:]

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=ScriptedLeanPaperProofTransport(
            proof_sources_by_statement=_oracle_sources_by_statement(case)
        ),
        real_transport=False,
        entry_id="lean_paper_scripted",
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=formal_targets,
            termination_count_target=3,
            kill_point="progress_75",
            total_planned_ai_unit_count=len(planned),
        ),
    )

    dead_attempts = tuple(
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
    )
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(dead_attempts) == 3
    assert len(result.fault_records) == 3
    assert len({record["worker_pid"] for record in result.fault_records}) == 3
    assert all(
        record["lease_expiry"]["trigger"] == "lease_expired"
        for record in result.fault_records
    )


@pytest.mark.parametrize(
    "case_id, topic_family, expected_ai_units",
    [
        ("lean_v2_medium_function_set_dx_subset_chain_01", "function_set", 4),
        ("lean_v2_medium_induction_nat_predicate_chain_01", "induction", 5),
    ],
)
def test_lean_paper_adapter_runs_v2_medium_topic_family_regressions(
    tmp_path,
    case_id: str,
    topic_family: str,
    expected_ai_units: int,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, case_id)
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.provider_attempt_count == expected_ai_units
    assert len(transport.calls) == expected_ai_units
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.task_result.topic_family == topic_family
    assert result.task_result.paper_difficulty == "medium_lemma_dag"
    assert result.task_result.construction_rule_id == case["construction_rule_id"]
    assert result.run_evidence["lean_lemma_graph"]["topic_family"] == topic_family
    assert result.run_evidence["lean_lemma_graph"]["proof_assembly_shape"] == case["proof_assembly_shape"]


def test_lean_paper_adapter_blocks_v2_merge_when_node_checker_rejects(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    proof_sources = _oracle_sources_by_statement(case)
    proof_sources["P"] = "by\n  exact hpq"
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=proof_sources
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
        checker=real_lean_checker,
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.task_result.failure_stage == PaperFailureStage.CHECKER
    assert result.task_result.failure_kind == PaperFailureKind.CHECKER_REJECTED
    assert result.merge_summary["status"] == "blocked"
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert not result.merge_summary.get("root_proof_artifact_ref")
    assert any(item["checker"]["accepted"] is False for item in result.child_results)
    assert any(
        attempt.attempt_status == PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_maps_v2_parse_failure_without_fake_merge_success(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_InvalidJsonTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert result.merge_summary["status"] == "blocked"
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_maps_v2_provider_failure_without_fake_merge_success(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ProviderErrorTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.failure_kind == PaperFailureKind.PROVIDER_ERROR
    assert result.merge_summary["status"] == "blocked"
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_returns_structured_blocked_for_v2_hard_frontier_no_oracle(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_hard_frontier_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.BLOCKED
    assert result.task_result.accepted_validity is False
    assert result.task_result.provider_attempt_count == 0
    assert result.task_result.paper_difficulty == "hard_frontier"
    assert result.task_result.topic_family == case["topic_family"]
    assert result.merge_summary["status"] == "blocked"
    assert result.merge_summary["reason"] == "structured_blocked_no_oracle_frontier_stress"
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert len(transport.calls) == 0
    assert result.attempt_results == ()
    assert result.run_evidence["lean_lemma_graph"]["preflight_status"] == "structured_blocked"


def test_lean_paper_adapter_executes_checker_backed_hard_frontier_golden(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_hard_frontier_pure_logic_checker_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert case["paper_difficulty"] == "hard_frontier"
    assert case["preflight_status"] == "passed"
    assert isinstance(case["oracle_proof_package_ref"], dict)
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.paper_difficulty == "hard_frontier"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.provider_attempt_count == case["expected_ai_unit_count"]
    assert len(transport.calls) == case["expected_ai_unit_count"]
    assert result.split_summary["split_status"] == "succeeded"
    assert result.split_summary["split_kind"] == "recursive_lemma_dag"
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]


def test_lean_paper_adapter_simple_blocked_split_preserves_frozen_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport()
    monkeypatch.setattr(
        lean_runtime_adapter,
        "run_lean_split_helper",
        lambda *args, **kwargs: SimpleNamespace(
            status=SimpleNamespace(value="failed"),
            certificate=None,
            certificate_ref=None,
            report_ref=None,
        ),
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.BLOCKED
    assert result.task_result.paper_difficulty == "simple"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.topic_family_version == "shallow_v1"
    assert result.task_result.construction_rule_id is None
    assert result.task_result.oracle_package_group is None
    assert result.task_result.proof_assembly_shape is None
    assert result.attempt_results == ()
    assert transport.calls == []


def test_lean_paper_adapter_blocks_merge_when_checker_rejects_child_proof(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement={"P": "by\n  exact hQ"}
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
        checker=real_lean_checker,
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.task_result.failure_stage == PaperFailureStage.CHECKER
    assert result.task_result.failure_kind == PaperFailureKind.CHECKER_REJECTED
    assert result.merge_summary["status"] == "blocked"
    assert any(item["checker"]["accepted"] is False for item in result.child_results)
    assert any(
        attempt.attempt_status == PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_reports_parse_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_InvalidJsonTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert result.merge_summary["status"] == "blocked"
    assert any(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_reports_provider_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ProviderErrorTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.failure_kind == PaperFailureKind.PROVIDER_ERROR
    assert result.merge_summary["status"] == "blocked"
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_rejects_custom_transport_marked_real(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibSiliconFlowTransport"):
        run_lean_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=_CustomRealTransportSubclass(),
            real_transport=True,
            ai_api_config=_real_transport_config(),
        )


def test_lean_real_transport_guard_accepts_provider_router_wrapper() -> None:
    real_transport = UrlLibSiliconFlowTransport()

    class Wrapper:
        def tokenshare_transport_for_provider(self, provider_family: str):
            assert provider_family == "siliconflow"
            return real_transport

    lean_paper_adapter._validate_real_transport_mode(
        real_transport=True,
        transport=Wrapper(),
        ai_api_config=_real_transport_config(),
    )


def test_lean_paper_adapter_accepts_openai_real_transport_through_executor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
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
                "id": "fake-openai-lean",
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

    result = run_lean_paper_case(
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
    registration_time = next(
        event["occurred_at"]
        for event in result.event_records
        if event["event_type"] == "TASK_REGISTERED"
    )
    later_plugin_events = [
        event
        for event in result.event_records
        if event["event_type"]
        in {
            "SPLIT_STRATEGY_INVOCATION_RECORDED",
            "EXPANSION_DECISION_RECORDED",
        }
    ]
    assert later_plugin_events
    assert all(
        event["occurred_at"] != registration_time
        for event in later_plugin_events
    )
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


def test_lean_paper_adapter_v2_openai_request_artifacts_record_openai(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
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
                "id": "fake-openai-lean-lemma-graph",
                    "model": body["model"],
                "choices": [
                    {
                        "message": {"content": "not-json"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    monkeypatch.setattr(transport, "post_chat_completion", fake_openai_call)

    result = run_lean_paper_case(
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
    assert all(attempt.provider == "openai" for attempt in result.attempt_results)
    assert all(
        attempt.to_dict()["paper_difficulty"] == "medium_lemma_dag"
        for attempt in result.attempt_results
    )
    for attempt in result.attempt_results:
        request = _read_request_artifact(result.output_root, attempt.request_ref)
        assert request["capability_snapshot"]["provider_family"] == "openai"
        assert request["hard_requirements"]["provider_family"] == "openai"
        assert _registry_provider_matches(request) == ["openai"]


def test_lean_paper_adapter_rejects_openai_url_transport_without_real_flag(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibOpenAITransport"):
        run_lean_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=UrlLibOpenAITransport(),
            real_transport=False,
        )


def test_lean_paper_adapter_rejects_same_entry_id_with_wrong_model_before_simple_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        model="gpt-5.6-sol-wrong",
        reasoning_effort="high",
    )
    transport = ScriptedLeanPaperProofTransport()
    output_root = tmp_path / "wrong-model"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
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


def test_lean_paper_adapter_rejects_reasoning_profile_drift_before_lemma_dag_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )
    output_root = tmp_path / "wrong-reasoning"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
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


def test_lean_retry_keeps_original_identity_and_rejects_changed_config(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )

    initial_transport = ScriptedLeanPaperProofTransport(model="gpt-5.6-sol")
    initial = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "initial-attempt",
        transport=initial_transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )
    replacement_transport = ScriptedLeanPaperProofTransport(model="gpt-5.6-sol")
    replacement = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "replacement-attempt",
        transport=replacement_transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert initial.task_result.root_status == PaperTaskStatus.COMPLETED
    assert replacement.task_result.root_status == PaperTaskStatus.COMPLETED
    assert initial.condition is condition
    assert replacement.condition is condition
    assert {
        json.loads(call["body_bytes"].decode("utf-8"))["model"]
        for call in replacement_transport.calls
    } == {"gpt-5.6-sol"}
    for attempt in replacement.attempt_results:
        assert attempt.model_execution_record_ref is not None
        execution_record = _read_artifact(
            replacement.output_root,
            attempt.model_execution_record_ref,
        )
        assert execution_record["identity_status"] == "matched"
        assert execution_record["mismatch_reasons"] == []
        assert execution_record["requested_model"] == "gpt-5.6-sol"
        assert execution_record["resolved_model"] == "gpt-5.6-sol"
        assert execution_record["response_model_status"] == "present"
        assert execution_record["source_provider_config_digest"] == (
            approved_config.config_digest
        )
        assert execution_record["prepared_execution_config_digest"] != (
            execution_record["source_provider_config_digest"]
        )

    changed_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    rejected_transport = ScriptedLeanPaperProofTransport()
    rejected_output = tmp_path / "changed-config-attempt"
    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
                case=case,
                condition=condition,
                output_root=rejected_output,
                transport=rejected_transport,
                real_transport=False,
                ai_api_config=changed_config,
                entry_id="gpt-entry",
            )
        finally:
            assert rejected_transport.calls == []
            assert not rejected_output.exists()


def test_lean_simple_resolved_model_mismatch_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        model="gpt-5.6-sol-versioned",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "simple-resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_resolved_model_mismatch_stops_condition(result, transport)
    assert len(result.child_results) == len(transport.calls)


def test_lean_lemma_dag_fixed_entry_matching_response_writes_matched_v2_records(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
        model="gpt-5.6-sol",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-matching-response",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(transport.calls) == case["expected_ai_unit_count"]
    for attempt in result.attempt_results:
        assert attempt.model_execution_record_ref is not None
        assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
        record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
        assert record["identity_status"] == "matched"
        assert record["requested_model"] == "gpt-5.6-sol"
        assert record["resolved_model"] == "gpt-5.6-sol"
        assert record["response_model_status"] == "present"
        assert record["mismatch_reasons"] == []


def test_lean_lemma_dag_resolved_model_mismatch_records_audit_and_stops_later_nodes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
        model="gpt-5.6-sol-versioned",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_resolved_model_mismatch_stops_condition(result, transport)
    assert len(result.child_results) == len(transport.calls)
    assert case["expected_ai_unit_count"] > 1


def test_lean_simple_missing_resolved_model_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelLeanTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "simple-missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_missing_resolved_model_stops_condition(result, transport)
    assert len(result.child_results) == len(transport.calls)


def test_lean_lemma_dag_missing_resolved_model_records_audit_and_stops_later_nodes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelLeanTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_missing_resolved_model_stops_condition(result, transport)
    assert len(result.child_results) == len(transport.calls)
    assert case["expected_ai_unit_count"] > 1


def _assert_resolved_model_mismatch_stops_condition(result, transport) -> None:
    assert len(transport.calls) == result.task_result.attempt_count
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
        and item.error_kind == "model_identity_mismatch"
        and item.paper_eligible is False
        for item in result.attempt_results
    )
    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    provenance = _read_artifact(result.output_root, attempt.provenance_ref)
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["paper_eligible"] is False
    assert "resolved_model_mismatch" in record["mismatch_reasons"]
    assert record["actual_request_identities"][0]["configured_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["requested_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert record["resolved_model"] == "gpt-5.6-sol-versioned"
    assert record["source_provider_config_digest"] == (
        result.condition.source_provider_config_digest
    )
    assert record["prepared_execution_config_digest"] == provenance["config_digest"]


def _assert_missing_resolved_model_stops_condition(result, transport) -> None:
    assert len(transport.calls) == result.task_result.attempt_count
    assert result.task_result.provider_attempt_count == len(transport.calls)
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.task_result.paper_eligible is False
    assert result.merge_summary["status"] == "blocked"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.model_execution_record_ref is not None
    assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
    assert attempt.raw_output_ref is not None
    assert attempt.provenance_ref is not None
    assert attempt.usage_ref is not None
    assert all(
        item.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        and item.error_kind == "model_identity_mismatch"
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


class _MissingResolvedModelLeanTransport(ScriptedLeanPaperProofTransport):
    def post_chat_completion(self, **kwargs):
        response = super().post_chat_completion(**kwargs)
        response.body.pop("model", None)
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response


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


def _condition(catalog_digest: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_easy_w10_r0",
        domain="lean_proof",
        difficulty="easy",
        paper_difficulty="simple",
        topic_family="pure_logic",
        topic_family_version="shallow_v1",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _condition_for_case(catalog_digest: str, case: dict) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"exp1_lean_{case['paper_difficulty']}_{case['case_id']}_r0",
        domain="lean_proof",
        difficulty=case["difficulty"],
        paper_difficulty=case.get("paper_difficulty"),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _formal_exp5_condition_for_case(
    *,
    catalog_digest: str,
    case: dict,
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
        condition_id=f"exp5_lean_{case['case_id']}_gpt_r0",
        domain="lean_proof",
        difficulty=case["difficulty"],
        paper_difficulty=case.get("paper_difficulty"),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
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


def _catalog_with_lemma_graph():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
        lean_lemma_graph_path=LEAN_LEMMA_GRAPH_CATALOG,
    )


def _v2_case(catalog, case_id: str) -> dict:
    matches = [
        case
        for case in catalog.cases_for(domain="lean_proof")
        if case["case_id"] == case_id
    ]
    assert len(matches) == 1
    return matches[0]


def _oracle_sources_by_statement(case: dict) -> dict[str, str]:
    proof_sources_by_node = case["oracle_proof_package_ref"]["node_proof_sources"]
    result: dict[str, str] = {}
    for node in case["lemma_graph"]["nodes"]:
        result[node["theorem_payload"]["statement_source"]] = proof_sources_by_node[node["node_id"]]
    return result


def _identity_config(*, model: str, reasoning_effort: str):
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
                "max_tokens": 1024,
                "temperature": 0.0,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "gpt-entry",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
                    "model": model,
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {
                        "temperature": 0.0,
                        "reasoning_effort": reasoning_effort,
                    },
                    "pricing": {
                        "currency": "USD",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["identity_test"],
                }
            ],
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
                    "tags": ["lean_paper", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-real-transport-guard-test"},
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
                "max_tokens": 1024,
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
                    "tags": ["lean_paper", "openai", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-openai-real-transport-guard-test"},
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


def test_resume_restores_entry_ordinal_roles_and_delivery_timing(
    tmp_path,
    _use_recording_checker_for_adapter_regressions,
) -> None:
    from dataclasses import replace
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
        TraceSourceBinding,
    )
    from tokenshare.local_runtime.coordinator import commit_prepared_delivery
    from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
    from tokenshare.plugins.lean_proof.prompt_builder import (
        derive_lean_proof_candidate_id_v2,
    )
    from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter

    resolver = _bank(
        tmp_path / "lean-resume-bank",
        terminal_kinds=("success", "success"),
        contents=(
            json.dumps({"proof_source": "by exact hP", "slot": 0}),
            json.dumps({"proof_source": "by exact hP", "slot": 1}),
        ),
    )
    original = _binding(resolver)
    resumed = TraceSourceBinding.from_dict(original.to_dict())
    request = _bound_request(resumed, ordinal=1, logical_start_ms=90)
    delivery = TraceBackedExecutor(
        resolver=resolver,
        bindings=(resumed,),
        current_run_id="lean-resumed-run",
    ).execute(request, submission_id="ignored", submitted_at="ignored")

    assert resumed == original
    assert delivery.entry_id == "entry-1"
    assert delivery.attempt_ordinal == 1
    assert delivery.source_latency_ms == 11
    assert delivery.logical_finish_ms == 101
    assert {item["object_role"] for item in delivery.source_bank_object_locators} == {
        "request_body", "raw_output", "provenance", "usage", "latency",
        "pricing", "acquisition_attempt", "model_record",
    }
    assert hasattr(lean_paper_adapter, "LeanTraceDomainStage")

    stores = _stores(tmp_path / "lean-formal-chain")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    runtime = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=lean_paper_adapter.default_lean_paper_environment_manifest(),
        checker=_use_recording_checker_for_adapter_regressions,
        seed=7,
    )
    units = runtime.plan_units(case, artifact_store=stores.artifact_store)
    proof_unit = next(unit for unit in units if runtime.planned_ai_unit_id(unit) is not None)
    planned_ai_unit_id = runtime.planned_ai_unit_id(proof_unit)
    payload_ref = proof_unit.input_refs.get(
        "lemma_theorem_payload",
        proof_unit.input_refs.get("child_theorem_payload"),
    )
    payload = LeanTheoremPayload.from_dict(
        json.loads(stores.artifact_store.read_bytes(payload_ref).decode("utf-8"))
    )
    candidate = {
        "schema_version": "lean_proof.proof_candidate.v1",
        "proof_candidate_id": derive_lean_proof_candidate_id_v2(
            theorem_payload_digest=payload.payload_digest,
            planned_ai_unit_id=planned_ai_unit_id,
        ),
        "theorem_payload_digest": payload.payload_digest,
        "proof_source": lean_paper_adapter._scripted_proof_source(
            payload.statement_source
        ),
        "created_at": lean_paper_adapter.NOW,
    }
    formal_resolver = _bank(
        tmp_path / "lean-formal-bank",
        contents=(json.dumps(candidate, sort_keys=True),),
        planned_ai_unit_id=planned_ai_unit_id,
    )
    formal_binding = _binding(formal_resolver)
    formal_request = replace(
        lean_paper_adapter.bind_lean_trace_request(
            _bound_request(formal_binding), formal_binding
        ),
        input_artifact_refs=dict(proof_unit.input_refs),
        task_unit_snapshot=proof_unit.to_dict(),
        request_id="lean_trace_request",
    )
    formal_delivery = TraceBackedExecutor(
        resolver=formal_resolver,
        bindings=(formal_binding,),
        current_run_id="run_trace",
    ).execute(formal_request, submission_id="ignored", submitted_at="ignored")
    formal_delivery = formal_delivery.with_child_completion(
        child_worker_id="worker-1", child_completion_sequence=1
    )
    runtime_records = []
    fault_hook = paper_formal_runner._Exp3RuntimeHookBridge(
        condition=SimpleNamespace(
            condition_id="exp3-trace-false-negative",
            repeat_id=0,
            seed=7,
        ),
        case_id=str(case["case_id"]),
        fault_type="false_negative",
        selected_unit_ids=(f'{case["case_id"]}:{planned_ai_unit_id}',),
        reserve_unit_ids=(),
        runtime_records=runtime_records,
    )
    trace_domain_stage = lean_paper_adapter.LeanTraceDomainStage(
        runtime,
        post_raw_output_hook=fault_hook,
    )
    record = commit_prepared_delivery(
        delivery=formal_delivery,
        worker_completion=SimpleNamespace(
            fact=_completion(), request=formal_request, failure_kind=None
        ),
        killed_worker_ids=(),
        active_lease=replace(
            _lease(),
            metadata={"binding_digest": formal_binding.binding_digest, "attempt_ordinal": 0},
        ),
        current_fencing_token="fence_trace",
        current_stores=replace(
            stores,
            trace_delivery_stager=TraceBackedParentStager(
                resolver=formal_resolver,
                bindings=(formal_binding,),
                domain_stage=trace_domain_stage,
            ),
        ),
    )
    assert _use_recording_checker_for_adapter_regressions.requests
    assert _use_recording_checker_for_adapter_regressions.modes == [
        LeanCheckerMode.CHILD_PROOF
    ]
    assert record.core.canonical_ref is not None
    assert record.core.canonical_ref.artifact_type == "canonical_output"
    assert record.core.execution_result_kind is None
    assert len(record.core.verifier_checker_refs) == 1
    assert len(runtime_records) == 1
    assert runtime_records[0]["fault_type"] == "false_negative"
    mutated_ref = runtime_records[0]["mutated_output_ref"]
    checker_ref = _use_recording_checker_for_adapter_regressions.requests[
        -1
    ].proof_candidate_ref
    assert checker_ref.to_dict() == mutated_ref
    assert checker_ref.content_hash != record.core.parser_result_ref.content_hash
    checker_candidate = json.loads(
        stores.artifact_store.read_bytes(checker_ref).decode("utf-8")
    )
    assert checker_candidate["proof_source"] == ""
    provenance = json.loads(
        stores.artifact_store.read_bytes(record.core.current_provenance_ref).decode(
            "utf-8"
        )
    )
    attribution = json.loads(
        stores.artifact_store.read_bytes(record.core.trace_attribution_refs[0]).decode(
            "utf-8"
        )
    )
    assert provenance["current_provider_call_count"] == 0
    assert attribution["current_provider_call_count"] == 0
    assert attribution["source_usage"]

    staged_submission = trace_domain_stage.submissions_by_attempt[
        formal_request.attempt_id
    ]
    assert fault_hook.after_parsed_candidate_persisted(
        ParsedCandidateContext(
            run_id="run_trace",
            task_id=staged_submission.task_id,
            unit_id=staged_submission.unit_id,
            attempt_id=staged_submission.attempt_id,
            lease_id=staged_submission.lease_id,
            worker_id="worker-1",
            raw_output_ref=staged_submission.raw_output_ref,
            original_parsed_output_ref=staged_submission.parsed_output_ref,
            candidate_output_refs=dict(staged_submission.candidate_output_refs),
            submitted_at=staged_submission.submitted_at,
            experiment_unit_id=planned_ai_unit_id,
        )
    ) is None
    assert len(runtime_records) == 1


@pytest.mark.parametrize(
    "terminal_result_kind",
    ("no_return", "late_submission", "executor_error"),
)
def test_exp3_lean_trace_terminal_fault_recovers_with_fresh_checker(
    tmp_path: Path,
    terminal_result_kind: str,
    _use_recording_checker_for_adapter_regressions: RecordingLeanChecker,
) -> None:
    from dataclasses import replace

    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments.paper_response_bank import PaperTraceRuntimeContext
    from tokenshare.local_runtime.contracts import PreparedTraceDelivery

    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    base_condition = _condition_for_case(catalog.catalog_digest, case)
    source = run_lean_paper_case(
        case=case,
        condition=base_condition,
        output_root=tmp_path / f"source-{terminal_result_kind}",
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
        checker=RecordingLeanChecker(),
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / f"bank-{terminal_result_kind}",
        adapter_result=source,
        replacement_count=2,
    )
    target_ai_unit_id = next(
        str(attempt.planned_ai_unit_id)
        for attempt in source.attempt_results
        if attempt.planned_ai_unit_id is not None
    )
    target_binding = next(
        binding
        for binding in trace_context.bindings
        if binding.planned_ai_unit_id == target_ai_unit_id
    )
    runtime_records: list[dict] = []
    condition = replace(
        base_condition,
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id=f"exp3-trace-{terminal_result_kind}",
        fault_type=terminal_result_kind,
        fault_rate=1.0,
    )
    fault_hook = paper_formal_runner._Exp3RuntimeHookBridge(
        condition=condition,
        case_id=str(case["case_id"]),
        fault_type=terminal_result_kind,
        selected_unit_ids=(f'{case["case_id"]}:{target_ai_unit_id}',),
        reserve_unit_ids=(),
        runtime_records=runtime_records,
    )
    transport = ScriptedLeanPaperProofTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / f"trace-{terminal_result_kind}",
        transport=transport,
        real_transport=False,
        checker=_use_recording_checker_for_adapter_regressions,
        post_raw_output_hook=fault_hook,
        trace_context=trace_context,
    )

    assert PaperTraceRuntimeContext.current_provider_call_count == 0
    assert transport.calls == []
    assert result.task_result.provider_attempt_count == 0
    assert result.task_result.root_status is PaperTaskStatus.COMPLETED
    assert len(runtime_records) == 1
    assert runtime_records[0]["fault_type"] == terminal_result_kind
    store = ArtifactStore(Path(result.output_root))
    target_events = [
        event
        for event in result.event_records
        if event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
        and event["payload"]["binding_digest"] == target_binding.binding_digest
    ]
    assert target_events[0]["payload"]["execution_result_kind"] == (
        terminal_result_kind
    )
    assert "execution_result_kind" not in target_events[1]["payload"]
    target_deliveries = [
        PreparedTraceDelivery.from_dict(
            json.loads(
                store.read_bytes(
                    ArtifactRef.from_dict(event["payload"]["current_wrapper_ref"])
                ).decode("utf-8")
            )
        )
        for event in target_events
    ]
    assert [delivery.attempt_ordinal for delivery in target_deliveries] == [0, 1]
    slot_candidate_ids = [
        "lean_trace_candidate_"
        f"{delivery.delivery_digest.removeprefix('sha256:')}"
        for delivery in target_deliveries
    ]
    checker_candidate_ids = {
        request.proof_candidate_ref.artifact_id
        for request in _use_recording_checker_for_adapter_regressions.requests
    }
    assert slot_candidate_ids[0] not in checker_candidate_ids
    assert slot_candidate_ids[1] in checker_candidate_ids
    records_by_logical_key: dict[str, list[dict]] = {}
    for record in result.child_results:
        records_by_logical_key.setdefault(
            str(record["child_logical_key"]), []
        ).append(record)
    recovered_records = next(
        records
        for records in records_by_logical_key.values()
        if len(records) == 2
    )
    assert recovered_records[0]["candidate_output_ref"] is None
    assert recovered_records[0]["checker"] == {
        "accepted": False,
        "failure_kind": None,
        "environment_digest": None,
        "report_ref": None,
        "proof_artifact_ref": None,
    }
    assert recovered_records[1]["candidate_output_ref"] is not None
    assert recovered_records[1]["checker"]["accepted"] is True
    assert recovered_records[1]["checker"]["report_ref"] is not None
    assert source.task_result.provider_attempt_count > 0
    assert source.task_result.total_tokens is not None
    assert source.task_result.total_tokens > 0
    staged_submission_ids_by_attempt: dict[str, str] = {}
    for event in result.event_records:
        if event["event_type"] != "TRACE_DELIVERY_COMMITTED.v1":
            continue
        delivery = PreparedTraceDelivery.from_dict(
            json.loads(
                store.read_bytes(
                    ArtifactRef.from_dict(event["payload"]["current_wrapper_ref"])
                ).decode("utf-8")
            )
        )
        stage_kind = (
            "terminal"
            if "execution_result_kind" in event["payload"]
            else "parser"
        )
        staged_submission_ids_by_attempt[delivery.attempt_id] = (
            f"trace_{stage_kind}_submission_"
            f"{delivery.delivery_digest.removeprefix('sha256:')}"
        )
    for attempt in result.attempt_results:
        assert attempt.usage_ref is not None
        usage_ref = ArtifactRef.from_dict(attempt.usage_ref)
        assert usage_ref.artifact_type == "TraceCurrentUsage"
        usage = json.loads(store.read_bytes(usage_ref).decode("utf-8"))
        request = json.loads(
            store.read_bytes(ArtifactRef.from_dict(attempt.request_ref)).decode(
                "utf-8"
            )
        )
        assert usage["attempt_id"] == attempt.attempt_id
        assert usage["request_id"] == request["request_id"]
        assert usage["submission_id"] == staged_submission_ids_by_attempt[
            attempt.attempt_id
        ]
        assert usage["provider_attempt_count"] == 0
        assert usage["current_provider_call_count"] == 0
        assert not {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_estimate",
        } & set(usage)
    assert len(staged_submission_ids_by_attempt) == len(result.attempt_results)
