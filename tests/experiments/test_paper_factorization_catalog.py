from __future__ import annotations

from collections import Counter, defaultdict

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


def test_generates_500_unique_roots_across_all_difficulties_and_magnitudes() -> None:
    cases = generate_factorization_paper_cases()

    assert len(cases) == 500
    assert len({case["case_id"] for case in cases}) == 500
    assert len({case["target_n"] for case in cases}) == 500
    assert Counter(case["difficulty"] for case in cases) == EXPECTED_DIFFICULTY_COUNTS
    assert [case["catalog_ordinal"] for case in cases] == list(range(500))
    assert {case["generator_version"] for case in cases} == {
        CATALOG_GENERATOR_VERSION
    }

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
        assert count == end - start + 1
        assert end * end <= target_n

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
    assert 1 <= hard_positions["no_factor"] <= 10
    hard_factor_counts = [hard_positions[name] for name in ("early", "middle", "late")]
    assert max(hard_factor_counts) - min(hard_factor_counts) <= 1


def test_catalog_jsonl_is_byte_stable_for_seed_and_changes_for_new_seed() -> None:
    first = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260720))
    repeated = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260720))
    changed = catalog_jsonl_text(generate_factorization_paper_cases(seed=20260721))

    assert first == repeated
    assert first != changed
    assert len(first.splitlines()) == 500
    assert first.endswith("\n")
