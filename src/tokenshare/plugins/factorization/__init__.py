"""Factorization proof-of-concept plugin package."""

from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.models import (
    CandidateRangeCoverageProof,
    CandidateRangePartitionParams,
    FactorIntegerSubject,
    FactorSearchInstruction,
    FactorSearchRangeInput,
    FactorizationMergeResult,
    PrimeFactor,
    PrimeFactorizationResult,
    RangeResult,
    RootInput,
)
from tokenshare.plugins.factorization.merge_policy import (
    FactorizationMergePolicyResult,
    RangeSlotMergeInput,
    merge_required_range_results,
)
from tokenshare.plugins.factorization.prompt_builder import build_factor_search_prompt_package
from tokenshare.plugins.factorization.split_strategy import (
    CandidateRangePartitionResult,
    FactorizationSplitStrategyActionResult,
    FactorizationSplitPlanResult,
    build_factorization_split_plan,
    build_factorization_split_strategy_result,
    partition_candidate_ranges,
)
from tokenshare.plugins.factorization.validator import (
    FactorizationAIParseResult,
    parse_factorization_ai_output,
    verify_factor_integer_subject,
)

__all__ = [
    "CandidateRangeCoverageProof",
    "CandidateRangePartitionParams",
    "CandidateRangePartitionResult",
    "FactorizationSplitStrategyActionResult",
    "FactorizationSplitPlanResult",
    "FactorIntegerSubject",
    "FactorSearchInstruction",
    "FactorSearchRangeInput",
    "FactorizationMergeResult",
    "FactorizationMergePolicyResult",
    "FactorizationAIParseResult",
    "PrimeFactor",
    "PrimeFactorizationResult",
    "RangeResult",
    "RangeSlotMergeInput",
    "RootInput",
    "build_factor_search_prompt_package",
    "build_factorization_split_plan",
    "build_factorization_split_strategy_result",
    "build_factorization_plugin_descriptor",
    "merge_required_range_results",
    "parse_factorization_ai_output",
    "partition_candidate_ranges",
    "verify_factor_integer_subject",
]
