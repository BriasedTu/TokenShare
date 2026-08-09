from __future__ import annotations

import json
from pathlib import Path

from tokenshare.core.models import (
    Attempt,
    AttemptState,
    Lease,
    LeaseState,
    ProtocolConfig,
)
from tokenshare.experiments.paper_catalog import (
    default_lean_paper_environment_manifest,
)
from tokenshare.executors.ai_api import prepare_ai_api_outbound_request
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.storage.artifacts import ArtifactStore

from tests.support.lean_checker import RecordingLeanChecker


CATALOG_PATH = Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
SIMPLE_CATALOG_PATH = Path("benchmarks/paper/lean_catalog.v1.jsonl")
NOW = "2026-07-14T00:00:00Z"


def test_fixed_plan_units_use_ai_candidate_executor_and_preserve_dag_dependencies(
    tmp_path: Path,
) -> None:
    case = _case("lean_v2_medium_lemma_dag_01")
    adapter = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=default_lean_paper_environment_manifest(),
        checker=RecordingLeanChecker(),
        protocol_config=ProtocolConfig.default(
            config_id="lean_runtime_unit_contract",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events.jsonl",
        ),
    )

    units = adapter.plan_units(case, artifact_store=ArtifactStore(tmp_path))
    split_plan = adapter.planned_split_plan

    assert [unit.metadata["child_logical_key"] for unit in units] == [
        node["node_id"] for node in case["lemma_graph"]["nodes"]
    ]
    assert all(unit.required_capabilities["executor"] == "ai_api" for unit in units)
    assert all(
        unit.required_capabilities["provider_family"] == "siliconflow"
        for unit in units
    )
    assert split_plan.certificate.rule_id == "lean_split.lemma_graph_dag.v2"
    assert [
        (edge["source_child_key"], edge["target_child_key"])
        for edge in split_plan.proposal.dependency_edges
    ] == [
        (edge["source_node_id"], edge["target_node_id"])
        for edge in case["dependency_edges"]
    ]


def test_simple_runtime_requests_use_stable_planned_child_ids(tmp_path: Path) -> None:
    case = _simple_case("lean_easy_01")
    adapter = LeanRuntimeAdapter(
        provider_family="siliconflow",
        environment_manifest=default_lean_paper_environment_manifest(),
        checker=RecordingLeanChecker(),
        created_at=NOW,
    )
    units = adapter.plan_units(case, artifact_store=ArtifactStore(tmp_path))

    planned_ids = []
    for index, unit in enumerate(units):
        attempt_id = f"attempt_{index}"
        lease_id = f"lease_{index}"
        request = adapter.build_execution_request(
            unit,
            attempt=Attempt(
                attempt_id=attempt_id,
                task_id=unit.task_id,
                unit_id=unit.unit_id,
                lease_id=lease_id,
                client_id="worker_lean_ai",
                state=AttemptState.RUNNING,
                attempt_kind="primary",
                created_at=NOW,
                started_at=NOW,
            ),
            lease=Lease(
                lease_id=lease_id,
                task_id=unit.task_id,
                unit_id=unit.unit_id,
                attempt_id=attempt_id,
                client_id="worker_lean_ai",
                state=LeaseState.ACTIVE,
                fencing_token=f"fence_{index}",
                issued_at=NOW,
                expires_at="2026-07-14T00:05:00Z",
                last_heartbeat_at=None,
                heartbeat_count=0,
                lease_kind="execution",
                terminated_at=None,
                terminated_reason=None,
                metadata={},
            ),
        )
        planned_ids.append(request.soft_hints["planned_ai_unit_id"])

    assert planned_ids == ["child_0", "child_1"]


