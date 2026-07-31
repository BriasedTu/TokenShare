from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_suite_scale import (
    build_paper_suite_scale_policy,
    load_paper_suite_scale_profile,
    validate_paper_suite_scale_policy,
)
from tokenshare.experiments.paper_models import digest_json


def _candidates() -> dict[str, tuple[dict[str, str], ...]]:
    return {
        "easy": tuple(
            {"case_id": f"factor_v2_easy_{index:03d}"} for index in range(167)
        ),
        "medium": tuple(
            {"case_id": f"factor_v2_medium_{index:03d}"} for index in range(167)
        ),
        "hard": tuple(
            {"case_id": f"factor_v2_hard_{index:03d}"} for index in range(166)
        ),
    }


def test_tracked_suite_scale_profile_builds_nested_experiment_selections() -> None:
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )

    selected = profile.select_factorization_cases(_candidates())

    assert {key: len(value) for key, value in selected["corpus"].items()} == {
        "easy": 100,
        "medium": 100,
        "hard": 100,
    }
    assert selected["exp1_real_ai_feasibility"] == selected["corpus"]
    assert set(selected["exp2_real_ai_scalability"]) == {"hard"}
    assert selected["exp2_real_ai_scalability"]["hard"] == selected["corpus"][
        "hard"
    ][:50]
    exp3 = selected["exp3_real_ai_fault_recovery"]
    exp4 = selected["exp4_real_ai_protocol_ablation"]
    assert exp3 == exp4
    assert {key: len(value) for key, value in exp3.items()} == {
        "easy": 17,
        "medium": 17,
        "hard": 16,
    }
    assert all(
        exp3[difficulty]
        == selected["corpus"][difficulty][: len(exp3[difficulty])]
        for difficulty in exp3
    )


def test_tracked_suite_scale_profile_freezes_exp5_v4_nested_counts() -> None:
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )

    assert profile.exp5_source_selection_path == (
        "benchmarks/paper/exp5_parent_quarter_selection.v4.json"
    )
    assert profile.exp5_source_selection_digest == (
        "sha256:452f25dcc53a1eb0387665c6f451f1320095efb0c4bf154af62bf5e388afb6b2"
    )
    assert profile.exp5_case_count_by_stratum == {
        "factorization:hard": 42,
        "lean_proof:hard:pure_logic": 4,
        "lean_proof:hard:function_set": 4,
        "lean_proof:hard:induction": 4,
    }
    assert sum(profile.exp5_case_count_by_stratum.values()) == 54


def test_tracked_historical_selection_artifacts_remain_byte_immutable() -> None:
    anchors = {
        "benchmarks/paper/paper_factorization_sampling_profile.v1.json": (
            618,
            "2e4bae1e24d43b58403a5421a572526fa696d096d52b713236f6a2ebc4cba2da",
        ),
        "benchmarks/paper/exp5_hard_half_selection.v3.json": (
            10262,
            "76e3e3d4edc6817b8babb8470e44f2ea3617a1dcc12e8f548b3e702a1c20bf71",
        ),
    }

    for path, (expected_size, expected_digest) in anchors.items():
        body = Path(path).read_bytes()
        assert len(body) == expected_size
        assert hashlib.sha256(body).hexdigest() == expected_digest


def _tracked_candidates() -> dict[str, tuple[dict[str, object], ...]]:
    candidates = {difficulty: [] for difficulty in ("easy", "medium", "hard")}
    for line in Path("benchmarks/paper/factorization_catalog.v2.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        case = json.loads(line)
        candidates[str(case["paper_difficulty"])].append(case)
    return {key: tuple(value) for key, value in candidates.items()}


def test_suite_scale_policy_validates_against_real_candidate_catalog() -> None:
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    candidates = _tracked_candidates()
    policy, selected = build_paper_suite_scale_policy(
        profile=profile,
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=profile.catalog_digest,
        candidates_by_difficulty=candidates,
    )

    _, validated = validate_paper_suite_scale_policy(
        dict(reversed(tuple(policy.items()))),
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=profile.catalog_digest,
        experiment_id="exp3_real_ai_fault_recovery",
    )

    assert validated == {
        difficulty: tuple(str(case["case_id"]) for case in cases)
        for difficulty, cases in selected["exp3_real_ai_fault_recovery"].items()
    }
    reversed_candidates = {
        difficulty: tuple(reversed(cases))
        for difficulty, cases in candidates.items()
    }
    assert profile.select_factorization_cases(reversed_candidates) == selected


def test_suite_scale_policy_rejects_self_consistent_non_catalog_candidates() -> None:
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    forged_policy, _ = build_paper_suite_scale_policy(
        profile=profile,
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=profile.catalog_digest,
        candidates_by_difficulty=_candidates(),
    )

    with pytest.raises(ValueError, match="canonical candidate selection drift"):
        validate_paper_suite_scale_policy(
            forged_policy,
            catalog_id=profile.catalog_id,
            catalog_version=profile.catalog_version,
            catalog_digest=profile.catalog_digest,
            experiment_id="exp1_real_ai_feasibility",
        )


def test_suite_scale_selection_fails_closed_when_candidates_are_missing() -> None:
    profile = load_paper_suite_scale_profile(
        "benchmarks/paper/paper_suite_scale_profile.v1.json"
    )
    candidates = _tracked_candidates()
    candidates["hard"] = candidates["hard"][:99]

    with pytest.raises(ValueError, match="candidate count is insufficient"):
        profile.select_factorization_cases(candidates)


def test_suite_profile_rejects_self_consistent_exp5_non_parent_prefix(
    tmp_path: Path,
) -> None:
    active = json.loads(
        Path("benchmarks/paper/exp5_parent_quarter_selection.v4.json").read_text(
            encoding="utf-8"
        )
    )
    active["strata"][0]["ordered_case_ids"][0] = "factor_v2_hard_050"
    active["ordered_case_ids"][0] = "factor_v2_hard_050"
    digest_body = dict(active)
    digest_body.pop("selection_digest")
    active["selection_digest"] = digest_json(digest_body)
    active_path = tmp_path / "forged-exp5-v4.json"
    active_path.write_text(json.dumps(active), encoding="utf-8")

    profile = json.loads(
        Path("benchmarks/paper/paper_suite_scale_profile.v1.json").read_text(
            encoding="utf-8"
        )
    )
    profile["exp5"]["source_selection_path"] = active_path.as_posix()
    profile["exp5"]["source_selection_digest"] = active["selection_digest"]
    profile_path = tmp_path / "forged-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="provenance drift"):
        load_paper_suite_scale_profile(profile_path)
