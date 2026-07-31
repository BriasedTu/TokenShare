from __future__ import annotations

import json

import pytest

from tokenshare.experiments.paper_factorization_sampling import (
    FACTORIZATION_SAMPLING_PROFILE_SCHEMA_VERSION,
    FactorizationSamplingProfile,
    load_factorization_sampling_profile,
)
from tokenshare.experiments.paper_models import digest_json


def _profile_payload(*, cases_per_difficulty: int) -> dict[str, object]:
    return {
        "schema_version": FACTORIZATION_SAMPLING_PROFILE_SCHEMA_VERSION,
        "profile_id": "factorization_test_stable_hash.v1",
        "approved_catalog_versions": ["v2"],
        "experiment_ids": [
            "exp3_real_ai_fault_recovery",
            "exp4_real_ai_protocol_ablation",
        ],
        "selection_rule": {
            "rule_id": "stable_hash_by_difficulty_not_catalog_prefix",
            "score_schema_version": (
                "tokenshare.paper_factorization_stratified_score.v2"
            ),
            "seed": 24680,
        },
        "difficulty_order": ["easy", "medium", "hard"],
        "case_count_by_difficulty": {
            difficulty: cases_per_difficulty
            for difficulty in ("easy", "medium", "hard")
        },
    }


def test_sampling_profile_loader_accepts_digest_bound_data_driven_counts(
    tmp_path,
) -> None:
    profile_path = tmp_path / "factor-sampling.json"
    profile_path.write_text(
        json.dumps(_profile_payload(cases_per_difficulty=30)),
        encoding="utf-8",
    )

    profile = load_factorization_sampling_profile(profile_path)

    assert isinstance(profile, FactorizationSamplingProfile)
    assert profile.case_count_by_difficulty == {
        "easy": 30,
        "medium": 30,
        "hard": 30,
    }
    assert profile.profile_digest == digest_json(
        _profile_payload(cases_per_difficulty=30)
    )
    assert profile.total_case_count == 90
    assert profile.approves(
        catalog_version="v2",
        experiment_id="exp3_real_ai_fault_recovery",
    )
    assert profile.approves(
        catalog_version="v2",
        experiment_id="exp4_real_ai_protocol_ablation",
    )


def test_sampling_profile_loader_rejects_source_digest_drift(tmp_path) -> None:
    payload = _profile_payload(cases_per_difficulty=30)
    payload["profile_digest"] = "sha256:" + "0" * 64
    profile_path = tmp_path / "factor-sampling-drifted.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="profile digest"):
        load_factorization_sampling_profile(profile_path)


def test_sampling_profile_loader_rejects_unknown_source_fields(tmp_path) -> None:
    payload = _profile_payload(cases_per_difficulty=30)
    payload["unexpected"] = "not-approved"
    profile_path = tmp_path / "factor-sampling-unknown.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown fields"):
        load_factorization_sampling_profile(profile_path)


def test_sampling_profile_loader_rejects_digest_after_body_drift(tmp_path) -> None:
    payload = _profile_payload(cases_per_difficulty=30)
    payload["profile_digest"] = digest_json(payload)
    payload["case_count_by_difficulty"] = {
        "easy": 29,
        "medium": 30,
        "hard": 30,
    }
    profile_path = tmp_path / "factor-sampling-drifted.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="profile digest"):
        load_factorization_sampling_profile(profile_path)


def test_tracked_sampling_profile_freezes_current_factor150_authority() -> None:
    profile = load_factorization_sampling_profile(
        "benchmarks/paper/paper_factorization_sampling_profile.v1.json"
    )

    assert profile.profile_id == "factorization_150_stable_hash.v2"
    assert profile.seed == 315_150
    assert profile.case_count_by_difficulty == {
        "easy": 50,
        "medium": 50,
        "hard": 50,
    }
    assert profile.total_case_count == 150
    assert profile.source_path == (
        "benchmarks/paper/paper_factorization_sampling_profile.v1.json"
    )


def test_count_only_profile_change_preserves_stable_hash_ranking_prefix() -> None:
    tracked = load_factorization_sampling_profile(
        "benchmarks/paper/paper_factorization_sampling_profile.v1.json"
    )
    count_only_body = tracked.source_body()
    count_only_body["case_count_by_difficulty"] = {
        difficulty: 30 for difficulty in tracked.difficulty_order
    }
    reduced = FactorizationSamplingProfile.from_mapping(count_only_body)
    candidates = {
        difficulty: tuple(
            {"case_id": f"factor_v2_{difficulty}_{index:03d}"}
            for index in range(167)
        )
        for difficulty in tracked.difficulty_order
    }

    tracked_selected = tracked.select_cases_by_difficulty(candidates)
    reduced_selected = reduced.select_cases_by_difficulty(candidates)

    for difficulty in tracked.difficulty_order:
        tracked_ids = tuple(
            str(case["case_id"]) for case in tracked_selected[difficulty]
        )
        reduced_ids = tuple(
            str(case["case_id"]) for case in reduced_selected[difficulty]
        )
        reference_ids = tuple(
            str(case["case_id"])
            for case in sorted(
                candidates[difficulty],
                key=lambda case: (
                    digest_json(
                        {
                            "schema_version": tracked.score_schema_version,
                            "profile_id": tracked.profile_id,
                            "seed": tracked.seed,
                            "difficulty": difficulty,
                            "case_id": str(case["case_id"]),
                        }
                    ),
                    str(case["case_id"]),
                ),
            )[:50]
        )
        assert tracked_ids == reference_ids
        assert reduced_ids == tracked_ids[:30]