def test_lemma_graph_planning_request_freezes_the_exact_runtime_outbound_body(
    tmp_path: Path,
) -> None:
    case = _case("lean_v2_medium_lemma_dag_01")
    store = ArtifactStore(tmp_path / "artifacts")
    adapter = LeanRuntimeAdapter(
        provider_family="deepseek",
        environment_manifest=default_lean_paper_environment_manifest(),
        checker=RecordingLeanChecker(),
        created_at=NOW,
        max_tokens=300000,
        timeout_seconds=600,
    )
    units = adapter.plan_units(case, artifact_store=store)
    unit = units[-1]
    attempt = Attempt(
        attempt_id="attempt_planning_equivalence",
        task_id=unit.task_id,
        unit_id=unit.unit_id,
        lease_id="lease_planning_equivalence",
        client_id="worker_lean_ai",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at=NOW,
        started_at=NOW,
    )
    lease = Lease(
        lease_id="lease_planning_equivalence",
        task_id=unit.task_id,
        unit_id=unit.unit_id,
        attempt_id=attempt.attempt_id,
        client_id="worker_lean_ai",
        state=LeaseState.ACTIVE,
        fencing_token="fence_planning_equivalence",
        issued_at=NOW,
        expires_at="2026-07-14T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="execution",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )

    planning_request = adapter.build_planning_execution_request(
        unit,
        attempt=attempt,
        lease=lease,
    )
    for source_unit in units[:-1]:
        logical_key = str(source_unit.metadata["child_logical_key"])
        canonical_ref = store.save_json(
            {"logical_key": logical_key, "proof": "by simp"},
            artifact_id=f"canonical_{logical_key}",
            artifact_type="LeanProofArtifact",
            artifact_schema_id="tokenshare.lean_proof_artifact",
            artifact_schema_version="v1",
            source={"kind": "test"},
            metadata={},
            created_at=NOW,
        )
        adapter.ingest_process_proof_state(
            {
                "request_id": f"accepted_{logical_key}",
                "logical_key": logical_key,
                "checker_report": None,
                "proof_input": None,
                "canonical_proof_ref": canonical_ref,
            }
        )
    runtime_attempt = Attempt(
        attempt_id="attempt_7d18bcb2d17f4b3ca5acdd83c0a00002",
        task_id=unit.task_id,
        unit_id=unit.unit_id,
        lease_id="lease_b4f610927b694df5a0f877aff0800002",
        client_id="worker_lean_ai",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at=NOW,
        started_at=NOW,
    )
    runtime_lease = Lease(
        lease_id="lease_b4f610927b694df5a0f877aff0800002",
        task_id=unit.task_id,
        unit_id=unit.unit_id,
        attempt_id=runtime_attempt.attempt_id,
        client_id="worker_lean_ai",
        state=LeaseState.ACTIVE,
        fencing_token="fence_runtime_equivalence",
        issued_at=NOW,
        expires_at="2026-07-14T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="execution",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )
    runtime_request = adapter.build_execution_request(
        unit,
        attempt=runtime_attempt,
        lease=runtime_lease,
    )
    config = load_ai_api_config(
        json.loads(
            Path(
                "benchmarks/paper/exp1_baseline_provider_config.v3.json"
            ).read_text(encoding="utf-8")
        )
    )

    def prepare(request):
        prompt = json.loads(store.read_bytes(request.prompt_package_ref))
        return prepare_ai_api_outbound_request(
            config=config,
            request=request,
            prompt=prompt,
            entry=config.entries[0],
        )

    planned = prepare(planning_request)
    runtime = prepare(runtime_request)

    assert planned.prepared_request.body_bytes == runtime.prepared_request.body_bytes
    assert planned.prepared_request.body_digest == runtime.prepared_request.body_digest
    assert (
        planned.prepared_request.inference_request_digest
        == runtime.prepared_request.inference_request_digest
    )
    assert planned.provider_request_identity == runtime.provider_request_identity
    assert planning_request.request_id != runtime_request.request_id
    assert planning_request.input_artifact_refs.keys() < (
        runtime_request.input_artifact_refs.keys()
    )


def _case(case_id: str) -> dict:
    return next(
        json.loads(line)
        for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["case_id"] == case_id
    )


def _simple_case(case_id: str) -> dict:
    return next(
        json.loads(line)
        for line in SIMPLE_CATALOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["case_id"] == case_id
    )
