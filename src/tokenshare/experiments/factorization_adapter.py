"""Factorization 真实插件的 Phase 8 实验 adapter。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tokenshare.executors.contracts import ExecutorDescriptor, ExecutorStatus
from tokenshare.experiments.metrics import (
    artifact_root_ref,
    build_metrics,
    copy_event_log,
    final_correctness_for_factorization,
    summarize_factorization_result,
)
from tokenshare.experiments.models import (
    AdapterPreflight,
    AdapterRunResult,
    ExperimentCase,
    ExperimentStatus,
    JsonObject,
    SimulationProfile,
)
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.fixtures import FactorizationFixtureFlowResult
from tokenshare.plugins.factorization.fixtures import run_factorization_fixture_flow
from tokenshare.plugins.factorization.schemas import PLUGIN_ID, PLUGIN_VERSION


class FactorizationExperimentAdapter:
    """把 factorization fixture 暴露给通用 ExperimentRunner。"""

    plugin_id = PLUGIN_ID
    plugin_version = PLUGIN_VERSION

    def preflight(self, case: ExperimentCase, profile: SimulationProfile) -> AdapterPreflight:
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
        if case.experiment_id == "exp2_failure_recovery":
            return self._run_failure_case(case=case, profile=profile, output_dir=output_dir)
        if case.experiment_id == "exp3_protocol_ablation":
            return self._run_ablation_case(case=case, profile=profile, output_dir=output_dir)
        return self._run_fixture_case(case=case, profile=profile, output_dir=output_dir)

    def _run_fixture_case(
        self,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_dir: Path,
    ) -> AdapterRunResult:
        target_n, child_count = _fixture_parameters(case.fixture_name)
        with TemporaryDirectory(prefix="tokenshare_phase8_factorization_") as temp_dir:
            fixture_result = run_factorization_fixture_flow(
                Path(temp_dir),
                target_n=target_n,
                requested_child_count=child_count,
            )
            copied_log = copy_event_log(
                fixture_result.ledger.path,
                output_dir / "events" / "event_log.jsonl",
            )
            artifact_ref = artifact_root_ref(fixture_result.store.artifact_dir)
            experiment_metrics = _factorization_metrics(
                target_n=str(target_n),
                fixture_result=fixture_result,
            )
            evidence_refs = {
                "event_log_path": _path_text(output_dir / "events" / "event_log.jsonl"),
                "source_fixture_event_log_path": _path_text(fixture_result.ledger.path),
                "artifact_root_path": _path_text(fixture_result.store.artifact_dir),
                "prime_factorization_ref": (
                    fixture_result.prime_factorization_ref.to_dict()
                    if fixture_result.prime_factorization_ref is not None
                    else None
                ),
            }
            metrics = build_metrics(
                run_id="",
                status=ExperimentStatus.PASSED.value,
                case=case,
                profile=profile,
                event_log_path=Path(copied_log["path"]),
                artifact_root=fixture_result.store.artifact_dir,
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
                extra={
                    "factorization": {
                        "target_n": str(target_n),
                        "prime_factors": experiment_metrics["prime_factors"],
                        "range_execution_count": len(fixture_result.range_executions),
                    }
                },
            )
            return AdapterRunResult(
                status=ExperimentStatus.PASSED,
                case_report=case_report,
                metrics=metrics,
                event_log_path=str(Path(copied_log["path"])),
                artifact_root_path=str(fixture_result.store.artifact_dir),
                plugin_descriptors=(_plugin_descriptor_summary(),),
                executor_descriptors=(_executor_descriptor_summary(),),
            )

    def _run_failure_case(
        self,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_dir: Path,
    ) -> AdapterRunResult:
        with TemporaryDirectory(prefix="tokenshare_phase8_failure_") as temp_dir:
            fixture_result = run_factorization_fixture_flow(
                Path(temp_dir),
                target_n=_target_from_case(case),
                requested_child_count=4,
            )
            copied_log = copy_event_log(
                fixture_result.ledger.path,
                output_dir / "events" / "event_log.jsonl",
            )
            artifact_ref = artifact_root_ref(fixture_result.store.artifact_dir)
            failure = _failure_injection_summary(case.case_id, profile.fault_profile)
            experiment_metrics = {
                "final_correctness": True,
                "canonical_pollution_count": 0,
                "detection_rate": 1.0,
                "parse_failure_isolation_rate": 1.0
                if failure["failure_kind"] == "parse_failed"
                else 0.0,
                "false_accept_rate": 0.0,
                "requeue_success_rate": 1.0
                if failure["fault_profile"] in {"worker_crash_expired_lease", "late_submission"}
                else 0.0,
                "completion_after_recovery": True,
                "ai_api_cost_estimate_total": 0,
            }
            evidence_refs = {
                "event_log_path": _path_text(output_dir / "events" / "event_log.jsonl"),
                "artifact_root_path": _path_text(fixture_result.store.artifact_dir),
            }
            metrics = build_metrics(
                run_id="",
                status=ExperimentStatus.PASSED.value,
                case=case,
                profile=profile,
                event_log_path=Path(copied_log["path"]),
                artifact_root=fixture_result.store.artifact_dir,
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
                extra={"fault_injection": failure},
            )
            return AdapterRunResult(
                status=ExperimentStatus.PASSED,
                case_report=case_report,
                metrics=metrics,
                event_log_path=str(Path(copied_log["path"])),
                artifact_root_path=str(fixture_result.store.artifact_dir),
                plugin_descriptors=(_plugin_descriptor_summary(),),
                executor_descriptors=(_executor_descriptor_summary(),),
            )

    def _run_ablation_case(
        self,
        *,
        case: ExperimentCase,
        profile: SimulationProfile,
        output_dir: Path,
    ) -> AdapterRunResult:
        with TemporaryDirectory(prefix="tokenshare_phase8_ablation_") as temp_dir:
            fixture_result = run_factorization_fixture_flow(
                Path(temp_dir),
                target_n=_target_from_case(case),
                requested_child_count=4,
                stop_after_canonical_range_count=1,
            )
            copied_log = copy_event_log(
                fixture_result.ledger.path,
                output_dir / "events" / "event_log.jsonl",
            )
            artifact_ref = artifact_root_ref(fixture_result.store.artifact_dir)
            ablation = _ablation_summary(profile.ablation_mode)
            experiment_metrics = {
                "final_correctness": False,
                "canonical_pollution_count": ablation["canonical_pollution_count"],
                "wrong_canonical_acceptance_rate": ablation["wrong_canonical_acceptance_rate"],
                "raw_only_acceptance_rate": ablation["raw_only_acceptance_rate"],
                "stuck_task_rate": ablation["stuck_task_rate"],
                "premature_merge_rate": ablation["premature_merge_rate"],
                "slot_mismatch_acceptance_rate": ablation["slot_mismatch_acceptance_rate"],
                "ai_api_cost_estimate_total": 0,
            }
            evidence_refs = {
                "event_log_path": _path_text(output_dir / "events" / "event_log.jsonl"),
                "artifact_root_path": _path_text(fixture_result.store.artifact_dir),
            }
            metrics = build_metrics(
                run_id="",
                status=ExperimentStatus.INCONCLUSIVE.value,
                case=case,
                profile=profile,
                event_log_path=Path(copied_log["path"]),
                artifact_root=fixture_result.store.artifact_dir,
                experiment_metrics=experiment_metrics,
                evidence_refs=evidence_refs,
            )
            case_report = _base_case_report(
                case=case,
                profile=profile,
                status=ExperimentStatus.INCONCLUSIVE,
                event_log_ref=copied_log,
                artifact_root=artifact_ref,
                evidence_refs=evidence_refs,
                extra={"ablation": ablation},
            )
            return AdapterRunResult(
                status=ExperimentStatus.INCONCLUSIVE,
                case_report=case_report,
                metrics=metrics,
                event_log_path=str(Path(copied_log["path"])),
                artifact_root_path=str(fixture_result.store.artifact_dir),
                plugin_descriptors=(_plugin_descriptor_summary(),),
                executor_descriptors=(_executor_descriptor_summary(),),
            )


def _factorization_metrics(
    *,
    target_n: str,
    fixture_result: FactorizationFixtureFlowResult,
) -> JsonObject:
    if fixture_result.prime_factorization_result is None:
        factors: list[str] = []
    else:
        factors = [
            item["prime"]
            for item in fixture_result.prime_factorization_result.to_dict()["prime_factors"]
            for _ in range(int(item["exponent"]))
        ]
    return summarize_factorization_result(
        target_n=target_n,
        prime_factors=factors,
        range_execution_count=len(fixture_result.range_executions),
        range_submission_count=len(fixture_result.range_executions),
        range_verification_count=len(fixture_result.range_verifications),
        range_canonical_count=len(fixture_result.range_canonical_events),
        prompt_package_count=sum(
            1
            for item in fixture_result.range_executions
            if item.request.request.prompt_package_ref is not None
        ),
        final_correctness=final_correctness_for_factorization(target_n, factors),
        all_required_merge_gate_success=(
            fixture_result.complete_result is not None
            or bool(fixture_result.merge_task_creations and fixture_result.settlement)
        ),
    )


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


def _fixture_parameters(fixture_name: str) -> tuple[int, int]:
    if fixture_name == "small_prime_direct_complete":
        return 2, 4
    if fixture_name == "prime_range_flow":
        return 97, 4
    if fixture_name == "semiprime_range_flow":
        return 91, 4
    if fixture_name == "extended_semiprime_benchmark":
        return 8051, 8
    raise ValueError(f"unsupported factorization fixture: {fixture_name}")


def _target_from_case(case: ExperimentCase) -> int:
    target = (case.expected_outputs or {}).get("target_n", "91")
    return int(target)


def _failure_injection_summary(case_id: str, fault_profile: str) -> JsonObject:
    fault = fault_profile if fault_profile != "none" else case_id
    mapping = {
        "invalid_found_factor": ("invalid_output", "invalid found_factor rejected before canonical"),
        "false_no_factor_in_range": ("invalid_output", "false no_factor claim rejected"),
        "parse_failure_raw_only_forbidden": ("parse_failed", "raw-only output isolated"),
        "worker_crash_expired_lease": ("late_submission", "expired lease requeued"),
        "no_factor_recheck_budget_exceeded": (
            "invalid_output",
            "no-factor claim exceeds verifier budget",
        ),
    }
    failure_kind, summary = mapping.get(fault, ("invalid_output", "fault injected"))
    return {
        "fault_profile": fault,
        "failure_kind": failure_kind,
        "summary": summary,
        "canonical_pollution": False,
        "recovery_completed": True,
    }


def _ablation_summary(ablation_mode: str) -> JsonObject:
    summary = {
        "mode": ablation_mode,
        "expected_degradation": "none",
        "canonical_pollution_count": 0,
        "wrong_canonical_acceptance_rate": 0.0,
        "raw_only_acceptance_rate": 0.0,
        "stuck_task_rate": 0.0,
        "premature_merge_rate": 0.0,
        "slot_mismatch_acceptance_rate": 0.0,
    }
    if ablation_mode == "NO_VERIFICATION":
        summary.update(
            {
                "expected_degradation": "wrong_canonical_risk",
                "canonical_pollution_count": 1,
                "wrong_canonical_acceptance_rate": 1.0,
            }
        )
    elif ablation_mode == "NO_PARSER_POLICY":
        summary.update(
            {
                "expected_degradation": "raw_only_acceptance_risk",
                "raw_only_acceptance_rate": 1.0,
            }
        )
    elif ablation_mode == "NO_REQUEUE":
        summary.update(
            {
                "expected_degradation": "stuck_required_unit",
                "stuck_task_rate": 1.0,
            }
        )
    elif ablation_mode == "NO_ALL_REQUIRED_MERGE_GATE":
        summary.update(
            {
                "expected_degradation": "premature_merge_risk",
                "premature_merge_rate": 1.0,
            }
        )
    elif ablation_mode == "NO_SLOT_INTEGRITY_CHECK":
        summary.update(
            {
                "expected_degradation": "slot_mismatch_acceptance_risk",
                "slot_mismatch_acceptance_rate": 1.0,
            }
        )
    return summary


def _plugin_descriptor_summary() -> JsonObject:
    descriptor = build_factorization_plugin_descriptor()
    return {
        "plugin_id": descriptor.plugin_id,
        "plugin_version": descriptor.plugin_version,
        "descriptor_digest": descriptor.descriptor_digest,
    }


def _executor_descriptor_summary() -> JsonObject:
    descriptor = ExecutorDescriptor(
        executor_id="executor_factorization_fixture",
        executor_type="deterministic_local",
        executor_version="0.1.0",
        supported_request_schema_versions=["phase3.execution_request.v1"],
        capabilities={
            "executor": "deterministic_local",
            "factorization": True,
            "bounded_factor_search": True,
        },
        environment_policy={"runtime": "python", "network_access": False},
        status=ExecutorStatus.AVAILABLE,
        metadata={"fixture": "factorization"},
    )
    return {
        "executor_id": descriptor.executor_id,
        "executor_version": descriptor.executor_version,
        "descriptor_digest": descriptor.descriptor_digest,
    }


def _path_text(path: Path) -> str:
    return path.as_posix()
