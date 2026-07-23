"""Experiment runner, fault simulation, metrics, and reports."""

from tokenshare.experiments.adapters import AdapterRegistry, PluginExperimentAdapter
from tokenshare.experiments.ai_profile import run_ai_profile_suite
from tokenshare.experiments.factorization_adapter import FactorizationExperimentAdapter
from tokenshare.experiments.factorization_500_ai import run_factorization_500_ai_suite
from tokenshare.experiments.factorization_paper_adapter import (
    FactorizationPaperRunResult,
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_adapter import LeanProofExperimentAdapter
from tokenshare.experiments.lean_ai_benchmark import (
    generate_lean_ai_benchmark_cases,
    run_lean_ai_benchmark_suite,
)
from tokenshare.experiments.lean_paper_adapter import (
    LeanPaperRunResult,
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.models import (
    ExperimentCase,
    ExperimentResult,
    ExperimentRun,
    ExperimentStatus,
    SimulationProfile,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_ablation import (
    AblationAttemptCoverage,
    PaperAblationEvidenceRecord,
    PaperAblationMode,
    PaperAblationProfile,
    PaperAblationRuntimeControls,
    PaperAblationRuntimeHooks,
    PaperAblationSummary,
    ablation_profile_for_mode,
    runtime_controls_for_mode,
    summarize_ablation_evidence,
    validate_ablation_attempt_coverage,
)
from tokenshare.experiments.paper_faults import (
    FaultInjectionOutcome,
    FaultInjectionRecord,
    FaultTargetDescriptor,
    PaperFaultType,
    PaperFaultRuntimeHooks,
    inject_post_ai_fault,
    select_fault_target_descriptors,
    select_fault_targets,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperBudgetResult,
    PaperConditionResult,
    PaperEligibilityReport,
    PaperExperimentCondition,
    PaperExperimentResult,
    PaperRunResult,
    PaperStatus,
    PaperSuiteResult,
    PaperTaskResult,
    PaperTaskStatus,
    evaluate_paper_eligibility,
)
from tokenshare.experiments.paper_dispatcher import (
    FrozenCaseSelectionBatch,
    FrozenConditionSelectionBinding,
    PaperExperimentDispatchPlan,
    dispatch_paper_case,
    dispatch_paper_condition,
    load_paper_experiment_module,
    plan_paper_experiment,
    registered_paper_experiment_ids,
)
from tokenshare.experiments.paper_metrics import (
    PaperMetricsResult,
    recompute_gate_c_pilot_metrics,
    recompute_exp1_pilot_metrics,
    write_gate_c_pilot_metrics,
)
from tokenshare.experiments.paper_projection import (
    PaperProtocolProjection,
    project_paper_protocol_run,
)
from tokenshare.experiments.paper_report import (
    PaperReportResult,
    write_exp1_pilot_report,
)
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_formal_runner import (
    execute_paper_formal_suite,
    replay_paper_formal_suite,
)
from tokenshare.experiments.paper_formal_callbacks import (
    FormalAblationResult,
    FormalStrategyResult,
    run_exp1_normal_strategy,
    run_exp3_post_ai_strategy,
    run_exp3_worker_death_strategy,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_formal_metrics import (
    FormalMetricsResult,
    recompute_paper_formal_metrics,
)
from tokenshare.experiments.paper_formal_report import (
    FormalReportResult,
    generate_paper_formal_report,
)
from tokenshare.experiments.paper_runner import expand_plan_conditions
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    WorkerDeathOutcome,
    WorkerDeathPlan,
    WorkerDeathRecord,
    freeze_worker_death_plan,
    record_worker_death_observation,
    run_worker_death_harness,
    validate_ai_unit_dependency_graph,
)
from tokenshare.experiments.runner import (
    ExperimentRunner,
    default_experiment_cases,
    run_phase8_default_suite,
)
from tokenshare.experiments.simulation import SimulationWrapper

__all__ = [
    "AdapterRegistry",
    "ExperimentCase",
    "ExperimentResult",
    "ExperimentRun",
    "ExperimentRunner",
    "ExperimentStatus",
    "FactorizationExperimentAdapter",
    "FactorizationPaperRunResult",
    "FaultInjectionOutcome",
    "FaultInjectionRecord",
    "FaultTargetDescriptor",
    "FormalAblationResult",
    "FormalEvidenceStore",
    "FormalMetricsResult",
    "FormalReportResult",
    "FormalStrategyResult",
    "FrozenCaseSelectionBatch",
    "FrozenConditionSelectionBinding",
    "LeanProofExperimentAdapter",
    "LeanPaperRunResult",
    "PaperAttemptResult",
    "PaperAttemptStatus",
    "PaperAblationEvidenceRecord",
    "PaperAblationMode",
    "PaperAblationProfile",
    "PaperAblationRuntimeControls",
    "PaperAblationRuntimeHooks",
    "PaperAblationSummary",
    "PaperAIUnit",
    "PaperBudgetResult",
    "PaperConditionResult",
    "PaperEligibilityReport",
    "PaperExperimentCondition",
    "PaperExperimentDispatchPlan",
    "PaperExperimentResult",
    "PaperRunResult",
    "PaperStatus",
    "PaperSuiteResult",
    "PaperTaskResult",
    "PaperTaskStatus",
    "PaperFaultType",
    "PaperFaultRuntimeHooks",
    "PaperMetricsResult",
    "PaperReportResult",
    "PaperProtocolProjection",
    "PluginExperimentAdapter",
    "SimulationProfile",
    "SimulationWrapper",
    "ScriptedFactorizationRangeTransport",
    "ScriptedLeanPaperProofTransport",
    "AblationAttemptCoverage",
    "WorkerDeathKillPoint",
    "WorkerDeathOutcome",
    "WorkerDeathPlan",
    "WorkerDeathRecord",
    "freeze_worker_death_plan",
    "record_worker_death_observation",
    "ablation_profile_for_mode",
    "runtime_controls_for_mode",
    "default_experiment_cases",
    "dispatch_paper_case",
    "dispatch_paper_condition",
    "evaluate_paper_eligibility",
    "execute_paper_formal_suite",
    "expand_plan_conditions",
    "generate_lean_ai_benchmark_cases",
    "generate_paper_formal_report",
    "inject_post_ai_fault",
    "load_paper_catalogs",
    "load_paper_experiment_module",
    "plan_paper_experiment",
    "project_paper_protocol_run",
    "registered_paper_experiment_ids",
    "run_factorization_500_ai_suite",
    "run_factorization_paper_case",
    "run_ai_profile_suite",
    "run_lean_ai_benchmark_suite",
    "run_lean_paper_case",
    "run_phase8_default_suite",
    "recompute_exp1_pilot_metrics",
    "recompute_gate_c_pilot_metrics",
    "recompute_paper_formal_metrics",
    "replay_paper_formal_suite",
    "run_exp1_normal_strategy",
    "run_exp3_post_ai_strategy",
    "run_exp3_worker_death_strategy",
    "run_exp4_ablation_strategy",
    "run_exp5_identity_strategy",
    "run_scheduled_cases",
    "run_worker_death_harness",
    "select_fault_target_descriptors",
    "select_fault_targets",
    "summarize_ablation_evidence",
    "validate_ablation_attempt_coverage",
    "validate_ai_unit_dependency_graph",
    "write_exp1_pilot_report",
    "write_gate_c_pilot_metrics",
]
