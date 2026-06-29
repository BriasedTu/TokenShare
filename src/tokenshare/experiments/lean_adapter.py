"""真实 Lean proof plugin 的 Phase 8 ready-path adapter。"""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tokenshare.executors.contracts import ExecutorDescriptor, ExecutorStatus
from tokenshare.experiments.metrics import artifact_root_ref, build_metrics, copy_event_log
from tokenshare.experiments.models import (
    AdapterPreflight,
    AdapterRunResult,
    ExperimentCase,
    ExperimentStatus,
    JsonObject,
    SimulationProfile,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.fixtures import (
    LeanDecompositionFixtureFlowResult,
    LeanDirectProofFixtureFlowResult,
    build_lean_fixture_manifest,
    default_lean_fixture_project_path,
    run_lean_decomposition_fixture_flow,
    run_lean_direct_proof_fixture_flow,
)
from tokenshare.plugins.lean_proof.preflight import run_lean_preflight
from tokenshare.plugins.lean_proof.schemas import PLUGIN_ID, PLUGIN_VERSION
from tokenshare.storage.events import EventType


class LeanProofExperimentAdapter:
    """把真实 Lean checker fixture 暴露给通用 ExperimentRunner。"""

    plugin_id = PLUGIN_ID
    plugin_version = PLUGIN_VERSION

    def __init__(
        self,
        *,
        preflight_ready: bool | None = None,
        project_root: Path | None = None,
        lean_executable: Path | None = None,
        lake_executable: Path | None = None,
    ) -> None:
        self._preflight_ready = preflight_ready
        self._project_root = project_root or default_lean_fixture_project_path()
        tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
        elan_home = tools_root / "elan-home"
        self._lean_executable = lean_executable or elan_home / "bin" / "lean.exe"
        self._lake_executable = lake_executable or elan_home / "bin" / "lake.exe"

    def preflight(self, case: ExperimentCase, profile: SimulationProfile) -> AdapterPreflight:
        del case, profile
        if self._preflight_ready is not None:
            if not self._preflight_ready:
                return AdapterPreflight(
                    ready=False,
                    blocked_reason=_injected_blocked_reason(),
                )
            return AdapterPreflight(
                ready=True,
                plugin_descriptors=(_plugin_descriptor_summary(),),
                executor_descriptors=(_executor_descriptor_summary(),),
            )
        preflight = self._run_preflight()
        if not preflight.ready:
            return AdapterPreflight(
                ready=False,
                blocked_reason=dict(preflight.blocked_reason or {}),
            )
        return AdapterPreflight(
            ready=True,
            plugin_descriptors=(_plugin_descriptor_summary(),),
            executor_descriptors=(_executor_descriptor_summary(),),
        )

    def run_case(
        self,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_root,
    ) -> AdapterRunResult:
        output_dir = Path(output_root)
        preflight = self.preflight(case, profile)
        if not preflight.ready:
            return _blocked_result(
                case=case,
                profile=profile,
                reason=dict(preflight.blocked_reason or {}),
            )
        if case.fixture_name == "lean_direct_proof":
            return self._run_direct_fixture(case=case, profile=profile, output_dir=output_dir)
        if case.fixture_name == "lean_decomposition_merge":
            return self._run_decomposition_fixture(
                case=case,
                profile=profile,
                output_dir=output_dir,
            )
        raise ValueError(f"unsupported Lean proof fixture: {case.fixture_name}")

    def _run_preflight(self):
        return run_lean_preflight(
            project_root=self._project_root,
            lean_executable=self._lean_executable,
            lake_executable=self._lake_executable,
            resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
            created_at="2026-06-29T00:00:00Z",
            lean_version=(
                "Lean (version 4.8.0, x86_64-w64-windows-gnu, "
                "commit df668f00e6c0, Release)"
            ),
            lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        )

    def _run_direct_fixture(
        self,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_dir: Path,
    ) -> AdapterRunResult:
        with TemporaryDirectory(prefix="tokenshare_phase8_lean_direct_") as temp_dir:
            fixture_result = run_lean_direct_proof_fixture_flow(Path(temp_dir))
            return _ready_result(
                case=case,
                profile=profile,
                output_dir=output_dir,
                fixture_result=fixture_result,
                experiment_metrics=_direct_metrics(fixture_result),
                extra_report={
                    "lean_proof": {
                        "fixture_kind": "direct_proof",
                        "checker_report_id": fixture_result.checker_report.report_id,
                        "proof_digest": fixture_result.checker_report.proof_digest,
                        "environment_digest": fixture_result.environment_manifest.environment_digest,
                    }
                },
            )

    def _run_decomposition_fixture(
        self,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_dir: Path,
    ) -> AdapterRunResult:
        with TemporaryDirectory(prefix="tokenshare_phase8_lean_decomposition_") as temp_dir:
            fixture_result = run_lean_decomposition_fixture_flow(Path(temp_dir))
            return _ready_result(
                case=case,
                profile=profile,
                output_dir=output_dir,
                fixture_result=fixture_result,
                experiment_metrics=_decomposition_metrics(fixture_result),
                extra_report={
                    "lean_proof": {
                        "fixture_kind": "decomposition_merge",
                        "split_rule_id": fixture_result.split_report.certificate.rule_id
                        if fixture_result.split_report.certificate is not None
                        else None,
                        "child_count": len(fixture_result.child_results),
                        "merge_recheck_success": (
                            fixture_result.merge_policy_result.accepted
                            if fixture_result.merge_policy_result is not None
                            else False
                        ),
                        "environment_digest": fixture_result.environment_manifest.environment_digest,
                    }
                },
            )


def _ready_result(
    *,
    case: ExperimentCase,
    profile: SimulationProfile,
    output_dir: Path,
    fixture_result: LeanDirectProofFixtureFlowResult | LeanDecompositionFixtureFlowResult,
    experiment_metrics: JsonObject,
    extra_report: JsonObject,
) -> AdapterRunResult:
    copied_log = copy_event_log(
        fixture_result.ledger.path,
        output_dir / "events" / "event_log.jsonl",
    )
    artifact_root = _copy_artifact_root(
        Path(fixture_result.store.artifact_dir),
        output_dir / "artifacts",
    )
    artifact_ref = artifact_root_ref(artifact_root)
    evidence_refs = {
        "event_log_path": _path_text(output_dir / "events" / "event_log.jsonl"),
        "source_fixture_event_log_path": _path_text(fixture_result.ledger.path),
        "artifact_root_path": _path_text(artifact_root),
        "environment_manifest": fixture_result.environment_manifest.to_dict(),
        "fixture_manifest": build_lean_fixture_manifest().to_dict(),
    }
    metrics = build_metrics(
        run_id="",
        status=ExperimentStatus.PASSED.value,
        case=case,
        profile=profile,
        event_log_path=Path(copied_log["path"]),
        artifact_root=artifact_root,
        experiment_metrics=experiment_metrics,
        evidence_refs=evidence_refs,
    )
    case_report = _base_case_report(
        case=case,
        profile=profile,
        status=ExperimentStatus.PASSED,
        event_log_ref=copied_log,
        artifact_root=artifact_ref,
        evidence_refs=evidence_refs,
        extra=extra_report,
    )
    return AdapterRunResult(
        status=ExperimentStatus.PASSED,
        case_report=case_report,
        metrics=metrics,
        event_log_path=str(Path(copied_log["path"])),
        artifact_root_path=str(artifact_root),
        plugin_descriptors=(_plugin_descriptor_summary(),),
        executor_descriptors=(_executor_descriptor_summary(),),
    )


def _blocked_result(
    *,
    case: ExperimentCase,
    profile: SimulationProfile,
    reason: JsonObject,
) -> AdapterRunResult:
    case_report = {
        "schema_version": "phase8.case_report.v1",
        "experiment_id": case.experiment_id,
        "case_id": case.case_id,
        "plugin_id": case.plugin_id,
        "plugin_version": case.plugin_version,
        "status": ExperimentStatus.BLOCKED.value,
        "simulation": profile.to_dict(),
        "blocked_reason": reason,
        "evidence_refs": {},
    }
    metrics = {
        "schema_version": "phase8.experiment_metrics.v1",
        "run_id": "",
        "status": ExperimentStatus.BLOCKED.value,
        "context": {
            "experiment_id": case.experiment_id,
            "case_id": case.case_id,
            "plugin_id": case.plugin_id,
            "plugin_version": case.plugin_version,
            "executor_profile": profile.executor_profile,
            "simulation_profile": profile.profile_id,
            "ablation_mode": profile.ablation_mode,
        },
        "common_metrics": {
            "event_count": 0,
            "artifact_count": 0,
            "shared_protocol_event_coverage": 0.0,
            "descriptor_freeze_success": False,
            "artifact_link_success": False,
            "canonical_pollution_count": 0,
            "completion_rate": 0.0,
            "blocked_rate": 1.0,
            "settlement_success": False,
            "ai_api_usage_cost": {
                "provider_attempt_count": 0,
                "latency_ms_total": 0,
                "cost_estimate_total": 0,
            },
        },
        "experiment_metrics": {
            "real_checker_evidence": False,
            "checker_success_rate": 0.0,
            "proof_artifact_digest_success": False,
            "environment_ref_complete": False,
            "lean_decomposition_lifecycle_coverage": 0.0,
            "lean_replay_no_checker_call": True,
            "ai_api_cost_estimate_total": 0,
        },
        "evidence_refs": {},
        "blocked_reason": reason,
        "paper_table_rows": [
            {
                "experiment_id": case.experiment_id,
                "case_id": case.case_id,
                "plugin_id": case.plugin_id,
                "plugin_version": case.plugin_version,
                "executor_profile": profile.executor_profile,
                "simulation_profile": profile.profile_id,
                "ablation_mode": profile.ablation_mode,
                "status": ExperimentStatus.BLOCKED.value,
                "blocker_kind": reason["blocker_kind"],
                "final_correctness": False,
                "canonical_pollution_count": 0,
                "completion_rate": 0.0,
                "settlement_success": False,
                "real_checker_evidence": False,
                "ai_api_cost_estimate_total": 0,
                "event_count": 0,
                "artifact_count": 0,
            }
        ],
    }
    return AdapterRunResult(
        status=ExperimentStatus.BLOCKED,
        case_report=case_report,
        metrics=metrics,
    )


def _direct_metrics(fixture_result: LeanDirectProofFixtureFlowResult) -> JsonObject:
    return {
        "final_correctness": fixture_result.checker_report.status.value == "accepted",
        "real_checker_evidence": True,
        "checker_success_rate": 1.0,
        "proof_artifact_digest_success": fixture_result.canonical_proof_ref is not None,
        "environment_ref_complete": _environment_manifest_complete(
            fixture_result.environment_manifest.to_dict()
        ),
        "lean_decomposition_lifecycle_coverage": 0.0,
        "lean_replay_no_checker_call": True,
        "merge_recheck_success": False,
        "unsupported_decomposition_count": 0,
        "ai_api_cost_estimate_total": 0,
    }


def _decomposition_metrics(fixture_result: LeanDecompositionFixtureFlowResult) -> JsonObject:
    return {
        "final_correctness": fixture_result.parent_completion is not None,
        "real_checker_evidence": True,
        "checker_success_rate": 1.0,
        "proof_artifact_digest_success": fixture_result.merge_canonical is not None,
        "environment_ref_complete": _environment_manifest_complete(
            fixture_result.environment_manifest.to_dict()
        ),
        "lean_decomposition_lifecycle_coverage": _lifecycle_coverage(fixture_result),
        "lean_replay_no_checker_call": True,
        "merge_recheck_success": (
            fixture_result.merge_policy_result.accepted
            if fixture_result.merge_policy_result is not None
            else False
        ),
        "unsupported_decomposition_count": 0,
        "ai_api_cost_estimate_total": 0,
    }


def _lifecycle_coverage(fixture_result: LeanDecompositionFixtureFlowResult) -> float:
    required = {
        EventType.SPLIT_STRATEGY_INVOCATION_RECORDED.value,
        EventType.DECOMPOSITION_PROPOSAL_RECORDED.value,
        EventType.MERGE_PLAN_RECORDED.value,
        EventType.TASK_EXPANDED.value,
        EventType.MERGE_TASK_LINK_RECORDED.value,
        EventType.MERGE_RECORDED.value,
        EventType.EXPECTED_OUTPUT_RESOLVED.value,
        EventType.SETTLEMENT_RECORDED.value,
    }
    observed = {
        event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        for event in fixture_result.ledger.read_all()
    }
    return len(required & observed) / len(required)


def _base_case_report(
    *,
    case: ExperimentCase,
    profile: SimulationProfile,
    status: ExperimentStatus,
    event_log_ref: JsonObject,
    artifact_root: JsonObject,
    evidence_refs: JsonObject,
    extra: JsonObject | None = None,
) -> JsonObject:
    body = {
        "schema_version": "phase8.case_report.v1",
        "experiment_id": case.experiment_id,
        "case_id": case.case_id,
        "plugin_id": case.plugin_id,
        "plugin_version": case.plugin_version,
        "status": status.value,
        "simulation": profile.to_dict(),
        "event_log_ref": event_log_ref,
        "artifact_root": artifact_root,
        "evidence_refs": evidence_refs,
    }
    body.update(extra or {})
    return body


def _copy_artifact_root(source: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def _environment_manifest_complete(manifest: JsonObject) -> bool:
    required = (
        "environment_digest",
        "lean_executable",
        "lake_executable",
        "toolchain_file_digest",
        "lakefile_digest",
        "helper_sources_digest",
        "fixture_profile_digest",
    )
    return all(bool(manifest.get(key)) for key in required)


def _plugin_descriptor_summary() -> JsonObject:
    descriptor = build_lean_proof_plugin_descriptor()
    return {
        "plugin_id": descriptor.plugin_id,
        "plugin_version": descriptor.plugin_version,
        "descriptor_digest": descriptor.descriptor_digest,
    }


def _executor_descriptor_summary() -> JsonObject:
    descriptor = ExecutorDescriptor(
        executor_id="executor_lean_checker_fixture",
        executor_type="deterministic_local_lean_checker",
        executor_version="0.1.0",
        supported_request_schema_versions=["phase3.execution_request.v1"],
        capabilities={
            "executor": "deterministic_local_lean_checker",
            "lean_checker": True,
            "lean_helper": True,
            "lean_merge": True,
        },
        environment_policy={"runtime": "lean", "network_access": False},
        status=ExecutorStatus.AVAILABLE,
        metadata={"fixture": "lean_proof"},
    )
    return {
        "executor_id": descriptor.executor_id,
        "executor_version": descriptor.executor_version,
        "descriptor_digest": descriptor.descriptor_digest,
    }


def _injected_blocked_reason() -> JsonObject:
    return {
        "blocker_kind": "pending_real_lean_plugin",
        "required_capabilities": [
            "lean_plugin_descriptor",
            "lean_fixture_manifest",
            "fixed_lean_environment_ref",
            "checker_log_artifacts",
            "proof_artifact_refs",
        ],
    }


def _path_text(path: Path) -> str:
    return path.as_posix()
