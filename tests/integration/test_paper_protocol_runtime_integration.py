from __future__ import annotations

import json
import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Lock

import pytest

import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter_module
import tokenshare.experiments.paper_catalog as paper_catalog_module

from tests.local_runtime.test_coordinator_full_lifecycle import (
    _ArtifactExecutor,
    _ExpandedPluginRuntime,
)
from tests.local_runtime.test_runtime_boundaries import (
    _protocol_state_write_violations_from_source,
    _python_files,
    _relative_module,
)
from tests.local_runtime.test_submission_and_recovery import (
    _RejectFirstSubmissionExecutor,
    _RejectFirstVerificationPlugin,
    _runtime as recovery_runtime,
)
from tests.local_runtime.test_worker_death_recovery import (
    _runtime as worker_runtime,
)
from tests.support.lean_checker import RecordingLeanChecker
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
)
from tokenshare.experiments.paper_budget import load_exp1_pilot_profile
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
from tokenshare.experiments.paper_factorization_catalog import (
    generate_factorization_paper_cases,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperTaskStatus,
)
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ParsedCandidateDirective,
    ProcessWorkerBackend,
    ProtocolRunRequest,
    SequentialWorkerBackend,
)
from tokenshare.core.models import ArtifactRef
from tokenshare.storage.events import EventType


REPO_ROOT = Path(__file__).resolve().parents[2]
FACTOR_CATALOG = REPO_ROOT / "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = REPO_ROOT / "benchmarks/paper/lean_catalog.v1.jsonl"
LEAN_GRAPH_CATALOG = (
    REPO_ROOT / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)
EXP1_PROFILE = REPO_ROOT / "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"


@pytest.fixture(autouse=True)
def _use_tracked_lean_catalog_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = REPO_ROOT
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
    monkeypatch.setattr(
        lean_paper_adapter_module,
        "default_lean_paper_environment_manifest",
        lambda: environment_manifest,
    )

FULL_LIFECYCLE_EVENTS = {
    EventType.TASK_REGISTERED.value,
    EventType.TASK_UNIT_CREATED.value,
    EventType.LEASE_STATE_CHANGED.value,
    EventType.ATTEMPT_STATE_CHANGED.value,
    EventType.EXECUTION_REQUEST_RECORDED.value,
    EventType.EXECUTION_SUBMISSION_RECORDED.value,
    EventType.VERIFICATION_RECORDED.value,
    EventType.CANONICAL_OUTPUTS_BOUND.value,
    EventType.TASK_EXPANDED.value,
    EventType.MERGE_RECORDED.value,
    EventType.CONTRIBUTION_STATE_CHANGED.value,
    EventType.SETTLEMENT_RECORDED.value,
}


class _CapturingFactorizationTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self) -> None:
        self._delegate = ScriptedFactorizationRangeTransport()
        self.calls: list[dict] = []

    def post_chat_completion(self, *, entry, api_key, body, timeout_seconds):
        response = self._delegate.post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body["model"] = entry.model
        self.calls.append(json.loads(json.dumps(body)))
        return response


class _CapturingLeanTransport:
    tokenshare_offline_capturing_transport = True

    def __init__(self, *, proof_sources_by_statement: dict[str, str]) -> None:
        self._delegate = ScriptedLeanPaperProofTransport(
            proof_sources_by_statement=proof_sources_by_statement,
        )
        self.calls: list[dict] = []

    def post_chat_completion(self, *, entry, api_key, body, timeout_seconds):
        response = self._delegate.post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body["model"] = entry.model
        self.calls.append(json.loads(json.dumps(body)))
        return response


