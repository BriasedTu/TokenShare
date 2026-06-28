from math import isqrt

import pytest

from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges


def test_candidate_range_partition_covers_domain_without_gap_or_overlap() -> None:
    result = partition_candidate_ranges(
        target_n="101",
        requested_child_count=4,
        max_children_per_unit=10,
    )

    ranges = result.ranges
    assert result.params.target_n == "101"
    assert result.params.min_divisor == "2"
    assert result.params.max_divisor == str(isqrt(101))
    assert result.params.actual_child_count == 4
    assert len(ranges) == 4

    expected_start = 2
    for index, range_input in enumerate(ranges):
        assert range_input.child_index == index
        assert range_input.child_count == len(ranges)
        assert range_input.range_start == str(expected_start)
        assert int(range_input.range_start) <= int(range_input.range_end)
        assert range_input.coverage_id == result.coverage_proof.coverage_id
        assert range_input.partition_params_digest == result.params.params_digest
        expected_start = int(range_input.range_end) + 1

    assert ranges[-1].range_end == str(isqrt(101))
    assert result.coverage_proof.no_gap is True
    assert result.coverage_proof.no_overlap is True
    assert result.coverage_proof.full_domain_covered is True
    assert result.coverage_proof.sqrt_bound_checked is True


def test_candidate_range_partition_is_deterministic_for_same_input() -> None:
    first = partition_candidate_ranges(
        target_n="221",
        requested_child_count=5,
        max_children_per_unit=4,
    )
    second = partition_candidate_ranges(
        target_n="221",
        requested_child_count=5,
        max_children_per_unit=4,
    )

    assert first.to_dict() == second.to_dict()
    assert first.params.params_digest == second.params.params_digest
    assert first.coverage_proof.coverage_id == second.coverage_proof.coverage_id
    assert first.coverage_proof.ranges_digest == second.coverage_proof.ranges_digest


def test_candidate_range_partition_respects_max_children_per_unit() -> None:
    result = partition_candidate_ranges(
        target_n="1009",
        requested_child_count=20,
        max_children_per_unit=3,
    )

    assert result.params.requested_child_count == 20
    assert result.params.actual_child_count == 3
    assert len(result.ranges) == 3
    assert all(range_input.child_count == 3 for range_input in result.ranges)


def test_candidate_range_partition_uses_non_empty_ranges() -> None:
    result = partition_candidate_ranges(
        target_n="10",
        requested_child_count=8,
        max_children_per_unit=8,
    )

    assert result.params.actual_child_count == 2
    assert [(item.range_start, item.range_end) for item in result.ranges] == [
        ("2", "2"),
        ("3", "3"),
    ]
    assert all(int(item.range_start) <= int(item.range_end) for item in result.ranges)


def test_candidate_range_partition_rejects_empty_domain_instead_of_zero_child_coverage() -> None:
    with pytest.raises(ValueError, match="candidate domain"):
        partition_candidate_ranges(
            target_n="2",
            requested_child_count=4,
            max_children_per_unit=4,
        )


def test_candidate_range_partition_rejects_bool_child_counts() -> None:
    with pytest.raises(TypeError, match="requested_child_count"):
        partition_candidate_ranges(
            target_n="101",
            requested_child_count=True,  # type: ignore[arg-type]
            max_children_per_unit=4,
        )
    with pytest.raises(TypeError, match="max_children_per_unit"):
        partition_candidate_ranges(
            target_n="101",
            requested_child_count=4,
            max_children_per_unit=True,  # type: ignore[arg-type]
        )
