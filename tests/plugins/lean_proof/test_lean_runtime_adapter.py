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