def test_factorization_full_capturing_transport_covers_system_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(EXP1_PROFILE)
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "task9-offline-capture-key")
    case = next(
        item
        for item in generate_factorization_paper_cases()
        if item["difficulty"] == "easy"
        and item["factor_position_quantile"] != "no_factor"
    )
    condition = _condition_for_case(
        case=case,
        domain="factorization",
        catalog_digest="sha256:" + "1" * 64,
        profile=profile,
    )
    transport = _CapturingFactorizationTransport()

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=str(tmp_path),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=512,
        timeout_seconds=30,
    )

    coverage = result.run_evidence["protocol_runtime"]["lifecycle_coverage"]
    present = set(coverage["present_event_types"])
    assert coverage["complete"] is True
    assert FULL_LIFECYCLE_EVENTS <= present
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_eligible is False
    assert result.run_evidence["secret_scan_report"]["status"] == "passed"
    assert transport.calls
    assert all(
        attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
        for attempt in result.attempt_results
    )
    assert _only_offline_transport_blocks_paper_eligibility(
        result.eligibility_report.ineligibility_reasons
    )
    with pytest.raises(
        ValueError,
        match="Factorization paper dispatch does not accept a Lean checker",
    ):
        dispatch_paper_case(
            case=case,
            condition=condition,
            output_root=str(tmp_path / "invalid-checker"),
            transport=transport,
            real_transport=True,
            ai_api_config=profile.source_provider_config,
            entry_id=entry.entry_id,
            max_tokens=512,
            timeout_seconds=30,
            checker=object(),
        )


@pytest.mark.parametrize(
    ("case_id", "target_n", "candidate_end", "oracle_prime_factors", "expected_calls"),
    (
        (
            "factor_exp2_early_witness",
            "3027",
            "55",
            (
                {"prime": "3", "exponent": 1},
                {"prime": "1009", "exponent": 1},
            ),
            3,
        ),
        (
            "factor_exp2_no_factor",
            "104729",
            "323",
            ({"prime": "104729", "exponent": 1},),
            20,
        ),
    ),
)
def test_exp2_20way_factorization_stops_before_the_next_worker_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    target_n: str,
    candidate_end: str,
    oracle_prime_factors: tuple[dict[str, object], ...],
    expected_calls: int,
) -> None:
    profile = load_exp1_pilot_profile(EXP1_PROFILE)
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "task4-offline-capture-key")
    template = next(
        item
        for item in generate_factorization_paper_cases()
        if item["difficulty"] == "hard"
    )
    case = {
        **template,
        "case_id": case_id,
        "target_n": target_n,
        "candidate_start": "2",
        "candidate_end": candidate_end,
        "factor_position_quantile": (
            "no_factor" if len(oracle_prime_factors) == 1 else "early"
        ),
        "oracle_prime_factors": [dict(item) for item in oracle_prime_factors],
        "split_params": {
            "strategy_id": "factorization.candidate_range_partition.v1",
            "range_policy": "contiguous",
            "split_profile_id": "factorization.exp2_contiguous_20way.v1",
        },
    }
    condition = replace(
        _condition_for_case(
            case=case,
            domain="factorization",
            catalog_digest="sha256:" + "2" * 64,
            profile=profile,
        ),
        experiment_id="exp2_real_ai_scalability",
        condition_id=f"task4_{case_id}_w3_r0",
        worker_count=3,
    )
    transport = _CapturingFactorizationTransport()

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=str(tmp_path / case_id),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=512,
        timeout_seconds=30,
    )

    observation = result.run_evidence["protocol_runtime"]["runtime_observation"]
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.split_summary["range_child_count"] == 20
    assert len(transport.calls) == expected_calls
    assert len(observation["planned_ai_unit_ids"]) == 20
    assert len(observation["dispatched_ai_unit_ids"]) == expected_calls
    assert len(observation["completed_ai_unit_ids"]) == expected_calls
    assert len(observation["unscheduled_ai_unit_ids"]) == 20 - expected_calls
    assert observation["observed_peak_concurrency"] <= 3
    if expected_calls == 3:
        assert observation["in_flight_ai_unit_ids_at_witness"] == []
        assert observation["witness_observed_at"] is not None
    else:
        assert observation["in_flight_ai_unit_ids_at_witness"] == []


