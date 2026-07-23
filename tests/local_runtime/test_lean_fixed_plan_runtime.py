from __future__ import annotations

import json
from pathlib import Path

from tokenshare.core.models import ProtocolConfig
from tokenshare.executors.contracts import ExecutionSubmission
from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
)
from tokenshare.local_runtime import (
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    SequentialWorkerBackend,
)
from tokenshare.plugins.lean_proof.checker import LeanCheckerMode
from tokenshare.plugins.lean_proof.prompt_builder import PROOF_CANDIDATE_OUTPUT_NAME
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanExecutionBridge,
    LeanRuntimeAdapter,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.support.lean_checker import RecordingLeanChecker


CATALOG_PATH = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
SIMPLE_CATALOG_PATH = Path("benchmarks/paper/lean_catalog.v1.jsonl")
NOW = "2026-07-22T00:00:00Z"


class _ScriptedProofCandidateExecutor:
    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self.requests = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.requests.append(request)
        payload_ref = request.input_artifact_refs.get(
            "lemma_theorem_payload",
            request.input_artifact_refs.get("child_theorem_payload"),
        )
        payload = json.loads(self.store.read_bytes(payload_ref).decode("utf-8"))
        candidate_ref = self.store.save_json(
            {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": f"candidate:{submission_id}",
                "theorem_payload_digest": payload["payload_digest"],
                "proof_source": "by\n  trivial",
                "created_at": submitted_at,
            },
            artifact_id=f"candidate_{submission_id}",
            artifact_type="LeanProofCandidate",
            artifact_schema_id="lean_proof.proof_candidate",
            artifact_schema_version="v1",
            source={"kind": "scripted_runtime_test"},
            metadata={"output_name": PROOF_CANDIDATE_OUTPUT_NAME},
            created_at=submitted_at,
        )
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=str(request.executor["executor_id"]),
            executor_version=str(request.executor["executor_version"]),
            result_kind="succeeded",
            raw_output_ref=candidate_ref,
            parsed_output_ref=candidate_ref,
            candidate_output_refs={PROOF_CANDIDATE_OUTPUT_NAME: candidate_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "scripted_runtime_test"},
            provenance_ref=None,
            usage_summary={"provider_attempt_count": 1},
            error=None,
            submitted_at=submitted_at,
        )


def test_fixed_lemma_dag_runs_through_coordinator_checker_canonical_merge_and_root_recheck(
    tmp_path: Path,
) -> None:
    case = _case("lean_v2_medium_lemma_dag_01")
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "lean_runtime.jsonl")
    checker = RecordingLeanChecker()
    config = ProtocolConfig.default(
        config_id="lean_fixed_plan_runtime",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/lean_runtime.jsonl",
    )
    adapter = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=default_lean_paper_environment_manifest(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    candidate_executor = _ScriptedProofCandidateExecutor(store)

    result = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: NOW,
    ).run_root(
        ProtocolRunRequest(
            run_id="lean_fixed_plan_runtime",
            root_input=case,
            plugin_runtime=adapter,
            worker_backend=SequentialWorkerBackend(
                executor=LeanExecutionBridge(
                    plugin_runtime=adapter,
                    proof_candidate_executor=candidate_executor,
                ),
                submitted_at=lambda: NOW,
            ),
        )
    )

    assert result.status == "completed"
    assert len(candidate_executor.requests) == case["expected_ai_unit_count"]
    assert checker.modes.count(LeanCheckerMode.CHILD_PROOF) == case[
        "expected_ai_unit_count"
    ]
    assert checker.modes[-1] == LeanCheckerMode.MERGE_PROOF
    assert adapter.merge_result.accepted is True

    dependency_targets = {
        edge["target_node_id"] for edge in case["dependency_edges"]
    }
    requested_dependency_targets = {
        str(request.soft_hints["lemma_node_id"])
        for request in candidate_executor.requests
        if any(name.startswith("dependency:") for name in request.input_artifact_refs)
    }
    assert requested_dependency_targets == dependency_targets

    events = ledger.read_all()
    node_units = {
        event.payload["task_unit"]["unit_id"]
        for event in events
        if event.event_type == EventType.TASK_UNIT_CREATED
        and event.payload["task_unit"]["unit_type"] == "lean_proof_lemma_node"
    }
    assert len(node_units) == case["expected_ai_unit_count"]
    dependency_activation_events = [
        event
        for event in events
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.payload["task_unit_state_change"]["trigger"]
        == "dependency_resolution"
    ]
    assert len(dependency_activation_events) == len(dependency_targets)
    assert all(
        event.payload["task_unit_state_change"]["state_context"][
            "dependency_source_unit_ids"
        ]
        and event.payload["task_unit_state_change"]["state_context"][
            "dependency_output_refs"
        ]
        for event in dependency_activation_events
    )
    for unit_id in node_units:
        assert any(
            event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
            and event.payload.get("unit_id") == unit_id
            and "lean_proof_artifact" in event.payload["canonical_output_refs"]
            for event in events
        )
    assert any(event.event_type == EventType.MERGE_RECORDED for event in events)
    assert any(event.event_type == EventType.SETTLEMENT_RECORDED for event in events)


def test_simple_split_case_uses_the_same_coordinator_and_checker_merge(
    tmp_path: Path,
) -> None:
    case = json.loads(
        SIMPLE_CATALOG_PATH.read_text(encoding="utf-8").splitlines()[0]
    )
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "lean_simple_runtime.jsonl")
    checker = RecordingLeanChecker()
    config = ProtocolConfig.default(
        config_id="lean_simple_runtime",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events/lean_simple_runtime.jsonl",
    )
    adapter = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=default_lean_paper_environment_manifest(),
        checker=checker,
        protocol_config=config,
        created_at=NOW,
    )
    candidate_executor = _ScriptedProofCandidateExecutor(store)

    result = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: NOW,
    ).run_root(
        ProtocolRunRequest(
            run_id="lean_simple_runtime",
            root_input=case,
            plugin_runtime=adapter,
            worker_backend=SequentialWorkerBackend(
                executor=LeanExecutionBridge(
                    plugin_runtime=adapter,
                    proof_candidate_executor=candidate_executor,
                ),
                submitted_at=lambda: NOW,
            ),
        )
    )

    assert result.status == "completed"
    assert len(candidate_executor.requests) == case["expected_child_count"]
    assert checker.modes.count(LeanCheckerMode.CHILD_PROOF) == case[
        "expected_child_count"
    ]
    assert checker.modes[-1] == LeanCheckerMode.MERGE_PROOF
    assert all(
        request.task_unit_snapshot["unit_type"] == "lean_proof_subgoal"
        for request in candidate_executor.requests
    )


def _case(case_id: str) -> dict:
    for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if case["case_id"] == case_id:
                return case
    raise AssertionError(case_id)
