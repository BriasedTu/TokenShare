import pytest

from tokenshare.experiments.factorization_exp1_analysis import (
    _validate_output_path,
    assign_input_scale_groups,
    attempt_failure_type,
    nullable_sum,
    partition_ranges,
)


def test_output_path_validation_allows_repo_without_tracked_results(tmp_path):
    repo = tmp_path / "repo"
    catalog_path = repo / "benchmarks" / "experiments" / "catalog.jsonl"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text("", encoding="utf-8")
    run_dir = tmp_path / "official-full-run"
    run_dir.mkdir()
    output_dir = (
        repo / "TokenShareData" / "outputs" / "experiments" / "analysis-run"
    )

    _validate_output_path(run_dir, catalog_path, output_dir)

    with pytest.raises(ValueError, match="analysis output namespace"):
        _validate_output_path(run_dir, catalog_path, repo / "analysis-run")


def test_partition_ranges_puts_remainder_in_earliest_ranges():
    assert partition_ranges(2, 11, 3) == ((2, 5), (6, 8), (9, 11))


def test_nullable_sum_preserves_missing_usage():
    assert nullable_sum([]) == 0
    assert nullable_sum([4, 5]) == 9
    assert nullable_sum([4, None]) is None


def test_attempt_failure_type_uses_documented_precedence():
    assert attempt_failure_type("provider_failed", None, None) == "provider_failure"
    assert attempt_failure_type("parse_rejected", "rejected", None) == "parse_failure"
    assert (
        attempt_failure_type("parsed", "parsed", "rejected")
        == "verification_rejection"
    )
    assert attempt_failure_type("parsed", "parsed", "passed") is None


def test_input_scale_groups_are_deterministic_ntiles():
    rows = [
        {
            "case_id": f"c{i:03d}",
            "partition_count": 8,
            "is_prime": False,
            "candidate_domain_size": i,
            "target_n": i * i,
        }
        for i in range(1, 95)
    ]

    assign_input_scale_groups(rows)

    counts = {
        name: sum(row["input_scale_group"] == name for row in rows)
        for name in ("small_M", "middle_M", "large_M")
    }
    assert counts == {"small_M": 32, "middle_M": 31, "large_M": 31}
    assert [row["input_scale_rank"] for row in rows] == list(range(1, 95))