def test_lean_fixed_plan_full_is_plugin_validated_and_engine_merged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_exp1_pilot_profile(EXP1_PROFILE)
    entry = profile.source_provider_config.entries[0]
    monkeypatch.setenv(entry.api_key_env, "task9-offline-lean-capture-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
        lean_lemma_graph_path=LEAN_GRAPH_CATALOG,
    )
    case = next(
        item
        for item in catalog.cases_for(domain="lean_proof")
        if item["case_id"] == "lean_v2_medium_lemma_dag_01"
    )
    condition = _condition_for_case(
        case=case,
        domain="lean_proof",
        catalog_digest=catalog.catalog_digest,
        profile=profile,
    )
    checker = RecordingLeanChecker()
    transport = _CapturingLeanTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
    )

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=str(tmp_path),
        transport=transport,
        real_transport=True,
        ai_api_config=profile.source_provider_config,
        entry_id=entry.entry_id,
        max_tokens=1024,
        timeout_seconds=30,
        checker=checker,
    )

    coverage = result.run_evidence["protocol_runtime"]["lifecycle_coverage"]
    metadata = result.run_evidence["lean_lemma_graph"]
    assert coverage["complete"] is True
    assert FULL_LIFECYCLE_EVENTS <= set(coverage["present_event_types"])
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert metadata["certificate_ref"]
    assert metadata["certificate_digest"].startswith("sha256:")
    assert len(result.child_results) == case["expected_ai_unit_count"]
    assert len(checker.modes) == case["expected_ai_unit_count"] + 1
    assert result.merge_summary["root_checker_accepted"] is True
    assert _only_offline_transport_blocks_paper_eligibility(
        tuple(
            reason
            for reason in result.eligibility_report.ineligibility_reasons
            if reason != "non_production_checker_backend"
        )
    )


def test_verifier_rejection_and_late_submission_requeue_without_old_canonical(
    tmp_path: Path,
) -> None:
    for failure_kind, plugin_type in (
        ("verification_rejected", _RejectFirstVerificationPlugin),
        ("expired", None),
    ):
        root = tmp_path / failure_kind
        store, ledger, plugin, clock, coordinator = recovery_runtime(
            root,
            max_retries=2,
            plugin_type=plugin_type or _ExpandedPluginRuntime,
        )
        delegate = _ArtifactExecutor(store)
        executor = (
            delegate
            if failure_kind == "verification_rejected"
            else _RejectFirstSubmissionExecutor(delegate, rejection="expired")
        )
        result = coordinator.run_root(
            ProtocolRunRequest(
                run_id=f"task9_{failure_kind}",
                root_input={"failure": failure_kind},
                plugin_runtime=plugin,
                worker_backend=SequentialWorkerBackend(
                    executor=executor,
                    submitted_at=clock,
                ),
            )
        )

        events = ledger.read_all()
        recovery = next(
            event.payload["recovery_action"]
            for event in events
            if event.event_type == EventType.RECOVERY_ACTION_RECORDED
        )
        old_attempt_id = recovery["attempt_id"]
        canonical_attempt_ids = {
            event.payload["selected_attempt_id"]
            for event in events
            if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        }
        same_unit_attempts = {
            event.payload["attempt"]["attempt_id"]
            for event in events
            if event.event_type == EventType.ATTEMPT_STATE_CHANGED
            and event.payload.get("attempt", {}).get("unit_id")
            == recovery["unit_id"]
        }
        assert result.status == "completed"
        assert recovery["retry_allowed"] is True
        assert len(same_unit_attempts) == 2
        assert old_attempt_id not in canonical_attempt_ids
        assert canonical_attempt_ids & (same_unit_attempts - {old_attempt_id})


def test_parsed_candidate_hook_runs_before_submission_and_verification(
    tmp_path: Path,
) -> None:
    store, ledger, plugin, clock, coordinator = recovery_runtime(
        tmp_path,
        max_retries=0,
        plugin_type=_ExpandedPluginRuntime,
    )
    hooks = _ParsedCandidateOrderingHook(store=store, ledger=ledger)

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="task5_parsed_candidate_boundary",
            root_input={"fault": "parsed_candidate"},
            plugin_runtime=plugin,
            worker_backend=SequentialWorkerBackend(
                executor=_ArtifactExecutor(store),
                submitted_at=clock,
            ),
            hooks=hooks,
        )
    )

    assert result.status == "completed"
    assert hooks.observed_attempt_ids
    submission_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED.value
        and event.payload.get("acceptance_status") == "accepted"
    ]
    assert len(submission_events) == len(hooks.observed_attempt_ids)
    for event in submission_events:
        submission_ref = ArtifactRef.from_dict(event.payload["submission_ref"])
        submission = json.loads(store.read_bytes(submission_ref).decode("utf-8"))
        assert {
            ref["artifact_id"]
            for ref in submission["candidate_output_refs"].values()
        } == {f"parsed_candidate_hook_{event.payload['attempt_id']}"}


