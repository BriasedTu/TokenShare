"""Experiment runner, fault simulation, metrics, and reports."""

from tokenshare.experiments.adapters import AdapterRegistry, PluginExperimentAdapter
from tokenshare.experiments.ai_profile import run_ai_profile_suite
from tokenshare.experiments.factorization_adapter import FactorizationExperimentAdapter
from tokenshare.experiments.factorization_500_ai import run_factorization_500_ai_suite
from tokenshare.experiments.lean_adapter import LeanProofExperimentAdapter
from tokenshare.experiments.lean_ai_benchmark import (
    generate_lean_ai_benchmark_cases,
    run_lean_ai_benchmark_suite,
)
from tokenshare.experiments.models import (
    ExperimentCase,
    ExperimentResult,
    ExperimentRun,
    ExperimentStatus,
    SimulationProfile,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
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
from tokenshare.experiments.paper_runner import expand_plan_conditions
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
    "LeanProofExperimentAdapter",
    "PaperAttemptResult",
    "PaperAttemptStatus",
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
    "PluginExperimentAdapter",
    "SimulationProfile",
    "SimulationWrapper",
    "default_experiment_cases",
    "evaluate_paper_eligibility",
    "expand_plan_conditions",
    "generate_lean_ai_benchmark_cases",
    "load_paper_catalogs",
    "run_factorization_500_ai_suite",
    "run_ai_profile_suite",
    "run_lean_ai_benchmark_suite",
    "run_phase8_default_suite",
]
