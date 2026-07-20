from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
from math import isqrt
from pathlib import Path

import pytest

from tokenshare.experiments.paper_catalog import (
    _factorization_catalog_profile,
    _validate_factorization_v2_inventory,
)
from tokenshare.experiments.paper_factorization_catalog import (
    CATALOG_GENERATOR_VERSION,
    catalog_jsonl_text,
    generate_factorization_paper_cases,
    is_prime_64,
)
from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges


EXPECTED_DIFFICULTY_COUNTS = {"easy": 167, "medium": 167, "hard": 166}
EXPECTED_CHILD_COUNTS = {"easy": 2, "medium": 4, "hard": 8}
EXPECTED_MAGNITUDES = {6, 7, 8, 9, 10}
EXPECTED_GENERATOR_VERSION = "tokenshare.paper_factorization_catalog.v2.full_domain.v1"


def test_generates_500_unique_roots_across_all_difficulties_and_magnitudes() -> None:
    cases = generate_factorization_paper_cases()

    assert len(cases) == 500
    assert len({case["case_id"] for case in cases}) == 500
    assert len({case["target_n"] for case in cases}) == 500
    assert Counter(case["difficulty"] for case in cases) == EXPECTED_DIFFICULTY_COUNTS
    assert [case["catalog_ordinal"] for case in cases] == list(range(500))
    assert {case["generator_version"] for case in cases} == {
        EXPECTED_GENERATOR_VERSION
    }
    assert CATALOG_GENERATOR_VERSION == EXPECTED_GENERATOR_VERSION

    magnitudes_by_difficulty: dict[str, set[int]] = defaultdict(set)
    for case in cases:
        target_n = int(case["target_n"])
        assert 1_000_000 <= target_n < 100_000_000_000
        magnitudes_by_difficulty[case["difficulty"]].add(len(str(target_n)) - 1)

    assert magnitudes_by_difficulty == {
        difficulty: EXPECTED_MAGNITUDES for difficulty in EXPECTED_DIFFICULTY_COUNTS
    }


def test_every_case_has_a_valid_oracle_position_and_deterministic_range_split() -> None:
    cases = generate_factorization_paper_cases()
    positions_by_difficulty: dict[str, Counter[str]] = defaultdict(Counter)

    for case in cases:
        target_n = int(case["target_n"])
        factors = case["oracle_prime_factors"]
        product = 1
        for factor in factors:
            prime = int(factor["prime"])
            assert is_prime_64(prime)
            product *= prime ** int(factor["exponent"])
        assert product == target_n

        start = int(case["candidate_start"])
        end = int(case["candidate_end"])
        count = int(case["candidate_divisor_count"])
        assert start == 2
        assert end == isqrt(target_n)
        assert count == end - start + 1

        difficulty = case["difficulty"]
        requested_children = int(case["split_params"]["requested_child_count"])
        assert requested_children == EXPECTED_CHILD_COUNTS[difficulty]
        partition = partition_candidate_ranges(
            target_n=target_n,
            requested_child_count=requested_children,
            max_children_per_unit=requested_children,
            min_divisor=start,
            max_divisor=end,
        )
        assert len(partition.ranges) == requested_children
        assert partition.coverage_proof.no_gap is True
        assert partition.coverage_proof.no_overlap is True
        assert partition.coverage_proof.full_domain_covered is True
        assert int(partition.ranges[0].range_start) == start
        assert int(partition.ranges[-1].range_end) == end

        position = case["factor_position_quantile"]
        positions_by_difficulty[difficulty][position] += 1
        in_range_factors = [
            int(factor["prime"])
            for factor in factors
            if start <= int(factor["prime"]) <= end
        ]
        if position == "no_factor":
            assert difficulty == "hard"
            assert not in_range_factors
            continue

        assert len(in_range_factors) == 1
        ratio = (in_range_factors[0] - start) / (end - start)
        expected_position = (
            "early" if ratio <= 1 / 3 else "middle" if ratio <= 2 / 3 else "late"
        )
        assert position == expected_position

    for difficulty in ("easy", "medium"):
        counts = positions_by_difficulty[difficulty]
        assert set(counts) == {"early", "middle", "late"}
        assert max(counts.values()) - min(counts.values()) <= 1
    hard_positions = positions_by_difficulty["hard"]
    assert hard_positions["no_factor"] == 7
    hard_factor_counts = [hard_positions[name] for name in ("early", "middle", "late")]
    assert max(hard_factor_counts) - min(hard_factor_counts) <= 1


def test_factorization_v2_validator_rejects_candidate_end_shortened_by_one() -> None:
    cases = list(generate_factorization_paper_cases())
    shortened = deepcopy(cases[0])
    shortened["candidate_end"] = str(int(shortened["candidate_end"]) - 1)
    shortened["candidate_divisor_count"] = (
        int(shortened["candidate_divisor_count"]) - 1
    )
    cases[0] = shortened

    with pytest.raises(ValueError, match="complete candidate domain"):
        _validate_factorization_v2_inventory(tuple(cases))


def test_factorization_v2_profile_rejects_missing_row_generator_version() -> None:
    cases = list(generate_factorization_paper_cases())
    missing_version = deepcopy(cases[1])
    missing_version.pop("generator_version")
    cases[1] = missing_version

    with pytest.raises(ValueError, match="every row must use generator version"):
        _factorization_catalog_profile(tuple(cases))


def test_catalog_jsonl_is_byte_stable_for_seed_and_changes_for_new_seed() -> None:
    first = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260720))
    repeated = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260720))
    changed = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260721))

    assert first == repeated
    assert first != changed
    assert len(first.splitlines()) == 500
    assert first.endswith("\n")


def test_checked_in_v2_catalog_matches_full_domain_generator_byte_for_byte() -> None:
    checked_in = Path("benchmarks/paper/factorization_catalog.v2.jsonl").read_bytes()
    generated = catalog_jsonl_text(generate_factorization_paper_cases()).encode("utf-8")

    assert len(checked_in) == len(generated)
    assert sha256(checked_in).digest() == sha256(generated).digest()