def test_real_os_worker_death_expires_lease_and_reassigns_same_unit(
    tmp_path: Path,
) -> None:
    store, ledger, plugin, clock, coordinator = worker_runtime(tmp_path)
    coordinator_pid = os.getpid()
    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=2,
        submitted_at=clock,
        terminate_once=lambda request: (
            request.unit_id != "unit_ready"
            and request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        ),
        kill_point="progress_25",
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="task9_process_death",
            root_input={"mode": "process_death"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
        )
    )

    killed = [
        fact for fact in backend.execution_facts
        if fact.result_kind == "worker_terminated"
    ]
    assert result.status == "completed"
    assert os.getpid() == coordinator_pid
    assert len(killed) == 1
    assert killed[0].worker_pid not in (None, coordinator_pid)
    assert killed[0].process_exitcode not in (None, 0)
    recovery = next(
        event.payload["recovery_action"]
        for event in ledger.read_all()
        if event.event_type == EventType.RECOVERY_ACTION_RECORDED
        and event.payload["recovery_action"]["trigger"] == "lease_expired"
    )
    active_leases = [
        event.payload["lease"]
        for event in ledger.read_all()
        if event.event_type == EventType.LEASE_STATE_CHANGED
        and event.payload.get("new_state") == "Active"
        and event.payload.get("lease", {}).get("unit_id") == recovery["unit_id"]
    ]
    assert len(active_leases) == 2
    assert active_leases[0]["lease_id"] != active_leases[1]["lease_id"]
    assert active_leases[0]["attempt_id"] != active_leases[1]["attempt_id"]
    assert active_leases[0]["fencing_token"] != active_leases[1]["fencing_token"]


@pytest.mark.parametrize(
    "mode",
    (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    ),
)
def test_five_ablation_modes_are_observed_from_runtime_gates(
    tmp_path: Path,
    mode: str,
) -> None:
    case = next(
        item
        for item in generate_factorization_paper_cases()
        if item["difficulty"] == "easy"
        and item["factor_position_quantile"] != "no_factor"
    )
    condition = PaperExperimentCondition(
        experiment_id="exp4_real_ai_protocol_ablation",
        condition_id=f"task9_ablation_{mode.lower()}",
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=2,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode=mode,
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "4" * 64,
    )
    transport = ScriptedFactorizationRangeTransport(
        force_false_negative_child_indices=(
            {0} if mode in {"NO_VERIFICATION", "NO_MERGE_GATE"} else set()
        )
    )
    post_raw_hook = (
        _ExecutorErrorOncePerUnit()
        if mode in {"FULL", "NO_REQUEUE"}
        else None
    )

    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=str(tmp_path / mode),
        transport=transport,
        real_transport=False,
        ai_api_config=None,
        entry_id="factorization_paper_scripted",
        max_tokens=512,
        timeout_seconds=30,
        post_raw_output_hook=post_raw_hook,
        ablation_mode=mode,
    )

    runtime = result.run_evidence["ablation_runtime"]
    assert runtime["mode"] == mode
    assert runtime["max_retries"] == 1
    assert runtime["applied_before_adapter_completion"] is True
    if mode == "FULL":
        assert runtime["disabled_mechanism"] is None
        assert runtime["hook_observations"] == []
        assert result.task_result.root_status == PaperTaskStatus.COMPLETED
        assert len(
            {attempt.unit_id for attempt in result.attempt_results}
        ) < len(result.attempt_results)
    else:
        assert runtime["disabled_mechanism"] is not None
        assert runtime["hook_observations"]
        assert all(
            observation["kind"]
            in {
                "EXPERIMENT_ABLATION_GATE_APPLIED",
                "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED",
            }
            for observation in runtime["hook_observations"]
        )
        if mode in {"NO_REQUEUE", "NO_MERGE_GATE"}:
            assert all(
                observation["payload"]["protocol_event_refs"]
                for observation in runtime["hook_observations"]
                if observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
            )
    if mode == "NO_VERIFICATION":
        assert result.task_result.accepted_validity is False
        assert runtime["attempt_observations"]
        assert any(
            observation["canonical_output_refs"]
            and observation["independent_candidate_validity"] is False
            for observation in runtime["attempt_observations"]
        )
    if mode == "NO_PARSER_POLICY":
        assert runtime["attempt_observations"]
        assert all(
            observation["raw_output_ref"] == observation["candidate_output_ref"]
            for observation in runtime["attempt_observations"]
        )
        assert all(
            observation["final_validity"]
            == result.task_result.accepted_validity
            for observation in runtime["attempt_observations"]
        )
    if mode == "NO_REQUEUE":
        assert result.task_result.root_status != PaperTaskStatus.COMPLETED
        assert runtime["max_retries"] == 1
        assert len(
            {attempt.unit_id for attempt in result.attempt_results}
        ) == len(result.attempt_results)
    if mode == "NO_MERGE_GATE":
        premature = [
            observation
            for observation in runtime["hook_observations"]
            if observation["kind"]
            == "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED"
        ]
        assert premature
        assert premature[0]["payload"]["attempt_status"] == "executed"
        assert premature[0]["payload"]["root_check_passed"] is False
        assert premature[0]["payload"]["result_artifact_ref"][
            "content_hash"
        ].startswith(
            "sha256:"
        )


