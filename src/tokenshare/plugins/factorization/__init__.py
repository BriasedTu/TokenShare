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
from tokenshare.plugins.factorization.split_strategy import (
    CandidateRangePartitionResult,
    partition_candidate_ranges,
)

__all__ = [
    "CandidateRangeCoverageProof",
    "CandidateRangePartitionParams",
    "CandidateRangePartitionResult",
    "FactorIntegerSubject",
    "FactorSearchInstruction",
    "FactorSearchRangeInput",
    "FactorizationMergeResult",
    "PrimeFactor",
    "PrimeFactorizationResult",
    "RangeResult",
    "RootInput",
    "build_factorization_plugin_descriptor",
    "partition_candidate_ranges",
]
