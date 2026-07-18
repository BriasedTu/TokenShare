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
    PaperAblationSummary,
    ablation_profile_for_mode,
    summarize_ablation_evidence,
    validate_ablation_attempt_coverage,
)
from tokenshare.experiments.paper_faults import (
    FaultInjectionOutcome,
    FaultInjectionRecord,
    FaultTargetDescriptor,
    PaperFaultType,
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
from tokenshare.experiments.paper_metrics import (
    PaperMetricsResult,
    recompute_exp1_pilot_metrics,
)
from tokenshare.experiments.paper_report import (
    PaperReportResult,
    write_exp1_pilot_report,
)
from tokenshare.experiments.paper_runner import expand_plan_conditions
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    WorkerDeathOutcome,
    WorkerDeathRecord,
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
    "LeanProofExperimentAdapter",
    "LeanPaperRunResult",
    "PaperAttemptResult",
    "PaperAttemptStatus",
    "PaperAblationEvidenceRecord",
    "PaperAblationMode",
    "PaperAblationProfile",
    "PaperAblationSummary",
    "PaperAIUnit",
    "PaperBudgetResult",
    "PaperConditionResult",
    "PaperEligibilityReport",
    "PaperExperimentCondition",
    "PaperExperimentResult",
    "PaperRunResult",
    "PaperStatus",
    "PaperSuiteResult",
    "PaperTaskResult",
    "PaperTaskStatus",
    "PaperFaultType",
    "PaperMetricsResult",
    "PaperReportResult",
    "PluginExperimentAdapter",
    "SimulationProfile",
    "SimulationWrapper",
    "ScriptedFactorizationRangeTransport",
    "ScriptedLeanPaperProofTransport",
    "AblationAttemptCoverage",
    "WorkerDeathKillPoint",
    "WorkerDeathOutcome",
    "WorkerDeathRecord",
    "ablation_profile_for_mode",
    "default_experiment_cases",
    "evaluate_paper_eligibility",
    "expand_plan_conditions",
    "generate_lean_ai_benchmark_cases",
    "inject_post_ai_fault",
    "load_paper_catalogs",
    "run_factorization_500_ai_suite",
    "run_factorization_paper_case",
    "run_ai_profile_suite",
    "run_lean_ai_benchmark_suite",
    "run_lean_paper_case",
    "run_phase8_default_suite",
    "recompute_exp1_pilot_metrics",
    "run_worker_death_harness",
    "select_fault_target_descriptors",
    "select_fault_targets",
    "summarize_ablation_evidence",
    "validate_ablation_attempt_coverage",
    "validate_ai_unit_dependency_graph",
    "write_exp1_pilot_report",
]