def test_experiments_package_has_no_direct_protocol_state_write_authority() -> None:
    violations: list[str] = []
    for path in _python_files("experiments"):
        violations.extend(
            f"{path.name}:{violation}"
            for violation in _protocol_state_write_violations_from_source(
                path.read_text(encoding="utf-8"),
                module_name=_relative_module(path),
            )
        )
    assert violations == []


def _condition_for_case(*, case, domain, catalog_digest, profile):
    identity = profile.model_endpoint_identity
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"task9_{domain}_{case['case_id']}",
        domain=domain,
        difficulty=str(case["difficulty"]),
        paper_difficulty=case.get("paper_difficulty", case["difficulty"]),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
        worker_count=2,
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


def _oracle_sources_by_statement(case: dict) -> dict[str, str]:
    proof_sources = case["oracle_proof_package_ref"]["node_proof_sources"]
    return {
        node["theorem_payload"]["statement_source"]: proof_sources[node["node_id"]]
        for node in case["lemma_graph"]["nodes"]
    }


def _only_offline_transport_blocks_paper_eligibility(
    reasons: tuple[str, ...],
) -> bool:
    allowed_exact = {
        "real_transport_required",
        "transport_config_source_not_local_gitignored",
        "incomplete_attempt_evidence",
    }
    return bool(reasons) and all(
        reason in allowed_exact
        or reason.startswith("unsupported_transport:")
        or reason.startswith("attempt_not_real_transport")
        or reason.startswith("model_execution_record_not_paper_eligible")
        for reason in reasons
    )


class _ExecutorErrorOncePerUnit:
    def __init__(self) -> None:
        self._seen_unit_ids: set[str] = set()
        self._lock = Lock()

    def __call__(self, **context):
        unit_id = str(context["request"].unit_id)
        with self._lock:
            if unit_id in self._seen_unit_ids:
                return None
            self._seen_unit_ids.add(unit_id)
        return {"result_kind": "executor_error"}


class _ParsedCandidateOrderingHook(NoOpRuntimeHooks):
    def __init__(self, *, store, ledger) -> None:
        self._store = store
        self._ledger = ledger
        self.observed_attempt_ids: list[str] = []

    def after_parsed_candidate_persisted(self, context):
        prior_attempt_events = [
            event
            for event in self._ledger.read_all()
            if event.payload.get("attempt_id") == context.attempt_id
            and event.event_type
            in {
                EventType.EXECUTION_SUBMISSION_RECORDED.value,
                EventType.VERIFICATION_RECORDED.value,
            }
        ]
        assert prior_attempt_events == []
        replacement_ref = self._store.save_json(
            {"unit_id": context.unit_id, "answer": "fault-mutated"},
            artifact_id=f"parsed_candidate_hook_{context.attempt_id}",
            artifact_type="canonical_output",
            artifact_schema_id="tokenshare.runtime_test.answer",
            artifact_schema_version="v1",
            source={"kind": "parsed_candidate_ordering_hook"},
            metadata={"attempt_id": context.attempt_id},
            created_at=context.submitted_at,
        )
        self.observed_attempt_ids.append(context.attempt_id)
        return ParsedCandidateDirective(
            replacement_candidate_output_refs={
                name: replacement_ref
                for name in context.candidate_output_refs
            },
        )
